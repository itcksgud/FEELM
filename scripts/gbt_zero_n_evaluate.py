"""Evaluate validation-only GBT outputs while keeping FINAL_TEST sealed."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


BUCKET_ORDER = ["0", "1", "2", "3-4", "5-9", "10-19", "20-29", "30-49", "50+"]
NDCG_AT_10_MIN_OBSERVED_JUDGMENTS = 10
LOW_HISTORY_MIN_USERS_PER_BUCKET = 100


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def n_bucket(value: int) -> str:
    boundaries = ((0, "0"), (1, "1"), (2, "2"), (4, "3-4"), (9, "5-9"),
                  (19, "10-19"), (29, "20-29"), (49, "30-49"))
    return next((label for upper, label in boundaries if value <= upper), "50+")


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


def ratio_metric(numerator: int | float, denominator: int, unit: str) -> dict:
    return {
        "value": None if denominator == 0 else float(numerator / denominator),
        "numerator": float(numerator),
        "denominator": int(denominator),
        "unit": unit,
    }


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
    for bucket in BUCKET_ORDER[1:]:
        frame = paired[paired.n_bucket == bucket]
        if frame.empty:
            continue
        user = frame.groupby("uid", sort=True).agg(
            mse=("squared_error", "mean"), baseline_mse=("baseline_squared_error", "mean"),
            mae=("absolute_error", "mean"), baseline_mae=("baseline_absolute_error", "mean"),
            mse_delta=("mse_delta", "mean"), mae_delta=("mae_delta", "mean"),
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
            "user_macro_mae_delta_vs_n0_bootstrap_95_ci": bootstrap_mean_ci(
                user.mae_delta.to_numpy(dtype=float), seed + 100 + len(result)
            ),
            "improved_user_target_fraction_by_squared_error": float((frame.mse_delta < 0).mean()),
            "worsened_user_target_fraction_by_squared_error": float((frame.mse_delta > 0).mean()),
            "bootstrap": {
                "unit": "USER",
                "estimand": "USER_MACRO_MEAN_OF_USER_TARGET_DELTAS",
                "replicates": 2000,
            },
        })
    return result


def candidate_metrics(candidates: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    if candidates.empty:
        raise RuntimeError("candidate evaluation requires rows")
    candidates = candidates.copy()
    candidates["candidate_supported"] = np.isfinite(candidates.prediction.to_numpy(dtype=float))
    if candidates.duplicated(["episode_id", "candidate_movie_id"]).any():
        raise RuntimeError("candidate IDs must be unique within an episode")
    candidates = candidates.sort_values(
        ["episode_id", "candidate_supported", "prediction", "candidate_movie_id"],
        ascending=[True, False, False, True],
    )
    candidates["model_rank"] = candidates.groupby("episode_id", sort=False).cumcount()
    target_rows = candidates[candidates.is_target]
    target_counts = target_rows.groupby("episode_id", sort=False).size()
    if len(target_counts) != candidates.episode_id.nunique() or not (target_counts == 1).all():
        raise RuntimeError("each candidate episode must contain exactly one target")

    candidates["gain"] = ((candidates.label >= 4.0) & candidates.label.notna()).astype(float)
    candidates["discounted_gain"] = np.where(
        candidates.model_rank < 10,
        candidates.gain / np.log2(candidates.model_rank + 2),
        0.0,
    )
    ranked = candidates.groupby("episode_id", sort=False).agg(
        uid=("uid", "first"),
        dcg=("discounted_gain", "sum"),
        positives=("gain", "sum"),
        observed_judgments=("label", lambda values: int(values.notna().sum())),
        candidate_rows=("candidate_movie_id", "size"),
        supported_rows=("candidate_supported", "sum"),
    )
    ranked["idcg"] = ranked.positives.map(
        lambda count: sum(1 / math.log2(rank + 2) for rank in range(min(int(count), 10)))
    )
    ranked["ndcg_at_10_eligible"] = (
        (ranked.observed_judgments >= NDCG_AT_10_MIN_OBSERVED_JUDGMENTS) & (ranked.idcg > 0)
    )
    ranked["observed_ndcg_at_10"] = (
        ranked.dcg / ranked.idcg
    ).where(ranked.ndcg_at_10_eligible)

    top10 = candidates[candidates.model_rank < 10].copy()
    top = top10.groupby("episode_id", sort=False).agg(
        top10_rows=("candidate_movie_id", "size"),
        top10_unique=("candidate_movie_id", "nunique"),
        top10_positive=("gain", "sum"),
        top10_unknown=("label_state", lambda values: int((values == "UNKNOWN_SAMPLED").sum())),
        top10_observed=("label", lambda values: int(values.notna().sum())),
        top10_low=("label", lambda values: int(((values < 4.0) & values.notna()).sum())),
    )
    ranked = ranked.join(top, validate="one_to_one")
    target_rank = target_rows[["episode_id", "model_rank", "candidate_supported"]].set_index("episode_id")
    ranked = ranked.join(target_rank, validate="one_to_one")
    ranked["observed_recall_at_10"] = ranked.top10_positive / ranked.positives.where(ranked.positives > 0)

    user_ndcg = ranked.groupby("uid", sort=True).observed_ndcg_at_10.mean().dropna()
    observed_positive_denominator = int(ranked.positives.sum())
    observed_positive_numerator = int(ranked.top10_positive.sum())
    observed_slots = int(ranked.top10_observed.sum())
    low_slots = int(ranked.top10_low.sum())
    top_slots = int(ranked.top10_rows.sum())
    unknown_slots = int(ranked.top10_unknown.sum())
    all_rows = int(ranked.candidate_rows.sum())
    supported_rows = int(ranked.supported_rows.sum())
    target_supported = int(ranked.candidate_supported.sum())
    target_top10 = int((ranked.model_rank < 10).sum())
    diversity_numerator = int(ranked.top10_unique.sum())
    metrics = {
        "episodes": int(len(ranked)),
        "users": int(ranked.uid.nunique()),
        "observed_ndcg_at_10_user_macro": None if user_ndcg.empty else float(user_ndcg.mean()),
        "observed_ndcg_at_10_users": int(len(user_ndcg)),
        "observed_ndcg_at_10_eligible_episodes": int(ranked.ndcg_at_10_eligible.sum()),
        "observed_ndcg_at_10_min_observed_judgments": NDCG_AT_10_MIN_OBSERVED_JUDGMENTS,
        "observed_ndcg_at_10_status": "PASS" if ranked.ndcg_at_10_eligible.any()
        else "INSUFFICIENT_JUDGMENTS",
        "observed_ndcg_excluded_insufficient_judgments": int(
            (ranked.observed_judgments < NDCG_AT_10_MIN_OBSERVED_JUDGMENTS).sum()
        ),
        "observed_ndcg_excluded_no_positive_episodes": int((ranked.positives == 0).sum()),
        "observed_positive_recall_at_10": ratio_metric(
            observed_positive_numerator, observed_positive_denominator, "OBSERVED_POSITIVE_CANDIDATE"
        ),
        "observed_low_rating_exposure_at_10": ratio_metric(low_slots, observed_slots, "OBSERVED_TOP10_SLOT"),
        "candidate_support_rate": ratio_metric(supported_rows, all_rows, "REQUESTED_CANDIDATE_ROW"),
        "target_candidate_recall": ratio_metric(target_supported, len(ranked), "EPISODE"),
        "observed_target_top10_rate": ratio_metric(target_top10, len(ranked), "EPISODE"),
        "request_diversity_at_10": ratio_metric(diversity_numerator, top_slots, "TOP10_SLOT"),
        "catalog_coverage_at_10": ratio_metric(
            top10.candidate_movie_id.nunique(), candidates[candidates.candidate_supported].candidate_movie_id.nunique(),
            "SUPPORTED_UNIQUE_CANDIDATE"
        ),
        "unknown_slot_fraction_at_10": ratio_metric(unknown_slots, top_slots, "TOP10_SLOT"),
        "candidate_interpretation": "UNKNOWN_SAMPLED slots are unjudged, not negative labels",
    }
    return metrics, ranked.reset_index()


def low_history_cohort(
    target: pd.DataFrame,
    candidates: pd.DataFrame,
    minimum_users: int = LOW_HISTORY_MIN_USERS_PER_BUCKET,
) -> list[dict]:
    full_target = target[target.is_full_history].copy()
    if full_target.empty:
        raise RuntimeError("LOW_HISTORY_COHORT requires full-history variants")
    if full_target.duplicated(["uid", "target_movie_id", "prediction_at", "n"]).any():
        raise RuntimeError("full-history variant is not unique per user-target-N")
    if not (full_target.n == full_target.total_history_count).all():
        raise RuntimeError("full-history variant does not use totalHistoryCount")
    full_target["cohort_bucket"] = full_target.total_history_count.map(n_bucket)
    full_candidates = candidates[candidates.is_full_history].copy()
    full_candidates["cohort_bucket"] = full_candidates.total_history_count.map(n_bucket)
    result = []
    for bucket in BUCKET_ORDER:
        targets = full_target[full_target.cohort_bucket == bucket]
        if targets.empty:
            continue
        pool = full_candidates[full_candidates.cohort_bucket == bucket]
        candidate_summary, _ = candidate_metrics(pool)
        user = targets.groupby("uid", sort=True)[["squared_error", "absolute_error"]].mean()
        result.append({
            "total_history_bucket": bucket,
            "rows": int(len(targets)),
            "users": int(targets.uid.nunique()),
            "target_rows": int(len(targets)),
            "unique_target_movies": int(targets.target_movie_id.nunique()),
            "sample_status": "PASS" if targets.uid.nunique() >= minimum_users else "INSUFFICIENT_SAMPLE",
            "minimum_users": int(minimum_users),
            "user_macro_mse": float(user.squared_error.mean()),
            "user_macro_mae": float(user.absolute_error.mean()),
            "candidate_metrics": candidate_summary,
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
        if not config.get("allow_no_eligible_profile", False):
            raise RuntimeError("no profile passed the nested-history selection gate")
        diagnostic = min(
            config["profiles"], key=lambda name: results[name][config["selection_metric"]]
        )
        return diagnostic, {
            "status": "NO_ELIGIBLE_PROFILE",
            "policy": gate,
            "eligible_profiles": [],
            "diagnostic_profile": diagnostic,
            "profiles": decisions,
        }
    selected = min(eligible, key=lambda name: results[name][config["selection_metric"]])
    return selected, {
        "status": "PASS",
        "policy": gate,
        "eligible_profiles": eligible,
        "profiles": decisions,
    }


def evaluate_profile(
    root: Path,
    seed: int,
    evaluation_split: str | None = None,
) -> tuple[dict, pd.DataFrame]:
    target = pd.read_parquet(root / "validation-target-predictions.parquet")
    candidates = pd.read_parquet(root / "validation-candidate-predictions.parquet")
    if evaluation_split:
        if "evaluation_split" not in target.columns or "evaluation_split" not in candidates.columns:
            raise RuntimeError("predictions do not contain evaluation_split")
        target = target[target.evaluation_split == evaluation_split].copy()
        candidates = candidates[candidates.evaluation_split == evaluation_split].copy()
        if target.empty or candidates.empty:
            raise RuntimeError(f"no rows for evaluation split {evaluation_split}")
    required = {
        "total_history_count", "provided_history_count", "supported_history_count",
        "is_full_history", "is_controlled_prefix",
    }
    if not required.issubset(target.columns) or not required.issubset(candidates.columns):
        raise RuntimeError("predictions do not contain the v7 arbitrary-N history columns")
    if not (target.supported_history_count == target.n).all():
        raise RuntimeError("supportedHistoryCount differs from N")
    if not (target.provided_history_count == target.n).all():
        raise RuntimeError("providedHistoryCount differs from N")
    target["prediction"] = target.prediction.clip(0.5, 5)
    target["squared_error"] = (target.prediction - target.label) ** 2
    target["absolute_error"] = (target.prediction - target.label).abs()
    user = target.groupby("uid", sort=True)[["squared_error", "absolute_error"]].mean()
    target_by_n = target.groupby("n_bucket", sort=False).agg(
        rows=("episode_id", "size"), users=("uid", "nunique"),
        mse=("squared_error", "mean"), mae=("absolute_error", "mean"),
    ).reset_index().to_dict("records")
    candidate_summary, ranked = candidate_metrics(candidates)
    result = {
        "validation_user_macro_mse": float(user.squared_error.mean()),
        "validation_user_macro_mae": float(user.absolute_error.mean()),
        "validation_rows": int(len(target)),
        "validation_users": int(target.uid.nunique()),
        "target_by_n": target_by_n,
        "nested_history_paired_vs_n0": nested_history_curve(target, seed),
        "low_history_cohort": (
            low_history_cohort(target, candidates) if target.is_full_history.any() else []
        ),
        "low_history_cohort_status": (
            "NOT_EVALUATED_CONTROLLED_PREFIX"
            if target.is_controlled_prefix.all() else "EVALUATED"
        ),
        "low_history_cohort_reason": (
            "controlled prefixes do not represent an actual low-history population"
            if target.is_controlled_prefix.all() else None
        ),
        "candidate_metrics": candidate_summary,
        "candidate_episodes": candidate_summary["episodes"],
        "observed_ndcg_at_10": candidate_summary["observed_ndcg_at_10_user_macro"],
        "observed_positive_recall_at_10": candidate_summary["observed_positive_recall_at_10"]["value"],
        "observed_low_rating_exposure_at_10": candidate_summary[
            "observed_low_rating_exposure_at_10"]["value"],
        "observed_target_top10_rate": candidate_summary["observed_target_top10_rate"]["value"],
        "unknown_slot_fraction_at_10": candidate_summary["unknown_slot_fraction_at_10"]["value"],
    }
    joined = target[["episode_id", "uid", "n_bucket", "squared_error"]].merge(
        ranked[["episode_id", "observed_ndcg_at_10"]], on="episode_id", validate="one_to_one"
    )
    return result, joined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evaluation-split", choices=["SELECTION", "CONFIRM"])
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    results = {}
    rows = {}
    for profile in config["profiles"]:
        results[profile], rows[profile] = evaluate_profile(
            args.fits_root / profile, config["seed"], args.evaluation_split
        )
    baseline_profile = config.get(
        "comparison_baseline_profile",
        "movie_only" if "movie_only" in rows else config["profiles"][0],
    )
    baseline = rows[baseline_profile].rename(columns={
        "squared_error": "baseline_squared_error", "observed_ndcg_at_10": "baseline_ndcg",
    })
    paired = {}
    for profile, frame in rows.items():
        joined = frame.merge(baseline, on=["episode_id", "uid", "n_bucket"], validate="one_to_one")
        user_mse_delta = joined.assign(
            mse_delta=joined.squared_error - joined.baseline_squared_error
        ).groupby("uid", sort=True).mse_delta.mean()
        ndcg_delta = joined.observed_ndcg_at_10 - joined.baseline_ndcg
        paired[profile] = {
            f"mean_squared_error_delta_vs_{baseline_profile}": float(
                (joined.squared_error - joined.baseline_squared_error).mean()
            ),
            f"mean_ndcg_at_10_delta_vs_{baseline_profile}": (
                None if ndcg_delta.dropna().empty else float(ndcg_delta.mean())
            ),
            "worse_episode_fraction_by_squared_error": float(
                (joined.squared_error > joined.baseline_squared_error).mean()
            ),
            f"user_macro_mse_delta_vs_{baseline_profile}": float(user_mse_delta.mean()),
            f"user_macro_mse_delta_vs_{baseline_profile}_bootstrap_95_ci": bootstrap_mean_ci(
                user_mse_delta.to_numpy(dtype=float), config["seed"] + config["profiles"].index(profile)
            ),
            "bootstrap": {"unit": "USER", "replicates": 2000, "seed_base": config["seed"]},
        }
    selected, selection_gate = select_profile(results, config)
    selected_low = results[selected]["low_history_cohort"]
    if results[selected]["low_history_cohort_status"] == "NOT_EVALUATED_CONTROLLED_PREFIX":
        low_history_status = "NOT_EVALUATED_CONTROLLED_PREFIX"
    else:
        low_history_status = (
            "PASS" if selected_low and all(row["sample_status"] == "PASS" for row in selected_low)
            else "INSUFFICIENT_SAMPLE"
        )
    ndcg_status = results[selected]["candidate_metrics"]["observed_ndcg_at_10_status"]
    report = {
        "status": "PASS",
        "calculator": {
            "version": "GBT_ZERO_N_EVALUATOR_V4_ARBITRARY_N_AND_COHORTS",
            "path": "scripts/gbt_zero_n_evaluate.py",
            "sha256": file_sha256(Path(__file__)),
        },
        "selection_metric": config["selection_metric"],
        "evaluation_split": args.evaluation_split or "ALL_VALIDATION",
        "comparison_baseline_profile": baseline_profile,
        "selection_gate": selection_gate,
        "selected_profile_on_validation": selected,
        "profiles": results,
        "paired": paired,
        "requirements_coverage": {
            "nested_history": "PASS",
            "low_history_cohort": low_history_status,
            "candidate_denominators": "PASS",
            "arbitrary_full_n": "PASS",
            "ndcg_at_10": ndcg_status,
        },
        "final_test_opened": False,
    }
    output = args.output or (args.fits_root / "validation-report.json")
    if args.output is not None and output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
