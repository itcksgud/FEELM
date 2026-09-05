#!/usr/bin/env python3
"""Run REC-EV-023C crossed user/item membership sensitivity."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import sparse

try:
    from rec_ev_022a_core import canonical_decimal
    from run_rec_ev_023b_masked_cold_screen import (
        PRIMARY_METRICS,
        atomic_save_npz,
        atomic_write_json,
        build_contrasts,
        canonical_json_bytes,
        locked_spec as prior_locked_spec,
        read_json,
        sha256_contract,
        sha256_file,
        verify_integrity,
        verify_implementation,
        verify_sources,
        write_integrity,
    )
    from validate_rec_ev_023c_contract import validate_contract
except ModuleNotFoundError:
    from scripts.rec_ev_022a_core import canonical_decimal
    from scripts.run_rec_ev_023b_masked_cold_screen import (
        PRIMARY_METRICS,
        atomic_save_npz,
        atomic_write_json,
        build_contrasts,
        canonical_json_bytes,
        locked_spec as prior_locked_spec,
        read_json,
        sha256_contract,
        sha256_file,
        verify_integrity,
        verify_implementation,
        verify_sources,
        write_integrity,
    )
    from scripts.validate_rec_ev_023c_contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023c-crossed-membership-sensitivity.json"
REGIMES = ("USER_ONLY", "ITEM_ONLY", "TWO_WAY")


class ResumeError(RuntimeError):
    pass


def resolve_input(entry: Mapping[str, Any]) -> Path:
    path = Path(str(entry["path"]))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def output_path(contract: Mapping[str, Any], name: str) -> Path:
    return ROOT / str(contract["output_root"]) / str(contract["outputs"][name])


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "purpose", "authorization", "adaptive_boundary", "implementation_artifacts", "membership",
        "contrast_family", "bootstrap", "intervals", "decision", "resume", "invariants",
    )
    return {key: contract[key] for key in keys}


def _expected_manifest(sources: list[dict[str, Any]], implementations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_id": "REC-EV-023C",
        "sources": sources,
        "implementation_artifacts": implementations,
        "rec_ev_023b_result_seen_before_design": True,
        "predictions_rankings_and_q_not_opened": True,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
    }


def create_or_verify_lock(contract: Mapping[str, Any], contract_path: Path, *, resume: bool) -> dict[str, Any]:
    lock_path = output_path(contract, "protocol_lock")
    manifest_path = output_path(contract, "source_manifest")
    sources = verify_sources(contract)
    implementations = verify_implementation(contract)
    hashes = {
        "contract_sha256": sha256_contract(contract_path),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest(),
    }
    manifest = _expected_manifest(sources, implementations)
    if lock_path.is_file():
        if not resume:
            raise ResumeError("existing REC-EV-023C lock requires --resume")
        if not manifest_path.is_file() or read_json(manifest_path) != manifest:
            raise ResumeError("REC-EV-023C source manifest drift")
        expected = {
            "schema_version": 1,
            "evidence_id": "REC-EV-023C",
            "status": "LOCKED_ADAPTIVE_CROSSED_SENSITIVITY",
            **hashes,
            "source_manifest_sha256": sha256_file(manifest_path),
            "predictions_recomputed": False,
            "rankings_recomputed": False,
            "q_labels_opened": False,
            "locked_test_opened": False,
            "stage2_opened": False,
            "final_reserve_opened": False,
            "champion": None,
            "product_policy_updated": False,
        }
        actual = read_json(lock_path)
        if actual != expected:
            raise ResumeError("REC-EV-023C protocol lock drift")
        return actual
    if resume:
        raise ResumeError("create the first REC-EV-023C lock without --resume")
    atomic_write_json(manifest_path, manifest)
    lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023C",
        "status": "LOCKED_ADAPTIVE_CROSSED_SENSITIVITY",
        **hashes,
        "source_manifest_sha256": sha256_file(manifest_path),
        "predictions_recomputed": False,
        "rankings_recomputed": False,
        "q_labels_opened": False,
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
    value = read_json(path) if path.is_file() else {"schema_version": 1, "evidence_id": "REC-EV-023C"}
    value.update({"phase": phase, **extra})
    atomic_write_json(path, value)


def verify_rec_ev_023b_chain(contract: Mapping[str, Any]) -> dict[str, Any]:
    entries = contract["allowed_input_artifacts"]
    prior_contract = read_json(resolve_input(entries["rec_ev_023b_contract"]))
    prior_lock = read_json(resolve_input(entries["rec_ev_023b_protocol_lock"]))
    prior_manifest = read_json(resolve_input(entries["rec_ev_023b_source_manifest"]))
    if prior_lock.get("contract_sha256") != entries["rec_ev_023b_contract"]["sha256"]:
        raise RuntimeError("REC-EV-023B contract-to-lock drift")
    if prior_lock.get("source_manifest_sha256") != entries["rec_ev_023b_source_manifest"]["sha256"]:
        raise RuntimeError("REC-EV-023B manifest-to-lock drift")
    if hashlib.sha256(canonical_json_bytes(prior_manifest.get("sources"))).hexdigest() != prior_lock.get("source_artifacts_sha256"):
        raise RuntimeError("REC-EV-023B source family digest drift")
    if hashlib.sha256(canonical_json_bytes(prior_manifest.get("implementation_artifacts"))).hexdigest() != prior_lock.get("implementation_artifacts_sha256"):
        raise RuntimeError("REC-EV-023B implementation family digest drift")
    if hashlib.sha256(canonical_json_bytes(prior_locked_spec(prior_contract))).hexdigest() != prior_lock.get("locked_spec_sha256"):
        raise RuntimeError("REC-EV-023B locked spec digest drift")
    if prior_manifest.get("locked_test_opened") or prior_manifest.get("stage2_opened") or prior_manifest.get("final_reserve_opened"):
        raise RuntimeError("REC-EV-023B source manifest forbidden access drift")
    if any(prior_lock.get(key) for key in ("locked_test_opened", "stage2_opened", "final_reserve_opened")):
        raise RuntimeError("REC-EV-023B protocol forbidden access drift")
    prior_signature = hashlib.sha256(canonical_json_bytes({key: prior_lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )})).hexdigest()
    prepared = read_json(resolve_input(entries["rec_ev_023b_score_prepared_integrity"]))
    metrics_integrity = read_json(resolve_input(entries["rec_ev_023b_user_metrics_integrity"]))
    if prepared.get("run_signature") != prior_signature or metrics_integrity.get("run_signature") != prior_signature:
        raise RuntimeError("REC-EV-023B derived seal signature drift")
    metrics_artifact = metrics_integrity.get("artifacts", {}).get("user_metrics", {})
    expected_prepared_set = {"universe", "train_prior", "structured_full", "e5_aligned", "score_input"}
    if set(prepared.get("artifacts", {})) != expected_prepared_set or set(metrics_integrity.get("artifacts", {})) != {"user_metrics"}:
        raise RuntimeError("REC-EV-023B derived artifact set drift")
    for artifact_name, source_name in (("universe", "rec_ev_023b_universe"), ("score_input", "rec_ev_023b_score_input")):
        artifact = prepared["artifacts"][artifact_name]
        source = entries[source_name]
        if artifact != {"path": source["path"], "bytes": source["bytes"], "sha256": source["sha256"]}:
            raise RuntimeError(f"REC-EV-023B prepared artifact seal drift: {artifact_name}")
    expected_metric = entries["rec_ev_023b_user_metrics"]
    if metrics_artifact != {"path": expected_metric["path"], "bytes": expected_metric["bytes"], "sha256": expected_metric["sha256"]}:
        raise RuntimeError("REC-EV-023B metric seal drift")
    result = read_json(resolve_input(entries["rec_ev_023b_result"]))
    selection = read_json(resolve_input(entries["rec_ev_023b_selection"]))
    if result.get("selection") != selection or selection.get("status") != "PSEUDO_COLD_DEVELOPMENT_SIGNAL":
        raise RuntimeError("REC-EV-023B result-selection drift")
    if selection.get("champion") is not None or any(selection.get(key) for key in (
        "locked_test_opened", "stage2_opened", "final_reserve_opened", "product_policy_updated",
    )):
        raise RuntimeError("REC-EV-023B selection invariant drift")
    return prior_contract


def _component_count(user_items: Sequence[Sequence[int]]) -> int:
    item_values = sorted({int(movie) for row in user_items for movie in row})
    item_position = {movie: len(user_items) + index for index, movie in enumerate(item_values)}
    parent = np.arange(len(user_items) + len(item_values), dtype=np.int32)
    size = np.ones(len(parent), dtype=np.int32)

    def find(value: int) -> int:
        while int(parent[value]) != value:
            parent[value] = parent[int(parent[value])]
            value = int(parent[value])
        return value

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a == b:
            return
        if size[a] < size[b]:
            a, b = b, a
        parent[b] = a
        size[a] += size[b]

    for user_index, movies in enumerate(user_items):
        for movie in movies:
            union(user_index, item_position[int(movie)])
    return len({find(index) for index in range(len(parent))})


def prepare_membership(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    membership_path = output_path(contract, "membership")
    integrity_path = output_path(contract, "membership_integrity")
    if membership_path.exists() or integrity_path.exists():
        integrity = verify_integrity(integrity_path, {"membership": membership_path}, signature=signature)
        return {"status": "REUSED_MEMBERSHIP", **integrity["metadata"]}
    prior_contract = verify_rec_ev_023b_chain(contract)
    entries = contract["allowed_input_artifacts"]
    score_input = pd.read_parquet(resolve_input(entries["rec_ev_023b_score_input"]), columns=["user_key", "target_movie_ids"])
    score_input = score_input.sort_values("user_key", kind="stable", ignore_index=True)
    user_keys = score_input["user_key"].astype(str).tolist()
    target_rows = [[int(movie) for movie in row] for row in score_input["target_movie_ids"]]
    if len(user_keys) != int(contract["membership"]["users"]) or any(len(row) != 20 or len(set(row)) != 20 for row in target_rows):
        raise RuntimeError("fixed JUDGED20 membership drift")
    item_ids = np.asarray(sorted({movie for row in target_rows for movie in row}), dtype=np.int64)
    item_position = {int(movie): index for index, movie in enumerate(item_ids.tolist())}
    rows = np.repeat(np.arange(len(user_keys), dtype=np.int32), 20)
    columns = np.asarray([item_position[movie] for row in target_rows for movie in row], dtype=np.int32)
    values = np.full(len(columns), 1.0 / 20.0, dtype=np.float64)
    membership = sparse.coo_matrix((values, (rows, columns)), shape=(len(user_keys), len(item_ids))).tocsr()
    degrees = np.asarray((membership != 0).sum(axis=0)).ravel().astype(np.int64)
    top10_sum = int(np.sort(degrees)[-10:].sum())
    universe = np.load(resolve_input(entries["rec_ev_023b_universe"]), allow_pickle=False)
    universe_ids = universe["item_ids"].astype(np.int64)
    warm = universe["warm_mask"].astype(bool)
    lookup = {int(movie): index for index, movie in enumerate(universe_ids.tolist())}
    if any(int(movie) not in lookup or bool(warm[lookup[int(movie)]]) for movie in item_ids):
        raise RuntimeError("membership includes a non-masked-cold target")
    observed = {
        "users": len(user_keys),
        "items_per_user": 20,
        "memberships": int(membership.nnz),
        "unique_items": len(item_ids),
        "connected_components": _component_count(target_rows),
        "maximum_item_degree": int(degrees.max(initial=0)),
        "top10_item_degree_sum": top10_sum,
        "top10_share_exact": f"{top10_sum}/{membership.nnz}",
        "all_targets_masked_cold": True,
        "csr_value": float(values[0]),
    }
    expected = contract["membership"]
    for key in (
        "users", "items_per_user", "memberships", "unique_items", "connected_components",
        "maximum_item_degree", "top10_item_degree_sum", "top10_share_exact", "csr_value",
    ):
        if observed[key] != expected[key]:
            raise RuntimeError(f"membership invariant drift: {key}={observed[key]}")
    metrics = pd.read_parquet(resolve_input(entries["rec_ev_023b_user_metrics"]))
    values_matrix, metadata, metric_users = build_contrasts(metrics, prior_contract)
    if metric_users != user_keys or values_matrix.shape != (9520, 72):
        raise RuntimeError("REC-EV-023B contrast-to-membership alignment drift")
    atomic_save_npz(
        membership_path,
        user_keys=np.asarray(user_keys, dtype="U64"),
        item_ids=item_ids.astype(np.int32),
        indptr=membership.indptr.astype(np.int32),
        indices=membership.indices.astype(np.int32),
        data=membership.data.astype(np.float64),
    )
    write_integrity(integrity_path, {"membership": membership_path}, signature=signature, metadata={
        **observed,
        "contrast_metadata_sha256": hashlib.sha256(canonical_json_bytes(metadata)).hexdigest(),
    })
    progress_update(contract, "MEMBERSHIP_PREPARED", users=len(user_keys), unique_items=len(item_ids))
    return {"status": "PREPARED_MEMBERSHIP", **observed}


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


def poisson_weight(
    protocol_version: str, attempt: int, axis: str, cluster_id: str, cutoffs: Sequence[int],
) -> tuple[int, int]:
    payload = f"feelm-bootstrap-v1|{protocol_version}|{canonical_decimal(attempt)}|{axis}|{cluster_id}".encode("utf-8")
    x_value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)
    return bisect.bisect_left(cutoffs, x_value), x_value


def verify_golden(contract: Mapping[str, Any], cutoffs: Sequence[int]) -> None:
    protocol = str(contract["bootstrap"]["protocol_version"])
    for row in contract["bootstrap"]["golden"]:
        weight, x_value = poisson_weight(protocol, int(row["attempt"]), str(row["axis"]), str(row["cluster_id"]), cutoffs)
        if x_value != int(row["x"]) or weight != int(row["weight"]):
            raise RuntimeError("inverse-Poisson golden fixture drift")


def run_bootstrap(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    membership_path = output_path(contract, "membership")
    membership_integrity = verify_integrity(
        output_path(contract, "membership_integrity"), {"membership": membership_path}, signature=signature,
    )
    replicates_path = output_path(contract, "replicates")
    replicates_integrity_path = output_path(contract, "replicates_integrity")
    if replicates_path.exists() or replicates_integrity_path.exists():
        integrity = verify_integrity(replicates_integrity_path, {"replicates": replicates_path}, signature=signature)
        return {"status": "REUSED_BOOTSTRAP", **integrity["metadata"]}
    cached = np.load(membership_path, allow_pickle=False)
    user_keys = cached["user_keys"].astype(str).tolist()
    item_ids = cached["item_ids"].astype(np.int64)
    membership = sparse.csr_matrix(
        (cached["data"].astype(np.float64), cached["indices"].astype(np.int32), cached["indptr"].astype(np.int32)),
        shape=(len(user_keys), len(item_ids)),
    )
    prior_contract = verify_rec_ev_023b_chain(contract)
    metrics = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["rec_ev_023b_user_metrics"]))
    contrast_values, metadata, metric_users = build_contrasts(metrics, prior_contract)
    if metric_users != user_keys or hashlib.sha256(canonical_json_bytes(metadata)).hexdigest() != membership_integrity["metadata"]["contrast_metadata_sha256"]:
        raise RuntimeError("contrast metadata drift before bootstrap")
    point = contrast_values.mean(axis=0)
    cutoffs = poisson_cutoffs(precision=int(contract["bootstrap"]["decimal_precision"]))
    verify_golden(contract, cutoffs)
    protocol = str(contract["bootstrap"]["protocol_version"])
    target_valid = int(contract["bootstrap"]["valid_replicates"])
    arrays = {regime: [] for regime in REGIMES}
    valid_attempts: list[int] = []
    invalid_attempts: list[int] = []
    invalid_reasons: list[str] = []
    for attempt in range(int(contract["bootstrap"]["attempts"])):
        user_weights = np.fromiter(
            (poisson_weight(protocol, attempt, "user", key, cutoffs)[0] for key in user_keys),
            dtype=np.float64,
            count=len(user_keys),
        )
        item_weights = np.fromiter(
            (poisson_weight(protocol, attempt, "item", canonical_decimal(int(movie)), cutoffs)[0] for movie in item_ids),
            dtype=np.float64,
            count=len(item_ids),
        )
        item_membership_weight = np.asarray(membership @ item_weights, dtype=np.float64).ravel()
        request_weights = {
            "USER_ONLY": user_weights,
            "ITEM_ONLY": item_membership_weight,
            "TWO_WAY": user_weights * item_membership_weight,
        }
        current: dict[str, np.ndarray] = {}
        reason = ""
        for regime in REGIMES:
            weights = request_weights[regime]
            denominator = float(weights.sum())
            if not math.isfinite(denominator) or denominator <= 0:
                reason = f"{regime}_DENOMINATOR"
                break
            estimate = (weights @ contrast_values) / denominator
            if not np.isfinite(estimate).all():
                reason = f"{regime}_NONFINITE_ESTIMATE"
                break
            current[regime] = np.asarray(estimate, dtype=np.float64)
        if reason:
            invalid_attempts.append(attempt)
            invalid_reasons.append(reason)
            continue
        valid_attempts.append(attempt)
        for regime in REGIMES:
            arrays[regime].append(current[regime])
        if len(valid_attempts) == target_valid:
            break
        if attempt % 100 == 0:
            progress_update(contract, "BOOTSTRAP", attempt=attempt, valid=len(valid_attempts))
    if len(valid_attempts) != target_valid:
        raise RuntimeError("fewer than 2,000 common-valid bootstrap replicates")
    matrices = {regime: np.vstack(arrays[regime]).astype(np.float64) for regime in REGIMES}
    atomic_save_npz(
        replicates_path,
        point=point.astype(np.float64),
        user_only=matrices["USER_ONLY"],
        item_only=matrices["ITEM_ONLY"],
        two_way=matrices["TWO_WAY"],
        valid_attempt_ids=np.asarray(valid_attempts, dtype=np.int32),
        invalid_attempt_ids=np.asarray(invalid_attempts, dtype=np.int32),
        invalid_reason_codes=np.asarray(invalid_reasons, dtype="U64"),
    )
    metadata_value = {
        "valid_replicates": len(valid_attempts),
        "invalid_attempts": len(invalid_attempts),
        "first_valid_attempt": valid_attempts[0],
        "last_valid_attempt": valid_attempts[-1],
        "contrasts": contrast_values.shape[1],
        "regimes": list(REGIMES),
        "golden_verified": True,
    }
    write_integrity(
        replicates_integrity_path, {"replicates": replicates_path}, signature=signature, metadata=metadata_value,
    )
    progress_update(contract, "BOOTSTRAP_COMPLETE", **metadata_value)
    return {"status": "BOOTSTRAP_COMPLETE", **metadata_value}


def nearest_rank(values: Sequence[float], probability: float) -> float:
    array = np.sort(np.asarray(values, dtype=np.float64))
    if not len(array) or not 0 < probability <= 1:
        raise ValueError("invalid nearest-rank inputs")
    return float(array[math.ceil(probability * len(array)) - 1])


def regime_intervals(point: np.ndarray, replicates: np.ndarray) -> dict[str, Any]:
    se = replicates.std(axis=0, ddof=1)
    active = np.isfinite(se) & (se > 0)
    if bool(active.any()):
        maxima = np.max(np.abs((replicates[:, active] - point[active]) / se[active]), axis=1)
        critical = nearest_rank(maxima, 0.95)
    else:
        maxima = np.zeros(len(replicates), dtype=np.float64)
        critical = 0.0
    half_width = critical * np.where(active, se, 0.0)
    return {
        "se": se,
        "active": active,
        "critical": critical,
        "low": point - half_width,
        "high": point + half_width,
        "half_width": half_width,
        "maxima": maxima,
    }


def _interval_rows(
    metadata: Sequence[Mapping[str, Any]], point: np.ndarray, values: Mapping[str, Any], regime: str,
) -> list[dict[str, Any]]:
    return [{
        **dict(meta),
        "regime": regime,
        "mean": float(point[index]),
        "se": float(values["se"][index]),
        "low": float(values["low"][index]),
        "high": float(values["high"][index]),
        "half_width": float(values["half_width"][index]),
        "estimable": bool(values["active"][index]),
    } for index, meta in enumerate(metadata)]


def _truth_table(
    rows: Sequence[Mapping[str, Any]], cells: Sequence[Mapping[str, Any]], *, utility_margin: float, loss_margin: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lookup = {
        (str(row["encoding"]), int(row["k"]), str(row["left"]), str(row["right"]), str(row["metric"])): row
        for row in rows
    }

    def q(encoding: str, k: int, left: str, right: str) -> bool:
        utility = lookup[(encoding, k, left, right, "top2_mean_q")]
        loss = lookup[(encoding, k, left, right, "top2_worst_q_loss")]
        return (
            bool(utility.get("estimable", True)) and bool(loss.get("estimable", True))
            and float(utility["low"]) >= utility_margin and float(loss["high"]) <= loss_margin
        )

    propositions: list[dict[str, Any]] = []
    forward: list[dict[str, Any]] = []
    for cell in cells:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        structured = q(encoding, k, "STRUCTURED", "RANDOM_EXPECTATION")
        e5 = q(encoding, k, "E5", "RANDOM_EXPECTATION") and q(encoding, k, "E5", "STRUCTURED")
        rrf = (
            q(encoding, k, "AVAILABLE_HEAD_CONTENT_RRF", "RANDOM_EXPECTATION")
            and q(encoding, k, "AVAILABLE_HEAD_CONTENT_RRF", "E5")
            and q(encoding, k, "AVAILABLE_HEAD_CONTENT_RRF", "STRUCTURED")
        )
        propositions.append({
            "encoding": encoding, "k": k,
            "structured_signal": structured, "e5_incremental_signal": e5, "rrf_improvement_signal": rrf,
        })
        for head, passed in (("STRUCTURED", structured), ("E5", e5), ("AVAILABLE_HEAD_CONTENT_RRF", rrf)):
            if passed:
                forward.append({"encoding": encoding, "k": k, "head": head})
    return propositions, forward


def analyze(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    replicates_path = output_path(contract, "replicates")
    replicate_integrity = verify_integrity(
        output_path(contract, "replicates_integrity"), {"replicates": replicates_path}, signature=signature,
    )
    cached = np.load(replicates_path, allow_pickle=False)
    point = cached["point"].astype(np.float64)
    prior_contract = verify_rec_ev_023b_chain(contract)
    metrics = pd.read_parquet(resolve_input(contract["allowed_input_artifacts"]["rec_ev_023b_user_metrics"]))
    recomputed_point_values, metadata, _ = build_contrasts(metrics, prior_contract)
    if not np.array_equal(point, recomputed_point_values.mean(axis=0)):
        raise RuntimeError("sealed bootstrap point estimate drift")
    all_rows: list[dict[str, Any]] = []
    critical_values: dict[str, float] = {}
    nonestimable: dict[str, int] = {}
    two_way_rows: list[dict[str, Any]] = []
    for regime, key in (("USER_ONLY", "user_only"), ("ITEM_ONLY", "item_only"), ("TWO_WAY", "two_way")):
        values = regime_intervals(point, cached[key].astype(np.float64))
        rows = _interval_rows(metadata, point, values, regime)
        all_rows.extend(rows)
        critical_values[regime] = float(values["critical"])
        nonestimable[regime] = int((~values["active"]).sum())
        if regime == "TWO_WAY":
            two_way_rows = rows
    prior_result = read_json(resolve_input(contract["allowed_input_artifacts"]["rec_ev_023b_result"]))
    prior_rows = [{**row, "estimable": True} for row in prior_result["simultaneous_intervals"]]
    utility_margin = float(contract["decision"]["utility_margin"])
    loss_margin = float(contract["decision"]["worst_loss_margin"])
    prior_props, prior_forward = _truth_table(
        prior_rows, prior_contract["cells"], utility_margin=utility_margin, loss_margin=loss_margin,
    )
    if prior_forward != prior_result["selection"]["forward_set"]:
        raise RuntimeError("REC-EV-023B forward truth table drift")
    two_way_props, two_way_forward = _truth_table(
        two_way_rows, prior_contract["cells"], utility_margin=utility_margin, loss_margin=loss_margin,
    )
    prior_keys = {(row["encoding"], int(row["k"]), row["head"]) for row in prior_forward}
    robust_forward = [
        row for row in two_way_forward
        if (row["encoding"], int(row["k"]), row["head"]) in prior_keys
    ]
    if any((row["encoding"], int(row["k"]), row["head"]) not in prior_keys for row in robust_forward):
        raise RuntimeError("robust forward is not a subset of REC-EV-023B")
    status = "OBSERVED_MEMBERSHIP_ROBUST_SIGNAL" if robust_forward else "OBSERVED_MEMBERSHIP_NO_ROBUST_SIGNAL"
    selection = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023C",
        "status": status,
        "rec_ev_023b_propositions_recomputed": prior_props,
        "rec_ev_023b_forward_recomputed": prior_forward,
        "two_way_propositions": two_way_props,
        "two_way_forward": two_way_forward,
        "robust_forward": robust_forward,
        "champion": None,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
    }
    membership_integrity = read_json(output_path(contract, "membership_integrity"))
    result = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023C",
        "status": status,
        "claim_boundary": contract["adaptive_boundary"],
        "membership": membership_integrity["metadata"],
        "bootstrap": replicate_integrity["metadata"],
        "critical_values": critical_values,
        "nonestimable_contrasts": nonestimable,
        "intervals": all_rows,
        "selection": selection,
        "prediction_or_ranking_recomputed": False,
        "q_labels_opened": False,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "champion": None,
        "product_policy_updated": False,
    }
    atomic_write_json(output_path(contract, "selection"), selection)
    atomic_write_json(output_path(contract, "result"), result)
    progress_update(contract, "COMPLETE", status=status, robust_forward=len(robust_forward))
    return selection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--phase", choices=("lock", "prepare", "bootstrap", "analyze", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    contract_path = args.contract.resolve()
    if contract_path != DEFAULT_CONTRACT.resolve():
        raise RuntimeError("REC-EV-023C accepts only the committed default contract")
    contract = read_json(contract_path)
    validate_contract(contract)
    if np.__version__ != contract["bootstrap"]["numpy_version"]:
        raise RuntimeError(f"NumPy version drift: {np.__version__}")
    if args.phase == "lock":
        value = create_or_verify_lock(contract, contract_path, resume=args.resume)
    else:
        if not args.resume:
            raise ResumeError("REC-EV-023C real phases require --resume")
        create_or_verify_lock(contract, contract_path, resume=True)
        if args.phase in {"prepare", "run"}:
            value = prepare_membership(contract)
        if args.phase in {"bootstrap", "run"}:
            if not output_path(contract, "membership_integrity").is_file():
                raise ResumeError("bootstrap requires sealed membership")
            value = run_bootstrap(contract)
        if args.phase in {"analyze", "run"}:
            if not output_path(contract, "replicates_integrity").is_file():
                raise ResumeError("analysis requires sealed bootstrap replicates")
            value = analyze(contract)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
