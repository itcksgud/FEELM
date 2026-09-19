"""Evaluate validation targets and candidate target positions without opening FINAL_TEST."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bootstrap_mean_ci(values: np.ndarray, seed: int, replicates: int = 2000) -> list[float]:
    if values.size == 0:
        raise ValueError("bootstrap requires at least one value")
    rng = np.random.default_rng(seed)
    means = np.empty(replicates, dtype=float)
    chunk = 100
    for start in range(0, replicates, chunk):
        size = min(chunk, replicates - start)
        indices = rng.integers(0, values.size, size=(size, values.size))
        means[start:start + size] = values[indices].mean(axis=1)
    return [float(value) for value in np.quantile(means, [0.025, 0.975])]


def nested_history_curve(target: pd.DataFrame, seed: int = 622) -> list[dict]:
    keys = ["uid", "target_movie_id", "prediction_at"]
    baseline = target[target.n == 0][keys + ["squared_error", "absolute_error"]].rename(columns={
        "squared_error": "baseline_squared_error",
        "absolute_error": "baseline_absolute_error",
    })
    if baseline.duplicated(keys).any():
        raise RuntimeError("N=0 baseline is not unique per user-target")
    paired = target[target.n > 0].merge(baseline, on=keys, validate="many_to_one")
    if len(paired) != int((target.n > 0).sum()):
        raise RuntimeError("a nonzero-N row is missing its N=0 pair")
    paired["mse_delta"] = paired.squared_error - paired.baseline_squared_error
    paired["mae_delta"] = paired.absolute_error - paired.baseline_absolute_error
    result = []
    order = ["1", "2", "3-4", "5-9", "10-19", "20-29", "30-49", "50+"]
    for bucket in order:
        frame = paired[paired.n_bucket == bucket]
        if frame.empty:
            continue
        user = frame.groupby("uid", sort=True).agg(
            mse=("squared_error", "mean"),
            baseline_mse=("baseline_squared_error", "mean"),
            mae=("absolute_error", "mean"),
            baseline_mae=("baseline_absolute_error", "mean"),
            mse_delta=("mse_delta", "mean"),
            mae_delta=("mae_delta", "mean"),
        )
        result.append({
            "n_bucket": bucket,
            "rows": int(len(frame)),
            "users": int(len(user)),
            "baseline_user_macro_mse_on_same_cohort": float(user.baseline_mse.mean()),
            "user_macro_mse": float(user.mse.mean()),
            "user_macro_mse_delta_vs_n0": float(user.mse_delta.mean()),
            "user_macro_mse_delta_vs_n0_bootstrap_95_ci": bootstrap_mean_ci(
                user.mse_delta.to_numpy(dtype=float), seed + len(result)
            ),
            "baseline_user_macro_mae_on_same_cohort": float(user.baseline_mae.mean()),
            "user_macro_mae": float(user.mae.mean()),
            "user_macro_mae_delta_vs_n0": float(user.mae_delta.mean()),
            "improved_user_target_fraction_by_squared_error": float((frame.mse_delta < 0).mean()),
        })
    return result


def select_profile(results: dict[str, dict], config: dict) -> tuple[str, dict]:
    gate = config["selection_gate"]
    decisions = {}
    eligible = []
    for profile in config["profiles"]:
        deltas = [row["user_macro_mse_delta_vs_n0"]
                  for row in results[profile]["nested_history_paired_vs_n0"]]
        non_worse = bool(deltas) and all(
            value <= gate["maximum_positive_user_macro_mse_delta"] for value in deltas
        )
        strict = any(value <= -gate["minimum_strict_improvement"] for value in deltas)
        passed = non_worse and (strict or not gate["require_strict_improvement"])
        decisions[profile] = {
            "status": "PASS" if passed else "FAIL",
            "all_buckets_non_worse_than_n0": non_worse,
            "has_strict_improvement": strict,
            "maximum_user_macro_mse_delta_vs_n0": max(deltas) if deltas else None,
            "minimum_user_macro_mse_delta_vs_n0": min(deltas) if deltas else None,
        }
        if passed:
            eligible.append(profile)
    if not eligible:
        raise RuntimeError("no profile passed the nested-history selection gate")
    selected = min(eligible, key=lambda name: results[name][config["selection_metric"]])
    return selected, {
        "policy": gate,
        "eligible_profiles": eligible,
        "profiles": decisions,
    }


def evaluate_profile(root: Path, seed: int) -> tuple[dict, pd.DataFrame]:
    target = pd.read_parquet(root / "validation-target-predictions.parquet")
    candidates = pd.read_parquet(root / "validation-candidate-predictions.parquet")
    target["squared_error"] = (target.prediction.clip(0.5, 5) - target.label) ** 2
    target["absolute_error"] = (target.prediction.clip(0.5, 5) - target.label).abs()
    user = target.groupby("uid", sort=True)[["squared_error", "absolute_error"]].mean()
    target_by_n = target.groupby("n_bucket", sort=False).agg(
        rows=("episode_id", "size"), users=("uid", "nunique"),
        mse=("squared_error", "mean"), mae=("absolute_error", "mean"),
    ).reset_index().to_dict("records")

    candidates = candidates.sort_values(
        ["episode_id", "prediction", "candidate_movie_id"], ascending=[True, False, True]
    )
    candidates["model_rank"] = candidates.groupby("episode_id", sort=False).cumcount()
    target_ranks = candidates[candidates.is_target][["episode_id", "uid", "n_bucket", "model_rank"]].copy()
    if target_ranks.episode_id.nunique() != target.episode_id.nunique():
        raise RuntimeError("each validation episode must have exactly one target rank")
    candidates["gain"] = ((candidates.label >= 4.0) & candidates.label.notna()).astype(float)
    candidates["discounted_gain"] = np.where(
        candidates.model_rank < 10,
        candidates.gain / np.log2(candidates.model_rank + 2),
        0.0,
    )
    ranked = candidates.groupby("episode_id", sort=False).agg(
        dcg=("discounted_gain", "sum"), positives=("gain", "sum"),
    )
    ranked["idcg"] = ranked.positives.map(
        lambda count: sum(1 / math.log2(rank + 2) for rank in range(min(int(count), 10)))
    )
    ranked["observed_ndcg_at_10"] = ranked.dcg / ranked.idcg.where(ranked.idcg > 0)
    top10 = candidates[candidates.model_rank < 10].copy()
    observed_top10 = top10[top10.label.notna()]
    low_exposure = float((observed_top10.label < 4.0).mean()) if len(observed_top10) else None
    positive_recall_at_10 = float(top10.groupby("episode_id").gain.sum().sum() / ranked.positives.sum())
    target_ranks = target_ranks.merge(
        ranked[["observed_ndcg_at_10"]], left_on="episode_id", right_index=True, validate="one_to_one"
    )
    unknown_fraction = float((top10.label_state == "UNKNOWN_SAMPLED").mean())
    result = {
        "validation_user_macro_mse": float(user.squared_error.mean()),
        "validation_user_macro_mae": float(user.absolute_error.mean()),
        "validation_rows": int(len(target)),
        "validation_users": int(target.uid.nunique()),
        "target_by_n": target_by_n,
        "nested_history_paired_vs_n0": nested_history_curve(target, seed),
        "candidate_episodes": int(target_ranks.episode_id.nunique()),
        "observed_ndcg_at_10": float(target_ranks.observed_ndcg_at_10.mean()),
        "observed_positive_recall_at_10": positive_recall_at_10,
        "observed_low_rating_exposure_at_10": low_exposure,
        "observed_target_top10_rate": float((target_ranks.model_rank < 10).mean()),
        "unknown_slot_fraction_at_10": unknown_fraction,
        "candidate_interpretation": "UNKNOWN_SAMPLED slots are unjudged, not negative labels",
    }
    joined = target[["episode_id", "uid", "n_bucket", "squared_error"]].merge(
        target_ranks[["episode_id", "observed_ndcg_at_10"]], on="episode_id", validate="one_to_one"
    )
    return result, joined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    results = {}
    rows = {}
    for profile in config["profiles"]:
        results[profile], rows[profile] = evaluate_profile(args.fits_root / profile, config["seed"])
    baseline = rows["movie_only"].rename(columns={
        "squared_error": "baseline_squared_error",
        "observed_ndcg_at_10": "baseline_ndcg",
    })
    paired = {}
    for profile, frame in rows.items():
        joined = frame.merge(baseline, on=["episode_id", "uid", "n_bucket"], validate="one_to_one")
        user_mse_delta = joined.assign(
            mse_delta=joined.squared_error - joined.baseline_squared_error
        ).groupby("uid", sort=True).mse_delta.mean()
        paired[profile] = {
            "mean_squared_error_delta_vs_movie_only": float(
                (joined.squared_error - joined.baseline_squared_error).mean()
            ),
            "mean_ndcg_at_10_delta_vs_movie_only": float(
                (joined.observed_ndcg_at_10 - joined.baseline_ndcg).mean()
            ),
            "worse_episode_fraction_by_squared_error": float(
                (joined.squared_error > joined.baseline_squared_error).mean()
            ),
            "user_macro_mse_delta_vs_movie_only": float(user_mse_delta.mean()),
            "user_macro_mse_delta_vs_movie_only_bootstrap_95_ci": bootstrap_mean_ci(
                user_mse_delta.to_numpy(dtype=float), config["seed"] + config["profiles"].index(profile)
            ),
            "bootstrap": {"unit": "USER", "replicates": 2000, "seed_base": config["seed"]},
        }
    selected, selection_gate = select_profile(results, config)
    report = {
        "status": "PASS",
        "calculator": {
            "version": "GBT_ZERO_N_EVALUATOR_V3_NESTED_USER_BOOTSTRAP",
            "path": "scripts/gbt_zero_n_evaluate.py",
            "sha256": file_sha256(Path(__file__)),
        },
        "selection_metric": config["selection_metric"],
        "selection_gate": selection_gate,
        "selected_profile_on_validation": selected,
        "profiles": results,
        "paired": paired,
        "final_test_opened": False,
    }
    (args.fits_root / "validation-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
