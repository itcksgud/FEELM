#!/usr/bin/env python3
"""Run the pre-label REC-EV-023D-A1 fold-in numerical amendment."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

try:
    import run_rec_ev_023d_lightfm_attribution as base
    from rec_ev_022a_core import RATING_VALUES, encoding_weights
    from validate_rec_ev_023d_a1_contract import validate
    from validate_rec_ev_023d_contract import validate as validate_base
except ModuleNotFoundError:
    from scripts import run_rec_ev_023d_lightfm_attribution as base
    from scripts.rec_ev_022a_core import RATING_VALUES, encoding_weights
    from scripts.validate_rec_ev_023d_a1_contract import validate
    from scripts.validate_rec_ev_023d_contract import validate as validate_base


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023d-a1-feature-only-lightfm-attribution.json"
EVIDENCE_ID = "REC-EV-023D-A1"
PREDECESSOR_EVIDENCE_ID = "REC-EV-023D"
PREDECESSOR_OUTPUT_NAMES = {
    "interactions": "predecessor_interactions",
    "train_users": "predecessor_train_users",
    "feature_mask": "predecessor_feature_mask",
    "structured_matched": "predecessor_structured_matched",
    "prepared_integrity": "predecessor_prepared_integrity",
}

_ORIGINAL_VERIFY_INTEGRITY = base.verify_integrity
_ORIGINAL_WRITE_INTEGRITY = base.write_integrity
_ORIGINAL_VERIFY_RANK_SET = base.verify_rank_set
_ORIGINAL_SEAL_ANALYSIS = base.seal_or_reuse_analysis


class ResumeError(RuntimeError):
    pass


def _entry_path(entry: Mapping[str, Any]) -> Path:
    path = Path(str(entry["path"]))
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def load_effective_contract(contract_path: Path = DEFAULT_CONTRACT) -> tuple[dict[str, Any], dict[str, Any]]:
    overlay = json.loads(contract_path.read_text(encoding="utf-8"))
    validate(overlay)
    base_path = _entry_path(overlay["base_contract"])
    if base.sha256_file(base_path) != overlay["base_contract"]["sha256"] or base_path.stat().st_size != overlay["base_contract"]["bytes"]:
        raise ResumeError("REC-EV-023D base contract envelope drift")
    inherited = json.loads(base_path.read_text(encoding="utf-8"))
    validate_base(inherited)
    contract = copy.deepcopy(inherited)
    contract["contract_id"] = overlay["contract_id"]
    contract["evidence_id"] = overlay["evidence_id"]
    contract["amendment_status"] = overlay["status"]
    contract["output_root"] = overlay["output_root"]
    contract["predecessor"] = copy.deepcopy(overlay["predecessor"])
    contract["target_fold_in"] = copy.deepcopy(overlay["target_fold_in"])
    contract["target_fold_in"]["learning_rate"] = float(overlay["target_fold_in"]["base_learning_rate"])
    contract["implementation_artifacts"] = list(overlay["implementation_artifacts"])
    contract["outputs"] = copy.deepcopy(overlay["outputs"])
    contract["resume"] = copy.deepcopy(overlay["resume"])
    contract["invariants"] = copy.deepcopy(overlay["invariants"])
    contract["allowed_input_artifacts"].update(copy.deepcopy(overlay["predecessor"]["artifacts"]))
    return contract, overlay


def output_path(contract: Mapping[str, Any], name: str) -> Path:
    if name in PREDECESSOR_OUTPUT_NAMES:
        entry = contract["predecessor"]["artifacts"][PREDECESSOR_OUTPUT_NAMES[name]]
        return _entry_path(entry)
    return ROOT / str(contract["output_root"]) / str(contract["outputs"][name])


def predecessor_fit_directory(contract: Mapping[str, Any], seed: int) -> Path:
    entry = contract["predecessor"]["artifacts"][f"predecessor_s{int(seed)}_result"]
    return _entry_path(entry).parent


def predecessor_signature(contract: Mapping[str, Any]) -> str:
    lock_entry = contract["predecessor"]["artifacts"]["predecessor_protocol_lock"]
    lock = base.read_json(_entry_path(lock_entry))
    payload = {key: lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )}
    return hashlib.sha256(base.canonical_json_bytes(payload)).hexdigest()


def _verify_entry(entry: Mapping[str, Any], label: str) -> Path:
    path = _entry_path(entry)
    if not path.is_file() or path.stat().st_size != int(entry["bytes"]) or base.sha256_file(path) != entry["sha256"]:
        raise ResumeError(f"predecessor artifact drift: {label}")
    return path


def verify_predecessor_fit(contract: Mapping[str, Any], seed: int, *, signature: str | None = None) -> dict[str, Any]:
    del signature
    seed = int(seed)
    artifacts = contract["predecessor"]["artifacts"]
    directory = predecessor_fit_directory(contract, seed)
    expected = {directory / "config.json", directory / "result.npz", directory / "integrity.json"}
    if not base.exact_regular_children(directory, expected):
        raise ResumeError(f"predecessor fit child set drift: S{seed}")
    config_path = _verify_entry(artifacts[f"predecessor_s{seed}_config"], f"S{seed} config")
    result_path = _verify_entry(artifacts[f"predecessor_s{seed}_result"], f"S{seed} result")
    integrity_path = _verify_entry(artifacts[f"predecessor_s{seed}_integrity"], f"S{seed} integrity")
    old_signature = predecessor_signature(contract)
    integrity = _ORIGINAL_VERIFY_INTEGRITY(
        integrity_path, {"config": config_path, "result": result_path}, signature=old_signature,
    )
    model = contract["lightfm"]
    expected_config = {
        "loss": model["loss"], "dimension": model["dimension"], "learning_schedule": model["learning_schedule"],
        "learning_rate": model["learning_rate"], "item_alpha": model["item_alpha"], "user_alpha": model["user_alpha"],
        "epochs": model["epochs"], "threads": model["threads"], "seed": seed,
        "interaction_sha256": artifacts["predecessor_interactions"]["sha256"],
        "item_feature_sha256": artifacts["predecessor_structured_matched"]["sha256"],
        "item_identity_features": False,
    }
    if base.read_json(config_path) != expected_config:
        raise ResumeError(f"predecessor fit config drift: S{seed}")
    with np.load(result_path, allow_pickle=False) as result:
        if set(result.files) != {"item_biases", "item_factors"}:
            raise ResumeError(f"predecessor result key drift: S{seed}")
        biases, factors = result["item_biases"], result["item_factors"]
        if biases.dtype != np.float32 or factors.dtype != np.float32:
            raise ResumeError(f"predecessor result dtype drift: S{seed}")
        if biases.shape != (41439,) or factors.shape != (41439, 128):
            raise ResumeError(f"predecessor result shape drift: S{seed}")
        if not np.isfinite(biases).all() or not np.isfinite(factors).all():
            raise ResumeError(f"predecessor result nonfinite: S{seed}")
        if bool((np.linalg.norm(factors.astype(np.float64), axis=1) <= 0).any()):
            raise ResumeError(f"predecessor result zero factor: S{seed}")
    return integrity


def verify_predecessor_layout(cache_root: Path, seeds: Sequence[int]) -> None:
    fit_root = cache_root / "lightfm-seeds"
    expected_cache_children = {
        cache_root / "feature-mask.npy",
        fit_root,
        cache_root / "prepared.integrity.json",
        cache_root / "structured-matched.npz",
        cache_root / "train-interactions.npz",
        cache_root / "train-user-keys.npy",
    }
    if not cache_root.is_dir() or set(cache_root.iterdir()) != expected_cache_children:
        raise ResumeError("predecessor cache child set drift")
    if any(not path.is_file() for path in expected_cache_children if path != fit_root) or not fit_root.is_dir():
        raise ResumeError("predecessor cache child type drift")
    expected_seed_names = {f"S{int(seed)}" for seed in seeds}
    if {path.name for path in fit_root.iterdir()} != expected_seed_names or any(not path.is_dir() for path in fit_root.iterdir()):
        raise ResumeError("predecessor fit root seed set drift")
    for seed in seeds:
        directory = fit_root / f"S{int(seed)}"
        expected = {directory / "config.json", directory / "result.npz", directory / "integrity.json"}
        if not base.exact_regular_children(directory, expected):
            raise ResumeError(f"predecessor fit child set drift: S{int(seed)}")


def verify_predecessor(contract: Mapping[str, Any]) -> dict[str, Any]:
    predecessor = contract["predecessor"]
    if predecessor_signature(contract) != predecessor["run_signature"]:
        raise ResumeError("predecessor run signature drift")
    for label, entry in predecessor["artifacts"].items():
        _verify_entry(entry, label)
    old_lock = base.read_json(_entry_path(predecessor["artifacts"]["predecessor_protocol_lock"]))
    old_manifest = base.read_json(_entry_path(predecessor["artifacts"]["predecessor_source_manifest"]))
    if hashlib.sha256(base.canonical_json_bytes(old_manifest.get("sources"))).hexdigest() != old_lock.get("source_artifacts_sha256"):
        raise ResumeError("predecessor source-family digest drift")
    if hashlib.sha256(base.canonical_json_bytes(old_manifest.get("implementation_artifacts"))).hexdigest() != old_lock.get("implementation_artifacts_sha256"):
        raise ResumeError("predecessor implementation-family digest drift")
    for implementation in old_manifest.get("implementation_artifacts", []):
        _verify_entry(implementation, f"predecessor implementation {implementation.get('path')}")
    rank_root = ROOT / str(predecessor["rank_root"])
    if rank_root.exists():
        raise ResumeError("predecessor rank root must remain absent")
    progress = base.read_json(_entry_path(predecessor["artifacts"]["predecessor_progress"]))
    if progress.get("phase") != predecessor["progress_phase"] or progress.get("completed_seeds") != predecessor["completed_seeds"]:
        raise ResumeError("predecessor progress state drift")
    fit_root = predecessor_fit_directory(contract, 17).parent
    cache_root = fit_root.parent
    verify_predecessor_layout(cache_root, predecessor["completed_seeds"])
    for seed in predecessor["completed_seeds"]:
        verify_predecessor_fit(contract, int(seed))
    prepared_path = _entry_path(predecessor["artifacts"]["predecessor_prepared_integrity"])
    prepared = _ORIGINAL_VERIFY_INTEGRITY(
        prepared_path,
        {
            "interactions": _entry_path(predecessor["artifacts"]["predecessor_interactions"]),
            "train_users": _entry_path(predecessor["artifacts"]["predecessor_train_users"]),
            "feature_mask": _entry_path(predecessor["artifacts"]["predecessor_feature_mask"]),
            "structured_matched": _entry_path(predecessor["artifacts"]["predecessor_structured_matched"]),
        },
        signature=predecessor["run_signature"],
    )
    return {"status": "PREDECESSOR_FITS_VERIFIED", "seeds": predecessor["completed_seeds"], "prepared": prepared["metadata"]}


def locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    value = base.locked_spec(contract)
    value.update({
        "evidence_id": contract["evidence_id"],
        "amendment_status": contract["amendment_status"],
        "predecessor": contract["predecessor"],
    })
    return value


def _manifest(sources: Sequence[Mapping[str, Any]], implementations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_id": EVIDENCE_ID,
        "sources": list(sources),
        "implementation_artifacts": list(implementations),
        "predecessor_evidence_id": PREDECESSOR_EVIDENCE_ID,
        "predecessor_evaluation_labels_opened": False,
        "rank_written_at_lock": False,
        "evaluation_labels_opened_at_lock": False,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
    }


def create_or_verify_lock(
    contract: Mapping[str, Any], overlay: Mapping[str, Any], contract_path: Path, *, resume: bool,
) -> dict[str, Any]:
    verify_predecessor(contract)
    sources = base.verify_sources(contract)
    implementations = base.verify_implementation(contract)
    base.verify_upstream(contract)
    manifest = _manifest(sources, implementations)
    hashes = {
        "contract_sha256": base.sha256_contract(contract_path),
        "source_artifacts_sha256": hashlib.sha256(base.canonical_json_bytes(sources)).hexdigest(),
        "implementation_artifacts_sha256": hashlib.sha256(base.canonical_json_bytes(implementations)).hexdigest(),
        "locked_spec_sha256": hashlib.sha256(base.canonical_json_bytes(locked_spec(contract))).hexdigest(),
    }
    manifest_path = output_path(contract, "source_manifest")
    lock_path = output_path(contract, "protocol_lock")
    lock_state = base.artifact_state((manifest_path, lock_path))
    if lock_state == "PARTIAL":
        raise ResumeError("partial REC-EV-023D-A1 lock cannot be overwritten or promoted")
    if lock_state == "NONE":
        output_root = ROOT / str(contract["output_root"])
        if output_root.exists() and any(output_root.iterdir()):
            raise ResumeError("REC-EV-023D-A1 output exists before protocol lock")
        if resume:
            raise ResumeError("create REC-EV-023D-A1 lock without --resume first")
    elif not resume:
        raise ResumeError("existing REC-EV-023D-A1 lock requires --resume")
    expected_lock = {
        "schema_version": 1,
        "evidence_id": EVIDENCE_ID,
        "status": overlay["status"],
        **hashes,
        "source_manifest_sha256": None,
        "predecessor_run_signature": contract["predecessor"]["run_signature"],
        "predecessor_rank_root_absent": True,
        "evaluation_labels_opened_at_lock": False,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "champion": None,
        "product_policy_updated": False,
    }
    if lock_state == "ALL":
        if not manifest_path.is_file() or base.read_json(manifest_path) != manifest:
            raise ResumeError("REC-EV-023D-A1 source manifest drift")
        expected_lock["source_manifest_sha256"] = base.sha256_file(manifest_path)
        if base.read_json(lock_path) != expected_lock:
            raise ResumeError("REC-EV-023D-A1 protocol lock drift")
        return expected_lock
    base.atomic_write_json(manifest_path, manifest)
    expected_lock["source_manifest_sha256"] = base.sha256_file(manifest_path)
    base.atomic_write_json(lock_path, expected_lock)
    return expected_lock


def run_signature(contract: Mapping[str, Any]) -> str:
    lock = base.read_json(output_path(contract, "protocol_lock"))
    payload = {key: lock[key] for key in (
        "contract_sha256", "source_artifacts_sha256", "implementation_artifacts_sha256", "locked_spec_sha256",
    )}
    return hashlib.sha256(base.canonical_json_bytes(payload)).hexdigest()


def progress_update(contract: Mapping[str, Any], phase: str, **extra: Any) -> None:
    path = output_path(contract, "progress")
    value = base.read_json(path) if path.is_file() else {"schema_version": 1, "evidence_id": EVIDENCE_ID}
    value.update({"phase": phase, **extra})
    base.atomic_write_json(path, value)


def _fold_components(
    factors: np.ndarray, profile_positions: np.ndarray, weights: np.ndarray, *,
    regularization: float, base_learning_rate: float, safety_factor: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    profile = factors[profile_positions].astype(np.float64)
    w = np.asarray(weights, dtype=np.float64)
    nonzero = np.abs(w) > 0
    counts = nonzero.sum(axis=1)
    active = counts > 0
    confidence = np.zeros_like(w)
    mean_abs = np.divide(np.abs(w).sum(axis=1), counts, out=np.zeros(len(w)), where=active)
    confidence[active] = np.divide(
        np.abs(w[active]), mean_abs[active, None], out=np.zeros_like(w[active]), where=mean_abs[active, None] > 0,
    )
    labels = np.sign(w)
    total_confidence = confidence.sum(axis=1)
    norm_squared = np.einsum("bkd,bkd->bk", profile, profile)
    l_bound = float(regularization) + np.divide(
        np.sum(confidence * norm_squared, axis=1),
        4.0 * total_confidence,
        out=np.zeros(len(w), dtype=np.float64),
        where=total_confidence > 0,
    )
    eta = np.zeros(len(w), dtype=np.float64)
    eta[active] = np.minimum(float(base_learning_rate), float(safety_factor) / l_bound[active])
    tolerance = 32.0 * np.finfo(np.float64).eps
    if (
        not np.isfinite(l_bound[active]).all() or bool((l_bound[active] <= 0).any())
        or not np.isfinite(eta).all() or bool((eta < 0).any())
        or bool((eta > float(base_learning_rate) + tolerance).any())
        or bool((eta[active] * l_bound[active] > float(safety_factor) + tolerance).any())
    ):
        raise RuntimeError("REC-EV-023D-A1 fold-in schedule guard failed")
    return profile, confidence, labels, total_confidence, l_bound, eta


def learning_rate_schedule(
    factors: np.ndarray, profile_positions: np.ndarray, weights: np.ndarray, *,
    regularization: float = 1e-6, base_learning_rate: float = 0.05, safety_factor: float = 0.9,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    _, _, _, total_confidence, l_bound, eta = _fold_components(
        factors, profile_positions, weights,
        regularization=regularization, base_learning_rate=base_learning_rate, safety_factor=safety_factor,
    )
    return l_bound, eta, total_confidence > 0


def fold_in_batch(
    biases: np.ndarray, factors: np.ndarray, profile_positions: np.ndarray, weights: np.ndarray,
    target_positions: np.ndarray, *, steps: int = 80, learning_rate: float = 0.05,
    regularization: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    profile, confidence, labels, total_confidence, _, eta = _fold_components(
        factors, profile_positions, weights,
        regularization=regularization, base_learning_rate=learning_rate, safety_factor=0.9,
    )
    active = total_confidence > 0
    profile_bias = biases[profile_positions].astype(np.float64)
    user = np.zeros((len(weights), factors.shape[1]), dtype=np.float64)
    for _ in range(int(steps)):
        z = profile_bias + np.einsum("bkd,bd->bk", profile, user)
        sigmoid = base._stable_sigmoid(-labels * z)
        coefficients = confidence * labels * sigmoid
        gradient = -np.einsum("bk,bkd->bd", coefficients, profile)
        gradient = np.divide(
            gradient, total_confidence[:, None], out=np.zeros_like(gradient),
            where=total_confidence[:, None] > 0,
        )
        gradient += float(regularization) * user
        if not np.isfinite(gradient).all():
            raise RuntimeError("REC-EV-023D-A1 fold-in gradient nonfinite")
        user -= eta[:, None] * gradient
    scores = biases[target_positions].astype(np.float64) + np.einsum(
        "btd,bd->bt", factors[target_positions].astype(np.float64), user,
    )
    if not np.isfinite(user).all() or not np.isfinite(scores[active]).all():
        raise RuntimeError("REC-EV-023D-A1 fold-in vector or score nonfinite")
    return scores, active


def _schedule_arrays(contract: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    entries = contract["allowed_input_artifacts"]
    universe = np.load(base.resolve_input(entries["rec_ev_023b_universe"]), allow_pickle=False)
    item_ids = universe["item_ids"].astype(np.int64)
    position_lookup = base._movie_position_lookup(item_ids)
    score_input = pd.read_parquet(base.resolve_input(entries["rec_ev_023b_score_input"])).sort_values(
        "user_key", kind="stable", ignore_index=True,
    )
    prior = np.load(base.resolve_input(entries["rec_ev_023b_train_prior"]), allow_pickle=False)
    g0_mid = prior["g0_mid"].astype(np.float64)
    profile_movies = np.stack(score_input["profile_movie_ids"].map(lambda value: np.asarray(value, dtype=np.int64)))
    profile_positions = position_lookup[profile_movies]
    rating_indices_batch = np.stack(score_input["profile_rating_idx"].map(lambda value: np.asarray(value, dtype=np.int8)))
    if bool((profile_positions < 0).any()):
        raise RuntimeError("schedule profile movie outside universe")
    shape = (len(contract["lightfm"]["seeds"]), len(contract["fixed_reuse"]["cells"]), len(score_input))
    l_bounds = np.empty(shape, dtype=np.float64)
    learning_rates = np.empty(shape, dtype=np.float64)
    active = np.empty(shape, dtype=bool)
    fold = contract["target_fold_in"]
    factor_sha_before_after: dict[str, str] = {}
    for seed_index, seed_raw in enumerate(contract["lightfm"]["seeds"]):
        seed = int(seed_raw)
        result_path = predecessor_fit_directory(contract, seed) / "result.npz"
        before = base.sha256_file(result_path)
        with np.load(result_path, allow_pickle=False) as fitted:
            factors = fitted["item_factors"].astype(np.float64)
        for cell_index, cell in enumerate(contract["fixed_reuse"]["cells"]):
            encoding, k = str(cell["encoding"]), int(cell["k"])
            weights = np.stack([
                encoding_weights(encoding, RATING_VALUES[rating_indices_batch[index, :k]], g0_mid, tau=5.0)
                for index in range(len(score_input))
            ]).astype(np.float64)
            l_bound, eta, current_active = learning_rate_schedule(
                factors, profile_positions[:, :k], weights,
                regularization=float(fold["regularization"]),
                base_learning_rate=float(fold["base_learning_rate"]),
                safety_factor=float(fold["safety_factor"]),
            )
            l_bounds[seed_index, cell_index] = l_bound
            learning_rates[seed_index, cell_index] = eta
            active[seed_index, cell_index] = current_active
        after = base.sha256_file(result_path)
        if before != after:
            raise RuntimeError(f"predecessor item factors mutated during schedule: S{seed}")
        factor_sha_before_after[str(seed)] = before
    cells = [{"encoding": str(cell["encoding"]), "k": int(cell["k"])} for cell in contract["fixed_reuse"]["cells"]]
    metadata = {
        "seeds": [int(seed) for seed in contract["lightfm"]["seeds"]],
        "cells": cells,
        "users": len(score_input),
        "shape": list(shape),
        "user_keys_sha256": hashlib.sha256(base.canonical_json_bytes(score_input["user_key"].astype(str).tolist())).hexdigest(),
        "cells_sha256": hashlib.sha256(base.canonical_json_bytes(cells)).hexdigest(),
        "factor_sha_before_after": factor_sha_before_after,
        "base_learning_rate": float(fold["base_learning_rate"]),
        "safety_factor": float(fold["safety_factor"]),
        "regularization": float(fold["regularization"]),
        "step_size_forbidden_inputs_opened": False,
        "evaluation_labels_opened": False,
    }
    return {"l_bound": l_bounds, "eta": learning_rates, "active": active}, metadata


def verify_schedule(contract: Mapping[str, Any], *, recompute: bool = True) -> dict[str, Any]:
    signature = run_signature(contract)
    schedule_path = output_path(contract, "foldin_schedule")
    integrity_path = output_path(contract, "foldin_schedule_integrity")
    integrity = _ORIGINAL_VERIFY_INTEGRITY(
        integrity_path, {"foldin_schedule": schedule_path}, signature=signature,
    )
    with np.load(schedule_path, allow_pickle=False) as cached:
        if set(cached.files) != {"l_bound", "eta", "active"}:
            raise ResumeError("fold-in schedule key drift")
        stored = {name: cached[name] for name in cached.files}
    expected_shape = (5, 6, 9520)
    if stored["l_bound"].dtype != np.float64 or stored["eta"].dtype != np.float64 or stored["active"].dtype != bool:
        raise ResumeError("fold-in schedule dtype drift")
    if any(value.shape != expected_shape for value in stored.values()):
        raise ResumeError("fold-in schedule shape drift")
    tolerance = 32.0 * np.finfo(np.float64).eps
    active = stored["active"]
    if (
        not np.isfinite(stored["l_bound"][active]).all()
        or not np.isfinite(stored["eta"]).all()
        or bool((stored["eta"] < 0).any())
        or bool((stored["eta"] > 0.05 + tolerance).any())
        or bool((stored["eta"][active] * stored["l_bound"][active] > 0.9 + tolerance).any())
    ):
        raise ResumeError("fold-in schedule numerical invariant drift")
    if recompute:
        expected, metadata = _schedule_arrays(contract)
        if integrity["metadata"] != metadata:
            raise ResumeError("fold-in schedule metadata drift")
        for name in ("l_bound", "eta", "active"):
            if not np.array_equal(stored[name], expected[name]):
                raise ResumeError(f"fold-in schedule recomputation drift: {name}")
    return integrity


def _downstream_paths(contract: Mapping[str, Any]) -> tuple[Path, ...]:
    return tuple(output_path(contract, name) for name in (
        "rank_root", "rank_set_integrity", "user_metrics", "user_metrics_integrity",
        "bootstrap_replicates", "bootstrap_integrity", "selection", "result", "analysis_integrity",
    ))


def create_or_verify_schedule(contract: Mapping[str, Any], *, resume: bool) -> dict[str, Any]:
    schedule_path = output_path(contract, "foldin_schedule")
    integrity_path = output_path(contract, "foldin_schedule_integrity")
    state = base.artifact_state((schedule_path, integrity_path))
    if state == "PARTIAL":
        raise ResumeError("partial fold-in schedule cannot be promoted")
    if state == "ALL":
        if not resume:
            raise ResumeError("existing fold-in schedule requires --resume")
        integrity = verify_schedule(contract, recompute=True)
        return {"status": "REUSED_FOLDIN_SCHEDULE", **integrity["metadata"]}
    if resume:
        if any(path.exists() for path in _downstream_paths(contract)):
            raise ResumeError("cannot create fold-in schedule after rank or downstream output exists")
        arrays, metadata = _schedule_arrays(contract)
        base.atomic_save_npz(schedule_path, **arrays)
        base.write_integrity(
            integrity_path, {"foldin_schedule": schedule_path}, signature=run_signature(contract), metadata=metadata,
        )
        verify_schedule(contract, recompute=True)
        progress_update(contract, "FOLDIN_SCHEDULE_COMPLETE", **metadata)
        return {"status": "FOLDIN_SCHEDULE_COMPLETE", **metadata}
    raise ResumeError("REC-EV-023D-A1 schedule requires --resume")


def verify_rank_set_with_schedule(contract: Mapping[str, Any], *, signature: str) -> dict[str, Any]:
    schedule_integrity = verify_schedule(contract, recompute=True)
    rank_integrity = _ORIGINAL_VERIFY_RANK_SET(contract, signature=signature)
    metadata = rank_integrity.get("metadata", {})
    expected_schedule = {
        "foldin_schedule_sha256": schedule_integrity["artifacts"]["foldin_schedule"]["sha256"],
        "foldin_schedule_integrity_sha256": base.sha256_file(output_path(contract, "foldin_schedule_integrity")),
        "foldin_schedule_run_signature": run_signature(contract),
    }
    for key, value in expected_schedule.items():
        if metadata.get(key) != value:
            raise ResumeError(f"rank-set fold-in schedule provenance drift: {key}")
    if metadata.get("evaluation_labels_opened") is not False:
        raise ResumeError("rank-set label firewall drift")
    return rank_integrity


def rank_preflight(contract: Mapping[str, Any]) -> dict[str, Any]:
    signature = run_signature(contract)
    score_input = pd.read_parquet(
        base.resolve_input(contract["allowed_input_artifacts"]["rec_ev_023b_score_input"])
    ).sort_values("user_key", kind="stable", ignore_index=True)
    expected, expected_parts, expected_integrities = base._expected_rank_artifacts(contract, len(score_input))
    del expected
    rank_root = output_path(contract, "rank_root")
    rank_set_integrity = output_path(contract, "rank_set_integrity")
    downstream_after_rank = tuple(output_path(contract, name) for name in (
        "user_metrics", "user_metrics_integrity", "bootstrap_replicates", "bootstrap_integrity",
        "selection", "result", "analysis_integrity",
    ))
    if any(path.exists() for path in downstream_after_rank) and not rank_set_integrity.is_file():
        raise ResumeError("downstream output exists before complete rank-set seal")
    if not rank_root.exists():
        if rank_set_integrity.exists():
            raise ResumeError("rank-set integrity exists without rank root")
        return {"status": "RANK_PREFLIGHT_EMPTY", "complete_parts": 0}
    if not rank_root.is_dir():
        raise ResumeError("rank root is not a directory")
    expected_seed_names = {f"S{int(seed)}" for seed in contract["lightfm"]["seeds"]}
    actual_seed_entries = list(rank_root.iterdir())
    if any(not path.is_dir() for path in actual_seed_entries):
        raise ResumeError("rank root contains a non-directory child")
    if not {path.name for path in actual_seed_entries} <= expected_seed_names:
        raise ResumeError("rank root contains an unknown seed directory")
    completed_parts = 0
    for seed_raw in contract["lightfm"]["seeds"]:
        seed = int(seed_raw)
        seed_root = rank_root / f"S{seed}"
        if not seed_root.exists():
            continue
        allowed = {path for path in expected_parts | expected_integrities if path.parent == seed_root}
        actual = set(seed_root.iterdir())
        if any(not path.is_file() for path in actual) or not actual <= allowed:
            raise ResumeError(f"rank seed S{seed} contains an unknown or non-file child")
        for start in range(0, len(score_input), 200):
            stop = min(len(score_input), start + 200)
            part = base._part_path(seed_root, start, stop)
            integrity_path = part.with_suffix(".integrity.json")
            state = base.artifact_state((part, integrity_path))
            if state == "PARTIAL":
                raise ResumeError(f"rank part pair is partial before scoring: S{seed}/{part.name}")
            if state == "ALL":
                expected_keys = score_input.iloc[start:stop]["user_key"].astype(str).tolist()
                expected_metadata = {
                    "seed": seed,
                    "start": start,
                    "stop": stop,
                    "users": expected_keys,
                    "rows": (stop - start) * len(contract["fixed_reuse"]["cells"]) * len(base.SCORED_HEADS),
                }
                observed = _ORIGINAL_VERIFY_INTEGRITY(
                    integrity_path, {"rank_part": part}, signature=signature,
                )
                if observed.get("metadata") != expected_metadata:
                    raise ResumeError(f"rank part metadata drift before scoring: S{seed}/{part.name}")
                completed_parts += 1
    actual_parts = set(rank_root.glob("S*/part-*.parquet"))
    actual_integrities = set(rank_root.glob("S*/part-*.integrity.json"))
    if rank_set_integrity.exists():
        if actual_parts != expected_parts or actual_integrities != expected_integrities:
            raise ResumeError("rank-set integrity exists with an incomplete rank set")
        verify_rank_set_with_schedule(contract, signature=signature)
    return {"status": "RANK_PREFLIGHT_PASS", "complete_parts": completed_parts}


def amend_analysis_payload(
    contract: Mapping[str, Any], selection: Mapping[str, Any], result: Mapping[str, Any],
    *, schedule_integrity: Mapping[str, Any], rank_integrity: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    amended_selection = copy.deepcopy(dict(selection))
    amended_selection["evidence_id"] = EVIDENCE_ID
    amended_selection["numerical_amendment"] = {
        "predecessor": PREDECESSOR_EVIDENCE_ID,
        "failure_phase": contract["predecessor"]["failure_phase"],
        "fold_in": contract["target_fold_in"]["name"],
    }
    amended_result = copy.deepcopy(dict(result))
    amended_result["evidence_id"] = EVIDENCE_ID
    amended_result["predecessor"] = {
        "evidence_id": PREDECESSOR_EVIDENCE_ID,
        "run_signature": contract["predecessor"]["run_signature"],
        "evaluation_labels_opened": False,
        "rank_root_absent_before_amendment": True,
    }
    amended_result["numerical_amendment"] = copy.deepcopy(amended_selection["numerical_amendment"])
    amended_result["selection"] = amended_selection
    amended_result["foldin_schedule"] = {
        "artifact": schedule_integrity["artifacts"]["foldin_schedule"],
        "integrity_sha256": base.sha256_file(output_path(contract, "foldin_schedule_integrity")),
        "metadata": schedule_integrity["metadata"],
        "rank_set_integrity_sha256": base.sha256_file(output_path(contract, "rank_set_integrity")),
        "rank_set_schedule_sha256": rank_integrity["metadata"]["foldin_schedule_sha256"],
    }
    return amended_selection, amended_result


def seal_amended_analysis(
    contract: Mapping[str, Any], selection: Mapping[str, Any], result: Mapping[str, Any], *,
    schedule_integrity: Mapping[str, Any], rank_integrity: Mapping[str, Any], selection_path: Path,
    result_path: Path, integrity_path: Path, signature: str,
) -> bool:
    amended_selection, amended_result = amend_analysis_payload(
        contract, selection, result, schedule_integrity=schedule_integrity, rank_integrity=rank_integrity,
    )
    return _ORIGINAL_SEAL_ANALYSIS(
        amended_selection, amended_result, selection_path=selection_path, result_path=result_path,
        integrity_path=integrity_path, signature=signature,
    )


def _configure_base(contract: Mapping[str, Any]) -> None:
    old_prepared = output_path(contract, "prepared_integrity").resolve()
    old_signature = contract["predecessor"]["run_signature"]
    rank_set_integrity_path = output_path(contract, "rank_set_integrity").resolve()

    def verify_integrity_proxy(
        integrity_path: Path, artifacts: Mapping[str, Path], *, signature: str,
    ) -> dict[str, Any]:
        effective_signature = old_signature if Path(integrity_path).resolve() == old_prepared else signature
        return _ORIGINAL_VERIFY_INTEGRITY(integrity_path, artifacts, signature=effective_signature)

    def write_integrity_proxy(
        integrity_path: Path, artifacts: Mapping[str, Path], *, signature: str, metadata: Mapping[str, Any],
    ) -> None:
        amended_metadata = copy.deepcopy(dict(metadata))
        if Path(integrity_path).resolve() == rank_set_integrity_path:
            schedule_integrity = verify_schedule(contract, recompute=True)
            amended_metadata.update({
                "foldin_schedule_sha256": schedule_integrity["artifacts"]["foldin_schedule"]["sha256"],
                "foldin_schedule_integrity_sha256": base.sha256_file(output_path(contract, "foldin_schedule_integrity")),
                "foldin_schedule_run_signature": run_signature(contract),
            })
        _ORIGINAL_WRITE_INTEGRITY(
            integrity_path, artifacts, signature=signature, metadata=amended_metadata,
        )

    def seal_analysis_proxy(
        selection: Mapping[str, Any], result: Mapping[str, Any], *, selection_path: Path,
        result_path: Path, integrity_path: Path, signature: str,
    ) -> bool:
        schedule_integrity = verify_schedule(contract, recompute=False)
        rank_integrity = verify_rank_set_with_schedule(contract, signature=run_signature(contract))
        return seal_amended_analysis(
            contract, selection, result, schedule_integrity=schedule_integrity, rank_integrity=rank_integrity,
            selection_path=selection_path, result_path=result_path, integrity_path=integrity_path,
            signature=signature,
        )

    base.output_path = output_path
    base._fit_directory = predecessor_fit_directory
    base._verify_fit = verify_predecessor_fit
    base.verify_integrity = verify_integrity_proxy
    base.write_integrity = write_integrity_proxy
    base.verify_rank_set = verify_rank_set_with_schedule
    base.run_signature = run_signature
    base.progress_update = progress_update
    base.fold_in_batch = fold_in_batch
    base.seal_or_reuse_analysis = seal_analysis_proxy


def score(contract: Mapping[str, Any]) -> dict[str, Any]:
    verify_schedule(contract, recompute=True)
    rank_preflight(contract)
    return base.score(contract)


def materialize_metrics(contract: Mapping[str, Any]) -> dict[str, Any]:
    verify_rank_set_with_schedule(contract, signature=run_signature(contract))
    return base.materialize_metrics(contract)


def bootstrap(contract: Mapping[str, Any]) -> dict[str, Any]:
    verify_rank_set_with_schedule(contract, signature=run_signature(contract))
    return base.bootstrap(contract)


def analyze(contract: Mapping[str, Any]) -> dict[str, Any]:
    verify_rank_set_with_schedule(contract, signature=run_signature(contract))
    base.analyze(contract)
    return base.read_json(output_path(contract, "selection"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument(
        "--phase", choices=("lock", "fit", "schedule", "score", "metrics", "bootstrap", "analyze", "run"), required=True,
    )
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    contract_path = args.contract.resolve()
    if contract_path != DEFAULT_CONTRACT.resolve():
        raise RuntimeError("REC-EV-023D-A1 accepts only the default amendment contract")
    contract, overlay = load_effective_contract(contract_path)
    if np.__version__ != contract["statistics"]["numpy_version"]:
        raise RuntimeError(f"NumPy version drift: {np.__version__}")
    if args.phase == "lock":
        value: Any = create_or_verify_lock(contract, overlay, contract_path, resume=args.resume)
    else:
        if not args.resume:
            raise ResumeError("REC-EV-023D-A1 real phases require --resume")
        create_or_verify_lock(contract, overlay, contract_path, resume=True)
        _configure_base(contract)
        phases = ("fit", "schedule", "score", "metrics", "bootstrap", "analyze") if args.phase == "run" else (args.phase,)
        value = None
        for phase in phases:
            if phase == "fit":
                value = verify_predecessor(contract)
                progress_update(contract, "PREDECESSOR_FITS_VERIFIED", completed_seeds=contract["lightfm"]["seeds"])
            elif phase == "schedule":
                value = create_or_verify_schedule(contract, resume=True)
            elif phase == "score":
                value = score(contract)
            elif phase == "metrics":
                value = materialize_metrics(contract)
            elif phase == "bootstrap":
                value = bootstrap(contract)
            elif phase == "analyze":
                value = analyze(contract)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
