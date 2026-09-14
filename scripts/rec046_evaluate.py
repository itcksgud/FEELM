"""Open allowed E only after selection seal; aggregate exact user-level quality."""

# ruff: noqa: E402 -- fix BLAS threads before importing NumPy.
from __future__ import annotations
import os

for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"
import itertools
import json
from collections import defaultdict
import numpy as np
import pandas as pd
from rec046_common import (
    DOC,
    OUT,
    require,
    reviewed,
    fingerprint,
    pin,
    write_json,
    pair_accuracy,
    verify_prepared,
)
from rec046_run import METHODS, load_labels


def group_masks(ei, catalog, n, direct, c):
    ei = np.asarray(ei)
    held = catalog["held"][ei]
    pre = catalog["precounts"][ei]
    masks = {
        "ALL": np.ones(len(ei), bool),
        "ALS_DIRECT": direct,
        "ITEM_SUPPORTED": n[ei] > 0,
        "WITHHELD": held,
        "OTHER_UNSUPPORTED": (n[ei] == 0) & (~held),
        "NO_PRE_OBSERVATIONS": (~held) & (pre == 0),
        "SAMPLE_UNSUPPORTED": (~held) & (pre > 0) & (n[ei] == 0),
    }
    if c["k"] and c["input_supported"] == 0:
        masks["INPUT_ALL_UNSUPPORTED"] = np.ones(len(ei), bool)
    if 0 < c["input_supported"] < c["k"]:
        masks["INPUT_SOME_UNSUPPORTED"] = np.ones(len(ei), bool)
    years = catalog["years"][ei]
    for label, mask in [
        ("UNKNOWN_YEAR", years <= 0),
        ("BEFORE_2000", (years > 0) & (years < 2000)),
        ("2000_2009", (years >= 2000) & (years < 2010)),
        ("2010_2018", (years >= 2010) & (years < 2019)),
        ("2019_PLUS", years >= 2019),
        ("TRAIN_0", n[ei] == 0),
        ("TRAIN_1_9", (n[ei] > 0) & (n[ei] < 10)),
        ("TRAIN_10_49", (n[ei] >= 10) & (n[ei] < 50)),
        ("TRAIN_50_PLUS", n[ei] >= 50),
    ]:
        masks[label] = mask
    return masks


def aggregate_rows(table):
    user = table.groupby(["uid", "k", "group", "method"], as_index=False)[
        ["mse", "mae", "pair_accuracy"]
    ].mean()
    summary = user.groupby(["k", "group", "method"], as_index=False).agg(
        users=("uid", "size"),
        mse=("mse", "mean"),
        mae=("mae", "mean"),
        pair_accuracy=("pair_accuracy", "mean"),
        pair_users=("pair_accuracy", "count"),
    )
    return user, summary


def contrasts(user, cfg):
    rows = []
    for k in cfg["ks"]:
        for metric in ["mse", "pair_accuracy"]:
            table = (
                user[(user.k == k) & (user.group == "ALL")]
                .pivot(index="uid", columns="method", values=metric)
                .reindex(columns=METHODS)
                .dropna()
            )
            x = table.to_numpy()
            require(len(x) >= 30, "insufficient main paired users")
            rng = np.random.default_rng(
                cfg["seed"] + k + (100 if metric == "mse" else 200)
            )
            draws = np.empty((cfg["bootstrap_repeats"], len(METHODS)))
            for start in range(0, len(draws), 128):
                length = min(128, len(draws) - start)
                draws[start : start + length] = x[
                    rng.integers(len(x), size=(length, len(x)))
                ].mean(axis=1)
            for a, b in itertools.combinations(range(len(METHODS)), 2):
                delta = draws[:, a] - draws[:, b]
                ci = np.quantile(delta, [0.05 / 40, 1 - 0.05 / 40])
                rows.append(
                    {
                        "k": k,
                        "metric": metric,
                        "a": METHODS[a],
                        "b": METHODS[b],
                        "users": len(x),
                        "delta_a_minus_b": float(np.mean(x[:, a] - x[:, b])),
                        "ci_low": float(ci[0]),
                        "ci_high": float(ci[1]),
                        "excludes_zero": bool(ci[0] > 0 or ci[1] < 0),
                    }
                )
    return rows


def evaluate():
    reviewed()
    verify_prepared()
    cfg = json.loads((DOC / "config.json").read_text())
    seal = json.loads((OUT / "prediction-seal.json").read_text())
    require(seal["fingerprint"] == fingerprint(), "prediction version")
    require(
        seal["prepared_seal"] == pin(OUT / "prepared-seal.json"),
        "prediction prepared seal changed",
    )
    require(
        seal["all_fit_seal"] == pin(OUT / "all-fit-seal.json"),
        "prediction fit seal changed",
    )
    fits = json.loads((OUT / "all-fit-seal.json").read_text())
    require(
        fits["prepared_seal"] == pin(OUT / "prepared-seal.json"),
        "fit prepared chain changed",
    )
    for path, expected in fits["files"].items():
        require(pin(OUT / path) == expected, "fit output drift")
    for path, expected in seal["files"].items():
        require(pin(OUT / path) == expected, "prediction drift")
    catalog = dict(np.load(OUT / "catalog.npz", allow_pickle=False))
    ids = catalog["movie_ids"]
    contexts = [
        json.loads((OUT / f"r{r}/contexts.json").read_text())
        for r in range(cfg["rounds"])
    ]
    labels = load_labels("evaluation", contexts, ids)
    records = []
    support = []
    group_movies = defaultdict(set)
    group_pairs = defaultdict(set)
    group_instances = defaultdict(int)
    for r, cs in enumerate(contexts):
        z = np.load(OUT / f"r{r}/selected.npz", allow_pickle=False)
        p = z["predictions"]
        direct = z["direct_als"]
        require(list(z["methods"]) == METHODS, "method axis")
        n = np.load(OUT / f"r{r}/train-counts.npy", allow_pickle=False)
        for c in cs:
            if c["role"] != "evaluation":
                continue
            ei = c["ei"]
            s = slice(c["start"], c["stop"])
            pred = p[s]
            y = np.array([labels[(c["uid"], int(ids[i]))] for i in ei])
            masks = group_masks(ei, catalog, n, direct[s], c)
            support.append(
                {
                    "round": r,
                    "uid": c["uid"],
                    "k": c["k"],
                    "pairs": len(y),
                    "direct_als": int(direct[s].sum()),
                    "input_supported": c["input_supported"],
                    "input_withheld": c["input_withheld"],
                    "withheld_targets": int(catalog["held"][ei].sum()),
                }
            )
            for group, mask in masks.items():
                if not mask.any():
                    continue
                key = (c["k"], group)
                selected_movies = ids[np.asarray(ei)[mask]]
                group_movies[key].update(map(int, selected_movies))
                group_pairs[key].update((c["uid"], int(mid)) for mid in selected_movies)
                group_instances[key] += int(mask.sum())
                for j, method in enumerate(METHODS):
                    error = np.clip(pred[mask, j], 0.5, 5) - y[mask]
                    records.append(
                        {
                            "round": r,
                            "uid": c["uid"],
                            "k": c["k"],
                            "group": group,
                            "method": method,
                            "ratings": int(mask.sum()),
                            "mse": float(np.mean(error**2)),
                            "mae": float(np.mean(np.abs(error))),
                            "pair_accuracy": pair_accuracy(y[mask], pred[mask, j]),
                        }
                    )
    table = pd.DataFrame(records)
    table.to_parquet(OUT / "user-metrics.parquet", index=False)
    pd.DataFrame(support).to_parquet(OUT / "support.parquet", index=False)
    user, summary = aggregate_rows(table)
    counts = pd.DataFrame(
        [
            {
                "k": k,
                "group": group,
                "unique_movies": len(group_movies[(k, group)]),
                "unique_rating_pairs": len(group_pairs[(k, group)]),
                "round_rating_instances": group_instances[(k, group)],
            }
            for k, group in sorted(group_movies)
        ]
    )
    summary = summary.merge(counts, on=["k", "group"], validate="many_to_one")
    summary.to_csv(OUT / "summary.csv", index=False)
    pair = contrasts(user, cfg)
    write_json(OUT / "contrasts.json", pair)
    table[table.group == "ALL"].groupby(["round", "k", "method"])[
        ["mse", "mae", "pair_accuracy"]
    ].mean().to_csv(OUT / "round-summary.csv")
    write_json(
        OUT / "completion-seal.json",
        {
            "fingerprint": fingerprint(),
            "prediction_seal": pin(OUT / "prediction-seal.json"),
            "status": "CALCULATED_PENDING_INDEPENDENT_REVIEW",
            "files": {
                p.relative_to(OUT).as_posix(): pin(p)
                for p in [
                    OUT / "evaluation-labels.parquet",
                    OUT / "user-metrics.parquet",
                    OUT / "summary.csv",
                    OUT / "contrasts.json",
                    OUT / "support.parquet",
                    OUT / "round-summary.csv",
                ]
            },
        },
    )
    print(summary[summary.group == "ALL"].to_string(index=False), flush=True)


if __name__ == "__main__":
    evaluate()
