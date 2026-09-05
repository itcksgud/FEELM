#!/usr/bin/env python3
"""ID-only common-support feasibility for REC-EV-025A/B."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

try:
    from rec_ev_022a_core import user_key
    from run_rec_ev_023ef_preflight import (
        ResumeError,
        atomic_write_json,
        canonical_json_bytes,
        implementation_rows,
        movie_id_only_rows,
        output_path,
        read_json,
        resolve_input,
        sha256_file,
        source_rows,
    )
    from validate_rec_ev_025ab_contract import validate_contract
except ImportError:
    from scripts.rec_ev_022a_core import user_key
    from scripts.run_rec_ev_023ef_preflight import (
        ResumeError,
        atomic_write_json,
        canonical_json_bytes,
        implementation_rows,
        movie_id_only_rows,
        output_path,
        read_json,
        resolve_input,
        sha256_file,
        source_rows,
    )
    from scripts.validate_rec_ev_025ab_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-025ab-feature-transfer-design.json"


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "purpose", "independent_design_audit", "motivation_only", "prior_invalid_preflight_incident",
        "authorization", "forbidden_input_artifacts", "reader", "common_support", "serialization", "heads",
        "common_design", "experiments", "execution_statistics", "decision", "bootstrap", "claim_boundary",
        "resume", "invariants",
    )
    return {key: contract[key] for key in keys}


def expected_lock_state(contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_contract(contract)
    sources = source_rows(contract)
    implementations = implementation_rows(contract)
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-025AB-PREFLIGHT",
        "sources": sources,
        "implementation_artifacts": implementations,
        "rating_value_bytes_parsed": 0,
        "timestamp_bytes_parsed": 0,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
    }
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-025AB-PREFLIGHT",
        "status": "LOCKED_COMMON_SUPPORT_ID_ONLY_PREFLIGHT",
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
        raise ResumeError("partial preflight lock")
    if resume and not any(present):
        # An absent lock is a state error, not an invitation to hash or scan any
        # source.  Keep this check ahead of expected_lock_state().
        raise ResumeError("--resume before preflight lock")
    manifest, lock = expected_lock_state(contract)
    if all(present):
        if not resume:
            raise ResumeError("preflight lock exists; use --resume")
        if read_json(lock_path) != lock or read_json(manifest_path) != manifest:
            raise ResumeError("preflight lock drift")
        return lock
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(lock_path, lock)
    return lock


def run_signature(contract: Mapping[str, Any]) -> str:
    lock = read_json(output_path(contract, "protocol_lock"))
    payload = {key: lock[key] for key in ("contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256")}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_common_support(contract: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    structured = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["structured_features"]))
    required = {"movie_id", "feature_eligible", "release_year", "genre_ids", "original_language", "director_ids", "top5_cast_ids", "keyword_ids"}
    if not required.issubset(structured.columns) or structured["movie_id"].duplicated().any():
        raise RuntimeError("structured schema or identity drift")
    def nonempty(value: Any) -> bool:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return False
        try:
            return len(value) > 0
        except TypeError:
            return False

    current_full_nonzero = (
        structured["genre_ids"].map(nonempty)
        | structured["original_language"].notna()
        | structured["release_year"].notna()
        | structured.get("runtime_minutes", pd.Series(False, index=structured.index)).notna()
        | structured["director_ids"].map(nonempty)
        | structured["top5_cast_ids"].map(nonempty)
        | structured["keyword_ids"].map(nonempty)
    )
    structured_ok = (
        structured["feature_eligible"].fillna(False).astype(bool)
        & structured["release_year"].notna()
        & current_full_nonzero
    )
    structured_frame = structured.loc[structured_ok, ["movie_id", "release_year"]].copy()
    text_path = resolve_input(contract["allowed_input_artifacts"]["text_embeddings"])
    dimension = int(contract["common_support"]["e5_dimension"])
    table = pq.read_table(
        text_path,
        columns=["movie_id", "model_id", "model_revision", "embedding", "feature_eligible"],
    )
    expected_types = {
        "movie_id": pa.int32(),
        "model_id": pa.string(),
        "model_revision": pa.string(),
        "embedding": pa.list_(pa.float32(), dimension),
        "feature_eligible": pa.bool_(),
    }
    for name, expected_type in expected_types.items():
        if table.schema.field(name).type != expected_type:
            raise RuntimeError(f"E5 {name} schema drift")
        if table.column(name).null_count:
            raise RuntimeError(f"E5 {name} contains null")
    if table.column("embedding").combine_chunks().values.null_count:
        raise RuntimeError("E5 embedding contains null element")
    text_ids = table.column("movie_id").to_numpy(zero_copy_only=False).astype(np.int64)
    if len(text_ids) != len(set(text_ids.tolist())):
        raise RuntimeError("E5 movie identity duplicate")
    model_ids = table.column("model_id").to_pylist()
    revisions = table.column("model_revision").to_pylist()
    eligible = table.column("feature_eligible").to_numpy(zero_copy_only=False).astype(bool)
    flat = table.column("embedding").combine_chunks().values.to_numpy(zero_copy_only=False)
    vectors = np.asarray(flat, dtype=np.float32).reshape(len(text_ids), dimension)
    finite = np.isfinite(vectors).all(axis=1)
    norms = np.linalg.norm(vectors.astype(np.float64), axis=1)
    norm_ok = np.abs(norms - 1.0) <= 0.0001
    model_ok = np.asarray([value == contract["common_support"]["e5_model_id"] for value in model_ids], dtype=bool)
    revision_ok = np.asarray([value == contract["common_support"]["e5_revision"] for value in revisions], dtype=bool)
    text_ok = eligible & finite & norm_ok & model_ok & revision_ok
    supported = {int(movie) for movie in text_ids[text_ok].tolist()}
    frame = structured_frame.loc[structured_frame["movie_id"].isin(supported)].sort_values("movie_id", kind="stable", ignore_index=True)
    movie_ids = frame["movie_id"].to_numpy(dtype=np.int32)
    years = frame["release_year"].to_numpy(dtype=np.int16)
    projection = read_json(resolve_input(contract["allowed_input_artifacts"]["korean_movie_id_projection"]))
    korean_ids = {int(movie) for movie in projection["movie_ids"]}
    if projection.get("artifact_id") != "KOREAN_ORIGIN_MOVIELENS_MOVIE_ID_PROJECTION_V1" or len(korean_ids) != 1078:
        raise RuntimeError("Korean projection drift")
    korean = np.fromiter((int(movie) in korean_ids for movie in movie_ids), dtype=bool, count=len(movie_ids))
    return movie_ids, years, korean, {
        "structured_rows": int(len(structured)),
        "structured_eligible_rows": int(structured_ok.sum()),
        "e5_rows": int(len(text_ids)),
        "e5_eligible_exact_rows": int(text_ok.sum()),
        "common_support_items": int(len(movie_ids)),
        "korean_origin_items": int(korean.sum()),
        "recent_2020_2023_items": int(((years >= 2020) & (years <= 2023)).sum()),
        "pre_2020_items": int((years < 2020).sum()),
    }


def role_order(anonymous: str, movies: Sequence[int], salt: str, role: str) -> list[int]:
    return sorted(
        (int(movie) for movie in movies),
        key=lambda movie: (hashlib.sha256(f"{salt}|{anonymous}|{role}|{movie}".encode("utf-8")).digest(), movie),
    )


def partition_user(anonymous: str, source: Sequence[int], target: Sequence[int], spec: Mapping[str, Any]) -> dict[str, Any]:
    roles = spec["role_discriminators"]
    profile = role_order(anonymous, source, str(spec["global_profile_salt"]), str(roles["profile"]))[:14]
    if len(profile) != 14:
        raise RuntimeError("global profile short")
    control_pool = list(set(int(movie) for movie in source) - set(profile))
    panels: list[dict[str, Any]] = []
    for panel in range(4):
        selected_target = role_order(anonymous, target, str(spec["panel_target_salts"][panel]), str(roles["target"]))[:int(spec["target_n"])]
        control = role_order(anonymous, control_pool, str(spec["panel_control_salts"][panel]), str(roles["control"]))[:int(spec["control_n"])]
        if len(selected_target) != int(spec["target_n"]) or len(control) != int(spec["control_n"]):
            raise RuntimeError("panel role short")
        panels.append({"panel": panel, "target": selected_target, "control": control})
    if set(profile) & {movie for row in panels for movie in row["control"]}:
        raise RuntimeError("global profile/control overlap")
    return {"profile": profile, "panels": panels}


def _records_hash(records: Sequence[str]) -> str:
    return hashlib.sha256(canonical_json_bytes(sorted(records))).hexdigest()


def _degree(movies: Sequence[int]) -> dict[str, int]:
    degrees: dict[int, int] = {}
    for movie in movies:
        degrees[int(movie)] = degrees.get(int(movie), 0) + 1
    values = np.asarray(list(degrees.values()), dtype=np.int64)
    return {"unique_items": len(degrees), "memberships": int(values.sum(initial=0)), "maximum_degree": int(values.max(initial=0)), "top10_degree_sum": int(np.sort(values)[-10:].sum()) if len(values) else 0}


def experiment_summary(
    evidence_id: str, users: np.ndarray, source_pools: Mapping[int, list[int]], target_pools: Mapping[int, list[int]], spec: Mapping[str, Any],
) -> dict[str, Any]:
    profile_records: list[str] = []
    target_records: list[str] = []
    control_records: list[str] = []
    profile_movies: list[int] = []
    target_movies: list[int] = []
    control_movies: list[int] = []
    for raw_user in users.tolist():
        anonymous = user_key(int(raw_user))
        selected = partition_user(anonymous, source_pools[int(raw_user)], target_pools[int(raw_user)], spec)
        for movie in selected["profile"]:
            profile_records.append(f"{anonymous}|GLOBAL_PROFILE|GLOBAL|{movie}")
            profile_movies.append(int(movie))
        for row in selected["panels"]:
            panel = int(row["panel"])
            for movie in row["target"]:
                target_records.append(f"{anonymous}|PANEL_TARGET|{panel}|{movie}")
                target_movies.append(int(movie))
            for movie in row["control"]:
                control_records.append(f"{anonymous}|PANEL_CONTROL|{panel}|{movie}")
                control_movies.append(int(movie))
    profile = _degree(profile_movies)
    target = _degree(target_movies)
    control = _degree(control_movies)
    expected = {
        "profile": len(users) * 14,
        "target": len(users) * 4 * int(spec["target_n"]),
        "control": len(users) * 4 * int(spec["control_n"]),
    }
    if (profile["memberships"], target["memberships"], control["memberships"]) != (expected["profile"], expected["target"], expected["control"]):
        raise RuntimeError(f"{evidence_id} membership count drift")
    feasible = len(users) >= int(spec["minimum_users"]) and target["unique_items"] >= int(spec["minimum_unique_targets"])
    return {
        "eligible_users": int(len(users)),
        "eligible_user_key_set_sha256": _records_hash([user_key(int(value)) for value in users]),
        "global_profile": {**profile, "exposure_sha256": _records_hash(profile_records)},
        "panel_target": {**target, "exposure_sha256": _records_hash(target_records)},
        "panel_control": {**control, "exposure_sha256": _records_hash(control_records)},
        "profile_control_intersection": 0,
        "floors": {"minimum_users": int(spec["minimum_users"]), "minimum_unique_targets": int(spec["minimum_unique_targets"])},
        "status": "FEASIBLE_PRELABEL" if feasible else "INFEASIBLE_PRELABEL",
    }


def compute_result(contract: Mapping[str, Any]) -> dict[str, Any]:
    movie_ids, years, korean, support = build_common_support(contract)
    lookup = np.full(int(movie_ids.max(initial=0)) + 1, -1, dtype=np.int32)
    lookup[movie_ids] = np.arange(len(movie_ids), dtype=np.int32)
    maximum_user = int(contract["reader"]["maximum_user_id"])
    counts = {key: np.zeros(maximum_user + 1, dtype=np.int32) for key in ("korean", "non_korean", "recent", "old")}
    archive = contract["allowed_input_artifacts"]["movielens_archive"]
    archive_path = resolve_input(archive)
    allowed_rows = 0
    for raw_user, movie in movie_id_only_rows(archive_path, str(archive["member"]), maximum_user):
        allowed_rows += 1
        position = int(lookup[movie]) if 0 <= movie < len(lookup) else -1
        if position < 0:
            continue
        if bool(korean[position]):
            counts["korean"][raw_user] += 1
        else:
            counts["non_korean"][raw_user] += 1
        year = int(years[position])
        if year < 2020:
            counts["old"][raw_user] += 1
        elif year <= 2023:
            counts["recent"][raw_user] += 1
    specs = contract["experiments"]
    masks = {
        "REC-EV-025A": (counts["non_korean"] >= 24) & (counts["korean"] >= 10),
        "REC-EV-025B": (counts["old"] >= 34) & (counts["recent"] >= 20),
    }
    any_mask = masks["REC-EV-025A"] | masks["REC-EV-025B"]
    pools: dict[str, dict[str, dict[int, list[int]]]] = {}
    for evidence_id, mask in masks.items():
        users = np.flatnonzero(mask)
        pools[evidence_id] = {"source": {int(value): [] for value in users}, "target": {int(value): [] for value in users}}
    second_rows = 0
    for raw_user, movie in movie_id_only_rows(archive_path, str(archive["member"]), maximum_user):
        if not any_mask[raw_user]:
            continue
        second_rows += 1
        position = int(lookup[movie]) if 0 <= movie < len(lookup) else -1
        if position < 0:
            continue
        if masks["REC-EV-025A"][raw_user]:
            pools["REC-EV-025A"]["target" if korean[position] else "source"][int(raw_user)].append(int(movie))
        if masks["REC-EV-025B"][raw_user]:
            year = int(years[position])
            if year < 2020:
                pools["REC-EV-025B"]["source"][int(raw_user)].append(int(movie))
            elif year <= 2023:
                pools["REC-EV-025B"]["target"][int(raw_user)].append(int(movie))
    experiments = {
        evidence_id: experiment_summary(evidence_id, np.flatnonzero(mask).astype(np.int32), pools[evidence_id]["source"], pools[evidence_id]["target"], specs[evidence_id])
        for evidence_id, mask in masks.items()
    }
    return {
        "schema_version": 1, "evidence_id": "REC-EV-025AB-PREFLIGHT", "status": "PREFLIGHT_COMPLETE",
        "run_signature": run_signature(contract),
        "reader": {"allowed_rows_movie_id_parsed_first_pass": allowed_rows, "eligible_rows_movie_id_parsed_second_pass": second_rows, "rating_value_bytes_parsed": 0, "timestamp_bytes_parsed": 0, "raw_user_ids_written": False},
        "common_support": support, "experiments": experiments,
        "old_locked_item_ids_previously_parsed_in_invalid_nonartifact_preflight": True,
        "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False,
        "product_policy_updated": False, "champion": None,
    }


def expected_integrity(contract: Mapping[str, Any], result: Mapping[str, Any], progress: Mapping[str, Any], signature: str) -> dict[str, Any]:
    result_path = output_path(contract, "preflight")
    progress_path = output_path(contract, "progress")
    return {
        "schema_version": 1, "run_signature": signature,
        "artifacts": {
            "preflight": {"path": result_path.relative_to(ROOT).as_posix(), "bytes": result_path.stat().st_size, "sha256": sha256_file(result_path)},
            "progress": {"path": progress_path.relative_to(ROOT).as_posix(), "bytes": progress_path.stat().st_size, "sha256": sha256_file(progress_path)},
        },
        "metadata": {"experiments": result["experiments"], "rating_value_bytes_parsed": 0, "timestamp_bytes_parsed": 0},
    }


def preflight(contract: Mapping[str, Any], *, resume: bool) -> dict[str, Any]:
    if not resume:
        raise ResumeError("post-lock preflight requires --resume")
    create_or_verify_lock(contract, resume=True)
    result_path = output_path(contract, "preflight")
    progress_path = output_path(contract, "progress")
    integrity_path = output_path(contract, "preflight_integrity")
    states = [result_path.exists(), progress_path.exists(), integrity_path.exists()]
    if any(states) and not all(states):
        raise ResumeError("partial preflight result")
    signature = run_signature(contract)
    result = compute_result(contract)
    progress = {"schema_version": 1, "phase": "PREFLIGHT_COMPLETE", "run_signature": signature, "experiments": result["experiments"]}
    if all(states):
        if read_json(result_path) != result or read_json(progress_path) != progress:
            raise ResumeError("preflight semantic drift")
        if read_json(integrity_path) != expected_integrity(contract, result, progress, signature):
            raise ResumeError("preflight integrity drift")
        return {"status": "REUSED_EXACT_PREFLIGHT", "experiments": result["experiments"]}
    atomic_write_json(result_path, result)
    atomic_write_json(progress_path, progress)
    atomic_write_json(integrity_path, expected_integrity(contract, result, progress, signature))
    return {"status": "WROTE_PREFLIGHT", "experiments": result["experiments"]}


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(contract)
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default=str(DEFAULT))
    parser.add_argument("--phase", choices=("lock", "preflight", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    path = Path(args.contract)
    path = path if path.is_absolute() else (ROOT / path).resolve()
    contract = load_contract(path)
    if args.phase == "lock":
        print(json.dumps(create_or_verify_lock(contract, resume=args.resume), ensure_ascii=False, sort_keys=True))
        return 0
    print(json.dumps(preflight(contract, resume=args.resume), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
