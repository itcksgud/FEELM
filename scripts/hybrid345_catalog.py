"""Full-catalog diagnostics for the sealed hybrid345 policy selection.

This stage intentionally runs after evaluation because the calibration and
warm/cold policy choices are label-dependent.  It reuses only the candidate
axis and fixed FM/GBT scores in final344's sealed per-user catalog cache.  ALS
unavailability remains explicit: only the router may use its declared cold
head and GBT fallback.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import sparse

from hybrid345_common import ROOT, OUT, pin, read_json, require, write_json
from hybrid345_evaluate import (EVALUATION_REVIEW, TMDB_VOTE_METADATA_RELATIVE,
                                evaluation_fingerprint, require_evaluation_review,
                                verify_final344_calibration_parent)
from hybrid345_models import dense_profile_scores, fold_in_scores, sparse_profile_scores
from hybrid345_score import load_actual_factors, verify_mapper, verify_predictions


DEFAULT_CONFIG = ROOT / "docs/recommendation/experiments/hybrid345/config.json"
DEFAULT_EVALUATION = OUT
CATALOG_REVIEW = ROOT / "docs/recommendation/experiments/hybrid345/catalog-code-review.json"
FINAL344 = ROOT / "outputs/recommendation-evidence/final344"
TOP_NS = (2, 4, 6)
OUTPUT_MODELS = ("QWEN_DIRECT", "ALS_C2F", "ALS_QWEN", "SELECTED_COLD_HEAD", "ROUTER_s339")
ALLOWED_COLD = ("GBT120_s339", "STRUCTURED_DIRECT", "QWEN_DIRECT", "ALS_C2F", "FM150_s339")
MODEL_SCOPES = {
    "QWEN_DIRECT": "FULL_EI_WHERE_MODEL_AVAILABLE",
    "ALS_C2F": "FULL_EI_WHERE_MODEL_AVAILABLE",
    "ALS_QWEN": "ALS_WARM_AVAILABLE_WITHIN_EI",
    "SELECTED_COLD_HEAD": "FULL_EI_WHERE_SELECTED_HEAD_AVAILABLE",
    "ROUTER_s339": "FULL_EI_WITH_DECLARED_GBT_FALLBACK",
}


def catalog_fingerprint(config_path: Path = DEFAULT_CONFIG) -> dict[str, dict[str, Any]]:
    paths = [
        Path(__file__),
        ROOT / "scripts/test_hybrid345_catalog.py",
        ROOT / "scripts/report_hybrid345.py",
        ROOT / "scripts/hybrid345_evaluate.py",
        ROOT / "scripts/test_hybrid345_evaluate.py",
        ROOT / "docs/recommendation/experiments/hybrid345/DESIGN.md",
        Path(config_path),
        ROOT / "docs/recommendation/experiments/hybrid345/design-review.json",
        ROOT / "docs/recommendation/experiments/hybrid345/evaluation-code-review.json",
    ]
    return {path.resolve().relative_to(ROOT.resolve()).as_posix(): pin(path) for path in paths}


def require_catalog_review(config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    require(CATALOG_REVIEW.is_file(),
            "independent catalog-code review is required before catalog labels")
    review = read_json(CATALOG_REVIEW)
    require(review.get("status") == "PASS" and review.get("scope") == "DEVELOPMENT_ONLY",
            "catalog-code review must PASS in development-only scope")
    require(review.get("fingerprint") == catalog_fingerprint(config_path),
            "catalog implementation changed after review")
    return review


def _assert_pin(path: Path, expected: Mapping[str, Any], label: str) -> None:
    require(path.is_file(), f"missing {label}: {path}")
    require(pin(path) == dict(expected), f"sealed {label} drift")


def _resolve_artifact(name: str, directory: Path) -> Path:
    path = Path(name)
    if path.is_absolute():
        return path
    local = directory / path
    return local if local.exists() else ROOT / path


def verify_evaluation_gate(evaluation_dir: Path, config_path: Path) -> dict[str, Any]:
    """Verify every selection/calibration parent before any label table is read."""
    seal_path = evaluation_dir / "evaluation-seal.json"
    require(seal_path.is_file(), "catalog diagnostic requires a completed evaluation seal")
    seal = read_json(seal_path)
    require(seal.get("scope") == "DEVELOPMENT_ONLY", "development-only evaluation seal")
    manifest_path = evaluation_dir / "evaluation-manifest.json"
    _assert_pin(manifest_path, seal.get("manifest", {}), "evaluation manifest")
    for name, expected in seal.get("files", {}).items():
        _assert_pin(_resolve_artifact(str(name), evaluation_dir), expected, f"evaluation output {name}")

    manifest = read_json(manifest_path)
    evaluation_review = require_evaluation_review(config_path)
    require(manifest.get("scope") == "DEVELOPMENT_ONLY", "development-only evaluation manifest")
    require(manifest.get("labels_opened_after_prediction_gate") is True,
            "evaluation must record its label gate")
    require(manifest.get("config") == pin(config_path), "evaluation/config drift")
    require(manifest.get("evaluation_review") == pin(EVALUATION_REVIEW),
            "evaluation manifest review parent drift")
    require(manifest.get("evaluation_fingerprint") == evaluation_review["fingerprint"] ==
            evaluation_fingerprint(config_path), "evaluation manifest code fingerprint drift")
    require(manifest.get("code", {}).get("evaluation") ==
            pin(ROOT / "scripts/hybrid345_evaluate.py"),
            "evaluation manifest evaluator code drift")
    prediction_seal = OUT / "prediction-seal.json"
    _assert_pin(prediction_seal, seal.get("prediction_seal", {}), "prediction seal")
    require(manifest.get("prediction_seal") == pin(prediction_seal),
            "evaluation manifest prediction parent")
    config = read_json(config_path)
    direct_inputs = ("catalog", "contexts", "roles", "partition", "rec033_metadata", "texts",
                     "final344_input_lock")
    for source_name in direct_inputs:
        expected = manifest.get("inputs", {}).get(source_name)
        require(expected is not None, f"evaluation manifest pins {source_name}")
        _assert_pin(ROOT / config["sources"][source_name], expected,
                    f"evaluation input {source_name}")
    input_lock = read_json(ROOT / config["sources"]["final344_input_lock"])
    expected_vote_metadata = input_lock.get("files", {}).get(TMDB_VOTE_METADATA_RELATIVE)
    require(expected_vote_metadata is not None, "final344 input lock pins TMDB vote metadata")
    require(manifest.get("inputs", {}).get("tmdb_vote_metadata") == expected_vote_metadata,
            "evaluation manifest records locked TMDB vote metadata")
    _assert_pin(ROOT / TMDB_VOTE_METADATA_RELATIVE, expected_vote_metadata,
                "evaluation TMDB vote metadata")
    calibration_inputs = verify_final344_calibration_parent(config)
    for name, expected in calibration_inputs.items():
        require(manifest.get("inputs", {}).get(name) == expected,
                f"evaluation manifest records {name}")
    require(set(manifest.get("inputs", {})) == {
        *direct_inputs, "labels", "tmdb_vote_metadata", *calibration_inputs,
    },
            "evaluation manifest exact input inventory")
    # This recursively validates Qwen, mapper, model code and label-free score axes.
    verify_predictions()

    selection_path = evaluation_dir / "selection.json"
    calibration_path = evaluation_dir / "calibration.json"
    require("selection.json" in seal.get("files", {}) and
            "calibration.json" in seal.get("files", {}),
            "evaluation seal must pin selection and calibration")
    _assert_pin(selection_path, seal["files"]["selection.json"], "selection")
    _assert_pin(calibration_path, seal["files"]["calibration.json"], "calibration")
    selection = read_json(selection_path)
    require(selection.get("scope") == "DEVELOPMENT_ONLY", "development selection scope")
    require(selection.get("roles_used") == ["calibration"] and
            selection.get("comparison_labels_used") is False,
            "policy selection must use calibration users only")
    weight = float(selection.get("warm", {}).get("content_weight", np.nan))
    cold = str(selection.get("cold", {}).get("head", ""))
    require(weight in (0.0, 0.1, 0.25, 0.5), "frozen warm content weight")
    require(cold in ALLOWED_COLD, "declared cold head")
    return {
        "seal": seal,
        "seal_pin": pin(seal_path),
        "manifest": manifest,
        "manifest_pin": pin(manifest_path),
        "selection": selection,
        "selection_pin": pin(selection_path),
        "calibration_pin": pin(calibration_path),
    }


def calibration_map(path: Path, cap: int = 10) -> dict[str, tuple[float, float]]:
    payload = read_json(path)
    require(payload.get("scope") == "DEVELOPMENT_ONLY", "calibration scope")
    result: dict[str, tuple[float, float]] = {}
    for row in payload.get("fits", []):
        if int(row["cap"]) != cap:
            continue
        require(row.get("a") is not None and row.get("b") is not None,
                f"usable cap{cap} calibration for {row.get('model')}")
        result[str(row["model"])] = (float(row["a"]), float(row["b"]))
    return result


def affine(values: np.ndarray, fit: tuple[float, float]) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    return fit[0] + fit[1] * values


def expected_candidate_indices(
    released_at_snapshot: np.ndarray,
    viewed: Sequence[int],
) -> np.ndarray:
    allowed = np.asarray(released_at_snapshot, dtype=bool).copy()
    viewed_array = np.asarray(viewed, dtype=np.int64)
    require(viewed_array.ndim == 1 and len(np.unique(viewed_array)) == len(viewed_array),
            "unique viewed indices")
    require(((viewed_array >= 0) & (viewed_array < len(allowed))).all(),
            "legal viewed indices")
    allowed[viewed_array] = False
    return np.flatnonzero(allowed)


def verify_candidate_axis(
    cached: np.ndarray,
    released_at_snapshot: np.ndarray,
    viewed: Sequence[int],
) -> np.ndarray:
    cached = np.asarray(cached, dtype=np.int64)
    require(cached.ndim == 1 and len(np.unique(cached)) == len(cached),
            "unique cached candidate indices")
    expected = expected_candidate_indices(released_at_snapshot, viewed)
    require(np.array_equal(cached, expected),
            "final344 candidate axis must exactly reproduce release and seen exclusion")
    return cached


def rank_top(scores: np.ndarray, movie_ids: np.ndarray, n: int = 6) -> np.ndarray:
    scores = np.asarray(scores, dtype=np.float64)
    movie_ids = np.asarray(movie_ids, dtype=np.int64)
    require(scores.shape == movie_ids.shape, "score/movie ranking axis")
    finite = np.isfinite(scores)
    if not finite.any():
        return np.empty(0, dtype=np.int64)
    positions = np.flatnonzero(finite)
    order = np.lexsort((movie_ids[positions], -scores[positions]))
    return positions[order[:n]]


def combine_policy_scores(
    *,
    actual_support: np.ndarray,
    user_factor_available: bool,
    als_raw: np.ndarray,
    qwen_raw: np.ndarray,
    cold_raw: np.ndarray,
    fallback_raw: np.ndarray,
    als_fit: tuple[float, float],
    qwen_fit: tuple[float, float],
    cold_fit: tuple[float, float],
    fallback_fit: tuple[float, float],
    content_weight: float,
) -> dict[str, np.ndarray]:
    """Apply the exact evaluated item-level warm/cold router without ALS fill."""
    actual_support = np.asarray(actual_support, dtype=bool)
    arrays = [np.asarray(x, dtype=np.float64) for x in
              (als_raw, qwen_raw, cold_raw, fallback_raw)]
    require(all(x.shape == actual_support.shape for x in arrays), "policy score axes")
    als_cal, qwen_cal = affine(arrays[0], als_fit), affine(arrays[1], qwen_fit)
    cold_cal, fallback_cal = affine(arrays[2], cold_fit), affine(arrays[3], fallback_fit)
    warm = np.full(actual_support.shape, np.nan, dtype=np.float64)
    if content_weight == 0.0:
        warm_available = bool(user_factor_available) & actual_support & np.isfinite(als_cal)
        warm[warm_available] = als_cal[warm_available]
    else:
        warm_available = (bool(user_factor_available) & actual_support &
                          np.isfinite(als_cal) & np.isfinite(qwen_cal))
        warm[warm_available] = ((1.0 - content_weight) * als_cal[warm_available]
                                + content_weight * qwen_cal[warm_available])
    router = np.full(actual_support.shape, np.nan, dtype=np.float64)
    router[warm_available] = warm[warm_available]
    cold_available = bool(user_factor_available) & ~actual_support & np.isfinite(cold_cal)
    router[cold_available] = cold_cal[cold_available]
    fallback = ~np.isfinite(router) & np.isfinite(fallback_cal)
    router[fallback] = fallback_cal[fallback]
    require(np.isfinite(router).all(), "router fallback must score every candidate")
    return {"ALS_QWEN": warm, "SELECTED_COLD_HEAD": cold_cal, "ROUTER_s339": router}


def summarize_top(top: pd.DataFrame, users: int, eligible_catalog_movies: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for model in OUTPUT_MODELS:
        model_rows = top[top.model.eq(model)]
        for n in TOP_NS:
            sample = model_rows[model_rows["rank"] <= n]
            counts = sample.movie_id.value_counts()
            returned = int(len(sample))
            slots = int(users * n)
            shares = counts.to_numpy(np.float64) / returned if returned else np.empty(0)
            rows.append({
                "model": model,
                "candidate_scope": MODEL_SCOPES[model],
                "top_n": n,
                "users": users,
                "slots": slots,
                "returned": returned,
                "missing": slots - returned,
                "unique_movies": int(len(counts)),
                "catalog_coverage": float(len(counts) / eligible_catalog_movies),
                "unknown": int(sample.unknown.sum()),
                "unknown_rate": float(sample.unknown.mean()) if returned else np.nan,
                "hhi": float(np.square(shares).sum()) if returned else np.nan,
                "max_share": float(shares.max()) if returned else np.nan,
                "support0": int(sample.support.eq(0).sum()),
                "support1_9": int(sample.support.between(1, 9).sum()),
                "support10_49": int(sample.support.between(10, 49).sum()),
                "support50plus": int(sample.support.ge(50).sum()),
                "actual_als_supported": int(sample.actual_als_supported.sum()),
                "blocked": int(sample.blocked.sum()),
            })
    return pd.DataFrame(rows)


def _load_contexts(config: Mapping[str, Any], catalog_size: int) -> list[dict[str, Any]]:
    roles = pd.read_csv(ROOT / config["sources"]["roles"])
    comparison = set(roles.loc[roles.role.eq("comparison"), "uid"].astype(int))
    contexts = read_json(ROOT / config["sources"]["contexts"])
    cap_contexts = sorted(
        (c for c in contexts if int(c["cap"]) == 10 and int(c["uid"]) in comparison),
        key=lambda c: int(c["uid"]),
    )
    require(len(comparison) == int(config["roles"]["comparison"]) and
            len(cap_contexts) == int(config["roles"]["comparison"]) and
            len({int(c["uid"]) for c in cap_contexts}) == len(cap_contexts),
            "all 180 comparison cap10 contexts")
    selected = [c for c in cap_contexts if int(c["h"]) > 0]
    require(selected and len(selected) < len(cap_contexts),
            "nonempty explicit H10/h>0 comparison subset")
    for context in selected:
        oi = np.asarray(context["oi"], dtype=np.int64)
        stars = np.asarray(context["stars"], dtype=np.float64)
        require(len(oi) == len(stars) == int(context["h"]) and len(oi) > 0,
                "positive exact input history")
        require(((oi >= 0) & (oi < catalog_size)).all(), "legal history indices")
    return selected


def _cold_raw(
    cold_name: str,
    cache: Mapping[str, np.ndarray],
    *,
    candidates: np.ndarray,
    structure: sparse.csr_matrix,
    qwen_raw: np.ndarray,
    c2f_raw: np.ndarray,
    history: np.ndarray,
    stars: np.ndarray,
    prior: np.ndarray,
) -> np.ndarray:
    if cold_name == "STRUCTURED_DIRECT":
        values, active = sparse_profile_scores(structure, history, stars, candidates, prior)
        return values if active else np.full(len(candidates), np.nan)
    if cold_name == "QWEN_DIRECT":
        return qwen_raw
    if cold_name == "ALS_C2F":
        return c2f_raw
    require(cold_name in ("FM150_s339", "GBT120_s339"), "cache-backed cold head")
    return np.asarray(cache[cold_name], dtype=np.float64)


def verify_final344_catalog_lineage(config: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively verify the sealed cache producer and all declared parents."""
    sources = config["sources"]
    final_seal_path = ROOT / sources["final344_catalog_seal"]
    configured_pin = config["source_pins"][sources["final344_catalog_seal"]]
    _assert_pin(final_seal_path, configured_pin, "final344 catalog seal")
    final_seal = read_json(final_seal_path)
    input_lock_path = ROOT / sources["final344_input_lock"]
    model_audit_path = ROOT / sources["final344_model_audit"]
    _assert_pin(input_lock_path, config["source_pins"][sources["final344_input_lock"]],
                "configured final344 input lock")
    _assert_pin(model_audit_path, config["source_pins"][sources["final344_model_audit"]],
                "configured final344 model audit")
    require(final_seal.get("input_lock") == pin(input_lock_path),
            "final344 catalog input-lock parent drift")
    require(final_seal.get("model_audit") == pin(model_audit_path),
            "final344 catalog model-audit parent drift")
    expected_fit_seals = {}
    for source_key in ("fm_seals_by_seed", "gbt_seals_by_seed"):
        for seed, relative in sources[source_key].items():
            model = ("FM150_s" if source_key.startswith("fm_") else "GBT120_s") + seed
            _assert_pin(ROOT / relative, config["source_pins"][relative],
                        f"configured final344 fit seal {model}")
            expected_fit_seals[model] = pin(ROOT / relative)
    require(final_seal.get("fit_seals") == expected_fit_seals,
            "final344 catalog fit-seal parents drift")
    selection_seal_path = FINAL344 / "selection-seal.json"
    _assert_pin(selection_seal_path, final_seal.get("selection", {}),
                "final344 selection seal")
    final_selection_seal = read_json(selection_seal_path)
    require(final_selection_seal.get("input_lock") == pin(input_lock_path),
            "final344 selection/input-lock parent drift")
    require(set(final_selection_seal.get("files", {})) ==
            {"selection.json", "selection-user-metrics.parquet"},
            "final344 selection seal exact output inventory")
    for name, expected in final_selection_seal["files"].items():
        _assert_pin(FINAL344 / name, expected, f"final344 selection output {name}")
    return {
        "catalog_seal": final_seal,
        "catalog_seal_pin": pin(final_seal_path),
        "input_lock": pin(input_lock_path),
        "model_audit": pin(model_audit_path),
        "fit_seals": expected_fit_seals,
        "selection_seal": pin(selection_seal_path),
        "selection": final_selection_seal,
    }


def run_catalog(
    evaluation_dir: Path = DEFAULT_EVALUATION,
    config_path: Path = DEFAULT_CONFIG,
    output_dir: Path = OUT,
) -> dict[str, Any]:
    started = time.monotonic()
    evaluation_dir, config_path, output_dir = map(Path, (evaluation_dir, config_path, output_dir))
    config = read_json(config_path)
    require(config.get("claim_scope") == "DEVELOPMENT_ONLY", "development-only catalog scope")
    contract = config.get("catalog_diagnostic", {})
    require(contract.get("roles") == "comparison" and int(contract.get("cap", -1)) == 10
            and contract.get("h_positive") is True and tuple(contract.get("top_ns", ())) == TOP_NS,
            "frozen catalog diagnostic contract")
    catalog_review = require_catalog_review(config_path)
    gate = verify_evaluation_gate(evaluation_dir, config_path)

    result_names = ("catalog-top6.parquet", "catalog-summary.csv", "catalog-timing.csv",
                    "catalog-supply.csv", "catalog-diagnostic-seal.json")
    require(not any((output_dir / name).exists() for name in result_names),
            "preserve existing hybrid345 catalog outputs")

    sources = config["sources"]
    catalog = pd.read_parquet(ROOT / sources["catalog"])
    ids = catalog.movie_id.to_numpy(np.int64)
    require(len(ids) == int(config["catalog_movies"]) and np.all(ids[:-1] < ids[1:]),
            "canonical catalog axis")
    actual_support = (~catalog.blocked.to_numpy(bool)) & catalog.train_count.to_numpy(np.int64).__gt__(0)
    require(int(actual_support.sum()) == int(config["movie_groups"]["W_DIRECT"]),
            "ALS supported catalog partition")
    texts = pd.read_parquet(ROOT / sources["texts"], columns=["movie_id", "release_date"])
    require(np.array_equal(texts.movie_id.to_numpy(np.int64), ids), "text/catalog movie axis")
    dates = pd.to_datetime(texts.release_date, format="%Y-%m-%d", errors="coerce", utc=True)
    released = dates.notna().to_numpy() & (dates.astype("int64").to_numpy() // 10**9
                                           <= int(config["catalog_snapshot_timestamp"]))
    contexts = _load_contexts(config, len(ids))

    final_seal_path = ROOT / sources["final344_catalog_seal"]
    final344_parent = verify_final344_catalog_lineage(config)
    final_seal = final344_parent["catalog_seal"]
    cache_dir = ROOT / sources["final344_catalog_cache"]

    qwen = np.load(OUT / "qwen-embeddings.npy", mmap_mode="r")
    mapped = np.load(OUT / "mapped-factors.npy", mmap_mode="r")
    require(qwen.shape == (len(ids), int(config["qwen"]["dimension"])) and
            mapped.shape == (len(ids), 32), "Qwen/mapped catalog axes")
    require(np.isfinite(qwen).all() and np.isfinite(mapped).all(), "finite catalog representations")
    structure = sparse.load_npz(ROOT / sources["structured"]).tocsr()
    require(structure.shape[0] == len(ids) and np.isfinite(structure.data).all(),
            "structured catalog axis")
    prior = np.load(OUT / "current-prior.npz", allow_pickle=False)["g0_mid"].astype(np.float64)
    mapper_parent = verify_mapper(OUT / "qwen-embeddings.npy")
    qwen_pin = pin(OUT / "qwen-embeddings.npy")
    mapped_pin = pin(OUT / "mapped-factors.npy")
    actual_factors = load_actual_factors(ids, actual_support)

    calibrations = calibration_map(evaluation_dir / "calibration.json", 10)
    selection = gate["selection"]
    cold_name = str(selection["cold"]["head"])
    weight = float(selection["warm"]["content_weight"])
    needed = {"ALS", "QWEN_DIRECT", "ALS_C2F", cold_name, "GBT120_s339"}
    require(needed.issubset(calibrations), "all catalog policy heads have cap10 calibration")

    top_rows: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    supply_rows: list[dict[str, Any]] = []
    eligible_union_mask = np.zeros(len(ids), dtype=bool)
    used_cache_pins: dict[int, Mapping[str, Any]] = {}
    for number, context in enumerate(contexts, 1):
        clock = time.perf_counter()
        uid = int(context["uid"])
        cache_path = cache_dir / f"{uid}.npz"
        key = f"catalog-cache/{uid}.npz"
        require(key in final_seal.get("files", {}), f"sealed final344 cache entry {uid}")
        _assert_pin(cache_path, final_seal["files"][key], f"final344 catalog cache {uid}")
        used_cache_pins[uid] = dict(final_seal["files"][key])
        cache = np.load(cache_path, allow_pickle=False)
        candidates = verify_candidate_axis(cache["ei"], released, context["viewed"])
        eligible_union_mask[candidates] = True
        require("GBT120_s339" in cache.files and "FM150_s339" in cache.files,
                "fixed seed339 cache scores")
        history = np.asarray(context["oi"], dtype=np.int64)
        stars = np.asarray(context["stars"], dtype=np.float64)

        qwen_raw, qwen_active = dense_profile_scores(qwen, history, stars, candidates, prior)
        history_supported = actual_support[history]
        als_raw = np.full(len(candidates), np.nan, dtype=np.float64)
        if history_supported.any():
            warm_positions = np.flatnonzero(actual_support[candidates])
            warm_values, active = fold_in_scores(
                actual_factors[history[history_supported]], stars[history_supported],
                actual_factors[candidates[warm_positions]], reg=float(config["als_fold_in_reg"]),
            )
            require(active, "supported ALS history must fold in")
            als_raw[warm_positions] = warm_values
        c2f_raw, c2f_active = fold_in_scores(
            actual_factors[history[history_supported]], stars[history_supported], mapped[candidates],
            reg=float(config["als_fold_in_reg"]),
        )
        if not qwen_active:
            require(np.isnan(qwen_raw).all(), "inactive Qwen stays unavailable")
        if not c2f_active:
            require(np.isnan(c2f_raw).all(), "inactive ALS-C2F stays unavailable")
        cold_raw = _cold_raw(cold_name, cache, candidates=candidates, structure=structure,
                             qwen_raw=qwen_raw, c2f_raw=c2f_raw, history=history,
                             stars=stars, prior=prior)
        fallback_raw = np.asarray(cache["GBT120_s339"], dtype=np.float64)
        require(len(fallback_raw) == len(candidates) and np.isfinite(fallback_raw).all(),
                "finite complete GBT fallback")

        derived = combine_policy_scores(
            actual_support=actual_support[candidates], user_factor_available=bool(c2f_active),
            als_raw=als_raw, qwen_raw=qwen_raw,
            cold_raw=cold_raw, fallback_raw=fallback_raw,
            als_fit=calibrations["ALS"], qwen_fit=calibrations["QWEN_DIRECT"],
            cold_fit=calibrations[cold_name], fallback_fit=calibrations["GBT120_s339"],
            content_weight=weight,
        )
        scores = {
            "QWEN_DIRECT": affine(qwen_raw, calibrations["QWEN_DIRECT"]),
            "ALS_C2F": affine(c2f_raw, calibrations["ALS_C2F"]),
            **derived,
        }
        for model in OUTPUT_MODELS:
            values = scores[model]
            require(values.shape == candidates.shape, f"{model} candidate score axis")
            finite = np.isfinite(values)
            supply_rows.append({"uid": uid, "h": int(context["h"]), "model": model,
                                "candidates": len(candidates), "finite_scores": int(finite.sum()),
                                "unavailable_scores": int((~finite).sum())})
            ranked = rank_top(values, ids[candidates], 6)
            for rank, relative in enumerate(ranked, 1):
                ci = int(candidates[relative])
                top_rows.append({"uid": uid, "h": int(context["h"]), "model": model,
                                 "rank": rank, "movie_id": int(ids[ci]),
                                 "prediction": float(values[relative]), "catalog_index": ci,
                                 "candidate_scope": MODEL_SCOPES[model]})
        timing_rows.append({"uid": uid, "h": int(context["h"]),
                            "seconds": time.perf_counter() - clock,
                            "candidates": len(candidates)})
        print(f"HYBRID345_CATALOG {number}/{len(contexts)}", flush=True)

    top = pd.DataFrame(top_rows)
    require(not top.duplicated(["uid", "model", "movie_id"]).any(),
            "no duplicate recommendation per user/model")
    ix = top.catalog_index.to_numpy(np.int64)
    require(np.array_equal(ids[ix], top.movie_id.to_numpy(np.int64)), "ranked catalog identity")

    # The only label read in this module occurs after the completed evaluation gate,
    # frozen selection, and all full-catalog scoring.
    label_path = ROOT / sources["labels"]
    manifest_label = gate["manifest"].get("inputs", {}).get("labels")
    require(manifest_label is not None, "evaluation manifest pins labels")
    _assert_pin(label_path, manifest_label, "evaluation labels")
    labels = pd.read_parquet(label_path, columns=["uid", "movie_id"])
    observed = pd.MultiIndex.from_frame(labels[["uid", "movie_id"]])
    top["unknown"] = ~pd.MultiIndex.from_frame(top[["uid", "movie_id"]]).isin(observed)
    top["support"] = catalog.train_count.to_numpy(np.int64)[ix]
    top["blocked"] = catalog.blocked.to_numpy(bool)[ix]
    top["actual_als_supported"] = actual_support[ix]
    top["selected_warm_weight"] = weight
    top["selected_cold_head"] = cold_name
    eligible_union = int(eligible_union_mask.sum())
    summary = summarize_top(top, users=len(contexts), eligible_catalog_movies=eligible_union)
    summary["eligible_catalog_movies"] = eligible_union
    summary["selected_warm_weight"] = weight
    summary["selected_cold_head"] = cold_name

    _assert_pin(label_path, manifest_label, "evaluation labels after unknown join")
    require(verify_mapper(OUT / "qwen-embeddings.npy") == mapper_parent,
            "mapper parent changed during catalog diagnostic")
    require(pin(OUT / "qwen-embeddings.npy") == qwen_pin and
            pin(OUT / "mapped-factors.npy") == mapped_pin,
            "catalog representation changed during diagnostic")
    for uid, expected in used_cache_pins.items():
        _assert_pin(cache_dir / f"{uid}.npz", expected,
                    f"final344 catalog cache {uid} after scoring")
    require(verify_final344_catalog_lineage(config) == final344_parent,
            "final344 catalog lineage changed during diagnostic")

    output_dir.mkdir(parents=True, exist_ok=True)
    top.to_parquet(output_dir / "catalog-top6.parquet", index=False)
    summary.to_csv(output_dir / "catalog-summary.csv", index=False)
    pd.DataFrame(timing_rows).to_csv(output_dir / "catalog-timing.csv", index=False)
    pd.DataFrame(supply_rows).to_csv(output_dir / "catalog-supply.csv", index=False)
    files = {name: pin(output_dir / name) for name in result_names[:-1]}
    seal = {
        "scope": "DEVELOPMENT_ONLY",
        "diagnostic_only": True,
        "evaluation_seal": gate["seal_pin"],
        "evaluation_manifest": gate["manifest_pin"],
        "selection": gate["selection_pin"],
        "calibration": gate["calibration_pin"],
        "prediction_seal": pin(OUT / "prediction-seal.json"),
        "mapper_seal": pin(OUT / "mapper-seal.json"),
        "qwen_embeddings": qwen_pin,
        "mapped_factors": mapped_pin,
        "final344_catalog_seal": final344_parent["catalog_seal_pin"],
        "models": list(OUTPUT_MODELS),
        "model_candidate_scopes": MODEL_SCOPES,
        "selected_warm_weight": weight,
        "selected_cold_head": cold_name,
        "users": len(contexts),
        "h_positive_comparison_users": len(contexts),
        "comparison_cap10_users": int(config["roles"]["comparison"]),
        "eligible_catalog_movies": eligible_union,
        "top_ns": list(TOP_NS),
        "unknown_definition": "not_in_observed_user_movie_labels",
        "labels_opened_after_evaluation_gate_and_selection": True,
        "seconds": time.monotonic() - started,
        "code": pin(Path(__file__)),
        "catalog_review": pin(CATALOG_REVIEW),
        "catalog_fingerprint": catalog_review["fingerprint"],
        "files": files,
    }
    write_json(output_dir / "catalog-diagnostic-seal.json", seal)
    require(verify_evaluation_gate(evaluation_dir, config_path)["seal_pin"] == gate["seal_pin"],
            "evaluation gate changed during catalog diagnostic")
    require(require_catalog_review(config_path) == catalog_review,
            "catalog review changed during catalog diagnostic")
    return seal


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-dir", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    result = run_catalog(args.evaluation_dir, args.config, args.output_dir)
    print(json.dumps({"status": "SEALED", "users": result["users"],
                      "warm_weight": result["selected_warm_weight"],
                      "cold_head": result["selected_cold_head"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
