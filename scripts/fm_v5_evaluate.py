"""Evaluate frozen FM-v5 predictions after opening only sealed VALIDATION labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fm_v2_train import file_pin


KEYS = ["episode_id", "uid", "target_movie_id", "candidate_movie_id", "prediction_at", "n"]
CUTOFFS = (2, 4, 6, 10)


def _assert_same_keys(left: pd.DataFrame, right: pd.DataFrame) -> None:
    if len(left) != len(right):
        raise RuntimeError("prediction/label row counts differ")
    for name in KEYS:
        if not np.array_equal(left[name].to_numpy(), right[name].to_numpy()):
            raise RuntimeError(f"prediction/label row order differs: {name}")


def _episode_metadata(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop_duplicates("episode_id")[[
        "episode_id", "uid", "target_movie_id", "prediction_at", "n", "n_bucket",
        "total_history_count", "supported_history_count", "is_full_history",
    ]].reset_index(drop=True)


def _ranking_contributions(frame: pd.DataFrame, score: np.ndarray, profile: str) -> pd.DataFrame:
    episode_ids = frame["episode_id"].to_numpy()
    codes, unique = pd.factorize(episode_ids, sort=False)
    candidate = frame["candidate_movie_id"].to_numpy(np.int64)
    order = np.lexsort((candidate, -np.asarray(score, dtype=np.float64), codes))
    sorted_codes = codes[order]
    starts = np.flatnonzero(np.r_[True, sorted_codes[1:] != sorted_codes[:-1]])
    ends = np.r_[starts[1:], len(order)]
    rating = frame["observed_rating"].to_numpy(np.float64)
    state = frame["label_state"].to_numpy()
    is_target = frame["is_target"].to_numpy(bool)
    metadata = _episode_metadata(frame).set_index("episode_id")
    rows = []
    for start, end in zip(starts, ends):
        idx = order[start:end]
        episode_id = episode_ids[idx[0]]
        observed = np.isfinite(rating[idx])
        observed_ratings = rating[idx][observed]
        positive_total = int(np.sum(state[idx] == "POSITIVE_OBSERVED"))
        item = metadata.loc[episode_id].to_dict() | {"episode_id": episode_id, "profile": profile,
                                                     "candidate_rows": len(idx),
                                                     "observed_judgments": int(observed.sum()),
                                                     "positive_judgments": positive_total}
        for cutoff in CUTOFFS:
            top = idx[:cutoff]
            top_observed = np.isfinite(rating[top])
            gains = np.where(top_observed, np.power(2.0, rating[top] - 0.5) - 1.0, 0.0)
            dcg = float(np.sum(gains / np.log2(np.arange(len(top)) + 2.0)))
            eligible = len(observed_ratings) >= cutoff
            ideal = np.sort(observed_ratings)[::-1][:cutoff] if eligible else np.asarray([])
            idcg = float(np.sum((np.power(2.0, ideal - 0.5) - 1.0) /
                                np.log2(np.arange(len(ideal)) + 2.0))) if eligible else 0.0
            item[f"ndcg_at_{cutoff}"] = dcg / idcg if eligible and idcg > 0 else np.nan
            item[f"positive_recall_at_{cutoff}"] = (float(np.sum(state[top] == "POSITIVE_OBSERVED")) /
                                                     positive_total if positive_total else np.nan)
            item[f"low_exposure_at_{cutoff}"] = (float(np.mean(rating[top][top_observed] <= 2.0))
                                                  if top_observed.any() else np.nan)
            item[f"unknown_fraction_at_{cutoff}"] = float(np.mean(state[top] == "UNKNOWN_SAMPLED"))
            item[f"target_hit_at_{cutoff}"] = float(np.any(is_target[top]))
            item[f"support_at_{cutoff}"] = len(top) / cutoff
            item[f"unique_rate_at_{cutoff}"] = len(np.unique(candidate[top])) / max(1, len(top))
        rows.append(item)
    result = pd.DataFrame(rows)
    if set(result.episode_id) != set(unique):
        raise RuntimeError("ranking contribution episode set mismatch")
    return result


def _user_macro(contributions: pd.DataFrame, metric: str) -> tuple[float | None, int]:
    users = contributions.groupby("uid", sort=False)[metric].mean().dropna()
    return (float(users.mean()), int(len(users))) if len(users) else (None, 0)


def _summary(contributions: pd.DataFrame) -> dict:
    metrics = {}
    for cutoff in CUTOFFS:
        for stem in ("ndcg", "positive_recall", "low_exposure", "unknown_fraction", "target_hit",
                     "support", "unique_rate"):
            name = f"{stem}_at_{cutoff}"
            value, users = _user_macro(contributions, name)
            metrics[name] = {"value": value, "users": users}
    return {"episodes": len(contributions), "users": int(contributions.uid.nunique()), "metrics": metrics}


def _paired_user(contributions_a: pd.DataFrame, contributions_b: pd.DataFrame, metric: str,
                 seed: int, replicates: int) -> dict:
    a = contributions_a.groupby("uid", sort=True)[metric].mean()
    b = contributions_b.groupby("uid", sort=True)[metric].mean()
    joined = pd.concat((a.rename("a"), b.rename("b")), axis=1).dropna()
    delta = (joined.a - joined.b).to_numpy(np.float64)
    if len(delta) == 0:
        return {"status": "INSUFFICIENT_SAMPLE", "users": 0, "delta": None, "ci95": [None, None]}
    rng = np.random.default_rng(seed)
    draws = np.empty(replicates, dtype=np.float64)
    for start in range(0, replicates, 250):
        width = min(250, replicates - start)
        sample = rng.integers(0, len(delta), size=(width, len(delta)))
        draws[start:start + width] = delta[sample].mean(axis=1)
    return {"status": "PASS", "users": len(delta), "delta": float(delta.mean()),
            "ci95": [float(value) for value in np.quantile(draws, [0.025, 0.975])],
            "worse_user_fraction": float(np.mean(delta < 0.0)),
            "bootstrap": {"replicates": replicates, "seed": seed, "unit": "USER"}}


def _target_errors(predictions: pd.DataFrame, labels: pd.DataFrame, profiles: list[str]) -> dict:
    _assert_same_keys(predictions, labels)
    actual = labels["observed_rating"].to_numpy(np.float64)
    result = {}
    for profile in profiles:
        predicted = predictions[profile].to_numpy(np.float64)
        temp = pd.DataFrame({"uid": predictions.uid, "squared": np.square(predicted - actual),
                             "absolute": np.abs(predicted - actual)})
        result[profile] = {
            "user_macro_mse": float(temp.groupby("uid").squared.mean().mean()),
            "user_macro_mae": float(temp.groupby("uid").absolute.mean().mean()),
            "rows": len(temp), "users": int(temp.uid.nunique()),
        }
    return result


def evaluate(args: argparse.Namespace) -> dict:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    fit_report = json.loads((args.fits_root / "run-report.json").read_text(encoding="utf-8"))
    if fit_report.get("status") != "PASS" or fit_report.get("validation_labels_read") is not False:
        raise RuntimeError("fit report is not a sealed PASS")
    label_manifest = json.loads((args.labels_root / "manifest.json").read_text(encoding="utf-8"))
    if label_manifest.get("status") != "SEALED_VALIDATION_LABELS" or label_manifest.get("final_test") != "NOT_PRESENT":
        raise RuntimeError("validation label manifest contract failed")
    prediction_path = args.fits_root / "validation-candidate-predictions.parquet"
    prediction_schema = pq.ParquetFile(prediction_path).schema_arrow.names
    seed_columns = [name for name in prediction_schema if name.startswith("sparse_history_content_fm_v5__seed_")]
    prediction_columns = list(dict.fromkeys(KEYS + [
        "n_bucket", "total_history_count", "supported_history_count", "is_full_history",
        "candidate_rank", "is_target", "supported", "popularity_bayes_v5", "content_linear_v5",
        "sparse_linear_v5", "sparse_history_fm_v5", "sparse_history_content_fm_v5",
    ] + seed_columns))
    candidate_predictions = pq.read_table(prediction_path, columns=prediction_columns).to_pandas()
    candidate_labels = pq.read_table(
        args.labels_root / "validation-candidate-labels.parquet",
        columns=KEYS + ["label_state", "observed_rating", "sampling_probability", "importance_weight"],
    ).to_pandas()
    _assert_same_keys(candidate_predictions, candidate_labels)
    candidate = pd.concat((candidate_predictions, candidate_labels[["label_state", "observed_rating",
                                                                      "sampling_probability", "importance_weight"]]),
                          axis=1)
    base_profiles = ["popularity_bayes_v5", "content_linear_v5", "sparse_linear_v5",
                     "sparse_history_fm_v5", "sparse_history_content_fm_v5"]
    contributions = {profile: _ranking_contributions(candidate, candidate[profile].to_numpy(), profile)
                     for profile in base_profiles}
    seed_profiles = seed_columns
    seed_contributions = {profile: _ranking_contributions(candidate, candidate[profile].to_numpy(), profile)
                          for profile in seed_profiles}
    popularity_value = _summary(contributions["popularity_bayes_v5"])["metrics"]["ndcg_at_2"]["value"]
    content_value = _summary(contributions["content_linear_v5"])["metrics"]["ndcg_at_2"]["value"]
    baseline = "content_linear_v5" if (content_value or float("-inf")) >= (popularity_value or float("-inf")) \
        else "popularity_bayes_v5"
    fm = "sparse_history_content_fm_v5"
    policy_contributions = {}
    policy_scores = {}
    n_values = candidate["n"].to_numpy(np.float64)
    base_score = candidate[baseline].to_numpy(np.float64)
    fm_score = candidate[fm].to_numpy(np.float64)
    for threshold in config["policy"]["thresholds"]:
        hard_name = f"hard_switch_t{threshold}"
        blend_name = f"linear_blend_t{threshold}"
        policy_scores[hard_name] = np.where(n_values < threshold, base_score, fm_score)
        alpha = np.where(n_values == 0, 0.0, np.minimum(1.0, n_values / threshold))
        policy_scores[blend_name] = (1.0 - alpha) * base_score + alpha * fm_score
    for name, score in policy_scores.items():
        policy_contributions[name] = _ranking_contributions(candidate, score, name)
    summaries = {name: _summary(value) for name, value in contributions.items()}
    summaries.update({name: _summary(value) for name, value in policy_contributions.items()})
    paired = {name: _paired_user(value, contributions[baseline], "ndcg_at_2",
                                 int(config["uncertainty"]["bootstrap_seed"]),
                                 int(config["uncertainty"]["bootstrap_replicates"]))
              for name, value in (contributions | policy_contributions | seed_contributions).items()
              if name != baseline}
    thresholds = {}
    selected_policy = None
    baseline_low = summaries[baseline]["metrics"]["low_exposure_at_2"]["value"]
    baseline_unique = summaries[baseline]["metrics"]["unique_rate_at_2"]["value"]
    for threshold in config["policy"]["thresholds"]:
        eligible = contributions[fm][contributions[fm].n >= threshold]
        eligible_base = contributions[baseline][contributions[baseline].n >= threshold]
        comparison = _paired_user(eligible, eligible_base, "ndcg_at_2",
                                  int(config["uncertainty"]["bootstrap_seed"]),
                                  int(config["uncertainty"]["bootstrap_replicates"]))
        seed_points = []
        for value in seed_contributions.values():
            seed_points.append(_paired_user(value[value.n >= threshold], eligible_base, "ndcg_at_2", 339, 1000)["delta"])
        thresholds[str(threshold)] = {"comparison": comparison, "seed_deltas": seed_points,
                                      "supported_users": comparison["users"]}
        fm_low, _ = _user_macro(eligible, "low_exposure_at_2")
        base_low, _ = _user_macro(eligible_base, "low_exposure_at_2")
        fm_unique, _ = _user_macro(eligible, "unique_rate_at_2")
        base_unique, _ = _user_macro(eligible_base, "unique_rate_at_2")
        safety = {
            "low_exposure_delta": (fm_low - base_low) if fm_low is not None and base_low is not None else None,
            "unique_rate_delta": (fm_unique - base_unique)
            if fm_unique is not None and base_unique is not None else None,
        }
        thresholds[str(threshold)]["safety"] = safety
        if (selected_policy is None and comparison["delta"] is not None and comparison["delta"] > 0 and
                comparison.get("worse_user_fraction", 1.0) < 0.5 and
                all(value is not None and value > 0 for value in seed_points) and
                safety["low_exposure_delta"] is not None and safety["low_exposure_delta"] <= 0.02 and
                safety["unique_rate_delta"] is not None and safety["unique_rate_delta"] >= 0.0):
            selected_policy = f"hard_switch_t{threshold}"
    if selected_policy is None:
        best = max(policy_contributions, key=lambda name: summaries[name]["metrics"]["ndcg_at_2"]["value"]
                   if summaries[name]["metrics"]["ndcg_at_2"]["value"] is not None else float("-inf"))
        delta = paired[best]["delta"]
        best_low = summaries[best]["metrics"]["low_exposure_at_2"]["value"]
        best_unique = summaries[best]["metrics"]["unique_rate_at_2"]["value"]
        selected_policy = (best if delta is not None and delta > 0 and
                           paired[best].get("worse_user_fraction", 1.0) < 0.5 and
                           best_low is not None and baseline_low is not None and best_low - baseline_low <= 0.02 and
                           best_unique is not None and baseline_unique is not None and best_unique >= baseline_unique
                           else None)
    k_status = "CANDIDATE" if selected_policy else "NOT_SUPPORTED"
    transition_status = "VALIDATION_REQUIRED" if selected_policy else "NOT_EVALUATED"
    interaction_delta = paired[fm]["delta"]
    interaction_status = "CANDIDATE" if interaction_delta is not None and interaction_delta > 0 else "NOT_SUPPORTED"
    target_predictions = pq.read_table(
        args.fits_root / "validation-target-predictions.parquet", columns=KEYS + base_profiles
    ).to_pandas()
    target_labels = pq.read_table(
        args.labels_root / "validation-target-labels.parquet", columns=KEYS + ["observed_rating", "label_state"]
    ).to_pandas()
    target_metrics = _target_errors(target_predictions, target_labels, base_profiles)
    full_history = contributions[fm][contributions[fm].is_full_history]
    def total_bucket(value: int) -> str:
        if value == 0: return "0"
        if value == 1: return "1"
        if value == 2: return "2"
        if value <= 4: return "3-4"
        if value <= 9: return "5-9"
        if value <= 19: return "10-19"
        if value <= 29: return "20-29"
        if value <= 49: return "30-49"
        return "50+"
    low_history_counts = (full_history.assign(
        total_history_bucket=full_history.total_history_count.map(total_bucket)
    ).groupby("total_history_bucket").agg(users=("uid", "nunique"), episodes=("episode_id", "nunique"))
                          .to_dict(orient="index"))
    selected_contributions = policy_contributions[selected_policy] if selected_policy else contributions[fm]
    pq.write_table(pa.Table.from_pandas(selected_contributions, preserve_index=False),
                   args.output_root / "selected-policy-contributions.parquet", compression="zstd")
    report = {
        "schema_version": 5, "status": "PASS", "claim_ceiling": "CANDIDATE",
        "baseline_selected_on_validation": baseline, "selected_policy": selected_policy,
        "K_POLICY_STATUS": k_status, "TRANSITION_THRESHOLD_STATUS": transition_status,
        "FM_INTERACTION_STATUS": interaction_status,
        "summaries": summaries, "paired_ndcg_at_2": paired, "threshold_regions": thresholds,
        "seed_profiles": seed_profiles, "target_error_metrics_secondary": target_metrics,
        "LOW_HISTORY_COHORT": {"counts": low_history_counts,
                               "status": "INSUFFICIENT_SAMPLE" if any(
                                   int(value["users"]) < int(config["minimum_users_per_required_cell"])
                                   for key, value in low_history_counts.items() if key != "0") else "PASS"},
        "distribution_status": "DISTRIBUTION_SHIFT",
        "limitations": [
            "VALIDATION was previously inspected by completed GBT v7; no new VALIDATED claim is allowed",
            "actual LOW_HISTORY_COHORT decision cells are insufficient in the completed GBT audit",
            "director, actor, keyword, verified TMDB, and verified KOBIS namespaces are unavailable",
        ],
        "artifacts": {"candidate_predictions": file_pin(args.fits_root / "validation-candidate-predictions.parquet"),
                      "candidate_labels": file_pin(args.labels_root / "validation-candidate-labels.parquet"),
                      "selected_contributions": file_pin(args.output_root / "selected-policy-contributions.parquet")},
        "validation_labels_opened": True, "final_test_opened": False,
    }
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--labels-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("evaluation output must be an existing empty isolated directory")
    report = evaluate(args)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
