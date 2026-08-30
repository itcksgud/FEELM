#!/usr/bin/env python3
"""Independently verify REC-EV-003B selection and held-out metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


K_VALUES = (1, 3, 5, 10, 20)


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
    if path.stat().st_size != record["bytes"] or sha256_file(path) != record["sha256"]:
        raise RuntimeError(f"artifact mismatch: {path}")
    return path


def assert_close(actual: float, expected: float, label: str, tolerance: float = 1e-6) -> None:
    if not math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise RuntimeError(f"{label}: expected {expected}, found {actual}")


def stable_user_selection(user_ids: np.ndarray, seed: int) -> np.ndarray:
    values = user_ids.astype(np.uint64)
    mixed = values * np.uint64(11400714819323198485) + np.uint64(seed)
    return (mixed & np.uint64(1)) == 0


def per_user_ndcg(frame: pd.DataFrame, score_column: str) -> pd.Series:
    ranked = frame[["user_id", "is_positive", score_column]].sort_values(
        ["user_id", score_column, "is_positive"],
        ascending=[True, False, False],
        kind="stable",
    )
    ranks = ranked.groupby("user_id").cumcount() + 1
    positive = ranked["is_positive"].to_numpy() == 1
    positive_ranks = ranks[positive].to_numpy(dtype=np.float64)
    values = np.where(
        positive_ranks <= 10, 1.0 / np.log2(positive_ranks + 1), 0.0
    )
    return pd.Series(values, index=ranked.loc[positive, "user_id"].to_numpy())


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest["evidence_id"] != "REC-EV-003B" or manifest["source"]["test_used"] is not False:
        raise RuntimeError("invalid REC-EV-003B manifest or Test usage")
    parent_path = Path(manifest["source"]["cold_start_manifest"])
    if sha256_file(parent_path) != manifest["source"]["cold_start_manifest_sha256"]:
        raise RuntimeError("cold-start manifest checksum mismatch")
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    parent_predictions = pd.read_parquet(
        Path(parent["artifacts"]["validation_predictions"]["path"])
    )
    parent_candidates = pd.read_parquet(
        Path(parent["artifacts"]["sampled_ranking"]["path"])
    )
    artifacts = {
        name: verify_artifact(record)
        for name, record in manifest["artifacts"].items()
    }
    stars = pd.read_parquet(artifacts["selected_star_predictions"])
    ranks = pd.read_parquet(artifacts["selected_ranking_scores"])
    if len(stars) != len(parent_predictions) or len(ranks) != len(parent_candidates):
        raise RuntimeError("selected artifact row count mismatch")
    if not stars["row_id"].equals(parent_predictions["row_id"]):
        raise RuntimeError("star rows do not align with parent predictions")
    if not ranks["candidate_id"].equals(parent_candidates["candidate_id"]):
        raise RuntimeError("ranking rows do not align with parent candidates")

    calibration_boundary = manifest["protocol"]["star_calibration_boundary"]
    selection_boundary = manifest["protocol"]["star_selection_boundary"]
    calibration = stars["timestamp"] < calibration_boundary
    selection = (stars["timestamp"] >= calibration_boundary) & (
        stars["timestamp"] < selection_boundary
    )
    evaluation = stars["timestamp"] >= selection_boundary
    if stars.loc[calibration, "timestamp"].max() >= stars.loc[selection, "timestamp"].min():
        raise RuntimeError("calibration and star selection overlap")
    if stars.loc[selection, "timestamp"].max() >= stars.loc[evaluation, "timestamp"].min():
        raise RuntimeError("star selection and evaluation overlap")

    selection_rows = stable_user_selection(
        ranks["user_id"].to_numpy(dtype=np.int64), manifest["protocol"]["seed"]
    )
    selection_users = set(ranks.loc[selection_rows, "user_id"])
    evaluation_users = set(ranks.loc[~selection_rows, "user_id"])
    if not selection_users.isdisjoint(evaluation_users):
        raise RuntimeError("ranking user sets overlap")
    if len(selection_users) != manifest["protocol"]["ranking_selection_users"]:
        raise RuntimeError("ranking selection user count mismatch")
    if len(evaluation_users) != manifest["protocol"]["ranking_evaluation_users"]:
        raise RuntimeError("ranking evaluation user count mismatch")

    baseline_eval_ndcg = per_user_ndcg(
        parent_candidates.loc[~selection_rows].assign(
            selected_score=parent_candidates.loc[~selection_rows, "score_k0"].to_numpy()
        ),
        "selected_score",
    )
    practically_supported: list[int] = []
    statistically_supported: list[int] = []
    for k in K_VALUES:
        selection_record = manifest["metrics"]["alpha_selection"][str(k)]
        star_grid = selection_record["star_grid"]
        rank_grid = selection_record["rank_grid"]
        expected_star = min(star_grid, key=lambda value: (value["macro_mae"], value["alpha"]))
        expected_rank = max(rank_grid, key=lambda value: (value["ndcg_at_10"], -value["alpha"]))
        assert_close(
            selection_record["star_alpha"], expected_star["alpha"], f"K{k} star alpha"
        )
        assert_close(
            selection_record["rank_alpha"], expected_rank["alpha"], f"K{k} rank alpha"
        )
        selected = manifest["metrics"]["selected_curve"][str(k)]
        if not np.isfinite(stars[f"prediction_k{k}"]).all() or not np.isfinite(ranks[f"score_k{k}"]).all():
            raise RuntimeError(f"K{k} selected output contains non-finite values")
        eval_frame = stars.loc[evaluation, ["user_id", "rating", f"prediction_k{k}"]].copy()
        eval_frame["absolute_error"] = np.abs(
            eval_frame[f"prediction_k{k}"] - eval_frame["rating"]
        )
        macro_mae = float(eval_frame.groupby("user_id")["absolute_error"].mean().mean())
        assert_close(macro_mae, selected["star_macro_mae"], f"K{k} star macro MAE")

        expected_rank_score = (
            (1.0 - selected["rank_alpha"])
            * parent_candidates["score_k0"].to_numpy(dtype=np.float64)
            + selected["rank_alpha"]
            * parent_candidates[f"score_k{k}"].to_numpy(dtype=np.float64)
        )
        if not np.allclose(expected_rank_score, ranks[f"score_k{k}"]):
            raise RuntimeError(f"K{k} selected ranking score mismatch")
        eval_rank = ranks.loc[~selection_rows]
        eval_ndcg = per_user_ndcg(eval_rank, f"score_k{k}")
        assert_close(
            float(eval_ndcg.mean()),
            selected["ranking_eval"]["ndcg_at_10"],
            f"K{k} ranking eval NDCG",
        )
        if not eval_ndcg.index.equals(baseline_eval_ndcg.index):
            eval_ndcg = eval_ndcg.reindex(baseline_eval_ndcg.index)
        observed_difference = float((eval_ndcg - baseline_eval_ndcg).mean())
        assert_close(
            observed_difference,
            selected["rank_vs_popularity"]["mean_difference"],
            f"K{k} ranking difference",
        )
        rank_noninferior = (
            selected["rank_vs_popularity"]["ci95_low"]
            >= -manifest["protocol"]["ranking_noninferiority"]
        )
        statistically_better = selected["star_vs_k0"]["ci95_high"] < 0
        if statistically_better and rank_noninferior:
            statistically_supported.append(k)
            if (
                selected["star_relative_improvement"]
                >= manifest["protocol"]["star_min_relative_improvement"]
            ):
                practically_supported.append(k)

    first_statistical = min(statistically_supported) if statistically_supported else None
    first_practical = min(practically_supported) if practically_supported else None
    expected_statistical = f"K{first_statistical}" if first_statistical else "NONE"
    expected_practical = f"K{first_practical}_DATA_ONLY" if first_practical else "NONE"
    if manifest["conclusion"]["first_statistically_supported_k"] != expected_statistical:
        raise RuntimeError("first statistical K conclusion mismatch")
    if manifest["conclusion"]["minimum_supported_k"] != expected_practical:
        raise RuntimeError("minimum practical K conclusion mismatch")

    print(
        "REC-EV-003B verification passed: "
        f"{len(stars):,} star rows, {len(ranks):,} ranking candidates; "
        "source/artifact checksums, disjoint selection, alpha choices, held-out metrics, and K gates valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
