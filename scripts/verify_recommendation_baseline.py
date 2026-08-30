#!/usr/bin/env python3
"""Independently verify REC-EV-002 artifacts and key aggregate metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


METRIC_COLUMNS = {
    "global_mean": "prediction_global",
    "popularity": "prediction_popularity",
    "bias_raw": "prediction_bias_raw",
    "bias_isotonic": "prediction_bias_isotonic",
    "als_warm_raw": "prediction_als_raw",
    "als_warm_isotonic": "prediction_als_isotonic",
    "als_bias_fallback_raw": "prediction_als_bias_fallback_raw",
    "als_bias_fallback_isotonic": "prediction_als_bias_fallback_isotonic",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_close(actual: float, expected: float, label: str, tolerance: float = 1e-6) -> None:
    if not math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise RuntimeError(f"{label}: expected {expected}, found {actual}")


def verify_artifact(record: dict[str, Any]) -> Path:
    path = Path(record["path"])
    if not path.is_file():
        raise RuntimeError(f"artifact missing or not a file: {path}")
    if path.stat().st_size != record["bytes"]:
        raise RuntimeError(f"artifact size mismatch: {path}")
    if sha256_file(path) != record["sha256"]:
        raise RuntimeError(f"artifact checksum mismatch: {path}")
    return path


def dense_factors(ids: np.ndarray, values: np.ndarray) -> np.ndarray:
    result = np.full((int(ids.max()) + 1, values.shape[1]), np.nan, dtype=np.float32)
    result[ids] = values
    return result


def verify_factor_scores(
    frame: pd.DataFrame,
    score_column: str,
    user_factors: np.ndarray,
    movie_factors: np.ndarray,
    sample_size: int = 2048,
) -> None:
    finite = frame.loc[np.isfinite(frame[score_column]), ["user_id", "movie_id", score_column]]
    if finite.empty:
        raise RuntimeError(f"no finite scores in {score_column}")
    if len(finite) > sample_size:
        positions = np.linspace(0, len(finite) - 1, sample_size, dtype=np.int64)
        finite = finite.iloc[positions]
    users = finite["user_id"].to_numpy(dtype=np.int64)
    movies = finite["movie_id"].to_numpy(dtype=np.int64)
    expected = np.einsum(
        "ij,ij->i", user_factors[users], movie_factors[movies], optimize=True
    )
    observed = finite[score_column].to_numpy(dtype=np.float64)
    if not np.allclose(expected, observed, rtol=1e-5, atol=1e-5):
        difference = float(np.max(np.abs(expected - observed)))
        raise RuntimeError(f"ALS factor score mismatch; max difference {difference}")


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest["evidence_id"] != "REC-EV-002":
        raise RuntimeError("not a REC-EV-002 manifest")
    if manifest["source"]["test_used"] is not False:
        raise RuntimeError("Test must not be used in REC-EV-002")

    split_manifest_path = Path(manifest["source"]["split_manifest"])
    if sha256_file(split_manifest_path) != manifest["source"]["split_manifest_sha256"]:
        raise RuntimeError("REC-EV-001 split manifest checksum mismatch")

    artifacts = {
        name: verify_artifact(record)
        for name, record in manifest["artifacts"].items()
    }
    predictions = pd.read_parquet(artifacts["validation_predictions"])
    candidates = pd.read_parquet(artifacts["sampled_ranking_candidates"])
    if len(predictions) != manifest["source"]["validation_rows"]:
        raise RuntimeError("validation prediction row count mismatch")
    if not predictions["row_id"].is_unique:
        raise RuntimeError("validation row_id is not unique")
    if len(candidates) != manifest["artifacts"]["sampled_ranking_candidates"]["rows"]:
        raise RuntimeError("ranking candidate row count mismatch")

    boundary = manifest["protocol"]["calibration_boundary"]
    calibration = predictions["timestamp"] < boundary
    evaluation = ~calibration
    if int(calibration.sum()) != manifest["protocol"]["calibration_fit_rows"]:
        raise RuntimeError("calibration row count mismatch")
    if int(evaluation.sum()) != manifest["protocol"]["validation_eval_rows"]:
        raise RuntimeError("evaluation row count mismatch")
    if predictions.loc[calibration, "timestamp"].max() >= predictions.loc[evaluation, "timestamp"].min():
        raise RuntimeError("calibration and evaluation times overlap")

    evaluation_frame = predictions.loc[evaluation]
    actual = evaluation_frame["rating"].to_numpy(dtype=np.float64)
    metric_manifest = manifest["metrics"]["validation_eval"]
    for name, column in METRIC_COLUMNS.items():
        predicted = evaluation_frame[column].to_numpy(dtype=np.float64)
        finite = np.isfinite(predicted)
        observed_rows = int(finite.sum())
        observed_coverage = observed_rows / len(evaluation_frame)
        observed_mae = float(np.mean(np.abs(predicted[finite] - actual[finite])))
        observed_rmse = float(np.sqrt(np.mean(np.square(predicted[finite] - actual[finite]))))
        expected = metric_manifest[name]
        if observed_rows != expected["rows"]:
            raise RuntimeError(f"{name} row count mismatch")
        assert_close(observed_coverage, expected["coverage"], f"{name} coverage")
        assert_close(observed_mae, expected["mae"], f"{name} MAE")
        assert_close(observed_rmse, expected["rmse"], f"{name} RMSE")

    finite_als = np.isfinite(predictions["prediction_als_raw"])
    warm = predictions["identity_state"] == "KNOWN_USER_KNOWN_ITEM"
    if not np.array_equal(finite_als.to_numpy(), warm.to_numpy()):
        raise RuntimeError("ALS finite rows do not equal warm identity rows")
    if not np.isfinite(predictions["prediction_als_bias_fallback_isotonic"]).all():
        raise RuntimeError("calibrated fallback does not cover all validation rows")

    per_user_counts = candidates.groupby("user_id").size()
    expected_count = manifest["protocol"]["ranking_negatives"] + 1
    if not bool((per_user_counts == expected_count).all()):
        raise RuntimeError("ranking users do not all have the fixed candidate count")
    if not bool((candidates.groupby("user_id")["is_positive"].sum() == 1).all()):
        raise RuntimeError("ranking users do not all have exactly one positive")
    if not np.isfinite(candidates["score_als"]).all():
        raise RuntimeError("sampled ALS candidates contain missing scores")

    factors = np.load(artifacts["als_model"])
    user_ids = factors["user_ids"].astype(np.int64)
    movie_ids = factors["movie_ids"].astype(np.int64)
    if len(np.unique(user_ids)) != len(user_ids) or len(np.unique(movie_ids)) != len(movie_ids):
        raise RuntimeError("ALS factor ids are not unique")
    user_factors = dense_factors(user_ids, factors["user_factors"])
    movie_factors = dense_factors(movie_ids, factors["movie_factors"])
    verify_factor_scores(
        predictions,
        "prediction_als_raw",
        user_factors,
        movie_factors,
    )
    verify_factor_scores(candidates, "score_als", user_factors, movie_factors)

    print(
        "REC-EV-002 verification passed: "
        f"{len(predictions):,} validation rows, "
        f"{int(finite_als.sum()):,} direct ALS rows, "
        f"{candidates['user_id'].nunique():,} sampled-ranking users; "
        "checksums, time order, metrics, and factor dot products valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
