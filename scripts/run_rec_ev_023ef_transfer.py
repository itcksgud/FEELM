#!/usr/bin/env python3
"""Run the locked REC-EV-023E or REC-EV-023F conditional-transfer experiment."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import tempfile
import zipfile
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import sparse

try:
    from rec_ev_022a_core import (
        RATING_VALUES,
        canonical_decimal,
        encoding_weights,
        old_user_bucket,
        user_key,
        user_role_bucket,
    )
except ImportError:
    from scripts.rec_ev_022a_core import (
        RATING_VALUES,
        canonical_decimal,
        encoding_weights,
        old_user_bucket,
        user_key,
        user_role_bucket,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACTS = {
    "REC-EV-023E": ROOT / "docs/recommendation/contracts/rec-ev-023e-korean-origin-transfer.json",
    "REC-EV-023F": ROOT / "docs/recommendation/contracts/rec-ev-023f-recent-release-transfer.json",
}
HEAD_FILE_KEYS = {
    "BASIC": "feature_basic",
    "RELEASE_PROXY": "feature_release",
    "FULL_CURRENT": "feature_full",
}
CLASSES = ("TARGET_IMPROVEMENT", "CONTROL_IMPROVEMENT", "CONDITIONAL_GAP")
ENDPOINTS = (
    "UTILITY_IMPROVEMENT_MODEL_MINUS_RANDOM",
    "SAFETY_IMPROVEMENT_RANDOM_LOSS_MINUS_MODEL",
)
MAXIMUM_USER_ID = 300_000
PROGRESS_PHASE_INDEX = {
    "PREPARED": 10,
    "SCORING": 20,
    "RANK_SEALED": 30,
    "METRICS_SEALED": 40,
    "BOOTSTRAP_SEALED": 50,
    "COMPLETE": 60,
}
PROGRESS_DETAIL_KEYS = {
    "PREPARED": {
        "allowed_rows_movie_id_parsed_membership_first_pass", "rating_bytes_parsed_membership_first_pass",
        "eligible_users", "eligible_user_key_set_sha256", "timestamp_bytes_parsed",
        "excluded_rows_movie_or_rating_parsed", "eligible_rows_movie_id_parsed_membership_second_pass",
        "rating_bytes_parsed_membership_second_pass", "selected_profile_rating_bytes_parsed_score_input_pass",
        "target_control_rating_bytes_parsed_before_rank_seal", "universe_items", "panels",
        "unique_selected_targets", "selected_target_memberships", "selected_target_exposure_sha256",
        "selection_rating_q_timestamp_popularity_arguments", "label_source_created_before_rank_seal",
        "evaluation_labels_opened_before_rank_seal", "raw_user_ids_written",
        "old_locked_ratings_timestamps_metrics_opened", "final_reserve_opened",
    },
    "SCORING": {"users_complete", "users_total"},
    "RANK_SEALED": {"users", "rows", "inactive_rows", "label_source_opened", "b0_or_target_popularity_opened"},
    "METRICS_SEALED": {"users", "contrasts"},
    "BOOTSTRAP_SEALED": {
        "users", "contrasts", "valid_replicates", "invalid_attempts", "first_valid_attempt",
        "last_valid_attempt", "poisson_golden_verified", "primary_regime",
    },
    "COMPLETE": {"status", "users", "contrasts"},
}


class ResumeError(RuntimeError):
    pass


class ReaderFirewallError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_to_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.parquet")
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_save_npy(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.npy")
    try:
        np.save(temporary, values, allow_pickle=False)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_save_npz(path: Path, **values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.npz")
    try:
        np.savez_compressed(temporary, **values)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_save_sparse(path: Path, matrix: sparse.spmatrix) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.npz")
    try:
        sparse.save_npz(temporary, matrix.tocsr(), compressed=True)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def resolve_input(entry: Mapping[str, Any]) -> Path:
    path = Path(str(entry["path"]))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def output_path(contract: Mapping[str, Any], name: str) -> Path:
    return ROOT / str(contract["output_root"]) / str(contract["outputs"][name])


def artifact_row(path: Path, *, logical_path: str | None = None) -> dict[str, Any]:
    return {
        "path": logical_path if logical_path is not None else path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def source_rows(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, entry in sorted(contract["allowed_input_artifacts"].items()):
        path = resolve_input(entry)
        if not path.is_file():
            raise RuntimeError(f"source missing: {name}")
        row = artifact_row(path, logical_path=str(entry["path"]))
        if row["bytes"] != int(entry["bytes"]) or row["sha256"] != str(entry["sha256"]):
            raise RuntimeError(f"source pin mismatch: {name}")
        rows.append({"name": name, **row})
    return rows


def implementation_rows(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative in contract["implementation_artifacts"]:
        path = (ROOT / str(relative)).resolve()
        if not path.is_file():
            raise RuntimeError(f"implementation missing: {relative}")
        rows.append(artifact_row(path, logical_path=str(relative)))
    return rows


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "purpose", "joint_design_audit", "joint_contract_binding", "execution_delta", "preflight_audits",
        "authorization", "reader", "universe", "preflight", "cohort", "cells", "features", "scoring", "metrics", "statistics", "decision",
        "claim_boundary", "resume", "invariants",
    )
    return {key: contract[key] for key in keys}


def _validator(contract: Mapping[str, Any]):
    if contract.get("evidence_id") == "REC-EV-023E":
        try:
            from validate_rec_ev_023e_contract import validate_contract
        except ImportError:
            from scripts.validate_rec_ev_023e_contract import validate_contract
        return validate_contract
    if contract.get("evidence_id") == "REC-EV-023F":
        try:
            from validate_rec_ev_023f_contract import validate_contract
        except ImportError:
            from scripts.validate_rec_ev_023f_contract import validate_contract
        return validate_contract
    raise RuntimeError("unsupported transfer contract")


def expected_lock_state(contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _validator(contract)(contract)
    sources = source_rows(contract)
    implementations = implementation_rows(contract)
    manifest = {
        "schema_version": 1,
        "evidence_id": contract["evidence_id"],
        "sources": sources,
        "implementation_artifacts": implementations,
        "joint_preflight_result_opened": True,
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "excluded_old_locked_users_used": False,
        "evaluation_labels_opened_at_lock": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    hashes = {
        "contract_sha256": hashlib.sha256(canonical_json_bytes(contract)).hexdigest(),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest(),
    }
    lock = {
        "schema_version": 1,
        "evidence_id": contract["evidence_id"],
        "status": "LOCKED_FIXED_CONDITIONAL_TRANSFER",
        **hashes,
        "source_manifest_sha256": hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        "evaluation_labels_opened_at_lock": False,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    return manifest, lock


def create_or_verify_lock(contract: Mapping[str, Any], *, resume: bool) -> dict[str, Any]:
    lock_path = output_path(contract, "protocol_lock")
    manifest_path = output_path(contract, "source_manifest")
    present = [lock_path.exists(), manifest_path.exists()]
    if any(present) and not all(present):
        raise ResumeError("partial protocol lock state")
    manifest, lock = expected_lock_state(contract)
    if all(present):
        if not resume:
            raise ResumeError("protocol lock exists; use --resume")
        if read_json(manifest_path) != manifest or read_json(lock_path) != lock:
            raise ResumeError("protocol lock or source manifest drift")
        return lock
    if resume:
        raise ResumeError("--resume requested before protocol lock exists")
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(lock_path, lock)
    return lock


def run_signature(contract: Mapping[str, Any]) -> str:
    lock = read_json(output_path(contract, "protocol_lock"))
    payload = {key: lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def write_integrity(
    path: Path,
    artifacts: Mapping[str, Path],
    *,
    signature: str,
    metadata: Mapping[str, Any],
) -> None:
    value = {
        "schema_version": 1,
        "run_signature": signature,
        "artifacts": {name: artifact_row(value_path) for name, value_path in sorted(artifacts.items())},
        "metadata": dict(metadata),
    }
    atomic_write_json(path, value)


def verify_integrity(
    path: Path,
    artifacts: Mapping[str, Path],
    *,
    signature: str,
    allow_manifest_subset: bool = False,
    expected_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        raise ResumeError(f"integrity manifest missing: {path.name}")
    value = read_json(path)
    if set(value) != {"schema_version", "run_signature", "artifacts", "metadata"} or value.get("schema_version") != 1:
        raise ResumeError(f"integrity schema drift: {path.name}")
    if value.get("run_signature") != signature:
        raise ResumeError(f"integrity signature drift: {path.name}")
    recorded = value.get("artifacts", {})
    if (not allow_manifest_subset and set(recorded) != set(artifacts)) or not set(artifacts).issubset(recorded):
        raise ResumeError(f"integrity artifact set drift: {path.name}")
    for name, value_path in artifacts.items():
        if not value_path.is_file() or recorded[name] != artifact_row(value_path):
            raise ResumeError(f"artifact integrity drift: {name}")
    if expected_metadata is not None and value.get("metadata") != dict(expected_metadata):
        raise ResumeError(f"integrity metadata drift: {path.name}")
    return value


def validate_progress(contract: Mapping[str, Any], *, minimum_phase: str | None = None) -> dict[str, Any]:
    path = output_path(contract, "progress")
    if not path.is_file():
        raise ResumeError("progress state missing")
    value = read_json(path)
    expected_keys = {
        "schema_version", "evidence_id", "run_signature", "phase", "phase_index", "details",
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight",
        "old_locked_ratings_timestamps_metrics_opened", "final_reserve_opened",
        "evaluation_labels_opened", "product_policy_updated", "champion",
    }
    if set(value) != expected_keys or value.get("schema_version") != 1:
        raise ResumeError("progress schema or unknown key drift")
    phase = value.get("phase")
    if phase not in PROGRESS_PHASE_INDEX or value.get("phase_index") != PROGRESS_PHASE_INDEX[phase]:
        raise ResumeError("progress phase identity drift")
    if value.get("evidence_id") != contract["evidence_id"] or value.get("run_signature") != run_signature(contract):
        raise ResumeError("progress evidence or signature drift")
    if set(value.get("details", {})) != PROGRESS_DETAIL_KEYS[phase]:
        raise ResumeError("progress detail keyset drift")
    if (
        value.get("old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight") is not True
        or value.get("old_locked_ratings_timestamps_metrics_opened") is not False
        or value.get("final_reserve_opened") is not False
        or value.get("product_policy_updated") is not False
        or value.get("champion") is not None
    ):
        raise ResumeError("progress safety invariant drift")
    expected_labels_opened = PROGRESS_PHASE_INDEX[phase] >= PROGRESS_PHASE_INDEX["METRICS_SEALED"]
    if value.get("evaluation_labels_opened") is not expected_labels_opened:
        raise ResumeError("progress label-open state drift")
    details = value["details"]
    for key in ("old_locked_ratings_timestamps_metrics_opened", "final_reserve_opened"):
        if key in details and details[key] is not False:
            raise ResumeError("progress detail safety invariant drift")
    if minimum_phase is not None and PROGRESS_PHASE_INDEX[phase] < PROGRESS_PHASE_INDEX[minimum_phase]:
        raise ResumeError("progress phase is behind sealed artifact state")
    return value


def progress_update(contract: Mapping[str, Any], phase: str, **extra: Any) -> None:
    if phase not in PROGRESS_PHASE_INDEX or set(extra) != PROGRESS_DETAIL_KEYS[phase]:
        raise ResumeError("invalid progress phase or detail keyset")
    path = output_path(contract, "progress")
    previous = validate_progress(contract) if path.is_file() else None
    if previous is not None:
        if PROGRESS_PHASE_INDEX[phase] < int(previous["phase_index"]):
            raise ResumeError("progress phase regression")
        if phase == "SCORING" and previous["phase"] == "SCORING":
            if int(extra["users_complete"]) < int(previous["details"]["users_complete"]):
                raise ResumeError("progress scoring counter regression")
    value = {
        "schema_version": 1,
        "evidence_id": contract["evidence_id"],
        "run_signature": run_signature(contract),
        "phase": phase,
        "phase_index": PROGRESS_PHASE_INDEX[phase],
        "details": {key: _jsonable_value(extra[key]) for key in sorted(extra)},
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
        "evaluation_labels_opened": PROGRESS_PHASE_INDEX[phase] >= PROGRESS_PHASE_INDEX["METRICS_SEALED"],
        "product_policy_updated": False,
        "champion": None,
    }
    atomic_write_json(path, value)


def allowed_evaluation_user(raw_user: int) -> bool:
    if raw_user <= 0 or raw_user > MAXIMUM_USER_ID:
        raise ReaderFirewallError("MovieLens user id outside preregistered bound")
    if old_user_bucket(raw_user) > 59:
        return False
    bucket = user_role_bucket(raw_user)
    return 6000 <= bucket <= 9199


def parse_allowed_movie_line(raw_line: bytes) -> tuple[int, int, int] | None:
    """Parse only user/movie identity; never inspect bytes after the second comma."""
    first = raw_line.find(b",")
    if first <= 0:
        raise ReaderFirewallError("MovieLens user delimiter drift")
    raw_user = int(raw_line[:first])
    if not allowed_evaluation_user(raw_user):
        return None
    second = raw_line.find(b",", first + 1)
    if second <= first + 1:
        raise ReaderFirewallError("MovieLens movie delimiter drift")
    movie = int(raw_line[first + 1:second])
    return raw_user, movie, second


def _rating_index_after_movie(raw_line: bytes, second_comma: int) -> int:
    third = raw_line.find(b",", second_comma + 1)
    if third <= second_comma + 1:
        raise ReaderFirewallError("MovieLens rating delimiter drift")
    rating_bytes = raw_line[second_comma + 1:third]
    try:
        rating = float(rating_bytes)
    except ValueError as error:
        raise ReaderFirewallError("MovieLens rating parse drift") from error
    index = int(round((rating - 0.5) * 2.0))
    if index < 0 or index >= len(RATING_VALUES) or not math.isclose(float(RATING_VALUES[index]), rating):
        raise ReaderFirewallError("MovieLens rating grid drift")
    return index


def iter_allowed_movie_lines(lines: Iterable[bytes]):
    previous_user = 0
    current_user = -1
    current_allowed = False
    seen_movies: set[int] = set()
    for raw_line in lines:
        first = raw_line.find(b",")
        if first <= 0:
            raise ReaderFirewallError("MovieLens user delimiter drift")
        raw_user = int(raw_line[:first])
        if raw_user < previous_user:
            raise ReaderFirewallError("MovieLens user order drift")
        previous_user = raw_user
        if raw_user != current_user:
            current_user = raw_user
            current_allowed = allowed_evaluation_user(raw_user)
            seen_movies.clear()
        if not current_allowed:
            continue
        second = raw_line.find(b",", first + 1)
        if second <= first + 1:
            raise ReaderFirewallError("MovieLens movie delimiter drift")
        movie = int(raw_line[first + 1:second])
        if movie in seen_movies:
            raise ReaderFirewallError("duplicate allowed user-movie rating row")
        seen_movies.add(movie)
        yield raw_user, movie, raw_line, second


def movie_lens_movie_rows(contract: Mapping[str, Any]):
    entry = contract["allowed_input_artifacts"]["movielens_archive"]
    archive = resolve_input(entry)
    member = str(entry["member"])
    with zipfile.ZipFile(archive) as bundle:
        if member not in bundle.namelist():
            raise ReaderFirewallError("MovieLens rating member missing")
        with bundle.open(member) as handle:
            if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
                raise ReaderFirewallError("MovieLens rating header drift")
            yield from iter_allowed_movie_lines(handle)


def _list_values(value: Any) -> list[int]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    return [int(item) for item in value]


def _group_matrix(token_rows: Sequence[Sequence[str]]) -> sparse.csr_matrix:
    vocabulary = {token: index for index, token in enumerate(sorted({token for row in token_rows for token in row}))}
    rows: list[int] = []
    columns: list[int] = []
    for row_index, tokens in enumerate(token_rows):
        for token in sorted(set(tokens)):
            rows.append(row_index)
            columns.append(vocabulary[token])
    matrix = sparse.coo_matrix(
        (np.ones(len(rows), dtype=np.float32), (rows, columns)),
        shape=(len(token_rows), len(vocabulary)),
        dtype=np.float32,
    ).tocsr()
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    inverse = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
    return (sparse.diags(inverse.astype(np.float32)) @ matrix).tocsr()


def build_feature_heads(structured: pd.DataFrame) -> tuple[np.ndarray, dict[str, sparse.csr_matrix], np.ndarray, np.ndarray]:
    required = {
        "movie_id", "feature_eligible", "release_year", "runtime_minutes", "original_language",
        "genre_ids", "director_ids", "top5_cast_ids", "keyword_ids",
    }
    if not required.issubset(structured.columns):
        raise RuntimeError("structured feature schema drift")
    eligible = structured["feature_eligible"].fillna(False).astype(bool) & structured["release_year"].notna()
    frame = structured.loc[eligible].copy().sort_values("movie_id", kind="stable", ignore_index=True)
    if frame["movie_id"].duplicated().any():
        raise RuntimeError("structured movie id duplicate")
    genre_rows: list[list[str]] = []
    context_rows: list[list[str]] = []
    people_rows: list[list[str]] = []
    keyword_rows: list[list[str]] = []
    for row in frame.itertuples(index=False):
        decade = int(row.release_year) // 10 * 10
        runtime_bucket = int(row.runtime_minutes) // 30 if pd.notna(row.runtime_minutes) else None
        genre_rows.append([f"genre:{value}" for value in _list_values(row.genre_ids)])
        context_rows.append(
            ([f"language:{row.original_language}"] if pd.notna(row.original_language) else [])
            + [f"decade:{decade}"]
            + ([f"runtime30:{runtime_bucket}"] if runtime_bucket is not None else [])
        )
        people_rows.append(
            [f"director:{value}" for value in _list_values(row.director_ids)]
            + [f"cast:{value}" for value in _list_values(row.top5_cast_ids)]
        )
        keyword_rows.append([f"keyword:{value}" for value in _list_values(row.keyword_ids)])
    groups = {
        "G": _group_matrix(genre_rows),
        "C": _group_matrix(context_rows),
        "P": _group_matrix(people_rows),
        "W": _group_matrix(keyword_rows),
    }
    heads: dict[str, sparse.csr_matrix] = {}
    for head, group_names in {
        "BASIC": ("G", "C"),
        "RELEASE_PROXY": ("G", "C", "P"),
        "FULL_CURRENT": ("G", "C", "P", "W"),
    }.items():
        matrix = sparse.hstack([groups[name] * np.float32(0.25) for name in group_names], format="csr")
        norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
        inverse = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
        heads[head] = (sparse.diags(inverse.astype(np.float32)) @ matrix).tocsr()
    if np.any(np.asarray(heads["BASIC"].getnnz(axis=1)).ravel() == 0):
        raise RuntimeError("eligible universe includes a zero BASIC vector")
    return (
        frame["movie_id"].to_numpy(dtype=np.int32),
        heads,
        frame["release_year"].to_numpy(dtype=np.int16),
        frame.index.to_numpy(dtype=np.int32),
    )


def korean_projection(contract: Mapping[str, Any]) -> set[int]:
    if contract["evidence_id"] != "REC-EV-023E":
        return set()
    value = read_json(resolve_input(contract["allowed_input_artifacts"]["korean_movie_id_projection"]))
    expected_keys = {"artifact_id", "claim", "count", "movie_ids", "projection_rule", "schema_version", "source_artifacts"}
    if set(value) != expected_keys or value["artifact_id"] != "KOREAN_ORIGIN_MOVIELENS_MOVIE_ID_PROJECTION_V1":
        raise RuntimeError("Korean movie-id projection schema drift")
    movies = {int(movie) for movie in value["movie_ids"]}
    if int(value["count"]) != 1078 or len(movies) != 1078:
        raise RuntimeError("Korean movie-id projection cardinality drift")
    return movies


def _movie_lookup(movie_ids: np.ndarray) -> np.ndarray:
    lookup = np.full(int(movie_ids.max(initial=0)) + 1, -1, dtype=np.int32)
    lookup[movie_ids] = np.arange(len(movie_ids), dtype=np.int32)
    return lookup


def _domain_masks(contract: Mapping[str, Any], movie_ids: np.ndarray, years: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if contract["evidence_id"] == "REC-EV-023E":
        korean = korean_projection(contract)
        target = np.fromiter((int(movie) in korean for movie in movie_ids), dtype=bool, count=len(movie_ids))
        return ~target, target
    target = (years >= 2020) & (years <= 2023)
    return years < 2020, target


def _panel_order(user_key_value: str, movies: Sequence[int], salt: str) -> list[int]:
    return sorted(
        (int(movie) for movie in movies),
        key=lambda movie: (
            hashlib.sha256(f"{salt}|{user_key_value}|{canonical_decimal(movie)}".encode("utf-8")).digest(),
            movie,
        ),
    )


def _first_pass(
    contract: Mapping[str, Any], movie_ids: np.ndarray, profile_mask: np.ndarray, target_mask: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    lookup = _movie_lookup(movie_ids)
    profile_counts = np.zeros(MAXIMUM_USER_ID + 1, dtype=np.uint32)
    target_counts = np.zeros(MAXIMUM_USER_ID + 1, dtype=np.uint32)
    allowed_rows = 0
    for raw_user, movie, _raw_line, _second in movie_lens_movie_rows(contract):
        allowed_rows += 1
        position = int(lookup[movie]) if 0 <= movie < len(lookup) else -1
        if position >= 0:
            profile_counts[raw_user] += int(profile_mask[position])
            target_counts[raw_user] += int(target_mask[position])
    cohort = contract["cohort"]
    eligible_mask = (
        (profile_counts >= int(cohort["minimum_profile_control_ratings"]))
        & (target_counts >= int(cohort["minimum_target_ratings"]))
    )
    eligible_users = np.flatnonzero(eligible_mask).astype(np.int32)
    keys = sorted(user_key(int(value)) for value in eligible_users)
    observed_hash = hashlib.sha256(canonical_json_bytes(keys)).hexdigest()
    expected = contract["preflight"]
    if len(eligible_users) != int(expected["expected_users"]) or observed_hash != expected["eligible_user_key_set_sha256"]:
        raise RuntimeError("eligible user set drift from pre-label result")
    metadata = {
        "allowed_rows_movie_id_parsed_membership_first_pass": allowed_rows,
        "rating_bytes_parsed_membership_first_pass": 0,
        "eligible_users": len(eligible_users),
        "eligible_user_key_set_sha256": observed_hash,
        "timestamp_bytes_parsed": 0,
        "excluded_rows_movie_or_rating_parsed": 0,
    }
    return eligible_users, metadata


def _second_pass_pools(
    contract: Mapping[str, Any], eligible_users: np.ndarray, movie_ids: np.ndarray,
    profile_mask: np.ndarray, target_mask: np.ndarray,
) -> tuple[dict[int, list[int]], dict[int, list[int]], int]:
    eligible = np.zeros(MAXIMUM_USER_ID + 1, dtype=bool)
    eligible[eligible_users] = True
    lookup = _movie_lookup(movie_ids)
    profile_movies = {int(value): [] for value in eligible_users}
    target_movies = {int(value): [] for value in eligible_users}
    parsed = 0
    for raw_user, movie, _raw_line, _second in movie_lens_movie_rows(contract):
        if not eligible[raw_user]:
            continue
        parsed += 1
        position = int(lookup[movie]) if 0 <= movie < len(lookup) else -1
        if position < 0:
            continue
        if bool(profile_mask[position]):
            profile_movies[raw_user].append(int(movie))
        elif bool(target_mask[position]):
            target_movies[raw_user].append(int(movie))
    return profile_movies, target_movies, parsed


def _profile_rating_pass(
    contract: Mapping[str, Any], selected_profile_movies: Mapping[int, set[int]],
) -> tuple[dict[int, dict[int, int]], int]:
    ratings = {int(raw_user): {} for raw_user in selected_profile_movies}
    parsed = 0
    for raw_user, movie, raw_line, second in movie_lens_movie_rows(contract):
        requested = selected_profile_movies.get(int(raw_user))
        if requested is None or int(movie) not in requested:
            continue
        ratings[int(raw_user)][int(movie)] = _rating_index_after_movie(raw_line, second)
        parsed += 1
    for raw_user, requested in selected_profile_movies.items():
        if set(ratings[int(raw_user)]) != set(requested):
            raise RuntimeError("selected profile rating completeness drift")
    return ratings, parsed


def _prepared_artifacts(contract: Mapping[str, Any]) -> dict[str, Path]:
    return {
        "item_ids": output_path(contract, "item_ids"),
        "feature_basic": output_path(contract, "feature_basic"),
        "feature_release": output_path(contract, "feature_release"),
        "feature_full": output_path(contract, "feature_full"),
        "score_input": output_path(contract, "score_input"),
    }


def _score_only_artifacts(contract: Mapping[str, Any]) -> dict[str, Path]:
    return _prepared_artifacts(contract)


def require_same_sparse(expected: sparse.csr_matrix, actual: sparse.csr_matrix, name: str) -> None:
    left = expected.tocsr()
    right = actual.tocsr()
    if (
        left.shape != right.shape
        or not np.array_equal(left.indptr, right.indptr)
        or not np.array_equal(left.indices, right.indices)
        or not np.array_equal(left.data, right.data)
    ):
        raise ResumeError(f"{name} semantic drift")


def prepare(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    integrity_path = output_path(contract, "prepared_integrity")
    artifacts = _prepared_artifacts(contract)
    present = [integrity_path.exists(), *(path.exists() for path in artifacts.values())]
    if any(present):
        if not all(present):
            raise ResumeError("partial prepared state")
    reuse = all(present)
    structured = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["structured_features"]))
    movie_ids, heads, years, _ = build_feature_heads(structured)
    profile_mask, target_mask = _domain_masks(contract, movie_ids, years)
    eligible_users, reader_metadata = _first_pass(contract, movie_ids, profile_mask, target_mask)
    profile_movies, target_movies, second_pass_rows = _second_pass_pools(
        contract, eligible_users, movie_ids, profile_mask, target_mask,
    )
    score_rows: list[dict[str, Any]] = []
    panel_memberships: list[dict[str, Any]] = []
    selected_profile_movies: dict[int, set[int]] = {int(value): set() for value in eligible_users}
    target_exposures: list[str] = []
    target_union: set[int] = set()
    profile_salts = contract["cohort"]["profile_control_salts"]
    target_salts = contract["cohort"]["target_salts"]
    profile_n = int(contract["cohort"]["profile_n"])
    control_n = int(contract["cohort"]["control_n"])
    target_n = int(contract["cohort"]["target_n"])
    for raw_user in eligible_users.tolist():
        key = user_key(int(raw_user))
        if len(profile_movies[int(raw_user)]) < profile_n + control_n or len(target_movies[int(raw_user)]) < target_n:
            raise RuntimeError("eligible user panel pool short after outcome pass")
        for panel in range(int(contract["cohort"]["panels"])):
            profile_control = _panel_order(key, profile_movies[int(raw_user)], str(profile_salts[panel]))
            selected_profile = profile_control[:profile_n]
            selected_control = profile_control[profile_n:profile_n + control_n]
            selected_target = _panel_order(key, target_movies[int(raw_user)], str(target_salts[panel]))[:target_n]
            if set(selected_profile) & set(selected_control):
                raise RuntimeError("profile/control panel overlap")
            target_union.update(selected_target)
            target_exposures.extend(f"{key}|{panel}|{movie}" for movie in selected_target)
            selected_profile_movies[int(raw_user)].update(selected_profile)
            panel_memberships.append({
                "raw_user": int(raw_user),
                "user_key": key,
                "panel": panel,
                "profile_movie_ids": selected_profile,
                "target_movie_ids": selected_target,
                "control_movie_ids": selected_control,
            })
    profile_ratings, profile_rating_rows = _profile_rating_pass(contract, selected_profile_movies)
    for membership in panel_memberships:
        raw_user = int(membership.pop("raw_user"))
        profiles = [int(movie) for movie in membership["profile_movie_ids"]]
        score_rows.append({
            **membership,
            "profile_rating_idx": [profile_ratings[raw_user][movie] for movie in profiles],
        })
    score_input = pd.DataFrame(score_rows).sort_values(["user_key", "panel"], kind="stable", ignore_index=True)
    exposure_sha = hashlib.sha256(canonical_json_bytes(target_exposures)).hexdigest()
    expected = contract["preflight"]
    if len(target_union) != int(expected["expected_unique_selected_targets"]):
        raise RuntimeError("unique selected target drift from preflight")
    if len(target_exposures) != int(expected["expected_selected_target_memberships"]):
        raise RuntimeError("target membership drift from preflight")
    if exposure_sha != expected["selected_target_exposure_sha256"]:
        raise RuntimeError("selected target exposure hash drift from preflight")
    metadata = {
        **reader_metadata,
        "eligible_rows_movie_id_parsed_membership_second_pass": second_pass_rows,
        "rating_bytes_parsed_membership_second_pass": 0,
        "selected_profile_rating_bytes_parsed_score_input_pass": profile_rating_rows,
        "target_control_rating_bytes_parsed_before_rank_seal": 0,
        "universe_items": len(movie_ids),
        "panels": len(score_input),
        "unique_selected_targets": len(target_union),
        "selected_target_memberships": len(target_exposures),
        "selected_target_exposure_sha256": exposure_sha,
        "selection_rating_q_timestamp_popularity_arguments": 0,
        "label_source_created_before_rank_seal": False,
        "evaluation_labels_opened_before_rank_seal": False,
        "raw_user_ids_written": False,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
    }
    if reuse:
        verify_integrity(
            integrity_path, artifacts, signature=signature, expected_metadata=metadata,
        )
        if not np.array_equal(np.load(artifacts["item_ids"], allow_pickle=False), movie_ids):
            raise ResumeError("prepared item ids semantic drift")
        require_same_sparse(heads["BASIC"], sparse.load_npz(artifacts["feature_basic"]), "BASIC feature matrix")
        require_same_sparse(heads["RELEASE_PROXY"], sparse.load_npz(artifacts["feature_release"]), "RELEASE_PROXY feature matrix")
        require_same_sparse(heads["FULL_CURRENT"], sparse.load_npz(artifacts["feature_full"]), "FULL_CURRENT feature matrix")
        require_same_frame(score_input, pd.read_parquet(artifacts["score_input"]), "prepared score input")
        validate_progress(contract, minimum_phase="PREPARED")
        return {"status": "REUSED_EXACT_PREPARED", **metadata}
    atomic_save_npy(artifacts["item_ids"], movie_ids)
    atomic_save_sparse(artifacts["feature_basic"], heads["BASIC"])
    atomic_save_sparse(artifacts["feature_release"], heads["RELEASE_PROXY"])
    atomic_save_sparse(artifacts["feature_full"], heads["FULL_CURRENT"])
    atomic_to_parquet(artifacts["score_input"], score_input)
    write_integrity(integrity_path, artifacts, signature=signature, metadata=metadata)
    progress_update(contract, "PREPARED", **metadata)
    return {"status": "PREPARED", **metadata}


def active_scores(similarity: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, bool]:
    matrix = np.asarray(similarity, dtype=np.float64)
    vector = np.asarray(weights, dtype=np.float64)
    denominator = float(np.abs(vector).sum())
    if matrix.ndim != 2 or matrix.shape[1] != len(vector):
        raise ValueError("similarity/weight shape mismatch")
    if not math.isfinite(denominator) or denominator == 0:
        return np.zeros(matrix.shape[0], dtype=np.float64), False
    scores = (matrix @ vector) / denominator
    active = bool(np.isfinite(scores).all() and len(np.unique(scores)) >= 2)
    return scores if active else np.zeros(matrix.shape[0], dtype=np.float64), active


def strict_score_order(
    contract: Mapping[str, Any], user: str, panel: int, domain: str, head: str,
    encoding: str, k: int, movie_ids: Sequence[int], scores: Sequence[float],
) -> list[int]:
    movies = [int(movie) for movie in movie_ids]
    values = np.asarray(scores, dtype=np.float64)
    if len(movies) != len(values) or not np.isfinite(values).all():
        raise ValueError("rank input drift")
    prefix = str(contract["scoring"]["tie_prefix"])
    digests = [hashlib.sha256(
        f"{prefix}|{user}|{canonical_decimal(panel)}|{domain}|{head}|{encoding}|{canonical_decimal(k)}|{canonical_decimal(movie)}".encode("utf-8")
    ).digest() for movie in movies]
    order = sorted(range(len(movies)), key=lambda index: (-float(values[index]), digests[index], movies[index]))
    return [movies[index] for index in order]


def _part_path(root: Path, start: int, stop: int) -> Path:
    return root / f"part-{start:06d}-{stop:06d}.parquet"


def build_rank_frame(
    contract: Mapping[str, Any], selected: pd.DataFrame, lookup: Mapping[int, int],
    matrices: Mapping[str, sparse.csr_matrix], g0_mid: np.ndarray,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for value in selected.itertuples(index=False):
        profile_movies = [int(movie) for movie in value.profile_movie_ids]
        profile_ratings = np.asarray([float(RATING_VALUES[int(index)]) for index in value.profile_rating_idx], dtype=np.float64)
        full_profile_positions = np.asarray([lookup[movie] for movie in profile_movies], dtype=np.int64)
        for domain, domain_movies_value in (("TARGET", value.target_movie_ids), ("CONTROL", value.control_movie_ids)):
            domain_movies = [int(movie) for movie in domain_movies_value]
            target_positions = np.asarray([lookup[movie] for movie in domain_movies], dtype=np.int64)
            for head in contract["features"]["heads"]:
                matrix = matrices[str(head)]
                full_similarity = (matrix[target_positions] @ matrix[full_profile_positions].T).toarray().astype(np.float64)
                for cell in contract["cells"]:
                    encoding, k = str(cell["encoding"]), int(cell["k"])
                    weights = encoding_weights(encoding, profile_ratings[:k], g0_mid, tau=float(contract["scoring"]["tau"]))
                    scores, active = active_scores(full_similarity[:, :k], weights)
                    ranked = strict_score_order(
                        contract, str(value.user_key), int(value.panel), domain, str(head), encoding, k,
                        domain_movies, scores,
                    ) if active else []
                    rows.append({
                        "user_key": str(value.user_key), "panel": int(value.panel), "domain": domain,
                        "head": str(head), "encoding": encoding, "k": k, "active": active,
                        "ranked_movie_ids": ranked,
                    })
    return pd.DataFrame(rows).sort_values(
        ["user_key", "panel", "domain", "head", "encoding", "k"], kind="stable", ignore_index=True,
    )


def score(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    prepare(contract)
    prepared_integrity = output_path(contract, "prepared_integrity")
    score_artifacts = _score_only_artifacts(contract)
    prepared = verify_integrity(
        prepared_integrity, score_artifacts, signature=signature, allow_manifest_subset=True,
    )
    if prepared["metadata"].get("label_source_created_before_rank_seal") is not False:
        raise ResumeError("prepared label firewall metadata drift")
    score_input = pd.read_parquet(output_path(contract, "score_input"))
    item_ids = np.load(output_path(contract, "item_ids"), allow_pickle=False).astype(np.int64)
    lookup = {int(movie): index for index, movie in enumerate(item_ids.tolist())}
    matrices = {head: sparse.load_npz(output_path(contract, key)).tocsr() for head, key in HEAD_FILE_KEYS.items()}
    with np.load(resolve_input(contract["allowed_input_artifacts"]["train_prior"]), allow_pickle=False) as prior:
        g0_mid = prior["g0_mid"].astype(np.float64)
    user_keys = score_input["user_key"].drop_duplicates().astype(str).tolist()
    if len(user_keys) != int(contract["preflight"]["expected_users"]):
        raise RuntimeError("score input user cardinality drift")
    parts_root = output_path(contract, "rank_parts")
    parts_root.mkdir(parents=True, exist_ok=True)
    chunks = [(start, min(start + 64, len(user_keys))) for start in range(0, len(user_keys), 64)]
    expected_parts = {_part_path(parts_root, start, stop) for start, stop in chunks}
    unexpected = set(parts_root.glob("part-*.parquet")) - expected_parts
    unexpected_integrities = set(parts_root.glob("part-*.integrity.json")) - {path.with_suffix(".integrity.json") for path in expected_parts}
    if unexpected or unexpected_integrities:
        raise ResumeError("unexpected rank part state")
    for start, stop in chunks:
        destination = _part_path(parts_root, start, stop)
        integrity_path = destination.with_suffix(".integrity.json")
        expected_users = user_keys[start:stop]
        selected = score_input.loc[score_input["user_key"].isin(expected_users)]
        frame = build_rank_frame(contract, selected, lookup, matrices, g0_mid)
        expected_part_metadata = {"start": start, "stop": stop, "user_keys": expected_users, "label_source_opened": False}
        if destination.exists() or integrity_path.exists():
            if not (destination.exists() and integrity_path.exists()):
                raise ResumeError("partial rank part state")
            verify_integrity(
                integrity_path, {"rank_part": destination}, signature=signature,
                expected_metadata=expected_part_metadata,
            )
            require_same_frame(frame, pd.read_parquet(destination), "rank part")
            continue
        atomic_to_parquet(destination, frame)
        write_integrity(
            integrity_path, {"rank_part": destination}, signature=signature,
            metadata=expected_part_metadata,
        )
        progress_update(contract, "SCORING", users_complete=stop, users_total=len(user_keys))
    rank_path = output_path(contract, "rank")
    rank_integrity = output_path(contract, "rank_integrity")
    combined = pd.concat([pd.read_parquet(path) for path in sorted(expected_parts)], ignore_index=True)
    expected_rows = len(user_keys) * int(contract["cohort"]["panels"]) * 2 * len(contract["features"]["heads"]) * len(contract["cells"])
    if len(combined) != expected_rows:
        raise RuntimeError("combined score rank cardinality drift")
    metadata = {
        "users": len(user_keys), "rows": len(combined), "inactive_rows": int((~combined["active"].astype(bool)).sum()),
        "label_source_opened": False, "b0_or_target_popularity_opened": False,
    }
    if rank_path.exists() or rank_integrity.exists():
        if not (rank_path.exists() and rank_integrity.exists()):
            raise ResumeError("partial combined rank state")
        verify_integrity(
            rank_integrity, {"score_rank": rank_path}, signature=signature, expected_metadata=metadata,
        )
        require_same_frame(combined, pd.read_parquet(rank_path), "combined score rank")
        validate_progress(contract, minimum_phase="RANK_SEALED")
        return {"status": "REUSED_EXACT_SCORE_RANK", **metadata}
    atomic_to_parquet(rank_path, combined)
    write_integrity(rank_integrity, {"score_rank": rank_path}, signature=signature, metadata=metadata)
    progress_update(contract, "RANK_SEALED", **metadata)
    return {"status": "RANK_SEALED", **metadata}


def q_from_hist(rating_indices: Sequence[int], full_hist: Sequence[int]) -> np.ndarray:
    indices = np.asarray(rating_indices, dtype=np.int64)
    hist = np.asarray(full_hist, dtype=np.float64)
    if hist.shape != (10,) or hist.sum() <= 0 or np.any(indices < 0) or np.any(indices >= 10):
        raise ValueError("label histogram drift")
    below = np.cumsum(hist) - hist
    return (below[indices] + 0.5 * hist[indices]) / hist.sum()


def analytic_random_top2(q_values: Sequence[float]) -> tuple[float, float]:
    q = np.asarray(q_values, dtype=np.float64)
    if len(q) < 2 or not np.isfinite(q).all():
        raise ValueError("judged slate must contain at least two finite q labels")
    utility_total = 0.0
    loss_total = 0.0
    pairs = 0
    for left in range(len(q) - 1):
        for right in range(left + 1, len(q)):
            utility_total += float((q[left] + q[right]) / 2.0)
            loss_total += float(1.0 - min(q[left], q[right]))
            pairs += 1
    return utility_total / pairs, loss_total / pairs


def _metric_integrities(contract: Mapping[str, Any]) -> dict[str, tuple[Path, Path, str]]:
    return {
        "evaluation_labels": (
            output_path(contract, "evaluation_labels"), output_path(contract, "evaluation_labels_integrity"), "evaluation_labels",
        ),
        "panel_metrics": (
            output_path(contract, "panel_metrics"), output_path(contract, "panel_metrics_integrity"), "panel_metrics",
        ),
        "user_contrasts": (
            output_path(contract, "user_contrasts"), output_path(contract, "user_contrasts_integrity"), "user_contrasts",
        ),
    }


def _evaluation_label_pass(contract: Mapping[str, Any], score_input: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    eligible_keys = set(score_input["user_key"].astype(str).unique().tolist())
    selected_by_key: dict[str, set[int]] = {key: set() for key in eligible_keys}
    for value in score_input.itertuples(index=False):
        key = str(value.user_key)
        selected_by_key[key].update(int(movie) for movie in value.target_movie_ids)
        selected_by_key[key].update(int(movie) for movie in value.control_movie_ids)
    hist = {key: np.zeros(10, dtype=np.uint32) for key in eligible_keys}
    selected_ratings: dict[str, dict[int, int]] = {key: {} for key in eligible_keys}
    parsed = 0
    current_raw_user = -1
    current_key = ""
    for raw_user, movie, raw_line, second in movie_lens_movie_rows(contract):
        if int(raw_user) != current_raw_user:
            current_raw_user = int(raw_user)
            current_key = user_key(current_raw_user)
        if current_key not in eligible_keys:
            continue
        rating_index = _rating_index_after_movie(raw_line, second)
        parsed += 1
        hist[current_key][rating_index] += 1
        if int(movie) in selected_by_key[current_key]:
            selected_ratings[current_key][int(movie)] = rating_index
    rows: list[dict[str, Any]] = []
    for value in score_input.itertuples(index=False):
        key = str(value.user_key)
        target = [int(movie) for movie in value.target_movie_ids]
        control = [int(movie) for movie in value.control_movie_ids]
        if not set(target + control).issubset(selected_ratings[key]):
            raise RuntimeError("evaluation target/control rating completeness drift")
        rows.append({
            "user_key": key,
            "panel": int(value.panel),
            "full_rating_hist": hist[key].tolist(),
            "target_movie_ids": target,
            "target_rating_idx": [selected_ratings[key][movie] for movie in target],
            "control_movie_ids": control,
            "control_rating_idx": [selected_ratings[key][movie] for movie in control],
        })
    frame = pd.DataFrame(rows).sort_values(["user_key", "panel"], kind="stable", ignore_index=True)
    return frame, {
        "eligible_rating_rows_parsed_after_rank_seal": parsed,
        "evaluation_labels_opened_after_rank_seal": True,
        "timestamp_bytes_parsed": 0,
    }


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return [_jsonable_value(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_jsonable_value(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def frame_semantic_digest(frame: pd.DataFrame) -> str:
    payload = {
        "columns": [str(column) for column in frame.columns],
        "records": [[_jsonable_value(value) for value in row] for row in frame.itertuples(index=False, name=None)],
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def require_same_frame(expected: pd.DataFrame, actual: pd.DataFrame, name: str) -> None:
    if frame_semantic_digest(expected) != frame_semantic_digest(actual):
        raise ResumeError(f"{name} semantic drift")


def build_evaluation_labels(labels: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for value in labels.itertuples(index=False):
        for domain in ("TARGET", "CONTROL"):
            prefix = domain.lower()
            movies = [int(movie) for movie in getattr(value, f"{prefix}_movie_ids")]
            q = q_from_hist(getattr(value, f"{prefix}_rating_idx"), value.full_rating_hist)
            rows.append({
                "user_key": str(value.user_key), "panel": int(value.panel), "domain": domain,
                "movie_ids": movies, "q": q.tolist(),
            })
    return pd.DataFrame(rows).sort_values(["user_key", "panel", "domain"], kind="stable", ignore_index=True)


def build_panel_metrics(ranks: pd.DataFrame, evaluation_labels: pd.DataFrame) -> pd.DataFrame:
    label_lookup: dict[tuple[str, int, str], tuple[list[int], np.ndarray]] = {}
    for value in evaluation_labels.itertuples(index=False):
        label_lookup[(str(value.user_key), int(value.panel), str(value.domain))] = (
            [int(movie) for movie in value.movie_ids], np.asarray(value.q, dtype=np.float64),
        )
    rows: list[dict[str, Any]] = []
    for value in ranks.itertuples(index=False):
        movies, q = label_lookup[(str(value.user_key), int(value.panel), str(value.domain))]
        random_utility, random_loss = analytic_random_top2(q)
        if bool(value.active):
            q_by_movie = {movie: float(label) for movie, label in zip(movies, q, strict=True)}
            ranked = [int(movie) for movie in value.ranked_movie_ids]
            if len(ranked) != len(movies) or set(ranked) != set(movies):
                raise RuntimeError("sealed ranking/label identity drift")
            top_q = np.asarray([q_by_movie[movie] for movie in ranked[:2]], dtype=np.float64)
            model_utility = float(top_q.mean())
            model_loss = float(1.0 - top_q.min())
        else:
            model_utility, model_loss = random_utility, random_loss
        rows.append({
            "user_key": str(value.user_key), "panel": int(value.panel), "domain": str(value.domain),
            "head": str(value.head), "encoding": str(value.encoding), "k": int(value.k), "active": bool(value.active),
            "model_utility": model_utility, "random_utility": random_utility,
            "model_loss": model_loss, "random_loss": random_loss,
            "utility_improvement": model_utility - random_utility,
            "safety_improvement": random_loss - model_loss,
        })
    return pd.DataFrame(rows).sort_values(
        ["user_key", "panel", "domain", "head", "encoding", "k"], kind="stable", ignore_index=True,
    )


def build_user_contrasts(panel_metrics: pd.DataFrame, contract: Mapping[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    metadata = contrast_metadata(contract)
    grouped = panel_metrics.set_index(["user_key", "panel", "domain", "head", "encoding", "k"])
    for key in sorted(panel_metrics["user_key"].unique().tolist()):
        for meta in metadata:
            endpoint_column = "utility_improvement" if meta["endpoint"] == ENDPOINTS[0] else "safety_improvement"
            values: list[float] = []
            for panel in range(int(contract["cohort"]["panels"])):
                target_value = float(grouped.loc[(key, panel, "TARGET", meta["head"], meta["encoding"], meta["k"]), endpoint_column])
                control_value = float(grouped.loc[(key, panel, "CONTROL", meta["head"], meta["encoding"], meta["k"]), endpoint_column])
                if meta["class"] == "TARGET_IMPROVEMENT":
                    values.append(target_value)
                elif meta["class"] == "CONTROL_IMPROVEMENT":
                    values.append(control_value)
                else:
                    values.append(target_value - control_value)
            rows.append({"user_key": key, **meta, "value": float(np.mean(values))})
    return pd.DataFrame(rows).sort_values(["user_key", "contrast_index"], kind="stable", ignore_index=True)


def materialize_metrics(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    score(contract)
    rank_path = output_path(contract, "rank")
    rank_seal = verify_integrity(
        output_path(contract, "rank_integrity"), {"score_rank": rank_path}, signature=signature,
    )
    if rank_seal["metadata"].get("label_source_opened") is not False:
        raise ResumeError("rank was not sealed behind the label firewall")
    ranks = pd.read_parquet(rank_path)
    expected_users = int(contract["preflight"]["expected_users"])
    expected_rank_rows = expected_users * int(contract["cohort"]["panels"]) * 2 * len(contract["features"]["heads"]) * len(contract["cells"])
    if len(ranks) != expected_rank_rows or ranks.duplicated(["user_key", "panel", "domain", "head", "encoding", "k"]).any():
        raise RuntimeError("sealed rank identity drift")
    expected_rank_metadata = {
        "users": expected_users, "rows": expected_rank_rows, "inactive_rows": int((~ranks["active"].astype(bool)).sum()),
        "label_source_opened": False, "b0_or_target_popularity_opened": False,
    }
    verify_integrity(
        output_path(contract, "rank_integrity"), {"score_rank": rank_path},
        signature=signature, expected_metadata=expected_rank_metadata,
    )
    label_path = output_path(contract, "label_source")
    states = _metric_integrities(contract)
    present = [label_path.exists(), *(path.exists() for pair in states.values() for path in pair[:2])]
    score_input = pd.read_parquet(output_path(contract, "score_input"))
    labels, label_reader_metadata = _evaluation_label_pass(contract, score_input)
    evaluation_labels = build_evaluation_labels(labels)
    panel_metrics = build_panel_metrics(ranks, evaluation_labels)
    user_contrasts = build_user_contrasts(panel_metrics, contract)
    common_metadata = {
        "rank_sealed_before_label_open": True,
        **label_reader_metadata,
        "users": expected_users,
        "panels": int(contract["cohort"]["panels"]),
        "primary_n": 2,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
    }
    evaluation_metadata = {**common_metadata, "rows": len(evaluation_labels)}
    panel_metadata = {**common_metadata, "rows": len(panel_metrics)}
    contrast_metadata_sha = hashlib.sha256(canonical_json_bytes(contrast_metadata(contract))).hexdigest()
    contrast_integrity_metadata = {
        **common_metadata, "rows": len(user_contrasts), "contrasts": 108,
        "contrast_metadata_sha256": contrast_metadata_sha,
    }
    if any(present):
        if not all(present):
            raise ResumeError("partial metric materialization state")
        stored_labels = pd.read_parquet(label_path)
        stored_evaluation = pd.read_parquet(states["evaluation_labels"][0])
        stored_panel = pd.read_parquet(states["panel_metrics"][0])
        stored_contrasts = pd.read_parquet(states["user_contrasts"][0])
        require_same_frame(labels, stored_labels, "label source")
        require_same_frame(evaluation_labels, stored_evaluation, "evaluation labels")
        require_same_frame(panel_metrics, stored_panel, "panel metrics")
        require_same_frame(user_contrasts, stored_contrasts, "user contrasts")
        verify_integrity(
            states["evaluation_labels"][1], {"label_source": label_path, "evaluation_labels": states["evaluation_labels"][0]},
            signature=signature, expected_metadata=evaluation_metadata,
        )
        verify_integrity(
            states["panel_metrics"][1], {"panel_metrics": states["panel_metrics"][0]},
            signature=signature, expected_metadata=panel_metadata,
        )
        verify_integrity(
            states["user_contrasts"][1], {"user_contrasts": states["user_contrasts"][0]},
            signature=signature, expected_metadata=contrast_integrity_metadata,
        )
        validate_progress(contract, minimum_phase="METRICS_SEALED")
        return {"status": "REUSED_EXACT_METRICS", **contrast_integrity_metadata}
    if len(user_contrasts) != expected_users * 108 or len(contrast_metadata(contract)) != 108:
        raise RuntimeError("user contrast family cardinality drift")
    atomic_to_parquet(label_path, labels)
    atomic_to_parquet(states["evaluation_labels"][0], evaluation_labels)
    atomic_to_parquet(states["panel_metrics"][0], panel_metrics)
    atomic_to_parquet(states["user_contrasts"][0], user_contrasts)
    write_integrity(
        states["evaluation_labels"][1], {
            "label_source": label_path, "evaluation_labels": states["evaluation_labels"][0],
        },
        signature=signature, metadata=evaluation_metadata,
    )
    write_integrity(
        states["panel_metrics"][1], {"panel_metrics": states["panel_metrics"][0]},
        signature=signature, metadata=panel_metadata,
    )
    write_integrity(
        states["user_contrasts"][1], {"user_contrasts": states["user_contrasts"][0]},
        signature=signature, metadata=contrast_integrity_metadata,
    )
    progress_update(contract, "METRICS_SEALED", users=expected_users, contrasts=108)
    return {"status": "METRICS_SEALED", "users": expected_users, "contrasts": 108}


def contrast_metadata(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for head in contract["features"]["heads"]:
        for cell in contract["cells"]:
            for contrast_class in CLASSES:
                for endpoint in ENDPOINTS:
                    rows.append({
                        "contrast_index": len(rows), "head": str(head), "encoding": str(cell["encoding"]),
                        "k": int(cell["k"]), "class": contrast_class, "endpoint": endpoint,
                    })
    return rows


def poisson_user_weight(evidence_id: str, attempt: int, key: str, cutoffs: Sequence[int]) -> tuple[int, int]:
    payload = f"feelm-bootstrap-v1|rec-ev-023ef-user-bootstrap-v1|{evidence_id}|{canonical_decimal(attempt)}|user|{key}".encode("utf-8")
    x_value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)
    return bisect.bisect_left(cutoffs, x_value), x_value


def poisson_cutoffs(*, precision: int) -> list[int]:
    with localcontext() as context:
        context.prec = int(precision)
        probability_zero = (-Decimal(1)).exp()
        term = Decimal(1)
        cumulative_sum = term
        cutoffs: list[int] = []
        denominator = Decimal(2**65)
        for k in range(64):
            cdf = probability_zero * cumulative_sum
            cutoff = int(((cdf * denominator) - Decimal(1)) // Decimal(2))
            cutoffs.append(min(cutoff, 2**64 - 1))
            if cutoffs[-1] >= 2**64 - 1:
                return cutoffs
            term /= Decimal(k + 1)
            cumulative_sum += term
    raise RuntimeError("Poisson(1) inverse CDF cutoff did not converge")


def verify_poisson_golden(contract: Mapping[str, Any], cutoffs: Sequence[int]) -> None:
    joint = read_json(resolve_input(contract["allowed_input_artifacts"]["joint_contract"]))
    fixtures = [row for row in joint["statistics"]["poisson_golden_fixtures"] if row["evidence_id"] == contract["evidence_id"]]
    if len(fixtures) != 2:
        raise RuntimeError("Poisson golden fixture set drift")
    for row in fixtures:
        weight, x_value = poisson_user_weight(
            str(row["evidence_id"]), int(row["attempt"]), str(row["user_key"]), cutoffs,
        )
        if weight != int(row["weight"]) or x_value != int(row["uint64"]):
            raise RuntimeError("Poisson golden fixture drift")


def contrast_matrix(contract: Mapping[str, Any], frame: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    expected_meta = contrast_metadata(contract)
    actual_meta = (
        frame[["contrast_index", "head", "encoding", "k", "class", "endpoint"]]
        .drop_duplicates().sort_values("contrast_index", kind="stable").to_dict("records")
    )
    if actual_meta != expected_meta:
        raise RuntimeError("contrast metadata drift")
    keys = sorted(frame["user_key"].astype(str).unique().tolist())
    ordered = frame.sort_values(["user_key", "contrast_index"], kind="stable", ignore_index=True)
    if len(ordered) != len(keys) * 108 or ordered.duplicated(["user_key", "contrast_index"]).any():
        raise RuntimeError("contrast matrix identity drift")
    values = ordered["value"].to_numpy(dtype=np.float64).reshape(len(keys), 108)
    if not np.isfinite(values).all():
        raise RuntimeError("nonfinite user contrast")
    return keys, values


def compute_bootstrap_arrays(
    contract: Mapping[str, Any], keys: Sequence[str], values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    values = np.asarray(values, dtype=np.float64)
    if values.shape != (len(keys), 108) or not np.isfinite(values).all():
        raise RuntimeError("bootstrap input matrix drift")
    point = values.mean(axis=0)
    cutoffs = poisson_cutoffs(precision=80)
    verify_poisson_golden(contract, cutoffs)
    replicates: list[np.ndarray] = []
    valid_attempts: list[int] = []
    invalid_attempts: list[int] = []
    start, stop = (int(value) for value in contract["statistics"]["attempts"])
    target_valid = int(contract["statistics"]["valid_replicates"])
    for attempt in range(start, stop + 1):
        weights = np.fromiter(
            (poisson_user_weight(str(contract["evidence_id"]), attempt, key, cutoffs)[0] for key in keys),
            dtype=np.float64, count=len(keys),
        )
        denominator = float(weights.sum())
        if not math.isfinite(denominator) or denominator <= 0:
            invalid_attempts.append(attempt)
            continue
        estimate = (weights @ values) / denominator
        if not np.isfinite(estimate).all():
            invalid_attempts.append(attempt)
            continue
        valid_attempts.append(attempt)
        replicates.append(np.asarray(estimate, dtype=np.float64))
        if len(replicates) == target_valid:
            break
    if len(replicates) != target_valid:
        raise RuntimeError("fewer than 4,000 valid user bootstrap replicates")
    matrix = np.vstack(replicates).astype(np.float64)
    valid_array = np.asarray(valid_attempts, dtype=np.int32)
    invalid_array = np.asarray(invalid_attempts, dtype=np.int32)
    metadata = {
        "users": len(keys), "contrasts": 108, "valid_replicates": len(replicates),
        "invalid_attempts": len(invalid_attempts), "first_valid_attempt": valid_attempts[0],
        "last_valid_attempt": valid_attempts[-1], "poisson_golden_verified": True,
        "primary_regime": "USER_ONLY",
    }
    return point.astype(np.float64), matrix, valid_array, invalid_array, metadata


def bootstrap(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    materialize_metrics(contract)
    contrasts_path = output_path(contract, "user_contrasts")
    contrasts = pd.read_parquet(contrasts_path)
    keys, values = contrast_matrix(contract, contrasts)
    point, matrix, valid_attempts, invalid_attempts, metadata = compute_bootstrap_arrays(contract, keys, values)
    destination = output_path(contract, "bootstrap")
    integrity_path = output_path(contract, "bootstrap_integrity")
    if destination.exists() or integrity_path.exists():
        if not (destination.exists() and integrity_path.exists()):
            raise ResumeError("partial bootstrap state")
        verify_integrity(
            integrity_path, {"bootstrap": destination}, signature=signature, expected_metadata=metadata,
        )
        with np.load(destination, allow_pickle=False) as cached:
            if set(cached.files) != {"point", "replicates", "valid_attempt_ids", "invalid_attempt_ids"}:
                raise ResumeError("bootstrap artifact schema drift")
            matches = (
                np.array_equal(cached["point"], point)
                and np.array_equal(cached["replicates"], matrix)
                and np.array_equal(cached["valid_attempt_ids"], valid_attempts)
                and np.array_equal(cached["invalid_attempt_ids"], invalid_attempts)
            )
        if not matches:
            raise ResumeError("bootstrap deterministic recomputation drift")
        validate_progress(contract, minimum_phase="BOOTSTRAP_SEALED")
        return {"status": "REUSED_EXACT_BOOTSTRAP", **metadata}
    atomic_save_npz(
        destination, point=point, replicates=matrix,
        valid_attempt_ids=valid_attempts, invalid_attempt_ids=invalid_attempts,
    )
    write_integrity(integrity_path, {"bootstrap": destination}, signature=signature, metadata=metadata)
    progress_update(contract, "BOOTSTRAP_SEALED", **metadata)
    return {"status": "BOOTSTRAP_SEALED", **metadata}


def nearest_rank(values: Sequence[float], probability: float) -> float:
    array = np.sort(np.asarray(values, dtype=np.float64))
    if len(array) == 0 or not 0 < probability <= 1:
        raise ValueError("invalid nearest-rank input")
    return float(array[math.ceil(probability * len(array)) - 1])


def simultaneous_intervals(point: np.ndarray, replicates: np.ndarray) -> tuple[list[dict[str, Any]], float]:
    point = np.asarray(point, dtype=np.float64)
    matrix = np.asarray(replicates, dtype=np.float64)
    if point.shape != (108,) or matrix.ndim != 2 or matrix.shape[1] != 108:
        raise ValueError("simultaneous family shape drift")
    se = matrix.std(axis=0, ddof=1)
    estimable = np.isfinite(se) & (se > 0)
    critical = 0.0
    if bool(estimable.any()):
        maxima = np.max(np.abs((matrix[:, estimable] - point[estimable]) / se[estimable]), axis=1)
        critical = nearest_rank(maxima, 0.975)
    rows: list[dict[str, Any]] = []
    for index in range(108):
        width = float(critical * se[index]) if estimable[index] else None
        rows.append({
            "contrast_index": index, "mean": float(point[index]), "se": float(se[index]) if np.isfinite(se[index]) else None,
            "estimable": bool(estimable[index]), "half_width": width,
            "low": float(point[index] - width) if width is not None else None,
            "high": float(point[index] + width) if width is not None else None,
        })
    return rows, critical


def decision_from_intervals(
    contract: Mapping[str, Any], interval_rows: Sequence[Mapping[str, Any]], panel_metrics: pd.DataFrame,
) -> dict[str, Any]:
    metadata = contrast_metadata(contract)
    intervals = [{**meta, **dict(interval_rows[index])} for index, meta in enumerate(metadata)]
    lookup = {(row["head"], row["encoding"], int(row["k"]), row["class"], row["endpoint"]): row for row in intervals}
    panel_means: dict[tuple[str, str, int, str, str, int], float] = {}
    for (head, encoding, k, panel), group in panel_metrics.groupby(["head", "encoding", "k", "panel"], sort=False):
        target = group.loc[group["domain"] == "TARGET"]
        control = group.loc[group["domain"] == "CONTROL"]
        for endpoint, column in ((ENDPOINTS[0], "utility_improvement"), (ENDPOINTS[1], "safety_improvement")):
            target_mean = float(target[column].mean())
            control_mean = float(control[column].mean())
            panel_means[(str(head), str(encoding), int(k), "TARGET_IMPROVEMENT", endpoint, int(panel))] = target_mean
            panel_means[(str(head), str(encoding), int(k), "CONTROL_IMPROVEMENT", endpoint, int(panel))] = control_mean
            panel_means[(str(head), str(encoding), int(k), "CONDITIONAL_GAP", endpoint, int(panel))] = target_mean - control_mean
    cell_truth: list[dict[str, Any]] = []
    maximum_width = float(contract["decision"]["maximum_half_width"])
    for head in contract["features"]["heads"]:
        for cell in contract["cells"]:
            encoding, k = str(cell["encoding"]), int(cell["k"])
            target_rows = [lookup[(head, encoding, k, "TARGET_IMPROVEMENT", endpoint)] for endpoint in ENDPOINTS]
            gap_rows = [lookup[(head, encoding, k, "CONDITIONAL_GAP", endpoint)] for endpoint in ENDPOINTS]
            target_panels = [
                panel_means[(head, encoding, k, "TARGET_IMPROVEMENT", endpoint, panel)]
                for endpoint in ENDPOINTS for panel in range(4)
            ]
            gap_panels = [
                panel_means[(head, encoding, k, "CONDITIONAL_GAP", endpoint, panel)]
                for endpoint in ENDPOINTS for panel in range(4)
            ]
            target_pass = all(
                row["estimable"] and float(row["low"]) >= float(contract["decision"]["target_margin"])
                and float(row["half_width"]) <= maximum_width for row in target_rows
            ) and all(value > 0 for value in target_panels)
            gap_pass = all(
                row["estimable"] and float(row["low"]) >= float(contract["decision"]["gap_noninferiority_margin"])
                and float(row["half_width"]) <= maximum_width for row in gap_rows
            ) and all(value >= float(contract["decision"]["gap_noninferiority_margin"]) for value in gap_panels)
            cell_truth.append({
                "head": head, "encoding": encoding, "k": k,
                "target_signal": target_pass, "conditional_noninferiority": gap_pass,
                "target_panel_points": target_panels, "gap_panel_points": gap_panels,
            })
    head_truth: list[dict[str, Any]] = []
    for head in contract["decision"]["head_hierarchy"]:
        rows = [row for row in cell_truth if row["head"] == head]
        head_truth.append({
            "head": head,
            "all_six_target_signal": len(rows) == 6 and all(row["target_signal"] for row in rows),
            "all_six_conditional_noninferior": len(rows) == 6 and all(row["conditional_noninferiority"] for row in rows),
        })
    relevant = [row for row in intervals if row["class"] in {"TARGET_IMPROVEMENT", "CONDITIONAL_GAP"}]
    imprecise = any(
        not row["estimable"] or row["half_width"] is None or float(row["half_width"]) > maximum_width
        for row in relevant
    )
    first_joint = next((row["head"] for row in head_truth if row["all_six_target_signal"] and row["all_six_conditional_noninferior"]), None)
    first_target = next((row["head"] for row in head_truth if row["all_six_target_signal"]), None)
    if contract["preflight"]["floor_status"] != "FEASIBLE_PRELABEL":
        status = "INFEASIBLE_PRELABEL"
    elif imprecise:
        status = "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE"
    elif first_joint is not None:
        status = "TARGET_SIGNAL_AND_CONDITIONAL_NONINFERIOR"
    elif first_target is not None:
        status = "TARGET_SIGNAL_WITH_CONDITIONAL_GAP_UNRESOLVED_OR_DEGRADED"
    else:
        status = "NO_ROBUST_TARGET_SIGNAL"
    return {
        "status": status, "cell_truth": cell_truth, "head_truth": head_truth,
        "first_hierarchical_joint_pass": first_joint, "first_hierarchical_target_pass": first_target,
        "precision_or_estimability_failure": imprecise,
    }


def exposure_concentration(label_source: pd.DataFrame) -> dict[str, Any]:
    degree: dict[int, int] = {}
    for movies in label_source["target_movie_ids"]:
        for movie in movies:
            degree[int(movie)] = degree.get(int(movie), 0) + 1
    values = np.asarray(list(degree.values()), dtype=np.int64)
    return {
        "unique_selected_target_items": len(degree),
        "selected_target_memberships": int(values.sum()),
        "maximum_target_degree": int(values.max(initial=0)),
        "top10_target_degree_sum": int(np.sort(values)[-10:].sum()) if len(values) else 0,
        "interpretation": "DESCRIPTIVE_ONLY_NO_ITEM_GENERALIZATION",
    }


def finalize_or_verify_result(
    result_path: Path,
    selection_path: Path,
    integrity_path: Path,
    *,
    result: Mapping[str, Any],
    selection: Mapping[str, Any],
    signature: str,
    metadata: Mapping[str, Any],
) -> str:
    present = [result_path.exists(), selection_path.exists(), integrity_path.exists()]
    if any(present):
        if not all(present):
            raise ResumeError("partial result state")
        verify_integrity(
            integrity_path,
            {"result": result_path, "selection": selection_path},
            signature=signature,
            expected_metadata=metadata,
        )
        if read_json(result_path) != dict(result) or read_json(selection_path) != dict(selection):
            raise ResumeError("completed result or selection semantic drift")
        return "REUSED_EXACT_RESULT"
    atomic_write_json(selection_path, selection)
    atomic_write_json(result_path, result)
    write_integrity(
        integrity_path,
        {"result": result_path, "selection": selection_path},
        signature=signature,
        metadata=metadata,
    )
    return "WROTE_RESULT"


def analyze(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    bootstrap(contract)
    expected_users = int(contract["preflight"]["expected_users"])
    label_path = output_path(contract, "label_source")
    evaluation_labels_path = output_path(contract, "evaluation_labels")
    panel_metrics_path = output_path(contract, "panel_metrics")
    contrasts_path = output_path(contract, "user_contrasts")
    bootstrap_path = output_path(contract, "bootstrap")
    verify_integrity(
        output_path(contract, "evaluation_labels_integrity"),
        {"label_source": label_path, "evaluation_labels": evaluation_labels_path},
        signature=signature,
    )
    verify_integrity(
        output_path(contract, "panel_metrics_integrity"), {"panel_metrics": panel_metrics_path}, signature=signature,
    )
    verify_integrity(
        output_path(contract, "user_contrasts_integrity"), {"user_contrasts": contrasts_path}, signature=signature,
    )
    verify_integrity(
        output_path(contract, "bootstrap_integrity"), {"bootstrap": bootstrap_path}, signature=signature,
    )
    labels = pd.read_parquet(label_path)
    evaluation_labels = pd.read_parquet(evaluation_labels_path)
    panel_metrics = pd.read_parquet(panel_metrics_path)
    contrasts = pd.read_parquet(contrasts_path)
    keys, values = contrast_matrix(contract, contrasts)
    if len(keys) != expected_users:
        raise RuntimeError("analysis user cardinality drift")
    unique_histories = labels.drop_duplicates("user_key", keep="first")
    parsed_after_rank = int(sum(sum(int(count) for count in row) for row in unique_histories["full_rating_hist"]))
    common_metadata = {
        "rank_sealed_before_label_open": True,
        "eligible_rating_rows_parsed_after_rank_seal": parsed_after_rank,
        "evaluation_labels_opened_after_rank_seal": True,
        "timestamp_bytes_parsed": 0,
        "users": expected_users,
        "panels": int(contract["cohort"]["panels"]),
        "primary_n": 2,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
    }
    expected_evaluation_rows = expected_users * int(contract["cohort"]["panels"]) * 2
    expected_metric_rows = expected_evaluation_rows * len(contract["features"]["heads"]) * len(contract["cells"])
    if len(labels) != expected_users * int(contract["cohort"]["panels"]) or len(evaluation_labels) != expected_evaluation_rows:
        raise RuntimeError("evaluation label cardinality drift")
    if len(panel_metrics) != expected_metric_rows or len(contrasts) != expected_users * 108:
        raise RuntimeError("metric or contrast cardinality drift")
    verify_integrity(
        output_path(contract, "evaluation_labels_integrity"),
        {"label_source": label_path, "evaluation_labels": evaluation_labels_path},
        signature=signature,
        expected_metadata={**common_metadata, "rows": expected_evaluation_rows},
    )
    verify_integrity(
        output_path(contract, "panel_metrics_integrity"), {"panel_metrics": panel_metrics_path},
        signature=signature, expected_metadata={**common_metadata, "rows": expected_metric_rows},
    )
    contrast_metadata_sha = hashlib.sha256(canonical_json_bytes(contrast_metadata(contract))).hexdigest()
    verify_integrity(
        output_path(contract, "user_contrasts_integrity"), {"user_contrasts": contrasts_path},
        signature=signature,
        expected_metadata={
            **common_metadata, "rows": expected_users * 108, "contrasts": 108,
            "contrast_metadata_sha256": contrast_metadata_sha,
        },
    )
    expected_point, expected_replicates, expected_valid, expected_invalid, bootstrap_metadata = compute_bootstrap_arrays(
        contract, keys, values,
    )
    with np.load(bootstrap_path, allow_pickle=False) as cached:
        if set(cached.files) != {"point", "replicates", "valid_attempt_ids", "invalid_attempt_ids"}:
            raise RuntimeError("bootstrap artifact schema drift")
        matches = (
            np.array_equal(cached["point"], expected_point)
            and np.array_equal(cached["replicates"], expected_replicates)
            and np.array_equal(cached["valid_attempt_ids"], expected_valid)
            and np.array_equal(cached["invalid_attempt_ids"], expected_invalid)
        )
    if not matches:
        raise ResumeError("bootstrap deterministic recomputation drift before analysis")
    verify_integrity(
        output_path(contract, "bootstrap_integrity"), {"bootstrap": bootstrap_path},
        signature=signature, expected_metadata=bootstrap_metadata,
    )
    raw_intervals, critical = simultaneous_intervals(expected_point, expected_replicates)
    metadata = contrast_metadata(contract)
    intervals = [{**metadata[index], **row} for index, row in enumerate(raw_intervals)]
    decision = decision_from_intervals(contract, raw_intervals, panel_metrics)
    concentration = exposure_concentration(labels)
    selection = {
        "schema_version": 1,
        "evidence_id": contract["evidence_id"],
        **decision,
        "champion": None,
        "product_policy_updated": False,
        "final_reserve_opened": False,
        "old_locked_ratings_timestamps_metrics_opened": False,
    }
    result = {
        "schema_version": 1,
        "evidence_id": contract["evidence_id"],
        "status": decision["status"],
        "run_signature": signature,
        "purpose": contract["purpose"],
        "claim_boundary": contract["claim_boundary"],
        "selection": selection,
        "simultaneous_intervals": intervals,
        "critical_value_97_5_percent": critical,
        "bootstrap": bootstrap_metadata,
        "item_membership_concentration": concentration,
        "users": len(keys),
        "panels_per_user": int(contract["cohort"]["panels"]),
        "primary_n": 2,
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    result_metadata = {"status": decision["status"], "users": len(keys), "contrasts": 108, "champion": None}
    reuse_status = finalize_or_verify_result(
        output_path(contract, "result"), output_path(contract, "selection"), output_path(contract, "result_integrity"),
        result=result, selection=selection, signature=signature, metadata=result_metadata,
    )
    progress_update(contract, "COMPLETE", status=decision["status"], users=len(keys), contrasts=108)
    return {"status": decision["status"], "users": len(keys), "contrasts": 108, "result_state": reuse_status}


def load_contract(path: Path) -> dict[str, Any]:
    contract = read_json(path)
    _validator(contract)(contract)
    return contract


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, help="REC-EV-023E, REC-EV-023F, or a contract path")
    parser.add_argument("--phase", choices=("lock", "prepare", "score", "metrics", "bootstrap", "analyze", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract_path = DEFAULT_CONTRACTS.get(args.contract, Path(args.contract))
    contract_path = contract_path if contract_path.is_absolute() else (ROOT / contract_path).resolve()
    contract = load_contract(contract_path)
    if args.phase == "lock":
        value = create_or_verify_lock(contract, resume=args.resume)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    if not args.resume:
        raise ResumeError("all post-lock phases require --resume")
    create_or_verify_lock(contract, resume=True)
    functions = {
        "prepare": prepare,
        "score": score,
        "metrics": materialize_metrics,
        "bootstrap": bootstrap,
        "analyze": analyze,
    }
    if args.phase == "run":
        value = analyze(contract)
    else:
        value = functions[args.phase](contract)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
