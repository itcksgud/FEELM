#!/usr/bin/env python3
"""Independently reconstruct and full-rescore REC-EV-019F Validation evidence."""

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

try:
    from recommendation_binary_onboarding_preflight import (
        future_midrank_utilities,
        sequential_binary_labels,
        split_prefix,
        stable_user_bucket,
    )
except ModuleNotFoundError:  # package import used by unittest
    from scripts.recommendation_binary_onboarding_preflight import (
        future_midrank_utilities,
        sequential_binary_labels,
        split_prefix,
        stable_user_bucket,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019f-independent-temporal-routing.json"
DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019f-validation.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def close(actual: float, expected: float, *, tolerance: float = 1e-10) -> bool:
    return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)


def user_key(user_id: int) -> str:
    return hashlib.sha256(f"feelm-ml32m-user-v1|{int(user_id)}".encode("utf-8")).hexdigest()


def base_train_midrank(manifest: Mapping[str, Any]) -> np.ndarray:
    counts = manifest["splits"]["train"]["rating_value_counts"]
    ordered = np.asarray(
        [int(counts[str(float(value))]) for value in np.arange(0.5, 5.01, 0.5)], dtype=np.float64,
    )
    before = np.cumsum(ordered) - ordered
    return (before + 0.5 * ordered) / ordered.sum()


def read_validation_bucket(path: Path, *, protocol: Mapping[str, Any]) -> pd.DataFrame:
    prefix = split_prefix(dict(protocol))
    frames: list[pd.DataFrame] = []
    for batch in pq.ParquetFile(path).iter_batches(columns=["user_id", "movie_id", "rating", "timestamp"], batch_size=262_144):
        frame = batch.to_pandas()
        buckets = np.fromiter(
            (stable_user_bucket(int(value), split_prefix=prefix) for value in frame["user_id"]),
            dtype=np.int16,
            count=len(frame),
        )
        mask = (buckets >= 50) & (buckets <= 59)
        if bool(mask.any()):
            frames.append(frame.loc[mask].copy())
    require(bool(frames), "Validation bucket 50..59 is empty")
    return pd.concat(frames, ignore_index=True).sort_values(
        ["user_id", "timestamp", "movie_id"], kind="stable", ignore_index=True,
    )


def reconstruct_episode(
    ratings: pd.DataFrame,
    *,
    global_midrank: np.ndarray,
    candidate_ids: set[int],
    tuning_union: set[str],
    historical_k10: set[str],
    historical_any: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    structural: list[dict[str, Any]] = []
    strict: list[dict[str, Any]] = []
    prefixes: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    for raw_user_id, raw_group in ratings.groupby("user_id", sort=True, observed=True):
        group = raw_group.sort_values(["timestamp", "movie_id"], kind="stable", ignore_index=True)
        key = user_key(int(raw_user_id))
        values = group["rating"].to_numpy(dtype=np.float64, copy=False)
        movies = group["movie_id"].to_numpy(dtype=np.int64, copy=False)
        timestamps = group["timestamp"].to_numpy(dtype=np.int64, copy=False)
        original = sequential_binary_labels(values, global_midrank, shrinkage=10.0, like_min=0.15, dislike_max=-0.15)
        if len(original) < 10:
            continue
        old_tenth = int(original[9][0])
        tail_start = old_tenth + 11
        if key in tuning_union or tail_start >= len(values):
            continue
        reset = sequential_binary_labels(values[tail_start:], global_midrank, shrinkage=10.0, like_min=0.15, dislike_max=-0.15)
        if len(reset) < 10:
            continue
        selected = reset[:10]
        fresh_tenth = tail_start + int(selected[-1][0])
        future_start = fresh_tenth + 1
        future_end = future_start + 9
        if future_end >= len(values):
            continue
        old_rows = {int(position) for position, _, _ in original[:10]} | set(range(old_tenth + 1, old_tenth + 11))
        fresh_positions = [tail_start + int(position) for position, _, _ in selected] + list(range(future_start, future_start + 10))
        overlap = len(old_rows.intersection(fresh_positions))
        require(overlap == 0, "reconstructed fresh episode overlaps historical source rows")
        structural.append({
            "user_key": key,
            "old_k10_tenth_source_position": old_tenth,
            "tail_start_source_position": tail_start,
            "fresh_k10_tenth_source_position": fresh_tenth,
            "future_start_source_position": future_start,
            "future_end_source_position": future_end,
            "historical_019a_k10_user": key in historical_k10,
            "historical_019a_any_user": key in historical_any,
            "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
            "user_independent": False,
        })
        for rank, (relative, label, utility) in enumerate(selected, start=1):
            position = tail_start + int(relative)
            prefixes.append({
                "role": "VALIDATION_019F_TEMPORAL", "user_key": key, "input_rank": rank,
                "movie_id": int(movies[position]), "binary_label": int(label), "relative_utility": float(utility),
                "source_position": position, "source_row_id": f"{key}:{position}", "timestamp": int(timestamps[position]),
            })
        future_values = values[future_start : future_start + 10]
        utilities = future_midrank_utilities(future_values)
        positive = utilities >= 0.65
        negative = utilities <= 0.35
        candidate_positive = int(sum(
            bool(flag) and int(movie) in candidate_ids
            for movie, flag in zip(movies[future_start : future_start + 10], positive, strict=True)
        ))
        for rank, offset in enumerate(range(10), start=1):
            position = future_start + offset
            windows.append({
                "role": "VALIDATION_019F_TEMPORAL", "user_key": key, "window_rank": rank,
                "movie_id": int(movies[position]), "rating": float(values[position]),
                "midrank_utility": float(utilities[offset]), "is_positive": bool(positive[offset]),
                "is_negative": bool(negative[offset]), "candidate_present": int(movies[position]) in candidate_ids,
                "source_position": position, "source_row_id": f"{key}:{position}", "timestamp": int(timestamps[position]),
            })
        if int(positive.sum()) >= 3 and candidate_positive >= 1:
            strict.append({
                "user_key": key, "future_positive_count": int(positive.sum()),
                "candidate_positive_count": candidate_positive,
                "historical_019a_k10_user": key in historical_k10,
                "historical_019a_any_user": key in historical_any,
                "completely_new_to_019a_validation": key not in historical_any,
                "historical_source_row_overlap": overlap,
                "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW", "user_independent": False,
            })
    for rows, order in (
        (structural, ("user_key",)), (strict, ("user_key",)),
        (prefixes, ("user_key", "input_rank")), (windows, ("user_key", "window_rank")),
    ):
        rows.sort(key=lambda row: tuple(row[field] for field in order))
    return structural, strict, prefixes, windows


def compare_records(expected: Sequence[Mapping[str, Any]], actual: pd.DataFrame, *, name: str) -> None:
    require(len(expected) == len(actual), f"{name} row count drift")
    expected_frame = pd.DataFrame(expected)
    require(list(expected_frame.columns) == list(actual.columns), f"{name} column drift")
    for column in expected_frame.columns:
        left = expected_frame[column]
        right = actual[column]
        if pd.api.types.is_float_dtype(left) or pd.api.types.is_float_dtype(right):
            require(bool(np.allclose(left.to_numpy(dtype=np.float64), right.to_numpy(dtype=np.float64), rtol=1e-6, atol=1e-7, equal_nan=True)), f"{name} float drift: {column}")
        else:
            require(left.tolist() == right.tolist(), f"{name} value drift: {column}")


def midrank_percentiles(values: np.ndarray) -> np.ndarray:
    scores = np.asarray(values, dtype=np.float64)
    require(scores.ndim == 1 and bool(np.isfinite(scores).all()), "score domain drift")
    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    boundaries = np.r_[0, np.flatnonzero(sorted_scores[1:] != sorted_scores[:-1]) + 1, len(scores)]
    result = np.empty(len(scores), dtype=np.float32)
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        result[order[start:stop]] = np.float32((start + stop) / (2.0 * len(scores)))
    return result


def fold_in(
    item_biases: np.ndarray,
    item_factors: np.ndarray,
    positions: Sequence[int],
    labels: Sequence[int],
    *,
    alpha: float,
    rate: float,
    epochs: int,
) -> tuple[float, np.ndarray, bool]:
    observed = np.asarray(positions, dtype=np.int32)
    signs = np.asarray(labels, dtype=np.int8)
    if len(observed) != len(signs) or not ({-1, 1} <= set(signs.tolist())):
        return 0.0, np.zeros(item_factors.shape[1], dtype=np.float32), True
    bias = 0.0
    vector = np.zeros(item_factors.shape[1], dtype=np.float64)
    fixed_bias = item_biases.astype(np.float64)
    fixed_factor = item_factors.astype(np.float64)
    for _ in range(int(epochs)):
        for position, sign in zip(observed, signs, strict=True):
            item = fixed_factor[int(position)]
            margin = float(sign) * (bias + fixed_bias[int(position)] + float(vector @ item))
            gradient = (
                -float(sign) * math.exp(-margin) / (1.0 + math.exp(-margin))
                if margin >= 0
                else -float(sign) / (1.0 + math.exp(margin))
            )
            vector -= rate * (gradient * item + alpha * vector)
            bias -= rate * gradient
    return bias, vector.astype(np.float32), False


def top_indices(candidate_ids: np.ndarray, scores: np.ndarray, count: int) -> np.ndarray:
    finite = np.flatnonzero(np.isfinite(scores))
    keep = min(count, len(finite))
    if keep < len(finite):
        finite = finite[np.argpartition(scores[finite], -keep)[-keep:]]
    return finite[np.lexsort((candidate_ids[finite], -scores[finite]))[:keep]].astype(np.int64, copy=False)


def full_metrics(
    candidate_ids: np.ndarray,
    scores: np.ndarray,
    future: Sequence[Mapping[str, Any]],
    *,
    fallback: bool,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    top501 = top_indices(candidate_ids, scores, 501)
    top500 = top501[:500]
    ranked = candidate_ids[top500]
    ranks = {int(movie): rank for rank, movie in enumerate(ranked, start=1)}
    candidate_set = set(map(int, candidate_ids))
    positive = [row for row in future if bool(row["is_positive"]) and int(row["movie_id"]) in candidate_set]
    negative = {int(row["movie_id"]) for row in future if bool(row["is_negative"])}
    gains = {int(row["movie_id"]): float(row["midrank_utility"]) for row in positive}
    ideal = sorted(gains.values(), reverse=True)[:10]
    idcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    dcg = sum(gains[movie] / math.log2(rank + 1) for movie, rank in ranks.items() if movie in gains and rank <= 10)
    first = min((ranks[movie] for movie in gains if movie in ranks), default=None)
    positions = {int(movie): index for index, movie in enumerate(candidate_ids)}
    exact: list[int] = []
    for movie in gains:
        position = positions[movie]
        score = float(scores[position])
        if math.isfinite(score):
            exact.append(1 + int(np.count_nonzero(scores > score)) + int(np.count_nonzero((scores == score) & (candidate_ids < movie))))
    finite_count = max(1, int(np.count_nonzero(np.isfinite(scores))))
    return {
        "ndcg_at_10": float(dcg / idcg) if idcg else 0.0,
        "recall_at_10": float(sum(ranks.get(movie, 11) <= 10 for movie in gains) / len(gains)) if gains else 0.0,
        "mrr_at_10": float(1.0 / first) if first is not None and first <= 10 else 0.0,
        "candidate_recall_at_500": float(any(movie in ranks for movie in gains)),
        "positive_mean_rank_percentile": float(np.mean([(rank - 1) / max(1, finite_count - 1) for rank in exact])) if exact else None,
        "harm_at_2": bool(set(map(int, ranked[:2])).intersection(negative)),
        "fallback_user": bool(fallback),
        "applicable_user": not bool(fallback),
    }, top500, top501


def top500_metrics(prediction: pd.DataFrame, future: Sequence[Mapping[str, Any]], candidate_set: set[int]) -> dict[str, Any]:
    ranked = prediction.sort_values("rank", kind="stable")
    movies = list(map(int, ranked["movie_id"]))
    ranks = {movie: rank for rank, movie in enumerate(movies, start=1)}
    positive = [row for row in future if bool(row["is_positive"]) and int(row["movie_id"]) in candidate_set]
    negative = {int(row["movie_id"]) for row in future if bool(row["is_negative"])}
    gains = {int(row["movie_id"]): float(row["midrank_utility"]) for row in positive}
    ideal = sorted(gains.values(), reverse=True)[:10]
    idcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    dcg = sum(gains[movie] / math.log2(rank + 1) for movie, rank in ranks.items() if movie in gains and rank <= 10)
    first = min((ranks[movie] for movie in gains if movie in ranks), default=None)
    return {
        "ndcg_at_10": float(dcg / idcg) if idcg else 0.0,
        "recall_at_10": float(sum(ranks.get(movie, 11) <= 10 for movie in gains) / len(gains)) if gains else 0.0,
        "mrr_at_10": float(1.0 / first) if first is not None and first <= 10 else 0.0,
        "candidate_recall_at_500": float(any(movie in ranks for movie in gains)),
        "harm_at_2": bool(set(movies[:2]).intersection(negative)),
    }


def bootstrap(ndcg: np.ndarray, harm: np.ndarray, *, iterations: int, seed: int) -> dict[str, Any]:
    ndcg = np.asarray(ndcg, dtype=np.float64)
    harm = np.asarray(harm, dtype=np.float64)
    rng = np.random.default_rng(seed)
    ndcg_means = np.empty(iterations)
    harm_means = np.empty(iterations)
    for start in range(0, iterations, 250):
        stop = min(start + 250, iterations)
        sample = rng.integers(0, len(ndcg), size=(stop - start, len(ndcg)))
        ndcg_means[start:stop] = ndcg[sample].mean(axis=1)
        harm_means[start:stop] = harm[sample].mean(axis=1)
    return {
        "iterations": iterations, "seed": seed, "ndcg_mean": float(ndcg.mean()),
        "ndcg_two_sided_95": [float(np.percentile(ndcg_means, 2.5)), float(np.percentile(ndcg_means, 97.5))],
        "harm_mean": float(harm.mean()), "harm_one_sided_95_upper": float(np.percentile(harm_means, 95.0)),
    }


def decision(values: Mapping[str, Any]) -> dict[str, str]:
    if float(values["harm_one_sided_95_upper"]) > 0.005:
        return {"status": "FAIL", "reason": "HARM_UPPER_EXCEEDS_0_005"}
    if float(values["ndcg_mean"]) >= 0.005 and float(values["ndcg_two_sided_95"][0]) > 0.0:
        return {"status": "PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION", "reason": "TEMPORAL_WINDOW_EFFICACY_AND_SAFETY_THRESHOLDS_MET"}
    return {"status": "INCONCLUSIVE", "reason": "TEMPORAL_WINDOW_SUCCESS_NOT_ESTABLISHED"}


def selected_users(users: Sequence[str], requested: int | str) -> list[str]:
    if requested == "all":
        return sorted(map(str, users))
    count = int(requested)
    require(1 <= count <= len(users), "full-rescore user count is outside the cohort")
    order = sorted(map(str, users), key=lambda value: (hashlib.sha256(value.encode()).hexdigest(), value))
    return sorted(order[:count])


def aggregate(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "users": int(frame["user_key"].nunique()), "ndcg_at_10": float(frame["ndcg_at_10"].mean()),
        "recall_at_10": float(frame["recall_at_10"].mean()), "mrr_at_10": float(frame["mrr_at_10"].mean()),
        "candidate_recall_at_500": float(frame["candidate_recall_at_500"].mean()),
        "positive_mean_rank_percentile": float(frame["positive_mean_rank_percentile"].mean()),
        "harm_at_2": float(frame["harm_at_2"].astype(float).mean()),
        "fallback_user_rate": float(frame["fallback_user"].astype(float).mean()),
        "applicability_rate": float(frame["applicable_user"].astype(float).mean()),
    }


def benefit_counts(frame: pd.DataFrame) -> dict[str, int]:
    values = frame["delta_ndcg_at_10"].to_numpy(dtype=np.float64)
    return {"benefit": int(np.count_nonzero(values > 0)), "neutral": int(np.count_nonzero(values == 0)), "harm": int(np.count_nonzero(values < 0))}


def verify(manifest_path: Path, *, root: Path = ROOT, full_rescore_users: int | str = 64) -> dict[str, Any]:
    contract_path = root / DEFAULT_CONTRACT.relative_to(ROOT)
    expected_manifest = root / DEFAULT_MANIFEST.relative_to(ROOT)
    require(manifest_path.resolve() == expected_manifest.resolve(), "unexpected manifest path")
    contract = read_json(contract_path)
    manifest = read_json(manifest_path)
    require(sha256_text(contract_path) == manifest["contract_sha256"], "contract hash drift")
    require(manifest["evidence_id"] == "REC-EV-019F", "evidence identity drift")
    for payload in (manifest, manifest["result"]):
        require(payload["locked_test_used"] is False, "Locked Test invariant failed")
        require(payload["champion"] is None, "champion invariant failed")
        require(payload["product_policy_updated"] is False, "product policy invariant failed")
        require(payload["user_independent"] is False, "user independence boundary drift")
        require(payload["independence_unit"] == "SOURCE_ROW_AND_TEMPORAL_WINDOW", "independence unit drift")
    allowed = contract["allowed_input_artifacts"]
    for name, expected in allowed.items():
        path = root / expected["path"]
        require(path.is_file(), f"missing source: {name}")
        require(path.stat().st_size == int(expected["bytes"]), f"source size drift: {name}")
        require(sha256_file(path) == expected["sha256"], f"source hash drift: {name}")
        require(manifest["source_checksums"][name] == expected["sha256"], f"manifest source drift: {name}")
    for artifact in manifest["artifacts"]:
        path = root / artifact["path"]
        require(path.is_file(), f"missing output: {artifact['path']}")
        require(path.stat().st_size == int(artifact["bytes"]), f"output size drift: {artifact['path']}")
        require(sha256_file(path) == artifact["sha256"], f"output hash drift: {artifact['path']}")

    output_root = root / contract["output_root"]
    paths = {name: output_root / relative for name, relative in contract["outputs"].items() if name != "checkpoints"}
    lock = read_json(paths["protocol_lock"])
    source_manifest = read_json(paths["source_manifest"])
    progress = read_json(paths["progress"])
    result = read_json(paths["result"])
    require(lock["ranking_metrics_read"] is False and lock["eligibility_counts_observed"] is True, "lock disclosure drift")
    require(lock["git"]["dirty"] is False, "lock git state was dirty")
    require(int(lock["created_at_epoch_ns"]) < int(progress["run_started_epoch_ns"]), "lock did not precede run")
    require(lock["contract_sha256"] == manifest["contract_sha256"], "lock contract drift")
    require(source_manifest["rec_ev_019d_predictions_reused"] is False, "019D prediction reuse drift")

    candidate = pq.read_table(root / allowed["candidate_core"]["path"], columns=["movie_id", "b0_score"]).to_pandas()
    candidate_array = candidate["movie_id"].to_numpy(dtype=np.int64)
    candidate_set = set(map(int, candidate_array))
    candidate_position = {movie: position for position, movie in enumerate(candidate_array)}
    require(len(candidate_set) == 41625 and bool(np.all(candidate_array[1:] > candidate_array[:-1])), "candidate boundary drift")
    historical_prefixes = pq.read_table(root / allowed["historical_validation_prefixes"]["path"], columns=["user_key", "k"]).to_pandas()
    historical_windows = pq.read_table(root / allowed["historical_validation_windows"]["path"], columns=["user_key", "k"]).to_pandas()
    old_k10 = set(historical_prefixes.loc[historical_prefixes["k"] == 10, "user_key"].astype(str))
    old_any = set(historical_prefixes["user_key"].astype(str)) | set(historical_windows["user_key"].astype(str))
    selection = read_json(root / allowed["validation_selection"]["path"])
    tuning = set(map(str, selection["tuning_panel"]["5"])) | set(map(str, selection["tuning_panel"]["10"]))
    require(len(tuning) == 477, "tuning union drift")
    ratings = read_validation_bucket(
        root / allowed["validation_ratings"]["path"],
        protocol=read_json(root / allowed["evaluation_protocol"]["path"]),
    )
    expected_rows = reconstruct_episode(
        ratings,
        global_midrank=base_train_midrank(read_json(root / allowed["global_time_manifest"]["path"])),
        candidate_ids=candidate_set,
        tuning_union=tuning,
        historical_k10=old_k10,
        historical_any=old_any,
    )
    structural = pq.read_table(paths["structural_cohort"]).to_pandas().sort_values("user_key", kind="stable", ignore_index=True)
    strict = pq.read_table(paths["strict_cohort"]).to_pandas().sort_values("user_key", kind="stable", ignore_index=True)
    prefixes = pq.read_table(paths["prefixes"]).to_pandas().sort_values(["user_key", "input_rank"], kind="stable", ignore_index=True)
    windows = pq.read_table(paths["windows"]).to_pandas().sort_values(["user_key", "window_rank"], kind="stable", ignore_index=True)
    for expected, actual, name in zip(expected_rows, (structural, strict, prefixes, windows), ("structural", "strict", "prefix", "window"), strict=True):
        compare_records(expected, actual, name=name)
    require((len(structural), len(strict)) == (1021, 802), "cohort counts drift")
    require(int(strict["historical_019a_k10_user"].sum()) == 629, "historical K10 overlap drift")
    require(int((~strict["historical_019a_k10_user"]).sum()) == 173, "outside historical K10 count drift")
    require(int(strict["completely_new_to_019a_validation"].sum()) == 31, "completely-new user count drift")
    require(int(strict["historical_source_row_overlap"].sum()) == 0, "historical source-row overlap drift")

    users = strict["user_key"].astype(str).tolist()
    audit_users = selected_users(users, full_rescore_users)
    audit_set = set(audit_users)
    prefix_lookup = {str(key): group.sort_values("input_rank") for key, group in prefixes[prefixes["user_key"].isin(users)].groupby("user_key")}
    future_lookup = {
        str(key): [
            {"movie_id": int(row.movie_id), "midrank_utility": float(row.midrank_utility), "is_positive": bool(row.is_positive), "is_negative": bool(row.is_negative)}
            for row in group.sort_values("window_rank").itertuples(index=False)
        ]
        for key, group in windows[windows["user_key"].isin(users)].groupby("user_key")
    }
    arms = pq.read_table(paths["arm_definitions"]).to_pandas()
    require(len(arms) == len(users) * 2, "arm definition count drift")
    arm_lookup = {(str(row.user_key), str(row.profile)): row for row in arms.itertuples(index=False)}
    profiles: dict[tuple[str, str], tuple[np.ndarray, np.ndarray, bool]] = {}
    strata: dict[str, str] = {}
    for key in users:
        prefix = prefix_lookup[key]
        movies = list(map(int, prefix["movie_id"]))
        labels = list(map(int, prefix["binary_label"]))
        applicable: dict[str, bool] = {}
        common_seen = [movie for movie in movies if movie in candidate_position]
        for profile, count in (("K5", 5), ("K10", 10)):
            row = arm_lookup[(key, profile)]
            expected_pairs = [(movie, label) for movie, label in zip(movies[:count], labels[:count], strict=True) if movie in candidate_position]
            positions = np.asarray([candidate_position[movie] for movie, _ in expected_pairs], dtype=np.int32)
            signs = np.asarray([label for _, label in expected_pairs], dtype=np.int8)
            is_applicable = {-1, 1} <= set(map(int, signs))
            require(list(map(int, row.profile_movie_ids)) == movies[:count], "profile movie drift")
            require(list(map(int, row.profile_labels)) == labels[:count], "profile label drift")
            require(list(map(int, row.candidate_valid_movie_ids)) == [movie for movie, _ in expected_pairs], "candidate-valid profile drift")
            require(list(map(int, row.common_k10_seen_movie_ids)) == common_seen, "common K10 seen mask drift")
            require(bool(row.applicable) == is_applicable and bool(row.fallback_user) == (not is_applicable), "profile applicability drift")
            require(bool(row.full_catalog_rescored), "full-catalog rescore attestation missing")
            profiles[(key, profile)] = (positions, signs, not is_applicable)
            applicable[profile] = is_applicable
        require(not applicable["K5"] or applicable["K10"], "invalid K5/K10 applicability transition")
        strata[key] = "BOTH_LIGHTFM" if applicable["K5"] else ("K10_NEWLY_APPLICABLE" if applicable["K10"] else "BOTH_FALLBACK")

    rankings = pq.read_table(paths["rankings"]).to_pandas()
    reported_metrics = pq.read_table(paths["user_arm_metrics"]).to_pandas()
    reported_paired = pq.read_table(paths["paired_deltas"]).to_pandas().sort_values("user_key", ignore_index=True)
    require(len(rankings) == len(users) * 2 * 500, "ranking row count drift")
    require(len(reported_metrics) == len(users) * 2 and len(reported_paired) == len(users), "metric row count drift")
    metric_lookup = {(str(row.user_key), str(row.variant)): row for row in reported_metrics.itertuples(index=False)}
    ranking_lookup: dict[tuple[str, str], pd.DataFrame] = {}
    for (key, profile), group in rankings.groupby(["user_key", "profile"], sort=False):
        key = str(key)
        profile = str(profile)
        ranked = group.sort_values("rank", kind="stable")
        require(len(ranked) == 500 and ranked["rank"].astype(int).tolist() == list(range(1, 501)), "Top-500 rank drift")
        scores = ranked["effective_score"].to_numpy(dtype=np.float64)
        movies = ranked["movie_id"].to_numpy(dtype=np.int64)
        require(bool(np.all(scores[1:] <= scores[:-1])), "ranking score order drift")
        ties = scores[1:] == scores[:-1]
        require(bool(np.all(movies[1:][ties] > movies[:-1][ties])), "ranking tie-break drift")
        require(not set(map(int, arm_lookup[(key, "K10")].candidate_valid_movie_ids)).intersection(map(int, movies)), "seen item in ranking")
        fallback = profiles[(key, profile)][2]
        require(set(map(bool, ranked["fallback_used"])) == {fallback}, "ranking fallback flag drift")
        ranking_lookup[(key, profile)] = ranked
        top_values = top500_metrics(ranked, future_lookup[key], candidate_set)
        stratum = strata[key]
        variants = ["COMPARATOR"] if profile == "K5" else []
        if (stratum == "K10_NEWLY_APPLICABLE" and profile == "K10") or (stratum != "K10_NEWLY_APPLICABLE" and profile == "K5"):
            variants.append("CANDIDATE")
        for variant in variants:
            reported = metric_lookup[(key, variant)]
            for metric in ("ndcg_at_10", "recall_at_10", "mrr_at_10", "candidate_recall_at_500"):
                require(close(top_values[metric], getattr(reported, metric)), f"Top-500 metric drift: {key} {variant} {metric}")
            require(bool(top_values["harm_at_2"]) == bool(reported.harm_at_2), f"Top-500 Harm drift: {key} {variant}")

    config = contract["model"]["lightfm_config"]
    with np.load(root / allowed["lightfm_result"]["path"], allow_pickle=False) as fitted:
        item_biases = fitted["item_biases"].astype(np.float32)
        item_factors = fitted["item_factors"].astype(np.float32)
    require(item_biases.shape == (41625,) and item_factors.shape == (41625, 128), "frozen representation shape drift")
    b0 = midrank_percentiles(candidate["b0_score"].to_numpy(dtype=np.float64))
    recomputed_profiles: dict[tuple[str, str], dict[str, Any]] = {}
    boundary_ties = 0
    batch_size = int(contract["resource_bounds"]["user_batch_size_max"])
    for start in range(0, len(users), batch_size):
        batch = users[start : start + batch_size]
        if not audit_set.intersection(batch):
            continue
        for profile in ("K5", "K10"):
            biases: list[float] = []
            vectors: list[np.ndarray] = []
            fallbacks: list[bool] = []
            for key in batch:
                positions, signs, expected_fallback = profiles[(key, profile)]
                bias, vector, fallback = fold_in(
                    item_biases, item_factors, positions, signs,
                    alpha=float(config["user_alpha"]), rate=float(config["learning_rate"]),
                    epochs=int(contract["model"]["target_fold_in"]["epochs"]),
                )
                require(fallback == expected_fallback, "full-rescore fallback drift")
                biases.append(bias)
                vectors.append(vector)
                fallbacks.append(fallback)
            scores = np.vstack(vectors).astype(np.float32) @ item_factors.T
            scores += item_biases[None, :]
            scores += np.asarray(biases, dtype=np.float32)[:, None]
            for index, key in enumerate(batch):
                if key not in audit_set:
                    continue
                effective = b0.copy() if fallbacks[index] else midrank_percentiles(scores[index])
                effective[profiles[(key, "K10")][0]] = -np.inf
                values, exact500, exact501 = full_metrics(candidate_array, effective, future_lookup[key], fallback=fallbacks[index])
                persisted = ranking_lookup[(key, profile)]
                require(np.array_equal(candidate_array[exact500], persisted["movie_id"].to_numpy(dtype=np.int64)), f"full-rescore Top-500 movie drift: {key} {profile}")
                require(np.array_equal(effective[exact500].astype(np.float32), persisted["effective_score"].to_numpy(dtype=np.float32)), f"full-rescore score drift: {key} {profile}")
                require(np.array_equal(candidate_array[exact500[:10]], persisted["movie_id"].to_numpy(dtype=np.int64)[:10]), f"full-rescore Top-10 drift: {key} {profile}")
                if len(exact501) > 500 and effective[exact501[499]] == effective[exact501[500]]:
                    boundary_ties += 1
                    require(candidate_array[exact501[499]] < candidate_array[exact501[500]], "Top-500 boundary tie-break drift")
                recomputed_profiles[(key, profile)] = values

    recomputed_metric_rows: list[dict[str, Any]] = []
    for key in audit_users:
        candidate_profile = "K10" if strata[key] == "K10_NEWLY_APPLICABLE" else "K5"
        for variant, profile in (("COMPARATOR", "K5"), ("CANDIDATE", candidate_profile)):
            values = recomputed_profiles[(key, profile)]
            reported = metric_lookup[(key, variant)]
            for metric in ("ndcg_at_10", "recall_at_10", "mrr_at_10", "candidate_recall_at_500", "positive_mean_rank_percentile"):
                require(close(values[metric], getattr(reported, metric)), f"full-rescore metric drift: {key} {variant} {metric}")
            require(bool(values["harm_at_2"]) == bool(reported.harm_at_2), f"full-rescore Harm drift: {key} {variant}")
            recomputed_metric_rows.append({"user_key": key, "applicability_stratum": strata[key], "variant": variant, **values})

    paired_rows: list[dict[str, Any]] = []
    for key in users:
        comparator = metric_lookup[(key, "COMPARATOR")]
        candidate_row = metric_lookup[(key, "CANDIDATE")]
        paired_rows.append({
            "user_key": key, "applicability_stratum": strata[key],
            "candidate_source_profile": "K10" if strata[key] == "K10_NEWLY_APPLICABLE" else "K5",
            "delta_ndcg_at_10": float(candidate_row.ndcg_at_10 - comparator.ndcg_at_10),
            "delta_recall_at_10": float(candidate_row.recall_at_10 - comparator.recall_at_10),
            "delta_mrr_at_10": float(candidate_row.mrr_at_10 - comparator.mrr_at_10),
            "delta_candidate_recall_at_500": float(candidate_row.candidate_recall_at_500 - comparator.candidate_recall_at_500),
            "delta_positive_mean_rank_percentile": float(candidate_row.positive_mean_rank_percentile - comparator.positive_mean_rank_percentile),
            "delta_harm_at_2": float(candidate_row.harm_at_2) - float(comparator.harm_at_2),
            "delta_fallback_user": float(candidate_row.fallback_user) - float(comparator.fallback_user),
            "delta_applicable_user": float(candidate_row.applicable_user) - float(comparator.applicable_user),
        })
    paired = pd.DataFrame(paired_rows).sort_values("user_key", ignore_index=True)
    require(list(paired.columns) == list(reported_paired.columns), "paired columns drift")
    for column in paired.columns:
        if column.startswith("delta_"):
            require(bool(np.allclose(paired[column], reported_paired[column], rtol=1e-12, atol=1e-12)), f"paired delta drift: {column}")
        else:
            require(paired[column].tolist() == reported_paired[column].tolist(), f"paired value drift: {column}")
    boot = bootstrap(
        paired["delta_ndcg_at_10"].to_numpy(), paired["delta_harm_at_2"].to_numpy(),
        iterations=int(contract["bootstrap"]["iterations"]), seed=int(contract["bootstrap"]["seed"]),
    )
    reported_boot = result["paired_strict"]["bootstrap"]
    for key in ("ndcg_mean", "harm_mean", "harm_one_sided_95_upper"):
        require(close(boot[key], reported_boot[key], tolerance=1e-12), f"bootstrap drift: {key}")
    require(bool(np.allclose(boot["ndcg_two_sided_95"], reported_boot["ndcg_two_sided_95"], rtol=1e-12, atol=1e-12)), "bootstrap interval drift")
    final_decision = decision(boot)
    require(final_decision == result["decision"], "decision drift")
    require(result["status"] == final_decision["status"], "result status drift")
    require(result["paired_strict"]["benefit_harm_user_counts"] == benefit_counts(paired), "benefit/neutral/harm drift")
    counts = {str(key): int(value) for key, value in paired["applicability_stratum"].value_counts().sort_index().items()}
    require(counts == result["routing_counts"], "routing counts drift")
    for variant in ("COMPARATOR", "CANDIDATE"):
        actual = aggregate(reported_metrics[reported_metrics["variant"] == variant])
        for key, value in actual.items():
            expected = result["aggregate_metrics"][variant][key]
            require(value == expected if key == "users" else close(value, expected), f"aggregate drift: {variant} {key}")
    for payload in (lock, source_manifest, progress, result, manifest):
        require(payload["locked_test_used"] is False, "final Locked Test invariant failed")
        require(payload["champion"] is None, "final champion invariant failed")
        require(payload["product_policy_updated"] is False, "final product policy invariant failed")
    return {
        "status": "PASS_INDEPENDENT_RECONSTRUCTION_AND_FULL_RESCORE",
        "decision": final_decision,
        "structural_users": 1021,
        "strict_users": 802,
        "routing_counts": counts,
        "source_row_overlap": 0,
        "user_overlap": {"historical_019a_k10": 629, "outside_historical_019a_k10": 173, "completely_new_to_019a_validation": 31},
        "full_rescore": {
            "mode": "ALL_STRICT_USERS" if len(audit_users) == len(users) else "DETERMINISTIC_SUBSET",
            "users": len(audit_users), "profiles": len(audit_users) * 2, "candidate_count": 41625,
            "exact_top10_verified": True, "exact_top500_verified": True,
            "positive_mean_rank_percentile_verified": True, "top500_boundary_tie_rankings": boundary_ties,
        },
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--full-rescore-users", default="64", help="Deterministic user count or 'all' for all 802 strict users")
    args = parser.parse_args()
    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    requested: int | str = "all" if str(args.full_rescore_users).lower() == "all" else int(args.full_rescore_users)
    print(json.dumps(verify(manifest, full_rescore_users=requested), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
