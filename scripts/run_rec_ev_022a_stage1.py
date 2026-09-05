#!/usr/bin/env python3
"""Lock, prepare, score, and analyze the REC-EV-022A Stage-1 experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse

try:
    from rec_ev_022a_core import (
        RATING_VALUES,
        build_structured_full,
        deterministic_rank,
        encoding_weights,
        itemknn_pair_similarity,
        old_user_bucket,
        order_key,
        pair1_metrics,
        pairwise_concordance,
        rating_indices,
        score_judged_targets,
        simultaneous_max_t,
        structured_pair_similarity,
        user_equal_prior,
        user_key,
        user_role_bucket,
    )
    from validate_rec_ev_022a_contract import validate_contract
except ModuleNotFoundError:  # imported as scripts.run_rec_ev_022a_stage1 by unittest
    from scripts.rec_ev_022a_core import (
        RATING_VALUES,
        build_structured_full,
        deterministic_rank,
        encoding_weights,
        itemknn_pair_similarity,
        old_user_bucket,
        order_key,
        pair1_metrics,
        pairwise_concordance,
        rating_indices,
        score_judged_targets,
        simultaneous_max_t,
        structured_pair_similarity,
        user_equal_prior,
        user_key,
        user_role_bucket,
    )
    from scripts.validate_rec_ev_022a_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-022a-k-input-encoding-stage1.json"
MAX_USER_ID = 300_000
CHUNK_ROWS = 1_000_000
ENCODINGS = ("BINARY_SIGN", "PERCENTILE_MAGNITUDE", "ORDINAL_RANK")
ANCHORS = ("STRUCTURED_CONTENT_SIM", "USER_DISJOINT_ITEMKNN_SIM")


class ResumeError(RuntimeError):
    pass


class InputFirewallError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_contract(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = canonical_json_bytes(value)
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_input(entry: Mapping[str, Any]) -> Path:
    raw = Path(str(entry["path"]))
    return raw.resolve() if raw.is_absolute() else (ROOT / raw).resolve()


def verify_sources(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    forbidden = {(ROOT / path).resolve() for path in contract["forbidden_input_artifacts"]}
    artifacts: list[dict[str, Any]] = []
    for name, entry in sorted(contract["allowed_input_artifacts"].items()):
        path = resolve_input(entry)
        if path in forbidden:
            raise InputFirewallError(f"forbidden input requested: {name}")
        if not path.is_file():
            raise FileNotFoundError(f"missing allowlisted input: {name}")
        if path.stat().st_size != int(entry["bytes"]):
            raise RuntimeError(f"input byte drift: {name}")
        digest = sha256_file(path)
        if digest != str(entry["sha256"]):
            raise RuntimeError(f"input checksum drift: {name}")
        artifacts.append({"name": name, "path": str(entry["path"]), "bytes": path.stat().st_size, "sha256": digest})
    return artifacts


def verify_implementation(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for raw in contract["implementation_artifacts"]:
        path = (ROOT / str(raw)).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise RuntimeError(f"implementation artifact missing or outside repository: {raw}")
        artifacts.append({
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return artifacts


def output_path(contract: Mapping[str, Any], name: str) -> Path:
    return ROOT / str(contract["output_root"]) / str(contract["outputs"][name])


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "implementation_artifacts", "source_population", "prefilter_reader", "user_roles", "determinism", "rating_scale", "item_universe",
        "cohort", "descriptive_strata", "encodings", "anchors", "scoring", "evaluation",
        "statistics", "decision", "role_boundary", "resume", "invariants",
    )
    return {key: contract[key] for key in keys}


def create_or_verify_lock(contract: Mapping[str, Any], contract_path: Path, *, resume: bool) -> dict[str, Any]:
    output_root = ROOT / str(contract["output_root"])
    lock_path = output_path(contract, "protocol_lock")
    manifest_path = output_path(contract, "source_manifest")
    contract_hash = sha256_contract(contract_path)
    sources = verify_sources(contract)
    implementations = verify_implementation(contract)
    source_hash = hashlib.sha256(canonical_json_bytes(sources)).hexdigest()
    implementation_hash = hashlib.sha256(canonical_json_bytes(implementations)).hexdigest()
    spec_hash = hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest()
    if lock_path.is_file():
        if not resume:
            raise ResumeError("existing protocol lock requires --resume")
        lock = read_json(lock_path)
        expected = {
            "contract_sha256": contract_hash,
            "source_artifacts_sha256": source_hash,
            "implementation_artifacts_sha256": implementation_hash,
            "locked_spec_sha256": spec_hash,
            "future_labels_joined_at_lock": False,
        }
        for key, value in expected.items():
            if lock.get(key) != value:
                raise ResumeError(f"protocol lock mismatch: {key}")
        if not manifest_path.is_file() or sha256_file(manifest_path) != lock.get("source_manifest_sha256"):
            raise ResumeError("source manifest drift")
        return lock
    if resume:
        raise ResumeError("--resume cannot create the first protocol lock; run --phase lock first")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-022A",
        "created_before_rating_member_open": True,
        "sources": sources,
        "implementation_artifacts": implementations,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
    }
    atomic_write_json(manifest_path, manifest)
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-022A",
        "status": "PREREGISTERED_BEFORE_RATING_MEMBER_OPEN",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract_path": contract_path.relative_to(ROOT).as_posix(),
        "contract_sha256": contract_hash,
        "source_manifest_sha256": sha256_file(manifest_path),
        "source_artifacts_sha256": source_hash,
        "implementation_artifacts_sha256": implementation_hash,
        "locked_spec_sha256": spec_hash,
        "future_labels_joined_at_lock": False,
        "locked_test_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    atomic_write_json(lock_path, lock)
    return lock


def progress_update(contract: Mapping[str, Any], phase: str, **extra: Any) -> None:
    path = output_path(contract, "progress")
    previous = read_json(path) if path.is_file() else {"schema_version": 1, "evidence_id": "REC-EV-022A"}
    previous.update({"phase": phase, "updated_at_utc": datetime.now(timezone.utc).isoformat(), **extra})
    atomic_write_json(path, previous)


def run_signature(contract: Mapping[str, Any]) -> str:
    lock = read_json(output_path(contract, "protocol_lock"))
    payload = {key: lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def write_integrity(path: Path, artifacts: Mapping[str, Path], *, signature: str, metadata: Mapping[str, Any]) -> None:
    atomic_write_json(path, {
        "schema_version": 1,
        "run_signature": signature,
        "artifacts": {
            name: {"path": artifact.relative_to(ROOT).as_posix(), "bytes": artifact.stat().st_size, "sha256": sha256_file(artifact)}
            for name, artifact in sorted(artifacts.items())
        },
        "metadata": dict(metadata),
    })


def verify_integrity(path: Path, artifacts: Mapping[str, Path], *, signature: str) -> dict[str, Any]:
    if not path.is_file():
        raise ResumeError(f"integrity manifest missing: {path.name}")
    value = read_json(path)
    if value.get("run_signature") != signature or set(value.get("artifacts", {})) != set(artifacts):
        raise ResumeError(f"integrity signature or artifact set mismatch: {path.name}")
    for name, artifact in artifacts.items():
        expected = value["artifacts"][name]
        if not artifact.is_file() or artifact.stat().st_size != int(expected["bytes"]) or sha256_file(artifact) != expected["sha256"]:
            raise ResumeError(f"artifact integrity mismatch: {name}")
    return value


def _role_lookups() -> tuple[np.ndarray, np.ndarray]:
    old_allowed = np.zeros(MAX_USER_ID + 1, dtype=bool)
    new_bucket = np.zeros(MAX_USER_ID + 1, dtype=np.uint16)
    for identifier in range(1, MAX_USER_ID + 1):
        old_allowed[identifier] = old_user_bucket(identifier) <= 59
        new_bucket[identifier] = user_role_bucket(identifier)
    return old_allowed, new_bucket


def _rating_chunks(
    archive: Path,
    member: str,
    *,
    allowed_user_mask: np.ndarray,
    include_timestamp: bool,
) -> Iterable[pd.DataFrame]:
    """Parse only userId until the pre-authorized role mask accepts the raw line."""
    allowed = np.asarray(allowed_user_mask, dtype=bool)
    with zipfile.ZipFile(archive) as bundle:
        if member not in bundle.namelist():
            raise RuntimeError("locked MovieLens rating member missing")
        with bundle.open(member) as handle:
            if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
                raise RuntimeError("MovieLens ratings header drift")
            users: list[int] = []
            movies: list[int] = []
            ratings: list[float] = []
            timestamps: list[int] = []

            def make_frame() -> pd.DataFrame:
                data: dict[str, Any] = {
                    "userId": np.asarray(users, dtype=np.int32),
                    "movieId": np.asarray(movies, dtype=np.int32),
                    "rating": np.asarray(ratings, dtype=np.float32),
                }
                if include_timestamp:
                    data["timestamp"] = np.asarray(timestamps, dtype=np.int64)
                return pd.DataFrame(data)

            for raw_line in handle:
                first_comma = raw_line.find(b",")
                if first_comma <= 0:
                    raise RuntimeError("MovieLens row has no userId delimiter")
                raw_user = int(raw_line[:first_comma])
                if raw_user < 0 or raw_user >= len(allowed) or not allowed[raw_user]:
                    continue
                fields = raw_line[first_comma + 1 :].rstrip(b"\r\n").split(b",")
                if len(fields) != 3:
                    raise RuntimeError("allowed MovieLens row column drift")
                users.append(raw_user)
                movies.append(int(fields[0]))
                ratings.append(float(fields[1]))
                if include_timestamp:
                    timestamps.append(int(fields[2]))
                if len(users) == CHUNK_ROWS:
                    yield make_frame()
                    users.clear()
                    movies.clear()
                    ratings.clear()
                    timestamps.clear()
            if users:
                yield make_frame()


def _entropy(hist: np.ndarray) -> np.ndarray:
    totals = hist.sum(axis=1, keepdims=True)
    probability = np.divide(hist, totals, out=np.zeros_like(hist, dtype=np.float64), where=totals > 0)
    logs = np.zeros_like(probability)
    np.log(probability, out=logs, where=probability > 0)
    return -(probability * logs).sum(axis=1) / math.log(hist.shape[1])


def prepare(contract: Mapping[str, Any]) -> dict[str, Any]:
    train_cache = output_path(contract, "train_cache")
    train_z_path = output_path(contract, "train_z")
    structured_cache = output_path(contract, "structured_cache")
    cohort_path = output_path(contract, "cohort_cache")
    cache_integrity = output_path(contract, "cache_integrity")
    cache_artifacts = {
        "cohort_cache": cohort_path,
        "structured_cache": structured_cache,
        "train_cache": train_cache,
        "train_z": train_z_path,
    }
    signature = run_signature(contract)
    if cache_integrity.exists() or any(path.exists() for path in cache_artifacts.values()):
        integrity = verify_integrity(cache_integrity, cache_artifacts, signature=signature)
        cohort_users = pq.read_metadata(cohort_path).num_rows
        if int(integrity["metadata"]["cohort_users"]) != cohort_users:
            raise ResumeError("prepared cohort row-count drift")
        return {"status": "REUSED_PREPARED_CACHE", "cohort_users": cohort_users}

    started = time.monotonic()
    progress_update(contract, "PREPARE_PASS1")
    archive_entry = contract["allowed_input_artifacts"]["movielens_archive"]
    archive = resolve_input(archive_entry)
    member = str(archive_entry["member"])
    candidate = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["candidate_identity"]), columns=["movie_id"])
    structured_frame = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["structured_features"]))
    structured_ok = structured_frame.loc[structured_frame["feature_eligible"], "movie_id"].astype(np.int64)
    initial_ids = np.intersect1d(candidate["movie_id"].to_numpy(dtype=np.int64), structured_ok.to_numpy(dtype=np.int64))
    max_movie = int(initial_ids.max(initial=0))
    initial_lookup = np.full(max_movie + 1, -1, dtype=np.int32)
    initial_lookup[initial_ids] = np.arange(len(initial_ids), dtype=np.int32)
    old_allowed, role_buckets = _role_lookups()
    stage1_allowed_users = old_allowed & (role_buckets < 8_000)

    train_hist = np.zeros((MAX_USER_ID + 1, len(RATING_VALUES)), dtype=np.uint32)
    stage1_hist = np.zeros_like(train_hist)
    train_movie_count = np.zeros(len(initial_ids), dtype=np.int64)
    train_movie_sum = np.zeros(len(initial_ids), dtype=np.float64)
    stage1_min_timestamp = np.full(MAX_USER_ID + 1, np.iinfo(np.int64).max, dtype=np.int64)
    train_min_timestamp = stage1_min_timestamp.copy()
    total_train_sum = 0.0
    total_train_count = 0
    rows_seen = 0
    for chunk_number, chunk in enumerate(_rating_chunks(
        archive, member, allowed_user_mask=stage1_allowed_users, include_timestamp=True,
    ), start=1):
        users = chunk["userId"].to_numpy(dtype=np.int64, copy=False)
        if users.max(initial=0) > MAX_USER_ID:
            raise RuntimeError("MovieLens user id exceeds the preregistered lookup bound")
        allowed = old_allowed[users] & (role_buckets[users] < 8_000)
        if not bool(allowed.any()):
            continue
        users = users[allowed]
        buckets = role_buckets[users]
        ratings = chunk["rating"].to_numpy(dtype=np.float64, copy=False)[allowed]
        movies = chunk["movieId"].to_numpy(dtype=np.int64, copy=False)[allowed]
        timestamps = chunk["timestamp"].to_numpy(dtype=np.int64, copy=False)[allowed]
        indices = rating_indices(ratings)
        train_mask = buckets < 6_000
        stage1_mask = (buckets >= 6_000) & (buckets < 8_000)
        if bool(train_mask.any()):
            train_users = users[train_mask]
            np.add.at(train_hist, (train_users, indices[train_mask]), 1)
            np.minimum.at(train_min_timestamp, train_users, timestamps[train_mask])
            total_train_sum += float(ratings[train_mask].sum())
            total_train_count += int(train_mask.sum())
            train_movies = movies[train_mask]
            valid_movie = train_movies <= max_movie
            positions = np.full(len(train_movies), -1, dtype=np.int32)
            positions[valid_movie] = initial_lookup[train_movies[valid_movie]]
            keep = positions >= 0
            np.add.at(train_movie_count, positions[keep], 1)
            np.add.at(train_movie_sum, positions[keep], ratings[train_mask][keep])
        if bool(stage1_mask.any()):
            stage1_users = users[stage1_mask]
            np.add.at(stage1_hist, (stage1_users, indices[stage1_mask]), 1)
            np.minimum.at(stage1_min_timestamp, stage1_users, timestamps[stage1_mask])
        rows_seen += len(chunk)
        progress_update(contract, "PREPARE_PASS1", chunks=chunk_number, source_rows_seen=rows_seen)

    train_user_ids = np.flatnonzero(train_hist.sum(axis=1) > 0)
    stage1_user_ids = np.flatnonzero(stage1_hist.sum(axis=1) > 0)
    pi0, g0_mid = user_equal_prior(train_hist[train_user_ids])
    i0_keep = train_movie_count >= 2
    i0_ids = initial_ids[i0_keep]
    i0_counts = train_movie_count[i0_keep]
    i0_sums = train_movie_sum[i0_keep]
    i0_lookup = np.full(max_movie + 1, -1, dtype=np.int32)
    i0_lookup[i0_ids] = np.arange(len(i0_ids), dtype=np.int32)
    train_row_lookup = np.full(MAX_USER_ID + 1, -1, dtype=np.int32)
    train_row_lookup[train_user_ids] = np.arange(len(train_user_ids), dtype=np.int32)

    train_counts = train_hist.sum(axis=1).astype(np.float64)
    train_below = np.cumsum(train_hist, axis=1) - train_hist
    q_train = np.divide(
        train_below + 0.5 * train_hist + 5.0 * g0_mid[None, :],
        train_counts[:, None] + 5.0,
        out=np.zeros_like(train_hist, dtype=np.float64),
        where=(train_counts[:, None] + 5.0) > 0,
    )
    z_by_user_rating = (2.0 * q_train - 1.0).astype(np.float32)

    progress_update(contract, "PREPARE_PASS2", i0_items=len(i0_ids), train_users=len(train_user_ids), stage1_users=len(stage1_user_ids))
    z_rows: list[np.ndarray] = []
    z_columns: list[np.ndarray] = []
    z_values: list[np.ndarray] = []
    stage1_rows: list[pd.DataFrame] = []
    for chunk_number, chunk in enumerate(_rating_chunks(
        archive, member, allowed_user_mask=stage1_allowed_users, include_timestamp=False,
    ), start=1):
        users_all = chunk["userId"].to_numpy(dtype=np.int64, copy=False)
        allowed = old_allowed[users_all] & (role_buckets[users_all] < 8_000)
        users = users_all[allowed]
        buckets = role_buckets[users]
        movies = chunk["movieId"].to_numpy(dtype=np.int64, copy=False)[allowed]
        ratings = chunk["rating"].to_numpy(dtype=np.float64, copy=False)[allowed]
        rating_idx = rating_indices(ratings)
        valid_range = movies <= max_movie
        positions = np.full(len(movies), -1, dtype=np.int32)
        positions[valid_range] = i0_lookup[movies[valid_range]]
        train_keep = (buckets < 6_000) & (positions >= 0)
        if bool(train_keep.any()):
            rows = train_row_lookup[users[train_keep]]
            values = z_by_user_rating[users[train_keep], rating_idx[train_keep]]
            nonzero = values != 0
            z_rows.append(rows[nonzero].astype(np.int32, copy=False))
            z_columns.append(positions[train_keep][nonzero].astype(np.int32, copy=False))
            z_values.append(values[nonzero].astype(np.float32, copy=False))
        stage1_keep = (buckets >= 6_000) & (buckets < 8_000) & (positions >= 0)
        if bool(stage1_keep.any()):
            stage1_rows.append(pd.DataFrame({
                "user_id": users[stage1_keep].astype(np.int32),
                "movie_id": movies[stage1_keep].astype(np.int32),
                "rating_idx": rating_idx[stage1_keep].astype(np.int8),
                "i0_position": positions[stage1_keep].astype(np.int32),
            }))
        progress_update(contract, "PREPARE_PASS2", chunks=chunk_number, i0_items=len(i0_ids))

    z_matrix = sparse.coo_matrix(
        (np.concatenate(z_values), (np.concatenate(z_rows), np.concatenate(z_columns))),
        shape=(len(train_user_ids), len(i0_ids)), dtype=np.float32,
    ).tocsr()
    z_matrix.sum_duplicates()
    column_norms_i0 = np.sqrt(np.asarray(z_matrix.multiply(z_matrix).sum(axis=0)).ravel())
    i_star_mask = column_norms_i0 > 0
    i_star_ids = i0_ids[i_star_mask]
    z_matrix = z_matrix[:, i_star_mask].tocsr()
    column_norms = column_norms_i0[i_star_mask].astype(np.float32)
    b0_counts = i0_counts[i_star_mask]
    b0_sums = i0_sums[i_star_mask]
    global_mean = total_train_sum / total_train_count
    b0_scores = (b0_sums + 100.0 * global_mean) / (b0_counts + 100.0)
    b0_order = np.lexsort((i_star_ids, b0_scores))
    b0_rank = np.empty(len(i_star_ids), dtype=np.int32)
    b0_rank[b0_order] = np.arange(len(i_star_ids), dtype=np.int32)
    b0_percentile = b0_rank.astype(np.float64) / max(1, len(i_star_ids) - 1)

    structured_subset = structured_frame.set_index("movie_id", verify_integrity=True).loc[i_star_ids].reset_index()
    structured_full = build_structured_full(structured_subset, i_star_ids)
    if bool((np.asarray(structured_full.getnnz(axis=1)).ravel() == 0).any()):
        raise RuntimeError("I_STAR contains a zero structured row")

    stage1 = pd.concat(stage1_rows, ignore_index=True)
    i_star_position_by_i0 = np.full(len(i0_ids), -1, dtype=np.int32)
    i_star_position_by_i0[np.flatnonzero(i_star_mask)] = np.arange(len(i_star_ids), dtype=np.int32)
    stage1["item_position"] = i_star_position_by_i0[stage1["i0_position"].to_numpy(dtype=np.int32)]
    stage1 = stage1.loc[stage1["item_position"] >= 0].copy()
    counts = stage1.groupby("user_id", sort=False)["movie_id"].size()
    eligible_users = counts.index[counts >= 50].to_numpy(dtype=np.int32)
    if len(eligible_users) < int(contract["cohort"]["minimum_common30_users"]):
        raise RuntimeError(f"COMMON30 minimum failed: {len(eligible_users)}")

    progress_update(contract, "BUILD_COMMON30", common30_users=len(eligible_users), i_star_items=len(i_star_ids))
    cohort_rows: list[dict[str, Any]] = []
    primary_salt = str(contract["determinism"]["primary_order_salt"])
    grouped = stage1.loc[stage1["user_id"].isin(eligible_users)].groupby("user_id", sort=True)
    for raw_user_id, frame in grouped:
        anonymous = user_key(int(raw_user_id))
        records = list(zip(
            frame["movie_id"].astype(int), frame["rating_idx"].astype(int), frame["item_position"].astype(int), strict=True
        ))
        records.sort(key=lambda row: order_key(primary_salt, anonymous, row[0]))
        selected = records[:50]
        full_hist = stage1_hist[int(raw_user_id)].astype(np.float64)
        below = np.cumsum(full_hist) - full_hist
        target_rating_idx = np.asarray([row[1] for row in selected[30:50]], dtype=np.int8)
        target_q = (below[target_rating_idx] + 0.5 * full_hist[target_rating_idx]) / full_hist.sum()
        user_i_star_positions = frame["item_position"].to_numpy(dtype=np.int32)
        cohort_rows.append({
            "user_key": anonymous,
            "full_rating_count": int(full_hist.sum()),
            "join_timestamp": int(stage1_min_timestamp[int(raw_user_id)]),
            "preference_entropy": float(_entropy(full_hist[None, :])[0]),
            "popularity_affinity": float(b0_percentile[user_i_star_positions].mean()),
            "profile_movie_ids": [row[0] for row in selected[:30]],
            "profile_rating_idx": [row[1] for row in selected[:30]],
            "profile_item_positions": [row[2] for row in selected[:30]],
            "target_movie_ids": [row[0] for row in selected[30:50]],
            "target_rating_idx": target_rating_idx.tolist(),
            "target_item_positions": [row[2] for row in selected[30:50]],
            "target_q_eval": target_q.astype(np.float32).tolist(),
        })
    cohort = pd.DataFrame(cohort_rows).sort_values("user_key", kind="stable", ignore_index=True)

    train_cache.parent.mkdir(parents=True, exist_ok=True)
    sparse.save_npz(train_z_path, z_matrix, compressed=True)
    sparse.save_npz(structured_cache, structured_full, compressed=True)
    np.savez_compressed(
        train_cache,
        item_ids=i_star_ids.astype(np.int32),
        column_norms=column_norms,
        b0_scores=b0_scores.astype(np.float64),
        b0_percentile=b0_percentile.astype(np.float64),
        pi0=pi0,
        g0_mid=g0_mid,
        global_mean=np.asarray([global_mean]),
    )
    cohort.to_parquet(cohort_path, index=False)
    write_integrity(
        cache_integrity,
        cache_artifacts,
        signature=signature,
        metadata={
            "cohort_users": len(cohort),
            "i_star_items": len(i_star_ids),
            "train_users": len(train_user_ids),
        },
    )
    progress_update(
        contract, "PREPARED", common30_users=len(cohort), i_star_items=len(i_star_ids),
        train_users=len(train_user_ids), elapsed_seconds=round(time.monotonic() - started, 3),
    )
    return {"status": "PREPARED", "cohort_users": len(cohort), "i_star_items": len(i_star_ids)}


def _metric_part_path(parts_root: Path, start: int, stop: int) -> Path:
    return parts_root / f"part-{start:06d}-{stop:06d}.parquet"


def score(contract: Mapping[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    signature = run_signature(contract)
    train_cache_path = output_path(contract, "train_cache")
    train_z_path = output_path(contract, "train_z")
    structured_cache_path = output_path(contract, "structured_cache")
    cohort_cache_path = output_path(contract, "cohort_cache")
    prepared_integrity = verify_integrity(
        output_path(contract, "cache_integrity"),
        {
            "cohort_cache": cohort_cache_path,
            "structured_cache": structured_cache_path,
            "train_cache": train_cache_path,
            "train_z": train_z_path,
        },
        signature=signature,
    )
    model = np.load(train_cache_path, allow_pickle=False)
    z_matrix = sparse.load_npz(train_z_path).tocsc()
    observed = z_matrix.copy()
    observed.data = np.ones_like(observed.data, dtype=np.float32)
    structured = sparse.load_npz(structured_cache_path).tocsr()
    cohort = pd.read_parquet(cohort_cache_path).sort_values("user_key", kind="stable", ignore_index=True)
    if int(prepared_integrity["metadata"]["cohort_users"]) != len(cohort):
        raise ResumeError("prepared cohort metadata drift before score")
    g0_mid = model["g0_mid"]
    b0_scores_all = model["b0_scores"]
    column_norms = model["column_norms"]
    parts_root = output_path(contract, "metric_parts")
    parts_root.mkdir(parents=True, exist_ok=True)
    batch_size = int(contract["resume"]["checkpoint_after_users"])
    expected_ranges = [(start, min(len(cohort), start + batch_size)) for start in range(0, len(cohort), batch_size)]
    expected_parts = {_metric_part_path(parts_root, start, stop) for start, stop in expected_ranges}
    expected_part_integrities = {path.with_suffix(".integrity.json") for path in expected_parts}
    user_metrics_path = output_path(contract, "user_metrics")
    user_metrics_integrity_path = output_path(contract, "user_metrics_integrity")
    if user_metrics_path.exists() != user_metrics_integrity_path.exists():
        raise ResumeError("partial combined user metrics state")
    combined_preexisting = user_metrics_path.exists()
    completed_users = 0
    for start in range(0, len(cohort), batch_size):
        stop = min(len(cohort), start + batch_size)
        part_path = _metric_part_path(parts_root, start, stop)
        part_integrity = part_path.with_suffix(".integrity.json")
        if part_path.exists() or part_integrity.exists():
            integrity = verify_integrity(part_integrity, {"part": part_path}, signature=signature)
            expected_keys = cohort.iloc[start:stop]["user_key"].tolist()
            actual_keys = pd.read_parquet(part_path, columns=["user_key"])["user_key"].drop_duplicates().tolist()
            expected_rows = (stop - start) * (31 * 2 * 2 + 30 * 2)
            expected_metadata = {"start": start, "stop": stop, "rows": expected_rows, "user_keys": expected_keys}
            if integrity.get("metadata") != expected_metadata or actual_keys != expected_keys:
                raise ResumeError(f"metric part slice drift: {part_path.name}")
            completed_users = stop
            continue
        rows: list[dict[str, Any]] = []
        for user in cohort.iloc[start:stop].itertuples(index=False):
            profile_positions = np.asarray(user.profile_item_positions, dtype=np.int32)
            target_positions = np.asarray(user.target_item_positions, dtype=np.int32)
            profile_ratings = RATING_VALUES[np.asarray(user.profile_rating_idx, dtype=np.int8)]
            target_ids = np.asarray(user.target_movie_ids, dtype=np.int64)
            target_q = np.asarray(user.target_q_eval, dtype=np.float64)
            b0 = b0_scores_all[target_positions]
            similarities = {
                "STRUCTURED_CONTENT_SIM": structured_pair_similarity(structured, profile_positions, target_positions),
                "USER_DISJOINT_ITEMKNN_SIM": itemknn_pair_similarity(
                    z_matrix, observed, column_norms, profile_positions, target_positions,
                    shrinkage=50.0, minimum_support=2,
                ),
            }
            for encoding in ENCODINGS:
                for k in range(31):
                    if encoding == "ORDINAL_RANK" and k == 1:
                        continue
                    weights = encoding_weights(encoding, profile_ratings[:k], g0_mid, tau=5.0)
                    for anchor in ANCHORS:
                        personal, fallback = score_judged_targets(similarities[anchor][:, :k], weights)
                        order = deterministic_rank(target_ids, personal, b0, fallback=fallback)
                        mean_q, worst_loss = pair1_metrics(target_q[order])
                        ranking_scores = b0 if fallback else personal
                        rows.append({
                            "user_key": user.user_key,
                            "encoding": encoding,
                            "anchor": anchor,
                            "k": k,
                            "pair1_mean_q": mean_q,
                            "pair1_worst_q_loss": worst_loss,
                            "pairwise_concordance": pairwise_concordance(ranking_scores, target_q),
                            "fallback": bool(fallback),
                        })
        pd.DataFrame(rows).to_parquet(part_path, index=False)
        write_integrity(
            part_integrity,
            {"part": part_path},
            signature=signature,
            metadata={
                "start": start,
                "stop": stop,
                "rows": len(rows),
                "user_keys": cohort.iloc[start:stop]["user_key"].tolist(),
            },
        )
        completed_users = stop
        progress_update(contract, "SCORING", completed_users=completed_users, total_users=len(cohort), elapsed_seconds=round(time.monotonic() - started, 3))

    actual_parts = set(parts_root.glob("part-*.parquet"))
    actual_part_integrities = set(parts_root.glob("part-*.integrity.json"))
    if actual_parts != expected_parts or actual_part_integrities != expected_part_integrities:
        raise ResumeError("metric part path set drift")
    parts = sorted(actual_parts)
    metrics = pd.concat((pd.read_parquet(path) for path in parts), ignore_index=True)
    expected = len(cohort) * (31 * 2 * 2 + 30 * 2)
    if len(metrics) != expected:
        raise RuntimeError(f"metric row count drift: {len(metrics)} != {expected}")
    if combined_preexisting:
        integrity = verify_integrity(
            user_metrics_integrity_path,
            {"user_metrics": user_metrics_path},
            signature=signature,
        )
        existing = pd.read_parquet(user_metrics_path, columns=["user_key"])
        if integrity.get("metadata") != {"rows": len(existing), "users": existing["user_key"].nunique()}:
            raise ResumeError("combined user metrics metadata drift before score reuse")
        if len(existing) != expected or existing["user_key"].nunique() != len(cohort):
            raise ResumeError("combined user metrics completeness drift")
        return {"status": "REUSED_SCORED_METRICS", "users": len(cohort), "metric_rows": len(existing)}
    metrics.to_parquet(user_metrics_path, index=False)
    write_integrity(
        user_metrics_integrity_path,
        {"user_metrics": user_metrics_path},
        signature=signature,
        metadata={"rows": len(metrics), "users": len(cohort)},
    )
    progress_update(contract, "SCORED", completed_users=len(cohort), metric_rows=len(metrics), elapsed_seconds=round(time.monotonic() - started, 3))
    return {"status": "SCORED", "users": len(cohort), "metric_rows": len(metrics)}


def _build_contrasts(metrics: pd.DataFrame) -> tuple[np.ndarray, list[dict[str, Any]], list[str]]:
    users = sorted(metrics["user_key"].unique())
    indexed = metrics.set_index(["user_key", "encoding", "anchor", "k"])
    columns: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []
    for encoding in ENCODINGS:
        for anchor in ANCHORS:
            base = indexed.loc[(slice(None), encoding, anchor, 0), ["pair1_mean_q", "pair1_worst_q_loss"]].droplevel([1, 2, 3]).reindex(users)
            k30 = indexed.loc[(slice(None), encoding, anchor, 30), ["pair1_mean_q", "pair1_worst_q_loss"]].droplevel([1, 2, 3]).reindex(users)
            for k in range(1, 31):
                if encoding == "ORDINAL_RANK" and k == 1:
                    continue
                current = indexed.loc[(slice(None), encoding, anchor, k), ["pair1_mean_q", "pair1_worst_q_loss"]].droplevel([1, 2, 3]).reindex(users)
                for metric in ("pair1_mean_q", "pair1_worst_q_loss"):
                    columns.append((current[metric] - base[metric]).to_numpy(dtype=np.float64))
                    metadata.append({"encoding": encoding, "anchor": anchor, "k": k, "reference": 0, "metric": metric})
                    if k < 30:
                        columns.append((current[metric] - k30[metric]).to_numpy(dtype=np.float64))
                        metadata.append({"encoding": encoding, "anchor": anchor, "k": k, "reference": 30, "metric": metric})
    return np.column_stack(columns), metadata, users


def _contrast_key(row: Mapping[str, Any]) -> tuple[str, str, int, int, str]:
    return str(row["encoding"]), str(row["anchor"]), int(row["k"]), int(row["reference"]), str(row["metric"])


def analyze(contract: Mapping[str, Any]) -> dict[str, Any]:
    user_metrics_path = output_path(contract, "user_metrics")
    integrity = verify_integrity(
        output_path(contract, "user_metrics_integrity"),
        {"user_metrics": user_metrics_path},
        signature=run_signature(contract),
    )
    metrics = pd.read_parquet(user_metrics_path)
    if integrity.get("metadata") != {"rows": len(metrics), "users": metrics["user_key"].nunique()}:
        raise ResumeError("combined user metrics metadata drift")
    values, metadata, users = _build_contrasts(metrics)
    progress_update(contract, "BOOTSTRAP", users=len(users), contrasts=len(metadata))
    intervals = simultaneous_max_t(
        values,
        repeats=int(contract["statistics"]["bootstrap_repeats"]),
        seed=int(contract["statistics"]["seed"]),
    )
    lookup: dict[tuple[str, str, int, int, str], dict[str, float]] = {}
    rows: list[dict[str, Any]] = []
    for index, meta in enumerate(metadata):
        row = {
            **meta,
            "mean": float(intervals["mean"][index]),
            "low": float(intervals["low"][index]),
            "high": float(intervals["high"][index]),
            "half_width": float(intervals["half_width"][index]),
        }
        rows.append(row)
        lookup[_contrast_key(meta)] = row

    decision = contract["decision"]
    utility_margin = float(decision["utility_margin"])
    loss_margin = float(decision["worst_loss_noninferiority_margin"])

    def k0_pass(encoding: str, anchor: str, k: int) -> bool:
        utility = lookup[(encoding, anchor, k, 0, "pair1_mean_q")]
        loss = lookup[(encoding, anchor, k, 0, "pair1_worst_q_loss")]
        return (
            utility["half_width"] <= float(decision["precision_half_width_utility_max"])
            and loss["half_width"] <= float(decision["precision_half_width_worst_loss_max"])
            and loss["high"] <= loss_margin
            and utility["low"] >= utility_margin
        )

    candidates: list[dict[str, Any]] = []
    encoding_results: dict[str, Any] = {}
    for encoding in ENCODINGS:
        minimum = None
        for k in range(1, 29):
            if encoding == "ORDINAL_RANK" and k == 1:
                continue
            if all(k0_pass(encoding, anchor, follow) for anchor in ANCHORS for follow in (k, k + 1, k + 2)):
                minimum = k
                break
        plateau = None
        if minimum is not None:
            for k in range(1, 31):
                if encoding == "ORDINAL_RANK" and k == 1:
                    continue
                equivalent = True
                for follow in range(k, 30):
                    for anchor in ANCHORS:
                        utility = lookup[(encoding, anchor, follow, 30, "pair1_mean_q")]
                        loss = lookup[(encoding, anchor, follow, 30, "pair1_worst_q_loss")]
                        equivalent &= (
                            utility["half_width"] <= float(decision["precision_half_width_utility_max"])
                            and loss["half_width"] <= float(decision["precision_half_width_worst_loss_max"])
                            and utility["low"] >= -float(decision["plateau_utility_equivalence"])
                            and utility["high"] <= float(decision["plateau_utility_equivalence"])
                            and loss["low"] >= -float(decision["plateau_worst_loss_equivalence"])
                            and loss["high"] <= float(decision["plateau_worst_loss_equivalence"])
                        )
                if equivalent and all(k0_pass(encoding, anchor, k) for anchor in ANCHORS):
                    plateau = k
                    break
            for kind, k in (("K_MINIMUM", minimum), ("K_PLATEAU", plateau)):
                if k is not None and not any(row["encoding"] == encoding and row["k"] == k for row in candidates):
                    candidates.append({"encoding": encoding, "k": k, "source": kind})
        encoding_results[encoding] = {"eligible": minimum is not None, "k_minimum": minimum, "k_plateau": plateau}

    candidates.sort(key=lambda row: (int(row["k"]), str(row["encoding"])))
    selection = {
        "schema_version": 1,
        "evidence_id": "REC-EV-022A",
        "status": "STAGE1_SELECTION_COMPLETE" if candidates else "STAGE1_NO_ELIGIBLE_POLICY",
        "users": len(users),
        "simultaneous_critical_value": float(intervals["critical"]),
        "encoding_results": encoding_results,
        "stage2_candidates": candidates,
        "locked_test_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-022A",
        "status": selection["status"],
        "selection": selection,
        "simultaneous_intervals": rows,
        "fallback_rates": metrics.groupby(["encoding", "anchor", "k"], observed=True)["fallback"].mean().reset_index().to_dict("records"),
        "metric_means": metrics.groupby(["encoding", "anchor", "k"], observed=True)[["pair1_mean_q", "pair1_worst_q_loss", "pairwise_concordance"]].mean().reset_index().to_dict("records"),
        "claim_boundary": contract["purpose"],
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    atomic_write_json(output_path(contract, "selection"), selection)
    atomic_write_json(output_path(contract, "result"), result)
    progress_update(contract, "COMPLETE", users=len(users), contrasts=len(metadata), candidates=len(candidates))
    return selection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--phase", choices=("lock", "prepare", "score", "analyze", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    contract_path = args.contract.resolve()
    contract = read_json(contract_path)
    validate_contract(contract)
    if np.__version__ != contract["determinism"]["numpy_version"]:
        raise RuntimeError(f"NumPy version drift: {np.__version__}")
    if args.phase == "lock":
        result = create_or_verify_lock(contract, contract_path, resume=args.resume)
    else:
        if not args.resume:
            raise ResumeError("real Stage1 phases require --resume")
        create_or_verify_lock(contract, contract_path, resume=True)
        if args.phase in {"prepare", "run"}:
            result = prepare(contract)
        if args.phase in {"score", "run"}:
            if not output_path(contract, "cohort_cache").is_file():
                raise ResumeError("score requires prepared cache")
            result = score(contract)
        if args.phase in {"analyze", "run"}:
            if not output_path(contract, "user_metrics").is_file():
                raise ResumeError("analyze requires completed user metrics")
            result = analyze(contract)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
