#!/usr/bin/env python3
"""Lock, prepare, score, and analyze the REC-EV-023A adaptive content screen."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import sparse

try:
    from rec_ev_022a_core import (
        RATING_VALUES, deterministic_rank, encoding_weights, itemknn_pair_similarity,
        pair1_metrics, pairwise_concordance, score_judged_targets, simultaneous_max_t,
        structured_pair_similarity,
    )
    from run_rec_ev_022a_stage1 import (
        atomic_write_json, canonical_json_bytes, read_json, sha256_contract, sha256_file,
        verify_implementation, verify_integrity, verify_sources, write_integrity,
    )
    from validate_rec_ev_023a_contract import validate_contract
except ModuleNotFoundError:
    from scripts.rec_ev_022a_core import (
        RATING_VALUES, deterministic_rank, encoding_weights, itemknn_pair_similarity,
        pair1_metrics, pairwise_concordance, score_judged_targets, simultaneous_max_t,
        structured_pair_similarity,
    )
    from scripts.run_rec_ev_022a_stage1 import (
        atomic_write_json, canonical_json_bytes, read_json, sha256_contract, sha256_file,
        verify_implementation, verify_integrity, verify_sources, write_integrity,
    )
    from scripts.validate_rec_ev_023a_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023a-content-vector-development-screen.json"
HEADS = (
    "B0", "ITEMKNN", "STRUCTURED", "E5",
    "AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY", "AVAILABLE_HEAD_HYBRID_POLICY",
)
CHALLENGERS = HEADS[1:]
PRIMARY_METRICS = ("pair1_mean_q", "pair1_worst_q_loss")


class ResumeError(RuntimeError):
    pass


def resolve_input(entry: Mapping[str, Any]) -> Path:
    path = Path(str(entry["path"]))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def output_path(contract: Mapping[str, Any], name: str) -> Path:
    return ROOT / str(contract["output_root"]) / str(contract["outputs"][name])


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "purpose", "authorization", "adaptive_reuse", "implementation_artifacts", "cells", "heads",
        "fixed_semantics", "e5_primary_invariants", "rrf", "metrics", "statistics", "decision",
        "claim_boundary", "resume", "invariants",
    )
    return {key: contract[key] for key in keys}


def verify_upstream_semantics(contract: Mapping[str, Any]) -> None:
    selection = read_json(resolve_input(contract["allowed_input_artifacts"]["rec_ev_022b_selection"]))
    if selection.get("model_development_candidates") != contract["cells"]:
        raise RuntimeError("REC-EV-022B selected-cell drift")
    registry = read_json(resolve_input(contract["allowed_input_artifacts"]["rec_ev_019c_trial_registry"]))
    rrf_trials = registry.get("trials", {}).get("B9_RRF", [])
    if not any(
        trial.get("trial_id") == "B9_RRF-T003"
        and trial.get("parameters") == {"c": 10, "head_set": "ALL_NONBASE"}
        for trial in rrf_trials
    ):
        raise RuntimeError("REC-EV-019C RRF provenance drift")
    old_selection = read_json(resolve_input(contract["allowed_input_artifacts"]["rec_ev_019c_validation_selection"]))
    per_model = old_selection.get("per_model_per_k", {}).get("B9_RRF", {})
    if any(per_model.get(str(k), {}).get("trial_id") != "B9_RRF-T003" for k in (5, 10)):
        raise RuntimeError("REC-EV-019C B9 internal selection drift")
    if any(old_selection.get("single_best_per_k", {}).get(str(k), {}).get("model_id") == "B9_RRF" for k in (5, 10)):
        raise RuntimeError("RRF provenance must not be represented as the overall winner")


def create_or_verify_lock(contract: Mapping[str, Any], contract_path: Path, *, resume: bool) -> dict[str, Any]:
    lock_path = output_path(contract, "protocol_lock")
    manifest_path = output_path(contract, "source_manifest")
    sources = verify_sources(contract)
    implementations = verify_implementation(contract)
    verify_upstream_semantics(contract)
    expected = {
        "contract_sha256": sha256_contract(contract_path),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest(),
        "stage2_cohort_columns_parsed_by_lock_command": False,
    }
    if lock_path.is_file():
        if not resume:
            raise ResumeError("existing REC-EV-023A lock requires --resume")
        lock = read_json(lock_path)
        for key, value in expected.items():
            if lock.get(key) != value:
                raise ResumeError(f"REC-EV-023A lock mismatch: {key}")
        if not manifest_path.is_file() or sha256_file(manifest_path) != lock.get("source_manifest_sha256"):
            raise ResumeError("REC-EV-023A source manifest drift")
        return lock
    if resume:
        raise ResumeError("create the first REC-EV-023A lock without --resume")
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023A",
        "stage2_cohort_previously_seen": True,
        "stage2_cohort_columns_parsed_by_lock_command": False,
        "sources": sources,
        "implementation_artifacts": implementations,
        "adaptive_stage2_reuse_declared": True,
        "locked_test_opened": False,
        "final_reserve_opened": False,
    }
    atomic_write_json(manifest_path, manifest)
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023A",
        "status": "PREREGISTERED_ADAPTIVE_DEVELOPMENT_SCREEN",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        **expected,
        "source_manifest_sha256": sha256_file(manifest_path),
        "adaptive_stage2_reuse": True,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "model_fitted_or_retuned": False,
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
    value = read_json(path) if path.is_file() else {"schema_version": 1, "evidence_id": "REC-EV-023A"}
    value.update({"phase": phase, "updated_at_utc": datetime.now(timezone.utc).isoformat(), **extra})
    atomic_write_json(path, value)


def atomic_save_npy(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _prepared_artifacts(contract: Mapping[str, Any]) -> dict[str, Path]:
    return {
        "e5_aligned": output_path(contract, "e5_aligned"),
        "e5_available": output_path(contract, "e5_available"),
    }


def prepare(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    artifacts = _prepared_artifacts(contract)
    integrity_path = output_path(contract, "prepared_integrity")
    if integrity_path.exists() or any(path.exists() for path in artifacts.values()):
        integrity = verify_integrity(integrity_path, artifacts, signature=signature)
        return {"status": "REUSED_PREPARED_E5", **integrity["metadata"]}

    started = time.monotonic()
    progress_update(contract, "PREPARE_E5")
    model = np.load(resolve_input(contract["allowed_input_artifacts"]["train_model"]), allow_pickle=False)
    item_ids = model["item_ids"].astype(np.int64)
    table = pq.read_table(
        resolve_input(contract["allowed_input_artifacts"]["text_embeddings"]),
        columns=["movie_id", "embedding", "feature_eligible"],
    )
    source_ids = table.column("movie_id").combine_chunks().to_numpy(zero_copy_only=False).astype(np.int64)
    if len(np.unique(source_ids)) != len(source_ids):
        raise RuntimeError("duplicate E5 movie id")
    embedding_values = table.column("embedding").combine_chunks().values.to_numpy(zero_copy_only=False)
    source_vectors = np.asarray(embedding_values, dtype=np.float32).reshape(len(source_ids), 384)
    source_available = table.column("feature_eligible").combine_chunks().to_numpy(zero_copy_only=False).astype(bool)
    source_lookup = {int(movie): index for index, movie in enumerate(source_ids.tolist())}
    aligned = np.zeros((len(item_ids), 384), dtype=np.float32)
    available = np.zeros(len(item_ids), dtype=bool)
    for target_index, movie in enumerate(item_ids.tolist()):
        source_index = source_lookup.get(int(movie))
        if source_index is not None and source_available[source_index]:
            aligned[target_index] = source_vectors[source_index]
            available[target_index] = True
    norms = np.linalg.norm(aligned[available].astype(np.float64), axis=1)
    if not len(norms) or not np.isfinite(norms).all() or float(np.max(np.abs(norms - 1.0))) > 0.0001:
        raise RuntimeError("eligible E5 norm drift")

    cohort = pd.read_parquet(
        resolve_input(contract["allowed_input_artifacts"]["stage2_cohort"]),
        columns=["profile_rating_idx", "profile_item_positions", "target_item_positions"],
    )
    target_positions = np.asarray(cohort["target_item_positions"].tolist(), dtype=np.int32)
    target_total = int(target_positions.size)
    target_eligible = int(available[target_positions].sum())
    required = contract["e5_primary_invariants"]
    if (target_eligible, target_total) != (int(required["target_eligible_exposures"]), int(required["target_total_exposures"])):
        raise RuntimeError("SOURCE_DRIFT_BLOCKED: E5 target coverage")
    g0_mid = model["g0_mid"]
    profile_eligible_by_k: dict[str, int] = {}
    profile_total_by_k: dict[str, int] = {}
    zero_by_cell: dict[str, int] = {}
    for cell in contract["cells"]:
        encoding = str(cell["encoding"])
        k = int(cell["k"])
        if str(k) not in profile_total_by_k:
            positions = np.asarray([row[:k] for row in cohort["profile_item_positions"]], dtype=np.int32)
            profile_total_by_k[str(k)] = int(positions.size)
            profile_eligible_by_k[str(k)] = int(available[positions].sum())
        zero_users = 0
        for rating_indices in cohort["profile_rating_idx"]:
            ratings = RATING_VALUES[np.asarray(rating_indices[:k], dtype=np.int8)]
            weights = encoding_weights(encoding, ratings, g0_mid, tau=5.0)
            zero_users += int(float(np.abs(weights).sum()) == 0.0)
        zero_by_cell[f"{encoding}|{k}"] = zero_users
    if profile_eligible_by_k != {key: int(value) for key, value in required["profile_eligible_exposures_by_k"].items()}:
        raise RuntimeError("SOURCE_DRIFT_BLOCKED: E5 profile coverage")
    if profile_total_by_k != {key: int(value) for key, value in required["profile_total_exposures_by_k"].items()}:
        raise RuntimeError("SOURCE_DRIFT_BLOCKED: E5 profile denominator")
    if any(value != int(required["usable_weight_zero_users_each_cell"]) for value in zero_by_cell.values()):
        raise RuntimeError("SOURCE_DRIFT_BLOCKED: zero usable profile weights")

    atomic_save_npy(artifacts["e5_aligned"], aligned)
    atomic_save_npy(artifacts["e5_available"], available)
    metadata = {
        "items": len(item_ids),
        "users": len(cohort),
        "target_eligible": target_eligible,
        "target_total": target_total,
        "profile_eligible_by_k": profile_eligible_by_k,
        "profile_total_by_k": profile_total_by_k,
        "zero_users_by_cell": zero_by_cell,
    }
    write_integrity(integrity_path, artifacts, signature=signature, metadata=metadata)
    progress_update(contract, "PREPARED", elapsed_seconds=round(time.monotonic() - started, 3), **metadata)
    return {"status": "PREPARED_E5", **metadata}


def _active_personal_scores(similarity: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, bool]:
    scores, denominator_fallback = score_judged_targets(similarity, weights)
    active = (
        not denominator_fallback
        and bool(np.isfinite(scores).all())
        and int(np.unique(scores).size) >= 2
    )
    return scores, active


def _one_based_ranks(order: np.ndarray) -> np.ndarray:
    permutation = np.asarray(order, dtype=np.int64)
    if sorted(permutation.tolist()) != list(range(len(permutation))):
        raise RuntimeError("rank order is not an exact permutation")
    ranks = np.empty(len(permutation), dtype=np.int32)
    ranks[permutation] = np.arange(1, len(permutation) + 1, dtype=np.int32)
    return ranks


def _available_head_rrf(
    movie_ids: np.ndarray,
    b0: np.ndarray,
    components: Sequence[tuple[str, np.ndarray, bool]],
    *,
    c: int,
) -> tuple[np.ndarray, bool, str]:
    total = np.zeros(len(movie_ids), dtype=np.float64)
    active_names: list[str] = []
    for name, scores, active in components:
        if not active:
            continue
        order = deterministic_rank(movie_ids, scores, b0, fallback=False)
        total += 1.0 / (float(c) + _one_based_ranks(order).astype(np.float64))
        active_names.append(name)
    active = bool(active_names) and int(np.unique(total).size) >= 2
    return total, active, "|".join(active_names)


def _part_path(root: Path, start: int, stop: int) -> Path:
    return root / f"part-{start:06d}-{stop:06d}.parquet"


def score(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    prepared_artifacts = _prepared_artifacts(contract)
    prepared = verify_integrity(
        output_path(contract, "prepared_integrity"), prepared_artifacts, signature=signature,
    )
    model = np.load(resolve_input(contract["allowed_input_artifacts"]["train_model"]), allow_pickle=False)
    z = sparse.load_npz(resolve_input(contract["allowed_input_artifacts"]["train_z"])).tocsc()
    observed = z.copy()
    observed.data = np.ones_like(observed.data, dtype=np.float32)
    structured = sparse.load_npz(resolve_input(contract["allowed_input_artifacts"]["structured_full"])).tocsr()
    e5 = np.load(prepared_artifacts["e5_aligned"], allow_pickle=False)
    e5_available = np.load(prepared_artifacts["e5_available"], allow_pickle=False)
    cohort = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["stage2_cohort"])).sort_values(
        "user_key", kind="stable", ignore_index=True,
    )
    if prepared["metadata"].get("users") != len(cohort) or not bool(e5_available.any()):
        raise ResumeError("prepared E5 metadata drift before score")
    b0_all = model["b0_scores"]
    g0_mid = model["g0_mid"]
    norms = model["column_norms"]
    parts_root = output_path(contract, "metric_parts")
    parts_root.mkdir(parents=True, exist_ok=True)
    batch_size = int(contract["resume"]["checkpoint_after_users"])
    ranges = [(start, min(len(cohort), start + batch_size)) for start in range(0, len(cohort), batch_size)]
    expected_parts = {_part_path(parts_root, start, stop) for start, stop in ranges}
    expected_integrities = {path.with_suffix(".integrity.json") for path in expected_parts}
    combined_path = output_path(contract, "user_metrics")
    combined_integrity = output_path(contract, "user_metrics_integrity")
    if combined_path.exists() != combined_integrity.exists():
        raise ResumeError("partial REC-EV-023A combined metrics state")
    combined_preexisting = combined_path.exists()
    started = time.monotonic()
    expected_rows_per_user = len(contract["cells"]) * len(HEADS)
    for start, stop in ranges:
        destination = _part_path(parts_root, start, stop)
        integrity_path = destination.with_suffix(".integrity.json")
        expected_keys = cohort.iloc[start:stop]["user_key"].tolist()
        expected_metadata = {
            "start": start, "stop": stop, "rows": (stop - start) * expected_rows_per_user, "user_keys": expected_keys,
        }
        if destination.exists() or integrity_path.exists():
            integrity = verify_integrity(integrity_path, {"part": destination}, signature=signature)
            actual = pd.read_parquet(destination, columns=["user_key"])
            if integrity.get("metadata") != expected_metadata or actual["user_key"].drop_duplicates().tolist() != expected_keys:
                raise ResumeError(f"REC-EV-023A part slice drift: {destination.name}")
            continue
        rows: list[dict[str, Any]] = []
        for user in cohort.iloc[start:stop].itertuples(index=False):
            profiles = np.asarray(user.profile_item_positions, dtype=np.int32)
            targets = np.asarray(user.target_item_positions, dtype=np.int32)
            target_movies = np.asarray(user.target_movie_ids, dtype=np.int64)
            q_eval = np.asarray(user.target_q_eval, dtype=np.float64)
            ratings = RATING_VALUES[np.asarray(user.profile_rating_idx, dtype=np.int8)]
            b0 = b0_all[targets]
            maximum_k = 14
            similarities = {
                "ITEMKNN": itemknn_pair_similarity(
                    z, observed, norms, profiles[:maximum_k], targets, shrinkage=50.0, minimum_support=2,
                ),
                "STRUCTURED": structured_pair_similarity(structured, profiles[:maximum_k], targets),
                "E5": (e5[targets].astype(np.float64) @ e5[profiles[:maximum_k]].astype(np.float64).T),
            }
            for cell in contract["cells"]:
                encoding = str(cell["encoding"])
                k = int(cell["k"])
                weights = encoding_weights(encoding, ratings[:k], g0_mid, tau=5.0)
                standalone: dict[str, tuple[np.ndarray, bool]] = {
                    name: _active_personal_scores(similarity[:, :k], weights)
                    for name, similarity in similarities.items()
                }
                content_score, content_active, content_composition = _available_head_rrf(
                    target_movies, b0,
                    [(name, *standalone[name]) for name in ("STRUCTURED", "E5")],
                    c=int(contract["rrf"]["c"]),
                )
                hybrid_score, hybrid_active, hybrid_composition = _available_head_rrf(
                    target_movies, b0,
                    [(name, *standalone[name]) for name in ("ITEMKNN", "STRUCTURED", "E5")],
                    c=int(contract["rrf"]["c"]),
                )
                head_values: dict[str, tuple[np.ndarray, bool, str]] = {
                    "B0": (np.zeros_like(b0), False, "B0"),
                    "ITEMKNN": (*standalone["ITEMKNN"], "ITEMKNN" if standalone["ITEMKNN"][1] else ""),
                    "STRUCTURED": (*standalone["STRUCTURED"], "STRUCTURED" if standalone["STRUCTURED"][1] else ""),
                    "E5": (*standalone["E5"], "E5" if standalone["E5"][1] else ""),
                    "AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY": (content_score, content_active, content_composition),
                    "AVAILABLE_HEAD_HYBRID_POLICY": (hybrid_score, hybrid_active, hybrid_composition),
                }
                for head in HEADS:
                    personal, active, composition = head_values[head]
                    use_b0 = head == "B0" or not active
                    order = deterministic_rank(target_movies, personal, b0, fallback=use_b0)
                    utility, loss = pair1_metrics(q_eval[order])
                    rows.append({
                        "user_key": user.user_key,
                        "encoding": encoding,
                        "k": k,
                        "head": head,
                        "pair1_mean_q": utility,
                        "pair1_worst_q_loss": loss,
                        "pairwise_concordance": pairwise_concordance(b0 if use_b0 else personal, q_eval),
                        "head_active": bool(active),
                        "used_b0": bool(use_b0),
                        "active_composition": composition,
                    })
        frame = pd.DataFrame(rows)
        frame.to_parquet(destination, index=False)
        write_integrity(integrity_path, {"part": destination}, signature=signature, metadata=expected_metadata)
        progress_update(
            contract, "SCORING", completed_users=stop, total_users=len(cohort),
            elapsed_seconds=round(time.monotonic() - started, 3),
        )

    actual_parts = set(parts_root.glob("part-*.parquet"))
    actual_integrities = set(parts_root.glob("part-*.integrity.json"))
    if actual_parts != expected_parts or actual_integrities != expected_integrities:
        raise ResumeError("REC-EV-023A metric part path set drift")
    metrics = pd.concat((pd.read_parquet(path) for path in sorted(actual_parts)), ignore_index=True)
    expected_rows = len(cohort) * expected_rows_per_user
    if len(metrics) != expected_rows or metrics.duplicated(["user_key", "encoding", "k", "head"]).any():
        raise RuntimeError("REC-EV-023A metric completeness drift")
    if combined_preexisting:
        integrity = verify_integrity(combined_integrity, {"user_metrics": combined_path}, signature=signature)
        existing = pd.read_parquet(combined_path, columns=["user_key"])
        if integrity.get("metadata") != {"rows": len(existing), "users": existing["user_key"].nunique()}:
            raise ResumeError("REC-EV-023A combined metadata drift")
        if len(existing) != expected_rows or existing["user_key"].nunique() != len(cohort):
            raise ResumeError("REC-EV-023A combined completeness drift")
        return {"status": "REUSED_CONTENT_SCREEN_METRICS", "users": len(cohort), "metric_rows": len(existing)}
    metrics.to_parquet(combined_path, index=False)
    write_integrity(
        combined_integrity, {"user_metrics": combined_path}, signature=signature,
        metadata={"rows": len(metrics), "users": len(cohort)},
    )
    progress_update(contract, "SCORED", users=len(cohort), metric_rows=len(metrics))
    return {"status": "SCORED_CONTENT_SCREEN", "users": len(cohort), "metric_rows": len(metrics)}


def _metric_series(
    indexed: pd.DataFrame, users: list[str], encoding: str, k: int, head: str, metric: str,
) -> np.ndarray:
    return indexed.loc[(slice(None), encoding, k, head), metric].droplevel([1, 2, 3]).reindex(users).to_numpy(dtype=np.float64)


def build_contrasts(metrics: pd.DataFrame, cells: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, list[dict[str, Any]], list[str]]:
    users = sorted(metrics["user_key"].unique())
    indexed = metrics.set_index(["user_key", "encoding", "k", "head"]).sort_index()
    columns: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []

    def add(encoding: str, k: int, left: str, right: str, metric: str) -> None:
        columns.append(
            _metric_series(indexed, users, encoding, k, left, metric)
            - _metric_series(indexed, users, encoding, k, right, metric)
        )
        metadata.append({"encoding": encoding, "k": k, "left": left, "right": right, "metric": metric})

    for cell in cells:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        for challenger in CHALLENGERS:
            for metric in PRIMARY_METRICS:
                add(encoding, k, challenger, "B0", metric)
        for left, right in (
            ("E5", "STRUCTURED"),
            ("AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY", "E5"),
            ("AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY", "STRUCTURED"),
            ("AVAILABLE_HEAD_HYBRID_POLICY", "ITEMKNN"),
        ):
            for metric in PRIMARY_METRICS:
                add(encoding, k, left, right, metric)
    if len(columns) != 108:
        raise RuntimeError(f"REC-EV-023A contrast family drift: {len(columns)}")
    return np.column_stack(columns), metadata, users


def analyze(contract: Mapping[str, Any]) -> dict[str, Any]:
    metrics_path = output_path(contract, "user_metrics")
    integrity = verify_integrity(
        output_path(contract, "user_metrics_integrity"), {"user_metrics": metrics_path},
        signature=run_signature(contract),
    )
    metrics = pd.read_parquet(metrics_path)
    if integrity.get("metadata") != {"rows": len(metrics), "users": metrics["user_key"].nunique()}:
        raise ResumeError("REC-EV-023A combined metadata drift before analysis")
    values, metadata, users = build_contrasts(metrics, contract["cells"])
    progress_update(contract, "BOOTSTRAP", users=len(users), contrasts=len(metadata))
    intervals = simultaneous_max_t(
        values,
        repeats=int(contract["statistics"]["bootstrap_repeats"]),
        seed=int(contract["statistics"]["seed"]),
    )
    interval_rows: list[dict[str, Any]] = []
    lookup: dict[tuple[str, int, str, str, str], dict[str, float]] = {}
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
        utility = lookup[(encoding, k, left, right, "pair1_mean_q")]
        loss = lookup[(encoding, k, left, right, "pair1_worst_q_loss")]
        return utility["low"] >= float(contract["decision"]["utility_margin"]) and loss["high"] <= float(contract["decision"]["worst_loss_margin"])

    propositions: list[dict[str, Any]] = []
    forward_set: list[dict[str, Any]] = []
    for cell in contract["cells"]:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        q_item = qualifies(encoding, k, "ITEMKNN", "B0")
        q_structured = qualifies(encoding, k, "STRUCTURED", "B0")
        q_e5_b0 = qualifies(encoding, k, "E5", "B0")
        q_e5_structured = qualifies(encoding, k, "E5", "STRUCTURED")
        q_content_b0 = qualifies(encoding, k, "AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY", "B0")
        q_content_e5 = qualifies(encoding, k, "AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY", "E5")
        q_content_structured = qualifies(encoding, k, "AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY", "STRUCTURED")
        q_hybrid_b0 = qualifies(encoding, k, "AVAILABLE_HEAD_HYBRID_POLICY", "B0")
        q_hybrid_item = qualifies(encoding, k, "AVAILABLE_HEAD_HYBRID_POLICY", "ITEMKNN")
        pure_content = q_structured or q_e5_b0 or q_content_b0
        e5_incremental = q_e5_b0 and q_e5_structured
        content_policy = q_content_b0 and q_content_e5 and q_content_structured
        hybrid_policy = q_hybrid_b0 and q_hybrid_item
        propositions.append({
            "encoding": encoding,
            "k": k,
            "pure_content_signal": pure_content,
            "e5_incremental_signal": e5_incremental,
            "available_head_content_ensemble_policy_signal": content_policy,
            "available_head_hybrid_policy_increment": hybrid_policy,
        })
        for head, passed in (
            ("ITEMKNN", q_item),
            ("STRUCTURED", q_structured),
            ("E5", e5_incremental),
            ("AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY", content_policy),
            ("AVAILABLE_HEAD_HYBRID_POLICY", hybrid_policy),
        ):
            if passed:
                forward_set.append({"encoding": encoding, "k": k, "head": head})
    content_forward_set = [row for row in forward_set if row["head"] != "ITEMKNN"]
    status = "DEVELOPMENT_SCREEN_SIGNAL" if content_forward_set else "DEVELOPMENT_SCREEN_NO_SIGNAL"
    selection = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023A",
        "status": status,
        "interval_label": contract["statistics"]["interval_label"],
        "adaptive_stage2_reuse": True,
        "users": len(users),
        "critical_value": float(intervals["critical"]),
        "propositions": propositions,
        "forward_set": forward_set,
        "content_forward_set": content_forward_set,
        "champion": None,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
    }
    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023A",
        "status": status,
        "claim_boundary": contract["claim_boundary"],
        "selection": selection,
        "simultaneous_intervals": interval_rows,
        "metric_means": metrics.groupby(["encoding", "k", "head"], observed=True)[
            ["pair1_mean_q", "pair1_worst_q_loss", "pairwise_concordance"]
        ].mean().reset_index().to_dict("records"),
        "active_rates": metrics.groupby(["encoding", "k", "head"], observed=True)["head_active"].mean().reset_index().to_dict("records"),
        "composition_counts": metrics.groupby(
            ["encoding", "k", "head", "active_composition"], observed=True, dropna=False,
        ).size().reset_index(name="users").to_dict("records"),
        "adaptive_stage2_reuse": True,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "model_fitted_or_retuned": False,
        "champion": None,
        "product_policy_updated": False,
    }
    atomic_write_json(output_path(contract, "selection"), selection)
    atomic_write_json(output_path(contract, "result"), result)
    progress_update(contract, "COMPLETE", status=status, forward_cells=len(forward_set), content_forward_cells=len(content_forward_set))
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
    if contract_path != DEFAULT_CONTRACT.resolve():
        raise RuntimeError("REC-EV-023A accepts only the committed default contract")
    contract = read_json(contract_path)
    validate_contract(contract)
    if np.__version__ != contract["statistics"]["numpy_version"]:
        raise RuntimeError(f"NumPy version drift: {np.__version__}")
    if args.phase == "lock":
        value = create_or_verify_lock(contract, contract_path, resume=args.resume)
    else:
        if not args.resume:
            raise ResumeError("REC-EV-023A real phases require --resume")
        create_or_verify_lock(contract, contract_path, resume=True)
        if args.phase in {"prepare", "run"}:
            value = prepare(contract)
        if args.phase in {"score", "run"}:
            if not output_path(contract, "prepared_integrity").is_file():
                raise ResumeError("REC-EV-023A score requires prepared E5")
            value = score(contract)
        if args.phase in {"analyze", "run"}:
            if not output_path(contract, "user_metrics_integrity").is_file():
                raise ResumeError("REC-EV-023A analyze requires completed metrics")
            value = analyze(contract)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
