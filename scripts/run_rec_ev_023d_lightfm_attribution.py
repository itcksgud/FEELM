#!/usr/bin/env python3
"""Run REC-EV-023D feature-only LightFM attribution on the fixed 023B slate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import sparse

try:
    from rec_ev_022a_core import RATING_VALUES, encoding_weights, pair1_metrics, rating_indices, user_key
    from run_rec_ev_023b_masked_cold_screen import (
        _movie_position_lookup,
        _role_lookups,
        analytic_random_top2,
        atomic_save_npy,
        atomic_save_npz,
        atomic_save_sparse,
        atomic_to_parquet,
        atomic_write_json,
        canonical_json_bytes,
        read_json,
        sha256_contract,
        sha256_file,
        verify_implementation,
        verify_integrity,
        verify_sources,
        write_integrity,
    )
    from run_rec_ev_023c_crossed_sensitivity import poisson_cutoffs, poisson_weight, regime_intervals
    from validate_rec_ev_023d_contract import validate
except ModuleNotFoundError:
    from scripts.rec_ev_022a_core import RATING_VALUES, encoding_weights, pair1_metrics, rating_indices, user_key
    from scripts.run_rec_ev_023b_masked_cold_screen import (
        _movie_position_lookup,
        _role_lookups,
        analytic_random_top2,
        atomic_save_npy,
        atomic_save_npz,
        atomic_save_sparse,
        atomic_to_parquet,
        atomic_write_json,
        canonical_json_bytes,
        read_json,
        sha256_contract,
        sha256_file,
        verify_implementation,
        verify_integrity,
        verify_sources,
        write_integrity,
    )
    from scripts.run_rec_ev_023c_crossed_sensitivity import poisson_cutoffs, poisson_weight, regime_intervals
    from scripts.validate_rec_ev_023d_contract import validate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023d-feature-only-lightfm-attribution.json"
PRIMARY_METRICS = ("top2_mean_q", "top2_worst_q_loss")
SCORED_HEADS = (
    "STRUCTURED_ORIGINAL",
    "STRUCTURED_MATCHED",
    "BIAS_ONLY",
    "EMBEDDED_DIRECT",
    "LIGHTFM_FULL",
    "RRF_ORIGINAL_LIGHTFM",
)
REGIMES = ("USER_ONLY", "ITEM_ONLY", "TWO_WAY")
MAX_USER_ID = 300_000


class ResumeError(RuntimeError):
    pass


def resolve_input(entry: Mapping[str, Any]) -> Path:
    path = Path(str(entry["path"]))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def output_path(contract: Mapping[str, Any], name: str) -> Path:
    return ROOT / str(contract["output_root"]) / str(contract["outputs"][name])


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "purpose", "authorization", "implementation_artifacts", "fixed_reuse", "train_reader",
        "feature_support", "lightfm", "target_fold_in", "heads", "head_semantics", "scoring",
        "metrics", "statistics", "decision", "carry_forward_equivalence", "resume",
        "claim_boundary", "invariants",
    )
    return {key: contract[key] for key in keys}


def _manifest(sources: list[dict[str, Any]], implementations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_id": "REC-EV-023D",
        "sources": sources,
        "implementation_artifacts": implementations,
        "adaptive_prior_results_seen": True,
        "rating_member_opened_at_lock": False,
        "evaluation_labels_opened_at_lock": False,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
    }


def verify_upstream(contract: Mapping[str, Any]) -> None:
    entries = contract["allowed_input_artifacts"]
    prior_lock = read_json(resolve_input(entries["rec_ev_023b_protocol_lock"]))
    if prior_lock.get("contract_sha256") != entries["rec_ev_023b_contract"]["sha256"]:
        raise RuntimeError("REC-EV-023B contract-lock chain drift")
    if prior_lock.get("source_manifest_sha256") != entries["rec_ev_023b_source_manifest"]["sha256"]:
        raise RuntimeError("REC-EV-023B manifest-lock chain drift")
    prepared = read_json(resolve_input(entries["rec_ev_023b_score_prepared_integrity"]))
    expected_prepared = {
        "universe": "rec_ev_023b_universe",
        "train_prior": "rec_ev_023b_train_prior",
        "structured_full": "rec_ev_023b_structured_full",
        "score_input": "rec_ev_023b_score_input",
    }
    for artifact, source in expected_prepared.items():
        expected = entries[source]
        observed = prepared.get("artifacts", {}).get(artifact)
        if observed != {"path": expected["path"], "bytes": expected["bytes"], "sha256": expected["sha256"]}:
            raise RuntimeError(f"REC-EV-023B prepared seal drift: {artifact}")
    rank_integrity = read_json(resolve_input(entries["rec_ev_023b_score_rank_integrity"]))
    rank = entries["rec_ev_023b_score_rank"]
    if rank_integrity.get("artifacts", {}).get("score_rank") != {
        "path": rank["path"], "bytes": rank["bytes"], "sha256": rank["sha256"],
    }:
        raise RuntimeError("REC-EV-023B score-rank seal drift")
    metric_integrity = read_json(resolve_input(entries["rec_ev_023b_user_metrics_integrity"]))
    metric = entries["rec_ev_023b_user_metrics"]
    if metric_integrity.get("artifacts", {}).get("user_metrics") != {
        "path": metric["path"], "bytes": metric["bytes"], "sha256": metric["sha256"],
    }:
        raise RuntimeError("REC-EV-023B user-metric seal drift")
    label_integrity = read_json(resolve_input(entries["rec_ev_023b_evaluation_labels_integrity"]))
    label = entries["rec_ev_023b_evaluation_labels"]
    if label_integrity.get("artifacts", {}).get("evaluation_labels") != {
        "path": label["path"], "bytes": label["bytes"], "sha256": label["sha256"],
    }:
        raise RuntimeError("REC-EV-023B evaluation-label seal drift")
    prior_c_lock = read_json(resolve_input(entries["rec_ev_023c_protocol_lock"]))
    prior_c_manifest = read_json(resolve_input(entries["rec_ev_023c_source_manifest"]))
    if prior_c_lock.get("contract_sha256") != entries["rec_ev_023c_contract"]["sha256"]:
        raise RuntimeError("REC-EV-023C contract-lock chain drift")
    if prior_c_lock.get("source_manifest_sha256") != entries["rec_ev_023c_source_manifest"]["sha256"]:
        raise RuntimeError("REC-EV-023C manifest-lock chain drift")
    if hashlib.sha256(canonical_json_bytes(prior_c_manifest.get("sources"))).hexdigest() != prior_c_lock.get("source_artifacts_sha256"):
        raise RuntimeError("REC-EV-023C source family digest drift")
    if hashlib.sha256(canonical_json_bytes(prior_c_manifest.get("implementation_artifacts"))).hexdigest() != prior_c_lock.get("implementation_artifacts_sha256"):
        raise RuntimeError("REC-EV-023C implementation family digest drift")
    prior_c_signature = hashlib.sha256(canonical_json_bytes({key: prior_c_lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )})).hexdigest()
    membership_integrity = read_json(resolve_input(entries["rec_ev_023c_membership_integrity"]))
    membership = entries["rec_ev_023c_membership"]
    if membership_integrity.get("artifacts", {}).get("membership") != {
        "path": membership["path"], "bytes": membership["bytes"], "sha256": membership["sha256"],
    }:
        raise RuntimeError("REC-EV-023C membership seal drift")
    prior_bootstrap_integrity = read_json(resolve_input(entries["rec_ev_023c_bootstrap_integrity"]))
    prior_replicates = entries["rec_ev_023c_bootstrap_replicates"]
    if membership_integrity.get("run_signature") != prior_c_signature or prior_bootstrap_integrity.get("run_signature") != prior_c_signature:
        raise RuntimeError("REC-EV-023C derived signature drift")
    if prior_bootstrap_integrity.get("artifacts", {}).get("replicates") != {
        "path": prior_replicates["path"], "bytes": prior_replicates["bytes"], "sha256": prior_replicates["sha256"],
    }:
        raise RuntimeError("REC-EV-023C bootstrap seal drift")
    selection = read_json(resolve_input(entries["rec_ev_023c_selection"]))
    robust = selection.get("robust_forward", [])
    cells = contract["fixed_reuse"]["cells"]
    expected = [{"encoding": cell["encoding"], "k": cell["k"], "head": "STRUCTURED"} for cell in cells]
    if robust != expected or selection.get("champion") is not None:
        raise RuntimeError("REC-EV-023C robust truth drift")


def create_or_verify_lock(contract: Mapping[str, Any], contract_path: Path, *, resume: bool) -> dict[str, Any]:
    sources = verify_sources(contract)
    implementations = verify_implementation(contract)
    verify_upstream(contract)
    manifest = _manifest(sources, implementations)
    hashes = {
        "contract_sha256": sha256_contract(contract_path),
        "source_artifacts_sha256": hashlib.sha256(canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(canonical_json_bytes(locked_spec(contract))).hexdigest(),
    }
    manifest_path = output_path(contract, "source_manifest")
    lock_path = output_path(contract, "protocol_lock")
    expected_lock = {
        "schema_version": 1,
        "evidence_id": "REC-EV-023D",
        "status": "LOCKED_ADAPTIVE_FEATURE_ONLY_LIGHTFM_ATTRIBUTION",
        **hashes,
        "source_manifest_sha256": None,
        "rating_member_opened_at_lock": False,
        "evaluation_labels_opened_at_lock": False,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "champion": None,
        "product_policy_updated": False,
    }
    if lock_path.is_file():
        if not resume:
            raise ResumeError("existing REC-EV-023D lock requires --resume")
        if not manifest_path.is_file() or read_json(manifest_path) != manifest:
            raise ResumeError("REC-EV-023D source manifest drift")
        expected_lock["source_manifest_sha256"] = sha256_file(manifest_path)
        if read_json(lock_path) != expected_lock:
            raise ResumeError("REC-EV-023D protocol lock drift")
        return expected_lock
    if resume:
        raise ResumeError("create REC-EV-023D lock without --resume first")
    atomic_write_json(manifest_path, manifest)
    expected_lock["source_manifest_sha256"] = sha256_file(manifest_path)
    atomic_write_json(lock_path, expected_lock)
    return expected_lock


def run_signature(contract: Mapping[str, Any]) -> str:
    lock = read_json(output_path(contract, "protocol_lock"))
    payload = {key: lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def progress_update(contract: Mapping[str, Any], phase: str, **extra: Any) -> None:
    path = output_path(contract, "progress")
    value = read_json(path) if path.is_file() else {"schema_version": 1, "evidence_id": "REC-EV-023D"}
    value.update({"phase": phase, **extra})
    atomic_write_json(path, value)


def _rating_index(raw: bytes) -> int:
    return int(rating_indices(np.asarray([float(raw)], dtype=np.float64))[0])


def _scan_train_histogram(
    archive: Path, member: str, movie_lookup: np.ndarray, warm: np.ndarray,
) -> tuple[np.ndarray, dict[str, int]]:
    old_allowed, roles = _role_lookups()
    hist = np.zeros((MAX_USER_ID + 1, len(RATING_VALUES)), dtype=np.uint32)
    counters = {"raw_rows": 0, "excluded_after_user_id": 0, "train_warm_rating_parsed": 0, "masked_cold_train_rating_parsed": 0, "timestamp_parsed": 0}
    with zipfile.ZipFile(archive) as bundle, bundle.open(member) as handle:
        header = handle.readline().strip()
        if header != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens rating header drift")
        for raw in handle:
            counters["raw_rows"] += 1
            fields = raw.rstrip(b"\r\n").split(b",")
            uid = int(fields[0])
            if uid > MAX_USER_ID or not old_allowed[uid] or int(roles[uid]) >= 6000:
                counters["excluded_after_user_id"] += 1
                continue
            movie = int(fields[1])
            position = int(movie_lookup[movie]) if movie < len(movie_lookup) else -1
            if position < 0:
                continue
            if not bool(warm[position]):
                continue
            index = _rating_index(fields[2])
            hist[uid, index] += 1
            counters["train_warm_rating_parsed"] += 1
    return hist, counters


def _sign_table(hist: np.ndarray, g0_mid: np.ndarray, tau: float = 5.0) -> np.ndarray:
    counts = hist.astype(np.float64)
    totals = counts.sum(axis=1)
    below = np.cumsum(counts, axis=1) - counts
    q = np.divide(
        below + 0.5 * counts + tau * np.asarray(g0_mid, dtype=np.float64)[None, :],
        totals[:, None] + tau,
        out=np.zeros_like(counts),
        where=(totals[:, None] + tau) > 0,
    )
    return np.sign(2.0 * q - 1.0).astype(np.int8)


def _build_interactions(
    archive: Path,
    member: str,
    movie_lookup: np.ndarray,
    warm: np.ndarray,
    hist: np.ndarray,
    signs: np.ndarray,
) -> tuple[np.ndarray, sparse.csr_matrix, dict[str, int]]:
    active_uids = np.flatnonzero(hist.sum(axis=1) > 0)
    keyed = sorted((user_key(int(uid)), int(uid)) for uid in active_uids.tolist())
    train_keys = np.asarray([key for key, _ in keyed], dtype="U64")
    row_by_uid = np.full(MAX_USER_ID + 1, -1, dtype=np.int32)
    for row, (_, uid) in enumerate(keyed):
        row_by_uid[uid] = row
    keep_count = int(sum(hist[uid, index] for uid in active_uids for index in range(10) if signs[uid, index] != 0))
    rows = np.empty(keep_count, dtype=np.int32)
    columns = np.empty(keep_count, dtype=np.int32)
    values = np.empty(keep_count, dtype=np.int8)
    old_allowed, roles = _role_lookups()
    cursor = 0
    counters = {"train_warm_rating_parsed": 0, "signed_interactions": 0, "zero_labels_omitted": 0, "masked_cold_train_rating_parsed": 0, "timestamp_parsed": 0}
    with zipfile.ZipFile(archive) as bundle, bundle.open(member) as handle:
        handle.readline()
        for raw in handle:
            fields = raw.rstrip(b"\r\n").split(b",")
            uid = int(fields[0])
            if uid > MAX_USER_ID or not old_allowed[uid] or int(roles[uid]) >= 6000:
                continue
            movie = int(fields[1])
            position = int(movie_lookup[movie]) if movie < len(movie_lookup) else -1
            if position < 0 or not bool(warm[position]):
                continue
            index = _rating_index(fields[2])
            counters["train_warm_rating_parsed"] += 1
            sign = int(signs[uid, index])
            if sign == 0:
                counters["zero_labels_omitted"] += 1
                continue
            rows[cursor] = row_by_uid[uid]
            columns[cursor] = position
            values[cursor] = sign
            cursor += 1
    if cursor != keep_count:
        raise RuntimeError("signed interaction preallocation drift")
    order = np.lexsort((columns, rows))
    rows, columns, values = rows[order], columns[order], values[order]
    if len(rows) > 1 and bool(((rows[1:] == rows[:-1]) & (columns[1:] == columns[:-1])).any()):
        raise RuntimeError("duplicate TRAIN user-item interaction")
    if set(values.tolist()) != {-1, 1}:
        raise RuntimeError("TRAIN signed label class drift")
    matrix = sparse.csr_matrix((values, (rows, columns)), shape=(len(train_keys), len(warm)), dtype=np.int8)
    matrix.sort_indices()
    counters["signed_interactions"] = matrix.nnz
    return train_keys, matrix, counters


def prepare(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    artifacts = {
        "interactions": output_path(contract, "interactions"),
        "train_users": output_path(contract, "train_users"),
        "feature_mask": output_path(contract, "feature_mask"),
        "structured_matched": output_path(contract, "structured_matched"),
    }
    integrity_path = output_path(contract, "prepared_integrity")
    if any(path.exists() for path in artifacts.values()) or integrity_path.exists():
        integrity = verify_integrity(integrity_path, artifacts, signature=signature)
        return {"status": "REUSED_PREPARED", **integrity["metadata"]}

    entries = contract["allowed_input_artifacts"]
    universe = np.load(resolve_input(entries["rec_ev_023b_universe"]), allow_pickle=False)
    item_ids = universe["item_ids"].astype(np.int64)
    warm = universe["warm_mask"].astype(bool)
    if (len(item_ids), int(warm.sum()), int((~warm).sum())) != (41439, 33078, 8361):
        raise RuntimeError("fixed item split drift")
    movie_lookup = _movie_position_lookup(item_ids)
    prior = np.load(resolve_input(entries["rec_ev_023b_train_prior"]), allow_pickle=False)
    g0_mid = prior["g0_mid"].astype(np.float64)
    archive_entry = entries["movielens_archive"]
    archive = resolve_input(archive_entry)
    hist, pass1 = _scan_train_histogram(archive, str(archive_entry["member"]), movie_lookup, warm)
    if pass1["train_warm_rating_parsed"] != 9006665 or pass1["masked_cold_train_rating_parsed"] != 0 or pass1["timestamp_parsed"] != 0:
        raise RuntimeError("TRAIN pass1 invariant drift")
    signs = _sign_table(hist, g0_mid, tau=5.0)
    train_keys, interactions, pass2 = _build_interactions(
        archive, str(archive_entry["member"]), movie_lookup, warm, hist, signs,
    )
    if pass2["train_warm_rating_parsed"] != 9006665 or pass2["masked_cold_train_rating_parsed"] != 0 or pass2["timestamp_parsed"] != 0:
        raise RuntimeError("TRAIN pass2 invariant drift")
    original = sparse.load_npz(resolve_input(entries["rec_ev_023b_structured_full"])).tocsr().astype(np.float32)
    touched = np.unique(interactions.indices)
    feature_mask = np.asarray(original[touched].getnnz(axis=0)).ravel() > 0
    if not bool(feature_mask.any()):
        raise RuntimeError("no TRAIN-touched structured feature")
    matched = original[:, feature_mask].tocsr()
    norms = np.sqrt(np.asarray(matched.multiply(matched).sum(axis=1)).ravel().astype(np.float64))
    if bool((norms <= 0).any()) or not np.isfinite(norms).all():
        raise RuntimeError("S_MATCHED contains zero or nonfinite row")
    matched = (sparse.diags((1.0 / norms).astype(np.float32)) @ matched).tocsr().astype(np.float32)
    matched.sort_indices()
    retained_support = np.asarray(matched[touched].getnnz(axis=0)).ravel()
    if bool((retained_support <= 0).any()) or bool((np.asarray(matched[~warm].getnnz(axis=1)).ravel() <= 0).any()):
        raise RuntimeError("retained feature or masked-cold row support drift")

    atomic_save_sparse(artifacts["interactions"], interactions)
    atomic_save_npy(artifacts["train_users"], train_keys)
    atomic_save_npy(artifacts["feature_mask"], feature_mask.astype(bool))
    atomic_save_sparse(artifacts["structured_matched"], matched)
    metadata = {
        "users": len(train_keys),
        "signed_interactions": interactions.nnz,
        "positive_interactions": int(np.count_nonzero(interactions.data == 1)),
        "negative_interactions": int(np.count_nonzero(interactions.data == -1)),
        "zero_labels_omitted": pass2["zero_labels_omitted"],
        "train_touched_items": len(touched),
        "original_feature_columns": original.shape[1],
        "retained_feature_columns": matched.shape[1],
        "all_universe_rows_positive_norm": True,
        "all_masked_cold_rows_positive_norm": True,
        "pass1": pass1,
        "pass2": pass2,
    }
    write_integrity(integrity_path, artifacts, signature=signature, metadata=metadata)
    progress_update(contract, "PREPARED", **metadata)
    return {"status": "PREPARED", **metadata}


def _fit_config(contract: Mapping[str, Any], seed: int) -> dict[str, Any]:
    model = contract["lightfm"]
    return {
        "loss": model["loss"], "dimension": model["dimension"], "learning_schedule": model["learning_schedule"],
        "learning_rate": model["learning_rate"], "item_alpha": model["item_alpha"], "user_alpha": model["user_alpha"],
        "epochs": model["epochs"], "threads": model["threads"], "seed": int(seed),
        "interaction_sha256": sha256_file(output_path(contract, "interactions")),
        "item_feature_sha256": sha256_file(output_path(contract, "structured_matched")),
        "item_identity_features": False,
    }


def _fit_directory(contract: Mapping[str, Any], seed: int) -> Path:
    return output_path(contract, "fit_root") / f"S{seed}"


def artifact_state(paths: Sequence[Path]) -> str:
    present = [path.exists() for path in paths]
    if not any(present):
        return "NONE"
    if all(present):
        return "ALL"
    return "PARTIAL"


def exact_regular_children(directory: Path, expected: set[Path]) -> bool:
    return bool(directory.is_dir() and set(directory.iterdir()) == expected and all(path.is_file() for path in expected))


def fit_seed_state(directory: Path) -> str:
    if not directory.exists() or (directory.is_dir() and not any(directory.iterdir())):
        return "NONE"
    expected = {directory / "config.json", directory / "result.npz", directory / "integrity.json"}
    return "ALL" if exact_regular_children(directory, expected) else "PARTIAL"


def _verify_fit(contract: Mapping[str, Any], seed: int, *, signature: str) -> dict[str, Any]:
    directory = _fit_directory(contract, seed)
    config_path, result_path, integrity_path = directory / "config.json", directory / "result.npz", directory / "integrity.json"
    integrity = verify_integrity(integrity_path, {"config": config_path, "result": result_path}, signature=signature)
    if read_json(config_path) != _fit_config(contract, seed):
        raise ResumeError(f"LightFM seed {seed} config drift")
    result = np.load(result_path, allow_pickle=False)
    biases, factors = result["item_biases"], result["item_factors"]
    if biases.shape != (41439,) or factors.shape != (41439, 128) or not np.isfinite(biases).all() or not np.isfinite(factors).all():
        raise ResumeError(f"LightFM seed {seed} result drift")
    if bool((np.linalg.norm(factors.astype(np.float64), axis=1) <= 0).any()):
        raise ResumeError(f"LightFM seed {seed} zero item factor")
    return integrity


def fit(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    verify_integrity(output_path(contract, "prepared_integrity"), {
        "interactions": output_path(contract, "interactions"),
        "train_users": output_path(contract, "train_users"),
        "feature_mask": output_path(contract, "feature_mask"),
        "structured_matched": output_path(contract, "structured_matched"),
    }, signature=signature)
    fit_root = output_path(contract, "fit_root")
    allowed_names = {f"S{int(seed)}" for seed in contract["lightfm"]["seeds"]}
    if fit_root.exists():
        actual_names = {path.name for path in fit_root.iterdir()}
        if not actual_names <= allowed_names or any(not path.is_dir() for path in fit_root.iterdir()):
            raise ResumeError("LightFM fit root contains an unexpected path")
    completed: list[int] = []
    for seed in contract["lightfm"]["seeds"]:
        seed = int(seed)
        directory = _fit_directory(contract, seed)
        config_path, result_path, integrity_path = directory / "config.json", directory / "result.npz", directory / "integrity.json"
        state = fit_seed_state(directory)
        if state == "ALL":
            _verify_fit(contract, seed, signature=signature)
            completed.append(seed)
            continue
        if state == "PARTIAL":
            raise ResumeError(f"LightFM seed {seed} is partial and cannot be promoted")
        directory.mkdir(parents=True, exist_ok=True)
        expected_config = _fit_config(contract, seed)
        if any(directory.iterdir()):
            raise ResumeError(f"LightFM seed {seed} new directory is not empty")
        atomic_write_json(config_path, expected_config)
        relative_job = directory.relative_to(ROOT).as_posix()
        relative_interactions = output_path(contract, "interactions").relative_to(ROOT).as_posix()
        relative_features = output_path(contract, "structured_matched").relative_to(ROOT).as_posix()
        model = contract["lightfm"]
        command = "\n".join([
            "python -m pip install --disable-pip-version-check --require-hashes -r requirements-rec-ev-019c.lock",
            f"python scripts/train_rec_ev_023d_lightfm.py --job /workspace/{relative_job} --interactions /workspace/{relative_interactions} --item-features /workspace/{relative_features}",
        ])
        completed_process = subprocess.run([
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "--mount", f"type=bind,source={ROOT},target=/workspace",
            "--mount", "type=volume,source=feelm-rec-ev-019c-pip,target=/root/.cache/pip",
            "--workdir", "/workspace", str(model["dependency"]["runtime_image"]), "sh", "-ec", command,
        ], check=False)
        if completed_process.returncode != 0:
            raise RuntimeError(f"REC-EV-023D LightFM seed {seed} failed: {completed_process.returncode}")
        write_integrity(integrity_path, {"config": config_path, "result": result_path}, signature=signature, metadata={
            "seed": seed, "item_feature_sha256": sha256_file(output_path(contract, "structured_matched")),
            "interaction_sha256": sha256_file(output_path(contract, "interactions")),
        })
        _verify_fit(contract, seed, signature=signature)
        completed.append(seed)
        progress_update(contract, "FITTING", completed_seeds=completed)
    if not fit_root.is_dir() or {path.name for path in fit_root.iterdir()} != allowed_names:
        raise ResumeError("LightFM completed seed directory set drift")
    return {"status": "FITS_COMPLETE", "seeds": completed}


def _stable_sigmoid(values: np.ndarray) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    result = np.empty_like(x)
    nonnegative = x >= 0
    result[nonnegative] = 1.0 / (1.0 + np.exp(-x[nonnegative]))
    exp_x = np.exp(x[~nonnegative])
    result[~nonnegative] = exp_x / (1.0 + exp_x)
    return result


def fold_in_batch(
    biases: np.ndarray, factors: np.ndarray, profile_positions: np.ndarray, weights: np.ndarray,
    target_positions: np.ndarray, *, steps: int = 80, learning_rate: float = 0.05, regularization: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    profile = factors[profile_positions].astype(np.float64)
    profile_bias = biases[profile_positions].astype(np.float64)
    w = np.asarray(weights, dtype=np.float64)
    nonzero = np.abs(w) > 0
    counts = nonzero.sum(axis=1)
    active = counts > 0
    confidence = np.zeros_like(w)
    mean_abs = np.divide(np.abs(w).sum(axis=1), counts, out=np.zeros(len(w)), where=active)
    confidence[active] = np.divide(np.abs(w[active]), mean_abs[active, None], out=np.zeros_like(w[active]), where=mean_abs[active, None] > 0)
    labels = np.sign(w)
    total_confidence = confidence.sum(axis=1)
    norm_squared = np.einsum("bkd,bkd->bk", profile, profile)
    l_bound = regularization + np.divide(
        np.sum(confidence * norm_squared, axis=1), 4.0 * total_confidence,
        out=np.zeros(len(w), dtype=np.float64), where=total_confidence > 0,
    )
    if bool((learning_rate * l_bound[active] > 1.0).any()):
        raise RuntimeError("023D fold-in Lipschitz guard failed")
    user = np.zeros((len(w), factors.shape[1]), dtype=np.float64)
    for _ in range(int(steps)):
        z = profile_bias + np.einsum("bkd,bd->bk", profile, user)
        sigmoid = _stable_sigmoid(-labels * z)
        coefficients = confidence * labels * sigmoid
        gradient = -np.einsum("bk,bkd->bd", coefficients, profile)
        gradient = np.divide(gradient, total_confidence[:, None], out=np.zeros_like(gradient), where=total_confidence[:, None] > 0)
        gradient += regularization * user
        if not np.isfinite(gradient).all():
            raise RuntimeError("023D fold-in gradient nonfinite")
        user -= learning_rate * gradient
    scores = biases[target_positions].astype(np.float64) + np.einsum(
        "btd,bd->bt", factors[target_positions].astype(np.float64), user,
    )
    if not np.isfinite(user).all() or not np.isfinite(scores[active]).all():
        raise RuntimeError("023D fold-in vector or score nonfinite")
    return scores, active


def _score_active(scores: np.ndarray) -> bool:
    return bool(np.isfinite(scores).all() and np.unique(scores).size >= 2)


def _rank(
    contract: Mapping[str, Any], movie_ids: np.ndarray, scores: np.ndarray, *, seed: int, head: str,
    encoding: str, k: int, user: str, rrf: bool = False,
) -> np.ndarray:
    if not _score_active(scores):
        return np.empty(0, dtype=np.int16)
    marker = "RRF_FINAL" if rrf else head
    order = sorted(range(len(movie_ids)), key=lambda index: (
        -float(scores[index]),
        hashlib.sha256(f"rec-ev-023d-score-tie-v1|{seed}|{marker}|{encoding}|{k}|{user}|{int(movie_ids[index])}".encode("utf-8")).digest(),
        int(movie_ids[index]),
    ))
    return np.asarray(order, dtype=np.int16)


def _rrf_order(
    contract: Mapping[str, Any], movie_ids: np.ndarray, component_orders: Sequence[np.ndarray], *, seed: int,
    encoding: str, k: int, user: str,
) -> tuple[np.ndarray, bool]:
    active = [np.asarray(order, dtype=np.int64) for order in component_orders if len(order)]
    if not active:
        return np.empty(0, dtype=np.int16), False
    scores = np.zeros(len(movie_ids), dtype=np.float64)
    for order in active:
        ranks = np.empty(len(order), dtype=np.int64)
        ranks[order] = np.arange(1, len(order) + 1)
        scores += 1.0 / (10.0 + ranks)
    order = _rank(contract, movie_ids, scores, seed=seed, head="RRF_ORIGINAL_LIGHTFM", encoding=encoding, k=k, user=user, rrf=True)
    return order, bool(len(order))


def _part_path(root: Path, start: int, stop: int) -> Path:
    return root / f"part-{start:06d}-{stop:06d}.parquet"


def _expected_rank_artifacts(contract: Mapping[str, Any], users: int) -> tuple[dict[str, Path], set[Path], set[Path]]:
    expected: dict[str, Path] = {}
    parts: set[Path] = set()
    integrities: set[Path] = set()
    for seed_raw in contract["lightfm"]["seeds"]:
        seed = int(seed_raw)
        root = output_path(contract, "rank_root") / f"S{seed}"
        per_seed_expected: set[Path] = set()
        for start in range(0, int(users), 200):
            stop = min(int(users), start + 200)
            part = _part_path(root, start, stop)
            integrity = part.with_suffix(".integrity.json")
            expected[f"S{seed}_{part.name}"] = part
            expected[f"S{seed}_{integrity.name}"] = integrity
            parts.add(part)
            integrities.add(integrity)
            per_seed_expected.update((part, integrity))
    return expected, parts, integrities


def verify_rank_set(contract: Mapping[str, Any], *, signature: str) -> dict[str, Any]:
    expected, expected_parts, expected_integrities = _expected_rank_artifacts(
        contract, int(contract["fixed_reuse"]["users"]),
    )
    rank_root = output_path(contract, "rank_root")
    actual_parts = set(rank_root.glob("S*/part-*.parquet")) if rank_root.is_dir() else set()
    actual_integrities = set(rank_root.glob("S*/part-*.integrity.json")) if rank_root.is_dir() else set()
    actual_seed_paths = {path.name for path in rank_root.iterdir()} if rank_root.is_dir() else set()
    expected_seed_paths = {f"S{int(seed)}" for seed in contract["lightfm"]["seeds"]}
    if actual_seed_paths != expected_seed_paths or actual_parts != expected_parts or actual_integrities != expected_integrities:
        raise ResumeError("rank-set exact path set drift")
    for seed_raw in contract["lightfm"]["seeds"]:
        seed = int(seed_raw)
        seed_root = rank_root / f"S{seed}"
        seed_expected = {
            path for path in expected_parts | expected_integrities if path.parent == seed_root
        }
        if not exact_regular_children(seed_root, seed_expected):
            raise ResumeError(f"rank seed S{seed} contains an unknown or non-file child")
        for start in range(0, int(contract["fixed_reuse"]["users"]), 200):
            stop = min(int(contract["fixed_reuse"]["users"]), start + 200)
            part = _part_path(rank_root / f"S{seed}", start, stop)
            integrity = verify_integrity(part.with_suffix(".integrity.json"), {"rank_part": part}, signature=signature)
            if integrity["metadata"].get("seed") != seed or integrity["metadata"].get("start") != start or integrity["metadata"].get("stop") != stop:
                raise ResumeError("rank part integrity slice drift")
    return verify_integrity(output_path(contract, "rank_set_integrity"), expected, signature=signature)


def score(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    for seed in contract["lightfm"]["seeds"]:
        _verify_fit(contract, int(seed), signature=signature)
    entries = contract["allowed_input_artifacts"]
    universe = np.load(resolve_input(entries["rec_ev_023b_universe"]), allow_pickle=False)
    item_ids = universe["item_ids"].astype(np.int64)
    position_lookup = _movie_position_lookup(item_ids)
    score_input = pd.read_parquet(resolve_input(entries["rec_ev_023b_score_input"])).sort_values("user_key", kind="stable", ignore_index=True)
    prior = np.load(resolve_input(entries["rec_ev_023b_train_prior"]), allow_pickle=False)
    g0_mid = prior["g0_mid"].astype(np.float64)
    matched = sparse.load_npz(output_path(contract, "structured_matched")).tocsr()
    original_rows = pd.read_parquet(resolve_input(entries["rec_ev_023b_score_rank"]))
    original_rows = original_rows.loc[original_rows["head"].eq("STRUCTURED")]
    original_lookup = {
        (str(row.user_key), str(row.encoding), int(row.k)): (np.asarray(row.ranked_target_indices, dtype=np.int16), bool(row.head_active))
        for row in original_rows.itertuples(index=False)
    }
    if len(original_lookup) != len(score_input) * len(contract["fixed_reuse"]["cells"]):
        raise RuntimeError("REC-EV-023B original rank lookup drift")
    batch_size = 200
    expected_artifacts: dict[str, Path] = {}
    factor_shas: dict[str, str] = {}
    for seed_raw in contract["lightfm"]["seeds"]:
        seed = int(seed_raw)
        result_path = _fit_directory(contract, seed) / "result.npz"
        factor_shas[str(seed)] = sha256_file(result_path)
        fitted = np.load(result_path, allow_pickle=False)
        biases = fitted["item_biases"].astype(np.float64)
        factors = fitted["item_factors"].astype(np.float64)
        norms = np.linalg.norm(factors, axis=1)
        normalized = factors / norms[:, None]
        parts_root = output_path(contract, "rank_root") / f"S{seed}"
        ranges = [(start, min(len(score_input), start + batch_size)) for start in range(0, len(score_input), batch_size)]
        for start, stop in ranges:
            destination = _part_path(parts_root, start, stop)
            integrity_path = destination.with_suffix(".integrity.json")
            expected_artifacts[f"S{seed}_{destination.name}"] = destination
            expected_artifacts[f"S{seed}_{integrity_path.name}"] = integrity_path
            expected_keys = score_input.iloc[start:stop]["user_key"].astype(str).tolist()
            metadata = {"seed": seed, "start": start, "stop": stop, "users": expected_keys, "rows": (stop - start) * len(contract["fixed_reuse"]["cells"]) * len(SCORED_HEADS)}
            if destination.exists() or integrity_path.exists():
                observed = verify_integrity(integrity_path, {"rank_part": destination}, signature=signature)
                if observed["metadata"] != metadata:
                    raise ResumeError(f"rank part slice drift: S{seed}/{destination.name}")
                continue
            frame = score_input.iloc[start:stop]
            profile_movies = np.stack(frame["profile_movie_ids"].map(lambda value: np.asarray(value, dtype=np.int64)))
            target_movies = np.stack(frame["target_movie_ids"].map(lambda value: np.asarray(value, dtype=np.int64)))
            profile_positions = position_lookup[profile_movies]
            target_positions = position_lookup[target_movies]
            rating_indices_batch = np.stack(frame["profile_rating_idx"].map(lambda value: np.asarray(value, dtype=np.int8)))
            if bool((profile_positions < 0).any()) or bool((target_positions < 0).any()):
                raise RuntimeError("score input movie outside universe")
            matched_similarity = np.stack([
                (matched[target_positions[index]] @ matched[profile_positions[index]].T).toarray().astype(np.float64)
                for index in range(len(frame))
            ])
            embedded_similarity = np.einsum(
                "btd,bpd->btp", normalized[target_positions], normalized[profile_positions],
            )
            bias_scores = biases[target_positions]
            users = frame["user_key"].astype(str).tolist()
            rows: list[dict[str, Any]] = []
            for cell in contract["fixed_reuse"]["cells"]:
                encoding, k = str(cell["encoding"]), int(cell["k"])
                weights = np.stack([
                    encoding_weights(encoding, RATING_VALUES[rating_indices_batch[index, :k]], g0_mid, tau=5.0)
                    for index in range(len(frame))
                ]).astype(np.float64)
                denominator = np.abs(weights).sum(axis=1)
                matched_scores = np.divide(
                    np.einsum("btk,bk->bt", matched_similarity[:, :, :k], weights), denominator[:, None],
                    out=np.zeros((len(frame), 20), dtype=np.float64), where=denominator[:, None] > 0,
                )
                embedded_scores = np.divide(
                    np.einsum("btk,bk->bt", embedded_similarity[:, :, :k], weights), denominator[:, None],
                    out=np.zeros((len(frame), 20), dtype=np.float64), where=denominator[:, None] > 0,
                )
                full_scores, fold_active = fold_in_batch(
                    biases, factors, profile_positions[:, :k], weights, target_positions,
                    steps=int(contract["target_fold_in"]["steps"]),
                    learning_rate=float(contract["target_fold_in"]["learning_rate"]),
                    regularization=float(contract["target_fold_in"]["regularization"]),
                )
                for index, user in enumerate(users):
                    movies = target_movies[index]
                    original_order, original_active = original_lookup[(user, encoding, k)]
                    head_orders: dict[str, tuple[np.ndarray, bool]] = {
                        "STRUCTURED_ORIGINAL": (original_order, original_active),
                        "STRUCTURED_MATCHED": (
                            _rank(contract, movies, matched_scores[index], seed=seed, head="STRUCTURED_MATCHED", encoding=encoding, k=k, user=user),
                            denominator[index] > 0 and _score_active(matched_scores[index]),
                        ),
                        "BIAS_ONLY": (
                            _rank(contract, movies, bias_scores[index], seed=seed, head="BIAS_ONLY", encoding=encoding, k=k, user=user),
                            _score_active(bias_scores[index]),
                        ),
                        "EMBEDDED_DIRECT": (
                            _rank(contract, movies, embedded_scores[index], seed=seed, head="EMBEDDED_DIRECT", encoding=encoding, k=k, user=user),
                            denominator[index] > 0 and _score_active(embedded_scores[index]),
                        ),
                        "LIGHTFM_FULL": (np.empty(0, dtype=np.int16), False),
                    }
                    full_is_active = bool(fold_active[index]) and _score_active(full_scores[index])
                    if full_is_active:
                        head_orders["LIGHTFM_FULL"] = (
                            _rank(contract, movies, full_scores[index], seed=seed, head="LIGHTFM_FULL", encoding=encoding, k=k, user=user),
                            True,
                        )
                    rrf_order, rrf_active = _rrf_order(
                        contract, movies,
                        [head_orders["STRUCTURED_ORIGINAL"][0] if head_orders["STRUCTURED_ORIGINAL"][1] else np.empty(0), head_orders["LIGHTFM_FULL"][0] if head_orders["LIGHTFM_FULL"][1] else np.empty(0)],
                        seed=seed, encoding=encoding, k=k, user=user,
                    )
                    head_orders["RRF_ORIGINAL_LIGHTFM"] = (rrf_order, rrf_active)
                    for head in SCORED_HEADS:
                        order, active = head_orders[head]
                        if bool(active) != bool(len(order)):
                            raise RuntimeError(f"active/order mismatch: {head}")
                        rows.append({
                            "user_key": user, "encoding": encoding, "k": k, "seed": seed, "head": head,
                            "head_active": bool(active), "ranked_target_indices": order.tolist() if active else [],
                        })
            rank_frame = pd.DataFrame(rows).sort_values(["user_key", "encoding", "k", "head"], kind="stable", ignore_index=True)
            if set(rank_frame.columns) != {"user_key", "encoding", "k", "seed", "head", "head_active", "ranked_target_indices"}:
                raise RuntimeError("rank schema drift")
            atomic_to_parquet(destination, rank_frame)
            write_integrity(integrity_path, {"rank_part": destination}, signature=signature, metadata=metadata)
            progress_update(contract, "SCORING", seed=seed, completed_users=stop, total_users=len(score_input))
        if sha256_file(result_path) != factor_shas[str(seed)]:
            raise RuntimeError(f"seed {seed} item representation mutated during scoring")
    expected_artifacts, expected_parts, expected_integrities = _expected_rank_artifacts(contract, len(score_input))
    actual_parts = set(output_path(contract, "rank_root").glob("S*/part-*.parquet"))
    actual_integrities = set(output_path(contract, "rank_root").glob("S*/part-*.integrity.json"))
    if actual_parts != expected_parts or actual_integrities != expected_integrities:
        raise ResumeError("rank part path set drift")
    for seed_raw in contract["lightfm"]["seeds"]:
        seed_root = output_path(contract, "rank_root") / f"S{int(seed_raw)}"
        seed_expected = {path for path in expected_parts | expected_integrities if path.parent == seed_root}
        if not exact_regular_children(seed_root, seed_expected):
            raise ResumeError(f"rank seed S{int(seed_raw)} contains an unknown or non-file child")
    rank_set_integrity = output_path(contract, "rank_set_integrity")
    if rank_set_integrity.exists():
        verify_rank_set(contract, signature=signature)
    else:
        write_integrity(rank_set_integrity, expected_artifacts, signature=signature, metadata={
            "seeds": contract["lightfm"]["seeds"], "parts": len(expected_parts), "users": len(score_input),
            "evaluation_labels_opened": False, "fit_result_sha_before_after": factor_shas,
        })
        verify_rank_set(contract, signature=signature)
    return {"status": "RANKS_COMPLETE", "parts": len(expected_parts), "users": len(score_input), "seeds": contract["lightfm"]["seeds"]}


def materialize_metrics(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    rank_integrity = verify_rank_set(contract, signature=signature)
    if rank_integrity.get("metadata", {}).get("evaluation_labels_opened") is not False:
        raise ResumeError("rank-set seal drift before labels")
    metrics_path, integrity_path = output_path(contract, "user_metrics"), output_path(contract, "user_metrics_integrity")
    if metrics_path.exists() or integrity_path.exists():
        integrity = verify_integrity(integrity_path, {"user_metrics": metrics_path}, signature=signature)
        return {"status": "REUSED_METRICS", **integrity["metadata"]}
    entries = contract["allowed_input_artifacts"]
    labels = pd.read_parquet(resolve_input(entries["rec_ev_023b_evaluation_labels"]))
    label_lookup = {str(row.user_key): np.asarray(row.target_q_eval, dtype=np.float64) for row in labels.itertuples(index=False)}
    rows: list[dict[str, Any]] = []
    for seed_raw in contract["lightfm"]["seeds"]:
        seed = int(seed_raw)
        for part in sorted((output_path(contract, "rank_root") / f"S{seed}").glob("part-*.parquet")):
            frame = pd.read_parquet(part)
            for row in frame.itertuples(index=False):
                q = label_lookup[str(row.user_key)]
                random_utility, random_loss = analytic_random_top2(q)
                active = bool(row.head_active)
                if active:
                    order = np.asarray(row.ranked_target_indices, dtype=np.int64)
                    if sorted(order.tolist()) != list(range(20)):
                        raise RuntimeError("active rank is not a target permutation")
                    utility, loss = pair1_metrics(q[order])
                else:
                    if len(row.ranked_target_indices):
                        raise RuntimeError("inactive rank stores an order")
                    utility, loss = random_utility, random_loss
                rows.append({
                    "user_key": str(row.user_key), "encoding": str(row.encoding), "k": int(row.k),
                    "seed": seed, "head": str(row.head), "top2_mean_q": utility,
                    "top2_worst_q_loss": loss, "head_active": active, "used_random_expectation": not active,
                })
                if str(row.head) == "STRUCTURED_ORIGINAL":
                    rows.append({
                        "user_key": str(row.user_key), "encoding": str(row.encoding), "k": int(row.k),
                        "seed": seed, "head": "RANDOM_EXPECTATION", "top2_mean_q": random_utility,
                        "top2_worst_q_loss": random_loss, "head_active": False, "used_random_expectation": True,
                    })
    metrics = pd.DataFrame(rows).sort_values(["seed", "user_key", "encoding", "k", "head"], kind="stable", ignore_index=True)
    expected_rows = len(labels) * len(contract["fixed_reuse"]["cells"]) * len(contract["heads"]) * len(contract["lightfm"]["seeds"])
    if len(metrics) != expected_rows or metrics.duplicated(["seed", "user_key", "encoding", "k", "head"]).any():
        raise RuntimeError("user metric completeness drift")
    old = pd.read_parquet(resolve_input(entries["rec_ev_023b_user_metrics"]))
    old = old.loc[old["head"].isin(["STRUCTURED", "RANDOM_EXPECTATION"]), ["user_key", "encoding", "k", "head", *PRIMARY_METRICS]].copy()
    old["head"] = old["head"].replace({"STRUCTURED": "STRUCTURED_ORIGINAL"})
    primary = metrics.loc[metrics["seed"].eq(17) & metrics["head"].isin(["STRUCTURED_ORIGINAL", "RANDOM_EXPECTATION"]), ["user_key", "encoding", "k", "head", *PRIMARY_METRICS]]
    merged = old.merge(primary, on=["user_key", "encoding", "k", "head"], suffixes=("_old", "_new"), validate="one_to_one")
    for metric in PRIMARY_METRICS:
        if not np.array_equal(merged[f"{metric}_old"].to_numpy(), merged[f"{metric}_new"].to_numpy()):
            raise RuntimeError(f"STRUCTURED_ORIGINAL metric differs from REC-EV-023B: {metric}")
    atomic_to_parquet(metrics_path, metrics)
    metadata = {"users": len(labels), "rows": len(metrics), "seeds": contract["lightfm"]["seeds"], "heads": list(contract["heads"]), "labels_opened_after_rank_set_seal": True}
    write_integrity(integrity_path, {"user_metrics": metrics_path}, signature=signature, metadata=metadata)
    progress_update(contract, "METRICS_COMPLETE", **metadata)
    return {"status": "METRICS_COMPLETE", **metadata}


def build_contrasts(metrics: pd.DataFrame, contract: Mapping[str, Any], seed: int) -> tuple[np.ndarray, list[dict[str, Any]], list[str]]:
    frame = metrics.loc[metrics["seed"].eq(int(seed))]
    users = sorted(frame["user_key"].unique().tolist())
    indexed = frame.set_index(["user_key", "encoding", "k", "head"]).sort_index()
    columns: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []
    for cell in contract["fixed_reuse"]["cells"]:
        encoding, k = str(cell["encoding"]), int(cell["k"])
        for left, right in contract["statistics"]["comparisons_per_cell"]:
            for metric in PRIMARY_METRICS:
                left_values = indexed.loc[(slice(None), encoding, k, left), metric].droplevel([1, 2, 3]).reindex(users).to_numpy(dtype=np.float64)
                right_values = indexed.loc[(slice(None), encoding, k, right), metric].droplevel([1, 2, 3]).reindex(users).to_numpy(dtype=np.float64)
                columns.append(left_values - right_values)
                metadata.append({"encoding": encoding, "k": k, "left": left, "right": right, "metric": metric})
    result = np.column_stack(columns)
    if result.shape != (9520, 156) or not np.isfinite(result).all():
        raise RuntimeError("REC-EV-023D contrast family drift")
    return result, metadata, users


def bootstrap(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    metrics_path = output_path(contract, "user_metrics")
    verify_integrity(output_path(contract, "user_metrics_integrity"), {"user_metrics": metrics_path}, signature=signature)
    destination, integrity_path = output_path(contract, "bootstrap_replicates"), output_path(contract, "bootstrap_integrity")
    if destination.exists() or integrity_path.exists():
        integrity = verify_integrity(integrity_path, {"bootstrap_replicates": destination}, signature=signature)
        return {"status": "REUSED_BOOTSTRAP", **integrity["metadata"]}
    metrics = pd.read_parquet(metrics_path)
    values, metadata, users = build_contrasts(metrics, contract, 17)
    membership_cache = np.load(resolve_input(contract["allowed_input_artifacts"]["rec_ev_023c_membership"]), allow_pickle=False)
    if membership_cache["user_keys"].astype(str).tolist() != users:
        raise RuntimeError("REC-EV-023C membership user alignment drift")
    item_ids = membership_cache["item_ids"].astype(np.int64)
    membership = sparse.csr_matrix((
        membership_cache["data"].astype(np.float64), membership_cache["indices"].astype(np.int32), membership_cache["indptr"].astype(np.int32),
    ), shape=(len(users), len(item_ids)))
    cutoffs = poisson_cutoffs(precision=80)
    arrays = {regime: np.empty((2000, 156), dtype=np.float64) for regime in REGIMES}
    protocol = "rec-ev-023c-crossed-sensitivity-v1"
    for attempt in range(2000):
        user_weights = np.fromiter((poisson_weight(protocol, attempt, "user", key, cutoffs)[0] for key in users), dtype=np.float64, count=len(users))
        item_weights = np.fromiter((poisson_weight(protocol, attempt, "item", str(int(movie)), cutoffs)[0] for movie in item_ids), dtype=np.float64, count=len(item_ids))
        membership_weight = np.asarray(membership @ item_weights).ravel()
        weights_by_regime = {"USER_ONLY": user_weights, "ITEM_ONLY": membership_weight, "TWO_WAY": user_weights * membership_weight}
        for regime, weights in weights_by_regime.items():
            denominator = float(weights.sum())
            estimate = (weights @ values) / denominator
            if denominator <= 0 or not np.isfinite(estimate).all():
                raise RuntimeError(f"invalid bootstrap attempt {attempt} {regime}")
            arrays[regime][attempt] = estimate
        if attempt % 100 == 0:
            progress_update(contract, "BOOTSTRAP", attempt=attempt)
    point = values.mean(axis=0)
    prior = np.load(resolve_input(contract["allowed_input_artifacts"]["rec_ev_023c_bootstrap_replicates"]), allow_pickle=False)
    if not np.array_equal(prior["valid_attempt_ids"], np.arange(2000, dtype=np.int32)):
        raise RuntimeError("REC-EV-023C valid bootstrap attempt IDs drift")
    for cell_index in range(6):
        old_slice = slice(cell_index * 12, cell_index * 12 + 2)
        new_slice = slice(cell_index * 26, cell_index * 26 + 2)
        if not np.array_equal(point[new_slice], prior["point"][old_slice]) or not np.array_equal(arrays["TWO_WAY"][:, new_slice], prior["two_way"][:, old_slice]):
            raise RuntimeError("STRUCTURED_ORIGINAL carry-forward point/replicate drift")
        if not np.array_equal(arrays["TWO_WAY"][:, new_slice].std(axis=0, ddof=1), prior["two_way"][:, old_slice].std(axis=0, ddof=1)):
            raise RuntimeError("STRUCTURED_ORIGINAL carry-forward SE drift")
    atomic_save_npz(destination, point=point, user_only=arrays["USER_ONLY"], item_only=arrays["ITEM_ONLY"], two_way=arrays["TWO_WAY"], valid_attempt_ids=np.arange(2000, dtype=np.int32))
    metadata_value = {"valid_replicates": 2000, "attempt_ids": [0, 1999], "contrasts": 156, "regimes": list(REGIMES), "contrast_metadata_sha256": hashlib.sha256(canonical_json_bytes(metadata)).hexdigest(), "carry_forward_point_replicate_se_exact": True}
    write_integrity(integrity_path, {"bootstrap_replicates": destination}, signature=signature, metadata=metadata_value)
    progress_update(contract, "BOOTSTRAP_COMPLETE", **metadata_value)
    return {"status": "BOOTSTRAP_COMPLETE", **metadata_value}


def _interval_rows(metadata: Sequence[Mapping[str, Any]], point: np.ndarray, values: Mapping[str, Any], regime: str) -> list[dict[str, Any]]:
    return [{
        **dict(meta), "regime": regime, "mean": float(point[index]), "se": float(values["se"][index]),
        "low": float(values["low"][index]), "high": float(values["high"][index]),
        "half_width": float(values["half_width"][index]), "estimable": bool(values["active"][index]),
    } for index, meta in enumerate(metadata)]


def point_margin_pass(utility: float, loss: float, *, utility_margin: float, loss_margin: float) -> bool:
    """Apply the stricter all-seed descriptive stability boundary."""
    return float(utility) >= float(utility_margin) and float(loss) < float(loss_margin)


def result_status(propositions: Sequence[Mapping[str, Any]]) -> str:
    if any(bool(row["lightfm_full_forward"]) or bool(row["rrf_forward"]) for row in propositions):
        return "END_TO_END_INCREMENTAL_SIGNAL"
    if any(bool(row["embedding_mechanism_signal"]) for row in propositions):
        return "EMBEDDING_MECHANISM_ONLY_SIGNAL"
    return "NO_INCREMENTAL_SIGNAL"


def seal_or_reuse_analysis(
    selection: Mapping[str, Any], result: Mapping[str, Any], *, selection_path: Path,
    result_path: Path, integrity_path: Path, signature: str,
) -> bool:
    state = artifact_state((selection_path, result_path, integrity_path))
    if state == "PARTIAL":
        raise ResumeError("REC-EV-023D final analysis artifacts are partial")
    if state == "ALL":
        verify_integrity(integrity_path, {"selection": selection_path, "result": result_path}, signature=signature)
        if read_json(selection_path) != selection or read_json(result_path) != result:
            raise ResumeError("REC-EV-023D sealed final analysis differs from recomputation")
        return True
    atomic_write_json(selection_path, selection)
    atomic_write_json(result_path, result)
    write_integrity(integrity_path, {"selection": selection_path, "result": result_path}, signature=signature, metadata={
        "status": str(selection["status"]), "forward": len(selection["forward_set"]), "all_five_metrics_sealed": True,
    })
    return False


def analyze(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    verify_integrity(output_path(contract, "prepared_integrity"), {
        "interactions": output_path(contract, "interactions"),
        "train_users": output_path(contract, "train_users"),
        "feature_mask": output_path(contract, "feature_mask"),
        "structured_matched": output_path(contract, "structured_matched"),
    }, signature=signature)
    for seed in contract["lightfm"]["seeds"]:
        _verify_fit(contract, int(seed), signature=signature)
    verify_rank_set(contract, signature=signature)
    metrics_path = output_path(contract, "user_metrics")
    metrics_integrity = verify_integrity(
        output_path(contract, "user_metrics_integrity"), {"user_metrics": metrics_path}, signature=signature,
    )
    destination = output_path(contract, "bootstrap_replicates")
    bootstrap_integrity = verify_integrity(output_path(contract, "bootstrap_integrity"), {"bootstrap_replicates": destination}, signature=signature)
    metrics = pd.read_parquet(metrics_path)
    values, metadata, _ = build_contrasts(metrics, contract, 17)
    cached = np.load(destination, allow_pickle=False)
    point = cached["point"].astype(np.float64)
    if not np.array_equal(point, values.mean(axis=0)) or hashlib.sha256(canonical_json_bytes(metadata)).hexdigest() != bootstrap_integrity["metadata"]["contrast_metadata_sha256"]:
        raise RuntimeError("sealed point or contrast metadata drift")
    all_rows: list[dict[str, Any]] = []
    critical_values: dict[str, float] = {}
    nonestimable: dict[str, int] = {}
    two_way_rows: list[dict[str, Any]] = []
    for regime, key in (("USER_ONLY", "user_only"), ("ITEM_ONLY", "item_only"), ("TWO_WAY", "two_way")):
        interval = regime_intervals(point, cached[key].astype(np.float64))
        rows = _interval_rows(metadata, point, interval, regime)
        all_rows.extend(rows)
        critical_values[regime] = float(interval["critical"])
        nonestimable[regime] = int((~interval["active"]).sum())
        if regime == "TWO_WAY":
            two_way_rows = rows
    lookup = {(row["encoding"], int(row["k"]), row["left"], row["right"], row["metric"]): row for row in two_way_rows}
    utility_margin, loss_margin = float(contract["decision"]["utility_margin"]), float(contract["decision"]["worst_loss_margin"])

    def q(encoding: str, k: int, left: str, right: str) -> bool:
        utility = lookup[(encoding, k, left, right, "top2_mean_q")]
        loss = lookup[(encoding, k, left, right, "top2_worst_q_loss")]
        return bool(utility["estimable"] and loss["estimable"] and utility["low"] >= utility_margin and loss["high"] <= loss_margin)

    seed_point: dict[int, dict[tuple[str, int, str, str], bool]] = {}
    seed_summaries: dict[str, Any] = {}
    seed_mean_vectors: list[np.ndarray] = []
    for seed in contract["lightfm"]["seeds"]:
        seed_values, seed_meta, _ = build_contrasts(metrics, contract, int(seed))
        means = seed_values.mean(axis=0)
        seed_mean_vectors.append(means)
        point_lookup = {(row["encoding"], int(row["k"]), row["left"], row["right"], row["metric"]): float(means[index]) for index, row in enumerate(seed_meta)}
        current: dict[tuple[str, int, str, str], bool] = {}
        for cell in contract["fixed_reuse"]["cells"]:
            encoding, k = str(cell["encoding"]), int(cell["k"])
            for left, right in contract["statistics"]["comparisons_per_cell"]:
                current[(encoding, k, left, right)] = point_margin_pass(
                    point_lookup[(encoding, k, left, right, "top2_mean_q")],
                    point_lookup[(encoding, k, left, right, "top2_worst_q_loss")],
                    utility_margin=utility_margin, loss_margin=loss_margin,
                )
        seed_point[int(seed)] = current
        seed_summaries[str(seed)] = {"contrast_means": [{**row, "mean": float(means[index])} for index, row in enumerate(seed_meta)]}
    prior_robust = {(row["encoding"], int(row["k"])) for row in read_json(resolve_input(contract["allowed_input_artifacts"]["rec_ev_023c_selection"]))["robust_forward"]}
    propositions: list[dict[str, Any]] = []
    forward: list[dict[str, Any]] = []
    for cell in contract["fixed_reuse"]["cells"]:
        encoding, k = str(cell["encoding"]), int(cell["k"])

        def stable(comparisons: Sequence[tuple[str, str]]) -> bool:
            return all(seed_point[int(seed)][(encoding, k, left, right)] for seed in contract["lightfm"]["seeds"] for left, right in comparisons)

        structured = (encoding, k) in prior_robust and q(encoding, k, "STRUCTURED_ORIGINAL", "RANDOM_EXPECTATION")
        mechanism_comparisons = (("EMBEDDED_DIRECT", "STRUCTURED_MATCHED"), ("EMBEDDED_DIRECT", "RANDOM_EXPECTATION"), ("LIGHTFM_FULL", "BIAS_ONLY"))
        mechanism = all(q(encoding, k, left, right) for left, right in mechanism_comparisons) and stable(mechanism_comparisons)
        full_comparisons = (("LIGHTFM_FULL", "RANDOM_EXPECTATION"), ("LIGHTFM_FULL", "STRUCTURED_ORIGINAL"), ("LIGHTFM_FULL", "STRUCTURED_MATCHED"), ("LIGHTFM_FULL", "BIAS_ONLY"))
        full = all(q(encoding, k, left, right) for left, right in full_comparisons) and stable(full_comparisons)
        rrf_comparisons = (("RRF_ORIGINAL_LIGHTFM", "RANDOM_EXPECTATION"), ("RRF_ORIGINAL_LIGHTFM", "LIGHTFM_FULL"), ("RRF_ORIGINAL_LIGHTFM", "STRUCTURED_ORIGINAL"))
        rrf = all(q(encoding, k, left, right) for left, right in rrf_comparisons) and stable(rrf_comparisons)
        propositions.append({"encoding": encoding, "k": k, "structured_original_robust": structured, "embedding_mechanism_signal": mechanism, "lightfm_full_forward": full, "rrf_forward": rrf})
        if structured:
            forward.append({"encoding": encoding, "k": k, "head": "STRUCTURED_ORIGINAL"})
        if full:
            forward.append({"encoding": encoding, "k": k, "head": "LIGHTFM_FULL"})
        if rrf:
            forward.append({"encoding": encoding, "k": k, "head": "RRF_ORIGINAL_LIGHTFM"})
    status = result_status(propositions)
    seed_matrix = np.vstack(seed_mean_vectors)
    seed_descriptive = [{
        **dict(meta),
        "mean": float(seed_matrix[:, index].mean()),
        "sd_ddof1": float(seed_matrix[:, index].std(ddof=1)),
        "min": float(seed_matrix[:, index].min()),
        "max": float(seed_matrix[:, index].max()),
        "range": float(seed_matrix[:, index].max() - seed_matrix[:, index].min()),
    } for index, meta in enumerate(metadata)]
    selection = {
        "schema_version": 1, "evidence_id": "REC-EV-023D", "status": status,
        "propositions": propositions, "forward_set": forward, "primary_seed": 17,
        "stability_seeds": contract["lightfm"]["seeds"], "champion": None,
        "locked_test_opened": False, "stage2_opened": False, "final_reserve_opened": False, "product_policy_updated": False,
    }
    result = {
        "schema_version": 1, "evidence_id": "REC-EV-023D", "status": status,
        "claim_boundary": contract["claim_boundary"], "prepared": read_json(output_path(contract, "prepared_integrity"))["metadata"],
        "fits": {str(seed): read_json(_fit_directory(contract, int(seed)) / "integrity.json")["metadata"] for seed in contract["lightfm"]["seeds"]},
        "bootstrap": bootstrap_integrity["metadata"], "critical_values": critical_values,
        "nonestimable_contrasts": nonestimable, "intervals": all_rows, "seed_point_summaries": seed_summaries,
        "five_seed_contrast_descriptive": seed_descriptive,
        "all_five_point_gate_input_user_metrics_sha256": metrics_integrity["artifacts"]["user_metrics"]["sha256"],
        "selection": selection, "locked_test_opened": False, "stage2_opened": False,
        "final_reserve_opened": False, "champion": None, "product_policy_updated": False,
    }
    selection_path = output_path(contract, "selection")
    result_path = output_path(contract, "result")
    analysis_integrity_path = output_path(contract, "analysis_integrity")
    if seal_or_reuse_analysis(
        selection, result, selection_path=selection_path, result_path=result_path,
        integrity_path=analysis_integrity_path, signature=signature,
    ):
        return selection
    progress_update(contract, "COMPLETE", status=status, forward=len(forward))
    return selection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--phase", choices=("lock", "prepare", "fit", "score", "metrics", "bootstrap", "analyze", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    contract_path = args.contract.resolve()
    if contract_path != DEFAULT_CONTRACT.resolve():
        raise RuntimeError("REC-EV-023D accepts only the committed default contract")
    contract = read_json(contract_path)
    validate(contract)
    if np.__version__ != contract["statistics"]["numpy_version"]:
        raise RuntimeError(f"NumPy version drift: {np.__version__}")
    if args.phase == "lock":
        value = create_or_verify_lock(contract, contract_path, resume=args.resume)
    else:
        if not args.resume:
            raise ResumeError("REC-EV-023D real phases require --resume")
        create_or_verify_lock(contract, contract_path, resume=True)
        phases = ("prepare", "fit", "score", "metrics", "bootstrap", "analyze") if args.phase == "run" else (args.phase,)
        value: Any = None
        for phase in phases:
            if phase == "prepare": value = prepare(contract)
            elif phase == "fit": value = fit(contract)
            elif phase == "score": value = score(contract)
            elif phase == "metrics": value = materialize_metrics(contract)
            elif phase == "bootstrap": value = bootstrap(contract)
            elif phase == "analyze": value = analyze(contract)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
