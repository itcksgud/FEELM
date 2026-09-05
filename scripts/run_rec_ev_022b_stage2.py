#!/usr/bin/env python3
"""Run the independently audited REC-EV-022B Stage2 confirmation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
from scipy import sparse

try:
    from rec_ev_022a_core import (
        RATING_VALUES, deterministic_rank, encoding_weights, itemknn_pair_similarity,
        order_key, pair1_metrics, pairwise_concordance, rating_indices,
        score_judged_targets, simultaneous_max_t, structured_pair_similarity, user_key,
    )
    from run_rec_ev_022a_stage1 import (
        ANCHORS, ENCODINGS, MAX_USER_ID, _rating_chunks, _role_lookups,
        atomic_write_json, canonical_json_bytes, read_json, sha256_contract, sha256_file,
        run_signature, verify_implementation, verify_integrity, write_integrity,
    )
    from validate_rec_ev_022b_contract import validate_contract
except ModuleNotFoundError:
    from scripts.rec_ev_022a_core import (
        RATING_VALUES, deterministic_rank, encoding_weights, itemknn_pair_similarity,
        order_key, pair1_metrics, pairwise_concordance, rating_indices,
        score_judged_targets, simultaneous_max_t, structured_pair_similarity, user_key,
    )
    from scripts.run_rec_ev_022a_stage1 import (
        ANCHORS, ENCODINGS, MAX_USER_ID, _rating_chunks, _role_lookups,
        atomic_write_json, canonical_json_bytes, read_json, sha256_contract, sha256_file,
        run_signature, verify_implementation, verify_integrity, write_integrity,
    )
    from scripts.validate_rec_ev_022b_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-022b-stage2-k-information-confirmation.json"
EVEN_K = tuple(range(2, 31, 2))


class ResumeError(RuntimeError):
    pass


def resolve_input(entry: Mapping[str, Any]) -> Path:
    path = Path(str(entry["path"]))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def output_path(contract: Mapping[str, Any], name: str) -> Path:
    return ROOT / str(contract["output_root"]) / str(contract["outputs"][name])


def verify_sources(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    forbidden = {(ROOT / value).resolve() for value in contract["forbidden_input_artifacts"]}
    rows: list[dict[str, Any]] = []
    for name, entry in sorted(contract["allowed_input_artifacts"].items()):
        path = resolve_input(entry)
        if path in forbidden or not path.is_file():
            raise RuntimeError(f"forbidden or missing input: {name}")
        if path.stat().st_size != int(entry["bytes"]):
            raise RuntimeError(f"input bytes drift: {name}")
        digest = sha256_file(path)
        if digest != entry["sha256"]:
            raise RuntimeError(f"input hash drift: {name}")
        rows.append({"name": name, "path": entry["path"], "bytes": path.stat().st_size, "sha256": digest})
    return rows


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {key: contract[key] for key in (
        "implementation_artifacts", "role_and_source", "prefilter_reader", "fixed_semantics", "k_values", "metrics", "statistics",
        "k_screen", "candidate_truth_table", "claim_boundary", "resume", "invariants",
    )}


def create_or_verify_lock(contract: Mapping[str, Any], contract_path: Path, *, resume: bool) -> dict[str, Any]:
    lock_path = output_path(contract, "protocol_lock")
    manifest_path = output_path(contract, "source_manifest")
    sources = verify_sources(contract)
    implementations = verify_implementation(contract)
    expected = {
        "contract_sha256": sha256_contract(contract_path),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest(),
        "stage2_labels_joined_at_lock": False,
    }
    if lock_path.is_file():
        if not resume:
            raise ResumeError("existing 022B lock requires --resume")
        lock = read_json(lock_path)
        for key, value in expected.items():
            if lock.get(key) != value:
                raise ResumeError(f"022B lock mismatch: {key}")
        if not manifest_path.is_file() or sha256_file(manifest_path) != lock.get("source_manifest_sha256"):
            raise ResumeError("022B source manifest drift")
        return lock
    if resume:
        raise ResumeError("create the first 022B lock without --resume")
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-022B",
        "created_before_stage2_materialization": True,
        "sources": sources,
        "implementation_artifacts": implementations,
        "locked_test_opened": False,
        "stage1_user_metrics_opened": False,
        "final_reserve_opened": False,
    }
    atomic_write_json(manifest_path, manifest)
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-022B",
        "status": "PREREGISTERED_BEFORE_STAGE2_MATERIALIZATION",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        **expected,
        "source_manifest_sha256": sha256_file(manifest_path),
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }
    atomic_write_json(lock_path, lock)
    return lock


def progress_update(contract: Mapping[str, Any], phase: str, **extra: Any) -> None:
    path = output_path(contract, "progress")
    value = read_json(path) if path.is_file() else {"schema_version": 1, "evidence_id": "REC-EV-022B"}
    value.update({"phase": phase, "updated_at_utc": datetime.now(timezone.utc).isoformat(), **extra})
    atomic_write_json(path, value)


def prepare(contract: Mapping[str, Any]) -> dict[str, Any]:
    cohort_path = output_path(contract, "cohort")
    cohort_integrity = output_path(contract, "cohort_integrity")
    signature = run_signature(contract)
    if cohort_path.exists() or cohort_integrity.exists():
        integrity = verify_integrity(cohort_integrity, {"cohort": cohort_path}, signature=signature)
        users = len(pd.read_parquet(cohort_path, columns=["user_key"]))
        if integrity.get("metadata") != {"users": users}:
            raise ResumeError("Stage2 cohort row-count drift")
        return {"status": "REUSED_STAGE2_COHORT", "users": users}
    model = np.load(resolve_input(contract["allowed_input_artifacts"]["train_model"]), allow_pickle=False)
    item_ids = model["item_ids"].astype(np.int64)
    max_movie = int(item_ids.max())
    item_lookup = np.full(max_movie + 1, -1, dtype=np.int32)
    item_lookup[item_ids] = np.arange(len(item_ids), dtype=np.int32)
    old_allowed, role_buckets = _role_lookups()
    stage2_allowed_users = old_allowed & (role_buckets >= 8_000) & (role_buckets < 9_200)
    hist = np.zeros((MAX_USER_ID + 1, len(RATING_VALUES)), dtype=np.uint32)
    collected: list[pd.DataFrame] = []
    archive_entry = contract["allowed_input_artifacts"]["movielens_archive"]
    archive = resolve_input(archive_entry)
    member = str(archive_entry["member"])
    started = time.monotonic()
    for chunk_number, chunk in enumerate(_rating_chunks(
        archive, member, allowed_user_mask=stage2_allowed_users, include_timestamp=False,
    ), start=1):
        all_users = chunk["userId"].to_numpy(dtype=np.int64, copy=False)
        allowed = old_allowed[all_users] & (role_buckets[all_users] >= 8_000) & (role_buckets[all_users] < 9_200)
        if not bool(allowed.any()):
            continue
        users = all_users[allowed]
        ratings = chunk["rating"].to_numpy(dtype=np.float64, copy=False)[allowed]
        movies = chunk["movieId"].to_numpy(dtype=np.int64, copy=False)[allowed]
        indices = rating_indices(ratings)
        np.add.at(hist, (users, indices), 1)
        in_range = movies <= max_movie
        positions = np.full(len(movies), -1, dtype=np.int32)
        positions[in_range] = item_lookup[movies[in_range]]
        keep = positions >= 0
        if bool(keep.any()):
            collected.append(pd.DataFrame({
                "user_id": users[keep].astype(np.int32),
                "movie_id": movies[keep].astype(np.int32),
                "rating_idx": indices[keep].astype(np.int8),
                "item_position": positions[keep].astype(np.int32),
            }))
        progress_update(contract, "MATERIALIZE_STAGE2", chunks=chunk_number, elapsed_seconds=round(time.monotonic() - started, 3))
    frame = pd.concat(collected, ignore_index=True)
    counts = frame.groupby("user_id", sort=False).size()
    eligible = counts.index[counts >= 50].to_numpy(dtype=np.int32)
    minimum = int(contract["fixed_semantics"]["minimum_common30_users"])
    if len(eligible) < minimum:
        raise RuntimeError(f"Stage2 COMMON30 minimum failed: {len(eligible)} < {minimum}")
    salt = str(contract["fixed_semantics"]["primary_order_salt"])
    rows: list[dict[str, Any]] = []
    for raw_user, group in frame.loc[frame["user_id"].isin(eligible)].groupby("user_id", sort=True):
        anonymous = user_key(int(raw_user))
        values = list(zip(group["movie_id"].astype(int), group["rating_idx"].astype(int), group["item_position"].astype(int), strict=True))
        values.sort(key=lambda row: order_key(salt, anonymous, row[0]))
        selected = values[:50]
        full_hist = hist[int(raw_user)].astype(np.float64)
        below = np.cumsum(full_hist) - full_hist
        target_idx = np.asarray([row[1] for row in selected[30:50]], dtype=np.int8)
        q_eval = (below[target_idx] + 0.5 * full_hist[target_idx]) / full_hist.sum()
        rows.append({
            "user_key": anonymous,
            "profile_movie_ids": [row[0] for row in selected[:30]],
            "profile_rating_idx": [row[1] for row in selected[:30]],
            "profile_item_positions": [row[2] for row in selected[:30]],
            "target_movie_ids": [row[0] for row in selected[30:50]],
            "target_item_positions": [row[2] for row in selected[30:50]],
            "target_q_eval": q_eval.astype(np.float32).tolist(),
        })
    cohort = pd.DataFrame(rows).sort_values("user_key", kind="stable", ignore_index=True)
    cohort_path.parent.mkdir(parents=True, exist_ok=True)
    cohort.to_parquet(cohort_path, index=False)
    write_integrity(cohort_integrity, {"cohort": cohort_path}, signature=signature, metadata={"users": len(cohort)})
    progress_update(contract, "STAGE2_PREPARED", users=len(cohort), elapsed_seconds=round(time.monotonic() - started, 3))
    return {"status": "STAGE2_PREPARED", "users": len(cohort)}


def _part_path(root: Path, start: int, stop: int) -> Path:
    return root / f"part-{start:06d}-{stop:06d}.parquet"


def score(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    cohort_path = output_path(contract, "cohort")
    cohort_integrity = verify_integrity(
        output_path(contract, "cohort_integrity"), {"cohort": cohort_path}, signature=signature,
    )
    model = np.load(resolve_input(contract["allowed_input_artifacts"]["train_model"]), allow_pickle=False)
    z = sparse.load_npz(resolve_input(contract["allowed_input_artifacts"]["train_z"])).tocsc()
    observed = z.copy()
    observed.data = np.ones_like(observed.data, dtype=np.float32)
    structured = sparse.load_npz(resolve_input(contract["allowed_input_artifacts"]["structured_full"])).tocsr()
    cohort = pd.read_parquet(cohort_path).sort_values("user_key", kind="stable", ignore_index=True)
    if cohort_integrity.get("metadata") != {"users": len(cohort)}:
        raise ResumeError("Stage2 cohort metadata drift")
    parts_root = output_path(contract, "metric_parts")
    parts_root.mkdir(parents=True, exist_ok=True)
    b0_all = model["b0_scores"]
    g0_mid = model["g0_mid"]
    norms = model["column_norms"]
    batch_size = int(contract["resume"]["checkpoint_after_users"])
    expected_ranges = [(start, min(len(cohort), start + batch_size)) for start in range(0, len(cohort), batch_size)]
    expected_parts = {_part_path(parts_root, start, stop) for start, stop in expected_ranges}
    expected_part_integrities = {path.with_suffix(".integrity.json") for path in expected_parts}
    user_metrics_path = output_path(contract, "user_metrics")
    user_metrics_integrity_path = output_path(contract, "user_metrics_integrity")
    if user_metrics_path.exists() != user_metrics_integrity_path.exists():
        raise ResumeError("partial Stage2 combined user metrics state")
    combined_preexisting = user_metrics_path.exists()
    started = time.monotonic()
    computed_k = (0,) + EVEN_K
    for start in range(0, len(cohort), batch_size):
        stop = min(len(cohort), start + batch_size)
        destination = _part_path(parts_root, start, stop)
        part_integrity = destination.with_suffix(".integrity.json")
        if destination.exists() or part_integrity.exists():
            integrity = verify_integrity(part_integrity, {"part": destination}, signature=signature)
            expected_keys = cohort.iloc[start:stop]["user_key"].tolist()
            actual_keys = pd.read_parquet(destination, columns=["user_key"])["user_key"].drop_duplicates().tolist()
            expected_metadata = {"start": start, "stop": stop, "rows": (stop - start) * 96, "user_keys": expected_keys}
            if integrity.get("metadata") != expected_metadata or actual_keys != expected_keys:
                raise ResumeError(f"Stage2 metric part slice drift: {destination.name}")
            continue
        rows: list[dict[str, Any]] = []
        for user in cohort.iloc[start:stop].itertuples(index=False):
            profiles = np.asarray(user.profile_item_positions, dtype=np.int32)
            targets = np.asarray(user.target_item_positions, dtype=np.int32)
            ratings = RATING_VALUES[np.asarray(user.profile_rating_idx, dtype=np.int8)]
            movies = np.asarray(user.target_movie_ids, dtype=np.int64)
            q_eval = np.asarray(user.target_q_eval, dtype=np.float64)
            b0 = b0_all[targets]
            sim = {
                "STRUCTURED_CONTENT_SIM": structured_pair_similarity(structured, profiles, targets),
                "USER_DISJOINT_ITEMKNN_SIM": itemknn_pair_similarity(z, observed, norms, profiles, targets, shrinkage=50.0, minimum_support=2),
            }
            for encoding in ENCODINGS:
                for k in computed_k:
                    weights = encoding_weights(encoding, ratings[:k], g0_mid, tau=5.0)
                    for anchor in ANCHORS:
                        personal, fallback = score_judged_targets(sim[anchor][:, :k], weights)
                        order = deterministic_rank(movies, personal, b0, fallback=fallback)
                        utility, loss = pair1_metrics(q_eval[order])
                        rows.append({
                            "user_key": user.user_key, "encoding": encoding, "anchor": anchor, "k": k,
                            "pair1_mean_q": utility, "pair1_worst_q_loss": loss,
                            "pairwise_concordance": pairwise_concordance(b0 if fallback else personal, q_eval),
                            "fallback": bool(fallback),
                        })
        pd.DataFrame(rows).to_parquet(destination, index=False)
        write_integrity(
            part_integrity,
            {"part": destination},
            signature=signature,
            metadata={
                "start": start,
                "stop": stop,
                "rows": len(rows),
                "user_keys": cohort.iloc[start:stop]["user_key"].tolist(),
            },
        )
        progress_update(contract, "STAGE2_SCORING", completed_users=stop, total_users=len(cohort), elapsed_seconds=round(time.monotonic() - started, 3))
    actual_parts = set(parts_root.glob("part-*.parquet"))
    actual_part_integrities = set(parts_root.glob("part-*.integrity.json"))
    if actual_parts != expected_parts or actual_part_integrities != expected_part_integrities:
        raise ResumeError("Stage2 metric part path set drift")
    metrics = pd.concat((pd.read_parquet(path) for path in sorted(actual_parts)), ignore_index=True)
    expected = len(cohort) * len(ENCODINGS) * len(ANCHORS) * len(computed_k)
    if len(metrics) != expected or metrics.duplicated(["user_key", "encoding", "anchor", "k"]).any():
        raise RuntimeError("Stage2 metric completeness failed")
    if combined_preexisting:
        integrity = verify_integrity(
            user_metrics_integrity_path,
            {"user_metrics": user_metrics_path},
            signature=signature,
        )
        existing = pd.read_parquet(user_metrics_path, columns=["user_key"])
        if integrity.get("metadata") != {"rows": len(existing), "users": existing["user_key"].nunique()}:
            raise ResumeError("Stage2 combined user metrics metadata drift before score reuse")
        if len(existing) != expected or existing["user_key"].nunique() != len(cohort):
            raise ResumeError("Stage2 combined user metrics completeness drift")
        return {"status": "REUSED_STAGE2_METRICS", "users": len(cohort), "metric_rows": len(existing)}
    metrics.to_parquet(user_metrics_path, index=False)
    write_integrity(
        user_metrics_integrity_path,
        {"user_metrics": user_metrics_path},
        signature=signature,
        metadata={"rows": len(metrics), "users": len(cohort)},
    )
    progress_update(contract, "STAGE2_SCORED", users=len(cohort), metric_rows=len(metrics))
    return {"status": "STAGE2_SCORED", "users": len(cohort), "metric_rows": len(metrics)}


def _series(indexed: pd.DataFrame, users: list[str], encoding: str, anchor: str, k: int, metric: str) -> np.ndarray:
    return indexed.loc[(slice(None), encoding, anchor, k), metric].droplevel([1, 2, 3]).reindex(users).to_numpy(dtype=np.float64)


def build_contrasts(metrics: pd.DataFrame) -> tuple[np.ndarray, list[dict[str, Any]], list[str]]:
    users = sorted(metrics["user_key"].unique())
    indexed = metrics.set_index(["user_key", "encoding", "anchor", "k"]).sort_index()
    columns: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []

    def add(values: np.ndarray, kind: str, encoding: str, anchor: str, k: int, metric: str) -> None:
        columns.append(values)
        metadata.append({"kind": kind, "encoding": encoding, "anchor": anchor, "k": k, "metric": metric})

    for encoding in ENCODINGS:
        for anchor in ANCHORS:
            for metric in ("pair1_mean_q", "pair1_worst_q_loss"):
                k2 = _series(indexed, users, encoding, anchor, 2, metric)
                k30 = _series(indexed, users, encoding, anchor, 30, metric)
                for k in range(4, 31, 2):
                    add(_series(indexed, users, encoding, anchor, k, metric) - k2, "K_MINUS_K2", encoding, anchor, k, metric)
                for k in range(2, 29, 2):
                    add(_series(indexed, users, encoding, anchor, k, metric) - k30, "K_MINUS_K30", encoding, anchor, k, metric)
    for kind, other in (("PERCENTILE_MINUS_BINARY", "BINARY_SIGN"), ("PERCENTILE_MINUS_ORDINAL", "ORDINAL_RANK")):
        for anchor in ANCHORS:
            for metric in ("pair1_mean_q", "pair1_worst_q_loss"):
                for k in EVEN_K:
                    left = _series(indexed, users, "PERCENTILE_MAGNITUDE", anchor, k, metric)
                    right = _series(indexed, users, other, anchor, k, metric)
                    add(left - right, kind, other, anchor, k, metric)
    if len(columns) != 456:
        raise RuntimeError(f"contrast family drift: {len(columns)}")
    return np.column_stack(columns), metadata, users


def analyze(contract: Mapping[str, Any]) -> dict[str, Any]:
    user_metrics_path = output_path(contract, "user_metrics")
    integrity = verify_integrity(
        output_path(contract, "user_metrics_integrity"),
        {"user_metrics": user_metrics_path},
        signature=run_signature(contract),
    )
    metrics = pd.read_parquet(user_metrics_path)
    if integrity.get("metadata") != {"rows": len(metrics), "users": metrics["user_key"].nunique()}:
        raise ResumeError("Stage2 combined user metrics metadata drift")
    values, metadata, users = build_contrasts(metrics)
    progress_update(contract, "STAGE2_BOOTSTRAP", users=len(users), contrasts=len(metadata))
    result = simultaneous_max_t(values, repeats=10000, seed=20260924)
    lookup: dict[tuple[str, str, str, int, str], dict[str, float]] = {}
    interval_rows: list[dict[str, Any]] = []
    for index, meta in enumerate(metadata):
        row = {**meta, "mean": float(result["mean"][index]), "low": float(result["low"][index]), "high": float(result["high"][index]), "half_width": float(result["half_width"][index])}
        interval_rows.append(row)
        lookup[(meta["kind"], meta["encoding"], meta["anchor"], int(meta["k"]), meta["metric"])] = row

    def onset_pass(encoding: str, anchor: str, k: int) -> bool:
        utility = lookup[("K_MINUS_K2", encoding, anchor, k, "pair1_mean_q")]
        loss = lookup[("K_MINUS_K2", encoding, anchor, k, "pair1_worst_q_loss")]
        return utility["low"] >= 0.005 and loss["high"] <= 0.010

    def plateau_pass(encoding: str, anchor: str, k: int) -> bool:
        utility = lookup[("K_MINUS_K30", encoding, anchor, k, "pair1_mean_q")]
        loss = lookup[("K_MINUS_K30", encoding, anchor, k, "pair1_worst_q_loss")]
        return utility["low"] >= -0.005 and utility["high"] <= 0.005 and loss["low"] >= -0.010 and loss["high"] <= 0.010

    screens: dict[str, dict[str, Any]] = {}
    for encoding in ENCODINGS:
        onset = next((k for k in range(4, 27, 2) if all(onset_pass(encoding, anchor, follow) for anchor in ANCHORS for follow in (k, k + 2, k + 4))), None)
        plateau = None
        if onset is not None:
            plateau = next((k for k in range(2, 29, 2) if all(plateau_pass(encoding, anchor, follow) for anchor in ANCHORS for follow in range(k, 29, 2))), None)
        screens[encoding] = {"k_onset": onset, "k_plateau": plateau, "eligible": onset is not None}
    g = [encoding for encoding in ENCODINGS if screens[encoding]["eligible"]]
    k_cand = sorted({value for encoding in g for value in (screens[encoding]["k_onset"], screens[encoding]["k_plateau"]) if value is not None})
    candidates = [{"encoding": encoding, "k": k} for encoding in g for k in k_cand]

    def encoding_pass(kind: str, other: str, k: int, utility_floor: float) -> bool:
        for anchor in ANCHORS:
            utility = lookup[(kind, other, anchor, k, "pair1_mean_q")]
            loss = lookup[(kind, other, anchor, k, "pair1_worst_q_loss")]
            if utility["low"] < utility_floor or loss["high"] > 0.010:
                return False
        return True

    if "PERCENTILE_MAGNITUDE" in g:
        filtered: list[dict[str, Any]] = []
        for cell in candidates:
            encoding = cell["encoding"]
            k = int(cell["k"])
            remove = False
            if encoding == "BINARY_SIGN" and "BINARY_SIGN" in g:
                remove = encoding_pass("PERCENTILE_MINUS_BINARY", "BINARY_SIGN", k, 0.005)
            elif encoding == "ORDINAL_RANK" and "ORDINAL_RANK" in g:
                remove = encoding_pass("PERCENTILE_MINUS_ORDINAL", "ORDINAL_RANK", k, -0.005)
            if not remove:
                filtered.append(cell)
        candidates = filtered
    candidates.sort(key=lambda row: (int(row["k"]), str(row["encoding"])))
    status = "NO_ELIGIBLE_POLICY" if not candidates else ("PASS_EXACT_CELLS_TO_MODEL_DEVELOPMENT" if len(candidates) <= 6 else "OVERFLOW_NO_SELECTION")
    final_candidates = candidates if status == "PASS_EXACT_CELLS_TO_MODEL_DEVELOPMENT" else []
    selection = {
        "schema_version": 1, "evidence_id": "REC-EV-022B", "status": status,
        "users": len(users), "critical_value": float(result["critical"]), "screens": screens,
        "g": g, "k_cand": k_cand, "s0_count": len(g) * len(k_cand),
        "truth_table_survivors": candidates, "model_development_candidates": final_candidates,
        "locked_test_opened": False, "final_reserve_opened": False,
        "product_policy_updated": False, "champion": None,
    }
    output = {
        "schema_version": 1, "evidence_id": "REC-EV-022B", "status": status,
        "selection": selection, "simultaneous_intervals": interval_rows,
        "metric_means": metrics.groupby(["encoding", "anchor", "k"], observed=True)[["pair1_mean_q", "pair1_worst_q_loss", "pairwise_concordance"]].mean().reset_index().to_dict("records"),
        "fallback_rates": metrics.groupby(["encoding", "anchor", "k"], observed=True)["fallback"].mean().reset_index().to_dict("records"),
        "locked_test_opened": False, "stage1_user_metrics_opened": False,
        "final_reserve_opened": False, "model_retrained": False,
        "product_policy_updated": False, "champion": None,
    }
    atomic_write_json(output_path(contract, "selection"), selection)
    atomic_write_json(output_path(contract, "result"), output)
    progress_update(contract, "COMPLETE", status=status, candidates=len(final_candidates))
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
    if np.__version__ != "1.26.4":
        raise RuntimeError("NumPy version drift")
    if args.phase == "lock":
        value = create_or_verify_lock(contract, contract_path, resume=args.resume)
    else:
        if not args.resume:
            raise ResumeError("022B real phases require --resume")
        create_or_verify_lock(contract, contract_path, resume=True)
        if args.phase in {"prepare", "run"}:
            value = prepare(contract)
        if args.phase in {"score", "run"}:
            if not output_path(contract, "cohort").is_file():
                raise ResumeError("022B score requires prepared cohort")
            value = score(contract)
        if args.phase in {"analyze", "run"}:
            if not output_path(contract, "user_metrics").is_file():
                raise ResumeError("022B analyze requires completed metrics")
            value = analyze(contract)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
