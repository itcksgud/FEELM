#!/usr/bin/env python3
"""Independently verify REC-EV-003 cohort, artifacts, metrics, and factor scores."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


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


def verify_artifact(record: dict[str, Any]) -> Path:
    path = Path(record["path"])
    if not path.is_file():
        raise RuntimeError(f"missing artifact: {path}")
    if path.stat().st_size != record["bytes"]:
        raise RuntimeError(f"artifact size mismatch: {path}")
    if sha256_file(path) != record["sha256"]:
        raise RuntimeError(f"artifact checksum mismatch: {path}")
    return path


def assert_close(actual: float, expected: float, label: str, tolerance: float = 1e-6) -> None:
    if not math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise RuntimeError(f"{label}: expected {expected}, found {actual}")


def dense_item_factors(ids: np.ndarray, values: np.ndarray) -> np.ndarray:
    dense = np.full((int(ids.max()) + 1, values.shape[1]), np.nan, dtype=np.float32)
    dense[ids] = values
    return dense


def sampled_ndcg(frame: pd.DataFrame, score_column: str) -> tuple[float, float]:
    ranked = frame[["user_id", "is_positive", score_column]].sort_values(
        ["user_id", score_column, "is_positive"],
        ascending=[True, False, False],
        kind="stable",
    )
    ranks = ranked.groupby("user_id").cumcount() + 1
    positive_ranks = ranks[ranked["is_positive"].to_numpy() == 1].to_numpy(
        dtype=np.float64
    )
    hit_rate = float(np.mean(positive_ranks <= 10))
    ndcg = float(
        np.mean(
            np.where(
                positive_ranks <= 10,
                1.0 / np.log2(positive_ranks + 1),
                0.0,
            )
        )
    )
    return hit_rate, ndcg


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest["evidence_id"] != "REC-EV-003":
        raise RuntimeError("not a REC-EV-003 manifest")
    if manifest["source"]["test_used"] is not False:
        raise RuntimeError("Test must not be used")
    for path_key, hash_key in (
        ("split_manifest", "split_manifest_sha256"),
        ("baseline_manifest", "baseline_manifest_sha256"),
        ("cohort_manifest", "cohort_manifest_sha256"),
    ):
        path = Path(manifest["source"][path_key])
        if sha256_file(path) != manifest["source"][hash_key]:
            raise RuntimeError(f"source checksum mismatch: {path}")

    artifacts = {
        name: verify_artifact(record)
        for name, record in manifest["artifacts"].items()
    }
    cohort_manifest = json.loads(
        Path(manifest["source"]["cohort_manifest"]).read_text(encoding="utf-8")
    )
    cohort = pd.read_parquet(artifacts["cohort"]).sort_values("user_id")
    cohort_ids = cohort["user_id"].to_numpy(dtype=np.int64)
    expected_ids = np.asarray(cohort_manifest["user_ids"], dtype=np.int64)
    if not np.array_equal(cohort_ids, expected_ids):
        raise RuntimeError("tracked cohort ids do not match cohort artifact")
    if len(cohort_ids) != manifest["cohort"]["users"] or len(np.unique(cohort_ids)) != len(cohort_ids):
        raise RuntimeError("cohort user count or uniqueness mismatch")
    if int(cohort["rating_count"].min()) < cohort_manifest["selection"]["min_train_history"]:
        raise RuntimeError("cohort contains a user below min history")

    split_manifest = json.loads(
        Path(manifest["source"]["split_manifest"]).read_text(encoding="utf-8")
    )
    train_path = Path(split_manifest["artifacts"]["train"]["path"])
    train = pd.read_parquet(
        train_path, columns=["user_id", "movie_id", "rating", "timestamp"]
    )
    excluded = train["user_id"].isin(cohort_ids)
    if int(excluded.sum()) != manifest["cohort"]["excluded_train_rows"]:
        raise RuntimeError("excluded Train row count mismatch")
    if int((~excluded).sum()) != manifest["cohort"]["remaining_train_rows"]:
        raise RuntimeError("remaining Train row count mismatch")

    expected_onboarding = train.loc[
        excluded, ["user_id", "movie_id", "rating", "timestamp"]
    ].copy()
    expected_onboarding = expected_onboarding.sort_values(
        ["user_id", "timestamp", "movie_id"], kind="stable"
    )
    expected_onboarding["onboarding_order"] = (
        expected_onboarding.groupby("user_id").cumcount() + 1
    )
    expected_onboarding = expected_onboarding.loc[
        expected_onboarding["onboarding_order"] <= 20
    ].reset_index(drop=True)
    onboarding = pd.read_parquet(artifacts["onboarding_first_20"]).reset_index(
        drop=True
    )
    pd.testing.assert_frame_equal(
        onboarding,
        expected_onboarding,
        check_dtype=False,
        check_exact=True,
    )
    if not bool((onboarding.groupby("user_id").size() == 20).all()):
        raise RuntimeError("not every cohort user has exactly 20 onboarding rows")
    del train, expected_onboarding, excluded

    item_artifact = np.load(artifacts["cohort_excluded_item_factors"])
    item_ids = item_artifact["movie_ids"].astype(np.int64)
    item_values = item_artifact["movie_factors"].astype(np.float32)
    if len(np.unique(item_ids)) != len(item_ids) or not np.isfinite(item_values).all():
        raise RuntimeError("invalid item factors")
    dense_items = dense_item_factors(item_ids, item_values)

    foldin = np.load(artifacts["foldin_user_factors"])
    k_values = foldin["k_values"].astype(np.int64)
    if not np.array_equal(k_values, np.asarray(manifest["protocol"]["k_values"])):
        raise RuntimeError("factor K values mismatch")
    if not np.array_equal(foldin["user_ids"].astype(np.int64), cohort_ids):
        raise RuntimeError("factor user ids mismatch")
    user_factor_cube = foldin["user_factors"]
    available_counts = foldin["available_item_counts"]
    if user_factor_cube.shape != (len(k_values), len(cohort_ids), manifest["model"]["als"]["rank"]):
        raise RuntimeError("fold-in factor cube shape mismatch")
    for index, k in enumerate(k_values):
        if bool((available_counts[index] > k).any()):
            raise RuntimeError(f"K{k} available factor count exceeds K")
        if k == 0 and not np.isnan(user_factor_cube[index]).all():
            raise RuntimeError("K0 must not have user factors")

    predictions = pd.read_parquet(artifacts["validation_predictions"])
    if not predictions["user_id"].isin(cohort_ids).all():
        raise RuntimeError("prediction artifact contains non-cohort users")
    boundary = manifest["protocol"]["calibration_boundary"]
    calibration = predictions["timestamp"] < boundary
    evaluation = ~calibration
    if int(calibration.sum()) != manifest["cohort"]["calibration_rows"]:
        raise RuntimeError("calibration row count mismatch")
    if int(evaluation.sum()) != manifest["cohort"]["evaluation_rows"]:
        raise RuntimeError("evaluation row count mismatch")
    if predictions.loc[calibration, "timestamp"].max() >= predictions.loc[evaluation, "timestamp"].min():
        raise RuntimeError("calibration and evaluation overlap")

    user_positions = np.searchsorted(cohort_ids, predictions["user_id"].to_numpy())
    movie_ids_for_rows = predictions["movie_id"].to_numpy(dtype=np.int64)
    in_item_range = movie_ids_for_rows < len(dense_items)
    row_item_factors = np.full(
        (len(predictions), dense_items.shape[1]), np.nan, dtype=np.float32
    )
    row_item_factors[in_item_range] = dense_items[
        movie_ids_for_rows[in_item_range]
    ]
    eval_actual = predictions.loc[evaluation, "rating"].to_numpy(dtype=np.float64)
    for k_index, k in enumerate(k_values):
        fallback_column = f"prediction_k{k}_fallback_isotonic"
        if not np.isfinite(predictions[fallback_column]).all():
            raise RuntimeError(f"K{k} fallback has non-finite predictions")
        eval_prediction = predictions.loc[evaluation, fallback_column].to_numpy(
            dtype=np.float64
        )
        micro_mae = float(np.mean(np.abs(eval_prediction - eval_actual)))
        expected_micro = manifest["metrics"]["k_curve"][str(k)][
            "fallback_isotonic"
        ]["mae"]
        assert_close(micro_mae, expected_micro, f"K{k} micro MAE")
        eval_frame = predictions.loc[
            evaluation, ["user_id", "rating", fallback_column]
        ].copy()
        eval_frame["absolute_error"] = np.abs(
            eval_frame[fallback_column] - eval_frame["rating"]
        )
        macro_mae = float(eval_frame.groupby("user_id")["absolute_error"].mean().mean())
        assert_close(
            macro_mae,
            manifest["metrics"]["k_curve"][str(k)]["macro_mae"],
            f"K{k} macro MAE",
        )
        if k > 0:
            direct = predictions[f"prediction_k{k}_direct"].to_numpy(dtype=bool)
            positions = np.flatnonzero(direct)
            if len(positions) > 2048:
                positions = positions[
                    np.linspace(0, len(positions) - 1, 2048, dtype=np.int64)
                ]
            expected_scores = np.einsum(
                "ij,ij->i",
                user_factor_cube[k_index, user_positions[positions]],
                row_item_factors[positions],
                optimize=True,
            )
            observed_scores = predictions.loc[
                positions, f"prediction_k{k}_direct_raw"
            ].to_numpy(dtype=np.float64)
            if not np.allclose(expected_scores, observed_scores, rtol=1e-5, atol=1e-5):
                raise RuntimeError(f"K{k} factor dot product mismatch")

    candidates = pd.read_parquet(artifacts["sampled_ranking"])
    fixed_count = 100
    if not bool((candidates.groupby("user_id").size() == fixed_count).all()):
        raise RuntimeError("sampled candidate counts differ")
    if not bool((candidates.groupby("user_id")["is_positive"].sum() == 1).all()):
        raise RuntimeError("sampled users do not have exactly one positive")
    for k in k_values:
        hit_rate, ndcg = sampled_ndcg(candidates, f"score_k{k}")
        metric = manifest["metrics"]["k_curve"][str(k)]["sampled_ranking"]
        assert_close(hit_rate, metric["hit_rate_at_10"], f"K{k} HR@10")
        assert_close(ndcg, metric["ndcg_at_10"], f"K{k} NDCG@10")

    print(
        "REC-EV-003 verification passed: "
        f"{len(cohort_ids):,} cohort users, "
        f"{manifest['cohort']['excluded_train_rows']:,} excluded Train rows, "
        f"{manifest['cohort']['evaluation_rows']:,} evaluation rows; "
        "checksums, first-K ordering, no-Test protocol, metrics, and factor dot products valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
