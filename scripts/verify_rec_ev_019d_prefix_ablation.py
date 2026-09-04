#!/usr/bin/env python3
"""Independently recompute REC-EV-019D reported metrics from allowlisted raw artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019d-prefix-ablation-artifacts.json"
DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019d-validation.json"
AUDIT_AMENDMENT = ROOT / "docs/recommendation/contracts/rec-ev-019d-post-run-audit-amendment.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_contract(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def close(actual: float, expected: float, *, tolerance: float = 1e-10) -> bool:
    return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)


def bootstrap_paired(ndcg: np.ndarray, harm: np.ndarray, *, iterations: int, seed: int) -> dict[str, Any]:
    ndcg_values = np.asarray(ndcg, dtype=np.float64)
    harm_values = np.asarray(harm, dtype=np.float64)
    rng = np.random.default_rng(int(seed))
    ndcg_means = np.empty(int(iterations), dtype=np.float64)
    harm_means = np.empty(int(iterations), dtype=np.float64)
    offset = 0
    while offset < int(iterations):
        stop = min(offset + 250, int(iterations))
        indices = rng.integers(0, len(ndcg_values), size=(stop - offset, len(ndcg_values)))
        ndcg_means[offset:stop] = ndcg_values[indices].mean(axis=1)
        harm_means[offset:stop] = harm_values[indices].mean(axis=1)
        offset = stop
    return {
        "iterations": int(iterations),
        "seed": int(seed),
        "ndcg_mean": float(ndcg_values.mean()),
        "ndcg_two_sided_95": [float(np.percentile(ndcg_means, 2.5)), float(np.percentile(ndcg_means, 97.5))],
        "harm_mean": float(harm_values.mean()),
        "harm_one_sided_95_upper": float(np.percentile(harm_means, 95.0)),
    }


def decision(bootstrap: Mapping[str, Any]) -> dict[str, str]:
    lower, upper = map(float, bootstrap["ndcg_two_sided_95"])
    if float(bootstrap["harm_one_sided_95_upper"]) > 0.005:
        return {"status": "FAIL", "reason": "SAFETY_MARGIN_EXCEEDED"}
    if upper < 0.0:
        return {"status": "FAIL", "reason": "EFFICACY_INTERVAL_ENTIRELY_NEGATIVE"}
    if float(bootstrap["ndcg_mean"]) >= 0.005 and lower > 0.0 and float(bootstrap["harm_one_sided_95_upper"]) <= 0.005:
        return {"status": "PASS", "reason": "ALL_PREDECLARED_EFFICACY_AND_SAFETY_CRITERIA_MET"}
    return {"status": "INCONCLUSIVE", "reason": "PREDECLARED_SUCCESS_NOT_ESTABLISHED_WITHOUT_A_DECLARED_FAIL_CONDITION"}


def metrics_from_top500(
    prediction: pd.DataFrame,
    future: Sequence[Mapping[str, Any]],
    candidate_set: set[int],
) -> dict[str, Any]:
    ranked = prediction.sort_values("rank", kind="stable")
    ranked_ids = list(map(int, ranked["movie_id"]))
    rank_by_movie = {movie_id: rank for rank, movie_id in enumerate(ranked_ids, start=1)}
    positives = [row for row in future if bool(row["is_positive"]) and int(row["movie_id"]) in candidate_set]
    negatives = {int(row["movie_id"]) for row in future if bool(row["is_negative"])}
    gains = {int(row["movie_id"]): float(row["midrank_utility"]) for row in positives}
    ideal = sorted(gains.values(), reverse=True)[:10]
    idcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    dcg = sum(
        gains[movie_id] / math.log2(rank + 1)
        for movie_id, rank in rank_by_movie.items()
        if movie_id in gains and rank <= 10
    )
    first_positive = min((rank_by_movie[movie_id] for movie_id in gains if movie_id in rank_by_movie), default=None)
    return {
        "ndcg_at_10": float(dcg / idcg) if idcg else 0.0,
        "recall_at_10": float(sum(rank_by_movie.get(movie_id, 11) <= 10 for movie_id in gains) / len(gains)) if gains else 0.0,
        "mrr_at_10": float(1.0 / first_positive) if first_positive is not None and first_positive <= 10 else 0.0,
        "candidate_recall_at_500": float(any(movie_id in rank_by_movie for movie_id in gains)),
        "harm_at_2": bool(set(ranked_ids[:2]).intersection(negatives)),
    }


def midrank_percentiles(values: np.ndarray) -> np.ndarray:
    scores = np.asarray(values, dtype=np.float64)
    require(scores.ndim == 1 and bool(np.isfinite(scores).all()), "scores must be finite and one-dimensional")
    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    boundaries = np.r_[0, np.flatnonzero(sorted_scores[1:] != sorted_scores[:-1]) + 1, len(scores)]
    result = np.empty(len(scores), dtype=np.float32)
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        result[order[start:stop]] = np.float32((start + stop) / (2.0 * len(scores)))
    return result


def fold_in_profile(
    item_biases: np.ndarray,
    item_factors: np.ndarray,
    positions: Sequence[int],
    labels: Sequence[int],
    *,
    regularization: float,
    learning_rate: float,
    epochs: int,
) -> tuple[float, np.ndarray, bool]:
    observed = np.asarray(positions, dtype=np.int32)
    signs = np.asarray(labels, dtype=np.int8)
    if len(observed) != len(signs) or not len(observed) or not ({-1, 1} <= set(signs.tolist())):
        return 0.0, np.zeros(item_factors.shape[1], dtype=np.float32), True
    user_bias = 0.0
    user_vector = np.zeros(item_factors.shape[1], dtype=np.float64)
    frozen_biases = np.asarray(item_biases, dtype=np.float64)
    frozen_factors = np.asarray(item_factors, dtype=np.float64)
    for _ in range(int(epochs)):
        for position, label in zip(observed, signs, strict=True):
            item_vector = frozen_factors[int(position)]
            score = user_bias + frozen_biases[int(position)] + float(user_vector @ item_vector)
            signed_margin = float(label) * score
            if signed_margin >= 0:
                factor = -float(label) * math.exp(-signed_margin) / (1.0 + math.exp(-signed_margin))
            else:
                factor = -float(label) / (1.0 + math.exp(signed_margin))
            user_vector -= learning_rate * (factor * item_vector + regularization * user_vector)
            user_bias -= learning_rate * factor
    return user_bias, user_vector.astype(np.float32), False


def deterministic_top_indices(candidate_ids: np.ndarray, scores: np.ndarray, *, top_n: int) -> np.ndarray:
    finite = np.flatnonzero(np.isfinite(scores))
    keep = min(int(top_n), len(finite))
    if keep < len(finite):
        finite = finite[np.argpartition(scores[finite], -keep)[-keep:]]
    order = np.lexsort((candidate_ids[finite], -scores[finite]))
    return finite[order[:keep]].astype(np.int64, copy=False)


def metrics_from_full_scores(
    candidate_ids: np.ndarray,
    scores: np.ndarray,
    future: Sequence[Mapping[str, Any]],
    *,
    top_candidates: int = 500,
    top_k: int = 10,
    fallback_user: bool,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    top501 = deterministic_top_indices(candidate_ids, scores, top_n=top_candidates + 1)
    top500 = top501[:top_candidates]
    ranked_ids = candidate_ids[top500].astype(np.int64)
    rank_by_movie = {int(movie_id): rank for rank, movie_id in enumerate(ranked_ids, start=1)}
    candidate_set = set(map(int, candidate_ids.tolist()))
    positives = [row for row in future if bool(row["is_positive"]) and int(row["movie_id"]) in candidate_set]
    negatives = {int(row["movie_id"]) for row in future if bool(row["is_negative"])}
    gains = {int(row["movie_id"]): float(row["midrank_utility"]) for row in positives}
    ideal = sorted(gains.values(), reverse=True)[:top_k]
    idcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    dcg = sum(
        gains[movie_id] / math.log2(rank + 1)
        for movie_id, rank in rank_by_movie.items()
        if movie_id in gains and rank <= top_k
    )
    first_positive = min((rank_by_movie[movie_id] for movie_id in gains if movie_id in rank_by_movie), default=None)
    movie_position = {int(movie_id): position for position, movie_id in enumerate(candidate_ids)}
    exact_ranks: list[int] = []
    for movie_id in gains:
        position = movie_position[movie_id]
        score = float(scores[position])
        if not math.isfinite(score):
            continue
        exact_ranks.append(
            1
            + int(np.count_nonzero(scores > score))
            + int(np.count_nonzero((scores == score) & (candidate_ids < movie_id)))
        )
    finite_count = max(1, int(np.count_nonzero(np.isfinite(scores))))
    return {
        "ndcg_at_10": float(dcg / idcg) if idcg else 0.0,
        "recall_at_10": float(sum(rank_by_movie.get(movie_id, top_k + 1) <= top_k for movie_id in gains) / len(gains)) if gains else 0.0,
        "mrr_at_10": float(1.0 / first_positive) if first_positive is not None and first_positive <= top_k else 0.0,
        "candidate_recall_at_500": float(any(movie_id in rank_by_movie for movie_id in gains)),
        "positive_mean_rank_percentile": float(np.mean([(rank - 1) / max(1, finite_count - 1) for rank in exact_ranks])) if exact_ranks else None,
        "harm_at_2": bool(set(map(int, ranked_ids[:2])).intersection(negatives)),
        "fallback_user": bool(fallback_user),
    }, top500, top501


def deterministic_full_rescore_users(users: Sequence[str], requested: int | str) -> list[str]:
    if requested == "all":
        return sorted(map(str, users))
    count = int(requested)
    if count < 1 or count > len(users):
        raise ValueError("full-rescore user count must be between 1 and the cohort size, or 'all'")
    ordered = sorted(map(str, users), key=lambda value: (hashlib.sha256(value.encode("utf-8")).hexdigest(), value))
    return sorted(ordered[:count])


def aggregate(frame: pd.DataFrame) -> dict[str, float]:
    return {
        "users": int(frame["user_key"].nunique()),
        "ndcg_at_10": float(frame["ndcg_at_10"].mean()),
        "recall_at_10": float(frame["recall_at_10"].mean()),
        "mrr_at_10": float(frame["mrr_at_10"].mean()),
        "candidate_recall_at_500": float(frame["candidate_recall_at_500"].mean()),
        "harm_at_2": float(frame["harm_at_2"].astype(float).mean()),
        "fallback_user_rate": float(frame["fallback_user"].astype(float).mean()),
    }


def verify(manifest_path: Path, *, root: Path = ROOT, full_rescore_users: int | str = 64) -> dict[str, Any]:
    contract_path = root / DEFAULT_CONTRACT.relative_to(ROOT)
    expected_manifest = root / DEFAULT_MANIFEST.relative_to(ROOT)
    require(manifest_path.resolve() == expected_manifest.resolve(), "unexpected manifest path")
    contract = read_json(contract_path)
    manifest = read_json(manifest_path)
    require(sha256_contract(contract_path) == manifest["contract_sha256"], "contract hash drift")
    require(manifest["evidence_id"] == "REC-EV-019D", "evidence identity drift")
    require(manifest["locked_test_used"] is False, "Locked Test invariant failed")
    require(manifest["champion"] is None, "champion invariant failed")
    require(manifest["product_policy_updated"] is False, "product policy invariant failed")

    allowed = contract["allowed_input_artifacts"]
    for name, expected in allowed.items():
        path = (root / expected["path"]).resolve()
        require(path.is_file(), f"missing allowlisted source: {name}")
        require(path.stat().st_size == int(expected["bytes"]), f"source size drift: {name}")
        require(sha256_file(path) == expected["sha256"], f"source hash drift: {name}")
        require(manifest["source_checksums"][name] == expected["sha256"], f"manifest source hash drift: {name}")
    for artifact in manifest["artifacts"]:
        path = root / artifact["path"]
        require(path.is_file(), f"missing output artifact: {artifact['path']}")
        require(path.stat().st_size == int(artifact["bytes"]), f"output size drift: {artifact['path']}")
        require(sha256_file(path) == artifact["sha256"], f"output hash drift: {artifact['path']}")

    output_root = root / contract["output_root"]
    lock = read_json(root / contract["leakage_lock"]["path"])
    progress = read_json(output_root / contract["outputs"]["progress"])
    result = read_json(output_root / contract["outputs"]["result"])
    source_manifest = read_json(output_root / contract["outputs"]["source_manifest"])
    require(lock["future_labels_joined_at_lock"] is False, "lock was not label-blind")
    require(int(lock["created_at_epoch_ns"]) < int(progress["run_started_epoch_ns"]), "lock timestamp did not precede run")
    require(lock["contract_sha256"] == manifest["contract_sha256"], "lock contract hash drift")
    require(source_manifest["created_before_future_label_join"] is True, "source manifest timing drift")
    require(source_manifest["base_representation"]["shared_by_both_arms"] is True, "base representation was not shared")
    require(source_manifest["base_representation"]["cache_reused"] is True, "expected cache reuse missing")

    amendment = read_json(root / AUDIT_AMENDMENT.relative_to(ROOT))
    require(amendment["historical_execution_contract"]["sha256_at_lock"] == manifest["contract_sha256"], "historical contract attestation drift")
    require(amendment["authoritative_cache_policy_for_any_reverification_or_follow_up"]["cache_absent_action"] == "FAIL_CLOSED_NO_REFIT", "cache policy amendment drift")
    candidate = pq.read_table(root / allowed["candidate_core"]["path"], columns=["movie_id", "b0_score"]).to_pandas()
    candidate_id_array = candidate["movie_id"].to_numpy(dtype=np.int64)
    candidate_ids = list(map(int, candidate_id_array))
    candidate_set = set(candidate_ids)
    candidate_position = {movie_id: position for position, movie_id in enumerate(candidate_ids)}
    require(len(candidate_set) == 41625, "candidate count drift")
    prefixes = pq.read_table(root / allowed["validation_prefixes"]["path"], columns=[
        "role", "user_key", "k", "input_rank", "movie_id", "binary_label", "source_position", "timestamp",
    ]).to_pandas()
    windows = pq.read_table(root / allowed["validation_windows"]["path"], columns=[
        "role", "user_key", "k", "window_rank", "movie_id", "midrank_utility", "is_positive", "is_negative",
    ]).to_pandas()
    require(set(prefixes["role"].astype(str)) == {"VALIDATION"}, "prefix role drift")
    require(set(windows["role"].astype(str)) == {"VALIDATION"}, "window role drift")
    prefixes = prefixes[prefixes["k"] == 10]
    windows = windows[windows["k"] == 10]
    users = sorted(set(prefixes["user_key"].astype(str)))
    require(len(users) == 1479 and set(users) == set(windows["user_key"].astype(str)), "K10 cohort drift")
    selected_full_rescore_users = deterministic_full_rescore_users(users, full_rescore_users)
    selected_full_rescore_set = set(selected_full_rescore_users)
    selection = read_json(root / allowed["validation_selection"]["path"])
    tuning_union = set(map(str, selection["tuning_panel"]["5"])) | set(map(str, selection["tuning_panel"]["10"]))
    excluded = set(users).intersection(tuning_union)
    require(len(excluded) == 426, "confirmatory exclusion drift")

    cohort = pq.read_table(output_root / contract["outputs"]["cohort"]).to_pandas()
    arms = pq.read_table(output_root / contract["outputs"]["arm_definitions"]).to_pandas()
    reported_metrics = pq.read_table(output_root / contract["outputs"]["user_arm_metrics"]).to_pandas()
    reported_paired = pq.read_table(output_root / contract["outputs"]["paired_deltas"]).to_pandas()
    require(len(cohort) == 1479 and int(cohort["confirmatory"].sum()) == 1053, "cohort artifact drift")
    require(set(cohort.loc[~cohort["confirmatory"], "user_key"].astype(str)) == excluded, "excluded-user artifact drift")
    require(len(arms) == 2958, "arm definition count drift")
    arm_lookup = {(str(row.user_key), str(row.arm)): row for row in arms.itertuples(index=False)}
    cohort_lookup = {str(row.user_key): row for row in cohort.itertuples(index=False)}
    prefix_groups = {str(key): group.sort_values("input_rank", kind="stable") for key, group in prefixes.groupby("user_key", sort=True)}
    future_lookup = {
        str(key): [
            {"movie_id": int(row.movie_id), "midrank_utility": float(row.midrank_utility), "is_positive": bool(row.is_positive), "is_negative": bool(row.is_negative)}
            for row in group.sort_values("window_rank", kind="stable").itertuples(index=False)
        ]
        for key, group in windows.groupby("user_key", sort=True)
    }
    recomputed_strata: dict[str, str] = {}
    full_rescore_profiles: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
    anchor_loss_k5 = 0
    anchor_loss_k10 = 0
    for user_key in users:
        prefix = prefix_groups[user_key]
        require(prefix["input_rank"].astype(int).tolist() == list(range(1, 11)), "input order drift")
        source = prefix["source_position"].to_numpy(dtype=np.int64)
        timestamps = prefix["timestamp"].to_numpy(dtype=np.int64)
        require(bool(np.all(source[1:] > source[:-1])) and bool(np.all(timestamps[1:] >= timestamps[:-1])), "source order drift")
        movies = list(map(int, prefix["movie_id"]))
        labels = list(map(int, prefix["binary_label"]))
        applicability: dict[str, bool] = {}
        for arm, count in (("K5", 5), ("K10", 10)):
            row = arm_lookup[(user_key, arm)]
            expected_movies = movies[:count]
            expected_labels = labels[:count]
            valid = [(movie, label) for movie, label in zip(expected_movies, expected_labels, strict=True) if movie in candidate_set]
            require(list(map(int, row.profile_movie_ids)) == expected_movies, "profile movie prefix drift")
            require(list(map(int, row.profile_labels)) == expected_labels, "profile label prefix drift")
            require(list(map(int, row.candidate_valid_movie_ids)) == [movie for movie, _ in valid], "candidate-valid profile drift")
            applicable = {-1, 1} <= {label for _, label in valid}
            raw_both = {-1, 1} <= set(expected_labels)
            require(bool(row.fallback_user) == (not applicable), "fallback definition drift")
            applicability[arm] = applicable
            full_rescore_profiles[(user_key, arm)] = (
                np.asarray([candidate_position[movie] for movie, _ in valid], dtype=np.int32),
                np.asarray([label for _, label in valid], dtype=np.int8),
            )
            if user_key not in excluded and raw_both and not applicable:
                if arm == "K5":
                    anchor_loss_k5 += 1
                else:
                    anchor_loss_k10 += 1
        stratum = "BOTH_LIGHTFM" if applicability["K5"] else ("K10_NEWLY_APPLICABLE" if applicability["K10"] else "BOTH_FALLBACK")
        require(str(cohort_lookup[user_key].applicability_stratum) == stratum, "cohort stratum drift")
        recomputed_strata[user_key] = stratum
    confirmatory_strata = pd.Series([recomputed_strata[user] for user in users if user not in excluded]).value_counts().to_dict()
    require(confirmatory_strata == {"BOTH_LIGHTFM": 661, "K10_NEWLY_APPLICABLE": 277, "BOTH_FALLBACK": 115}, "confirmatory strata drift")
    require((anchor_loss_k5, anchor_loss_k10) == (61, 34), "candidate-anchor loss drift")

    reported_lookup = {
        (str(row.user_key), str(row.estimand), str(row.arm)): row
        for row in reported_metrics.itertuples(index=False)
    }
    recomputed_rows: list[dict[str, Any]] = []
    selected_prediction_lookup: dict[tuple[str, str, str], tuple[np.ndarray, np.ndarray]] = {}
    prediction_path = output_root / contract["outputs"]["predictions"]
    parquet = pq.ParquetFile(prediction_path)
    seen_groups: set[tuple[str, str, str]] = set()
    for row_group_index in range(parquet.num_row_groups):
        frame = parquet.read_row_group(row_group_index).to_pandas()
        for (user_key, estimand, arm), group in frame.groupby(["user_key", "estimand", "arm"], sort=False):
            key = (str(user_key), str(estimand), str(arm))
            require(key not in seen_groups, "prediction group split or duplicated across row groups")
            seen_groups.add(key)
            ranked = group.sort_values("rank", kind="stable")
            require(len(ranked) == 500 and ranked["rank"].astype(int).tolist() == list(range(1, 501)), "Top-500 rank drift")
            scores = ranked["effective_score"].to_numpy(dtype=np.float64)
            movies = ranked["movie_id"].to_numpy(dtype=np.int64)
            require(bool(np.all(scores[1:] <= scores[:-1])), "score order drift")
            tie = scores[1:] == scores[:-1]
            require(bool(np.all(movies[1:][tie] > movies[:-1][tie])), "tie-break drift")
            seen = set(map(int, arm_lookup[(str(user_key), "K10" if estimand == "COMMON_K10_SEEN_MASK" else str(arm))].candidate_valid_movie_ids))
            require(not seen.intersection(map(int, movies)), "seen item appeared in predictions")
            fallback = bool(arm_lookup[(str(user_key), str(arm))].fallback_user)
            require(set(map(bool, ranked["fallback_used"])) == {fallback}, "prediction fallback flag drift")
            values = metrics_from_top500(ranked, future_lookup[str(user_key)], candidate_set)
            reported = reported_lookup[key]
            for metric in ("ndcg_at_10", "recall_at_10", "mrr_at_10", "candidate_recall_at_500"):
                require(close(values[metric], getattr(reported, metric)), f"metric drift: {key} {metric}")
            require(bool(values["harm_at_2"]) == bool(reported.harm_at_2), f"metric drift: {key} harm_at_2")
            require(bool(reported.fallback_user) == fallback, f"metric drift: {key} fallback")
            if str(user_key) in selected_full_rescore_set:
                selected_prediction_lookup[key] = (
                    movies.astype(np.int64, copy=True),
                    ranked["effective_score"].to_numpy(dtype=np.float32, copy=True),
                )
            recomputed_rows.append({
                "user_key": str(user_key),
                "confirmatory": str(user_key) not in excluded,
                "applicability_stratum": recomputed_strata[str(user_key)],
                "estimand": str(estimand),
                "arm": str(arm),
                "fallback_user": fallback,
                **values,
            })
    require(len(seen_groups) == 1479 * 4, "prediction group count drift")
    recomputed = pd.DataFrame(recomputed_rows)

    require(len(selected_prediction_lookup) == len(selected_full_rescore_users) * 4, "selected prediction lookup drift")
    config = contract["model"]["lightfm_config"]
    result_path = root / allowed["lightfm_result"]["path"]
    with np.load(result_path, allow_pickle=False) as fitted:
        item_biases = fitted["item_biases"].astype(np.float32)
        item_factors = fitted["item_factors"].astype(np.float32)
    require(item_biases.shape == (41625,), "item bias shape drift")
    require(item_factors.shape == (41625, int(config["dimension"])), "item factor shape drift")
    require(bool(np.isfinite(item_biases).all()) and bool(np.isfinite(item_factors).all()), "non-finite item representation")
    b0_percentiles = midrank_percentiles(candidate["b0_score"].to_numpy(dtype=np.float64))
    full_rescore_rows: list[dict[str, Any]] = []
    boundary_tie_rankings = 0
    boundary_tie_examples: list[dict[str, Any]] = []
    original_batch_size = int(contract["resource_bounds"]["user_batch_size_max"])
    for start in range(0, len(users), original_batch_size):
        batch_users = users[start : start + original_batch_size]
        if not selected_full_rescore_set.intersection(batch_users):
            continue
        for arm in ("K5", "K10"):
            biases: list[float] = []
            vectors: list[np.ndarray] = []
            fallback_flags: list[bool] = []
            for user_key in batch_users:
                positions, labels = full_rescore_profiles[(user_key, arm)]
                bias, vector, fallback = fold_in_profile(
                    item_biases,
                    item_factors,
                    positions,
                    labels,
                    regularization=float(config["user_alpha"]),
                    learning_rate=float(config["learning_rate"]),
                    epochs=int(contract["model"]["target_fold_in"]["epochs"]),
                )
                biases.append(float(bias))
                vectors.append(vector)
                fallback_flags.append(bool(fallback))
            score_matrix = np.vstack(vectors).astype(np.float32) @ item_factors.T
            score_matrix += item_biases[None, :]
            score_matrix += np.asarray(biases, dtype=np.float32)[:, None]
            for row_index, user_key in enumerate(batch_users):
                if user_key not in selected_full_rescore_set:
                    continue
                fallback = fallback_flags[row_index]
                effective = b0_percentiles.copy() if fallback else midrank_percentiles(score_matrix[row_index])
                for estimand in ("COMMON_K10_SEEN_MASK", "ARM_SPECIFIC_SEEN_MASK"):
                    seen_arm = "K10" if estimand == "COMMON_K10_SEEN_MASK" else arm
                    seen_positions = full_rescore_profiles[(user_key, seen_arm)][0]
                    masked = effective.copy()
                    masked[seen_positions] = -np.inf
                    values, top500, top501 = metrics_from_full_scores(
                        candidate_id_array,
                        masked,
                        future_lookup[user_key],
                        fallback_user=fallback,
                    )
                    key = (user_key, estimand, arm)
                    persisted_movies, persisted_scores = selected_prediction_lookup[key]
                    exact_movies = candidate_id_array[top500]
                    exact_scores = masked[top500].astype(np.float32, copy=False)
                    require(np.array_equal(exact_movies, persisted_movies), f"full-rescore Top-500 movie drift: {key}")
                    require(np.array_equal(exact_scores, persisted_scores), f"full-rescore Top-500 score drift: {key}")
                    require(np.array_equal(exact_movies[:10], persisted_movies[:10]), f"full-rescore Top-10 drift: {key}")
                    if len(top501) > 500 and masked[top501[499]] == masked[top501[500]]:
                        boundary_tie_rankings += 1
                        require(candidate_id_array[top501[499]] < candidate_id_array[top501[500]], f"Top-500 boundary tie-break drift: {key}")
                        if len(boundary_tie_examples) < 10:
                            boundary_tie_examples.append({
                                "user_key": user_key,
                                "estimand": estimand,
                                "arm": arm,
                                "rank_500_movie_id": int(candidate_id_array[top501[499]]),
                                "rank_501_movie_id": int(candidate_id_array[top501[500]]),
                                "effective_score": float(masked[top501[499]]),
                            })
                    reported = reported_lookup[key]
                    for metric in ("ndcg_at_10", "recall_at_10", "mrr_at_10", "candidate_recall_at_500"):
                        require(close(values[metric], getattr(reported, metric)), f"full-rescore metric drift: {key} {metric}")
                    if values["positive_mean_rank_percentile"] is None:
                        require(pd.isna(reported.positive_mean_rank_percentile), f"full-rescore positive rank null drift: {key}")
                    else:
                        require(close(values["positive_mean_rank_percentile"], reported.positive_mean_rank_percentile), f"full-rescore positive rank drift: {key}")
                    require(bool(values["harm_at_2"]) == bool(reported.harm_at_2), f"full-rescore Harm@2 drift: {key}")
                    full_rescore_rows.append({
                        "user_key": user_key,
                        "confirmatory": user_key not in excluded,
                        "applicability_stratum": recomputed_strata[user_key],
                        "estimand": estimand,
                        "arm": arm,
                        **values,
                    })
    require(len(full_rescore_rows) == len(selected_full_rescore_users) * 4, "full-rescore ranking count drift")
    full_rescore_frame = pd.DataFrame(full_rescore_rows)

    paired_rows: list[dict[str, Any]] = []
    for (user_key, estimand), group in recomputed.groupby(["user_key", "estimand"], sort=True):
        by_arm = {str(row.arm): row for row in group.itertuples(index=False)}
        k5, k10 = by_arm["K5"], by_arm["K10"]
        transition = "BOTH_LIGHTFM" if not k5.fallback_user else ("K10_NEWLY_APPLICABLE" if not k10.fallback_user else "BOTH_FALLBACK")
        paired_rows.append({
            "user_key": str(user_key),
            "confirmatory": bool(k5.confirmatory),
            "applicability_stratum": str(k5.applicability_stratum),
            "estimand": str(estimand),
            "delta_ndcg_at_10": float(k10.ndcg_at_10 - k5.ndcg_at_10),
            "delta_recall_at_10": float(k10.recall_at_10 - k5.recall_at_10),
            "delta_mrr_at_10": float(k10.mrr_at_10 - k5.mrr_at_10),
            "delta_candidate_recall_at_500": float(k10.candidate_recall_at_500 - k5.candidate_recall_at_500),
            "delta_harm_at_2": float(k10.harm_at_2) - float(k5.harm_at_2),
            "fallback_transition": transition,
        })
    recomputed_paired = pd.DataFrame(paired_rows).sort_values(["estimand", "user_key"]).reset_index(drop=True)
    reported_paired = reported_paired.sort_values(["estimand", "user_key"]).reset_index(drop=True)
    require(len(recomputed_paired) == len(reported_paired), "paired row count drift")
    for column in ("delta_ndcg_at_10", "delta_recall_at_10", "delta_mrr_at_10", "delta_candidate_recall_at_500", "delta_harm_at_2"):
        require(bool(np.allclose(recomputed_paired[column], reported_paired[column], rtol=1e-10, atol=1e-10)), f"paired delta drift: {column}")
    require(recomputed_paired["fallback_transition"].tolist() == reported_paired["fallback_transition"].tolist(), "fallback transition drift")

    primary = recomputed_paired[(recomputed_paired["confirmatory"]) & (recomputed_paired["estimand"] == "COMMON_K10_SEEN_MASK")]
    bootstrap = bootstrap_paired(
        primary["delta_ndcg_at_10"].to_numpy(dtype=np.float64),
        primary["delta_harm_at_2"].to_numpy(dtype=np.float64),
        iterations=int(contract["bootstrap"]["iterations"]),
        seed=int(contract["bootstrap"]["seed"]),
    )
    reported_bootstrap = result["primary_estimand"]["bootstrap"]
    for key in ("ndcg_mean", "harm_mean", "harm_one_sided_95_upper"):
        require(close(bootstrap[key], reported_bootstrap[key]), f"bootstrap drift: {key}")
    require(bool(np.allclose(bootstrap["ndcg_two_sided_95"], reported_bootstrap["ndcg_two_sided_95"], rtol=1e-12, atol=1e-12)), "bootstrap NDCG interval drift")
    recomputed_decision = decision(bootstrap)
    require(recomputed_decision == result["primary_estimand"]["decision"], "decision drift")
    require(result["status"] == recomputed_decision["status"] and result["reason"] == recomputed_decision["reason"], "result status drift")

    for estimand in ("COMMON_K10_SEEN_MASK", "ARM_SPECIFIC_SEEN_MASK"):
        for population, selected in (
            ("CONFIRMATORY", recomputed[(recomputed["confirmatory"]) & (recomputed["estimand"] == estimand)]),
            ("ALL_K10_COHORT", recomputed[recomputed["estimand"] == estimand]),
        ):
            for arm in ("K5", "K10"):
                actual = aggregate(selected[selected["arm"] == arm])
                expected = result["aggregate_metrics"][estimand][population][arm]
                for key, value in actual.items():
                    require(close(value, expected[key]) if key != "users" else value == expected[key], f"aggregate drift: {estimand} {population} {arm} {key}")

    full_rescore_aggregate_sha256 = None
    if len(selected_full_rescore_users) == len(users):
        full_aggregate: dict[str, Any] = {}
        for estimand in ("COMMON_K10_SEEN_MASK", "ARM_SPECIFIC_SEEN_MASK"):
            full_aggregate[estimand] = {}
            for population, selected in (
                ("CONFIRMATORY", full_rescore_frame[(full_rescore_frame["confirmatory"]) & (full_rescore_frame["estimand"] == estimand)]),
                ("ALL_K10_COHORT", full_rescore_frame[full_rescore_frame["estimand"] == estimand]),
            ):
                full_aggregate[estimand][population] = {}
                for arm in ("K5", "K10"):
                    arm_frame = selected[selected["arm"] == arm]
                    actual = aggregate(arm_frame)
                    expected = result["aggregate_metrics"][estimand][population][arm]
                    for key, value in actual.items():
                        require(close(value, expected[key]) if key != "users" else value == expected[key], f"full-rescore aggregate drift: {estimand} {population} {arm} {key}")
                    full_aggregate[estimand][population][arm] = {
                        **actual,
                        "positive_mean_rank_percentile": float(arm_frame["positive_mean_rank_percentile"].mean()),
                    }
        full_rescore_aggregate_sha256 = hashlib.sha256(
            json.dumps(full_aggregate, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    for payload in (result, manifest, progress, source_manifest, lock):
        require(payload["locked_test_used"] is False, "final Locked Test invariant failed")
        require(payload["champion"] is None, "final champion invariant failed")
        require(payload["product_policy_updated"] is False, "final policy invariant failed")
    return {
        "status": "PASS_INDEPENDENT_RECOMPUTATION",
        "decision": recomputed_decision,
        "cohort_users": 1479,
        "confirmatory_users": 1053,
        "strata": confirmatory_strata,
        "primary_bootstrap": bootstrap,
        "full_rescore": {
            "mode": "ALL_USERS_FULL_RESCORE" if len(selected_full_rescore_users) == len(users) else "BOUNDED_DETERMINISTIC_FULL_RESCORE",
            "users": len(selected_full_rescore_users),
            "rankings": len(full_rescore_rows),
            "candidate_count": len(candidate_ids),
            "exact_top10_verified": True,
            "exact_top500_verified": True,
            "top500_boundary_tie_rankings": boundary_tie_rankings,
            "top500_boundary_tie_examples": boundary_tie_examples,
            "positive_mean_rank_percentile_verified": True,
            "all_user_aggregate_sha256": full_rescore_aggregate_sha256,
        },
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--full-rescore-users",
        default="64",
        help="Deterministic full-catalog rescore user count, or 'all' for all 1,479 users.",
    )
    args = parser.parse_args()
    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    requested: int | str = "all" if str(args.full_rescore_users).lower() == "all" else int(args.full_rescore_users)
    summary = verify(manifest, full_rescore_users=requested)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
