"""Observed-star evaluation, fixed support strata and paired learning curves."""

from __future__ import annotations
import json
import numpy as np
import pandas as pd
from rec047_common import (
    OUT,
    KS,
    LEVELS,
    METHODS,
    reviewed,
    fingerprint,
    stage_seal,
    require,
    pin,
    write_json,
    pair_accuracy,
    support_group,
    check_prediction,
)
from rec047_run import validate_seal, validate_selection, load_labels


def interval(values, alpha=0.05, seed=47):
    values = np.asarray(values, float)
    if len(values) < 30:
        return [None, None]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(100):
        samples = rng.integers(len(values), size=(100, len(values)))
        means.extend(values[samples].mean(axis=1))
    return np.quantile(means, [alpha / 2, 1 - alpha / 2]).tolist()


def evaluate():
    reviewed()
    stage_seal("evaluation")
    validate_selection()
    folder = OUT / "evaluation"
    validate_seal(folder / "fit-seal.json", folder, "evaluation")
    require(not (OUT / "metrics.csv").exists(), "preserve existing evaluation")
    cat = np.load(folder / "catalog.npz")
    ids = cat["movie_ids"]
    counts = cat["counts"]
    full_groups = support_group(counts[-1])
    contexts = json.loads((folder / "contexts.json").read_text())
    combined = np.load(folder / "final-predictions.npz", allow_pickle=False)
    require(
        np.array_equal(combined["levels"], LEVELS)
        and np.array_equal(combined["methods"], METHODS),
        "combined prediction axes",
    )
    predictions, coverage = combined["predictions"], combined["direct"]
    n = contexts[-1]["stop"]
    require(
        predictions.shape == (len(LEVELS), n, len(METHODS))
        and coverage.shape == (len(LEVELS), n),
        "combined prediction shape",
    )
    for li in range(len(LEVELS)):
        for mi in range(len(METHODS)):
            check_prediction(predictions[li, :, mi], n, coverage[li])
    labels = load_labels("evaluation")
    rows = []
    population = {}
    for li, level in enumerate(LEVELS):
        vectors = {m: predictions[li, :, mi] for mi, m in enumerate(METHODS)}
        direct = coverage[li]
        for c in contexts:
            ei = np.asarray(c["ei"])
            sl = slice(c["start"], c["stop"])
            y = np.asarray([labels[(c["uid"], int(ids[i]))] for i in ei])
            for group in ["ALL", "0", "1_9", "10_49", "50_PLUS"]:
                mask = (
                    np.ones(len(ei), bool)
                    if group == "ALL"
                    else full_groups[ei] == group
                )
                if not mask.any():
                    continue
                truth = y[mask]
                if li == 0:
                    population.setdefault((c["k"], c["activity"], group), set()).update(
                        map(int, ei[mask])
                    )
                _, ties = np.unique(truth, return_counts=True)
                comparable = int(
                    (len(truth) * (len(truth) - 1) - np.sum(ties * (ties - 1))) // 2
                )
                for method in METHODS:
                    raw = vectors[method][sl][mask]
                    pred = np.clip(raw, 0.5, 5)
                    rows.append(
                        {
                            "level": level,
                            "method": method,
                            "uid": c["uid"],
                            "k": c["k"],
                            "activity": c["activity"],
                            "support": group,
                            "common_k30": c["pre_catalog"] >= 30,
                            "targets": len(truth),
                            "comparable_pairs": comparable,
                            "mse": float(np.mean((pred - truth) ** 2)),
                            "mae": float(np.mean(abs(pred - truth))),
                            "pa": pair_accuracy(truth, raw),
                            "als_direct": float(direct[sl][mask].mean()),
                            "actual_movie_support": float(
                                (counts[li, ei[mask]] > 0).mean()
                            ),
                            "prediction_outside_scale": float(
                                ((raw < 0.5) | (raw > 5)).mean()
                            ),
                        }
                    )
    user = pd.DataFrame(rows)
    user.to_parquet(OUT / "user-metrics.parquet", index=False)
    main = []
    grid = []
    for level in LEVELS:
        for method in METHODS:
            for k in KS:
                base = user[
                    (user.level == level) & (user.method == method) & (user.k == k)
                ]
                for cohort in ["eligible_k", "common_k30"]:
                    s = base[
                        (base.support == "ALL")
                        & (base.common_k30 if cohort == "common_k30" else True)
                    ]
                    for metric in [
                        "mse",
                        "mae",
                        "pa",
                        "als_direct",
                        "actual_movie_support",
                        "prediction_outside_scale",
                    ]:
                        a = s[metric].dropna().to_numpy()
                        ci = interval(a)
                        main.append(
                            {
                                "level": level,
                                "method": method,
                                "k": k,
                                "cohort": cohort,
                                "metric": metric,
                                "users": len(a),
                                "mean": float(a.mean()) if len(a) else None,
                                "ci95_low": ci[0],
                                "ci95_high": ci[1],
                                "status": "NO_DATA"
                                if not len(a)
                                else "DESCRIPTIVE_SMALL_N"
                                if len(a) < 30
                                else "DESCRIPTIVE_CI",
                            }
                        )
                for activity in ["0", "1_9", "10_29", "30_99", "100_299", "300_PLUS"]:
                    for group in ["0", "1_9", "10_49", "50_PLUS"]:
                        s = base[(base.activity == activity) & (base.support == group)]
                        for metric in ["mse", "pa"]:
                            a = s[metric].dropna().to_numpy()
                            grid.append(
                                {
                                    "level": level,
                                    "method": method,
                                    "k": k,
                                    "activity": activity,
                                    "support": group,
                                    "metric": metric,
                                    "users": len(a),
                                    "observed_pairs_all_cell_users": int(
                                        s.targets.sum()
                                    ),
                                    "unique_movies": len(
                                        population.get((k, activity, group), set())
                                    ),
                                    "comparable_rating_pairs": int(
                                        s.comparable_pairs.sum()
                                    ),
                                    "mean": float(a.mean()) if len(a) else None,
                                    "status": "NO_DATA"
                                    if not len(a)
                                    else "DESCRIPTIVE_SMALL_N"
                                    if len(a) < 30
                                    else "DESCRIPTIVE",
                                }
                            )
    pd.DataFrame(main).to_csv(OUT / "metrics.csv", index=False)
    pd.DataFrame(grid).to_csv(OUT / "strata.csv", index=False)
    contrasts = []
    for k in [10, 30]:
        base = user[(user.k == k) & (user.support == "ALL")]
        for method in METHODS:
            a = base[(base.level == 100) & (base.method == method)].set_index("uid").mse
            b = base[(base.level == 25) & (base.method == method)].set_index("uid").mse
            require(a.index.equals(b.index), "paired volume users")
            delta = a - b
            ci = interval(delta, alpha=0.05 / 10)
            contrasts.append(
                {
                    "family": "volume_25_to_100_mse_10_tests",
                    "k": k,
                    "method": method,
                    "metric": "mse",
                    "users": len(delta),
                    "difference": float(delta.mean()),
                    "relative_mse_change_pct": float(100 * delta.mean() / b.mean()),
                    "ci_low": ci[0],
                    "ci_high": ci[1],
                }
            )
        for method in ["FM", "ALS_FM", "RIDGE", "GBT"]:
            for metric in ["mse", "pa"]:
                a = base[(base.level == 100) & (base.method == method)].set_index(
                    "uid"
                )[metric]
                b = base[(base.level == 100) & (base.method == "ALS")].set_index("uid")[
                    metric
                ]
                require(a.index.equals(b.index), "paired model users")
                delta = (a - b).dropna()
                ci = interval(delta, alpha=0.05 / 16)
                contrasts.append(
                    {
                        "family": "model_vs_ALS_100pct_16_tests",
                        "k": k,
                        "method": method,
                        "metric": metric,
                        "users": len(delta),
                        "difference": float(delta.mean()),
                        "relative_mse_change_pct": float(
                            100 * delta.mean() / b.loc[delta.index].mean()
                        )
                        if metric == "mse"
                        else None,
                        "ci_low": ci[0],
                        "ci_high": ci[1],
                    }
                )
    pd.DataFrame(contrasts).to_csv(OUT / "contrasts.csv", index=False)
    training = []
    for stage in ["validation", "evaluation"]:
        info = json.loads((OUT / stage / "feature-info.json").read_text())
        episodes = pd.read_parquet(OUT / stage / "episodes.parquet")
        for level in LEVELS:
            s = episodes[episodes.level <= level]
            for k in KS:
                a = s[s.k == k]
                training.append(
                    {
                        "stage": stage,
                        "level": level,
                        "k": k,
                        "episode_users": int(a.uid.nunique()),
                        "episodes": len(a),
                        "target_rows": int(a.target_rows.sum()),
                        "total_ratings": info["levels"][str(level)]["ratings"],
                        "target_share": float(
                            a.target_rows.sum() / s.target_rows.sum()
                        ),
                    }
                )
    pd.DataFrame(training).to_csv(OUT / "training-composition.csv", index=False)
    write_json(
        OUT / "completion.json",
        {
            "status": "CALCULATED_PENDING_INDEPENDENT_REVIEW",
            "fingerprint": fingerprint(),
            "selection": pin(OUT / "selection.json"),
            "evaluation_fit_seal": pin(folder / "fit-seal.json"),
            "evaluation_labels": pin(folder / "labels.parquet"),
            "evaluation_users": len({c["uid"] for c in contexts}),
            "unique_observed_pairs": len(labels),
            "raw_rating_scale": "0.5 steps, preserved",
            "metric_scope": "observed future ratings only; no unobserved relevance ground truth",
            "files": {p.name: pin(p) for p in OUT.iterdir() if p.is_file()},
        },
    )
    print("CALCULATED_PENDING_INDEPENDENT_REVIEW", flush=True)


if __name__ == "__main__":
    evaluate()
