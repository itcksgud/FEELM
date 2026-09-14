"""Read-only REC045 replay: seals, every user metric, sample primal SVD, bootstrap."""

# ruff: noqa: E402 -- fix BLAS threads before importing numerical libraries.
import os

for _name in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ[_name] = "1"

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

from feelm_preference_structure import pin, require

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = (
    "CROWD",
    "GENRE",
    "KEYWORD",
    "COUNTRY_LANGUAGE",
    "ERA_RUNTIME",
    "DIRECTOR",
    "CAST",
    "COMPANY_COLLECTION",
)


def metadata_diagnostic():
    """Verify descriptive raw-mean correlations without opening evaluation stars."""
    folder = ROOT / "outputs/recommendation-evidence/rec-ev-045"
    saved = json.loads(
        (
            ROOT
            / "docs/recommendation/experiments/rec-ev-045/crowd-metadata-diagnostic.json"
        ).read_text(encoding="utf-8")
    )
    require(
        pin(folder / "completion-seal.json") == saved["source_completion"],
        "diagnostic source",
    )
    frame = pd.read_parquet(folder / "metadata.parquet")
    checked = 0
    for r in range(3):
        stats = read_npz(folder / f"r{r}/training-statistics.npz")
        counts, sums = stats["counts"], stats["sums"]
        means = np.divide(
            sums, counts, out=np.full(len(counts), np.nan), where=counts > 0
        )
        table = pd.DataFrame(
            {
                "ml_raw_mean": means,
                "ml_log_count": np.log1p(counts),
                "tmdb_mean": frame.tmdb_vote_average,
                "tmdb_log_votes": np.log1p(frame.tmdb_vote_count),
                "tmdb_log_popularity": np.log1p(frame.tmdb_popularity),
            }
        )
        masks = {
            "rated_movies": counts > 0,
            "ratings_1_49": (counts > 0) & (counts < 50),
            "ratings_50_499": (counts >= 50) & (counts < 500),
            "ratings_500plus": counts >= 500,
        }
        for name, mask in masks.items():
            record = next(
                row
                for row in saved["records"]
                if row["round"] == r and row["group"] == name
            )
            require(int(mask.sum()) == record["movies"], "diagnostic support")
            corr = table.loc[mask].corr(method="spearman")
            expected = pd.DataFrame(record["spearman_descriptive"]).loc[
                corr.index, corr.columns
            ]
            require(
                np.allclose(corr, expected, atol=1e-12, rtol=0, equal_nan=True),
                "diagnostic correlations",
            )
            checked += 1
    print(
        json.dumps(
            {
                "status": "PASS_READ_ONLY_METADATA_DIAGNOSTIC",
                "tables": checked,
                "E_stars_read": False,
            }
        ),
        flush=True,
    )


def read_npz(path):
    with np.load(path, allow_pickle=False) as archive:
        return {name: archive[name] for name in archive.files}


def run(temporal=False):
    main = ROOT / "outputs/recommendation-evidence/rec-ev-045"
    out = ROOT / (
        "outputs/recommendation-evidence/rec-ev-045-temporal"
        if temporal
        else "outputs/recommendation-evidence/rec-ev-045"
    )
    completion = json.loads((out / "completion-seal.json").read_text(encoding="utf-8"))
    for name, expected in completion["outputs"].items():
        require(pin(out / name) == expected, "output seal " + name)
    for name, expected in completion["fingerprint"].items():
        require(pin(ROOT / name) == expected, "source fingerprint " + name)
    keys, row_deltas, row_metrics = [], [], []
    max_metric_error = max_prediction_error = 0.0
    checks = 0
    for r in range(3):
        folder = out / f"r{r}"
        data = read_npz(folder / "input.npz")
        stats = read_npz(folder / "training-statistics.npz")
        labels = read_npz(folder / "opened-labels.npz")["rating_raw"]
        evaluated = read_npz(folder / "evaluation.npz")
        require(
            not set(data["training_ids"]).intersection(data["evaluation_ids"]),
            "user role intersection",
        )
        static = {
            name: sparse.load_npz((folder if name == "CROWD" else main) / f"{name}.npz")
            for name in (*BLOCKS, "PERSON_ERA")
        }
        sample = sorted(
            range(len(data["user_keys"])),
            key=lambda u: hashlib.sha256(
                ("rec045-verifier|" + data["user_keys"][u]).encode()
            ).digest(),
        )[:8]
        cases = (
            [(30, 0)]
            if temporal
            else [(k, d) for k in (5, 10, 30) for d in range(1 if k == 30 else 3)]
        )
        mses, maes = {}, {}
        for k, d in cases:
            stored = read_npz(
                folder
                / ("predictions.npz" if temporal else f"k{k}-d{d}-predictions.npz")
            )
            error = stored["predictions"] - labels[:, None]
            mse, mae = [], []
            for u in range(len(data["user_keys"])):
                rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
                mse.append((error[rows] ** 2).mean(axis=0))
                mae.append(np.abs(error[rows]).mean(axis=0))
            mses[k] = mses.get(k, 0) + np.array(mse) / (1 if temporal or k == 30 else 3)
            maes[k] = maes.get(k, 0) + np.array(mae) / (1 if temporal or k == 30 else 3)
            if d != 0:
                continue
            for u in sample:
                rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
                selected = np.arange(30) if temporal else stored["selected"][u]
                oi, ei = data["o_index"][u, selected], data["e_index"][rows]
                stars = data["o_ratings"][u, selected]
                g = stats["bayes"] + stats["shared_correction"]
                offset = (stars - g[oi]).mean()
                residual = stars - g[oi] - offset
                for column in [2, 3, 9, 10, 12] if k == 30 else [2, 3]:
                    names = (
                        ["GENRE"]
                        if column == 2
                        else [
                            b
                            for b in BLOCKS
                            if not (4 <= column <= 11 and b == BLOCKS[column - 4])
                        ]
                    )
                    if column == 12:
                        names.append("PERSON_ERA")
                    x = sparse.hstack([static[name] for name in names], format="csr")
                    active = np.unique(x[oi].indices)
                    xo = x[oi][:, active].toarray()
                    xe = x[ei][:, active].toarray()
                    left, values, right = np.linalg.svd(xo, full_matrices=False)
                    beta = right.T @ ((values / (values**2 + 5)) * (left.T @ residual))
                    expected = g[ei] + offset + xe @ beta
                    discrepancy = float(
                        np.max(np.abs(expected - stored["predictions"][rows, column]))
                    )
                    max_prediction_error = max(max_prediction_error, discrepancy)
                    require(discrepancy < 1e-9, "primal prediction discrepancy")
                    checks += len(ei)
        for k in mses:
            actual = evaluated["mse" if temporal else f"mse_k{k}"]
            actual_mae = evaluated["mae" if temporal else f"mae_k{k}"]
            max_metric_error = max(
                max_metric_error,
                float(np.max(np.abs(mses[k] - actual))),
                float(np.max(np.abs(maes[k] - actual_mae))),
            )
        require(max_metric_error < 1e-12, "all-user metric discrepancy")
        if temporal:
            delta = np.column_stack(
                [
                    mses[30][:, 1] - mses[30][:, 3],
                    mses[30][:, 2] - mses[30][:, 3],
                    mses[30][:, 3] - mses[30][:, 12],
                ]
            )
            matrix = mses[30]
        else:
            delta = np.column_stack(
                [mses[k][:, 1] - mses[k][:, 3] for k in (5, 10, 30)]
                + [mses[30][:, 2] - mses[30][:, 3]]
                + [mses[30][:, j] - mses[30][:, 3] for j in range(4, 12)]
                + [mses[30][:, 3] - mses[30][:, 12]]
            )
            matrix = np.column_stack([mses[k] for k in (5, 10, 30)])
        require(
            np.allclose(
                delta,
                evaluated["delta" if temporal else "contrasts"],
                atol=1e-12,
                rtol=0,
            ),
            "contrast axes",
        )
        keys.extend(data["user_keys"])
        row_deltas.extend(delta)
        row_metrics.extend(matrix)
        print(f"verified {'temporal' if temporal else 'main'} r{r}", flush=True)
    # Independently group by user rather than importing the implementation aggregation.
    grouped_delta, grouped_metrics = {}, {}
    for key, delta, metrics in zip(keys, row_deltas, row_metrics, strict=True):
        grouped_delta.setdefault(key, []).append(delta)
        grouped_metrics.setdefault(key, []).append(metrics)
    unique = sorted(grouped_delta)
    delta = np.array([np.mean(grouped_delta[key], axis=0) for key in unique])
    metrics = np.array([np.mean(grouped_metrics[key], axis=0) for key in unique])
    saved = read_npz(out / "unique-user-evaluation.npz")
    require(
        np.array_equal(unique, saved["user_keys"])
        and np.allclose(delta, saved["delta"], atol=1e-12, rtol=0),
        "unique person contrasts",
    )
    require(
        np.allclose(
            metrics, saved["mse" if temporal else "metrics"], atol=1e-12, rtol=0
        ),
        "unique person metrics",
    )
    # Same fixed RNG stream; count weights and manual linear order statistics.
    rng = np.random.Generator(np.random.PCG64(20260912 if temporal else 20260911))
    draws = np.empty((20000, delta.shape[1]))
    for i in range(20000):
        weights = np.bincount(
            rng.integers(len(delta), size=len(delta)), minlength=len(delta)
        )
        draws[i] = weights @ delta / len(delta)
    draws.sort(axis=0)
    family = 3 if temporal else 13
    limits = []
    for q in (0.05 / (2 * family), 1 - 0.05 / (2 * family)):
        p = q * 19999
        low = int(np.floor(p))
        high = int(np.ceil(p))
        limits.append(
            draws[low] * (high - p) + draws[high] * (p - low)
            if low != high
            else draws[low]
        )
    ci = np.array(limits).T
    max_ci_error = float(np.max(np.abs(ci - saved["intervals"])))
    require(max_ci_error < 1e-12, "bootstrap discrepancy")
    print(
        json.dumps(
            {
                "status": "PASS_READ_ONLY",
                "temporal": temporal,
                "unique_users": len(unique),
                "sample_prediction_cells": checks,
                "max_prediction_error": max_prediction_error,
                "max_all_user_metric_error": max_metric_error,
                "max_bootstrap_interval_error": max_ci_error,
                "completion_sha256": pin(out / "completion-seal.json")["sha256"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--temporal", action="store_true")
    parser.add_argument("--metadata-diagnostic", action="store_true")
    args = parser.parse_args()
    if args.metadata_diagnostic:
        metadata_diagnostic()
    else:
        run(args.temporal)
