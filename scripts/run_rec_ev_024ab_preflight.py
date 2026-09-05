#!/usr/bin/env python3
"""Firewalled ID-only feasibility run for REC-EV-024A/B anchor-policy experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

try:
    from rec_ev_022a_core import user_key
    from run_rec_ev_023ef_preflight import (
        ResumeError,
        atomic_write_json,
        build_universe,
        canonical_json_bytes,
        implementation_rows,
        movie_id_only_rows,
        output_path,
        panel_order,
        read_json,
        resolve_input,
        sha256_file,
        source_rows,
    )
    from validate_rec_ev_024ab_contract import validate_contract
except ImportError:
    from scripts.rec_ev_022a_core import user_key
    from scripts.run_rec_ev_023ef_preflight import (
        ResumeError,
        atomic_write_json,
        build_universe,
        canonical_json_bytes,
        implementation_rows,
        movie_id_only_rows,
        output_path,
        panel_order,
        read_json,
        resolve_input,
        sha256_file,
        source_rows,
    )
    from scripts.validate_rec_ev_024ab_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-024ab-anchor-policy-design.json"


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "purpose", "independent_design_audit", "prior_invalid_preflight_incident", "authorization",
        "forbidden_input_artifacts", "roles_and_reader", "serialization", "universe", "common_design",
        "experiments", "execution_contract", "bootstrap", "train_prior_pin", "claim_boundary", "resume",
        "invariants",
    )
    return {key: contract[key] for key in keys}


def expected_lock_state(contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sources = source_rows(contract)
    implementations = implementation_rows(contract)
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-024AB-PREFLIGHT",
        "sources": sources,
        "implementation_artifacts": implementations,
        "rating_value_bytes_parsed": 0,
        "timestamp_bytes_parsed": 0,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
    }
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-024AB-PREFLIGHT",
        "status": "APPROVED_FOR_PRELABEL_FIREWALLED_FEASIBILITY",
        "contract_sha256": hashlib.sha256(canonical_json_bytes(contract)).hexdigest(),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest(),
        "source_manifest_sha256": hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        "rating_value_bytes_parsed": 0,
        "timestamp_bytes_parsed": 0,
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
        raise ResumeError("partial preflight lock state")
    manifest, lock = expected_lock_state(contract)
    if all(present):
        if not resume:
            raise ResumeError("preflight lock exists; use --resume")
        if read_json(lock_path) != lock or read_json(manifest_path) != manifest:
            raise ResumeError("preflight lock or manifest drift")
        return lock
    if resume:
        raise ResumeError("--resume requested before preflight lock exists")
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(lock_path, lock)
    return lock


def run_signature(contract: Mapping[str, Any]) -> str:
    lock = read_json(output_path(contract, "protocol_lock"))
    payload = {key: lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _records_hash(records: Sequence[str]) -> str:
    return hashlib.sha256(canonical_json_bytes(sorted(str(value) for value in records))).hexdigest()


def _degree_summary(memberships: Sequence[int]) -> dict[str, int]:
    degree: dict[int, int] = {}
    for movie in memberships:
        degree[int(movie)] = degree.get(int(movie), 0) + 1
    values = np.asarray(list(degree.values()), dtype=np.int64)
    return {
        "unique_items": int(len(degree)),
        "memberships": int(values.sum(initial=0)),
        "maximum_degree": int(values.max(initial=0)),
        "top10_degree_sum": int(np.sort(values)[-10:].sum()) if len(values) else 0,
    }


def partition_user(
    anonymous: str,
    source_movies: Sequence[int],
    target_movies: Sequence[int],
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    target_master = panel_order(anonymous, list(target_movies), str(spec["global_target_salt"]))
    source_master = panel_order(anonymous, list(source_movies), str(spec["global_source_salt"]))
    anchors = target_master[:2]
    profile = source_master[:14]
    eval_pool = target_master[2:]
    control_pool = source_master[14:]
    if len(anchors) != 2 or len(profile) != 14:
        raise RuntimeError("global role master short")
    panels: list[dict[str, Any]] = []
    for panel in range(4):
        evaluation = panel_order(anonymous, eval_pool, str(spec["panel_target_salts"][panel]))[:int(spec["target_n"])]
        control = panel_order(anonymous, control_pool, str(spec["panel_control_salts"][panel]))[:int(spec["control_n"])]
        if len(evaluation) != int(spec["target_n"]) or len(control) != int(spec["control_n"]):
            raise RuntimeError("panel evaluation role short")
        panels.append({"panel": panel, "evaluation": evaluation, "control": control})
    evaluation_union = {movie for row in panels for movie in row["evaluation"] + row["control"]}
    input_allow = set(profile) | set(anchors)
    if set(anchors) & {movie for row in panels for movie in row["evaluation"]}:
        raise RuntimeError("anchor/evaluation cross-panel overlap")
    if set(profile) & {movie for row in panels for movie in row["control"]}:
        raise RuntimeError("profile/control cross-panel overlap")
    if input_allow & evaluation_union:
        raise RuntimeError("input/evaluation cross-panel overlap")
    return {"anchors": anchors, "profile": profile, "panels": panels}


def eligibility_masks(
    korean_count: np.ndarray,
    non_korean_count: np.ndarray,
    recent_count: np.ndarray,
    old_count: np.ndarray,
    experiments: Mapping[str, Mapping[str, Any]],
) -> dict[str, np.ndarray]:
    a_spec = experiments["REC-EV-024A"]
    b_spec = experiments["REC-EV-024B"]
    return {
        "REC-EV-024A": (non_korean_count >= int(a_spec["minimum_source_ratings"]))
        & (korean_count >= int(a_spec["minimum_target_ratings"])),
        "REC-EV-024B": (old_count >= int(b_spec["minimum_source_ratings"]))
        & (recent_count >= int(b_spec["minimum_target_ratings"])),
    }


def _experiment_summary(
    evidence_id: str,
    eligible_users: np.ndarray,
    source_pools: Mapping[int, list[int]],
    target_pools: Mapping[int, list[int]],
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    anchor_records: list[str] = []
    profile_records: list[str] = []
    eval_records: list[str] = []
    control_records: list[str] = []
    anchor_movies: list[int] = []
    profile_movies: list[int] = []
    eval_movies: list[int] = []
    control_movies: list[int] = []
    for raw_user in eligible_users.tolist():
        anonymous = user_key(int(raw_user))
        roles = partition_user(anonymous, source_pools[int(raw_user)], target_pools[int(raw_user)], spec)
        for movie in roles["anchors"]:
            anchor_records.append(f"{anonymous}|ANCHOR|GLOBAL|{movie}")
            anchor_movies.append(int(movie))
        for movie in roles["profile"]:
            profile_records.append(f"{anonymous}|PROFILE|GLOBAL|{movie}")
            profile_movies.append(int(movie))
        for row in roles["panels"]:
            panel = int(row["panel"])
            for movie in row["evaluation"]:
                eval_records.append(f"{anonymous}|EVALUATION|{panel}|{movie}")
                eval_movies.append(int(movie))
            for movie in row["control"]:
                control_records.append(f"{anonymous}|CONTROL|{panel}|{movie}")
                control_movies.append(int(movie))
    users = int(len(eligible_users))
    expected = {
        "anchor": users * 2,
        "profile": users * 14,
        "evaluation": users * 4 * int(spec["target_n"]),
        "control": users * 4 * int(spec["control_n"]),
    }
    observed = {
        "anchor": len(anchor_records), "profile": len(profile_records),
        "evaluation": len(eval_records), "control": len(control_records),
    }
    if observed != expected:
        raise RuntimeError(f"{evidence_id} membership cardinality drift")
    anchor_summary = _degree_summary(anchor_movies)
    eval_summary = _degree_summary(eval_movies)
    feasible = (
        users >= int(spec["minimum_users"])
        and anchor_summary["unique_items"] >= int(spec["minimum_unique_anchors"])
        and eval_summary["unique_items"] >= int(spec["minimum_unique_evaluation_targets"])
    )
    return {
        "eligible_users": users,
        "eligible_user_key_set_sha256": _records_hash([user_key(int(value)) for value in eligible_users]),
        "global_anchor": {**anchor_summary, "exposure_sha256": _records_hash(anchor_records)},
        "global_profile": {**_degree_summary(profile_movies), "exposure_sha256": _records_hash(profile_records)},
        "panel_evaluation": {**eval_summary, "exposure_sha256": _records_hash(eval_records)},
        "panel_control": {**_degree_summary(control_movies), "exposure_sha256": _records_hash(control_records)},
        "floors": {
            "minimum_users": int(spec["minimum_users"]),
            "minimum_unique_anchors": int(spec["minimum_unique_anchors"]),
            "minimum_unique_evaluation_targets": int(spec["minimum_unique_evaluation_targets"]),
        },
        "status": "FEASIBLE_PRELABEL" if feasible else "INFEASIBLE_PRELABEL",
        "rating_value_bytes_parsed": 0,
        "timestamp_bytes_parsed": 0,
        "input_evaluation_intersection": 0,
    }


def compute_preflight_result(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    movie_ids, years, korean, universe_summary = build_universe(contract)
    maximum_movie = int(movie_ids.max(initial=0))
    position = np.full(maximum_movie + 1, -1, dtype=np.int32)
    position[movie_ids] = np.arange(len(movie_ids), dtype=np.int32)
    maximum_user = int(contract["roles_and_reader"]["maximum_user_id"])
    korean_count = np.zeros(maximum_user + 1, dtype=np.int32)
    non_korean_count = np.zeros_like(korean_count)
    recent_count = np.zeros_like(korean_count)
    old_count = np.zeros_like(korean_count)
    archive = contract["allowed_input_artifacts"]["movielens_archive"]
    archive_path = resolve_input(archive)
    allowed_rows = 0
    for raw_user, movie in movie_id_only_rows(archive_path, str(archive["member"]), maximum_user):
        allowed_rows += 1
        p = int(position[movie]) if 0 <= movie <= maximum_movie else -1
        if p < 0:
            continue
        if bool(korean[p]):
            korean_count[raw_user] += 1
        else:
            non_korean_count[raw_user] += 1
        if int(years[p]) < 2020:
            old_count[raw_user] += 1
        elif int(years[p]) <= 2023:
            recent_count[raw_user] += 1
    specs = contract["experiments"]
    masks = eligibility_masks(korean_count, non_korean_count, recent_count, old_count, specs)
    eligible_any = masks["REC-EV-024A"] | masks["REC-EV-024B"]
    pools: dict[str, dict[str, dict[int, list[int]]]] = {}
    for evidence_id, mask in masks.items():
        users = np.flatnonzero(mask)
        pools[evidence_id] = {
            "source": {int(value): [] for value in users},
            "target": {int(value): [] for value in users},
        }
    second_pass_rows = 0
    for raw_user, movie in movie_id_only_rows(archive_path, str(archive["member"]), maximum_user):
        if not bool(eligible_any[raw_user]):
            continue
        second_pass_rows += 1
        p = int(position[movie]) if 0 <= movie <= maximum_movie else -1
        if p < 0:
            continue
        if masks["REC-EV-024A"][raw_user]:
            role = "target" if bool(korean[p]) else "source"
            pools["REC-EV-024A"][role][int(raw_user)].append(int(movie))
        if masks["REC-EV-024B"][raw_user]:
            year = int(years[p])
            if year < 2020:
                pools["REC-EV-024B"]["source"][int(raw_user)].append(int(movie))
            elif year <= 2023:
                pools["REC-EV-024B"]["target"][int(raw_user)].append(int(movie))
    experiments: dict[str, Any] = {}
    for evidence_id, mask in masks.items():
        experiments[evidence_id] = _experiment_summary(
            evidence_id,
            np.flatnonzero(mask).astype(np.int32),
            pools[evidence_id]["source"],
            pools[evidence_id]["target"],
            specs[evidence_id],
        )
    return {
        "schema_version": 1,
        "evidence_id": "REC-EV-024AB-PREFLIGHT",
        "status": "PREFLIGHT_COMPLETE",
        "run_signature": signature,
        "reader": {
            "allowed_rows_movie_id_parsed_first_pass": allowed_rows,
            "eligible_rows_movie_id_parsed_second_pass": second_pass_rows,
            "movie_id_only_passes": 2,
            "rating_value_bytes_parsed": 0,
            "timestamp_bytes_parsed": 0,
            "excluded_role_counts_reported": False,
            "raw_user_ids_written": False,
        },
        "universe": universe_summary,
        "experiments": experiments,
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }


def expected_integrity(
    contract: Mapping[str, Any], result: Mapping[str, Any], progress: Mapping[str, Any], signature: str,
) -> dict[str, Any]:
    result_path = output_path(contract, "preflight")
    progress_path = output_path(contract, "progress")
    return {
        "schema_version": 1,
        "run_signature": signature,
        "artifacts": {
            "preflight": {"path": result_path.relative_to(ROOT).as_posix(), "bytes": result_path.stat().st_size, "sha256": sha256_file(result_path)},
            "progress": {"path": progress_path.relative_to(ROOT).as_posix(), "bytes": progress_path.stat().st_size, "sha256": sha256_file(progress_path)},
        },
        "metadata": {
            "status": result["status"],
            "experiments": result["experiments"],
            "rating_value_bytes_parsed": 0,
            "timestamp_bytes_parsed": 0,
        },
    }


def preflight(contract: Mapping[str, Any], *, resume: bool) -> dict[str, Any]:
    if not resume:
        raise ResumeError("post-lock preflight requires --resume")
    result_path = output_path(contract, "preflight")
    integrity_path = output_path(contract, "preflight_integrity")
    progress_path = output_path(contract, "progress")
    present = [result_path.exists(), integrity_path.exists(), progress_path.exists()]
    if any(present) and not all(present):
        raise ResumeError("partial preflight result state")
    signature = run_signature(contract)
    recomputed = compute_preflight_result(contract)
    progress = {
        "schema_version": 1,
        "phase": "PREFLIGHT_COMPLETE",
        "experiments": recomputed["experiments"],
        "run_signature": signature,
    }
    if all(present):
        if not resume:
            raise ResumeError("preflight result exists; use --resume")
        if read_json(result_path) != recomputed or read_json(progress_path) != progress:
            raise ResumeError("preflight deterministic recomputation drift")
        if read_json(integrity_path) != expected_integrity(contract, recomputed, progress, signature):
            raise ResumeError("preflight integrity drift")
        return {"status": "REUSED_EXACT_PREFLIGHT", "experiments": recomputed["experiments"]}
    atomic_write_json(result_path, recomputed)
    atomic_write_json(progress_path, progress)
    atomic_write_json(integrity_path, expected_integrity(contract, recomputed, progress, signature))
    return {"status": "WROTE_PREFLIGHT", "experiments": recomputed["experiments"]}


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(contract)
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--phase", choices=("lock", "preflight", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    path = Path(args.contract)
    path = path if path.is_absolute() else (ROOT / path).resolve()
    contract = load_contract(path)
    if args.phase == "lock":
        print(json.dumps(create_or_verify_lock(contract, resume=args.resume), ensure_ascii=False, sort_keys=True))
        return 0
    create_or_verify_lock(contract, resume=True)
    print(json.dumps(preflight(contract, resume=args.resume), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
