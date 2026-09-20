"""Evaluate exact-K routing on TRAIN-derived popularity and validation candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from gbt_zero_n_evaluate import bootstrap_mean_ci, candidate_metrics


POLICY_THRESHOLDS = (5, 10, 20, 40, 50)
PRIMARY_METRICS = (
    "target_positive_hit_at_10",
    "observed_positive_recall_at_10",
    "observed_ndcg_at_10",
)


def pin(path: Path) -> dict:
    files = [path] if path.is_file() else [item for item in sorted(path.rglob("*")) if item.is_file()]
    parts = {}
    for item in files:
        digest = hashlib.sha256()
        with item.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        name = item.name if path.is_file() else str(item.relative_to(path)).replace("\\", "/")
        parts[name] = {"bytes": item.stat().st_size, "sha256": digest.hexdigest()}
    semantic = hashlib.sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"algorithm": "SORTED_FILE_PINS_V1", "sha256": semantic, "files": parts}


def load_train_popularity(
    ratings_path: Path,
    train_user_ids: set[int],
    cutoff: int,
    prior_count: float,
) -> tuple[pd.DataFrame, dict]:
    pieces = []
    total_sum = 0.0
    total_count = 0
    for chunk in pd.read_csv(
        ratings_path,
        usecols=["userId", "movieId", "rating", "timestamp"],
        chunksize=1_000_000,
    ):
        selected = chunk[
            chunk.userId.isin(train_user_ids) & (chunk.timestamp <= cutoff)
        ][["movieId", "rating"]]
        if selected.empty:
            continue
        grouped = selected.groupby("movieId", sort=False).rating.agg(["count", "sum"])
        pieces.append(grouped)
        total_sum += float(selected.rating.sum())
        total_count += int(len(selected))
    if not pieces or total_count == 0:
        raise RuntimeError("TRAIN-only popularity source has no ratings")
    aggregate = pd.concat(pieces).groupby(level=0, sort=True).sum()
    global_mean = total_sum / total_count
    aggregate["count_score"] = np.log1p(aggregate["count"].to_numpy(dtype=float))
    aggregate["bayes_score"] = (
        aggregate["sum"] + prior_count * global_mean
    ) / (aggregate["count"] + prior_count)
    return aggregate[["count", "count_score", "bayes_score"]], {
        "users": len(train_user_ids),
        "ratings": total_count,
        "cutoff_inclusive": cutoff,
        "global_mean": global_mean,
        "bayes_prior_count": prior_count,
        "unseen_count_score": 0.0,
        "unseen_bayes_score": global_mean,
    }


def policy_scores(
    frame: pd.DataFrame,
    popularity: pd.DataFrame | None = None,
    global_mean: float | None = None,
) -> dict[str, np.ndarray]:
    embedded = {"policy.popular_count_score", "policy.popular_bayes_score"}
    if embedded.issubset(frame.columns):
        count_score = frame["policy.popular_count_score"].to_numpy(dtype=float)
        bayes_score = frame["policy.popular_bayes_score"].to_numpy(dtype=float)
    else:
        if popularity is None or global_mean is None:
            raise RuntimeError("popularity scores are neither embedded nor supplied")
        count_score = frame.candidate_movie_id.map(popularity.count_score).fillna(0.0).to_numpy(dtype=float)
        bayes_score = frame.candidate_movie_id.map(popularity.bayes_score).fillna(global_mean).to_numpy(dtype=float)
    gbt_score = frame.prediction.to_numpy(dtype=float)
    n = frame.n.to_numpy(dtype=int)
    result = {
        "POPULAR_COUNT": count_score,
        "POPULAR_BAYES": bayes_score,
        "GBT_ALL": gbt_score,
    }
    for threshold in POLICY_THRESHOLDS:
        result[f"COUNT_TO_GBT_K{threshold}"] = np.where(n >= threshold, gbt_score, count_score)
        result[f"BAYES_TO_GBT_K{threshold}"] = np.where(n >= threshold, gbt_score, bayes_score)
    return result


def evaluate_scores(frame: pd.DataFrame, scores: np.ndarray) -> tuple[dict, pd.DataFrame]:
    scored = frame.copy()
    scored["prediction"] = scores
    summary, episode = candidate_metrics(scored)
    episode_meta = scored[["episode_id", "n", "n_bucket", "is_full_history"]].drop_duplicates("episode_id")
    targets = scored[scored.is_target][["episode_id", "label"]].rename(columns={"label": "target_label"})
    episode = episode.merge(episode_meta, on="episode_id", validate="one_to_one")
    episode = episode.merge(targets, on="episode_id", validate="one_to_one")
    episode["target_positive_hit_at_10"] = np.where(
        episode.target_label >= 4.0, (episode.model_rank < 10).astype(float), np.nan
    )
    episode["observed_positive_recall_at_10"] = (
        episode.top10_positive / episode.positives.where(episode.positives > 0)
    )
    episode["observed_low_rating_exposure_at_10"] = (
        episode.top10_low / episode.top10_observed.where(episode.top10_observed > 0)
    )
    for metric in (*PRIMARY_METRICS, "observed_low_rating_exposure_at_10"):
        values = episode.groupby("uid", sort=True)[metric].mean().dropna()
        summary[f"{metric}_user_macro"] = None if values.empty else float(values.mean())
        summary[f"{metric}_users"] = int(len(values))
    summary["by_n_bucket"] = []
    for bucket, rows in episode.groupby("n_bucket", sort=False):
        item = {"n_bucket": str(bucket), "episodes": int(len(rows)), "users": int(rows.uid.nunique())}
        for metric in (*PRIMARY_METRICS, "observed_low_rating_exposure_at_10"):
            values = rows.groupby("uid", sort=True)[metric].mean().dropna()
            item[f"{metric}_user_macro"] = None if values.empty else float(values.mean())
            item[f"{metric}_users"] = int(len(values))
        summary["by_n_bucket"].append(item)
    return summary, episode


def paired_comparison(
    policy: pd.DataFrame,
    baseline: pd.DataFrame,
    metric: str,
    seed: int,
) -> tuple[dict, np.ndarray]:
    joined = policy[["episode_id", "uid", metric]].merge(
        baseline[["episode_id", metric]],
        on="episode_id",
        suffixes=("_policy", "_baseline"),
        validate="one_to_one",
    ).dropna()
    joined["delta"] = joined[f"{metric}_policy"] - joined[f"{metric}_baseline"]
    user_delta = joined.groupby("uid", sort=True).delta.mean().to_numpy(dtype=float)
    if user_delta.size == 0:
        return {"delta": None, "bootstrap_95_ci": None, "users": 0}, user_delta
    return {
        "delta": float(user_delta.mean()),
        "bootstrap_95_ci": bootstrap_mean_ci(user_delta, seed),
        "users": int(user_delta.size),
        "bootstrap": {"unit": "USER", "replicates": 2000},
    }, user_delta


def sign_flip_pvalue(values: np.ndarray, seed: int, replicates: int = 5000) -> float | None:
    if values.size == 0:
        return None
    observed = abs(float(values.mean()))
    rng = np.random.default_rng(seed)
    extreme = 0
    for start in range(0, replicates, 100):
        size = min(100, replicates - start)
        signs = rng.choice((-1.0, 1.0), size=(size, values.size))
        extreme += int((np.abs((signs * values).mean(axis=1)) >= observed).sum())
    return (extreme + 1) / (replicates + 1)


def holm_adjust(values: dict[str, float | None]) -> dict[str, float | None]:
    valid = sorted(((name, value) for name, value in values.items() if value is not None), key=lambda row: row[1])
    result = {name: None for name in values}
    running = 0.0
    count = len(valid)
    for index, (name, value) in enumerate(valid):
        running = max(running, min(1.0, (count - index) * float(value)))
        result[name] = running
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--train-targets", type=Path)
    parser.add_argument("--ratings", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-cutoff", type=int, default=1577836799)
    parser.add_argument("--bayes-prior-count", type=float, default=20.0)
    parser.add_argument("--seed", type=int, default=622)
    parser.add_argument("--evaluation-split", choices=["SELECTION", "CONFIRM"])
    parser.add_argument("--candidate-threshold", type=int, choices=POLICY_THRESHOLDS)
    parser.add_argument("--noninferiority-margin", type=float, default=0.02)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")

    available_columns = set(ds.dataset(args.predictions, format="parquet").schema.names)
    columns = [
        "episode_id", "uid", "candidate_movie_id", "n", "n_bucket", "total_history_count",
        "provided_history_count", "supported_history_count", "is_full_history", "is_controlled_prefix",
        "label", "label_state", "is_target", "prediction",
    ]
    if "evaluation_split" in available_columns:
        columns.append("evaluation_split")
    embedded_columns = {"policy.popular_count_score", "policy.popular_bayes_score"}
    if embedded_columns.issubset(available_columns):
        columns.extend(sorted(embedded_columns))
    frame = pd.read_parquet(args.predictions, columns=columns)
    if args.evaluation_split:
        if "evaluation_split" not in frame.columns:
            raise RuntimeError("predictions do not contain evaluation_split")
        frame = frame[frame.evaluation_split == args.evaluation_split].copy()
        if frame.empty:
            raise RuntimeError(f"no rows for evaluation split {args.evaluation_split}")
    if not (frame.provided_history_count == frame.n).all():
        raise RuntimeError("providedHistoryCount differs from exact K")
    if not (frame.supported_history_count == frame.n).all():
        raise RuntimeError("supportedHistoryCount differs from exact K")
    if embedded_columns.issubset(frame.columns):
        popularity = None
        popularity_meta = {
            "mode": "EMBEDDED_PER_EPISODE_AS_OF",
            "strictly_before_prediction_at": True,
            "source": "SELECTED_TRAIN_USERS_ONLY",
        }
        global_mean = None
    else:
        if args.train_targets is None or args.ratings is None:
            raise RuntimeError("legacy popularity evaluation requires --train-targets and --ratings")
        train_users = set(
            pd.read_parquet(args.train_targets, columns=["uid"]).uid.unique().astype(int).tolist()
        )
        popularity, popularity_meta = load_train_popularity(
            args.ratings, train_users, args.train_cutoff, args.bayes_prior_count
        )
        global_mean = popularity_meta["global_mean"]
    policies = policy_scores(frame, popularity, global_mean)
    summaries = {}
    episodes = {}
    for name, scores in policies.items():
        summaries[name], episodes[name] = evaluate_scores(frame, scores)

    comparisons = {}
    pvalues: dict[str, dict[str, float | None]] = {metric: {} for metric in PRIMARY_METRICS}
    for fallback in ("POPULAR_COUNT", "POPULAR_BAYES"):
        prefix = "COUNT" if fallback == "POPULAR_COUNT" else "BAYES"
        for threshold in POLICY_THRESHOLDS:
            name = f"{prefix}_TO_GBT_K{threshold}"
            comparisons[name] = {"baseline": fallback, "metrics": {}}
            for offset, metric in enumerate((*PRIMARY_METRICS, "observed_low_rating_exposure_at_10")):
                routed_policy = episodes[name][episodes[name].n >= threshold]
                routed_baseline = episodes[fallback][episodes[fallback].n >= threshold]
                item, user_delta = paired_comparison(
                    routed_policy, routed_baseline, metric, args.seed + threshold + offset
                )
                if metric in PRIMARY_METRICS:
                    raw_p = sign_flip_pvalue(user_delta, args.seed + 1000 + threshold + offset)
                    item["paired_sign_flip_two_sided_p"] = raw_p
                    pvalues[metric][name] = raw_p
                comparisons[name]["metrics"][metric] = item
    adjusted = {metric: holm_adjust(values) for metric, values in pvalues.items()}
    for metric, values in adjusted.items():
        for name, value in values.items():
            comparisons[name]["metrics"][metric]["holm_adjusted_p_across_ten_switch_policies"] = value

    unique_episodes = frame[["episode_id", "uid", "n", "n_bucket", "is_full_history"]].drop_duplicates()
    exact_threshold_counts = {
        str(threshold): {
            "episodes": int((unique_episodes.n == threshold).sum()),
            "users": int(unique_episodes.loc[unique_episodes.n == threshold, "uid"].nunique()),
        }
        for threshold in POLICY_THRESHOLDS
    }
    low_counts = (
        unique_episodes[unique_episodes.is_full_history]
        .groupby("n_bucket", sort=False).uid.nunique().astype(int).to_dict()
    )
    acceptance = {}
    for threshold in POLICY_THRESHOLDS:
        name = f"COUNT_TO_GBT_K{threshold}"
        metric_checks = {}
        for metric in ("observed_positive_recall_at_10", "observed_ndcg_at_10"):
            comparison = comparisons[name]["metrics"][metric]
            interval = comparison["bootstrap_95_ci"]
            metric_checks[metric] = bool(
                interval is not None and interval[0] >= -args.noninferiority_margin
            )
        acceptance[str(threshold)] = {
            "policy": name,
            "metric_checks": metric_checks,
            "accepted": all(metric_checks.values()),
        }
    accepted_thresholds = [
        threshold for threshold in POLICY_THRESHOLDS if acceptance[str(threshold)]["accepted"]
    ]
    sample_sufficient = all(
        exact_threshold_counts[str(threshold)]["users"] >= 100 for threshold in POLICY_THRESHOLDS
    )
    ndcg_available = summaries["GBT_ALL"]["observed_ndcg_at_10_status"] == "PASS"
    if args.evaluation_split == "SELECTION":
        threshold_status = "CANDIDATE_FOUND" if accepted_thresholds else "NO_ACCEPTABLE_THRESHOLD"
    elif args.evaluation_split == "CONFIRM" and args.candidate_threshold is not None:
        threshold_status = (
            "VALIDATED" if acceptance[str(args.candidate_threshold)]["accepted"]
            else "REJECTED_ON_CONFIRM"
        )
    elif args.evaluation_split == "CONFIRM":
        threshold_status = "NOT_TESTED_NO_SELECTION_CANDIDATE"
    else:
        threshold_status = "EXPLORATORY_ONLY"
    evidence_status = (
        "CONFIRMATORY" if args.evaluation_split == "CONFIRM" and sample_sufficient and ndcg_available
        else "SELECTION" if args.evaluation_split == "SELECTION" and sample_sufficient and ndcg_available
        else "INSUFFICIENT"
    )
    report = {
        "schema_version": 1,
        "status": "PASS_WITH_LIMITATIONS",
        "decision": "PROMOTE_CANDIDATE" if threshold_status == "VALIDATED" else "DO_NOT_PROMOTE",
        "k_policy_status": "CANDIDATE" if accepted_thresholds else "NOT_SUPPORTED",
        "k_policy_evidence": evidence_status,
        "transition_threshold_status": threshold_status,
        "same_pool_scope": "RERANK_ONLY_TARGET_FORCED_INTO_CANDIDATE_POOL",
        "evaluation_split": args.evaluation_split or "ALL_VALIDATION",
        "final_test_opened": False,
        "limitations": [
            "MovieLens exact-K rows are controlled input prefixes, not observed production new-user cohorts",
            "target movies are forced into the sampled candidate pool, so results are rerank-only",
            "UNKNOWN_SAMPLED candidates are unjudged and are not interpreted as negatives",
            "a confirmed threshold still requires production-domain validation before serving",
        ],
        "popularity_source": popularity_meta,
        "exact_threshold_counts": exact_threshold_counts,
        "low_history_users_by_bucket": {str(key): int(value) for key, value in low_counts.items()},
        "actual_low_history_status": (
            "NOT_EVALUATED_CONTROLLED_PREFIX"
            if frame.is_controlled_prefix.all() else "EVALUATED"
        ),
        "threshold_acceptance": {
            "noninferiority_margin": args.noninferiority_margin,
            "metrics": ["observed_positive_recall_at_10", "observed_ndcg_at_10"],
            "accepted_thresholds": accepted_thresholds,
            "candidate_threshold_under_test": args.candidate_threshold,
            "by_threshold": acceptance,
        },
        "policies": summaries,
        "paired_switch_vs_fallback": comparisons,
        "multiple_testing": {
            "method": "HOLM_OVER_TEN_SWITCH_POLICIES_PER_PRIMARY_METRIC",
            "raw_test": "USER_CLUSTER_PAIRED_SIGN_FLIP_TWO_SIDED",
        },
        "inputs": {
            "predictions": pin(args.predictions),
            **({"train_targets": pin(args.train_targets)} if args.train_targets else {}),
            **({"ratings": pin(args.ratings)} if args.ratings else {}),
        },
        "calculator": pin(Path(__file__)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "decision": report["decision"],
        "k_policy_status": report["k_policy_status"],
        "transition_threshold_status": report["transition_threshold_status"],
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
