"""Evaluate validation predictions without treating UNKNOWN as negative or opening FINAL_TEST."""

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


def file_pin(path: Path) -> dict:
    return {"bytes": path.stat().st_size, "sha256": file_sha256(path)}


def tree_pin(path: Path) -> dict:
    files = {str(item.relative_to(path)).replace("\\", "/"): file_pin(item)
             for item in sorted(path.rglob("*")) if item.is_file()}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"bytes": sum(int(value["bytes"]) for value in files.values()),
            "sha256": digest, "files": len(files)}


def load_fit(root: Path, profile: str, seed: int, config: dict) -> tuple[dict, dict]:
    metrics_path = root / "metrics.json"
    if not metrics_path.exists():
        raise RuntimeError(f"missing configured seed fit: {profile} seed {seed}")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if (metrics.get("status") != "PASS" or metrics.get("profile") != profile or
            int(metrics.get("seed", -1)) != seed or metrics.get("final_test_opened") is not False or
            metrics.get("runtime_image_id") != config["runtime"]["image_id"]):
        raise RuntimeError(f"fit metrics contract mismatch: {profile} seed {seed}")
    target = tree_pin(root / "validation-target-predictions.parquet")
    candidates = tree_pin(root / "validation-candidate-predictions.parquet")
    if (target["sha256"] != metrics["target_predictions_artifact"] or
            candidates["sha256"] != metrics["candidate_predictions_artifact"]):
        raise RuntimeError(f"prediction artifact hash mismatch: {profile} seed {seed}")
    artifacts = {"metrics": file_pin(metrics_path), "target_predictions": target,
                 "candidate_predictions": candidates}
    return metrics, artifacts


def bootstrap_ci(values: np.ndarray, seed: int, replicates: int) -> list[float] | str:
    values = values[np.isfinite(values)]
    if values.size < 2:
        return "INSUFFICIENT_SAMPLE"
    rng = np.random.default_rng(seed)
    output = np.empty(replicates, dtype=float)
    for start in range(0, replicates, 100):
        size = min(100, replicates - start)
        output[start:start + size] = values[rng.integers(0, values.size, size=(size, values.size))].mean(axis=1)
    return [float(value) for value in np.quantile(output, [0.025, 0.975])]


def history_bucket(value: int) -> str:
    for upper, label in ((0, "K0"), (1, "K1"), (2, "K2"), (4, "K3_4"), (9, "K5_9"),
                         (19, "K10_19"), (29, "K20_29"), (49, "K30_49")):
        if value <= upper:
            return label
    return "K50_PLUS"


def diversity(frame: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    per_episode = []
    for episode_id, group in frame.groupby("episode_id", sort=False):
        sets = [set(value.split("|")) if isinstance(value, str) and value else set() for value in group.candidate_genres]
        union = set().union(*sets) if sets else set()
        distances = []
        for left in range(len(sets)):
            for right in range(left + 1, len(sets)):
                combined = sets[left] | sets[right]
                distances.append(1.0 - len(sets[left] & sets[right]) / len(combined) if combined else 0.0)
        per_episode.append((episode_id, len(union), float(np.mean(distances)) if distances else 0.0))
    detail = pd.DataFrame(per_episode, columns=["episode_id", "distinct_genres_at_10", "genre_jaccard_distance_at_10"])
    return ({"mean_distinct_genres_at_10": float(detail.distinct_genres_at_10.mean()),
             "mean_pairwise_genre_jaccard_distance_at_10": float(detail.genre_jaccard_distance_at_10.mean())}, detail)


def rank_candidates(path: Path, cutoff: int) -> tuple[pd.DataFrame, dict]:
    columns = ["episode_id", "uid", "n", "n_bucket", "total_history_count", "candidate_movie_id",
               "candidate_genres", "label", "label_state", "is_target", "supported", "prediction"]
    frame = pd.read_parquet(path, columns=columns)
    if frame.empty:
        raise RuntimeError("candidate predictions are empty")
    if frame.label_state.eq("UNKNOWN_SAMPLED").where(frame.label.notna(), False).any():
        raise RuntimeError("UNKNOWN_SAMPLED has an observed label")
    frame["support_sort"] = frame.supported & frame.prediction.notna()
    frame = frame.sort_values(["episode_id", "support_sort", "prediction", "candidate_movie_id"],
                              ascending=[True, False, False, True], na_position="last")
    frame["model_rank"] = frame.groupby("episode_id", sort=False).cumcount()
    frame["gain"] = ((frame.label >= 4.0) & frame.label.notna()).astype(float)
    frame["discounted_gain"] = np.where(frame.model_rank < cutoff,
                                         frame.gain / np.log2(frame.model_rank + 2), 0.0)
    grouped = frame.groupby("episode_id", sort=False).agg(
        uid=("uid", "first"), n=("n", "first"), n_bucket=("n_bucket", "first"),
        total_history_count=("total_history_count", "first"), dcg=("discounted_gain", "sum"),
        positives=("gain", "sum"), candidate_count=("candidate_movie_id", "size"),
        supported_candidates=("support_sort", "sum"), target_present=("is_target", "max"),
    )
    grouped["idcg"] = grouped.positives.map(lambda count: sum(1 / math.log2(rank + 2) for rank in range(min(int(count), cutoff))))
    grouped["observed_ndcg_at_10"] = grouped.dcg / grouped.idcg.where(grouped.idcg > 0)
    top = frame[frame.model_rank < cutoff]
    target = frame[frame.is_target][["episode_id", "model_rank"]].set_index("episode_id")
    grouped["target_rank"] = target.model_rank
    grouped["target_recall_at_10"] = grouped.target_rank < cutoff
    top_detail = top.groupby("episode_id", sort=False).agg(
        observed_top=("label", "count"), low_rating_top=("label", lambda values: int(((values < 4.0) & values.notna()).sum())),
        unknown_top=("label_state", lambda values: int((values == "UNKNOWN_SAMPLED").sum())),
    )
    grouped = grouped.join(top_detail, how="left")
    grouped["low_rating_exposure_at_10"] = grouped.low_rating_top / grouped.observed_top.where(grouped.observed_top > 0)
    grouped["unknown_fraction_at_10"] = grouped.unknown_top / cutoff
    observed_top = top[top.label.notna()]
    diversity_metrics, diversity_detail = diversity(top)
    grouped = grouped.reset_index().merge(diversity_detail, on="episode_id", how="left", validate="one_to_one").set_index("episode_id")
    metrics = {
        "candidate_recall": float(grouped.target_present.mean()),
        "target_recall_at_10": float(grouped.target_recall_at_10.mean()),
        "observed_ndcg_at_10": float(grouped.observed_ndcg_at_10.mean()),
        "observed_positive_recall_at_10": float(top.groupby("episode_id").gain.sum().sum() / grouped.positives.sum()),
        "low_rating_exposure_at_10_among_observed": float((observed_top.label < 4.0).mean()) if len(observed_top) else None,
        "unknown_sampled_fraction_at_10": float(top.label_state.eq("UNKNOWN_SAMPLED").mean()),
        "model_support_rate": float(frame.support_sort.mean()),
        "candidate_missing_rate": float((~frame.support_sort).mean()),
        "unsupported_candidates": int((~frame.support_sort).sum()),
        "unknown_ranking_policy": "UNKNOWN_SAMPLED remained in its scored rank position and was never relabeled or removed",
        **diversity_metrics,
    }
    return grouped.reset_index(), metrics


def nested_history(target: pd.DataFrame, episode_metrics: pd.DataFrame, seed: int, replicates: int) -> list[dict]:
    keys = ["uid", "target_movie_id", "prediction_at"]
    combined = target.merge(episode_metrics[["episode_id", "observed_ndcg_at_10"]], on="episode_id", how="left", validate="one_to_one")
    baseline = combined[combined.n == 0][keys + ["squared_error", "absolute_error", "observed_ndcg_at_10"]]
    baseline = baseline.rename(columns={name: f"baseline_{name}" for name in ("squared_error", "absolute_error", "observed_ndcg_at_10")})
    if baseline.duplicated(keys).any():
        raise RuntimeError("N=0 baseline is not unique")
    paired = combined[combined.n > 0].merge(baseline, on=keys, validate="many_to_one")
    if len(paired) != int((combined.n > 0).sum()):
        raise RuntimeError("nonzero-N row is missing its paired N=0 row")
    paired["mse_delta"] = paired.squared_error - paired.baseline_squared_error
    paired["mae_delta"] = paired.absolute_error - paired.baseline_absolute_error
    paired["ndcg_delta"] = paired.observed_ndcg_at_10 - paired.baseline_observed_ndcg_at_10
    result = []
    for index, bucket in enumerate(("1", "2", "3-4", "5-9", "10-19", "20-29", "30-49", "50+")):
        frame = paired[paired.n_bucket == bucket]
        if frame.empty:
            continue
        users = frame.groupby("uid", sort=True).agg(mse_delta=("mse_delta", "mean"), mae_delta=("mae_delta", "mean"), ndcg_delta=("ndcg_delta", "mean"))
        result.append({
            "n_bucket": "K50_PLUS" if bucket == "50+" else "K" + bucket.replace("-", "_"),
            "actual_n_values": sorted(int(value) for value in frame.n.unique()),
            "rows": int(len(frame)), "users": int(frame.uid.nunique()),
            "mean_mse_delta_vs_same_target_n0": float(frame.mse_delta.mean()),
            "user_target_bootstrap_95_ci": bootstrap_ci(frame.mse_delta.to_numpy(float), seed + index, replicates),
            "user_macro_mse_delta_vs_n0": float(users.mse_delta.mean()),
            "user_bootstrap_95_ci": bootstrap_ci(users.mse_delta.to_numpy(float), seed + 100 + index, replicates),
            "mean_mae_delta_vs_same_target_n0": float(frame.mae_delta.mean()),
            "mean_observed_ndcg_delta_vs_same_target_n0": float(frame.ndcg_delta.mean()),
            "worsened_user_fraction_by_mse": float((users.mse_delta > 0).mean()),
        })
    return result


def low_history_cohort(target: pd.DataFrame, episode_metrics: pd.DataFrame, seed: int, replicates: int) -> list[dict]:
    columns = ["episode_id", "observed_ndcg_at_10", "candidate_count", "supported_candidates",
               "target_present", "target_recall_at_10", "low_rating_exposure_at_10", "unknown_fraction_at_10",
               "distinct_genres_at_10", "genre_jaccard_distance_at_10"]
    full = target.merge(episode_metrics[columns], on="episode_id", how="left", validate="one_to_one")
    full["history_bucket"] = full.total_history_count.map(history_bucket)
    full["is_full_history_variant"] = full.n == full.total_history_count
    result = []
    order = ("K0", "K1", "K2", "K3_4", "K5_9", "K10_19", "K20_29", "K30_49", "K50_PLUS")
    cell = 0
    for bucket in order:
        cohort = full[full.history_bucket == bucket]
        for n_bucket in ("0", "1", "2", "3-4", "5-9", "10-19", "20-29", "30-49", "50+"):
            frame = cohort[cohort.n_bucket == n_bucket]
            if frame.empty:
                continue
            users = frame.groupby("uid", sort=True).squared_error.mean()
            result.append({
                "history_bucket": bucket, "evaluated_n_bucket": "K50_PLUS" if n_bucket == "50+" else "K" + n_bucket.replace("-", "_"),
                "actual_n_values": sorted(int(value) for value in frame.n.unique()),
                "users": int(frame.uid.nunique()), "targets": int(len(frame)),
                "unique_movies": int(frame.target_movie_id.nunique()),
                "full_history_variant_rows": int(frame.is_full_history_variant.sum()),
                "partial_history_variant_rows": int((~frame.is_full_history_variant).sum()),
                "model_support_rate": float(frame.supported_candidates.sum() / frame.candidate_count.sum()),
                "candidate_missing_rate": float(1.0 - frame.supported_candidates.sum() / frame.candidate_count.sum()),
                "candidate_recall": float(frame.target_present.mean()),
                "target_recall_at_10": float(frame.target_recall_at_10.mean()),
                "mse": float(frame.squared_error.mean()), "mae": float(frame.absolute_error.mean()),
                "user_macro_mse": float(users.mean()),
                "user_macro_mse_bootstrap_95_ci": bootstrap_ci(users.to_numpy(float), seed + 200 + cell, replicates),
                "observed_ndcg_at_10": float(frame.observed_ndcg_at_10.mean()),
                "low_rating_exposure_at_10_among_observed": float(frame.low_rating_exposure_at_10.mean()),
                "unknown_sampled_fraction_at_10": float(frame.unknown_fraction_at_10.mean()),
                "mean_distinct_genres_at_10": float(frame.distinct_genres_at_10.mean()),
                "mean_pairwise_genre_jaccard_distance_at_10": float(frame.genre_jaccard_distance_at_10.mean()),
                "interpretation": "different-user cohort crossed with evaluated prefix N; partial prefixes are never called full history",
            })
            cell += 1
    return result


def evaluate_profile(root: Path, config: dict, seed: int) -> dict:
    target = pd.read_parquet(root / "validation-target-predictions.parquet",
                             columns=["episode_id", "uid", "target_movie_id", "prediction_at", "n", "n_bucket",
                                      "total_history_count", "label", "supported", "prediction"])
    if target.prediction.isna().any() or (~np.isfinite(target.prediction)).any():
        raise RuntimeError("target predictions contain null/NaN/Inf")
    low, high = config["prediction_bounds"]
    if ((target.prediction < low) | (target.prediction > high)).any():
        raise RuntimeError("final predictions are outside configured bounds")
    target["squared_error"] = (target.prediction - target.label) ** 2
    target["absolute_error"] = (target.prediction - target.label).abs()
    user = target.groupby("uid", sort=True).agg(mse=("squared_error", "mean"), mae=("absolute_error", "mean"))
    ranked, ranking = rank_candidates(root / "validation-candidate-predictions.parquet", int(config["ranking_cutoff"]))
    return {
        "validation_rows": int(len(target)), "validation_users": int(target.uid.nunique()),
        "validation_user_macro_mse": float(user.mse.mean()), "validation_user_macro_mae": float(user.mae.mean()),
        "validation_mse": float(target.squared_error.mean()), "validation_mae": float(target.absolute_error.mean()),
        "validation_user_macro_mse_bootstrap_95_ci": bootstrap_ci(user.mse.to_numpy(float), seed, int(config["bootstrap_replicates"])),
        "ranking": ranking,
        "nested_history": nested_history(target, ranked, seed, int(config["bootstrap_replicates"])),
        "low_history_cohort": low_history_cohort(target, ranked, seed, int(config["bootstrap_replicates"])),
    }


def evaluate(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    seed = int(config["model_seeds"][0])
    profiles = {}
    for profile in config["profiles"]:
        root = args.fits_root / f"{profile}-seed-{seed}"
        metrics, artifacts = load_fit(root, profile, seed, config)
        profiles[profile] = {**evaluate_profile(root, config, seed), "fit": metrics,
                             "artifacts": artifacts}
    factor_profiles = [profile for profile in config["profiles"] if profile != "sparse_linear_only"]
    selected = min(factor_profiles, key=lambda profile: profiles[profile]["validation_user_macro_mse"])
    sensitivity = []
    for candidate_seed in config["model_seeds"]:
        root = args.fits_root / f"{selected}-seed-{candidate_seed}"
        metrics, artifacts = load_fit(root, selected, int(candidate_seed), config)
        result = evaluate_profile(root, config, int(candidate_seed))
        sensitivity.append({"seed": int(candidate_seed), "validation_user_macro_mse": result["validation_user_macro_mse"],
                            "validation_user_macro_mae": result["validation_user_macro_mae"],
                            "observed_ndcg_at_10": result["ranking"]["observed_ndcg_at_10"],
                            "fit": metrics, "artifacts": artifacts})
    report = {
        "schema_version": 1, "status": "PASS", "calculator": {"path": "scripts/fm_zero_n_evaluate.py", "sha256": file_sha256(Path(__file__))},
        "profiles": profiles, "selected_exploratory_factor_profile": selected,
        "selection_policy": "lowest validation user-macro MSE among factor-interaction profiles; exploratory, not service adoption",
        "seed_sensitivity": sensitivity,
        "final_test_opened": False,
        "movie_lens_evidence": "WEAK_OFFLINE_EVIDENCE_FOR_A_DIFFERENT_POPULATION",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    evaluate(parser.parse_args())


if __name__ == "__main__":
    main()
