#!/usr/bin/env python3
"""Run the REC-EV-023B interaction-masked item-disjoint content screen."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import sparse

try:
    from rec_ev_022a_core import (
        RATING_VALUES,
        build_structured_full,
        canonical_decimal,
        encoding_weights,
        old_user_bucket,
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
    from validate_rec_ev_023b_contract import validate_contract
except ModuleNotFoundError:
    from scripts.rec_ev_022a_core import (
        RATING_VALUES,
        build_structured_full,
        canonical_decimal,
        encoding_weights,
        old_user_bucket,
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
    from scripts.validate_rec_ev_023b_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023b-masked-item-cold-content-screen.json"
MAX_USER_ID = 300_000
HEADS = ("STRUCTURED", "E5", "AVAILABLE_HEAD_CONTENT_RRF")
METRIC_HEADS = ("RANDOM_EXPECTATION",) + HEADS
PRIMARY_METRICS = ("top2_mean_q", "top2_worst_q_loss")


class ResumeError(RuntimeError):
    pass


class InputFirewallError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_contract(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_save_npy(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_save_npz(path: Path, **values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **values)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_save_sparse(path: Path, matrix: sparse.spmatrix) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        sparse.save_npz(handle, matrix, compressed=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_to_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def resolve_input(entry: Mapping[str, Any]) -> Path:
    path = Path(str(entry["path"]))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def output_path(contract: Mapping[str, Any], name: str) -> Path:
    return ROOT / str(contract["output_root"]) / str(contract["outputs"][name])


def verify_sources(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    forbidden = {(ROOT / path).resolve() for path in contract["forbidden_input_artifacts"]}
    artifacts: list[dict[str, Any]] = []
    for name, entry in sorted(contract["allowed_input_artifacts"].items()):
        path = resolve_input(entry)
        if path in forbidden:
            raise InputFirewallError(f"forbidden input requested: {name}")
        if not path.is_file() or path.stat().st_size != int(entry["bytes"]):
            raise RuntimeError(f"input missing or byte drift: {name}")
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


def write_integrity(path: Path, artifacts: Mapping[str, Path], *, signature: str, metadata: Mapping[str, Any]) -> None:
    atomic_write_json(path, {
        "schema_version": 1,
        "run_signature": signature,
        "artifacts": {
            name: {
                "path": artifact.relative_to(ROOT).as_posix(),
                "bytes": artifact.stat().st_size,
                "sha256": sha256_file(artifact),
            }
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


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "purpose", "authorization", "adaptive_reuse", "implementation_artifacts", "roles_and_reader",
        "item_split", "cohort", "cells", "train_warm_prior", "q_eval", "features", "heads",
        "scoring", "metrics", "statistics", "decision", "korean_language_descriptive",
        "claim_boundary", "resume", "invariants",
    )
    return {key: contract[key] for key in keys}


def verify_upstream_semantics(contract: Mapping[str, Any]) -> None:
    selection = read_json(resolve_input(contract["allowed_input_artifacts"]["rec_ev_022b_selection"]))
    if selection.get("model_development_candidates") != contract["cells"]:
        raise RuntimeError("REC-EV-022B selected-cell drift")
    registry = read_json(resolve_input(contract["allowed_input_artifacts"]["rec_ev_019c_trial_registry"]))
    trials = registry.get("trials", {}).get("B9_RRF", [])
    if not any(
        trial.get("trial_id") == "B9_RRF-T003"
        and trial.get("parameters") == {"c": 10, "head_set": "ALL_NONBASE"}
        for trial in trials
    ):
        raise RuntimeError("REC-EV-019C RRF c=10 provenance drift")
    artifact_contract = read_json(resolve_input(contract["allowed_input_artifacts"]["rec_ev_019b_contract"]))
    embedding = artifact_contract.get("embedding", {})
    if embedding.get("model_id") != contract["features"]["e5_model_id"] or embedding.get("model_revision") != contract["features"]["e5_revision"]:
        raise RuntimeError("REC-EV-019B E5 provenance drift")


def create_or_verify_lock(contract: Mapping[str, Any], contract_path: Path, *, resume: bool) -> dict[str, Any]:
    lock_path = output_path(contract, "protocol_lock")
    manifest_path = output_path(contract, "source_manifest")
    sources = verify_sources(contract)
    implementations = verify_implementation(contract)
    verify_upstream_semantics(contract)
    expected_hashes = {
        "contract_sha256": sha256_contract(contract_path),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest(),
        "rating_member_opened_at_lock": False,
    }
    expected_manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023B",
        "sources": sources,
        "implementation_artifacts": implementations,
        "adaptive_stage1_and_split_feasibility_seen": True,
        "derived_prior_is_not_a_raw_source": True,
        "rating_member_opened_at_lock": False,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
    }
    if lock_path.is_file():
        if not resume:
            raise ResumeError("existing REC-EV-023B lock requires --resume")
        if not manifest_path.is_file() or read_json(manifest_path) != expected_manifest:
            raise ResumeError("REC-EV-023B source manifest content drift")
        manifest_sha = sha256_file(manifest_path)
        expected_static = {
            "schema_version": 1,
            "evidence_id": "REC-EV-023B",
            "status": "LOCKED_ADAPTIVE_DEVELOPMENT_PROTOCOL",
            **expected_hashes,
            "source_manifest_sha256": manifest_sha,
            "adaptive_stage1_reuse": True,
            "locked_test_opened": False,
            "stage2_opened": False,
            "final_reserve_opened": False,
            "champion": None,
            "product_policy_updated": False,
        }
        lock = read_json(lock_path)
        if set(lock) != set(expected_static) | {"created_at_utc"} or not isinstance(lock.get("created_at_utc"), str):
            raise ResumeError("REC-EV-023B lock schema drift")
        for key, value in expected_static.items():
            if lock.get(key) != value:
                raise ResumeError(f"REC-EV-023B lock mismatch: {key}")
        return lock
    if resume:
        raise ResumeError("create the first REC-EV-023B lock without --resume")
    atomic_write_json(manifest_path, expected_manifest)
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023B",
        "status": "LOCKED_ADAPTIVE_DEVELOPMENT_PROTOCOL",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        **expected_hashes,
        "source_manifest_sha256": sha256_file(manifest_path),
        "adaptive_stage1_reuse": True,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "champion": None,
        "product_policy_updated": False,
    }
    atomic_write_json(lock_path, lock)
    return lock


def run_signature(contract: Mapping[str, Any]) -> str:
    lock = read_json(output_path(contract, "protocol_lock"))
    payload = {key: lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def progress_update(contract: Mapping[str, Any], phase: str, **extra: Any) -> None:
    path = output_path(contract, "progress")
    value = read_json(path) if path.is_file() else {"schema_version": 1, "evidence_id": "REC-EV-023B"}
    value.update({"phase": phase, "updated_at_utc": datetime.now(timezone.utc).isoformat(), **extra})
    atomic_write_json(path, value)


def item_bucket(salt: str, movie_id: int) -> int:
    payload = f"{salt}|{canonical_decimal(movie_id)}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big", signed=False) % 10_000


def order_digest(salt: str, anonymous_user_key: str, movie_id: int) -> bytes:
    if len(anonymous_user_key) != 64 or anonymous_user_key.lower() != anonymous_user_key:
        raise ValueError("user key must be lowercase SHA-256")
    payload = f"{salt}|{anonymous_user_key}|{canonical_decimal(movie_id)}".encode("utf-8")
    return hashlib.sha256(payload).digest()


def _role_lookups() -> tuple[np.ndarray, np.ndarray]:
    old_allowed = np.zeros(MAX_USER_ID + 1, dtype=bool)
    new_bucket = np.zeros(MAX_USER_ID + 1, dtype=np.uint16)
    for identifier in range(1, MAX_USER_ID + 1):
        old_allowed[identifier] = old_user_bucket(identifier) <= 59
        new_bucket[identifier] = user_role_bucket(identifier)
    return old_allowed, new_bucket


def _rating_index_from_bytes(value: bytes) -> int:
    return int(rating_indices(np.asarray([float(value)], dtype=np.float64))[0])


def _movie_position_lookup(item_ids: np.ndarray) -> np.ndarray:
    maximum = int(item_ids.max(initial=0))
    lookup = np.full(maximum + 1, -1, dtype=np.int32)
    lookup[item_ids] = np.arange(len(item_ids), dtype=np.int32)
    return lookup


def _read_universe(contract: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray, sparse.csr_matrix, np.ndarray]:
    candidate_table = pq.read_table(
        resolve_input(contract["allowed_input_artifacts"]["candidate_identity"]), columns=["movie_id"],
    )
    candidate_ids = candidate_table.column("movie_id").combine_chunks().to_numpy(zero_copy_only=False).astype(np.int64)
    if len(np.unique(candidate_ids)) != len(candidate_ids):
        raise RuntimeError("candidate movie identity duplicate")
    structured_frame = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["structured_features"]))
    structured_ids = structured_frame.loc[structured_frame["feature_eligible"], "movie_id"].to_numpy(dtype=np.int64)
    text_table = pq.read_table(
        resolve_input(contract["allowed_input_artifacts"]["text_embeddings"]),
        columns=["movie_id", "model_revision", "embedding", "feature_eligible"],
    )
    text_ids = text_table.column("movie_id").combine_chunks().to_numpy(zero_copy_only=False).astype(np.int64)
    if len(np.unique(text_ids)) != len(text_ids):
        raise RuntimeError("text embedding movie identity duplicate")
    revisions = set(text_table.column("model_revision").combine_chunks().to_pylist())
    if revisions != {contract["features"]["e5_revision"]}:
        raise RuntimeError("E5 revision column drift")
    text_eligible = text_table.column("feature_eligible").combine_chunks().to_numpy(zero_copy_only=False).astype(bool)
    embedding_values = text_table.column("embedding").combine_chunks().values.to_numpy(zero_copy_only=False)
    text_vectors = np.asarray(embedding_values, dtype=np.float32).reshape(len(text_ids), int(contract["features"]["e5_dimension"]))
    text_norms = np.linalg.norm(text_vectors.astype(np.float64), axis=1)
    text_ok = text_eligible & np.isfinite(text_vectors).all(axis=1) & np.isfinite(text_norms)
    text_ok &= np.abs(text_norms - 1.0) <= float(contract["features"]["e5_norm_tolerance"])
    item_ids = np.intersect1d(np.intersect1d(candidate_ids, structured_ids), text_ids[text_ok]).astype(np.int64)
    structured_full = build_structured_full(structured_frame, item_ids)
    structured_nonzero = np.asarray(structured_full.getnnz(axis=1)).ravel() > 0
    if not bool(structured_nonzero.all()):
        item_ids = item_ids[structured_nonzero]
        structured_full = structured_full[structured_nonzero].tocsr()
    text_lookup = {int(movie): index for index, movie in enumerate(text_ids.tolist())}
    e5 = np.vstack([text_vectors[text_lookup[int(movie)]] for movie in item_ids]).astype(np.float32)
    norms = np.linalg.norm(e5.astype(np.float64), axis=1)
    if not np.isfinite(e5).all() or float(np.max(np.abs(norms - 1.0), initial=0.0)) > float(contract["features"]["e5_norm_tolerance"]):
        raise RuntimeError("aligned E5 coverage or norm drift")
    indexed = structured_frame.set_index("movie_id", verify_integrity=True).reindex(item_ids)
    original_language_ko = indexed["original_language"].fillna("").astype(str).eq("ko").to_numpy(dtype=bool)
    warm = np.asarray([
        item_bucket(str(contract["item_split"]["salt"]), int(movie)) >= 2000 for movie in item_ids
    ], dtype=bool)
    expected = contract["item_split"]
    if len(item_ids) != int(expected["expected_items"]) or int(warm.sum()) != int(expected["expected_warm"]) or int((~warm).sum()) != int(expected["expected_masked_cold"]):
        raise RuntimeError("observed pseudo-cold item split drift")
    return item_ids, warm, original_language_ko, structured_full, e5


def _first_pass(
    archive: Path,
    member: str,
    position_lookup: np.ndarray,
    warm: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, int]]:
    old_allowed, role_buckets = _role_lookups()
    train_hist = np.zeros((MAX_USER_ID + 1, len(RATING_VALUES)), dtype=np.uint32)
    stage1_hist = np.zeros_like(train_hist)
    stage1_warm_count = np.zeros(MAX_USER_ID + 1, dtype=np.uint32)
    stage1_cold_count = np.zeros(MAX_USER_ID + 1, dtype=np.uint32)
    counters = {
        "raw_rows": 0,
        "excluded_after_user_id": 0,
        "train_warm_rating_parsed": 0,
        "train_masked_cold_rating_parsed": 0,
        "stage1_rating_parsed": 0,
        "timestamp_parsed": 0,
    }
    with zipfile.ZipFile(archive) as bundle:
        with bundle.open(member) as handle:
            if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
                raise RuntimeError("MovieLens rating header drift")
            for raw_line in handle:
                counters["raw_rows"] += 1
                first = raw_line.find(b",")
                if first <= 0:
                    raise RuntimeError("MovieLens row user delimiter drift")
                raw_user = int(raw_line[:first])
                if raw_user <= 0 or raw_user > MAX_USER_ID or not old_allowed[raw_user] or role_buckets[raw_user] >= 8000:
                    counters["excluded_after_user_id"] += 1
                    continue
                bucket = int(role_buckets[raw_user])
                rest = raw_line[first + 1 :]
                second = rest.find(b",")
                if second <= 0:
                    raise RuntimeError("MovieLens row movie delimiter drift")
                raw_movie = int(rest[:second])
                position = int(position_lookup[raw_movie]) if 0 <= raw_movie < len(position_lookup) else -1
                if bucket < 6000 and (position < 0 or not bool(warm[position])):
                    continue
                if bucket >= 6000 and position >= 0:
                    if bool(warm[position]):
                        stage1_warm_count[raw_user] += 1
                    else:
                        stage1_cold_count[raw_user] += 1
                rating_and_timestamp = rest[second + 1 :]
                third = rating_and_timestamp.find(b",")
                if third <= 0:
                    raise RuntimeError("MovieLens row rating delimiter drift")
                rating_index = _rating_index_from_bytes(rating_and_timestamp[:third])
                if bucket < 6000:
                    train_hist[raw_user, rating_index] += 1
                    counters["train_warm_rating_parsed"] += 1
                else:
                    stage1_hist[raw_user, rating_index] += 1
                    counters["stage1_rating_parsed"] += 1
    if counters["train_masked_cold_rating_parsed"] != 0 or counters["timestamp_parsed"] != 0:
        raise RuntimeError("raw parser firewall counter drift")
    return train_hist, stage1_hist, stage1_warm_count, stage1_cold_count, counters


def _push_smallest(
    heap: list[tuple[int, int, int, int]],
    *,
    limit: int,
    digest_value: int,
    movie_id: int,
    position: int,
) -> tuple[bool, int | None]:
    entry = (-digest_value, -movie_id, movie_id, position)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
        return True, None
    if (digest_value, movie_id) < (-heap[0][0], -heap[0][1]):
        evicted = heapq.heapreplace(heap, entry)
        return True, int(evicted[2])
    return False, None


def _heap_records(heap: Sequence[tuple[int, int, int, int]]) -> list[tuple[int, int]]:
    values = [(-entry[0], entry[2], entry[3]) for entry in heap]
    values.sort(key=lambda row: (row[0], row[1]))
    return [(movie, position) for _, movie, position in values]


def _second_pass(
    contract: Mapping[str, Any],
    archive: Path,
    member: str,
    eligible_users: np.ndarray,
    stage1_hist: np.ndarray,
    item_ids: np.ndarray,
    warm: np.ndarray,
    original_language_ko: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    eligible = np.zeros(MAX_USER_ID + 1, dtype=bool)
    eligible[eligible_users] = True
    position_lookup = _movie_position_lookup(item_ids)
    anonymous = {int(uid): user_key(int(uid)) for uid in eligible_users.tolist()}
    profile_heaps = {int(uid): [] for uid in eligible_users.tolist()}
    target_heaps = {int(uid): [] for uid in eligible_users.tolist()}
    profile_selected_ratings = {int(uid): {} for uid in eligible_users.tolist()}
    target_selected_ratings = {int(uid): {} for uid in eligible_users.tolist()}
    korean_movies = {int(uid): [] for uid in eligible_users.tolist()}
    korean_ratings = {int(uid): [] for uid in eligible_users.tolist()}
    counters = {"rating_parsed": 0, "timestamp_parsed": 0, "selection_rating_argument_count": 0}
    profile_salt = str(contract["cohort"]["profile_order_salt"])
    target_salt = str(contract["cohort"]["target_order_salt"])
    with zipfile.ZipFile(archive) as bundle:
        with bundle.open(member) as handle:
            if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
                raise RuntimeError("MovieLens rating header drift")
            for raw_line in handle:
                first = raw_line.find(b",")
                if first <= 0:
                    raise RuntimeError("MovieLens row user delimiter drift")
                raw_user = int(raw_line[:first])
                if raw_user <= 0 or raw_user > MAX_USER_ID or not eligible[raw_user]:
                    continue
                rest = raw_line[first + 1 :]
                second = rest.find(b",")
                if second <= 0:
                    raise RuntimeError("MovieLens row movie delimiter drift")
                raw_movie = int(rest[:second])
                position = int(position_lookup[raw_movie]) if 0 <= raw_movie < len(position_lookup) else -1
                if position < 0:
                    continue
                key = anonymous[raw_user]
                if bool(warm[position]):
                    digest_value = int.from_bytes(order_digest(profile_salt, key, raw_movie), "big", signed=False)
                    selected, evicted = _push_smallest(
                        profile_heaps[raw_user], limit=14, digest_value=digest_value,
                        movie_id=raw_movie, position=position,
                    )
                    needs_rating = selected
                else:
                    digest_value = int.from_bytes(order_digest(target_salt, key, raw_movie), "big", signed=False)
                    selected, evicted = _push_smallest(
                        target_heaps[raw_user], limit=20, digest_value=digest_value,
                        movie_id=raw_movie, position=position,
                    )
                    needs_rating = selected or bool(original_language_ko[position])
                if not needs_rating:
                    continue
                rating_and_timestamp = rest[second + 1 :]
                third = rating_and_timestamp.find(b",")
                if third <= 0:
                    raise RuntimeError("MovieLens row rating delimiter drift")
                rating_index = _rating_index_from_bytes(rating_and_timestamp[:third])
                counters["rating_parsed"] += 1
                if bool(warm[position]) and selected:
                    if evicted is not None:
                        profile_selected_ratings[raw_user].pop(evicted, None)
                    profile_selected_ratings[raw_user][raw_movie] = rating_index
                if not bool(warm[position]) and selected:
                    if evicted is not None:
                        target_selected_ratings[raw_user].pop(evicted, None)
                    target_selected_ratings[raw_user][raw_movie] = rating_index
                if not bool(warm[position]):
                    if bool(original_language_ko[position]):
                        korean_movies[raw_user].append(raw_movie)
                        korean_ratings[raw_user].append(rating_index)
    if counters["timestamp_parsed"] != 0 or counters["selection_rating_argument_count"] != 0:
        raise RuntimeError("selection or timestamp firewall counter drift")
    score_rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    for raw_user in eligible_users.tolist():
        profile_selected = _heap_records(profile_heaps[int(raw_user)])
        target_selected = _heap_records(target_heaps[int(raw_user)])
        profile = [
            (movie, profile_selected_ratings[int(raw_user)][movie], position)
            for movie, position in profile_selected
        ]
        target = [
            (movie, target_selected_ratings[int(raw_user)][movie], position)
            for movie, position in target_selected
        ]
        if len(profile) != 14 or len(target) != 20:
            raise RuntimeError("eligible user deterministic slate completeness drift")
        key = anonymous[int(raw_user)]
        score_rows.append({
            "user_key": key,
            "profile_movie_ids": [row[0] for row in profile],
            "profile_rating_idx": [row[1] for row in profile],
            "target_movie_ids": [row[0] for row in target],
        })
        label_rows.append({
            "user_key": key,
            "target_movie_ids": [row[0] for row in target],
            "target_rating_idx": [row[1] for row in target],
            "full_rating_hist": stage1_hist[int(raw_user)].astype(np.uint32).tolist(),
            "korean_cold_movie_ids": korean_movies[int(raw_user)],
            "korean_cold_rating_idx": korean_ratings[int(raw_user)],
        })
    score_input = pd.DataFrame(score_rows).sort_values("user_key", kind="stable", ignore_index=True)
    label_source = pd.DataFrame(label_rows).sort_values("user_key", kind="stable", ignore_index=True)
    return score_input, label_source, counters


def _score_prepared_artifacts(contract: Mapping[str, Any]) -> dict[str, Path]:
    return {
        "universe": output_path(contract, "universe"),
        "train_prior": output_path(contract, "train_prior"),
        "structured_full": output_path(contract, "structured_full"),
        "e5_aligned": output_path(contract, "e5_aligned"),
        "score_input": output_path(contract, "score_input"),
    }


def prepare(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    score_artifacts = _score_prepared_artifacts(contract)
    score_integrity_path = output_path(contract, "score_prepared_integrity")
    label_source_path = output_path(contract, "label_source")
    label_integrity_path = output_path(contract, "label_source_integrity")
    existing = [score_integrity_path, label_source_path, label_integrity_path, *score_artifacts.values()]
    if any(path.exists() for path in existing):
        if not all(path.exists() for path in existing):
            raise ResumeError("partial REC-EV-023B prepared state")
        score_integrity = verify_integrity(score_integrity_path, score_artifacts, signature=signature)
        verify_integrity(label_integrity_path, {"label_source": label_source_path}, signature=signature)
        return {"status": "REUSED_PREPARED_MASKED_COLD", **score_integrity["metadata"]}

    started = time.monotonic()
    progress_update(contract, "PREPARE_UNIVERSE")
    item_ids, warm, original_language_ko, structured_full, e5 = _read_universe(contract)
    position_lookup = _movie_position_lookup(item_ids)
    archive_entry = contract["allowed_input_artifacts"]["movielens_archive"]
    archive = resolve_input(archive_entry)
    member = str(archive_entry["member"])
    progress_update(contract, "PREPARE_RATINGS_PASS1", items=len(item_ids))
    train_hist, stage1_hist, warm_counts, cold_counts, first_counters = _first_pass(
        archive, member, position_lookup, warm,
    )
    eligible_users = np.flatnonzero(
        (warm_counts >= int(contract["cohort"]["minimum_warm_eligible_ratings"]))
        & (cold_counts >= int(contract["cohort"]["minimum_masked_cold_eligible_ratings"]))
    ).astype(np.int32)
    if len(eligible_users) != int(contract["cohort"]["expected_users"]):
        raise RuntimeError(f"observed Stage1 pseudo-cold cohort drift: {len(eligible_users)}")
    valid_train = train_hist.sum(axis=1) > 0
    pi0, g0_mid = user_equal_prior(train_hist[valid_train])
    train_users = int(valid_train.sum())
    train_interactions = int(train_hist.sum())
    progress_update(contract, "PREPARE_RATINGS_PASS2", eligible_users=len(eligible_users))
    score_input, label_source, second_counters = _second_pass(
        contract, archive, member, eligible_users, stage1_hist, item_ids, warm, original_language_ko,
    )
    expected_score_columns = {"user_key", "profile_movie_ids", "profile_rating_idx", "target_movie_ids"}
    expected_label_columns = {
        "user_key", "target_movie_ids", "target_rating_idx", "full_rating_hist",
        "korean_cold_movie_ids", "korean_cold_rating_idx",
    }
    if set(score_input.columns) != expected_score_columns or set(label_source.columns) != expected_label_columns:
        raise RuntimeError("prepared artifact schema drift")
    if any(token in "|".join(score_input.columns).lower() for token in ("target_rating", "target_q", "timestamp")):
        raise RuntimeError("score input label leakage")
    zero_users: dict[str, int] = {}
    for cell in contract["cells"]:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        count = 0
        for indices in score_input["profile_rating_idx"]:
            ratings = RATING_VALUES[np.asarray(indices[:k], dtype=np.int8)]
            count += int(float(np.abs(encoding_weights(encoding, ratings, g0_mid, tau=5.0)).sum()) == 0.0)
        zero_users[f"{encoding}|{k}"] = count
    atomic_save_npz(
        score_artifacts["universe"],
        item_ids=item_ids.astype(np.int32),
        warm_mask=warm.astype(bool),
        original_language_ko=original_language_ko.astype(bool),
    )
    atomic_save_npz(
        score_artifacts["train_prior"],
        pi0=pi0.astype(np.float64),
        g0_mid=g0_mid.astype(np.float64),
        train_users=np.asarray([train_users], dtype=np.int64),
        train_interactions=np.asarray([train_interactions], dtype=np.int64),
    )
    atomic_save_sparse(score_artifacts["structured_full"], structured_full)
    atomic_save_npy(score_artifacts["e5_aligned"], e5)
    atomic_to_parquet(score_artifacts["score_input"], score_input)
    atomic_to_parquet(label_source_path, label_source)
    metadata = {
        "items": len(item_ids),
        "warm_items": int(warm.sum()),
        "masked_cold_items": int((~warm).sum()),
        "users": len(score_input),
        "train_warm_users": train_users,
        "train_warm_interactions": train_interactions,
        "pi0": pi0.tolist(),
        "g0_mid": g0_mid.tolist(),
        "parser_pass1": first_counters,
        "parser_pass2": second_counters,
        "score_input_columns": list(score_input.columns),
        "zero_weight_users_by_cell": zero_users,
        "structured_rows": int(structured_full.shape[0]),
        "structured_columns": int(structured_full.shape[1]),
        "e5_shape": list(e5.shape),
        "e5_finite": bool(np.isfinite(e5).all()),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    label_metadata = {
        "users": len(label_source),
        "columns": list(label_source.columns),
        "target_ratings": int(sum(len(row) for row in label_source["target_rating_idx"])),
        "created_before_score_but_not_allowlisted_by_scorer": True,
    }
    write_integrity(score_integrity_path, score_artifacts, signature=signature, metadata=metadata)
    write_integrity(label_integrity_path, {"label_source": label_source_path}, signature=signature, metadata=label_metadata)
    progress_update(contract, "PREPARED", **{key: metadata[key] for key in ("items", "users", "train_warm_users", "train_warm_interactions")})
    return {"status": "PREPARED_MASKED_COLD", **metadata}


def _active_personal_scores(similarity: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, bool]:
    scores, denominator_fallback = score_judged_targets(similarity, weights)
    active = (
        not denominator_fallback
        and bool(np.isfinite(scores).all())
        and int(np.unique(scores).size) >= 2
    )
    return scores, active


def _head_tie_digest(
    contract: Mapping[str, Any], head: str, encoding: str, k: int, user: str, movie_id: int,
) -> bytes:
    payload = (
        f"{contract['scoring']['head_tie_prefix']}{head}|{encoding}|{canonical_decimal(k)}|"
        f"{user}|{canonical_decimal(movie_id)}"
    ).encode("utf-8")
    return hashlib.sha256(payload).digest()


def _rrf_final_tie_digest(
    contract: Mapping[str, Any], encoding: str, k: int, user: str, movie_id: int,
) -> bytes:
    payload = (
        f"{contract['scoring']['rrf_final_tie_prefix']}{encoding}|{canonical_decimal(k)}|"
        f"{user}|{canonical_decimal(movie_id)}"
    ).encode("utf-8")
    return hashlib.sha256(payload).digest()


def strict_head_order(
    contract: Mapping[str, Any], movie_ids: Sequence[int], scores: Sequence[float],
    *, user: str, head: str, encoding: str, k: int,
) -> np.ndarray:
    movies = np.asarray(movie_ids, dtype=np.int64)
    values = np.asarray(scores, dtype=np.float64)
    if movies.shape != values.shape or not np.isfinite(values).all():
        raise ValueError("finite aligned target score arrays required")
    order = sorted(
        range(len(movies)),
        key=lambda index: (
            -float(values[index]),
            _head_tie_digest(contract, head, encoding, k, user, int(movies[index])),
            int(movies[index]),
        ),
    )
    if sorted(order) != list(range(len(movies))):
        raise RuntimeError("head order is not an exact permutation")
    return np.asarray(order, dtype=np.int32)


def strict_rrf_final_order(
    contract: Mapping[str, Any], movie_ids: Sequence[int], scores: Sequence[float],
    *, user: str, encoding: str, k: int,
) -> np.ndarray:
    movies = np.asarray(movie_ids, dtype=np.int64)
    values = np.asarray(scores, dtype=np.float64)
    if movies.shape != values.shape or not np.isfinite(values).all():
        raise ValueError("finite aligned RRF score arrays required")
    order = sorted(
        range(len(movies)),
        key=lambda index: (
            -float(values[index]),
            _rrf_final_tie_digest(contract, encoding, k, user, int(movies[index])),
            int(movies[index]),
        ),
    )
    if sorted(order) != list(range(len(movies))):
        raise RuntimeError("RRF order is not an exact permutation")
    return np.asarray(order, dtype=np.int32)


def _one_based_ranks(order: Sequence[int]) -> np.ndarray:
    permutation = np.asarray(order, dtype=np.int64)
    if sorted(permutation.tolist()) != list(range(len(permutation))):
        raise RuntimeError("rank order is not an exact permutation")
    ranks = np.empty(len(permutation), dtype=np.int32)
    ranks[permutation] = np.arange(1, len(permutation) + 1, dtype=np.int32)
    return ranks


def _part_path(root: Path, start: int, stop: int) -> Path:
    return root / f"part-{start:06d}-{stop:06d}.parquet"


def score(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    score_artifacts = _score_prepared_artifacts(contract)
    prepared = verify_integrity(
        output_path(contract, "score_prepared_integrity"), score_artifacts, signature=signature,
    )
    score_input = pd.read_parquet(score_artifacts["score_input"]).sort_values("user_key", kind="stable", ignore_index=True)
    expected_columns = {"user_key", "profile_movie_ids", "profile_rating_idx", "target_movie_ids"}
    if set(score_input.columns) != expected_columns:
        raise ResumeError("score input schema drift before scorer")
    if output_path(contract, "label_source") in score_artifacts.values():
        raise RuntimeError("label source is allowlisted by scorer")
    universe = np.load(score_artifacts["universe"], allow_pickle=False)
    item_ids = universe["item_ids"].astype(np.int64)
    position_lookup = _movie_position_lookup(item_ids)
    prior = np.load(score_artifacts["train_prior"], allow_pickle=False)
    g0_mid = prior["g0_mid"].astype(np.float64)
    structured = sparse.load_npz(score_artifacts["structured_full"]).tocsr()
    e5 = np.load(score_artifacts["e5_aligned"], allow_pickle=False)
    if len(score_input) != int(contract["cohort"]["expected_users"]) or prepared["metadata"].get("users") != len(score_input):
        raise ResumeError("prepared scorer user count drift")
    if structured.shape[0] != len(item_ids) or e5.shape != (len(item_ids), int(contract["features"]["e5_dimension"])):
        raise ResumeError("prepared feature alignment drift")

    parts_root = output_path(contract, "rank_parts")
    parts_root.mkdir(parents=True, exist_ok=True)
    batch_size = int(contract["resume"]["checkpoint_after_users"])
    ranges = [(start, min(len(score_input), start + batch_size)) for start in range(0, len(score_input), batch_size)]
    expected_parts = {_part_path(parts_root, start, stop) for start, stop in ranges}
    expected_integrities = {path.with_suffix(".integrity.json") for path in expected_parts}
    combined_path = output_path(contract, "score_rank")
    combined_integrity = output_path(contract, "score_rank_integrity")
    if combined_path.exists() != combined_integrity.exists():
        raise ResumeError("partial combined score-rank state")
    combined_preexisting = combined_path.exists()
    expected_rows_per_user = len(contract["cells"]) * len(HEADS)
    started = time.monotonic()
    for start, stop in ranges:
        destination = _part_path(parts_root, start, stop)
        integrity_path = destination.with_suffix(".integrity.json")
        expected_keys = score_input.iloc[start:stop]["user_key"].tolist()
        expected_metadata = {
            "start": start,
            "stop": stop,
            "rows": (stop - start) * expected_rows_per_user,
            "user_keys": expected_keys,
            "target_rating_or_q_columns": 0,
        }
        if destination.exists() or integrity_path.exists():
            integrity = verify_integrity(integrity_path, {"rank_part": destination}, signature=signature)
            actual = pd.read_parquet(destination, columns=["user_key"])
            if integrity.get("metadata") != expected_metadata or actual["user_key"].drop_duplicates().tolist() != expected_keys:
                raise ResumeError(f"rank part slice drift: {destination.name}")
            continue
        rows: list[dict[str, Any]] = []
        for user in score_input.iloc[start:stop].itertuples(index=False):
            profile_movies = np.asarray(user.profile_movie_ids, dtype=np.int64)
            target_movies = np.asarray(user.target_movie_ids, dtype=np.int64)
            if len(profile_movies) != 14 or len(target_movies) != 20:
                raise RuntimeError("score input slate length drift")
            profile_positions = position_lookup[profile_movies]
            target_positions = position_lookup[target_movies]
            if bool((profile_positions < 0).any()) or bool((target_positions < 0).any()):
                raise RuntimeError("score input movie outside prepared universe")
            ratings = RATING_VALUES[np.asarray(user.profile_rating_idx, dtype=np.int8)]
            similarities = {
                "STRUCTURED": structured_pair_similarity(
                    structured, profile_positions[:14], target_positions,
                ),
                "E5": e5[target_positions].astype(np.float64) @ e5[profile_positions[:14]].astype(np.float64).T,
            }
            for cell in contract["cells"]:
                encoding, k = str(cell["encoding"]), int(cell["k"])
                weights = encoding_weights(encoding, ratings[:k], g0_mid, tau=5.0)
                standalone: dict[str, dict[str, Any]] = {}
                for head in ("STRUCTURED", "E5"):
                    personal, active = _active_personal_scores(similarities[head][:, :k], weights)
                    order = (
                        strict_head_order(
                            contract, target_movies, personal, user=user.user_key,
                            head=head, encoding=encoding, k=k,
                        )
                        if active else np.empty(0, dtype=np.int32)
                    )
                    standalone[head] = {"scores": personal, "active": active, "order": order}
                    rows.append({
                        "user_key": user.user_key,
                        "encoding": encoding,
                        "k": k,
                        "head": head,
                        "head_active": bool(active),
                        "active_composition": head if active else "",
                        "ranked_target_indices": order.tolist(),
                        "target_scores": personal.astype(np.float64).tolist() if active else [],
                        "fallback": "NONE" if active else "RANDOM_EXPECTATION_ANALYTIC",
                    })
                rrf_scores = np.zeros(20, dtype=np.float64)
                composition: list[str] = []
                for head in ("STRUCTURED", "E5"):
                    if not standalone[head]["active"]:
                        continue
                    rrf_scores += 1.0 / (
                        float(contract["scoring"]["rrf_c"])
                        + _one_based_ranks(standalone[head]["order"]).astype(np.float64)
                    )
                    composition.append(head)
                rrf_active = bool(composition) and int(np.unique(rrf_scores).size) >= 2
                rrf_order = (
                    strict_rrf_final_order(
                        contract, target_movies, rrf_scores, user=user.user_key, encoding=encoding, k=k,
                    )
                    if rrf_active else np.empty(0, dtype=np.int32)
                )
                rows.append({
                    "user_key": user.user_key,
                    "encoding": encoding,
                    "k": k,
                    "head": "AVAILABLE_HEAD_CONTENT_RRF",
                    "head_active": bool(rrf_active),
                    "active_composition": "|".join(composition),
                    "ranked_target_indices": rrf_order.tolist(),
                    "target_scores": rrf_scores.tolist() if rrf_active else [],
                    "fallback": "NONE" if rrf_active else "RANDOM_EXPECTATION_ANALYTIC",
                })
        frame = pd.DataFrame(rows)
        if any(token in "|".join(frame.columns).lower() for token in ("rating", "q_eval", "timestamp", "movie_id")):
            raise RuntimeError("score-rank schema contains label, timestamp, or movie ID")
        atomic_to_parquet(destination, frame)
        write_integrity(integrity_path, {"rank_part": destination}, signature=signature, metadata=expected_metadata)
        progress_update(
            contract, "SCORING", completed_users=stop, total_users=len(score_input),
            elapsed_seconds=round(time.monotonic() - started, 3),
        )

    actual_parts = set(parts_root.glob("part-*.parquet"))
    actual_integrities = set(parts_root.glob("part-*.integrity.json"))
    if actual_parts != expected_parts or actual_integrities != expected_integrities:
        raise ResumeError("rank part path set drift")
    combined = pd.concat((pd.read_parquet(path) for path in sorted(actual_parts)), ignore_index=True)
    expected_rows = len(score_input) * expected_rows_per_user
    if len(combined) != expected_rows or combined.duplicated(["user_key", "encoding", "k", "head"]).any():
        raise RuntimeError("combined score-rank completeness drift")
    if combined_preexisting:
        integrity = verify_integrity(combined_integrity, {"score_rank": combined_path}, signature=signature)
        if integrity.get("metadata") != {
            "rows": expected_rows, "users": len(score_input), "target_rating_or_q_columns": 0,
        }:
            raise ResumeError("combined score-rank metadata drift")
        return {"status": "REUSED_SCORE_RANK", "users": len(score_input), "rows": expected_rows}
    atomic_to_parquet(combined_path, combined)
    write_integrity(
        combined_integrity, {"score_rank": combined_path}, signature=signature,
        metadata={"rows": expected_rows, "users": len(score_input), "target_rating_or_q_columns": 0},
    )
    progress_update(contract, "SCORED", users=len(score_input), rows=expected_rows)
    return {"status": "SCORED_MASKED_COLD", "users": len(score_input), "rows": expected_rows}


def _q_from_hist(rating_indices_value: Sequence[int], full_hist_value: Sequence[int]) -> np.ndarray:
    indices = np.asarray(rating_indices_value, dtype=np.int8)
    hist = np.asarray(full_hist_value, dtype=np.float64)
    if hist.shape != (10,) or hist.sum() <= 0 or bool((indices < 0).any()) or bool((indices >= 10).any()):
        raise ValueError("invalid full-history q inputs")
    below = np.cumsum(hist) - hist
    return (below[indices] + 0.5 * hist[indices]) / hist.sum()


def analytic_random_top2(q_eval: Sequence[float]) -> tuple[float, float]:
    q = np.asarray(q_eval, dtype=np.float64)
    if len(q) < 2 or not np.isfinite(q).all():
        raise ValueError("at least two finite q labels required")
    utility = float(q.mean())
    pair_losses = 1.0 - np.minimum(q[:, None], q[None, :])
    loss = float(pair_losses[np.triu_indices(len(q), k=1)].mean())
    return utility, loss


def materialize_metrics(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    score_rank_path = output_path(contract, "score_rank")
    score_rank_integrity = verify_integrity(
        output_path(contract, "score_rank_integrity"), {"score_rank": score_rank_path}, signature=signature,
    )
    if score_rank_integrity["metadata"].get("users") != int(contract["cohort"]["expected_users"]):
        raise ResumeError("score-rank incomplete before label join")
    label_source_path = output_path(contract, "label_source")
    verify_integrity(
        output_path(contract, "label_source_integrity"), {"label_source": label_source_path}, signature=signature,
    )
    score_input = pd.read_parquet(output_path(contract, "score_input")).sort_values("user_key", kind="stable", ignore_index=True)
    label_source = pd.read_parquet(label_source_path).sort_values("user_key", kind="stable", ignore_index=True)
    if score_input["user_key"].tolist() != label_source["user_key"].tolist():
        raise RuntimeError("score and label user alignment drift")
    if not all(
        list(score_targets) == list(label_targets)
        for score_targets, label_targets in zip(score_input["target_movie_ids"], label_source["target_movie_ids"], strict=True)
    ):
        raise RuntimeError("score and label target alignment drift")

    labels_path = output_path(contract, "evaluation_labels")
    labels_integrity_path = output_path(contract, "evaluation_labels_integrity")
    if labels_path.exists() != labels_integrity_path.exists():
        raise ResumeError("partial evaluation-label state")
    if labels_path.exists():
        verify_integrity(labels_integrity_path, {"evaluation_labels": labels_path}, signature=signature)
        labels = pd.read_parquet(labels_path)
    else:
        label_rows = []
        for row in label_source.itertuples(index=False):
            q_eval = _q_from_hist(row.target_rating_idx, row.full_rating_hist)
            label_rows.append({"user_key": row.user_key, "target_q_eval": q_eval.astype(np.float64).tolist()})
        labels = pd.DataFrame(label_rows).sort_values("user_key", kind="stable", ignore_index=True)
        atomic_to_parquet(labels_path, labels)
        write_integrity(
            labels_integrity_path, {"evaluation_labels": labels_path}, signature=signature,
            metadata={
                "users": len(labels), "target_labels": int(sum(len(row) for row in labels["target_q_eval"])),
                "created_after_complete_score_rank_integrity": True,
            },
        )
    if set(labels.columns) != {"user_key", "target_q_eval"} or len(labels) != len(score_input):
        raise RuntimeError("evaluation label schema or row drift")

    metrics_path = output_path(contract, "user_metrics")
    metrics_integrity_path = output_path(contract, "user_metrics_integrity")
    if metrics_path.exists() != metrics_integrity_path.exists():
        raise ResumeError("partial user metric state")
    if metrics_path.exists():
        integrity = verify_integrity(metrics_integrity_path, {"user_metrics": metrics_path}, signature=signature)
        return {"status": "REUSED_USER_METRICS", **integrity["metadata"]}

    ranks = pd.read_parquet(score_rank_path).sort_values(["user_key", "encoding", "k", "head"], kind="stable", ignore_index=True)
    q_by_user = {row.user_key: np.asarray(row.target_q_eval, dtype=np.float64) for row in labels.itertuples(index=False)}
    rows: list[dict[str, Any]] = []
    for (user, encoding, k), group in ranks.groupby(["user_key", "encoding", "k"], sort=True, observed=True):
        q_eval = q_by_user[str(user)]
        random_utility, random_loss = analytic_random_top2(q_eval)
        rows.append({
            "user_key": user,
            "encoding": encoding,
            "k": int(k),
            "head": "RANDOM_EXPECTATION",
            "top2_mean_q": random_utility,
            "top2_worst_q_loss": random_loss,
            "pairwise_concordance": np.nan,
            "head_active": False,
            "used_random_expectation": True,
            "active_composition": "ANALYTIC",
        })
        if set(group["head"]) != set(HEADS):
            raise RuntimeError("score-rank head Cartesian drift")
        for rank_row in group.itertuples(index=False):
            active = bool(rank_row.head_active)
            if active:
                order = np.asarray(rank_row.ranked_target_indices, dtype=np.int32)
                scores = np.asarray(rank_row.target_scores, dtype=np.float64)
                if sorted(order.tolist()) != list(range(20)) or scores.shape != (20,):
                    raise RuntimeError("active rank row shape drift")
                utility, loss = pair1_metrics(q_eval[order])
                concordance = pairwise_concordance(scores, q_eval)
            else:
                if len(rank_row.ranked_target_indices) or len(rank_row.target_scores):
                    raise RuntimeError("inactive rank row must not contain order or scores")
                utility, loss, concordance = random_utility, random_loss, np.nan
            rows.append({
                "user_key": user,
                "encoding": encoding,
                "k": int(k),
                "head": rank_row.head,
                "top2_mean_q": utility,
                "top2_worst_q_loss": loss,
                "pairwise_concordance": concordance,
                "head_active": active,
                "used_random_expectation": not active,
                "active_composition": rank_row.active_composition,
            })
    metrics = pd.DataFrame(rows)
    expected_rows = len(score_input) * len(contract["cells"]) * len(METRIC_HEADS)
    if len(metrics) != expected_rows or metrics.duplicated(["user_key", "encoding", "k", "head"]).any():
        raise RuntimeError("user metric completeness drift")
    if set(metrics.columns) & {"movie_id", "user_id", "rating", "target_q_eval", "timestamp"}:
        raise RuntimeError("raw identity or label leaked into user metrics")
    if not np.isfinite(metrics[list(PRIMARY_METRICS)].to_numpy(dtype=np.float64)).all():
        raise RuntimeError("nonfinite primary metric")
    atomic_to_parquet(metrics_path, metrics)
    metadata = {"users": len(score_input), "rows": len(metrics), "heads_including_random": len(METRIC_HEADS)}
    write_integrity(metrics_integrity_path, {"user_metrics": metrics_path}, signature=signature, metadata=metadata)
    progress_update(contract, "METRICS_COMPLETE", **metadata)
    return {"status": "MATERIALIZED_USER_METRICS", **metadata}


def _metric_series(
    indexed: pd.DataFrame, users: list[str], encoding: str, k: int, head: str, metric: str,
) -> np.ndarray:
    return indexed.loc[(slice(None), encoding, k, head), metric].droplevel([1, 2, 3]).reindex(users).to_numpy(dtype=np.float64)


def build_contrasts(
    metrics: pd.DataFrame, contract: Mapping[str, Any],
) -> tuple[np.ndarray, list[dict[str, Any]], list[str]]:
    users = sorted(metrics["user_key"].unique())
    indexed = metrics.set_index(["user_key", "encoding", "k", "head"]).sort_index()
    columns: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []
    for cell in contract["cells"]:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        for left, right in contract["statistics"]["comparisons_per_cell"]:
            for metric in PRIMARY_METRICS:
                columns.append(
                    _metric_series(indexed, users, encoding, k, str(left), metric)
                    - _metric_series(indexed, users, encoding, k, str(right), metric)
                )
                metadata.append({
                    "encoding": encoding, "k": k, "left": left, "right": right, "metric": metric,
                })
    if len(columns) != int(contract["statistics"]["expected_contrasts"]):
        raise RuntimeError(f"contrast family drift: {len(columns)}")
    return np.column_stack(columns), metadata, users


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    if not len(array):
        return {"n": 0}
    quantiles = np.quantile(array, [0.0, 0.25, 0.5, 0.75, 1.0], method="linear")
    return {
        "n": int(len(array)),
        "mean": float(array.mean()),
        "min": float(quantiles[0]),
        "p25": float(quantiles[1]),
        "median": float(quantiles[2]),
        "p75": float(quantiles[3]),
        "max": float(quantiles[4]),
    }


def korean_language_descriptive(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    label_source_path = output_path(contract, "label_source")
    verify_integrity(
        output_path(contract, "label_source_integrity"), {"label_source": label_source_path}, signature=signature,
    )
    source = pd.read_parquet(label_source_path)
    user_counts: list[int] = []
    unique_movies: set[int] = set()
    rating_hist = np.zeros(10, dtype=np.int64)
    q_values: list[float] = []
    for row in source.itertuples(index=False):
        movies = [int(value) for value in row.korean_cold_movie_ids]
        indices = np.asarray(row.korean_cold_rating_idx, dtype=np.int8)
        if len(movies) != len(indices):
            raise RuntimeError("Korean-language exposure alignment drift")
        user_counts.append(len(movies))
        unique_movies.update(movies)
        if len(indices):
            np.add.at(rating_hist, indices, 1)
            q_values.extend(_q_from_hist(indices, row.full_rating_hist).tolist())
    positive = [count for count in user_counts if count > 0]
    count_frequency = {str(value): int(user_counts.count(value)) for value in sorted(set(user_counts))}
    result = {
        "claim": contract["korean_language_descriptive"]["claim"],
        "cohort_users": len(user_counts),
        "users_with_exposure": len(positive),
        "users_with_at_least_2": int(sum(value >= 2 for value in user_counts)),
        "unique_items": len(unique_movies),
        "exposures": int(sum(user_counts)),
        "user_count_frequency_including_zero": count_frequency,
        "positive_user_count_distribution": _distribution(positive),
        "rating_histogram": {str(RATING_VALUES[index]): int(value) for index, value in enumerate(rating_hist)},
        "q_eval_distribution": _distribution(q_values),
    }
    forbidden_tokens = [token.lower() for token in contract["korean_language_descriptive"]["forbidden_outputs"]]
    if any(token in key.lower() for key in result for token in forbidden_tokens):
        raise RuntimeError("forbidden Korean descriptive field")
    return result


def analyze(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    metrics_path = output_path(contract, "user_metrics")
    integrity = verify_integrity(
        output_path(contract, "user_metrics_integrity"), {"user_metrics": metrics_path}, signature=signature,
    )
    metrics = pd.read_parquet(metrics_path)
    if integrity["metadata"] != {
        "users": int(contract["cohort"]["expected_users"]),
        "rows": len(metrics),
        "heads_including_random": len(METRIC_HEADS),
    }:
        raise ResumeError("user metric integrity metadata drift before analysis")
    values, metadata, users = build_contrasts(metrics, contract)
    progress_update(contract, "BOOTSTRAP", users=len(users), contrasts=len(metadata))
    intervals = simultaneous_max_t(
        values,
        repeats=int(contract["statistics"]["bootstrap_repeats"]),
        seed=int(contract["statistics"]["seed"]),
    )
    interval_rows: list[dict[str, Any]] = []
    lookup: dict[tuple[str, int, str, str, str], dict[str, Any]] = {}
    for index, meta in enumerate(metadata):
        row = {
            **meta,
            "mean": float(intervals["mean"][index]),
            "low": float(intervals["low"][index]),
            "high": float(intervals["high"][index]),
            "half_width": float(intervals["half_width"][index]),
            "interval_label": contract["statistics"]["interval_label"],
        }
        interval_rows.append(row)
        lookup[(str(meta["encoding"]), int(meta["k"]), str(meta["left"]), str(meta["right"]), str(meta["metric"]))] = row

    def qualifies(encoding: str, k: int, left: str, right: str) -> bool:
        utility = lookup[(encoding, k, left, right, "top2_mean_q")]
        loss = lookup[(encoding, k, left, right, "top2_worst_q_loss")]
        return (
            float(utility["low"]) >= float(contract["decision"]["utility_margin"])
            and float(loss["high"]) <= float(contract["decision"]["worst_loss_margin"])
        )

    propositions: list[dict[str, Any]] = []
    forward_set: list[dict[str, Any]] = []
    for cell in contract["cells"]:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        q_structured = qualifies(encoding, k, "STRUCTURED", "RANDOM_EXPECTATION")
        q_e5_random = qualifies(encoding, k, "E5", "RANDOM_EXPECTATION")
        q_e5_structured = qualifies(encoding, k, "E5", "STRUCTURED")
        q_rrf_random = qualifies(encoding, k, "AVAILABLE_HEAD_CONTENT_RRF", "RANDOM_EXPECTATION")
        q_rrf_e5 = qualifies(encoding, k, "AVAILABLE_HEAD_CONTENT_RRF", "E5")
        q_rrf_structured = qualifies(encoding, k, "AVAILABLE_HEAD_CONTENT_RRF", "STRUCTURED")
        e5_incremental = q_e5_random and q_e5_structured
        rrf_improvement = q_rrf_random and q_rrf_e5 and q_rrf_structured
        propositions.append({
            "encoding": encoding,
            "k": k,
            "structured_signal": q_structured,
            "e5_incremental_signal": e5_incremental,
            "rrf_improvement_signal": rrf_improvement,
        })
        for head, passed in (
            ("STRUCTURED", q_structured),
            ("E5", e5_incremental),
            ("AVAILABLE_HEAD_CONTENT_RRF", rrf_improvement),
        ):
            if passed:
                forward_set.append({"encoding": encoding, "k": k, "head": head})
    status = "PSEUDO_COLD_DEVELOPMENT_SIGNAL" if forward_set else "PSEUDO_COLD_DEVELOPMENT_NO_SIGNAL"
    selection = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023B",
        "status": status,
        "interval_label": contract["statistics"]["interval_label"],
        "adaptive_stage1_and_split_feasibility_seen": True,
        "users": len(users),
        "critical_value": float(intervals["critical"]),
        "propositions": propositions,
        "forward_set": forward_set,
        "champion": None,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
    }
    active_metrics = metrics.loc[metrics["head"] != "RANDOM_EXPECTATION"].copy()
    means = metrics.groupby(["encoding", "k", "head"], observed=True)[list(PRIMARY_METRICS)].mean().reset_index()
    concordance = active_metrics.groupby(["encoding", "k", "head"], observed=True)["pairwise_concordance"].agg(["mean", "count"]).reset_index()
    active = active_metrics.groupby(["encoding", "k", "head"], observed=True)["head_active"].agg(["mean", "sum", "count"]).reset_index()
    prepared = verify_integrity(
        output_path(contract, "score_prepared_integrity"), _score_prepared_artifacts(contract), signature=signature,
    )
    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023B",
        "status": status,
        "estimand": contract["item_split"]["name"],
        "claim_boundary": contract["claim_boundary"],
        "selection": selection,
        "simultaneous_intervals": interval_rows,
        "metric_means": means.to_dict("records"),
        "active_concordance": concordance.to_dict("records"),
        "active_and_null_counts": active.to_dict("records"),
        "composition_counts": active_metrics.groupby(
            ["encoding", "k", "head", "active_composition"], observed=True, dropna=False,
        ).size().reset_index(name="users").to_dict("records"),
        "korean_language_exposure_descriptive": korean_language_descriptive(contract),
        "derived_train_warm_prior": {
            key: prepared["metadata"][key]
            for key in ("train_warm_users", "train_warm_interactions", "pi0", "g0_mid")
        },
        "adaptive_stage1_and_split_feasibility_seen": True,
        "fresh_or_confirmatory": False,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "champion": None,
        "product_policy_updated": False,
    }
    atomic_write_json(output_path(contract, "selection"), selection)
    atomic_write_json(output_path(contract, "result"), result)
    progress_update(contract, "COMPLETE", status=status, forward_cells=len(forward_set))
    return selection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--phase", choices=("lock", "prepare", "score", "metrics", "analyze", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    contract_path = args.contract.resolve()
    if contract_path != DEFAULT_CONTRACT.resolve():
        raise RuntimeError("REC-EV-023B accepts only the committed default contract")
    contract = read_json(contract_path)
    validate_contract(contract)
    if np.__version__ != contract["statistics"]["numpy_version"]:
        raise RuntimeError(f"NumPy version drift: {np.__version__}")
    if args.phase == "lock":
        value = create_or_verify_lock(contract, contract_path, resume=args.resume)
    else:
        if not args.resume:
            raise ResumeError("REC-EV-023B real phases require --resume")
        create_or_verify_lock(contract, contract_path, resume=True)
        if args.phase in {"prepare", "run"}:
            value = prepare(contract)
        if args.phase in {"score", "run"}:
            if not output_path(contract, "score_prepared_integrity").is_file():
                raise ResumeError("score requires sealed prepared score artifacts")
            value = score(contract)
        if args.phase in {"metrics", "run"}:
            if not output_path(contract, "score_rank_integrity").is_file():
                raise ResumeError("metrics require complete score-rank integrity")
            value = materialize_metrics(contract)
        if args.phase in {"analyze", "run"}:
            if not output_path(contract, "user_metrics_integrity").is_file():
                raise ResumeError("analyze requires completed user metrics")
            value = analyze(contract)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
