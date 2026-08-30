#!/usr/bin/env python3
"""Independently verify REC-EV-005 manifest and small party result artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


METRICS = (
    "actual_mean_utility",
    "actual_min_utility",
    "actual_member_gap",
    "actual_raw_rating_mean",
    "predicted_relevance_loss",
)


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


def verify_file(record: dict[str, Any]) -> Path:
    path = Path(record["path"])
    if not path.is_file():
        raise RuntimeError(f"artifact missing: {path}")
    if path.stat().st_size != record["bytes"]:
        raise RuntimeError(f"artifact size mismatch: {path}")
    if sha256_file(path) != record["sha256"]:
        raise RuntimeError(f"artifact checksum mismatch: {path}")
    return path


def assert_close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, abs_tol=1e-6, rel_tol=1e-6):
        raise RuntimeError(f"{label}: expected {expected}, found {actual}")


def find_forbidden_keys(value: Any, path: str = "$") -> list[str]:
    forbidden = {"user_id", "movie_id", "member_ids", "candidate_ids", "raw_user_id", "raw_movie_id"}
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in forbidden:
                found.append(f"{path}.{key}")
            found.extend(find_forbidden_keys(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_forbidden_keys(item, f"{path}[{index}]"))
    return found


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("evidence_id") != "REC-EV-005":
        raise RuntimeError("not a REC-EV-005 manifest")
    if manifest.get("status") != "COMPLETED_OFFLINE_EVIDENCE":
        raise RuntimeError("REC-EV-005 is not complete offline evidence")
    if manifest["source"]["test_rating_values_used_for_parameter_selection"] is not False:
        raise RuntimeError("Test values were used for Balanced selection")
    if manifest["protocol"]["selection_split"] != "validation":
        raise RuntimeError("selection split must be validation")
    if manifest["protocol"]["evaluation_split"] != "test":
        raise RuntimeError("evaluation split must be test")
    if manifest["protocol"]["raw_ids_in_tracked_artifacts"] is not False:
        raise RuntimeError("raw identifiers are not allowed")
    forbidden = find_forbidden_keys(manifest)
    if forbidden:
        raise RuntimeError(f"raw identifier keys found: {forbidden}")

    for key in ("split_manifest", "baseline_manifest"):
        path = Path(manifest["source"][key])
        if sha256_file(path) != manifest["source"][f"{key}_sha256"]:
            raise RuntimeError(f"{key} checksum mismatch")
    for key in ("als_factors", "user_rating_profiles", "validation", "test"):
        verify_file(manifest["source"][key])
    metrics_path = verify_file(manifest["artifacts"]["party_policy_metrics"])
    frame = pd.read_parquet(metrics_path)
    if len(frame) != manifest["artifacts"]["party_policy_metrics"]["rows"]:
        raise RuntimeError("party metric row count mismatch")
    raw_columns = {
        "user_id",
        "movie_id",
        "member_ids",
        "candidate_ids",
        "raw_user_id",
        "raw_movie_id",
    }
    if raw_columns.intersection(frame.columns):
        raise RuntimeError("party metric artifact contains raw identifiers")
    if frame[list(METRICS)].isna().any().any():
        raise RuntimeError("party metrics contain missing values")
    policy_counts = frame.groupby("party_label")["policy"].nunique()
    if not bool((policy_counts == 4).all()):
        raise RuntimeError("every party must have four policy rows")
    cells = frame[["group_size", "group_type"]].drop_duplicates()
    if len(cells) != 9:
        raise RuntimeError(f"expected 9 group cells; found {len(cells)}")

    for policy, expected in manifest["evaluation"]["overall"].items():
        subset = frame[frame["policy"] == policy]
        if len(subset) != expected["parties"]:
            raise RuntimeError(f"{policy} party count mismatch")
        for metric in METRICS:
            assert_close(float(subset[metric].mean()), expected[metric], f"{policy}.{metric}")
    average_loss = manifest["evaluation"]["overall"]["AVERAGE"]["predicted_relevance_loss"]
    assert_close(average_loss, 0.0, "AVERAGE predicted relevance loss")
    if min(
        value["predicted_relevance_loss"]
        for value in manifest["evaluation"]["overall"].values()
    ) < -1e-6:
        raise RuntimeError("a policy exceeds Average predicted mean through a ranking inconsistency")

    parameters = manifest["selection"]["balanced_parameters"]
    matching = [
        row
        for row in manifest["selection"]["top_grid_candidates"]
        if row["floor"] == parameters["floor"]
        and row["floor_weight"] == parameters["floor_weight"]
        and row["gap_weight"] == parameters["gap_weight"]
    ]
    if not matching:
        raise RuntimeError("selected Balanced parameters are absent from the validation grid summary")
    if not manifest["validation"]["selection_and_evaluation_are_disjoint"]:
        raise RuntimeError("selection/evaluation separation failed")
    if not manifest["validation"]["test_did_not_select_thresholds_or_weights"]:
        raise RuntimeError("Test influenced similarity thresholds or Balanced weights")

    balanced_bootstrap = manifest["evaluation"]["paired_bootstrap_vs_average"]["BALANCED"]
    for metric, interval in balanced_bootstrap.items():
        if not (interval["ci95_low"] <= 0.0 <= interval["ci95_high"]):
            raise RuntimeError(f"Balanced {metric} CI no longer crosses zero; review conclusion")
    four_person_test = [
        row
        for row in manifest["evaluation"]["group_coverage"]
        if row["split"] == "test" and row["group_size"] == 4
    ]
    if len(four_person_test) != 3:
        raise RuntimeError("expected three 4-member Test coverage cells")
    for row in four_person_test:
        assert_close(
            row["evaluable_coverage"],
            row["evaluable_groups"] / row["attempted_groups"],
            f"4-member {row['group_type']} coverage",
        )
    if max(row["evaluable_coverage"] for row in four_person_test) > 0.011:
        raise RuntimeError("4-member coverage limitation changed; review evidence conclusion")

    print(
        "REC-EV-005 verification passed: "
        f"{frame['party_label'].nunique()} Test parties, {len(frame)} policy rows, "
        "9 cells, checksums and aggregate metrics valid; no raw IDs tracked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
