"""Evaluate the sealed hybrid345 score matrix on the frozen development split.

Importing this module performs no file I/O.  The CLI verifies the prediction
seal and all label-free axes before it opens the frozen rating labels.  Model
and policy choices use only the calibration users; comparison users are read
only after those choices have been frozen in memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "docs/recommendation/experiments/hybrid345/config.json"
DEFAULT_ARTIFACT = ROOT / "outputs/recommendation-evidence/hybrid345"
EVALUATION_REVIEW = ROOT / "docs/recommendation/experiments/hybrid345/evaluation-code-review.json"
LABEL_SHA256 = "e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8"
TMDB_VOTE_METADATA_RELATIVE = "outputs/recommendation-evidence/rec-ev-045/metadata.parquet"
FINAL344_CALIBRATION_RELATIVE = "outputs/recommendation-evidence/final344/final-calibration.json"
FINAL344_CALIBRATION_SEAL_RELATIVE = "outputs/recommendation-evidence/final344/final-calibration-seal.json"

CONTENT_MODELS = (
    "ALS",
    "STRUCTURED_DIRECT",
    "E5_DIRECT",
    "QWEN_DIRECT",
    "ALS_C2F",
)
SEEDS = (339, 344, 345)
FM_MODELS = tuple(f"FM150_s{seed}" for seed in SEEDS)
GBT_MODELS = tuple(f"GBT120_s{seed}" for seed in SEEDS)
BASE_MODELS = (*CONTENT_MODELS[:-1], *FM_MODELS, *GBT_MODELS, "ALS_C2F")
DERIVED_MODELS = ("ALS_QWEN", "ROUTER_s339")
METRIC_MEAN_MODELS = ("FM150_SEED_MEAN", "GBT120_SEED_MEAN")
COLD_CHALLENGERS = (
    "STRUCTURED_DIRECT",
    "QWEN_DIRECT",
    "ALS_C2F",
    "FM150_s339",
)
COLD_SELECTION_MODELS = (
    *COLD_CHALLENGERS,
    "GBT120_s339",
)
GROUPS = ("ALL", "W_DIRECT", "C", "V", "E", "NATURAL_ZERO")
TOP_NS = (1, 2, 4, 6)
EPS = 1e-12


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pin(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"bytes": size, "sha256": digest.hexdigest()}


def _assert_pin(path: Path, expected: Any, label: str) -> None:
    actual = pin(path)
    if isinstance(expected, str):
        require(actual["sha256"] == expected, f"sealed SHA mismatch: {label}")
        return
    require(isinstance(expected, Mapping), f"invalid pin record: {label}")
    require(actual["sha256"] == expected.get("sha256"), f"sealed SHA mismatch: {label}")
    if expected.get("bytes") is not None:
        require(actual["bytes"] == int(expected["bytes"]), f"sealed byte count mismatch: {label}")


def load_tmdb_vote_metadata(
    config: Mapping[str, Any],
    catalog: pd.DataFrame,
    root: Path = ROOT,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Load vote counts only through the already-pinned final344 input lock.

    REC-033 metadata is the text-document identity source and deliberately does
    not contain mutable popularity fields. REC-045 is the canonical full-axis
    source for the frozen TMDB vote-count diagnostic. The hybrid config pins
    final344's input lock, which in turn pins the exact REC-045 parquet.
    """
    root = Path(root)
    sources = config.get("sources", {})
    source_pins = config.get("source_pins", {})
    input_lock_relative = str(sources.get("final344_input_lock", ""))
    require(input_lock_relative in source_pins, "final344 input-lock source pin declared")
    input_lock_path = root / input_lock_relative
    _assert_pin(input_lock_path, source_pins[input_lock_relative], "config source:final344_input_lock")
    input_lock = read_json(input_lock_path)
    require(input_lock.get("evaluation_scope") == "DEVELOPMENT_ONLY" and
            input_lock.get("evaluation_labels_read") is False,
            "final344 input lock must be label-free development data")
    locked_files = input_lock.get("files", {})
    require(TMDB_VOTE_METADATA_RELATIVE in locked_files,
            "final344 input lock pins REC-045 TMDB vote metadata")
    metadata_path = root / TMDB_VOTE_METADATA_RELATIVE
    _assert_pin(metadata_path, locked_files[TMDB_VOTE_METADATA_RELATIVE],
                "final344 locked REC-045 TMDB vote metadata")
    metadata = pd.read_parquet(metadata_path, columns=["movie_id", "tmdb_vote_count"])
    require(not metadata.movie_id.duplicated().any() and
            np.array_equal(metadata.movie_id.to_numpy(np.int64), catalog.movie_id.to_numpy(np.int64)),
            "REC-045 vote metadata/catalog movie axis")
    votes = metadata.tmdb_vote_count.to_numpy(np.float64)
    require(np.isfinite(votes).all() and (votes >= 0).all() and np.equal(votes, np.floor(votes)).all(),
            "REC-045 TMDB vote counts must be finite nonnegative integers")
    return metadata, {
        "final344_input_lock": pin(input_lock_path),
        "tmdb_vote_metadata": pin(metadata_path),
    }


def verify_final344_calibration_parent(
    config: Mapping[str, Any],
    root: Path = ROOT,
) -> dict[str, dict[str, Any]]:
    """Verify the prior calibration reproduction reference without parsing its values."""
    root = Path(root)
    input_lock_relative = str(config.get("sources", {}).get("final344_input_lock", ""))
    expected_input_lock = config.get("source_pins", {}).get(input_lock_relative)
    require(expected_input_lock is not None, "final344 input-lock pin for calibration parent")
    input_lock_path = root / input_lock_relative
    _assert_pin(input_lock_path, expected_input_lock, "final344 calibration input lock")
    seal_path = root / FINAL344_CALIBRATION_SEAL_RELATIVE
    calibration_path = root / FINAL344_CALIBRATION_RELATIVE
    seal = read_json(seal_path)
    require(seal.get("input_lock") == pin(input_lock_path),
            "final344 calibration seal/input-lock lineage")
    require(set(seal.get("files", {})) == {"final-calibration.json"},
            "final344 calibration seal exact file inventory")
    _assert_pin(calibration_path, seal["files"]["final-calibration.json"],
                "final344 calibration reference")
    execution = seal.get("execution", {})
    require(isinstance(execution, Mapping), "final344 calibration execution pins")
    for name, expected in execution.items():
        _assert_pin(root / str(name), expected, f"final344 calibration execution:{name}")
    return {
        "final344_calibration_seal": pin(seal_path),
        "final344_calibration": pin(calibration_path),
    }


def _resolve_sealed_path(name: str, artifact_dir: Path) -> Path:
    candidate = Path(name)
    if candidate.is_absolute():
        return candidate
    local = artifact_dir / candidate
    return local if local.exists() else ROOT / candidate


def verify_prediction_seal(artifact_dir: Path, seal_name: str = "prediction-seal.json") -> dict[str, Any]:
    """Verify every file listed by the producer before any label can be read."""
    artifact_dir = Path(artifact_dir).resolve()
    seal_path = artifact_dir / seal_name
    require(seal_path.is_file(), f"missing prediction seal: {seal_path}")
    seal = read_json(seal_path)
    require(seal.get("scope") == "DEVELOPMENT_ONLY" and seal.get("labels_opened") is False,
            "prediction artifact must be label-free development output")
    if "names" in seal:
        require(tuple(map(str, seal["names"])) == BASE_MODELS, "sealed model order")
    files = seal.get("files")
    require(isinstance(files, Mapping) and "predictions.npz" in files, "prediction seal must pin predictions.npz")
    checked: dict[str, dict[str, Any]] = {}
    for name, expected in sorted(files.items()):
        path = _resolve_sealed_path(str(name), artifact_dir)
        require(path.is_file(), f"sealed prediction file missing: {name}")
        _assert_pin(path, expected, str(name))
        checked[str(name)] = pin(path)
    # Producers may additionally expose path-keyed immutable parents.  Verify
    # entries that have the same explicit pin shape; descriptive parent
    # metadata is retained but is never mistaken for a filesystem path.
    parents_checked: dict[str, dict[str, Any]] = {}
    parents = seal.get("parents", {})
    if isinstance(parents, Mapping):
        for name, expected in sorted(parents.items()):
            if not (isinstance(expected, Mapping) and "sha256" in expected):
                continue
            path = _resolve_sealed_path(str(name), artifact_dir)
            require(path.is_file(), f"sealed parent missing: {name}")
            _assert_pin(path, expected, f"parent:{name}")
            parents_checked[str(name)] = pin(path)
    implementation = seal.get("implementation")
    if isinstance(implementation, Mapping):
        for name, expected in implementation.items():
            path = ROOT / str(name)
            require(path.is_file(), f"scoring implementation file missing: {name}")
            _assert_pin(path, expected, f"implementation:{name}")
    if seal.get("independent_review") is not None:
        prelabel_review = ROOT / "docs/recommendation/experiments/hybrid345/prelabel-code-review.json"
        require(prelabel_review.is_file(), "missing pre-label score-code review")
        _assert_pin(prelabel_review, seal["independent_review"], "prelabel-code-review.json")
    if artifact_dir == DEFAULT_ARTIFACT.resolve() and seal_name == "prediction-seal.json":
        from hybrid345_score import verify_predictions

        require(verify_predictions() == seal, "recursive producer-lineage verification drift")
    return {
        "seal": seal,
        "seal_pin": pin(seal_path),
        "files": checked,
        "parents": parents_checked,
        "path": str(seal_path),
    }


def evaluation_fingerprint(config_path: Path = DEFAULT_CONFIG) -> dict[str, dict[str, Any]]:
    paths = [
        Path(__file__),
        ROOT / "scripts/report_hybrid345.py",
        ROOT / "scripts/test_hybrid345_evaluate.py",
        ROOT / "docs/recommendation/experiments/hybrid345/DESIGN.md",
        Path(config_path),
        ROOT / "docs/recommendation/experiments/hybrid345/design-review.json",
    ]
    return {path.resolve().relative_to(ROOT.resolve()).as_posix(): pin(path) for path in paths}


def require_evaluation_review(config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    require(EVALUATION_REVIEW.is_file(), "independent evaluation-code review is required before labels")
    review = read_json(EVALUATION_REVIEW)
    require(review.get("status") == "PASS" and review.get("scope") == "DEVELOPMENT_ONLY",
            "evaluation-code review must PASS in development-only scope")
    require(review.get("fingerprint") == evaluation_fingerprint(config_path), "evaluation implementation changed after review")
    return review


def valid_stars(values: Iterable[float]) -> bool:
    y = np.asarray(values, dtype=np.float64)
    return bool(np.isfinite(y).all() and np.isin(y, np.arange(1, 11, dtype=float) / 2).all())


def affine_by_user(records: pd.DataFrame, min_users: int = 20, min_rows: int = 40) -> dict[str, Any]:
    """Fit a+b*x, b>=0, giving every user the same total weight.

    This intentionally matches final344's calibration estimator.
    """
    required = {"uid", "movie_id", "raw", "rating"}
    require(required.issubset(records.columns), "calibration columns")
    require(not records.duplicated(["uid", "movie_id"]).any(), "unique calibration user/movie rows")
    require(valid_stars(records.rating), "calibration labels are finite half-stars")
    require(np.isfinite(records.raw.to_numpy(float)).all(), "finite calibration predictions")
    users = int(records.uid.nunique())
    result: dict[str, Any] = {
        "users": users,
        "rows": int(len(records)),
        "a": None,
        "b": None,
        "variance": None,
        "covariance": None,
        "state": "INSUFFICIENT",
    }
    if users < min_users or len(records) < min_rows:
        return result
    weights = 1.0 / records.groupby("uid").uid.transform("size").to_numpy(float)
    weights /= weights.sum()
    x = records.raw.to_numpy(float)
    y = records.rating.to_numpy(float)
    mx, my = float(weights @ x), float(weights @ y)
    variance = float(weights @ ((x - mx) ** 2))
    covariance = float(weights @ ((x - mx) * (y - my)))
    require(np.isfinite([mx, my, variance, covariance]).all(), "finite calibration moments")
    slope = 0.0 if variance <= EPS else max(0.0, covariance / variance)
    return {
        **result,
        "a": my - slope * mx,
        "b": slope,
        "variance": variance,
        "covariance": covariance,
        "state": "CONSTANT" if slope == 0 else "AFFINE",
    }


def apply_affine(raw: np.ndarray, calibration: Mapping[str, Any]) -> np.ndarray:
    raw = np.asarray(raw, dtype=np.float64)
    result = np.full(raw.shape, np.nan, dtype=np.float64)
    if calibration.get("a") is not None and calibration.get("b") is not None:
        finite = np.isfinite(raw)
        result[finite] = float(calibration["a"]) + float(calibration["b"]) * raw[finite]
    return result


def stable_order(raw: np.ndarray, movie_ids: np.ndarray) -> np.ndarray:
    raw = np.asarray(raw, dtype=np.float64)
    movie_ids = np.asarray(movie_ids)
    require(raw.ndim == movie_ids.ndim == 1 and len(raw) == len(movie_ids), "ranking axes")
    require(np.isfinite(raw).all() and len(np.unique(movie_ids)) == len(movie_ids), "finite scores and unique movie IDs")
    return np.lexsort((movie_ids, -raw))


def ndcg_at(y: np.ndarray, raw: np.ndarray, movie_ids: np.ndarray, n: int) -> float:
    y, raw, movie_ids = np.asarray(y, float), np.asarray(raw, float), np.asarray(movie_ids)
    require(len(y) == len(raw) == len(movie_ids), "NDCG axes")
    if len(y) < n:
        return math.nan
    require(valid_stars(y), "NDCG half-star labels")
    order = stable_order(raw, movie_ids)
    gains = (y - 0.5) / 4.5
    discount = 1.0 / np.log2(np.arange(n, dtype=float) + 2.0)
    ideal = float(np.sort(gains)[::-1][:n] @ discount)
    return float(gains[order[:n]] @ discount / ideal) if ideal > 0 else math.nan


def top_quality(y: np.ndarray, raw: np.ndarray, movie_ids: np.ndarray, n: int, start: int = 0) -> dict[str, float]:
    y, raw, movie_ids = np.asarray(y, float), np.asarray(raw, float), np.asarray(movie_ids)
    require(0 <= start < n, "legal top/page boundary")
    result = {"stars": math.nan, "good": math.nan, "low": math.nan, "any_low": math.nan, "both_low": math.nan}
    if len(y) < n:
        return result
    order = stable_order(raw, movie_ids)
    shown = y[order[start:n]]
    require(valid_stars(shown), "top/page half-star labels")
    return {
        "stars": float(shown.mean()),
        "good": float((shown >= 4.0).mean()),
        "low": float((shown <= 2.0).mean()),
        "any_low": float((shown <= 2.0).any()),
        "both_low": float((shown <= 2.0).all()) if len(shown) == 2 else math.nan,
    }


def pairwise_accuracy(y: np.ndarray, raw: np.ndarray) -> float:
    """Concordance over unequal-rating pairs; score ties receive one half."""
    y, raw = np.asarray(y, float), np.asarray(raw, float)
    require(len(y) == len(raw) and np.isfinite(raw).all() and valid_stars(y), "pairwise axes")
    if len(y) < 2:
        return math.nan
    left, right = np.triu_indices(len(y), 1)
    dy = y[left] - y[right]
    keep = dy != 0
    if not keep.any():
        return math.nan
    product = dy[keep] * (raw[left][keep] - raw[right][keep])
    return float(np.where(product > 0, 1.0, np.where(product == 0, 0.5, 0.0)).mean())


def user_metric_bundle(
    y: np.ndarray,
    ranking_score: np.ndarray,
    error_score: np.ndarray,
    movie_ids: np.ndarray,
    top_ns: Sequence[int] = TOP_NS,
) -> dict[str, float]:
    y = np.asarray(y, float)
    ranking_score = np.asarray(ranking_score, float)
    error_score = np.asarray(error_score, float)
    require(len(y) == len(ranking_score) == len(error_score) == len(movie_ids), "metric axes")
    require(valid_stars(y) and np.isfinite(ranking_score).all() and np.isfinite(error_score).all(), "finite metric data")
    clipped = np.clip(error_score, 0.5, 5.0)
    error = clipped - y
    result: dict[str, float] = {
        "mse": float(np.mean(error ** 2)),
        "mae": float(np.mean(np.abs(error))),
        "bias": float(np.mean(error)),
        "pa": pairwise_accuracy(y, ranking_score),
    }
    for n in top_ns:
        result[f"ndcg{n}"] = ndcg_at(y, ranking_score, movie_ids, n)
        result.update({f"{key}{n}": value for key, value in top_quality(y, ranking_score, movie_ids, n).items()})
    return result


def paired_bootstrap(
    before: np.ndarray,
    after: np.ndarray,
    *,
    samples: int = 20_000,
    seed: int = 346,
    confidence: float,
    minimum_users: int = 30,
) -> dict[str, Any]:
    before, after = np.asarray(before, float), np.asarray(after, float)
    require(before.ndim == after.ndim == 1 and before.shape == after.shape, "paired bootstrap axes")
    require(np.isfinite(before).all() and np.isfinite(after).all(), "paired bootstrap finite values")
    delta = after - before
    result: dict[str, Any] = {
        "users": int(len(delta)),
        "before_mean": float(before.mean()) if len(before) else None,
        "after_mean": float(after.mean()) if len(after) else None,
        "delta": float(delta.mean()) if len(delta) else None,
        "ci_low": None,
        "ci_high": None,
        "confidence": float(confidence),
        "samples": int(samples),
        "seed": int(seed),
    }
    if len(delta) < minimum_users:
        return result
    rng = np.random.default_rng(seed)
    draws = np.empty(samples, dtype=np.float64)
    for start in range(0, samples, 256):
        stop = min(samples, start + 256)
        indices = rng.integers(len(delta), size=(stop - start, len(delta)))
        draws[start:stop] = delta[indices].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    result["ci_low"], result["ci_high"] = map(float, np.quantile(draws, [alpha, 1.0 - alpha]))
    return result


def _summary(metrics: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {"users": int(len(metrics))}
    for name in ("mse", "ndcg2", "stars2", "low2", "any_low2"):
        values = metrics[name].dropna() if name in metrics else pd.Series(dtype=float)
        result[name] = float(values.mean()) if len(values) else None
        result[name + "_users"] = int(len(values))
    return result


def _pareto_safe(candidate: Mapping[str, Any], baseline: Mapping[str, Any], safety: Mapping[str, float]) -> bool:
    needed = ("mse", "ndcg2", "stars2", "low2")
    if any(candidate.get(k) is None or baseline.get(k) is None for k in needed):
        return False
    pareto = candidate["mse"] <= baseline["mse"] + EPS and candidate["ndcg2"] >= baseline["ndcg2"] - EPS
    strict = candidate["mse"] < baseline["mse"] - EPS or candidate["ndcg2"] > baseline["ndcg2"] + EPS
    safe = (
        candidate["stars2"] >= baseline["stars2"] - float(safety["max_top2_stars_loss"]) - EPS
        and candidate["low2"] <= baseline["low2"] + float(safety["max_low2_increase"]) + EPS
    )
    return bool(pareto and strict and safe)


def select_hybrid_weight(weight_metrics: Mapping[float, Mapping[str, Any]], safety: Mapping[str, float]) -> dict[str, Any]:
    require(0.0 in weight_metrics, "hybrid baseline weight 0")
    baseline = weight_metrics[0.0]
    passed = [w for w in sorted(weight_metrics) if w > 0 and _pareto_safe(weight_metrics[w], baseline, safety)]
    choice = float(passed[0]) if passed else 0.0
    return {
        "content_weight": choice,
        "reason": "SMALLEST_POSITIVE_PARETO_SAFE" if passed else "NO_POSITIVE_WEIGHT_PASSED_KEEP_ALS",
        "baseline": dict(baseline),
        "candidates": {str(w): dict(weight_metrics[w]) for w in sorted(weight_metrics)},
    }


def select_cold_head(candidate_metrics: Mapping[str, Mapping[str, Any]], safety: Mapping[str, float]) -> dict[str, Any]:
    require("GBT120_s339" in candidate_metrics, "cold incumbent GBT120_s339")
    baseline = candidate_metrics["GBT120_s339"]
    passed = [name for name in COLD_CHALLENGERS if _pareto_safe(candidate_metrics[name], baseline, safety)]
    order = {name: index for index, name in enumerate(COLD_CHALLENGERS)}
    passed.sort(key=lambda name: (-float(candidate_metrics[name]["ndcg2"]), float(candidate_metrics[name]["mse"]), order[name]))
    choice = passed[0] if passed else "GBT120_s339"
    return {
        "head": choice,
        "reason": "BEST_PARETO_SAFE_CHALLENGER" if passed else "NO_CHALLENGER_PASSED_KEEP_GBT120_s339",
        "incumbent": "GBT120_s339",
        "eligible_challengers": passed,
        "candidates": {name: dict(candidate_metrics[name]) for name in sorted(candidate_metrics)},
    }


def _normalise_names(values: np.ndarray) -> list[str]:
    result = []
    for value in np.asarray(values).tolist():
        result.append(value.decode("utf-8") if isinstance(value, bytes) else str(value))
    return result


def load_prediction_matrix(artifact_dir: Path, expected_rows: int) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    bundle = np.load(Path(artifact_dir) / "predictions.npz", allow_pickle=False)
    require({"names", "predictions", "availability"}.issubset(bundle.files), "prediction arrays names/predictions/availability")
    names = _normalise_names(bundle["names"])
    predictions = np.asarray(bundle["predictions"], dtype=np.float64)
    availability = np.asarray(bundle["availability"], dtype=bool)
    require(len(names) == len(set(names)) and set(BASE_MODELS).issubset(names), "unique required model names")
    require(predictions.shape == availability.shape == (expected_rows, len(names)), "fixed prediction row/model axes")
    require(np.array_equal(np.isfinite(predictions), availability), "availability is exactly the finite-score mask")
    return (
        {name: predictions[:, names.index(name)].copy() for name in BASE_MODELS},
        {name: availability[:, names.index(name)].copy() for name in BASE_MODELS},
    )


def build_row_axis(
    contexts: Sequence[Mapping[str, Any]],
    catalog: pd.DataFrame,
    caps: Sequence[int],
    origin_timestamp: int | None = None,
) -> pd.DataFrame:
    movie_ids = catalog.movie_id.to_numpy()
    require(len(movie_ids) == len(np.unique(movie_ids)) and np.all(movie_ids[:-1] < movie_ids[1:]), "sorted unique catalog axis")
    rows: list[pd.DataFrame] = []
    cursor = 0
    seen: set[tuple[int, int]] = set()
    for context_id, c in enumerate(contexts):
        uid, cap, h = int(c["uid"]), int(c["cap"]), int(c["h"])
        require(cap in caps and 0 <= h <= cap and (uid, cap) not in seen, "unique legal user/cap context")
        ei = np.asarray(c["ei"], dtype=np.int64)
        oi = np.asarray(c.get("oi", []), dtype=np.int64)
        require(c["start"] == cursor and c["stop"] == cursor + len(ei), "contiguous prediction rows")
        require(len(ei) and len(np.unique(ei)) == len(ei) and ((ei >= 0) & (ei < len(catalog))).all(), "legal unique target indices")
        require(len(oi) == h and len(np.unique(oi)) == len(oi) and ((oi >= 0) & (oi < len(catalog))).all(), "legal unique input indices")
        require(not np.intersect1d(ei, oi).size and valid_stars(c.get("stars", [])), "disjoint targets and half-star inputs")
        require(bool(catalog.released_at_origin.to_numpy(bool)[ei].all()), "targets released by origin")
        if "viewed" in c:
            require(not np.intersect1d(ei, np.asarray(c["viewed"], dtype=np.int64)).size, "targets exclude viewed movies")
        if origin_timestamp is not None:
            require(len(c.get("input_timestamps", [])) == h and
                    all(int(value) < origin_timestamp for value in c["input_timestamps"]), "strict-past inputs")
            require(len(c.get("target_timestamps", [])) == len(ei) and
                    all(int(value) >= origin_timestamp for value in c["target_timestamps"]), "post-origin targets")
        rows.append(pd.DataFrame({
            "row_id": np.arange(cursor, cursor + len(ei), dtype=np.int64),
            "context_id": context_id,
            "uid": uid,
            "cap": cap,
            "h": h,
            "movie_index": ei,
            "movie_id": movie_ids[ei],
        }))
        cursor += len(ei)
        seen.add((uid, cap))
    frame = pd.concat(rows, ignore_index=True)
    by_key = {(int(c["uid"]), int(c["cap"])): c for c in contexts}
    users = sorted({uid for uid, _ in seen})
    require(set(by_key) == {(uid, int(cap)) for uid in users for cap in caps}, "every context user has every cap")
    for uid in users:
        base = by_key[(uid, 0)]
        require(int(base["h"]) == 0, "cap0 is empty input")
        for cap in caps:
            current = by_key[(uid, int(cap))]
            require(current["ei"] == base["ei"], "same observed targets across caps")
            if "target_timestamps" in current:
                require(current["target_timestamps"] == base["target_timestamps"], "same target times across caps")
    return frame


def movie_groups(catalog: pd.DataFrame, partition: pd.DataFrame, expected: Mapping[str, int] | None = None) -> dict[str, np.ndarray]:
    require(np.array_equal(catalog.movie_id.to_numpy(), partition.movie_id.to_numpy()), "catalog/partition movie axis")
    blocked = catalog.blocked.to_numpy(bool)
    support = catalog.train_count.to_numpy(np.int64)
    p = partition.partition.astype(str).to_numpy()
    require(np.array_equal(blocked, np.isin(p, ["V", "E"])), "C agrees with V+E partition")
    groups = {
        "ALL": np.ones(len(catalog), dtype=bool),
        "W_DIRECT": (~blocked) & (support > 0),
        "C": blocked,
        "V": p == "V",
        "E": p == "E",
        "NATURAL_ZERO": (~blocked) & (support == 0),
    }
    require(int(groups["W_DIRECT"].sum() + groups["C"].sum() + groups["NATURAL_ZERO"].sum()) == len(catalog),
            "primary movie groups partition catalog")
    require(np.array_equal(groups["C"], groups["V"] | groups["E"]) and not (groups["V"] & groups["E"]).any(),
            "V/E exactly partition C")
    if expected:
        for name in ("W_DIRECT", "C", "NATURAL_ZERO"):
            require(int(groups[name].sum()) == int(expected[name]), f"fixed {name} movie count")
    return groups


def load_roles(path: Path, expected: Mapping[str, int]) -> dict[int, str]:
    frame = pd.read_csv(path)
    require({"uid", "role"}.issubset(frame.columns) and not frame.uid.duplicated().any(), "one role per user")
    roles = dict(zip(frame.uid.astype(int), frame.role.astype(str)))
    require(set(roles.values()) == set(expected), "known calibration/comparison roles")
    for role, count in expected.items():
        require(sum(value == role for value in roles.values()) == int(count), f"fixed {role} user count")
    return roles


def attach_roles_and_groups(row_axis: pd.DataFrame, roles: Mapping[int, str], groups: Mapping[str, np.ndarray]) -> pd.DataFrame:
    result = row_axis.copy()
    result["role"] = result.uid.map(roles)
    require(result.role.notna().all(), "every context user has a role")
    movie_index = result.movie_index.to_numpy(int)
    primary = np.full(len(result), "", dtype=object)
    for name in ("W_DIRECT", "C", "NATURAL_ZERO"):
        primary[groups[name][movie_index]] = name
    require((primary != "").all(), "every target has one primary movie group")
    result["primary_group"] = primary
    result["cold_partition"] = np.where(groups["V"][movie_index], "V", np.where(groups["E"][movie_index], "E", ""))
    return result


def attach_movie_diagnostics(
    row_axis: pd.DataFrame,
    catalog: pd.DataFrame,
    metadata: pd.DataFrame,
    texts: pd.DataFrame,
) -> pd.DataFrame:
    require(np.array_equal(metadata.movie_id.to_numpy(), catalog.movie_id.to_numpy()), "metadata/catalog movie axis")
    require(np.array_equal(texts.movie_id.to_numpy(), catalog.movie_id.to_numpy()), "texts/catalog movie axis")
    index = row_axis.movie_index.to_numpy(int)
    result = row_axis.copy()
    support = catalog.train_count.to_numpy(np.int64)[index]
    votes = metadata.tmdb_vote_count.to_numpy(float)[index]
    dates = pd.to_datetime(texts.release_date, format="%Y-%m-%d", errors="coerce", utc=True)
    years = dates.dt.year.to_numpy(float)[index]
    result["train_count"] = support
    result["support_band"] = np.select(
        [support == 0, (support >= 1) & (support <= 9), (support >= 10) & (support <= 49)],
        ["0", "1_9", "10_49"], default="50_PLUS",
    )
    result["release_year"] = years
    result["release_band"] = np.select(
        [~np.isfinite(years), years < 1980, years < 2000, years < 2010, years < 2020, years < 2023],
        ["MISSING", "PRE1980", "1980_1999", "2000_2009", "2010_2019", "2020_2022"],
        default="2023_PLUS",
    )
    result["tmdb_vote_count"] = votes
    result["vote_band"] = np.select(
        [~np.isfinite(votes) | (votes <= 0), votes <= 1, votes <= 9, votes <= 49, votes <= 499],
        ["ZERO_OR_MISSING", "ONE", "2_9", "10_49", "50_499"], default="500_PLUS",
    )
    return result


def load_truth(labels_path: Path, row_axis: pd.DataFrame) -> tuple[np.ndarray, dict[str, Any]]:
    label_pin = pin(labels_path)
    require(label_pin["sha256"] == LABEL_SHA256, "frozen development label identity")
    labels = pd.read_parquet(labels_path)
    require({"uid", "movie_id", "rating"}.issubset(labels.columns), "label columns")
    require(not labels.duplicated(["uid", "movie_id"]).any() and valid_stars(labels.rating), "unique half-star labels")
    indexed = labels.set_index(["uid", "movie_id"]).rating
    keys = pd.MultiIndex.from_arrays([row_axis.uid.to_numpy(int), row_axis.movie_id.to_numpy()])
    truth = indexed.reindex(keys).to_numpy(float)
    require(valid_stars(truth), "every fixed target has one half-star label")
    return truth, label_pin


def fit_calibrations(
    row_axis: pd.DataFrame,
    truth: np.ndarray,
    raw: Mapping[str, np.ndarray],
    available: Mapping[str, np.ndarray],
    caps: Sequence[int],
    *,
    min_users: int = 20,
    min_rows: int = 40,
) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    ids = row_axis.movie_id.to_numpy()
    for model in BASE_MODELS:
        for cap in caps:
            mask = (
                row_axis.role.eq("calibration").to_numpy()
                & row_axis.cap.eq(cap).to_numpy()
                & available[model]
            )
            frame = pd.DataFrame({
                "uid": row_axis.uid.to_numpy(int)[mask],
                "movie_id": ids[mask],
                "raw": raw[model][mask],
                "rating": truth[mask],
            })
            result[(model, int(cap))] = affine_by_user(frame, min_users=min_users, min_rows=min_rows)
    return result


def calibrated_vectors(
    row_axis: pd.DataFrame,
    raw: Mapping[str, np.ndarray],
    calibrations: Mapping[tuple[str, int], Mapping[str, Any]],
    caps: Sequence[int],
) -> dict[str, np.ndarray]:
    result: dict[str, np.ndarray] = {}
    for model, vector in raw.items():
        values = np.full(len(row_axis), np.nan, dtype=np.float64)
        for cap in caps:
            mask = row_axis.cap.eq(cap).to_numpy() & np.isfinite(vector)
            values[mask] = apply_affine(vector[mask], calibrations[(model, int(cap))])
        result[model] = values
    return result


def reproduce_final344_calibration(
    calibrations: Mapping[tuple[str, int], Mapping[str, Any]],
    path: Path,
    caps: Sequence[int],
    parent: Mapping[str, Mapping[str, Any]],
    tolerance: float = 1e-12,
) -> dict[str, Any]:
    seal_path = path.parent / "final-calibration-seal.json"
    _assert_pin(seal_path, parent["final344_calibration_seal"], "final344 calibration seal")
    _assert_pin(path, parent["final344_calibration"], "final344 calibration parent")
    seal = read_json(seal_path)
    require("final-calibration.json" in seal.get("files", {}), "final344 calibration seal entry")
    _assert_pin(path, seal["files"]["final-calibration.json"], "final344/final-calibration.json")
    payload = read_json(path)
    rows = {(str(r["model"]), int(r["cap"])): r for r in payload["fits"]}
    checks = []
    for current in (*FM_MODELS, *GBT_MODELS):
        previous = current
        for cap in caps:
            now = calibrations[(current, int(cap))]
            old = rows[(previous, int(cap))]
            differences = {}
            for field in ("a", "b", "variance", "covariance"):
                if now.get(field) is None or old.get(field) is None:
                    differences[field] = 0.0 if now.get(field) is old.get(field) else math.inf
                else:
                    differences[field] = abs(float(now[field]) - float(old[field]))
            passed = now["users"] == old["users"] and now["rows"] == old["rows"] and max(differences.values()) <= tolerance
            checks.append({"model": current, "cap": int(cap), "passed": bool(passed), "differences": differences,
                           "users": now["users"], "rows": now["rows"]})
    require(all(row["passed"] for row in checks), "all six FM150/GBT120 calibrations must reproduce final344")
    return {"status": "PASS", "tolerance": tolerance, "source": str(path), "source_pin": pin(path), "checks": checks}


def _context_metrics(
    row_axis: pd.DataFrame,
    truth: np.ndarray,
    rank_score: np.ndarray,
    error_score: np.ndarray,
    eligibility: np.ndarray,
    *,
    role: str,
    cap: int,
    group: str,
) -> pd.DataFrame:
    selected = row_axis.role.eq(role).to_numpy() & row_axis.cap.eq(cap).to_numpy()
    if group != "ALL":
        selected &= (row_axis.cold_partition.eq(group).to_numpy() if group in {"V", "E"}
                     else row_axis.primary_group.eq(group).to_numpy())
    rows = []
    for uid in sorted(row_axis.loc[selected, "uid"].unique()):
        mask = selected & row_axis.uid.eq(uid).to_numpy()
        total = int(mask.sum())
        complete = total > 0 and bool(eligibility[mask].all())
        h_values = row_axis.loc[mask, "h"].unique()
        require(len(h_values) == 1, "one input count per user/cap context")
        row: dict[str, Any] = {"uid": int(uid), "h": int(h_values[0]), "targets_total": total,
                               "targets_scored": int(eligibility[mask].sum()), "complete": complete}
        if complete:
            row.update(user_metric_bundle(truth[mask], rank_score[mask], error_score[mask], row_axis.movie_id.to_numpy()[mask]))
        else:
            for name in ("mse", "mae", "bias", "pa"):
                row[name] = math.nan
            for n in TOP_NS:
                for name in ("ndcg", "stars", "good", "low", "any_low", "both_low"):
                    row[f"{name}{n}"] = math.nan
        rows.append(row)
    return pd.DataFrame(rows)


def _selection_metric_table(
    row_axis: pd.DataFrame,
    truth: np.ndarray,
    candidates: Mapping[str, tuple[np.ndarray, np.ndarray]],
    common_eligibility: np.ndarray,
    *,
    group: str,
    cap: int,
) -> tuple[dict[str, dict[str, Any]], pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for name, (rank_score, error_score) in candidates.items():
        frame = _context_metrics(row_axis, truth, rank_score, error_score, common_eligibility,
                                 role="calibration", cap=cap, group=group)
        frame.insert(1, "candidate", name)
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    common_uids: set[int] | None = None
    for name in candidates:
        eligible = set(combined.loc[combined.candidate.eq(name) & combined.mse.notna() & combined.ndcg2.notna(), "uid"].astype(int))
        common_uids = eligible if common_uids is None else common_uids & eligible
    common = sorted(common_uids or set())
    combined["common_selection_uid"] = combined.uid.isin(common)
    summaries = {name: _summary(combined[combined.candidate.eq(name) & combined.common_selection_uid]) for name in candidates}
    require(len({summary["users"] for summary in summaries.values()}) == 1, "one common UID denominator for selection")
    return summaries, combined


def choose_policies(
    row_axis: pd.DataFrame,
    truth: np.ndarray,
    raw: Mapping[str, np.ndarray],
    available: Mapping[str, np.ndarray],
    calibrated: Mapping[str, np.ndarray],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    cap = int(config["primary_cap"])
    safety = config["safety"]
    hybrid_rows: list[pd.DataFrame] = []
    hybrid_metrics: dict[float, dict[str, Any]] = {}
    common_warm = available["ALS"] & available["QWEN_DIRECT"]
    for weight in map(float, config["hybrid_content_weights"]):
        score = (calibrated["ALS"].copy() if weight == 0.0 else
                 (1.0 - weight) * calibrated["ALS"] + weight * calibrated["QWEN_DIRECT"])
        if weight == 0.0:
            require(np.array_equal(score[common_warm], calibrated["ALS"][common_warm]), "weight zero exactly reproduces calibrated ALS")
        summaries, frame = _selection_metric_table(
            row_axis, truth, {str(weight): (score, score)}, common_warm,
            group="W_DIRECT", cap=cap,
        )
        hybrid_metrics[weight] = summaries[str(weight)]
        frame["selection"] = "WARM_WEIGHT"
        frame["weight"] = weight
        hybrid_rows.append(frame)
    warm = select_hybrid_weight(hybrid_metrics, safety)

    common_cold = np.logical_and.reduce([available[name] for name in COLD_SELECTION_MODELS])
    cold_candidates = {name: (calibrated[name], calibrated[name]) for name in COLD_SELECTION_MODELS}
    cold_metrics, cold_frame = _selection_metric_table(
        row_axis, truth, cold_candidates, common_cold, group="C", cap=cap,
    )
    cold = select_cold_head(cold_metrics, safety)
    cold_frame["selection"] = "COLD_HEAD"
    cold_frame["weight"] = math.nan
    selection_rows = pd.concat(hybrid_rows + [cold_frame], ignore_index=True)
    selection = {
        "scope": "DEVELOPMENT_ONLY",
        "roles_used": ["calibration"],
        "comparison_labels_used": False,
        "cap": cap,
        "warm": warm,
        "cold": cold,
        "common_denominator_policy": "ALL_DECLARED_MODELS_SCORE_EVERY_TARGET_OF_THE_USER_GROUP",
        "service_adoption": False,
    }
    return selection, selection_rows


def add_derived_models(
    row_axis: pd.DataFrame,
    raw: dict[str, np.ndarray],
    available: dict[str, np.ndarray],
    calibrated: Mapping[str, np.ndarray],
    selection: Mapping[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]:
    weight = float(selection["warm"]["content_weight"])
    hybrid = np.full(len(row_axis), np.nan, dtype=np.float64)
    if weight == 0.0:
        hybrid_available = available["ALS"].copy()
        hybrid[hybrid_available] = calibrated["ALS"][hybrid_available]
        require(np.array_equal(hybrid[hybrid_available], calibrated["ALS"][hybrid_available]),
                "selected weight zero exactly reproduces ALS on its full availability")
    else:
        hybrid_available = available["ALS"] & available["QWEN_DIRECT"]
        hybrid[hybrid_available] = (
            (1.0 - weight) * calibrated["ALS"][hybrid_available]
            + weight * calibrated["QWEN_DIRECT"][hybrid_available]
        )
    raw["ALS_QWEN"] = hybrid
    available["ALS_QWEN"] = hybrid_available

    cold_name = str(selection["cold"]["head"])
    router = np.full(len(row_axis), np.nan, dtype=np.float64)
    source = np.full(len(row_axis), "UNAVAILABLE", dtype=object)
    personalized = row_axis.h.gt(0).to_numpy()
    user_factor_available = available["ALS_C2F"]
    warm_item = row_axis.primary_group.eq("W_DIRECT").to_numpy()
    cold_item = ~warm_item
    warm_mask = personalized & user_factor_available & warm_item & hybrid_available
    router[warm_mask] = hybrid[warm_mask]
    source[warm_mask] = "WARM:" + ("ALS" if weight == 0 else "ALS_QWEN")
    cold_mask = personalized & user_factor_available & cold_item & available[cold_name]
    router[cold_mask] = calibrated[cold_name][cold_mask]
    source[cold_mask] = "COLD:" + cold_name
    fallback = personalized & ~np.isfinite(router) & available["GBT120_s339"]
    router[fallback] = calibrated["GBT120_s339"][fallback]
    source[fallback] = "FALLBACK:GBT120_s339"
    require(np.isfinite(router[personalized]).all(), "router fail-closed: GBT120_s339 fallback unavailable")
    raw["ROUTER_s339"] = router
    available["ROUTER_s339"] = np.isfinite(router)
    derived_calibrated = dict(calibrated)
    derived_calibrated["ALS_QWEN"] = hybrid.copy()
    derived_calibrated["ROUTER_s339"] = router.copy()
    return raw, available, {**derived_calibrated, "ROUTER_SOURCE": source}


def build_evaluation_tables(
    row_axis: pd.DataFrame,
    truth: np.ndarray,
    native_raw: Mapping[str, np.ndarray],
    rank_scores: Mapping[str, np.ndarray],
    available: Mapping[str, np.ndarray],
    error_scores: Mapping[str, np.ndarray],
    caps: Sequence[int],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    users: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    error_frames: list[pd.DataFrame] = []
    ids = row_axis.movie_id.to_numpy()
    comparison = row_axis.role.eq("comparison").to_numpy()
    contexts = [part for _, part in row_axis.loc[comparison].groupby("context_id", sort=True)]
    require(set(map(int, row_axis.loc[comparison, "cap"].unique())) == set(map(int, caps)), "comparison has every cap")
    for model in (*BASE_MODELS, *DERIVED_MODELS):
        native = native_raw[model]
        rank = rank_scores[model]
        err = error_scores[model]
        av = available[model]
        diagnostic_columns = [name for name in ("train_count", "support_band", "release_year", "release_band",
                                                 "tmdb_vote_count", "vote_band") if name in row_axis]
        frame = row_axis.loc[comparison, ["row_id", "context_id", "uid", "cap", "h", "movie_id",
                                                  "primary_group", "cold_partition", *diagnostic_columns]].copy()
        mask = comparison
        frame["model"] = model
        frame["rating"] = truth[mask]
        frame["available"] = av[mask]
        frame["raw"] = native[mask]
        frame["ranking_score"] = rank[mask]
        frame["calibrated_raw"] = err[mask]
        frame["calibrated"] = np.clip(err[mask], 0.5, 5.0)
        frame["error"] = frame.calibrated - frame.rating
        frame["se"] = frame.error ** 2
        frame["ae"] = frame.error.abs()
        error_frames.append(frame)
        for context in contexts:
            context_indices = context.index.to_numpy(dtype=np.int64)
            uid = int(context.uid.iloc[0])
            cap = int(context.cap.iloc[0])
            h = int(context.h.iloc[0])
            require(context.uid.nunique() == context.cap.nunique() == context.h.nunique() == 1, "one user/cap/h per context")
            for group in GROUPS:
                if group == "ALL":
                    indices = context_indices
                elif group in {"V", "E"}:
                    indices = context.index[context.cold_partition.eq(group)].to_numpy(dtype=np.int64)
                else:
                    indices = context.index[context.primary_group.eq(group)].to_numpy(dtype=np.int64)
                if not len(indices):
                    continue
                total, scored = int(len(indices)), int(av[indices].sum())
                complete = bool(av[indices].all())
                record: dict[str, Any] = {"model": model, "uid": uid, "cap": cap, "group": group, "h": h,
                                           "targets_total": total, "targets_scored": scored, "complete": complete}
                if complete:
                    record.update(user_metric_bundle(truth[indices], rank[indices], err[indices], ids[indices]))
                else:
                    for name in ("mse", "mae", "bias", "pa"):
                        record[name] = math.nan
                    for n in TOP_NS:
                        for name in ("ndcg", "stars", "good", "low", "any_low", "both_low"):
                            record[f"{name}{n}"] = math.nan
                users.append(record)
                for end in (2, 4, 6):
                    values = {"stars": math.nan, "good": math.nan, "low": math.nan,
                              "any_low": math.nan, "both_low": math.nan}
                    if complete:
                        values = top_quality(truth[indices], rank[indices], ids[indices], end, end - 2)
                    pages.append({"model": model, "uid": uid, "cap": cap, "group": group, "h": h,
                                  "start": end - 2, "end": end, "targets_total": total,
                                  "targets_scored": scored, "complete": complete, **values})
    user_frame = pd.DataFrame(users)
    # Derive h_group from h itself; the explicit assignment above is retained
    # only to make malformed synthetic context axes fail loudly here.
    user_frame["h_group"] = np.where(user_frame.h.gt(0), "H_POSITIVE", "H_ZERO")
    page_frame = pd.DataFrame(pages)
    page_frame["h_group"] = np.where(page_frame.h.gt(0), "H_POSITIVE", "H_ZERO")
    return user_frame, page_frame, pd.concat(error_frames, ignore_index=True)


def summarize_user_metrics(users: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    identifiers = {"model", "uid", "cap", "group", "h", "h_group", "targets_total", "targets_scored", "complete"}
    metrics = [name for name in users.columns if name not in identifiers]
    summaries, denominators = [], []
    for (model, cap, group, h_group), data in users.groupby(["model", "cap", "group", "h_group"], sort=True):
        denominators.append({"model": model, "cap": cap, "group": group, "h_group": h_group,
                             "user_contexts": len(data), "complete_user_contexts": int(data.complete.sum()),
                             "target_rows": int(data.targets_total.sum()), "scored_rows": int(data.targets_scored.sum())})
        for metric in metrics:
            values = data[metric].dropna()
            summaries.append({"model": model, "cap": cap, "group": group, "h_group": h_group,
                              "metric": metric, "valid_users": len(values), "user_contexts": len(data),
                              "mean": float(values.mean()) if len(values) else math.nan})
    return pd.DataFrame(summaries), pd.DataFrame(denominators)


def summarize_pages(pages: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for keys, data in pages.groupby(["model", "cap", "group", "h_group", "start", "end"], sort=True):
        model, cap, group, h_group, start, end = keys
        for metric in ("stars", "good", "low", "any_low", "both_low"):
            values = data[metric].dropna()
            rows.append({"model": model, "cap": cap, "group": group, "h_group": h_group, "start": start, "end": end,
                         "metric": metric, "valid_users": len(values), "user_contexts": len(data),
                         "mean": float(values.mean()) if len(values) else math.nan})
    return pd.DataFrame(rows)


def summarize_diagnostics(errors: pd.DataFrame) -> pd.DataFrame:
    """Descriptive user-macro calibrated MSE by the frozen metadata bands."""
    data = errors[errors.cap.eq(10) & errors.h.gt(0) & errors.available]
    rows: list[dict[str, Any]] = []
    dimensions = {
        "cold_partition": ["V", "E"],
        "support_band": ["0", "1_9", "10_49", "50_PLUS"],
        "release_band": ["MISSING", "PRE1980", "1980_1999", "2000_2009", "2010_2019", "2020_2022", "2023_PLUS"],
        "vote_band": ["ZERO_OR_MISSING", "ONE", "2_9", "10_49", "50_499", "500_PLUS"],
    }
    for dimension, levels in dimensions.items():
        if dimension not in data:
            continue
        for model in (*BASE_MODELS, *DERIVED_MODELS):
            model_rows = data[data.model.eq(model)]
            for level in levels:
                current = model_rows[model_rows[dimension].eq(level)]
                per_user = current.groupby("uid", sort=True).se.mean()
                rows.append({"model": model, "dimension": dimension, "level": level,
                             "users": int(len(per_user)), "rows": int(len(current)),
                             "movies": int(current.movie_id.nunique()),
                             "user_macro_mse": float(per_user.mean()) if len(per_user) else math.nan})
    return pd.DataFrame(rows)


def average_seed_metrics(frame: pd.DataFrame, *, page: bool = False) -> pd.DataFrame:
    """Average per-user metrics across three seeds, never prediction scores."""
    require("h_group" in frame.columns, "seed metrics retain input-presence denominator")
    keys = ["uid", "cap", "group", "h", "h_group"]
    constants = ["targets_total", "targets_scored", "complete"]
    if page:
        keys += ["start", "end"]
    identity = set(keys + constants + ["model"])
    numeric = [name for name in frame.columns if name not in identity and pd.api.types.is_numeric_dtype(frame[name])]
    rows: list[dict[str, Any]] = []
    for names, output_name in ((FM_MODELS, "FM150_SEED_MEAN"), (GBT_MODELS, "GBT120_SEED_MEAN")):
        selected = frame[frame.model.isin(names)]
        for _, group in selected.groupby(keys, sort=True, dropna=False):
            require(set(group.model) == set(names) and len(group) == len(SEEDS), "exact three-seed metric group")
            for column in constants:
                require(group[column].nunique(dropna=False) == 1, "fixed denominator across seeds")
            first = group.iloc[0]
            row = {column: first[column] for column in keys + constants}
            row["model"] = output_name
            for column in numeric:
                values = group[column].to_numpy(float)
                row[column] = float(values.mean()) if np.isfinite(values).all() else math.nan
            rows.append(row)
    return pd.DataFrame(rows, columns=frame.columns)


EXPECTED_FORMAL_CONTRASTS = (
    ("warm", "W_DIRECT", "ALS_QWEN", "ALS", "mse", "lower", 0.975),
    ("warm", "W_DIRECT", "ALS_QWEN", "ALS", "ndcg2", "higher", 0.975),
    ("cold", "C", "QWEN_DIRECT", "STRUCTURED_DIRECT", "mse", "lower", 0.99375),
    ("cold", "C", "QWEN_DIRECT", "STRUCTURED_DIRECT", "ndcg2", "higher", 0.99375),
    ("cold", "C", "ALS_C2F", "QWEN_DIRECT", "mse", "lower", 0.99375),
    ("cold", "C", "ALS_C2F", "QWEN_DIRECT", "ndcg2", "higher", 0.99375),
    ("cold", "C", "ALS_C2F", "FM150_SEED_MEAN", "mse", "lower", 0.99375),
    ("cold", "C", "ALS_C2F", "FM150_SEED_MEAN", "ndcg2", "higher", 0.99375),
    ("cold", "C", "ALS_C2F", "GBT120_SEED_MEAN", "mse", "lower", 0.99375),
    ("cold", "C", "ALS_C2F", "GBT120_SEED_MEAN", "ndcg2", "higher", 0.99375),
    ("router", "ALL", "ROUTER_s339", "FM150_SEED_MEAN", "mse", "lower", 0.9875),
    ("router", "ALL", "ROUTER_s339", "FM150_SEED_MEAN", "ndcg2", "higher", 0.9875),
    ("router", "ALL", "ROUTER_s339", "GBT120_SEED_MEAN", "mse", "lower", 0.9875),
    ("router", "ALL", "ROUTER_s339", "GBT120_SEED_MEAN", "ndcg2", "higher", 0.9875),
)


def validate_config_contract(config: Mapping[str, Any]) -> None:
    declared = tuple((str(r["family"]), str(r["group"]), str(r["after"]), str(r["before"]),
                      str(r["metric"]), str(r["better"]), float(r["confidence"]))
                     for r in config["formal_contrasts"])
    require(declared == EXPECTED_FORMAL_CONTRASTS, "exact frozen 14 formal contrasts and order")
    require(config["bootstrap"] == {
        "samples": 20000,
        "seed": 346,
        "minimum_users": 30,
        "unit": "USER",
        "pairing": "COMMON_FINITE_UID_PER_CONTRAST_METRIC",
        "uid_order": "ASCENDING",
        "shared_resamples_within_family": False,
        "seed_derivation": "UINT64_BIG_ENDIAN_FIRST8_SHA256_UTF8(hybrid345-bootstrap-v1|346|family|after|before|metric)",
        "generator": "numpy.random.Generator(PCG64)",
    }, "frozen bootstrap contract")
    require(config["decision_labels"] == ["ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM", "TRADEOFF",
            "NO_CLEAR_DIFFERENCE", "DETECTED_HARM", "DESCRIPTIVE_SMALL_N"], "frozen decision labels")
    require([(row["when"], row["label"]) for row in config["decision_precedence"]] == [
        ("COMMON_UID_LT_30", "DESCRIPTIVE_SMALL_N"),
        ("FAVORABLE_CI_AND_HARMFUL_CI", "TRADEOFF"),
        ("SAFETY_FAIL", "DETECTED_HARM"),
        ("HARMFUL_CI_AND_NO_FAVORABLE_CI", "DETECTED_HARM"),
        ("AT_LEAST_ONE_FAVORABLE_CI_AND_NO_HARMFUL_CI_AND_SAFETY_PASS", "ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM"),
        ("NO_FAVORABLE_CI_AND_NO_HARMFUL_CI", "NO_CLEAR_DIFFERENCE"),
    ], "frozen decision precedence")
    require(tuple(config["selection"]["cold"]["candidates"]) == COLD_CHALLENGERS and
            config["selection"]["cold"]["incumbent"] == "GBT120_s339", "frozen cold selection candidates")
    require(config["calibration"]["weighting"] == "USER_EQUAL_EACH_USER_TOTAL_WEIGHT_1_THEN_NORMALIZE" and
            config["calibration"]["secondary_calibrator"] is False, "frozen calibration semantics")


def _metric_pair(users: pd.DataFrame, before: str, after: str, group: str, metric: str, cap: int) -> pd.DataFrame:
    subset = users[users.cap.eq(cap) & users.h.gt(0) & users.group.eq(group)]
    pivot = subset.pivot(index="uid", columns="model", values=metric).sort_index()
    require(before in pivot.columns and after in pivot.columns, f"contrast models: {before}, {after}")
    return pivot[[before, after]].dropna()


def bootstrap_seed(configured_seed: int, family: str, after: str, before: str, metric: str) -> int:
    payload = f"hybrid345-bootstrap-v1|{configured_seed}|{family}|{after}|{before}|{metric}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)


def formal_contrasts(users: pd.DataFrame, config: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run the exact config-declared 2/8/4 contrasts with independent streams."""
    cap = int(config["primary_cap"])
    samples = int(config["bootstrap"]["samples"])
    minimum = int(config["bootstrap"]["minimum_users"])
    declared = tuple((str(r["family"]), str(r["group"]), str(r["after"]), str(r["before"]),
                      str(r["metric"]), str(r["better"]), float(r["confidence"]))
                     for r in config["formal_contrasts"])
    require(declared == EXPECTED_FORMAL_CONTRASTS, "exact frozen 14 formal contrasts and order")
    require(config["bootstrap"]["shared_resamples_within_family"] is False, "independent contrast bootstrap streams")
    contrast_rows: list[dict[str, Any]] = []
    paired_rows: list[dict[str, Any]] = []
    for family, group, after, before, metric, better, confidence in declared:
        pair = _metric_pair(users, before, after, group, metric, cap).sort_index()
        uids = pair.index.to_numpy(dtype=np.int64)
        require(np.array_equal(uids, np.sort(uids)) and len(np.unique(uids)) == len(uids), "ascending unique paired users")
        derived_seed = bootstrap_seed(int(config["bootstrap"]["seed"]), family, after, before, metric)
        result = paired_bootstrap(pair[before].to_numpy(), pair[after].to_numpy(), samples=samples,
                                  seed=derived_seed, confidence=confidence, minimum_users=minimum)
        row = {"family": family, "before": before, "after": after, "group": group, "metric": metric,
               "better": better, "configured_seed": int(config["bootstrap"]["seed"]), **result,
               "bootstrap_stream": "PCG64_INDEPENDENT_SHA256_DERIVED_SEED_ASCENDING_COMMON_FINITE_UID"}
        if row["ci_low"] is None:
            row["direction"] = "DESCRIPTIVE_SMALL_N"
        elif (better == "lower" and row["ci_high"] < 0) or (better == "higher" and row["ci_low"] > 0):
            row["direction"] = "AFTER_BETTER"
        elif (better == "lower" and row["ci_low"] > 0) or (better == "higher" and row["ci_high"] < 0):
            row["direction"] = "AFTER_WORSE"
        else:
            row["direction"] = "NO_CLEAR_DIFFERENCE"
        contrast_rows.append(row)
        for uid, values in pair.iterrows():
            paired_rows.append({"family": family, "before": before, "after": after, "group": group,
                                "metric": metric, "uid": int(uid), "before_value": float(values[before]),
                                "after_value": float(values[after]), "delta": float(values[after] - values[before])})

    decisions: list[dict[str, Any]] = []
    contrast_frame = pd.DataFrame(contrast_rows)
    pair_keys = contrast_frame[["family", "before", "after", "group"]].drop_duplicates().itertuples(index=False, name=None)
    for family, before, after, group in pair_keys:
        rows = contrast_frame[contrast_frame.family.eq(family) & contrast_frame.before.eq(before)
                              & contrast_frame.after.eq(after) & contrast_frame.group.eq(group)]
        require(set(rows.metric) == {"mse", "ndcg2"}, "two primary metrics per model pair")
        safety_baseline = "ALS" if family == "warm" else "GBT120_s339"
        safety_pairs = [_metric_pair(users, safety_baseline, after, group, metric, cap) for metric in ("stars2", "low2")]
        safety_uids = sorted(set(map(int, safety_pairs[0].index)) & set(map(int, safety_pairs[1].index)))
        safety: dict[str, Any] = {"safety_before": safety_baseline, "safety_users": len(safety_uids)}
        for metric, pair in zip(("stars2", "low2"), safety_pairs):
            pair = pair.loc[safety_uids]
            safety[metric + "_delta"] = float((pair[after] - pair[safety_baseline]).mean()) if len(pair) else None
        safe = (
            safety["stars2_delta"] is not None and safety["low2_delta"] is not None
            and safety["stars2_delta"] >= -float(config["safety"]["max_top2_stars_loss"]) - EPS
            and safety["low2_delta"] <= float(config["safety"]["max_low2_increase"]) + EPS
        )
        directions = set(rows.direction)
        small_n = bool(rows.users.lt(minimum).any() or len(safety_uids) < minimum)
        favorable = "AFTER_BETTER" in directions
        harmful = "AFTER_WORSE" in directions
        if small_n:
            verdict = "DESCRIPTIVE_SMALL_N"
        elif favorable and harmful:
            verdict = "TRADEOFF"
        elif not safe:
            verdict = "DETECTED_HARM"
        elif harmful:
            verdict = "DETECTED_HARM"
        elif favorable:
            verdict = "ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM"
        else:
            verdict = "NO_CLEAR_DIFFERENCE"
        require(verdict in config["decision_labels"], "declared decision label")
        decisions.append({"family": family, "before": before, "after": after, "group": group,
                          "confidence": float(rows.confidence.iloc[0]), "mse_users": int(rows.loc[rows.metric.eq("mse"), "users"].iloc[0]),
                          "ndcg2_users": int(rows.loc[rows.metric.eq("ndcg2"), "users"].iloc[0]),
                          "verdict": verdict, "safety_pass": bool(safe), **safety})

    require(contrast_frame.family.value_counts().to_dict() == {"cold": 8, "router": 4, "warm": 2}, "exact 2/8/4 contrast family")
    return pd.DataFrame(contrast_rows), pd.DataFrame(paired_rows), {
        "scope": "DEVELOPMENT_ONLY", "comparison_role_only": True,
        "bootstrap_uncertainty": "USER_SAMPLING_CONDITIONAL_ON_FIXED_MODELS_MAPPER_AND_CALIBRATORS",
        "shared_bootstrap_within_family": False,
        "seed_derivation": config["bootstrap"]["seed_derivation"],
        "not_equivalence_or_noninferiority_testing": True, "pair_decisions": decisions,
        "allowed_positive_label": "ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM", "service_adoption": False,
    }


def _calibration_payload(calibrations: Mapping[tuple[str, int], Mapping[str, Any]]) -> dict[str, Any]:
    rows = []
    for (model, cap), value in sorted(calibrations.items()):
        rows.append({"model": model, "cap": cap, **dict(value)})
    return {"scope": "DEVELOPMENT_ONLY", "estimator": "USER_MACRO_NONNEGATIVE_AFFINE", "fits": rows,
            "comparison_labels_used_for_fitting": False}


def _prepare_output(paths: Sequence[Path], overwrite: bool) -> None:
    existing = [path for path in paths if path.exists()]
    require(overwrite or not existing, "preserve existing evaluation outputs: " + ", ".join(map(str, existing[:3])))
    if overwrite:
        for path in existing:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def evaluate(artifact_dir: Path, output_dir: Path, config_path: Path, *, overwrite: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    artifact_dir, output_dir, config_path = map(Path, (artifact_dir, output_dir, config_path))
    config = read_json(config_path)
    require(config.get("claim_scope") == "DEVELOPMENT_ONLY", "development-only evaluation scope")
    require(tuple(config["top_ns"]) == TOP_NS and int(config["primary_cap"]) == 10, "frozen top-N/cap contract")
    validate_config_contract(config)

    # Label-free gate.  Nothing above or in this block opens labels.
    prediction_parent = verify_prediction_seal(artifact_dir)
    evaluation_review = require_evaluation_review(config_path)
    sources = {name: ROOT / value for name, value in config["sources"].items() if isinstance(value, str)}
    direct_input_names = ("catalog", "contexts", "roles", "partition", "rec033_metadata", "texts",
                          "final344_input_lock")
    for name in direct_input_names:
        relative = config["sources"][name]
        require(relative in config["source_pins"], f"source pin declared: {name}")
        _assert_pin(sources[name], config["source_pins"][relative], f"config source:{name}")
    require(config["source_pins"][config["sources"]["labels"]]["sha256"] == LABEL_SHA256,
            "config freezes the expected label identity")
    catalog = pd.read_parquet(sources["catalog"])
    partition = pd.read_parquet(sources["partition"])
    metadata, vote_inputs = load_tmdb_vote_metadata(config, catalog)
    final344_calibration_parent = verify_final344_calibration_parent(config)
    texts = pd.read_parquet(sources["texts"], columns=["movie_id", "release_date"])
    roles = load_roles(sources["roles"], config["roles"])
    contexts = read_json(sources["contexts"])
    row_axis = build_row_axis(contexts, catalog, config["caps"], int(config["origin_timestamp"]))
    require(len(row_axis) == int(config["context_rows"]), "fixed score row count")
    require(set(zip(row_axis.uid.astype(int), row_axis.cap.astype(int))) ==
            {(uid, cap) for uid in roles for cap in config["caps"]}, "every role user/cap context")
    groups = movie_groups(catalog, partition, config["movie_groups"])
    row_axis = attach_roles_and_groups(row_axis, roles, groups)
    row_axis = attach_movie_diagnostics(row_axis, catalog, metadata, texts)
    raw, available = load_prediction_matrix(artifact_dir, len(row_axis))

    output_names = ["calibration.json", "calibration-reproduction.json", "selection.json", "selection-user-metrics.parquet",
                    "user-metrics.parquet", "page-metrics.parquet", "row-errors.parquet", "summary.csv", "page-summary.csv",
                    "denominators.csv", "diagnostic-summary.csv", "primary-contrasts.csv", "primary-paired-users.parquet", "decisions.json",
                    "evaluation-manifest.json", "evaluation-seal.json"]
    output_dir.mkdir(parents=True, exist_ok=True)
    _prepare_output([output_dir / name for name in output_names], overwrite)

    # The immutable label pin is checked immediately before the sole label read.
    truth, label_pin = load_truth(sources["labels"], row_axis)
    calibrations = fit_calibrations(row_axis, truth, raw, available, config["caps"],
                                    min_users=int(config["calibration"]["min_users"]),
                                    min_rows=int(config["calibration"]["min_rows"]))
    calibrated = calibrated_vectors(row_axis, raw, calibrations, config["caps"])
    reproduction = reproduce_final344_calibration(
        calibrations, ROOT / FINAL344_CALIBRATION_RELATIVE, config["caps"], final344_calibration_parent
    )
    usable = {name: available[name] & np.isfinite(calibrated[name]) for name in BASE_MODELS}
    selection, selection_users = choose_policies(row_axis, truth, raw, usable, calibrated, config)
    native_raw = dict(raw)
    raw, usable, all_scores = add_derived_models(row_axis, dict(calibrated), dict(usable), calibrated, selection)
    native_raw.update({name: raw[name] for name in DERIVED_MODELS})
    router_source = all_scores.pop("ROUTER_SOURCE")
    users, pages, errors = build_evaluation_tables(row_axis, truth, native_raw, raw, usable, all_scores, config["caps"])
    users = pd.concat([users, average_seed_metrics(users)], ignore_index=True)
    pages = pd.concat([pages, average_seed_metrics(pages, page=True)], ignore_index=True)
    summary, denominators = summarize_user_metrics(users)
    page_summary = summarize_pages(pages)
    diagnostics = summarize_diagnostics(errors)
    contrasts, paired, decisions = formal_contrasts(users, config)
    decisions["selection"] = {"warm_content_weight": selection["warm"]["content_weight"], "cold_head": selection["cold"]["head"]}
    decisions["natural_zero_inference"] = "DESCRIPTIVE_ONLY_IF_NDCG2_VALID_USERS_BELOW_30"

    calibration_path = output_dir / "calibration.json"
    reproduction_path = output_dir / "calibration-reproduction.json"
    selection_path = output_dir / "selection.json"
    write_json(calibration_path, _calibration_payload(calibrations))
    write_json(reproduction_path, reproduction)
    write_json(selection_path, selection)
    selection_users.to_parquet(output_dir / "selection-user-metrics.parquet", index=False)
    users.to_parquet(output_dir / "user-metrics.parquet", index=False)
    pages.to_parquet(output_dir / "page-metrics.parquet", index=False)
    errors["router_source"] = np.where(errors.model.eq("ROUTER_s339"), router_source[errors.row_id.to_numpy(int)], "")
    errors.to_parquet(output_dir / "row-errors.parquet", index=False)
    summary.to_csv(output_dir / "summary.csv", index=False)
    page_summary.to_csv(output_dir / "page-summary.csv", index=False)
    denominators.to_csv(output_dir / "denominators.csv", index=False)
    diagnostics.to_csv(output_dir / "diagnostic-summary.csv", index=False)
    contrasts.to_csv(output_dir / "primary-contrasts.csv", index=False)
    paired.to_parquet(output_dir / "primary-paired-users.parquet", index=False)
    write_json(output_dir / "decisions.json", decisions)

    result_files = output_names[:-2]
    manifest = {
        "scope": "DEVELOPMENT_ONLY",
        "prediction_seal": prediction_parent["seal_pin"],
        "evaluation_review": pin(EVALUATION_REVIEW),
        "evaluation_fingerprint": evaluation_review["fingerprint"],
        "config": pin(config_path),
        "inputs": {
            **{name: pin(sources[name]) for name in (*direct_input_names, "labels")},
            "tmdb_vote_metadata": vote_inputs["tmdb_vote_metadata"],
            **final344_calibration_parent,
        },
        "code": {"evaluation": pin(Path(__file__))},
        "rows": len(row_axis),
        "models": [*BASE_MODELS, *DERIVED_MODELS, *METRIC_MEAN_MODELS],
        "selection": {"warm_content_weight": selection["warm"]["content_weight"], "cold_head": selection["cold"]["head"]},
        "labels_opened_after_prediction_gate": True,
        "label_pin": label_pin,
        "seconds": time.monotonic() - started,
        "files": {name: pin(output_dir / name) for name in result_files},
    }
    write_json(output_dir / "evaluation-manifest.json", manifest)
    # Recheck the parent after all label-dependent work, then seal results.
    require(verify_prediction_seal(artifact_dir)["seal_pin"] == prediction_parent["seal_pin"], "prediction parent changed during evaluation")
    require(require_evaluation_review(config_path) == evaluation_review, "evaluation review changed during evaluation")
    require(verify_final344_calibration_parent(config) == final344_calibration_parent,
            "final344 calibration parent changed during evaluation")
    seal = {
        "scope": "DEVELOPMENT_ONLY",
        "prediction_seal": prediction_parent["seal_pin"],
        "manifest": pin(output_dir / "evaluation-manifest.json"),
        "files": {name: pin(output_dir / name) for name in output_names[:-1]},
    }
    write_json(output_dir / "evaluation-seal.json", seal)
    return {"selection": selection, "decisions": decisions, "seal": seal}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT,
                        help="Directory containing predictions.npz and prediction-seal.json")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Evaluation output directory; defaults to --artifact-dir")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--overwrite", action="store_true", help="Replace only this evaluator's named outputs")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    output = args.output_dir or args.artifact_dir
    result = evaluate(args.artifact_dir, output, args.config, overwrite=args.overwrite)
    print(json.dumps({"status": "SEALED", "output": str(output),
                      "warm_content_weight": result["selection"]["warm"]["content_weight"],
                      "cold_head": result["selection"]["cold"]["head"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
