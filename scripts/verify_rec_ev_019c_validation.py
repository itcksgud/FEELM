#!/usr/bin/env python3
"""Verify the currently authorized REC-EV-019C synthetic preflight evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-synthetic-preflight.json"
CONTRACT_PATH = ROOT / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _safe_repo_path(relative: str, *, root: Path) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError("manifest artifact escapes repository") from error
    return path


def verify_manifest(manifest_path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    identity = read_json(manifest_path).get("evidence_id")
    if identity == "REC-EV-019C":
        return verify_real_manifest(manifest_path, root=root)
    contract = read_json(root / CONTRACT_PATH.relative_to(ROOT))
    manifest = read_json(manifest_path)
    expected_paths = contract["synthetic_preflight_artifacts"]
    contract_sha = sha256_file(root / CONTRACT_PATH.relative_to(ROOT))

    require(manifest.get("schema_version") == 1, "unexpected synthetic manifest schema")
    require(manifest.get("evidence_id") == "REC-EV-019C-SYNTHETIC-PREFLIGHT", "unexpected evidence id")
    require(manifest.get("status") == "PASS_SYNTHETIC_PREFLIGHT", "synthetic preflight is not PASS")
    require(manifest.get("contract_sha256") == contract_sha, "synthetic manifest contract hash is stale")
    require(manifest.get("source_checksums", {}).get("contract") == contract_sha, "source contract hash mismatch")

    artifacts = manifest.get("artifacts", [])
    require(len(artifacts) == 1, "synthetic manifest must track exactly one result artifact")
    artifact = artifacts[0]
    require(artifact.get("path") == expected_paths["result"], "synthetic result path changed")
    result_path = _safe_repo_path(artifact["path"], root=root)
    require(result_path.is_file(), "synthetic result is missing")
    require(int(artifact.get("bytes", -1)) == result_path.stat().st_size, "synthetic result byte count mismatch")
    require(artifact.get("sha256") == sha256_file(result_path), "synthetic result checksum mismatch")

    result = read_json(result_path)
    for key in expected_paths["required_result_keys"]:
        require(key in result, f"synthetic result key missing: {key}")
    require(result.get("status") == "PASS_SYNTHETIC_PREFLIGHT", "result status is not PASS")
    require(result.get("contract_sha256") == contract_sha, "result contract hash is stale")
    require(result.get("execution_role") == "VALIDATION", "synthetic execution role changed")
    checks = result.get("checks", {})
    for check in expected_paths["required_checks"]:
        require(checks.get(check) is True, f"synthetic safety check failed: {check}")
    require(result.get("real_validation_executed") is False, "real Validation was executed")
    require(result.get("locked_test_opened") is False, "Locked Test was opened")
    require(result.get("product_policy_changed") is False, "product policy changed")
    require(result.get("product_champion") is None, "synthetic preflight selected a champion")
    require(
        result.get("current_product_policy") == "APPROVED_C2A_INTERNAL_POPULARITY_ONLY",
        "current product policy changed",
    )
    require(
        result.get("next_gate") == "LINUX_DEPENDENCY_SMOKE_AND_RESOURCE_DRY_RUN",
        "next Gate changed",
    )

    validation = manifest.get("validation", {})
    require(validation.get("all_required_checks_pass") is True, "manifest did not record all checks PASS")
    require(validation.get("real_validation_executed") is False, "manifest opened real Validation")
    require(validation.get("locked_test_opened") is False, "manifest opened Locked Test")
    adoption = manifest.get("adoption", {})
    require(adoption.get("champion") is None, "manifest selected a champion")
    require(adoption.get("product_policy_changed") is False, "manifest changed product policy")
    require(adoption.get("real_validation_authorized") is False, "manifest authorized real Validation")

    declared_trials = {model_id: int(model["trial_count"]) for model_id, model in contract["models"].items()}
    require(result.get("trial_counts") == declared_trials, "synthetic trial expansion differs from contract")
    return {
        "status": "PASS",
        "evidence_id": result["evidence_id"],
        "checks": len(expected_paths["required_checks"]),
        "candidate_fixture_movies": result["fixture"]["candidate_count"],
        "real_validation_executed": False,
        "locked_test_opened": False,
        "next_gate": result["next_gate"],
        "product_champion": None,
    }


def verify_real_manifest(manifest_path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    import pyarrow.parquet as pq

    contract_path = root / CONTRACT_PATH.relative_to(ROOT)
    contract = read_json(contract_path)
    manifest = read_json(manifest_path)
    contract_sha = sha256_file(contract_path)
    require(manifest.get("schema_version") == 1, "unexpected real Validation manifest schema")
    require(manifest.get("evidence_id") == "REC-EV-019C", "unexpected real Validation evidence id")
    require(manifest.get("status") == "PASS_VALIDATION_SELECTION_LOCKED", "real Validation is not complete")
    require(manifest.get("contract_sha256") == contract_sha, "real Validation contract hash is stale")
    require(manifest.get("validation", {}).get("locked_test_opened") is False, "Locked Test was opened")
    require(manifest.get("adoption", {}).get("champion") is None, "Validation invented a product champion")
    require(manifest.get("adoption", {}).get("product_policy_changed") is False, "Validation changed product policy")

    expected = {item["path"] for item in contract["future_artifacts"] if not item["path"].startswith("docs/")}
    artifacts = manifest.get("artifacts", [])
    by_path = {item["path"]: item for item in artifacts}
    require(set(by_path) == expected, "real Validation artifact inventory changed")
    forbidden = set(contract["forbidden_input_artifacts"])
    require(not forbidden.intersection(by_path), "real Validation manifest contains a forbidden input path")
    for relative, artifact in by_path.items():
        path = _safe_repo_path(relative, root=root)
        require(path.is_file(), f"real Validation artifact missing: {relative}")
        require(path.stat().st_size == int(artifact.get("bytes", -1)), f"artifact byte count mismatch: {relative}")
        require(sha256_file(path) == artifact.get("sha256"), f"artifact checksum mismatch: {relative}")

    output = root / "outputs/recommendation-evidence/rec-ev-019c"
    candidate = pq.ParquetFile(output / "candidate-core-final.parquet")
    trial_metrics = pq.ParquetFile(output / "trial-user-metrics.parquet")
    predictions = pq.ParquetFile(output / "validation-predictions.parquet")
    validation_metrics = pq.ParquetFile(output / "validation-user-metrics.parquet")
    require(candidate.metadata.num_rows == 41625, "candidate row count drift")
    require(trial_metrics.metadata.num_rows == 22272, "trial metric row count drift")
    expected_validation_rows = 1674 + 7 * (1614 + 1479)
    require(validation_metrics.metadata.num_rows == expected_validation_rows, "Validation metric row count drift")
    require(predictions.metadata.num_rows == expected_validation_rows * 500, "prediction row count drift")

    contract_columns = {
        Path(item["path"]).name: [column[0] for column in item.get("columns", [])]
        for item in contract["future_artifacts"] if item.get("columns")
    }
    for name, parquet in (
        ("candidate-core-final.parquet", candidate),
        ("trial-user-metrics.parquet", trial_metrics),
        ("validation-predictions.parquet", predictions),
        ("validation-user-metrics.parquet", validation_metrics),
    ):
        require(parquet.schema_arrow.names == contract_columns[name], f"column contract drift: {name}")

    selection = read_json(output / "validation-selection.json")
    for key in ("tuning_panel", "per_model_per_k", "stability_panel", "single_best_per_k", "all_trial_metrics", "fallback_rates", "seed_variance", "champion"):
        require(key in selection, f"selection key missing: {key}")
    require(selection["champion"] is None, "selection invented a champion")
    require(set(selection["single_best_per_k"]) == {"0", "5", "10"}, "single-best K coverage changed")
    lock = read_json(output / "validation-selection-lock.json")
    require(lock.get("contract_sha256") == contract_sha, "selection lock contract hash mismatch")
    require(lock.get("candidate_count") == 41625, "selection lock candidate count drift")
    require(lock.get("created_before_test_read") is True, "selection lock timing invariant failed")
    require(lock.get("locked_test_opened") is False, "selection lock reports Test access")
    require(lock.get("validation_user_counts") == {"0": 1674, "5": 1614, "10": 1479}, "selection lock user counts drift")

    resource = read_json(output / "resource-summary.json")
    limits = contract["resource_execution_plan"]["budgets"]
    mapping = {
        "full_catalog_user_item_scores": "maximum_full_catalog_user_item_scores",
        "b8_base_updates": "maximum_b8_base_updates",
        "b4_pair_updates": "maximum_b4_pair_updates",
        "rrf_rank_contributions": "maximum_rrf_rank_contributions",
    }
    for counter, limit in mapping.items():
        require(int(resource.get("budget_counters", {}).get(counter, 0)) <= int(limits[limit]), f"resource budget exceeded: {counter}")
    require(math.isfinite(float(resource.get("wall_clock_seconds", float("nan")))), "invalid wall-clock metric")
    return {
        "status": "PASS",
        "evidence_id": "REC-EV-019C",
        "candidate_movies": 41625,
        "validation_metric_rows": expected_validation_rows,
        "prediction_rows": expected_validation_rows * 500,
        "single_best_per_k": selection["single_best_per_k"],
        "locked_test_opened": False,
        "product_champion": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify REC-EV-019C synthetic preflight")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_manifest(args.manifest.resolve()), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-019C verification failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
