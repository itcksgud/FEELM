#!/usr/bin/env python3
"""Run REC-EV-024A/B paired target-anchor policy replacement experiments."""

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
from scipy import sparse

try:
    from rec_ev_022a_core import RATING_VALUES, canonical_decimal, encoding_weights, user_key
    from run_rec_ev_023ef_transfer import (
        ResumeError,
        _evaluation_label_pass,
        _profile_rating_pass,
        active_scores,
        analytic_random_top2,
        atomic_save_npy,
        atomic_save_npz,
        atomic_save_sparse,
        atomic_to_parquet,
        atomic_write_json,
        build_evaluation_labels,
        build_feature_heads,
        canonical_json_bytes,
        implementation_rows,
        movie_lens_movie_rows,
        nearest_rank,
        output_path,
        poisson_cutoffs,
        read_json,
        require_same_frame,
        require_same_sparse,
        resolve_input,
        source_rows,
        verify_integrity,
        write_integrity,
    )
    from run_rec_ev_024ab_preflight import _experiment_summary, partition_user
    from validate_rec_ev_024_anchor_contract import validate_contract
except ImportError:
    from scripts.rec_ev_022a_core import RATING_VALUES, canonical_decimal, encoding_weights, user_key
    from scripts.run_rec_ev_023ef_transfer import (
        ResumeError,
        _evaluation_label_pass,
        _profile_rating_pass,
        active_scores,
        analytic_random_top2,
        atomic_save_npy,
        atomic_save_npz,
        atomic_save_sparse,
        atomic_to_parquet,
        atomic_write_json,
        build_evaluation_labels,
        build_feature_heads,
        canonical_json_bytes,
        implementation_rows,
        movie_lens_movie_rows,
        nearest_rank,
        output_path,
        poisson_cutoffs,
        read_json,
        require_same_frame,
        require_same_sparse,
        resolve_input,
        source_rows,
        verify_integrity,
        write_integrity,
    )
    from scripts.run_rec_ev_024ab_preflight import _experiment_summary, partition_user
    from scripts.validate_rec_ev_024_anchor_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "REC-EV-024A": ROOT / "docs/recommendation/contracts/rec-ev-024a-korean-anchor-policy.json",
    "REC-EV-024B": ROOT / "docs/recommendation/contracts/rec-ev-024b-recent-anchor-policy.json",
}
POLICIES = ("SOURCE_ONLY", "TARGET2_MIXED")
DOMAINS = ("TARGET", "CONTROL")
ENDPOINTS = ("UTILITY_EFFECT", "SAFETY_EFFECT")
PHASE_INDEX = {
    "MEMBERSHIP_SEALED": 10,
    "SCORE_INPUT_OPEN": 20,
    "RANK_SEALED": 30,
    "EVALUATION_OPEN": 40,
    "METRICS_SEALED": 45,
    "BOOTSTRAP_SEALED": 50,
    "COMPLETE": 60,
}


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "purpose", "design_audit", "preflight_audits", "authorization", "forbidden_input_artifacts",
        "reader", "preflight", "cohort", "cells", "policies", "feature", "rating_scale", "phases",
        "scoring", "metrics", "statistics", "decision", "claim_boundary", "resume", "invariants",
    )
    return {key: contract[key] for key in keys}


def expected_lock_state(contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_contract(contract)
    sources = source_rows(contract)
    implementations = implementation_rows(contract)
    manifest = {
        "schema_version": 1,
        "evidence_id": contract["evidence_id"],
        "sources": sources,
        "implementation_artifacts": implementations,
        "preflight_result_opened": True,
        "evaluation_labels_opened_at_lock": False,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    lock = {
        "schema_version": 1,
        "evidence_id": contract["evidence_id"],
        "status": "LOCKED_ANCHOR_POLICY_REPLACEMENT",
        "contract_sha256": hashlib.sha256(canonical_json_bytes(contract)).hexdigest(),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest(),
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
        if read_json(lock_path) != lock or read_json(manifest_path) != manifest:
            raise ResumeError("protocol lock or manifest drift")
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


def progress_value(contract: Mapping[str, Any], phase: str, completed_units: int = 0) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_id": contract["evidence_id"],
        "run_signature": run_signature(contract),
        "phase": phase,
        "phase_index": PHASE_INDEX[phase],
        "completed_units": int(completed_units),
        "evaluation_labels_opened": PHASE_INDEX[phase] >= PHASE_INDEX["EVALUATION_OPEN"],
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }


def validate_progress(contract: Mapping[str, Any], minimum_phase: str | None = None) -> dict[str, Any]:
    path = output_path(contract, "progress")
    if not path.is_file():
        raise ResumeError("progress missing")
    value = read_json(path)
    phase = value.get("phase")
    if phase not in PHASE_INDEX or value != progress_value(contract, str(phase), int(value.get("completed_units", -1))):
        raise ResumeError("progress schema or invariant drift")
    if minimum_phase is not None and PHASE_INDEX[str(phase)] < PHASE_INDEX[minimum_phase]:
        raise ResumeError("progress behind sealed artifact")
    return value


def progress_index(contract: Mapping[str, Any]) -> int:
    path = output_path(contract, "progress")
    return PHASE_INDEX[str(validate_progress(contract)["phase"])] if path.is_file() else -1


def sealed_group_state(contract: Mapping[str, Any], phase: str, paths: Sequence[Path]) -> bool:
    present = [path.exists() for path in paths]
    if any(present) and not all(present):
        raise ResumeError(f"partial {phase} artifact state")
    if not all(present) and progress_index(contract) >= PHASE_INDEX[phase]:
        raise ResumeError(f"progress ahead of absent {phase} artifacts")
    return all(present)


def reconcile_progress(contract: Mapping[str, Any], phase: str, completed_units: int = 0) -> None:
    path = output_path(contract, "progress")
    if not path.is_file():
        atomic_write_json(path, progress_value(contract, phase, completed_units))
        return
    previous = validate_progress(contract)
    prior_index = PHASE_INDEX[str(previous["phase"])]
    target_index = PHASE_INDEX[phase]
    if prior_index < target_index:
        atomic_write_json(path, progress_value(contract, phase, completed_units))
    elif prior_index == target_index and int(previous["completed_units"]) != int(completed_units):
        raise ResumeError("progress completed-unit drift")


def _movie_lookup(movie_ids: np.ndarray) -> np.ndarray:
    lookup = np.full(int(movie_ids.max(initial=0)) + 1, -1, dtype=np.int32)
    lookup[movie_ids] = np.arange(len(movie_ids), dtype=np.int32)
    return lookup


def _korean_ids(contract: Mapping[str, Any]) -> set[int]:
    value = read_json(resolve_input(contract["allowed_input_artifacts"]["korean_movie_id_projection"]))
    if value.get("artifact_id") != "KOREAN_ORIGIN_MOVIELENS_MOVIE_ID_PROJECTION_V1" or int(value.get("count", -1)) != 1078:
        raise RuntimeError("Korean projection identity drift")
    movies = {int(movie) for movie in value["movie_ids"]}
    if len(movies) != 1078:
        raise RuntimeError("Korean projection cardinality drift")
    return movies


def domain_masks(contract: Mapping[str, Any], movie_ids: np.ndarray, years: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if contract["evidence_id"] == "REC-EV-024A":
        korean = _korean_ids(contract)
        target = np.fromiter((int(movie) in korean for movie in movie_ids), dtype=bool, count=len(movie_ids))
        return ~target, target
    target = (years >= 2020) & (years <= 2023)
    return years < 2020, target


def _eligible_and_pools(
    contract: Mapping[str, Any], movie_ids: np.ndarray, years: np.ndarray,
) -> tuple[np.ndarray, dict[int, list[int]], dict[int, list[int]], dict[str, int]]:
    lookup = _movie_lookup(movie_ids)
    source_mask, target_mask = domain_masks(contract, movie_ids, years)
    maximum_user = int(contract["reader"]["maximum_user_id"])
    source_counts = np.zeros(maximum_user + 1, dtype=np.uint32)
    target_counts = np.zeros_like(source_counts)
    allowed_rows = 0
    for raw_user, movie, _raw_line, _second in movie_lens_movie_rows(contract):
        allowed_rows += 1
        position = int(lookup[movie]) if 0 <= movie < len(lookup) else -1
        if position >= 0:
            source_counts[raw_user] += int(source_mask[position])
            target_counts[raw_user] += int(target_mask[position])
    cohort = contract["cohort"]
    mask = (source_counts >= int(cohort["minimum_source_ratings"])) & (target_counts >= int(cohort["minimum_target_ratings"]))
    eligible = np.flatnonzero(mask).astype(np.int32)
    expected_keys = sorted(user_key(int(value)) for value in eligible)
    expected_hash = hashlib.sha256(canonical_json_bytes(expected_keys)).hexdigest()
    if len(eligible) != int(contract["preflight"]["expected_users"]) or expected_hash != contract["preflight"]["eligible_user_key_set_sha256"]:
        raise RuntimeError("eligible user set drift from preflight")
    source_pools = {int(value): [] for value in eligible}
    target_pools = {int(value): [] for value in eligible}
    eligible_mask = np.zeros(maximum_user + 1, dtype=bool)
    eligible_mask[eligible] = True
    second_rows = 0
    for raw_user, movie, _raw_line, _second in movie_lens_movie_rows(contract):
        if not eligible_mask[raw_user]:
            continue
        second_rows += 1
        position = int(lookup[movie]) if 0 <= movie < len(lookup) else -1
        if position < 0:
            continue
        if bool(source_mask[position]):
            source_pools[int(raw_user)].append(int(movie))
        elif bool(target_mask[position]):
            target_pools[int(raw_user)].append(int(movie))
    return eligible, source_pools, target_pools, {
        "allowed_rows_movie_id_parsed_membership_first_pass": allowed_rows,
        "eligible_rows_movie_id_parsed_membership_second_pass": second_rows,
    }


def _joint_experiment_spec(contract: Mapping[str, Any]) -> Mapping[str, Any]:
    joint = read_json(resolve_input(contract["allowed_input_artifacts"]["joint_design"]))
    return joint["experiments"][contract["evidence_id"]]


def _prepared_artifacts(contract: Mapping[str, Any]) -> dict[str, Path]:
    return {
        "item_ids": output_path(contract, "item_ids"),
        "feature_full": output_path(contract, "feature_full"),
        "score_input": output_path(contract, "score_input"),
    }


def _verify_prior(contract: Mapping[str, Any]) -> np.ndarray:
    with np.load(resolve_input(contract["allowed_input_artifacts"]["train_prior"]), allow_pickle=False) as prior:
        if "g0_mid" not in prior.files:
            raise RuntimeError("g0_mid prior missing")
        values = prior["g0_mid"]
    expected = contract["rating_scale"]
    if list(values.shape) != expected["prior_shape"] or values.dtype.str != expected["prior_dtype"]:
        raise RuntimeError("g0_mid shape or dtype drift")
    if hashlib.sha256(values.tobytes(order="C")).hexdigest() != expected["prior_c_order_value_bytes_sha256"]:
        raise RuntimeError("g0_mid value drift")
    return values.astype(np.float64)


def open_input_ratings_after_membership_seal(
    contract: Mapping[str, Any], input_allowlist: Mapping[int, set[int]],
    membership_frame: pd.DataFrame, membership_metadata: Mapping[str, Any], signature: str,
) -> tuple[dict[int, dict[int, int]], int]:
    membership_path = output_path(contract, "membership")
    verify_integrity(
        output_path(contract, "membership_integrity"), {"membership": membership_path},
        signature=signature, expected_metadata=membership_metadata,
    )
    require_same_frame(membership_frame, pd.read_parquet(membership_path), "membership before input rating open")
    return _profile_rating_pass(contract, input_allowlist)


def prepare(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    artifacts = _prepared_artifacts(contract)
    integrity_path = output_path(contract, "prepared_integrity")
    prepared_reuse = sealed_group_state(
        contract, "SCORE_INPUT_OPEN", [integrity_path, *artifacts.values()],
    )
    membership_path = output_path(contract, "membership")
    membership_integrity = output_path(contract, "membership_integrity")
    membership_reuse = sealed_group_state(
        contract, "MEMBERSHIP_SEALED", [membership_path, membership_integrity],
    )
    structured = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["structured_features"]))
    movie_ids, heads, years, _ = build_feature_heads(structured)
    feature_full = heads["FULL_CURRENT"]
    _verify_prior(contract)
    eligible, source_pools, target_pools, reader = _eligible_and_pools(contract, movie_ids, years)
    joint_spec = _joint_experiment_spec(contract)
    preflight = read_json(resolve_input(contract["allowed_input_artifacts"]["preflight_result"]))
    recomputed_summary = _experiment_summary(
        contract["evidence_id"], eligible, source_pools, target_pools, joint_spec,
    )
    if recomputed_summary != preflight["experiments"][contract["evidence_id"]]:
        raise RuntimeError("preflight role reconstruction drift")
    if recomputed_summary["input_evaluation_intersection"] != 0:
        raise RuntimeError("preflight input/evaluation overlap")
    memberships: list[dict[str, Any]] = []
    input_allowlist: dict[int, set[int]] = {int(value): set() for value in eligible}
    for raw_user in eligible.tolist():
        anonymous = user_key(int(raw_user))
        roles = partition_user(anonymous, source_pools[int(raw_user)], target_pools[int(raw_user)], joint_spec)
        input_allowlist[int(raw_user)].update(int(movie) for movie in roles["profile"] + roles["anchors"])
        for panel in roles["panels"]:
            memberships.append({
                "raw_user": int(raw_user), "user_key": anonymous, "panel": int(panel["panel"]),
                "profile_movie_ids": [int(movie) for movie in roles["profile"]],
                "anchor_movie_ids": [int(movie) for movie in roles["anchors"]],
                "target_movie_ids": [int(movie) for movie in panel["evaluation"]],
                "control_movie_ids": [int(movie) for movie in panel["control"]],
            })
    membership_frame = pd.DataFrame([
        {key: value for key, value in membership.items() if key != "raw_user"}
        for membership in memberships
    ]).sort_values(["user_key", "panel"], kind="stable", ignore_index=True)
    membership_metadata = {
        **reader,
        "eligible_users": len(eligible), "panels": len(membership_frame),
        "eligible_user_key_set_sha256": contract["preflight"]["eligible_user_key_set_sha256"],
        "preflight_experiment_summary_sha256": hashlib.sha256(canonical_json_bytes(recomputed_summary)).hexdigest(),
        "rating_value_bytes_parsed": 0, "timestamp_bytes_parsed": 0,
        "input_evaluation_intersection": 0, "raw_user_ids_written": False,
    }
    if membership_reuse:
        verify_integrity(
            membership_integrity, {"membership": membership_path}, signature=signature,
            expected_metadata=membership_metadata,
        )
        require_same_frame(membership_frame, pd.read_parquet(membership_path), "membership seal")
    else:
        atomic_to_parquet(membership_path, membership_frame)
        write_integrity(
            membership_integrity, {"membership": membership_path}, signature=signature,
            metadata=membership_metadata,
        )
    reconcile_progress(contract, "MEMBERSHIP_SEALED", len(memberships))
    ratings, rating_rows = open_input_ratings_after_membership_seal(
        contract, input_allowlist, membership_frame, membership_metadata, signature,
    )
    rows: list[dict[str, Any]] = []
    for membership in memberships:
        raw_user = int(membership["raw_user"])
        profile = membership["profile_movie_ids"]
        anchors = membership["anchor_movie_ids"]
        input_set = set(profile) | set(anchors)
        label_set = set(membership["target_movie_ids"]) | set(membership["control_movie_ids"])
        if input_set & label_set:
            raise RuntimeError("input/evaluation overlap before rating parse")
        rows.append({
            **{key: value for key, value in membership.items() if key != "raw_user"},
            "profile_rating_idx": [ratings[raw_user][movie] for movie in profile],
            "anchor_rating_idx": [ratings[raw_user][movie] for movie in anchors],
        })
    score_input = pd.DataFrame(rows).sort_values(["user_key", "panel"], kind="stable", ignore_index=True)
    metadata = {
        **reader,
        "eligible_users": len(eligible),
        "eligible_user_key_set_sha256": contract["preflight"]["eligible_user_key_set_sha256"],
        "panels": len(score_input),
        "universe_items": len(movie_ids),
        "membership_rating_bytes_parsed": 0,
        "selected_input_rating_bytes_parsed": rating_rows,
        "evaluation_rating_bytes_parsed_before_rank_seal": 0,
        "timestamp_bytes_parsed": 0,
        "input_evaluation_intersection": 0,
        "evaluation_labels_opened_before_rank_seal": False,
        "raw_user_ids_written": False,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
    }
    if prepared_reuse:
        verify_integrity(integrity_path, artifacts, signature=signature, expected_metadata=metadata)
        if not np.array_equal(np.load(artifacts["item_ids"], allow_pickle=False), movie_ids):
            raise ResumeError("prepared item ids semantic drift")
        require_same_sparse(feature_full, sparse.load_npz(artifacts["feature_full"]), "FULL_CURRENT feature")
        require_same_frame(score_input, pd.read_parquet(artifacts["score_input"]), "score input")
        reconcile_progress(contract, "SCORE_INPUT_OPEN", len(score_input))
        return {"status": "REUSED_EXACT_SCORE_INPUT", **metadata}
    atomic_save_npy(artifacts["item_ids"], movie_ids)
    atomic_save_sparse(artifacts["feature_full"], feature_full)
    atomic_to_parquet(artifacts["score_input"], score_input)
    write_integrity(integrity_path, artifacts, signature=signature, metadata=metadata)
    reconcile_progress(contract, "SCORE_INPUT_OPEN", len(score_input))
    return {"status": "SCORE_INPUT_OPEN", **metadata}


def strict_policy_order(
    contract: Mapping[str, Any], user: str, panel: int, domain: str, encoding: str,
    k: int, movie_ids: Sequence[int], scores: Sequence[float],
) -> list[int]:
    movies = [int(movie) for movie in movie_ids]
    values = np.asarray(scores, dtype=np.float64)
    if len(movies) != len(values) or not np.isfinite(values).all():
        raise ValueError("rank input drift")
    prefix = str(contract["scoring"]["tie_prefix"])
    digests = [hashlib.sha256(
        f"{prefix}|{user}|{canonical_decimal(panel)}|{domain}|{encoding}|{canonical_decimal(k)}|{canonical_decimal(movie)}".encode("utf-8")
    ).digest() for movie in movies]
    order = sorted(range(len(movies)), key=lambda index: (-float(values[index]), digests[index], movies[index]))
    return [movies[index] for index in order]


def policy_profile(value: Any, policy: str, k: int) -> tuple[list[int], np.ndarray]:
    profile_movies = [int(movie) for movie in value.profile_movie_ids]
    profile_ratings = np.asarray([float(RATING_VALUES[int(index)]) for index in value.profile_rating_idx], dtype=np.float64)
    anchor_movies = [int(movie) for movie in value.anchor_movie_ids]
    anchor_ratings = np.asarray([float(RATING_VALUES[int(index)]) for index in value.anchor_rating_idx], dtype=np.float64)
    if len(profile_movies) != 14 or len(anchor_movies) != 2:
        raise ValueError("policy master profile drift")
    if policy == "SOURCE_ONLY":
        return profile_movies[:k], profile_ratings[:k]
    if policy == "TARGET2_MIXED":
        return profile_movies[:k - 2] + anchor_movies, np.concatenate([profile_ratings[:k - 2], anchor_ratings])
    raise ValueError("unknown policy")


def build_rank_frame(
    contract: Mapping[str, Any], selected: pd.DataFrame, lookup: Mapping[int, int],
    matrix: sparse.csr_matrix, g0_mid: np.ndarray,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for value in selected.itertuples(index=False):
        for domain, candidates_value in (("TARGET", value.target_movie_ids), ("CONTROL", value.control_movie_ids)):
            candidates = [int(movie) for movie in candidates_value]
            candidate_positions = np.asarray([lookup[movie] for movie in candidates], dtype=np.int64)
            for cell in contract["cells"]:
                encoding, k = str(cell["encoding"]), int(cell["k"])
                for policy in POLICIES:
                    profile_movies, profile_ratings = policy_profile(value, policy, k)
                    profile_positions = np.asarray([lookup[movie] for movie in profile_movies], dtype=np.int64)
                    similarities = (matrix[candidate_positions] @ matrix[profile_positions].T).toarray().astype(np.float64)
                    weights = encoding_weights(encoding, profile_ratings, g0_mid, tau=5.0)
                    scores, active = active_scores(similarities, weights)
                    ranked = strict_policy_order(
                        contract, str(value.user_key), int(value.panel), domain, encoding, k, candidates, scores,
                    ) if active else []
                    rows.append({
                        "user_key": str(value.user_key), "panel": int(value.panel), "domain": domain,
                        "encoding": encoding, "k": k, "policy": policy, "active": active,
                        "ranked_movie_ids": ranked,
                    })
    return pd.DataFrame(rows).sort_values(
        ["user_key", "panel", "domain", "encoding", "k", "policy"], kind="stable", ignore_index=True,
    )


def _part_path(root: Path, start: int, stop: int) -> Path:
    return root / f"part-{start:06d}-{stop:06d}.parquet"


def score(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    prepare(contract)
    score_input = pd.read_parquet(output_path(contract, "score_input"))
    item_ids = np.load(output_path(contract, "item_ids"), allow_pickle=False).astype(np.int64)
    feature = sparse.load_npz(output_path(contract, "feature_full")).tocsr()
    lookup = {int(movie): index for index, movie in enumerate(item_ids.tolist())}
    g0_mid = _verify_prior(contract)
    keys = sorted(score_input["user_key"].astype(str).unique().tolist())
    if len(keys) != int(contract["preflight"]["expected_users"]):
        raise RuntimeError("score user cardinality drift")
    rank_path = output_path(contract, "rank")
    rank_integrity = output_path(contract, "rank_integrity")
    rank_reuse = sealed_group_state(contract, "RANK_SEALED", [rank_path, rank_integrity])
    parts_root = output_path(contract, "rank_parts")
    if parts_root.exists() and not parts_root.is_dir():
        raise ResumeError("rank parts path is not a directory")
    if not parts_root.exists():
        if progress_index(contract) >= PHASE_INDEX["RANK_SEALED"]:
            raise ResumeError("progress ahead of absent rank parts directory")
        parts_root.mkdir(parents=True, exist_ok=True)
    chunks = [(start, min(start + 64, len(keys))) for start in range(0, len(keys), 64)]
    expected_parts = {_part_path(parts_root, start, stop) for start, stop in chunks}
    unexpected = set(parts_root.glob("part-*.parquet")) - expected_parts
    expected_integrities = {path.with_suffix(".integrity.json") for path in expected_parts}
    unexpected_integrities = set(parts_root.glob("part-*.integrity.json")) - expected_integrities
    if unexpected or unexpected_integrities:
        raise ResumeError("unexpected rank part state")
    for start, stop in chunks:
        destination = _part_path(parts_root, start, stop)
        integrity = destination.with_suffix(".integrity.json")
        part_reuse = sealed_group_state(contract, "RANK_SEALED", [destination, integrity])
        selected_keys = keys[start:stop]
        selected = score_input.loc[score_input["user_key"].isin(selected_keys)]
        frame = build_rank_frame(contract, selected, lookup, feature, g0_mid)
        metadata = {"start": start, "stop": stop, "user_keys": selected_keys, "evaluation_labels_opened": False}
        if part_reuse:
            verify_integrity(integrity, {"rank_part": destination}, signature=signature, expected_metadata=metadata)
            require_same_frame(frame, pd.read_parquet(destination), "rank part")
        else:
            atomic_to_parquet(destination, frame)
            write_integrity(integrity, {"rank_part": destination}, signature=signature, metadata=metadata)
    combined = pd.concat([pd.read_parquet(path) for path in sorted(expected_parts)], ignore_index=True)
    combined = combined.sort_values(["user_key", "panel", "domain", "encoding", "k", "policy"], kind="stable", ignore_index=True)
    expected_rows = len(keys) * int(contract["cohort"]["panels"]) * len(DOMAINS) * len(contract["cells"]) * len(POLICIES)
    metadata = {
        "users": len(keys), "rows": expected_rows, "parts": len(chunks),
        "evaluation_labels_opened": False, "evaluation_rating_bytes_parsed": 0,
        "timestamp_bytes_parsed": 0, "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
    }
    if len(combined) != expected_rows or combined.duplicated(["user_key", "panel", "domain", "encoding", "k", "policy"]).any():
        raise RuntimeError("combined rank Cartesian drift")
    if rank_reuse:
        verify_integrity(rank_integrity, {"score_rank": rank_path}, signature=signature, expected_metadata=metadata)
        require_same_frame(combined, pd.read_parquet(rank_path), "combined rank")
        reconcile_progress(contract, "RANK_SEALED", len(keys))
        return {"status": "REUSED_EXACT_RANK", **metadata}
    atomic_to_parquet(rank_path, combined)
    write_integrity(rank_integrity, {"score_rank": rank_path}, signature=signature, metadata=metadata)
    reconcile_progress(contract, "RANK_SEALED", len(keys))
    return {"status": "RANK_SEALED", **metadata}


def _verify_input_label_disjoint(score_input: pd.DataFrame) -> None:
    for value in score_input.itertuples(index=False):
        inputs = set(int(movie) for movie in value.profile_movie_ids) | set(int(movie) for movie in value.anchor_movie_ids)
        labels = set(int(movie) for movie in value.target_movie_ids) | set(int(movie) for movie in value.control_movie_ids)
        if inputs & labels:
            raise RuntimeError("input/evaluation overlap at label-open transition")


def open_evaluation_after_rank_seal(
    contract: Mapping[str, Any], score_input: pd.DataFrame, signature: str,
) -> tuple[pd.DataFrame, dict[str, int]]:
    rank_path = output_path(contract, "rank")
    verify_integrity(
        output_path(contract, "rank_integrity"), {"score_rank": rank_path}, signature=signature,
    )
    _verify_input_label_disjoint(score_input)
    return _evaluation_label_pass(contract, score_input)


def evaluation(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    score(contract)
    score_input = pd.read_parquet(output_path(contract, "score_input"))
    label_source, parsed = open_evaluation_after_rank_seal(contract, score_input, signature)
    labels = build_evaluation_labels(label_source)
    artifacts = {
        "label_source": output_path(contract, "label_source"),
        "evaluation_labels": output_path(contract, "evaluation_labels"),
    }
    integrity = output_path(contract, "labels_integrity")
    reuse = sealed_group_state(contract, "EVALUATION_OPEN", [integrity, *artifacts.values()])
    metadata = {
        **parsed,
        "rank_sealed_before_label_open": True,
        "users": int(contract["preflight"]["expected_users"]),
        "label_source_rows": len(label_source), "evaluation_label_rows": len(labels),
        "input_evaluation_intersection": 0,
        "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False,
    }
    if reuse:
        verify_integrity(integrity, artifacts, signature=signature, expected_metadata=metadata)
        require_same_frame(label_source, pd.read_parquet(artifacts["label_source"]), "label source")
        require_same_frame(labels, pd.read_parquet(artifacts["evaluation_labels"]), "evaluation labels")
        reconcile_progress(contract, "EVALUATION_OPEN", len(labels))
        return {"status": "REUSED_EXACT_EVALUATION", **metadata}
    atomic_to_parquet(artifacts["label_source"], label_source)
    atomic_to_parquet(artifacts["evaluation_labels"], labels)
    write_integrity(integrity, artifacts, signature=signature, metadata=metadata)
    reconcile_progress(contract, "EVALUATION_OPEN", len(labels))
    return {"status": "EVALUATION_OPEN", **metadata}


def build_policy_metrics(ranks: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    label_lookup: dict[tuple[str, int, str], tuple[list[int], np.ndarray]] = {}
    for value in labels.itertuples(index=False):
        label_lookup[(str(value.user_key), int(value.panel), str(value.domain))] = (
            [int(movie) for movie in value.movie_ids], np.asarray(value.q, dtype=np.float64),
        )
    rows: list[dict[str, Any]] = []
    for value in ranks.itertuples(index=False):
        movies, q = label_lookup[(str(value.user_key), int(value.panel), str(value.domain))]
        random_utility, random_loss = analytic_random_top2(q)
        if bool(value.active):
            ranked = [int(movie) for movie in value.ranked_movie_ids]
            if len(ranked) != len(movies) or set(ranked) != set(movies):
                raise RuntimeError("rank/label identity drift")
            q_by_movie = {movie: float(label) for movie, label in zip(movies, q, strict=True)}
            top = np.asarray([q_by_movie[movie] for movie in ranked[:2]], dtype=np.float64)
            utility = float(top.mean())
            loss = float(1.0 - top.min())
        else:
            utility, loss = random_utility, random_loss
        rows.append({
            "user_key": str(value.user_key), "panel": int(value.panel), "domain": str(value.domain),
            "encoding": str(value.encoding), "k": int(value.k), "policy": str(value.policy),
            "active": bool(value.active), "model_utility": utility, "model_loss": loss,
            "random_utility": random_utility, "random_loss": random_loss,
            "utility_minus_random": utility - random_utility,
            "safety_minus_random": random_loss - loss,
        })
    return pd.DataFrame(rows).sort_values(
        ["user_key", "panel", "domain", "encoding", "k", "policy"], kind="stable", ignore_index=True,
    )


def contrast_metadata(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cell in contract["cells"]:
        for domain in DOMAINS:
            for endpoint in ENDPOINTS:
                rows.append({
                    "contrast_index": len(rows), "encoding": str(cell["encoding"]),
                    "k": int(cell["k"]), "domain": domain, "endpoint": endpoint,
                })
    return rows


def build_user_contrasts(metrics: pd.DataFrame, contract: Mapping[str, Any]) -> pd.DataFrame:
    if metrics.duplicated(["user_key", "panel", "domain", "encoding", "k", "policy"]).any():
        raise RuntimeError("policy metric duplicate")
    indexed = metrics.set_index(["user_key", "panel", "domain", "encoding", "k", "policy"])
    rows: list[dict[str, Any]] = []
    for key in sorted(metrics["user_key"].astype(str).unique().tolist()):
        for meta in contrast_metadata(contract):
            values: list[float] = []
            for panel in range(int(contract["cohort"]["panels"])):
                source = indexed.loc[(key, panel, meta["domain"], meta["encoding"], meta["k"], "SOURCE_ONLY")]
                mixed = indexed.loc[(key, panel, meta["domain"], meta["encoding"], meta["k"], "TARGET2_MIXED")]
                if meta["endpoint"] == "UTILITY_EFFECT":
                    values.append(float(mixed.model_utility) - float(source.model_utility))
                else:
                    values.append(float(source.model_loss) - float(mixed.model_loss))
            rows.append({"user_key": key, **meta, "value": float(np.mean(values))})
    return pd.DataFrame(rows).sort_values(["user_key", "contrast_index"], kind="stable", ignore_index=True)


def materialize_metrics(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    evaluation(contract)
    ranks = pd.read_parquet(output_path(contract, "rank"))
    labels = pd.read_parquet(output_path(contract, "evaluation_labels"))
    panel_metrics = build_policy_metrics(ranks, labels)
    contrasts = build_user_contrasts(panel_metrics, contract)
    users = int(contract["preflight"]["expected_users"])
    expected_metric_rows = users * 4 * 2 * 6 * 2
    expected_contrast_rows = users * 24
    if len(panel_metrics) != expected_metric_rows or len(contrasts) != expected_contrast_rows:
        raise RuntimeError("metric/contrast cardinality drift")
    metric_path = output_path(contract, "panel_metrics")
    metric_integrity = output_path(contract, "panel_metrics_integrity")
    contrast_path = output_path(contract, "user_contrasts")
    contrast_integrity = output_path(contract, "user_contrasts_integrity")
    metric_metadata = {"users": users, "rows": len(panel_metrics), "rank_sealed_before_label_open": True}
    contrast_metadata_value = {
        "users": users, "rows": len(contrasts), "contrasts": 24,
        "contrast_metadata_sha256": hashlib.sha256(canonical_json_bytes(contrast_metadata(contract))).hexdigest(),
    }
    reuse = sealed_group_state(
        contract, "METRICS_SEALED", [metric_path, metric_integrity, contrast_path, contrast_integrity],
    )
    if reuse:
        verify_integrity(metric_integrity, {"panel_metrics": metric_path}, signature=signature, expected_metadata=metric_metadata)
        verify_integrity(contrast_integrity, {"user_contrasts": contrast_path}, signature=signature, expected_metadata=contrast_metadata_value)
        require_same_frame(panel_metrics, pd.read_parquet(metric_path), "panel metrics")
        require_same_frame(contrasts, pd.read_parquet(contrast_path), "user contrasts")
        reconcile_progress(contract, "METRICS_SEALED", len(contrasts))
        return {"status": "REUSED_EXACT_METRICS", **contrast_metadata_value}
    atomic_to_parquet(metric_path, panel_metrics)
    write_integrity(metric_integrity, {"panel_metrics": metric_path}, signature=signature, metadata=metric_metadata)
    atomic_to_parquet(contrast_path, contrasts)
    write_integrity(contrast_integrity, {"user_contrasts": contrast_path}, signature=signature, metadata=contrast_metadata_value)
    reconcile_progress(contract, "METRICS_SEALED", len(contrasts))
    return {"status": "METRICS_SEALED", **contrast_metadata_value}


def poisson_user_weight(evidence_id: str, attempt: int, key: str, cutoffs: Sequence[int]) -> tuple[int, int]:
    payload = f"feelm-bootstrap-v1|rec-ev-024ab-anchor-user-bootstrap-v1|{evidence_id}|{canonical_decimal(attempt)}|user|{key}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)
    return bisect.bisect_left(cutoffs, value), value


def verify_poisson_golden(contract: Mapping[str, Any], cutoffs: Sequence[int]) -> None:
    joint = read_json(resolve_input(contract["allowed_input_artifacts"]["joint_design"]))
    fixtures = [row for row in joint["bootstrap"]["golden_fixtures"] if row["evidence_id"] == contract["evidence_id"]]
    if len(fixtures) != 2:
        raise RuntimeError("Poisson golden fixture set drift")
    for row in fixtures:
        weight, value = poisson_user_weight(str(row["evidence_id"]), int(row["attempt"]), str(row["user_key"]), cutoffs)
        if weight != int(row["weight"]) or value != int(row["uint64"]):
            raise RuntimeError("Poisson golden drift")


def contrast_matrix(contract: Mapping[str, Any], frame: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    observed = frame[["contrast_index", "encoding", "k", "domain", "endpoint"]].drop_duplicates().sort_values("contrast_index", kind="stable").to_dict("records")
    if observed != contrast_metadata(contract):
        raise RuntimeError("contrast metadata drift")
    keys = sorted(frame["user_key"].astype(str).unique().tolist())
    ordered = frame.sort_values(["user_key", "contrast_index"], kind="stable", ignore_index=True)
    if len(ordered) != len(keys) * 24 or ordered.duplicated(["user_key", "contrast_index"]).any():
        raise RuntimeError("contrast Cartesian drift")
    return keys, ordered["value"].to_numpy(dtype=np.float64).reshape(len(keys), 24)


def compute_bootstrap_arrays(
    contract: Mapping[str, Any], keys: Sequence[str], values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.shape != (len(keys), 24) or not np.isfinite(matrix).all():
        raise ValueError("bootstrap input drift")
    point = matrix.mean(axis=0)
    cutoffs = poisson_cutoffs(precision=80)
    verify_poisson_golden(contract, cutoffs)
    valid_attempts: list[int] = []
    invalid_attempts: list[int] = []
    replicates: list[np.ndarray] = []
    for attempt in range(8000):
        weights = np.fromiter(
            (poisson_user_weight(contract["evidence_id"], attempt, key, cutoffs)[0] for key in keys),
            dtype=np.float64, count=len(keys),
        )
        denominator = float(weights.sum())
        if not math.isfinite(denominator) or denominator <= 0:
            invalid_attempts.append(attempt)
            continue
        estimate = (weights @ matrix) / denominator
        if not np.isfinite(estimate).all():
            invalid_attempts.append(attempt)
            continue
        valid_attempts.append(attempt)
        replicates.append(np.asarray(estimate, dtype=np.float64))
        if len(replicates) == 4000:
            break
    if len(replicates) != 4000:
        raise RuntimeError("fewer than 4,000 valid bootstrap replicates")
    replicate_matrix = np.vstack(replicates)
    metadata = {
        "users": len(keys), "contrasts": 24, "valid_replicates": 4000,
        "invalid_attempts": len(invalid_attempts), "first_valid_attempt": valid_attempts[0],
        "last_valid_attempt": valid_attempts[-1], "poisson_golden_verified": True,
        "primary_regime": "USER_ONLY",
    }
    return (
        point, replicate_matrix, np.asarray(valid_attempts, dtype=np.int32),
        np.asarray(invalid_attempts, dtype=np.int32), metadata,
    )


def bootstrap(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    materialize_metrics(contract)
    contrasts = pd.read_parquet(output_path(contract, "user_contrasts"))
    keys, values = contrast_matrix(contract, contrasts)
    point, replicates, valid, invalid, metadata = compute_bootstrap_arrays(contract, keys, values)
    destination = output_path(contract, "bootstrap")
    integrity = output_path(contract, "bootstrap_integrity")
    reuse = sealed_group_state(contract, "BOOTSTRAP_SEALED", [destination, integrity])
    if reuse:
        verify_integrity(integrity, {"bootstrap": destination}, signature=signature, expected_metadata=metadata)
        with np.load(destination, allow_pickle=False) as cached:
            if set(cached.files) != {"point", "replicates", "valid_attempt_ids", "invalid_attempt_ids"}:
                raise ResumeError("bootstrap schema drift")
            matches = (
                np.array_equal(cached["point"], point) and np.array_equal(cached["replicates"], replicates)
                and np.array_equal(cached["valid_attempt_ids"], valid) and np.array_equal(cached["invalid_attempt_ids"], invalid)
            )
        if not matches:
            raise ResumeError("bootstrap semantic drift")
        reconcile_progress(contract, "BOOTSTRAP_SEALED", len(replicates))
        return {"status": "REUSED_EXACT_BOOTSTRAP", **metadata}
    atomic_save_npz(destination, point=point, replicates=replicates, valid_attempt_ids=valid, invalid_attempt_ids=invalid)
    write_integrity(integrity, {"bootstrap": destination}, signature=signature, metadata=metadata)
    reconcile_progress(contract, "BOOTSTRAP_SEALED", len(replicates))
    return {"status": "BOOTSTRAP_SEALED", **metadata}


def simultaneous_intervals(point: np.ndarray, replicates: np.ndarray) -> tuple[list[dict[str, Any]], float]:
    point = np.asarray(point, dtype=np.float64)
    matrix = np.asarray(replicates, dtype=np.float64)
    if point.shape != (24,) or matrix.shape != (4000, 24):
        raise ValueError("simultaneous family shape drift")
    se = matrix.std(axis=0, ddof=1)
    estimable = np.isfinite(se) & (se > 0)
    critical = 0.0
    if bool(estimable.any()):
        maxima = np.max(np.abs((matrix[:, estimable] - point[estimable]) / se[estimable]), axis=1)
        critical = nearest_rank(maxima, 0.975)
    rows: list[dict[str, Any]] = []
    for index in range(24):
        width = float(critical * se[index]) if estimable[index] else None
        rows.append({
            "contrast_index": index, "mean": float(point[index]),
            "se": float(se[index]) if np.isfinite(se[index]) else None,
            "estimable": bool(estimable[index]), "half_width": width,
            "low": float(point[index] - width) if width is not None else None,
            "high": float(point[index] + width) if width is not None else None,
        })
    return rows, critical


def decision_from_intervals(
    contract: Mapping[str, Any], interval_rows: Sequence[Mapping[str, Any]], panel_metrics: pd.DataFrame,
) -> dict[str, Any]:
    intervals = [{**contrast_metadata(contract)[index], **dict(row)} for index, row in enumerate(interval_rows)]
    lookup = {(row["encoding"], int(row["k"]), row["domain"], row["endpoint"]): row for row in intervals}
    indexed = panel_metrics.set_index(["user_key", "panel", "domain", "encoding", "k", "policy"])
    panel_points: dict[tuple[str, int, str, str, int], float] = {}
    keys = sorted(panel_metrics["user_key"].astype(str).unique().tolist())
    for cell in contract["cells"]:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        for domain in DOMAINS:
            for endpoint in ENDPOINTS:
                for panel in range(4):
                    effects: list[float] = []
                    for key in keys:
                        source = indexed.loc[(key, panel, domain, encoding, k, "SOURCE_ONLY")]
                        mixed = indexed.loc[(key, panel, domain, encoding, k, "TARGET2_MIXED")]
                        effect = (
                            float(mixed.model_utility) - float(source.model_utility)
                            if endpoint == "UTILITY_EFFECT"
                            else float(source.model_loss) - float(mixed.model_loss)
                        )
                        effects.append(effect)
                    panel_points[(encoding, k, domain, endpoint, panel)] = float(np.mean(effects))
    cells: list[dict[str, Any]] = []
    max_width = float(contract["decision"]["maximum_half_width"])
    for cell in contract["cells"]:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        target_rows = [lookup[(encoding, k, "TARGET", endpoint)] for endpoint in ENDPOINTS]
        control_rows = [lookup[(encoding, k, "CONTROL", endpoint)] for endpoint in ENDPOINTS]
        target_points = [panel_points[(encoding, k, "TARGET", endpoint, panel)] for endpoint in ENDPOINTS for panel in range(4)]
        control_points = [panel_points[(encoding, k, "CONTROL", endpoint, panel)] for endpoint in ENDPOINTS for panel in range(4)]
        target_pass = all(
            row["estimable"] and float(row["low"]) >= float(contract["decision"]["target_margin"])
            and float(row["half_width"]) <= max_width for row in target_rows
        ) and all(value > 0 for value in target_points)
        control_pass = all(
            row["estimable"] and float(row["low"]) >= float(contract["decision"]["control_noninferiority_margin"])
            and float(row["half_width"]) <= max_width for row in control_rows
        ) and all(value >= float(contract["decision"]["control_noninferiority_margin"]) for value in control_points)
        cells.append({
            "encoding": encoding, "k": k, "target_pass": target_pass, "control_pass": control_pass,
            "target_panel_points": target_points, "control_panel_points": control_points,
        })
    imprecise = any(
        not row["estimable"] or row["half_width"] is None or float(row["half_width"]) > max_width
        for row in intervals
    )
    all_target = all(row["target_pass"] for row in cells)
    all_joint = all(row["target_pass"] and row["control_pass"] for row in cells)
    any_joint = any(row["target_pass"] and row["control_pass"] for row in cells)
    any_target = any(row["target_pass"] for row in cells)
    if contract["preflight"]["status"] != "FEASIBLE_PRELABEL":
        status = "INFEASIBLE_PRELABEL"
    elif imprecise:
        status = "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE"
    elif all_joint:
        status = "ROBUST_INPUT_REMEDY"
    elif all_target:
        status = "TARGET_ROBUST_CONTROL_DEGRADED"
    elif any_joint:
        status = "CELL_SPECIFIC_SIGNAL_NOT_ROBUST"
    elif any_target:
        status = "TARGET_CELL_SIGNAL_CONTROL_DEGRADED"
    else:
        status = "NO_SUFFICIENT_INPUT_REMEDY"
    return {"status": status, "precision_or_estimability_failure": imprecise, "cell_truth": cells}


def _auxiliary_summary(metrics: pd.DataFrame) -> list[dict[str, Any]]:
    columns = ["model_utility", "model_loss", "random_utility", "random_loss", "utility_minus_random", "safety_minus_random", "active"]
    grouped = metrics.groupby(["policy", "domain", "encoding", "k"], observed=True)[columns].mean().reset_index()
    return grouped.to_dict("records")


def finalize_or_verify(
    contract: Mapping[str, Any], result: Mapping[str, Any], selection: Mapping[str, Any], metadata: Mapping[str, Any],
) -> str:
    signature = run_signature(contract)
    result_path = output_path(contract, "result")
    selection_path = output_path(contract, "selection")
    integrity = output_path(contract, "result_integrity")
    reuse = sealed_group_state(contract, "COMPLETE", [result_path, selection_path, integrity])
    if reuse:
        verify_integrity(integrity, {"result": result_path, "selection": selection_path}, signature=signature, expected_metadata=metadata)
        if read_json(result_path) != dict(result) or read_json(selection_path) != dict(selection):
            raise ResumeError("result semantic drift")
        return "REUSED_EXACT_RESULT"
    atomic_write_json(selection_path, selection)
    atomic_write_json(result_path, result)
    write_integrity(integrity, {"result": result_path, "selection": selection_path}, signature=signature, metadata=metadata)
    return "WROTE_RESULT"


def analyze(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    bootstrap(contract)
    panel_metrics = pd.read_parquet(output_path(contract, "panel_metrics"))
    contrasts = pd.read_parquet(output_path(contract, "user_contrasts"))
    keys, values = contrast_matrix(contract, contrasts)
    expected_point, expected_replicates, expected_valid, expected_invalid, bootstrap_metadata = compute_bootstrap_arrays(contract, keys, values)
    with np.load(output_path(contract, "bootstrap"), allow_pickle=False) as cached:
        if not (
            np.array_equal(cached["point"], expected_point) and np.array_equal(cached["replicates"], expected_replicates)
            and np.array_equal(cached["valid_attempt_ids"], expected_valid) and np.array_equal(cached["invalid_attempt_ids"], expected_invalid)
        ):
            raise ResumeError("bootstrap drift before result")
    verify_integrity(output_path(contract, "bootstrap_integrity"), {"bootstrap": output_path(contract, "bootstrap")}, signature=signature, expected_metadata=bootstrap_metadata)
    raw_intervals, critical = simultaneous_intervals(expected_point, expected_replicates)
    metadata_rows = contrast_metadata(contract)
    intervals = [{**metadata_rows[index], **row} for index, row in enumerate(raw_intervals)]
    decision = decision_from_intervals(contract, raw_intervals, panel_metrics)
    selection = {
        "schema_version": 1, "evidence_id": contract["evidence_id"], **decision,
        "champion": None, "product_policy_updated": False, "final_reserve_opened": False,
        "old_locked_ratings_timestamps_metrics_opened": False,
    }
    result = {
        "schema_version": 1, "evidence_id": contract["evidence_id"], "status": decision["status"],
        "run_signature": signature, "purpose": contract["purpose"], "claim_boundary": contract["claim_boundary"],
        "selection": selection, "simultaneous_intervals": intervals,
        "critical_value_97_5_percent": critical, "bootstrap": bootstrap_metadata,
        "auxiliary_random_comparisons": _auxiliary_summary(panel_metrics),
        "preflight_item_membership_concentration": {
            "global_anchor": contract["preflight"]["global_anchor"],
            "panel_evaluation": contract["preflight"]["panel_evaluation"],
            "interpretation": "DESCRIPTIVE_ONLY_NO_ITEM_GENERALIZATION",
        },
        "users": len(keys), "panels_per_user": 4, "primary_n": 2,
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True,
        "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False,
        "product_policy_updated": False, "champion": None,
    }
    result_metadata = {"status": decision["status"], "users": len(keys), "contrasts": 24, "champion": None}
    result_state = finalize_or_verify(contract, result, selection, result_metadata)
    reconcile_progress(contract, "COMPLETE", len(keys))
    return {"status": decision["status"], "users": len(keys), "contrasts": 24, "result_state": result_state}


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(contract)
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--phase", choices=("lock", "prepare", "score", "evaluation", "metrics", "bootstrap", "analyze", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    path = DEFAULTS.get(args.contract, Path(args.contract))
    path = path if path.is_absolute() else (ROOT / path).resolve()
    contract = load_contract(path)
    if args.phase == "lock":
        print(json.dumps(create_or_verify_lock(contract, resume=args.resume), ensure_ascii=False, sort_keys=True))
        return 0
    if not args.resume:
        raise ResumeError("all post-lock phases require --resume")
    create_or_verify_lock(contract, resume=True)
    phases = {"prepare": prepare, "score": score, "evaluation": evaluation, "metrics": materialize_metrics, "bootstrap": bootstrap, "analyze": analyze}
    value = analyze(contract) if args.phase == "run" else phases[args.phase](contract)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
