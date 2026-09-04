#!/usr/bin/env python3
"""Independent integrity and boundary checks for REC-EV-019C analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from analyze_rec_ev_019c_validation import BASELINE, ROOT, sha256_file
from verify_rec_ev_019c_validation import verify_manifest as verify_validation_manifest


DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json"


def _resolve_repo_path(value: str, *, root: Path) -> Path:
    path = (root / value).resolve()
    path.relative_to(root.resolve())
    return path


def _close(actual: float, expected: float, *, tolerance: float = 1e-10) -> bool:
    return bool(np.isclose(actual, expected, rtol=0.0, atol=tolerance, equal_nan=True))


def verify_analysis_manifest(path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("status") != "PASS_VALIDATION_ANALYSIS_ONLY":
        raise RuntimeError("analysis manifest status is not PASS_VALIDATION_ANALYSIS_ONLY")
    boundary = manifest.get("validation", {})
    if boundary != {
        "champion_selected": False,
        "locked_test_opened": False,
        "post_hoc_results_are_confirmatory": False,
        "product_policy_changed": False,
    }:
        raise RuntimeError("analysis boundary changed or is incomplete")

    validation_ref = manifest["source_validation_manifest"]
    validation_path = _resolve_repo_path(validation_ref["path"], root=root)
    if sha256_file(validation_path) != validation_ref["sha256"]:
        raise RuntimeError("source Validation manifest checksum differs")
    verify_validation_manifest(validation_path, root=root)

    artifacts: dict[str, Path] = {}
    for row in manifest["artifacts"]:
        artifact = _resolve_repo_path(row["path"], root=root)
        if not artifact.is_file():
            raise RuntimeError(f"analysis artifact is missing: {row['path']}")
        if artifact.stat().st_size != int(row["bytes"]):
            raise RuntimeError(f"analysis artifact size differs: {row['path']}")
        if sha256_file(artifact) != row["sha256"]:
            raise RuntimeError(f"analysis artifact checksum differs: {row['path']}")
        artifacts[row["path"]] = artifact

    summary_path = next(path for name, path in artifacts.items() if name.endswith("analysis-summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "VALIDATION_ANALYZED_LOCKED_TEST_UNOPENED":
        raise RuntimeError("analysis summary status differs")
    interpretation = summary["interpretation"]
    required_false = (
        "movie_lens_users_are_feelm_users", "unobserved_means_dislike", "locked_test_opened",
        "champion_selected", "product_policy_changed", "post_hoc_results_are_confirmatory",
    )
    if any(interpretation.get(key) is not False for key in required_false):
        raise RuntimeError("analysis interpretation boundary is not fail-closed")

    metrics = pd.read_parquet(
        root / "outputs/recommendation-evidence/rec-ev-019c/validation-user-metrics.parquet"
    )
    expected_groups = int(metrics.groupby(["model_id", "k"], observed=True).ngroups)
    if len(summary["aggregate"]) != expected_groups:
        raise RuntimeError("aggregate row count differs from Validation metrics")
    for row in summary["aggregate"]:
        group = metrics.loc[
            (metrics["model_id"] == row["model_id"]) & (metrics["k"] == int(row["k"]))
        ]
        if int(row["users"]) != int(group["user_key"].nunique()):
            raise RuntimeError("aggregate user count differs")
        if not _close(float(row["ndcg_at_10"]), float(group["ndcg_at_10"].mean())):
            raise RuntimeError("aggregate NDCG differs")
        if not _close(float(row["harm_at_2"]), float(group["harm_at_2"].astype(float).mean())):
            raise RuntimeError("aggregate Harm@2 differs")

    paired = summary["paired_vs_b0"]
    models_by_k = {
        int(k): set(metrics.loc[metrics["k"] == int(k), "model_id"])
        for k in (5, 10)
    }
    expected_paired = sum(len(models - {BASELINE}) for models in models_by_k.values())
    if len(paired) != expected_paired:
        raise RuntimeError("paired comparison count differs")
    for row in paired:
        total = float(row["benefit_rate"]) + float(row["tie_rate"]) + float(row["harm_rate"])
        if not _close(total, 1.0):
            raise RuntimeError("benefit/tie/harm rates do not sum to one")
        lower, upper = map(float, row["delta_ndcg_ci95"])
        if lower > upper:
            raise RuntimeError("paired NDCG confidence interval is reversed")

    history_rows = [row for row in summary["cohorts_for_validation_best_vs_b0"] if row["dimension"] == "history_group"]
    for k in (5, 10):
        cohorts = {row["cohort"] for row in history_rows if int(row["k"]) == k}
        if len(cohorts) < 2:
            raise RuntimeError(f"history cohorts collapsed for K={k}")

    item_slices = summary["item_slices"]
    slice_keys = [
        (row["model_id"], int(row["k"]), row["dimension"], row["cohort"])
        for row in item_slices
    ]
    if len(slice_keys) != len(set(slice_keys)):
        raise RuntimeError("item slice keys are duplicated")
    for k in (5, 10):
        for dimension in ("popularity_group", "language_group"):
            by_model = {
                model_id: {
                    row["cohort"]
                    for row in item_slices
                    if int(row["k"]) == k and row["dimension"] == dimension and row["model_id"] == model_id
                }
                for model_id in models_by_k[k]
            }
            expected = set().union(*by_model.values())
            if any(cohorts != expected for cohorts in by_model.values()):
                raise RuntimeError(f"item slice cohort coverage differs across models for K={k} {dimension}")

    return {
        "status": "PASS_REC_EV_019C_ANALYSIS_VERIFICATION",
        "aggregate_groups": expected_groups,
        "paired_comparisons": expected_paired,
        "locked_test_opened": False,
        "champion_selected": False,
        "product_policy_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    result = verify_analysis_manifest(args.manifest.resolve(), root=ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
