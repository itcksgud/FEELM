#!/usr/bin/env python3
"""Run the locked REC-EV-025A/B common-support feature-transfer experiments."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import sparse

try:
    from rec_ev_022a_core import RATING_VALUES, canonical_decimal, encoding_weights, user_key
    from run_rec_ev_023ef_transfer import (
        ResumeError, _evaluation_label_pass, _group_matrix, _profile_rating_pass,
        active_scores, analytic_random_top2, atomic_save_npy, atomic_save_npz,
        atomic_save_sparse, atomic_to_parquet, atomic_write_json, build_evaluation_labels,
        canonical_json_bytes, implementation_rows, movie_lens_movie_rows, nearest_rank,
        poisson_cutoffs, read_json, require_same_frame, require_same_sparse, resolve_input,
        source_rows, verify_integrity, write_integrity,
    )
    from run_rec_ev_025ab_preflight import build_common_support, experiment_summary, partition_user
    from validate_rec_ev_025_feature_transfer_contract import validate_contract
except ImportError:
    from scripts.rec_ev_022a_core import RATING_VALUES, canonical_decimal, encoding_weights, user_key
    from scripts.run_rec_ev_023ef_transfer import (
        ResumeError, _evaluation_label_pass, _group_matrix, _profile_rating_pass,
        active_scores, analytic_random_top2, atomic_save_npy, atomic_save_npz,
        atomic_save_sparse, atomic_to_parquet, atomic_write_json, build_evaluation_labels,
        canonical_json_bytes, implementation_rows, movie_lens_movie_rows, nearest_rank,
        poisson_cutoffs, read_json, require_same_frame, require_same_sparse, resolve_input,
        source_rows, verify_integrity, write_integrity,
    )
    from scripts.run_rec_ev_025ab_preflight import build_common_support, experiment_summary, partition_user
    from scripts.validate_rec_ev_025_feature_transfer_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-025ab-feature-transfer-execution.json"
EVIDENCE_IDS = ("REC-EV-025A", "REC-EV-025B")
HEADS = ("GENRE_ONLY", "TRANSFER_NO_CONTEXT", "E5", "CURRENT_FULL")
CHALLENGERS = ("GENRE_ONLY", "TRANSFER_NO_CONTEXT", "E5")
DOMAINS = ("TARGET", "CONTROL")
ABSOLUTE_CLASSES = ("TARGET_IMPROVEMENT", "CONTROL_IMPROVEMENT", "CONDITIONAL_GAP")
ABSOLUTE_ENDPOINTS = ("UTILITY_IMPROVEMENT_MODEL_MINUS_RANDOM", "SAFETY_IMPROVEMENT_RANDOM_LOSS_MINUS_MODEL")
INCREMENTAL_ENDPOINTS = ("UTILITY_CHALLENGER_MINUS_CURRENT", "SAFETY_CHALLENGER_MINUS_CURRENT")
PHASE_INDEX = {"MEMBERSHIP_SEALED": 10, "SCORE_INPUT_OPEN": 20, "RANK_SEALED": 30, "EVALUATION_OPEN": 40, "METRICS_SEALED": 45, "BOOTSTRAP_SEALED": 50, "COMPLETE": 60}


def output_path(contract: Mapping[str, Any], evidence_id: str, name: str) -> Path:
    return ROOT / str(contract["output_roots"][evidence_id]) / str(contract["outputs"][name])


def locked_spec(contract: Mapping[str, Any], evidence_id: str) -> dict[str, Any]:
    keys = ("purpose", "design_audit", "preflight_audits", "authorization", "forbidden_input_artifacts", "reader", "common_support", "cells", "heads", "rating_scale", "phases", "scoring", "metrics", "statistics", "bootstrap_golden_fixtures", "decision", "claim_boundary", "resume", "invariants")
    value = {key: contract[key] for key in keys}
    value["evidence_id"] = evidence_id
    value["preflight"] = contract["preflight"][evidence_id]
    value["experiment"] = contract["experiments"][evidence_id]
    value["output_root"] = contract["output_roots"][evidence_id]
    return value


def expected_lock_state(contract: Mapping[str, Any], evidence_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_contract(contract)
    if evidence_id not in EVIDENCE_IDS:
        raise ValueError("unsupported evidence id")
    sources = source_rows(contract)
    implementations = implementation_rows(contract)
    manifest = {
        "schema_version": 1, "evidence_id": evidence_id, "sources": sources,
        "implementation_artifacts": implementations, "preflight_result_opened": True,
        "evaluation_labels_opened_at_lock": False,
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True,
        "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False,
        "product_policy_updated": False, "champion": None,
    }
    lock = {
        "schema_version": 1, "evidence_id": evidence_id, "status": "LOCKED_COMMON_SUPPORT_FEATURE_TRANSFER",
        "contract_sha256": hashlib.sha256(canonical_json_bytes(contract)).hexdigest(),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(canonical_json_bytes(locked_spec(contract, evidence_id))).hexdigest(),
        "source_manifest_sha256": hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        "evaluation_labels_opened_at_lock": False,
        "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False,
        "product_policy_updated": False, "champion": None,
    }
    return manifest, lock


def create_or_verify_lock(contract: Mapping[str, Any], evidence_id: str, *, resume: bool) -> dict[str, Any]:
    lock_path = output_path(contract, evidence_id, "protocol_lock")
    manifest_path = output_path(contract, evidence_id, "source_manifest")
    present = [lock_path.exists(), manifest_path.exists()]
    if any(present) and not all(present):
        raise ResumeError("partial protocol lock state")
    if resume and not any(present):
        raise ResumeError("--resume before protocol lock")
    manifest, lock = expected_lock_state(contract, evidence_id)
    if all(present):
        if not resume:
            raise ResumeError("protocol lock exists; use --resume")
        if read_json(lock_path) != lock or read_json(manifest_path) != manifest:
            raise ResumeError("protocol lock or manifest drift")
        return lock
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(lock_path, lock)
    return lock


def run_signature(contract: Mapping[str, Any], evidence_id: str) -> str:
    lock = read_json(output_path(contract, evidence_id, "protocol_lock"))
    payload = {key: lock[key] for key in ("contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256")}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def progress_value(contract: Mapping[str, Any], evidence_id: str, phase: str, completed_units: int = 0) -> dict[str, Any]:
    return {
        "schema_version": 1, "evidence_id": evidence_id, "run_signature": run_signature(contract, evidence_id),
        "phase": phase, "phase_index": PHASE_INDEX[phase], "completed_units": int(completed_units),
        "evaluation_labels_opened": PHASE_INDEX[phase] >= PHASE_INDEX["EVALUATION_OPEN"],
        "all_four_head_rank_sealed_before_evaluation": PHASE_INDEX[phase] >= PHASE_INDEX["EVALUATION_OPEN"],
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True,
        "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False,
        "product_policy_updated": False, "champion": None,
    }


def validate_progress(contract: Mapping[str, Any], evidence_id: str, minimum_phase: str | None = None) -> dict[str, Any]:
    path = output_path(contract, evidence_id, "progress")
    if not path.is_file():
        raise ResumeError("progress missing")
    value = read_json(path)
    phase = str(value.get("phase"))
    if phase not in PHASE_INDEX or value != progress_value(contract, evidence_id, phase, int(value.get("completed_units", -1))):
        raise ResumeError("progress schema or invariant drift")
    if minimum_phase is not None and PHASE_INDEX[phase] < PHASE_INDEX[minimum_phase]:
        raise ResumeError("progress behind sealed artifact")
    return value


def progress_index(contract: Mapping[str, Any], evidence_id: str) -> int:
    path = output_path(contract, evidence_id, "progress")
    return PHASE_INDEX[str(validate_progress(contract, evidence_id)["phase"])] if path.is_file() else -1


def sealed_group_state(contract: Mapping[str, Any], evidence_id: str, phase: str, paths: Sequence[Path]) -> bool:
    present = [path.exists() for path in paths]
    if any(present) and not all(present):
        raise ResumeError(f"partial {phase} artifact state")
    if not all(present) and progress_index(contract, evidence_id) >= PHASE_INDEX[phase]:
        raise ResumeError(f"progress ahead of absent {phase} artifacts")
    return all(present)


def reconcile_progress(contract: Mapping[str, Any], evidence_id: str, phase: str, completed_units: int = 0) -> None:
    path = output_path(contract, evidence_id, "progress")
    if not path.is_file():
        atomic_write_json(path, progress_value(contract, evidence_id, phase, completed_units))
        return
    previous = validate_progress(contract, evidence_id)
    prior_index, target_index = PHASE_INDEX[str(previous["phase"])], PHASE_INDEX[phase]
    if prior_index < target_index:
        atomic_write_json(path, progress_value(contract, evidence_id, phase, completed_units))
    elif prior_index == target_index and int(previous["completed_units"]) != int(completed_units):
        raise ResumeError("progress completed-unit drift")


def _normalize_concat(groups: Sequence[sparse.csr_matrix], scale: float) -> sparse.csr_matrix:
    matrix = sparse.hstack([group * np.float32(scale) for group in groups], format="csr", dtype=np.float32)
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    inverse = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
    return (sparse.diags(inverse.astype(np.float32)) @ matrix).tocsr()


def build_feature_matrices(contract: Mapping[str, Any]) -> tuple[np.ndarray, dict[str, Any], np.ndarray, np.ndarray]:
    common_ids, common_years, common_korean, summary = build_common_support(contract)
    if len(common_ids) != int(contract["common_support"]["expected_items"]):
        raise RuntimeError("common support cardinality drift")
    structured = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["structured_features"]))
    frame = structured.set_index("movie_id", verify_integrity=True).reindex(common_ids)
    if frame["feature_eligible"].isna().any() or not frame["feature_eligible"].astype(bool).all():
        raise RuntimeError("common structured alignment drift")
    genre_rows: list[list[str]] = []
    context_rows: list[list[str]] = []
    people_rows: list[list[str]] = []
    keyword_rows: list[list[str]] = []
    def values(value: Any) -> list[int]:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return []
        return [int(item) for item in value]
    for row in frame.itertuples(index=False):
        decade = int(row.release_year) // 10 * 10
        runtime = int(row.runtime_minutes) // 30 if pd.notna(row.runtime_minutes) else None
        genre_rows.append([f"genre:{item}" for item in values(row.genre_ids)])
        context_rows.append(([f"language:{row.original_language}"] if pd.notna(row.original_language) else []) + [f"decade:{decade}"] + ([f"runtime30:{runtime}"] if runtime is not None else []))
        people_rows.append([f"director:{item}" for item in values(row.director_ids)] + [f"cast:{item}" for item in values(row.top5_cast_ids)])
        keyword_rows.append([f"keyword:{item}" for item in values(row.keyword_ids)])
    groups = {"G": _group_matrix(genre_rows), "C": _group_matrix(context_rows), "P": _group_matrix(people_rows), "W": _group_matrix(keyword_rows)}
    heads: dict[str, Any] = {
        "GENRE_ONLY": groups["G"].tocsr(),
        "TRANSFER_NO_CONTEXT": _normalize_concat((groups["G"], groups["P"], groups["W"]), 1.0 / 3.0),
        "CURRENT_FULL": _normalize_concat((groups["G"], groups["C"], groups["P"], groups["W"]), 0.25),
    }
    table = pq.read_table(resolve_input(contract["allowed_input_artifacts"]["text_embeddings"]), columns=["movie_id", "embedding"])
    text_ids = table.column("movie_id").to_numpy(zero_copy_only=False).astype(np.int64)
    flat = table.column("embedding").combine_chunks().values.to_numpy(zero_copy_only=False)
    vectors = np.asarray(flat, dtype=np.float32).reshape(len(text_ids), 384)
    lookup = np.full(int(text_ids.max(initial=0)) + 1, -1, dtype=np.int32)
    lookup[text_ids] = np.arange(len(text_ids), dtype=np.int32)
    positions = lookup[common_ids]
    if np.any(positions < 0):
        raise RuntimeError("common E5 alignment drift")
    heads["E5"] = vectors[positions].copy()
    if np.any(np.asarray(heads["CURRENT_FULL"].getnnz(axis=1)).ravel() == 0):
        raise RuntimeError("CURRENT_FULL zero in common support")
    if not np.isfinite(heads["E5"]).all() or np.any(np.abs(np.linalg.norm(heads["E5"].astype(np.float64), axis=1) - 1.0) > 0.0001):
        raise RuntimeError("aligned E5 validity drift")
    return common_ids.astype(np.int32), heads, common_years.astype(np.int16), common_korean.astype(bool)


def _movie_lookup(movie_ids: np.ndarray) -> np.ndarray:
    lookup = np.full(int(movie_ids.max(initial=0)) + 1, -1, dtype=np.int32)
    lookup[movie_ids] = np.arange(len(movie_ids), dtype=np.int32)
    return lookup


def domain_masks(evidence_id: str, years: np.ndarray, korean: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if evidence_id == "REC-EV-025A":
        return ~korean, korean
    return years < 2020, (years >= 2020) & (years <= 2023)


def eligible_and_pools(contract: Mapping[str, Any], evidence_id: str, movie_ids: np.ndarray, years: np.ndarray, korean: np.ndarray) -> tuple[np.ndarray, dict[int, list[int]], dict[int, list[int]], dict[str, int]]:
    lookup = _movie_lookup(movie_ids)
    source_mask, target_mask = domain_masks(evidence_id, years, korean)
    maximum = int(contract["reader"]["maximum_user_id"])
    source_counts = np.zeros(maximum + 1, dtype=np.uint32)
    target_counts = np.zeros_like(source_counts)
    allowed_rows = 0
    for raw_user, movie, _line, _second in movie_lens_movie_rows(contract):
        allowed_rows += 1
        position = int(lookup[movie]) if 0 <= movie < len(lookup) else -1
        if position >= 0:
            source_counts[raw_user] += int(source_mask[position])
            target_counts[raw_user] += int(target_mask[position])
    spec = contract["experiments"][evidence_id]
    mask = (source_counts >= int(spec["minimum_source_ratings"])) & (target_counts >= int(spec["minimum_target_ratings"]))
    eligible = np.flatnonzero(mask).astype(np.int32)
    observed_hash = hashlib.sha256(canonical_json_bytes(sorted(user_key(int(user)) for user in eligible))).hexdigest()
    preflight = contract["preflight"][evidence_id]
    if len(eligible) != int(preflight["expected_users"]) or observed_hash != preflight["eligible_user_key_set_sha256"]:
        raise RuntimeError("eligible user set drift")
    pools = {"source": {int(user): [] for user in eligible}, "target": {int(user): [] for user in eligible}}
    eligible_mask = np.zeros(maximum + 1, dtype=bool)
    eligible_mask[eligible] = True
    second_rows = 0
    for raw_user, movie, _line, _second in movie_lens_movie_rows(contract):
        if not eligible_mask[raw_user]:
            continue
        second_rows += 1
        position = int(lookup[movie]) if 0 <= movie < len(lookup) else -1
        if position < 0:
            continue
        if source_mask[position]:
            pools["source"][int(raw_user)].append(int(movie))
        elif target_mask[position]:
            pools["target"][int(raw_user)].append(int(movie))
    return eligible, pools["source"], pools["target"], {"allowed_rows_movie_id_parsed_membership_first_pass": allowed_rows, "eligible_rows_movie_id_parsed_membership_second_pass": second_rows}


def verify_prior(contract: Mapping[str, Any]) -> np.ndarray:
    with np.load(resolve_input(contract["allowed_input_artifacts"]["train_prior"]), allow_pickle=False) as prior:
        if "g0_mid" not in prior.files:
            raise RuntimeError("g0_mid missing")
        values = prior["g0_mid"]
    expected = contract["rating_scale"]
    if list(values.shape) != expected["prior_shape"] or values.dtype.str != expected["prior_dtype"] or hashlib.sha256(values.tobytes(order="C")).hexdigest() != expected["prior_c_order_value_bytes_sha256"]:
        raise RuntimeError("g0_mid drift")
    return values.astype(np.float64)


def prepared_artifacts(contract: Mapping[str, Any], evidence_id: str) -> dict[str, Path]:
    return {name: output_path(contract, evidence_id, name) for name in ("item_ids", "feature_genre", "feature_transfer", "feature_e5", "feature_full", "score_input")}


def prepare(contract: Mapping[str, Any], evidence_id: str) -> dict[str, Any]:
    signature = run_signature(contract, evidence_id)
    artifacts = prepared_artifacts(contract, evidence_id)
    prepared_integrity = output_path(contract, evidence_id, "prepared_integrity")
    prepared_reuse = sealed_group_state(contract, evidence_id, "SCORE_INPUT_OPEN", [prepared_integrity, *artifacts.values()])
    membership_path = output_path(contract, evidence_id, "membership")
    membership_integrity = output_path(contract, evidence_id, "membership_integrity")
    membership_reuse = sealed_group_state(contract, evidence_id, "MEMBERSHIP_SEALED", [membership_path, membership_integrity])
    movie_ids, heads, years, korean = build_feature_matrices(contract)
    verify_prior(contract)
    eligible, source_pools, target_pools, reader = eligible_and_pools(contract, evidence_id, movie_ids, years, korean)
    spec = contract["experiments"][evidence_id]
    preflight_result = read_json(resolve_input(contract["allowed_input_artifacts"]["preflight_result"]))
    summary = experiment_summary(evidence_id, eligible, source_pools, target_pools, spec)
    if summary != preflight_result["experiments"][evidence_id]:
        raise RuntimeError("preflight reconstruction drift")
    memberships: list[dict[str, Any]] = []
    raw_memberships: list[dict[str, Any]] = []
    allowlist: dict[int, set[int]] = {int(user): set() for user in eligible}
    for raw_user in eligible.tolist():
        anonymous = user_key(int(raw_user))
        roles = partition_user(anonymous, source_pools[int(raw_user)], target_pools[int(raw_user)], spec)
        allowlist[int(raw_user)].update(int(movie) for movie in roles["profile"])
        for panel in roles["panels"]:
            row = {"user_key": anonymous, "panel": int(panel["panel"]), "profile_movie_ids": [int(movie) for movie in roles["profile"]], "target_movie_ids": [int(movie) for movie in panel["target"]], "control_movie_ids": [int(movie) for movie in panel["control"]]}
            memberships.append(row)
            raw_memberships.append({"raw_user": int(raw_user), **row})
    membership_frame = pd.DataFrame(memberships).sort_values(["user_key", "panel"], kind="stable", ignore_index=True)
    membership_metadata = {**reader, "eligible_users": len(eligible), "panels": len(membership_frame), "eligible_user_key_set_sha256": contract["preflight"][evidence_id]["eligible_user_key_set_sha256"], "preflight_experiment_summary_sha256": hashlib.sha256(canonical_json_bytes(summary)).hexdigest(), "rating_value_bytes_parsed": 0, "timestamp_bytes_parsed": 0, "profile_control_intersection": 0, "raw_user_ids_written": False}
    if membership_reuse:
        verify_integrity(membership_integrity, {"membership": membership_path}, signature=signature, expected_metadata=membership_metadata)
        require_same_frame(membership_frame, pd.read_parquet(membership_path), "membership")
    else:
        atomic_to_parquet(membership_path, membership_frame)
        write_integrity(membership_integrity, {"membership": membership_path}, signature=signature, metadata=membership_metadata)
    reconcile_progress(contract, evidence_id, "MEMBERSHIP_SEALED", len(membership_frame))
    verify_integrity(membership_integrity, {"membership": membership_path}, signature=signature, expected_metadata=membership_metadata)
    ratings, rating_rows = _profile_rating_pass(contract, allowlist)
    score_rows: list[dict[str, Any]] = []
    for row in raw_memberships:
        profile = row["profile_movie_ids"]
        if set(profile) & (set(row["target_movie_ids"]) | set(row["control_movie_ids"])):
            raise RuntimeError("profile/evaluation overlap")
        score_rows.append({key: value for key, value in row.items() if key != "raw_user"} | {"profile_rating_idx": [ratings[row["raw_user"]][movie] for movie in profile]})
    score_input = pd.DataFrame(score_rows).sort_values(["user_key", "panel"], kind="stable", ignore_index=True)
    metadata = {**reader, "eligible_users": len(eligible), "panels": len(score_input), "universe_items": len(movie_ids), "selected_profile_rating_rows_parsed": rating_rows, "evaluation_rating_bytes_parsed_before_rank_seal": 0, "timestamp_bytes_parsed": 0, "profile_evaluation_intersection": 0, "evaluation_labels_opened_before_rank_seal": False, "raw_user_ids_written": False, "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False}
    if prepared_reuse:
        verify_integrity(prepared_integrity, artifacts, signature=signature, expected_metadata=metadata)
        if not np.array_equal(np.load(artifacts["item_ids"], allow_pickle=False), movie_ids):
            raise ResumeError("item ids drift")
        require_same_sparse(heads["GENRE_ONLY"], sparse.load_npz(artifacts["feature_genre"]), "GENRE_ONLY")
        require_same_sparse(heads["TRANSFER_NO_CONTEXT"], sparse.load_npz(artifacts["feature_transfer"]), "TRANSFER_NO_CONTEXT")
        require_same_sparse(heads["CURRENT_FULL"], sparse.load_npz(artifacts["feature_full"]), "CURRENT_FULL")
        if not np.array_equal(np.load(artifacts["feature_e5"], allow_pickle=False), heads["E5"]):
            raise ResumeError("E5 matrix drift")
        require_same_frame(score_input, pd.read_parquet(artifacts["score_input"]), "score input")
        reconcile_progress(contract, evidence_id, "SCORE_INPUT_OPEN", len(score_input))
        return {"status": "REUSED_EXACT_SCORE_INPUT", **metadata}
    atomic_save_npy(artifacts["item_ids"], movie_ids)
    atomic_save_sparse(artifacts["feature_genre"], heads["GENRE_ONLY"])
    atomic_save_sparse(artifacts["feature_transfer"], heads["TRANSFER_NO_CONTEXT"])
    atomic_save_npy(artifacts["feature_e5"], heads["E5"])
    atomic_save_sparse(artifacts["feature_full"], heads["CURRENT_FULL"])
    atomic_to_parquet(artifacts["score_input"], score_input)
    write_integrity(prepared_integrity, artifacts, signature=signature, metadata=metadata)
    reconcile_progress(contract, evidence_id, "SCORE_INPUT_OPEN", len(score_input))
    return {"status": "SCORE_INPUT_OPEN", **metadata}


def strict_head_order(contract: Mapping[str, Any], evidence_id: str, user: str, panel: int, domain: str, encoding: str, k: int, movie_ids: Sequence[int], scores: Sequence[float]) -> list[int]:
    movies = [int(movie) for movie in movie_ids]
    values = np.asarray(scores, dtype=np.float64)
    if len(movies) != len(values) or not np.isfinite(values).all():
        raise ValueError("rank input drift")
    prefix = str(contract["experiments"][evidence_id]["tie_prefix"])
    digests = [hashlib.sha256(f"{prefix}|{user}|{canonical_decimal(panel)}|{domain}|{encoding}|{canonical_decimal(k)}|{canonical_decimal(movie)}".encode("utf-8")).digest() for movie in movies]
    order = sorted(range(len(movies)), key=lambda index: (-float(values[index]), digests[index], movies[index]))
    return [movies[index] for index in order]


def build_rank_frame(contract: Mapping[str, Any], evidence_id: str, selected: pd.DataFrame, lookup: Mapping[int, int], matrices: Mapping[str, Any], prior: np.ndarray) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for value in selected.itertuples(index=False):
        profile_movies = [int(movie) for movie in value.profile_movie_ids]
        profile_ratings = np.asarray([float(RATING_VALUES[int(index)]) for index in value.profile_rating_idx], dtype=np.float64)
        profile_positions = np.asarray([lookup[movie] for movie in profile_movies], dtype=np.int64)
        for domain, candidates_value in (("TARGET", value.target_movie_ids), ("CONTROL", value.control_movie_ids)):
            candidates = [int(movie) for movie in candidates_value]
            candidate_positions = np.asarray([lookup[movie] for movie in candidates], dtype=np.int64)
            for head in HEADS:
                matrix = matrices[head]
                similarities = ((matrix[candidate_positions] @ matrix[profile_positions].T).toarray() if sparse.issparse(matrix) else matrix[candidate_positions] @ matrix[profile_positions].T).astype(np.float64)
                for cell in contract["cells"]:
                    encoding, k = str(cell["encoding"]), int(cell["k"])
                    weights = encoding_weights(encoding, profile_ratings[:k], prior, tau=float(contract["rating_scale"]["tau"]))
                    scores, active = active_scores(similarities[:, :k], weights)
                    ranked = strict_head_order(contract, evidence_id, str(value.user_key), int(value.panel), domain, encoding, k, candidates, scores) if active else []
                    rows.append({"user_key": str(value.user_key), "panel": int(value.panel), "domain": domain, "head": head, "encoding": encoding, "k": k, "active": active, "ranked_movie_ids": ranked})
    return pd.DataFrame(rows).sort_values(["user_key", "panel", "domain", "head", "encoding", "k"], kind="stable", ignore_index=True)


def _part_path(root: Path, start: int, stop: int) -> Path:
    return root / f"part-{start:06d}-{stop:06d}.parquet"


def score(contract: Mapping[str, Any], evidence_id: str) -> dict[str, Any]:
    signature = run_signature(contract, evidence_id)
    prepare(contract, evidence_id)
    score_input = pd.read_parquet(output_path(contract, evidence_id, "score_input"))
    item_ids = np.load(output_path(contract, evidence_id, "item_ids"), allow_pickle=False).astype(np.int64)
    matrices: dict[str, Any] = {
        "GENRE_ONLY": sparse.load_npz(output_path(contract, evidence_id, "feature_genre")).tocsr(),
        "TRANSFER_NO_CONTEXT": sparse.load_npz(output_path(contract, evidence_id, "feature_transfer")).tocsr(),
        "E5": np.load(output_path(contract, evidence_id, "feature_e5"), allow_pickle=False),
        "CURRENT_FULL": sparse.load_npz(output_path(contract, evidence_id, "feature_full")).tocsr(),
    }
    lookup = {int(movie): index for index, movie in enumerate(item_ids.tolist())}
    prior = verify_prior(contract)
    keys = sorted(score_input["user_key"].astype(str).unique().tolist())
    if len(keys) != int(contract["preflight"][evidence_id]["expected_users"]):
        raise RuntimeError("score user count drift")
    rank_path = output_path(contract, evidence_id, "rank")
    rank_integrity = output_path(contract, evidence_id, "rank_integrity")
    rank_reuse = sealed_group_state(contract, evidence_id, "RANK_SEALED", [rank_path, rank_integrity])
    parts_root = output_path(contract, evidence_id, "rank_parts")
    if parts_root.exists() and not parts_root.is_dir():
        raise ResumeError("rank parts path is not a directory")
    if not parts_root.exists():
        if progress_index(contract, evidence_id) >= PHASE_INDEX["RANK_SEALED"]:
            raise ResumeError("progress ahead of absent rank parts")
        parts_root.mkdir(parents=True, exist_ok=True)
    chunks = [(start, min(start + 64, len(keys))) for start in range(0, len(keys), 64)]
    expected_parts = {_part_path(parts_root, start, stop) for start, stop in chunks}
    expected_integrities = {path.with_suffix(".integrity.json") for path in expected_parts}
    if set(parts_root.glob("part-*.parquet")) - expected_parts or set(parts_root.glob("part-*.integrity.json")) - expected_integrities:
        raise ResumeError("unexpected rank part state")
    for start, stop in chunks:
        destination = _part_path(parts_root, start, stop)
        integrity = destination.with_suffix(".integrity.json")
        reuse = sealed_group_state(contract, evidence_id, "RANK_SEALED", [destination, integrity])
        selected_keys = keys[start:stop]
        frame = build_rank_frame(contract, evidence_id, score_input.loc[score_input["user_key"].isin(selected_keys)], lookup, matrices, prior)
        metadata = {"start": start, "stop": stop, "user_keys": selected_keys, "all_four_heads_complete": True, "evaluation_labels_opened": False}
        if reuse:
            verify_integrity(integrity, {"rank_part": destination}, signature=signature, expected_metadata=metadata)
            require_same_frame(frame, pd.read_parquet(destination), "rank part")
        else:
            atomic_to_parquet(destination, frame)
            write_integrity(integrity, {"rank_part": destination}, signature=signature, metadata=metadata)
    combined = pd.concat([pd.read_parquet(path) for path in sorted(expected_parts)], ignore_index=True).sort_values(["user_key", "panel", "domain", "head", "encoding", "k"], kind="stable", ignore_index=True)
    expected_rows = len(keys) * 4 * 2 * len(HEADS) * len(contract["cells"])
    if len(combined) != expected_rows or combined.duplicated(["user_key", "panel", "domain", "head", "encoding", "k"]).any():
        raise RuntimeError("rank Cartesian drift")
    metadata = {"users": len(keys), "rows": len(combined), "parts": len(chunks), "inactive_rows": int((~combined["active"].astype(bool)).sum()), "all_four_heads_complete": True, "evaluation_labels_opened": False, "evaluation_rating_bytes_parsed": 0, "timestamp_bytes_parsed": 0, "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False}
    if rank_reuse:
        verify_integrity(rank_integrity, {"score_rank": rank_path}, signature=signature, expected_metadata=metadata)
        require_same_frame(combined, pd.read_parquet(rank_path), "combined rank")
        reconcile_progress(contract, evidence_id, "RANK_SEALED", len(keys))
        return {"status": "REUSED_EXACT_RANK", **metadata}
    atomic_to_parquet(rank_path, combined)
    write_integrity(rank_integrity, {"score_rank": rank_path}, signature=signature, metadata=metadata)
    reconcile_progress(contract, evidence_id, "RANK_SEALED", len(keys))
    return {"status": "RANK_SEALED", **metadata}


def verify_input_label_disjoint(score_input: pd.DataFrame) -> None:
    for value in score_input.itertuples(index=False):
        if set(int(movie) for movie in value.profile_movie_ids) & (set(int(movie) for movie in value.target_movie_ids) | set(int(movie) for movie in value.control_movie_ids)):
            raise RuntimeError("profile/evaluation overlap at label open")


def evaluation(contract: Mapping[str, Any], evidence_id: str) -> dict[str, Any]:
    signature = run_signature(contract, evidence_id)
    score(contract, evidence_id)
    rank_path = output_path(contract, evidence_id, "rank")
    verify_integrity(output_path(contract, evidence_id, "rank_integrity"), {"score_rank": rank_path}, signature=signature)
    score_input = pd.read_parquet(output_path(contract, evidence_id, "score_input"))
    verify_input_label_disjoint(score_input)
    label_source, parsed = _evaluation_label_pass(contract, score_input)
    labels = build_evaluation_labels(label_source)
    artifacts = {"label_source": output_path(contract, evidence_id, "label_source"), "evaluation_labels": output_path(contract, evidence_id, "evaluation_labels")}
    integrity = output_path(contract, evidence_id, "labels_integrity")
    reuse = sealed_group_state(contract, evidence_id, "EVALUATION_OPEN", [integrity, *artifacts.values()])
    metadata = {**parsed, "rank_sealed_before_label_open": True, "all_four_heads_complete_before_label_open": True, "users": int(contract["preflight"][evidence_id]["expected_users"]), "label_source_rows": len(label_source), "evaluation_label_rows": len(labels), "profile_evaluation_intersection": 0, "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False}
    if reuse:
        verify_integrity(integrity, artifacts, signature=signature, expected_metadata=metadata)
        require_same_frame(label_source, pd.read_parquet(artifacts["label_source"]), "label source")
        require_same_frame(labels, pd.read_parquet(artifacts["evaluation_labels"]), "evaluation labels")
        reconcile_progress(contract, evidence_id, "EVALUATION_OPEN", len(labels))
        return {"status": "REUSED_EXACT_EVALUATION", **metadata}
    atomic_to_parquet(artifacts["label_source"], label_source)
    atomic_to_parquet(artifacts["evaluation_labels"], labels)
    write_integrity(integrity, artifacts, signature=signature, metadata=metadata)
    reconcile_progress(contract, evidence_id, "EVALUATION_OPEN", len(labels))
    return {"status": "EVALUATION_OPEN", **metadata}


def build_head_metrics(ranks: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    label_lookup = {(str(row.user_key), int(row.panel), str(row.domain)): ([int(movie) for movie in row.movie_ids], np.asarray(row.q, dtype=np.float64)) for row in labels.itertuples(index=False)}
    rows: list[dict[str, Any]] = []
    for value in ranks.itertuples(index=False):
        movies, q = label_lookup[(str(value.user_key), int(value.panel), str(value.domain))]
        random_utility, random_loss = analytic_random_top2(q)
        if bool(value.active):
            ranked = [int(movie) for movie in value.ranked_movie_ids]
            if len(ranked) != len(movies) or set(ranked) != set(movies):
                raise RuntimeError("rank/label identity drift")
            by_movie = {movie: float(label) for movie, label in zip(movies, q, strict=True)}
            top = np.asarray([by_movie[movie] for movie in ranked[:2]], dtype=np.float64)
            utility, loss = float(top.mean()), float(1.0 - top.min())
        else:
            utility, loss = random_utility, random_loss
        rows.append({"user_key": str(value.user_key), "panel": int(value.panel), "domain": str(value.domain), "head": str(value.head), "encoding": str(value.encoding), "k": int(value.k), "active": bool(value.active), "model_utility": utility, "model_loss": loss, "random_utility": random_utility, "random_loss": random_loss, "utility_minus_random": utility - random_utility, "safety_minus_random": random_loss - loss})
    return pd.DataFrame(rows).sort_values(["user_key", "panel", "domain", "head", "encoding", "k"], kind="stable", ignore_index=True)


def contrast_metadata(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for head in HEADS:
        for cell in contract["cells"]:
            for cls in ABSOLUTE_CLASSES:
                for endpoint in ABSOLUTE_ENDPOINTS:
                    rows.append({"contrast_index": len(rows), "family": "ABSOLUTE", "head": head, "challenger": "", "encoding": str(cell["encoding"]), "k": int(cell["k"]), "class": cls, "domain": "", "endpoint": endpoint})
    for challenger in CHALLENGERS:
        for cell in contract["cells"]:
            for domain in DOMAINS:
                for endpoint in INCREMENTAL_ENDPOINTS:
                    rows.append({"contrast_index": len(rows), "family": "INCREMENTAL", "head": "", "challenger": challenger, "encoding": str(cell["encoding"]), "k": int(cell["k"]), "class": "", "domain": domain, "endpoint": endpoint})
    if len(rows) != 216:
        raise RuntimeError("contrast family drift")
    return rows


def build_user_contrasts(metrics: pd.DataFrame, contract: Mapping[str, Any]) -> pd.DataFrame:
    identity = ["user_key", "panel", "domain", "head", "encoding", "k"]
    if metrics.duplicated(identity).any():
        raise RuntimeError("head metric duplicate")
    indexed = metrics.set_index(identity)
    rows: list[dict[str, Any]] = []
    for key in sorted(metrics["user_key"].astype(str).unique().tolist()):
        for meta in contrast_metadata(contract):
            panel_values: list[float] = []
            for panel in range(4):
                if meta["family"] == "ABSOLUTE":
                    head, encoding, k = meta["head"], meta["encoding"], meta["k"]
                    endpoint_column = "utility_minus_random" if meta["endpoint"] == ABSOLUTE_ENDPOINTS[0] else "safety_minus_random"
                    target = float(indexed.loc[(key, panel, "TARGET", head, encoding, k)][endpoint_column])
                    control = float(indexed.loc[(key, panel, "CONTROL", head, encoding, k)][endpoint_column])
                    panel_values.append(target if meta["class"] == "TARGET_IMPROVEMENT" else control if meta["class"] == "CONTROL_IMPROVEMENT" else target - control)
                else:
                    challenger, encoding, k, domain = meta["challenger"], meta["encoding"], meta["k"], meta["domain"]
                    challenge = indexed.loc[(key, panel, domain, challenger, encoding, k)]
                    current = indexed.loc[(key, panel, domain, "CURRENT_FULL", encoding, k)]
                    panel_values.append(float(challenge.model_utility) - float(current.model_utility) if meta["endpoint"] == INCREMENTAL_ENDPOINTS[0] else float(current.model_loss) - float(challenge.model_loss))
            rows.append({"user_key": key, **meta, "value": float(np.mean(panel_values))})
    return pd.DataFrame(rows).sort_values(["user_key", "contrast_index"], kind="stable", ignore_index=True)


def materialize_metrics(contract: Mapping[str, Any], evidence_id: str) -> dict[str, Any]:
    signature = run_signature(contract, evidence_id)
    evaluation(contract, evidence_id)
    metrics = build_head_metrics(pd.read_parquet(output_path(contract, evidence_id, "rank")), pd.read_parquet(output_path(contract, evidence_id, "evaluation_labels")))
    contrasts = build_user_contrasts(metrics, contract)
    users = int(contract["preflight"][evidence_id]["expected_users"])
    if len(metrics) != users * 4 * 2 * 4 * 6 or len(contrasts) != users * 216:
        raise RuntimeError("metric/contrast Cartesian drift")
    metric_path, metric_integrity = output_path(contract, evidence_id, "panel_metrics"), output_path(contract, evidence_id, "panel_metrics_integrity")
    contrast_path, contrast_integrity = output_path(contract, evidence_id, "user_contrasts"), output_path(contract, evidence_id, "user_contrasts_integrity")
    metric_metadata = {"users": users, "rows": len(metrics), "rank_sealed_before_label_open": True, "all_four_heads_complete": True}
    contrast_metadata_value = {"users": users, "rows": len(contrasts), "contrasts": 216, "contrast_metadata_sha256": hashlib.sha256(canonical_json_bytes(contrast_metadata(contract))).hexdigest()}
    reuse = sealed_group_state(contract, evidence_id, "METRICS_SEALED", [metric_path, metric_integrity, contrast_path, contrast_integrity])
    if reuse:
        verify_integrity(metric_integrity, {"panel_metrics": metric_path}, signature=signature, expected_metadata=metric_metadata)
        verify_integrity(contrast_integrity, {"user_contrasts": contrast_path}, signature=signature, expected_metadata=contrast_metadata_value)
        require_same_frame(metrics, pd.read_parquet(metric_path), "panel metrics")
        require_same_frame(contrasts, pd.read_parquet(contrast_path), "user contrasts")
        reconcile_progress(contract, evidence_id, "METRICS_SEALED", len(contrasts))
        return {"status": "REUSED_EXACT_METRICS", **contrast_metadata_value}
    atomic_to_parquet(metric_path, metrics)
    write_integrity(metric_integrity, {"panel_metrics": metric_path}, signature=signature, metadata=metric_metadata)
    atomic_to_parquet(contrast_path, contrasts)
    write_integrity(contrast_integrity, {"user_contrasts": contrast_path}, signature=signature, metadata=contrast_metadata_value)
    reconcile_progress(contract, evidence_id, "METRICS_SEALED", len(contrasts))
    return {"status": "METRICS_SEALED", **contrast_metadata_value}


def poisson_user_weight(evidence_id: str, attempt: int, key: str, cutoffs: Sequence[int]) -> tuple[int, int]:
    payload = f"feelm-bootstrap-v1|rec-ev-025ab-feature-transfer-bootstrap-v1|{evidence_id}|{canonical_decimal(attempt)}|user|{key}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)
    return bisect.bisect_left(cutoffs, value), value


def verify_poisson_golden(contract: Mapping[str, Any], evidence_id: str, cutoffs: Sequence[int]) -> None:
    fixtures = [row for row in contract["bootstrap_golden_fixtures"] if row["evidence_id"] == evidence_id]
    if len(fixtures) != 2:
        raise RuntimeError("Poisson fixture set drift")
    for row in fixtures:
        weight, value = poisson_user_weight(evidence_id, int(row["attempt"]), str(row["user_key"]), cutoffs)
        if weight != int(row["weight"]) or value != int(row["uint64"]):
            raise RuntimeError("Poisson golden drift")


def contrast_matrix(contract: Mapping[str, Any], frame: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    columns = ["contrast_index", "family", "head", "challenger", "encoding", "k", "class", "domain", "endpoint"]
    observed = frame[columns].drop_duplicates().sort_values("contrast_index", kind="stable").to_dict("records")
    if observed != contrast_metadata(contract):
        raise RuntimeError("contrast metadata drift")
    keys = sorted(frame["user_key"].astype(str).unique().tolist())
    ordered = frame.sort_values(["user_key", "contrast_index"], kind="stable", ignore_index=True)
    if len(ordered) != len(keys) * 216 or ordered.duplicated(["user_key", "contrast_index"]).any():
        raise RuntimeError("contrast Cartesian drift")
    return keys, ordered["value"].to_numpy(dtype=np.float64).reshape(len(keys), 216)


def compute_bootstrap_arrays(contract: Mapping[str, Any], evidence_id: str, keys: Sequence[str], values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.shape != (len(keys), 216) or not np.isfinite(matrix).all():
        raise ValueError("bootstrap input drift")
    point = matrix.mean(axis=0)
    cutoffs = poisson_cutoffs(precision=80)
    verify_poisson_golden(contract, evidence_id, cutoffs)
    valid: list[int] = []
    invalid: list[int] = []
    replicates: list[np.ndarray] = []
    start, stop = (int(value) for value in contract["statistics"]["attempts"])
    for attempt in range(start, stop + 1):
        weights = np.fromiter((poisson_user_weight(evidence_id, attempt, key, cutoffs)[0] for key in keys), dtype=np.float64, count=len(keys))
        denominator = float(weights.sum())
        if not math.isfinite(denominator) or denominator <= 0:
            invalid.append(attempt)
            continue
        estimate = (weights @ matrix) / denominator
        if not np.isfinite(estimate).all():
            invalid.append(attempt)
            continue
        valid.append(attempt)
        replicates.append(np.asarray(estimate, dtype=np.float64))
        if len(replicates) == int(contract["statistics"]["valid_replicates"]):
            break
    if len(replicates) != 4000:
        raise RuntimeError("fewer than 4,000 valid bootstrap replicates")
    metadata = {"users": len(keys), "contrasts": 216, "valid_replicates": 4000, "invalid_attempts": len(invalid), "first_valid_attempt": valid[0], "last_valid_attempt": valid[-1], "poisson_golden_verified": True, "primary_regime": "USER_ONLY"}
    return point, np.vstack(replicates), np.asarray(valid, dtype=np.int32), np.asarray(invalid, dtype=np.int32), metadata


def bootstrap(contract: Mapping[str, Any], evidence_id: str) -> dict[str, Any]:
    signature = run_signature(contract, evidence_id)
    materialize_metrics(contract, evidence_id)
    keys, values = contrast_matrix(contract, pd.read_parquet(output_path(contract, evidence_id, "user_contrasts")))
    point, replicates, valid, invalid, metadata = compute_bootstrap_arrays(contract, evidence_id, keys, values)
    destination, integrity = output_path(contract, evidence_id, "bootstrap"), output_path(contract, evidence_id, "bootstrap_integrity")
    reuse = sealed_group_state(contract, evidence_id, "BOOTSTRAP_SEALED", [destination, integrity])
    if reuse:
        verify_integrity(integrity, {"bootstrap": destination}, signature=signature, expected_metadata=metadata)
        with np.load(destination, allow_pickle=False) as cached:
            matches = set(cached.files) == {"point", "replicates", "valid_attempt_ids", "invalid_attempt_ids"} and np.array_equal(cached["point"], point) and np.array_equal(cached["replicates"], replicates) and np.array_equal(cached["valid_attempt_ids"], valid) and np.array_equal(cached["invalid_attempt_ids"], invalid)
        if not matches:
            raise ResumeError("bootstrap drift")
        reconcile_progress(contract, evidence_id, "BOOTSTRAP_SEALED", len(replicates))
        return {"status": "REUSED_EXACT_BOOTSTRAP", **metadata}
    atomic_save_npz(destination, point=point, replicates=replicates, valid_attempt_ids=valid, invalid_attempt_ids=invalid)
    write_integrity(integrity, {"bootstrap": destination}, signature=signature, metadata=metadata)
    reconcile_progress(contract, evidence_id, "BOOTSTRAP_SEALED", len(replicates))
    return {"status": "BOOTSTRAP_SEALED", **metadata}


def simultaneous_intervals(point: np.ndarray, replicates: np.ndarray) -> tuple[list[dict[str, Any]], float]:
    point = np.asarray(point, dtype=np.float64)
    matrix = np.asarray(replicates, dtype=np.float64)
    if point.shape != (216,) or matrix.shape != (4000, 216):
        raise ValueError("simultaneous family shape drift")
    se = matrix.std(axis=0, ddof=1)
    estimable = np.isfinite(se) & (se > 0)
    critical = 0.0
    if estimable.any():
        maxima = np.max(np.abs((matrix[:, estimable] - point[estimable]) / se[estimable]), axis=1)
        critical = nearest_rank(maxima, 0.975)
    rows: list[dict[str, Any]] = []
    for index in range(216):
        width = float(critical * se[index]) if estimable[index] else None
        rows.append({"contrast_index": index, "mean": float(point[index]), "se": float(se[index]) if np.isfinite(se[index]) else None, "estimable": bool(estimable[index]), "half_width": width, "low": float(point[index] - width) if width is not None else None, "high": float(point[index] + width) if width is not None else None})
    return rows, critical


def _panel_points(metrics: pd.DataFrame, contract: Mapping[str, Any]) -> dict[tuple[Any, ...], float]:
    indexed = metrics.set_index(["user_key", "panel", "domain", "head", "encoding", "k"])
    keys = sorted(metrics["user_key"].astype(str).unique().tolist())
    points: dict[tuple[Any, ...], float] = {}
    for cell in contract["cells"]:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        for panel in range(4):
            for head in HEADS:
                for domain in DOMAINS:
                    rows = [indexed.loc[(key, panel, domain, head, encoding, k)] for key in keys]
                    points[("ABS", head, encoding, k, domain, ABSOLUTE_ENDPOINTS[0], panel)] = float(np.mean([float(row.utility_minus_random) for row in rows]))
                    points[("ABS", head, encoding, k, domain, ABSOLUTE_ENDPOINTS[1], panel)] = float(np.mean([float(row.safety_minus_random) for row in rows]))
            for challenger in CHALLENGERS:
                for domain in DOMAINS:
                    challenge_rows = [indexed.loc[(key, panel, domain, challenger, encoding, k)] for key in keys]
                    current_rows = [indexed.loc[(key, panel, domain, "CURRENT_FULL", encoding, k)] for key in keys]
                    points[("INC", challenger, encoding, k, domain, INCREMENTAL_ENDPOINTS[0], panel)] = float(np.mean([float(left.model_utility) - float(right.model_utility) for left, right in zip(challenge_rows, current_rows, strict=True)]))
                    points[("INC", challenger, encoding, k, domain, INCREMENTAL_ENDPOINTS[1], panel)] = float(np.mean([float(right.model_loss) - float(left.model_loss) for left, right in zip(challenge_rows, current_rows, strict=True)]))
    return points


def decision_from_intervals(contract: Mapping[str, Any], evidence_id: str, interval_rows: Sequence[Mapping[str, Any]], panel_metrics: pd.DataFrame) -> dict[str, Any]:
    intervals = [{**contrast_metadata(contract)[index], **dict(row)} for index, row in enumerate(interval_rows)]
    absolute = {(row["head"], row["encoding"], int(row["k"]), row["class"], row["endpoint"]): row for row in intervals if row["family"] == "ABSOLUTE"}
    incremental = {(row["challenger"], row["encoding"], int(row["k"]), row["domain"], row["endpoint"]): row for row in intervals if row["family"] == "INCREMENTAL"}
    points = _panel_points(panel_metrics, contract)
    width_limit = float(contract["decision"]["maximum_half_width_strict"])
    absolute_cells: list[dict[str, Any]] = []
    for head in HEADS:
        for cell in contract["cells"]:
            encoding, k = str(cell["encoding"]), int(cell["k"])
            target_rows = [absolute[(head, encoding, k, "TARGET_IMPROVEMENT", endpoint)] for endpoint in ABSOLUTE_ENDPOINTS]
            gap_rows = [absolute[(head, encoding, k, "CONDITIONAL_GAP", endpoint)] for endpoint in ABSOLUTE_ENDPOINTS]
            target_points = [points[("ABS", head, encoding, k, "TARGET", endpoint, panel)] for endpoint in ABSOLUTE_ENDPOINTS for panel in range(4)]
            gap_points = [points[("ABS", head, encoding, k, "TARGET", endpoint, panel)] - points[("ABS", head, encoding, k, "CONTROL", endpoint, panel)] for endpoint in ABSOLUTE_ENDPOINTS for panel in range(4)]
            target_pass = all(row["estimable"] and float(row["low"]) >= float(contract["decision"]["absolute_target_margin"]) and float(row["half_width"]) < width_limit for row in target_rows) and all(value > 0 for value in target_points)
            gap_pass = all(row["estimable"] and float(row["low"]) >= float(contract["decision"]["absolute_gap_noninferiority"]) and float(row["half_width"]) < width_limit for row in gap_rows) and all(value >= float(contract["decision"]["absolute_gap_noninferiority"]) for value in gap_points)
            absolute_cells.append({"head": head, "encoding": encoding, "k": k, "target_pass": target_pass, "gap_pass": gap_pass, "target_panel_points": target_points, "gap_panel_points": gap_points})
    incremental_cells: list[dict[str, Any]] = []
    for challenger in CHALLENGERS:
        for cell in contract["cells"]:
            encoding, k = str(cell["encoding"]), int(cell["k"])
            target_rows = [incremental[(challenger, encoding, k, "TARGET", endpoint)] for endpoint in INCREMENTAL_ENDPOINTS]
            control_rows = [incremental[(challenger, encoding, k, "CONTROL", endpoint)] for endpoint in INCREMENTAL_ENDPOINTS]
            target_utility_points = [points[("INC", challenger, encoding, k, "TARGET", INCREMENTAL_ENDPOINTS[0], panel)] for panel in range(4)]
            target_safety_points = [points[("INC", challenger, encoding, k, "TARGET", INCREMENTAL_ENDPOINTS[1], panel)] for panel in range(4)]
            control_points = [points[("INC", challenger, encoding, k, "CONTROL", endpoint, panel)] for endpoint in INCREMENTAL_ENDPOINTS for panel in range(4)]
            target_pass = target_rows[0]["estimable"] and target_rows[1]["estimable"] and float(target_rows[0]["low"]) >= float(contract["decision"]["incremental_target_utility_margin"]) and float(target_rows[1]["low"]) >= float(contract["decision"]["incremental_target_safety_margin"]) and all(float(row["half_width"]) < width_limit for row in target_rows) and all(value >= float(contract["decision"]["incremental_target_utility_margin"]) for value in target_utility_points) and all(value >= float(contract["decision"]["incremental_target_safety_margin"]) for value in target_safety_points)
            control_pass = all(row["estimable"] and float(row["low"]) >= float(contract["decision"]["incremental_control_noninferiority"]) and float(row["half_width"]) < width_limit for row in control_rows) and all(value >= float(contract["decision"]["incremental_control_noninferiority"]) for value in control_points)
            incremental_cells.append({"challenger": challenger, "encoding": encoding, "k": k, "target_pass": target_pass, "control_pass": control_pass, "target_utility_panel_points": target_utility_points, "target_safety_panel_points": target_safety_points, "control_panel_points": control_points})
    robust_absolute = [head for head in HEADS if all(row["target_pass"] and row["gap_pass"] for row in absolute_cells if row["head"] == head)]
    robust_incremental = [challenger for challenger in CHALLENGERS if challenger in robust_absolute and all(row["target_pass"] and row["control_pass"] for row in incremental_cells if row["challenger"] == challenger)]
    any_cell = any(row["target_pass"] and row["gap_pass"] for row in absolute_cells) or any(row["target_pass"] and row["control_pass"] for row in incremental_cells)
    def precise(row: Mapping[str, Any]) -> bool:
        if not bool(row.get("estimable")):
            return False
        try:
            mean = float(row["mean"])
            se = float(row["se"])
            low = float(row["low"])
            high = float(row["high"])
            half_width = float(row["half_width"])
        except (KeyError, TypeError, ValueError):
            return False
        return all(math.isfinite(value) for value in (mean, se, low, high, half_width)) and se > 0 and half_width < width_limit
    imprecise = any(not precise(row) for row in intervals)
    if contract["preflight"][evidence_id]["status"] != "FEASIBLE_PRELABEL":
        status = "INFEASIBLE_PRELABEL"
    elif imprecise:
        status = "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE"
    elif robust_incremental:
        status = "ROBUST_INCREMENTAL_TRANSFER_HEAD"
    elif robust_absolute:
        status = "ROBUST_ABSOLUTE_TRANSFER_HEAD"
    elif any_cell:
        status = "CELL_SPECIFIC_SIGNAL_NOT_ROBUST"
    else:
        status = "NO_ROBUST_TRANSFER_HEAD"
    return {"status": status, "precision_or_estimability_failure": imprecise, "robust_absolute_heads": robust_absolute, "robust_incremental_heads": robust_incremental, "absolute_cell_truth": absolute_cells, "incremental_cell_truth": incremental_cells}


def auxiliary_summary(metrics: pd.DataFrame) -> list[dict[str, Any]]:
    columns = ["model_utility", "model_loss", "random_utility", "random_loss", "utility_minus_random", "safety_minus_random", "active"]
    return metrics.groupby(["head", "domain", "encoding", "k"], observed=True)[columns].mean().reset_index().to_dict("records")


def finalize_or_verify(contract: Mapping[str, Any], evidence_id: str, result: Mapping[str, Any], selection: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    signature = run_signature(contract, evidence_id)
    result_path, selection_path, integrity = output_path(contract, evidence_id, "result"), output_path(contract, evidence_id, "selection"), output_path(contract, evidence_id, "result_integrity")
    reuse = sealed_group_state(contract, evidence_id, "COMPLETE", [result_path, selection_path, integrity])
    if reuse:
        verify_integrity(integrity, {"result": result_path, "selection": selection_path}, signature=signature, expected_metadata=metadata)
        if read_json(result_path) != dict(result) or read_json(selection_path) != dict(selection):
            raise ResumeError("result semantic drift")
        return "REUSED_EXACT_RESULT"
    atomic_write_json(selection_path, selection)
    atomic_write_json(result_path, result)
    write_integrity(integrity, {"result": result_path, "selection": selection_path}, signature=signature, metadata=metadata)
    return "WROTE_RESULT"


def analyze(contract: Mapping[str, Any], evidence_id: str) -> dict[str, Any]:
    signature = run_signature(contract, evidence_id)
    bootstrap(contract, evidence_id)
    metrics = pd.read_parquet(output_path(contract, evidence_id, "panel_metrics"))
    keys, values = contrast_matrix(contract, pd.read_parquet(output_path(contract, evidence_id, "user_contrasts")))
    point, replicates, valid, invalid, bootstrap_metadata = compute_bootstrap_arrays(contract, evidence_id, keys, values)
    bootstrap_path = output_path(contract, evidence_id, "bootstrap")
    with np.load(bootstrap_path, allow_pickle=False) as cached:
        if not (np.array_equal(cached["point"], point) and np.array_equal(cached["replicates"], replicates) and np.array_equal(cached["valid_attempt_ids"], valid) and np.array_equal(cached["invalid_attempt_ids"], invalid)):
            raise ResumeError("bootstrap drift before result")
    verify_integrity(output_path(contract, evidence_id, "bootstrap_integrity"), {"bootstrap": bootstrap_path}, signature=signature, expected_metadata=bootstrap_metadata)
    raw_intervals, critical = simultaneous_intervals(point, replicates)
    intervals = [{**contrast_metadata(contract)[index], **row} for index, row in enumerate(raw_intervals)]
    decision = decision_from_intervals(contract, evidence_id, raw_intervals, metrics)
    selection = {"schema_version": 1, "evidence_id": evidence_id, **decision, "champion": None, "product_policy_updated": False, "final_reserve_opened": False, "old_locked_ratings_timestamps_metrics_opened": False}
    result = {"schema_version": 1, "evidence_id": evidence_id, "status": decision["status"], "run_signature": signature, "purpose": contract["purpose"], "claim_boundary": contract["claim_boundary"], "selection": selection, "simultaneous_intervals": intervals, "critical_value_97_5_percent": critical, "bootstrap": bootstrap_metadata, "auxiliary_random_comparisons": auxiliary_summary(metrics), "preflight_item_membership_concentration": {"global_profile": contract["preflight"][evidence_id]["global_profile"], "panel_target": contract["preflight"][evidence_id]["panel_target"], "panel_control": contract["preflight"][evidence_id]["panel_control"], "interpretation": "DESCRIPTIVE_ONLY_NO_ITEM_GENERALIZATION"}, "users": len(keys), "panels_per_user": 4, "primary_n": 2, "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True, "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False, "product_policy_updated": False, "champion": None}
    metadata = {"status": decision["status"], "users": len(keys), "contrasts": 216, "champion": None}
    state = finalize_or_verify(contract, evidence_id, result, selection, metadata)
    reconcile_progress(contract, evidence_id, "COMPLETE", len(keys))
    return {"status": decision["status"], "users": len(keys), "contrasts": 216, "result_state": state}


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(contract)
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default=str(DEFAULT))
    parser.add_argument("--evidence-id", choices=EVIDENCE_IDS, required=True)
    parser.add_argument("--phase", choices=("lock", "prepare", "score", "evaluation", "metrics", "bootstrap", "analyze", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    path = Path(args.contract)
    path = path if path.is_absolute() else (ROOT / path).resolve()
    contract = load_contract(path)
    evidence_id = str(args.evidence_id)
    if args.phase == "lock":
        print(json.dumps(create_or_verify_lock(contract, evidence_id, resume=args.resume), ensure_ascii=False, sort_keys=True))
        return 0
    if not args.resume:
        raise ResumeError("all post-lock phases require --resume")
    create_or_verify_lock(contract, evidence_id, resume=True)
    phases = {"prepare": prepare, "score": score, "evaluation": evaluation, "metrics": materialize_metrics, "bootstrap": bootstrap, "analyze": analyze}
    value = analyze(contract, evidence_id) if args.phase == "run" else phases[args.phase](contract, evidence_id)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
