#!/usr/bin/env python3
"""Verify REC-EV-019C metadata-only workload evidence."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"
DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-resource-dry-run.json"

EXPECTED_INPUT_ROWS = {
    "base_train_ratings": 10_254_572,
    "candidate_core_provisional": 42_123,
    "validation_prefixes": 22_860,
    "validation_windows": 47_670,
    "movie_identity": 69_603,
    "structured_features": 68_674,
    "text_embeddings": 68_674,
}
EXPECTED_BLOCKERS = [
    "B4_PAIR_SAMPLING_UNDEFINED",
    "STOCHASTIC_GRID_REPEATS_ALL_FIVE_SEEDS",
    "B8_WORST_CASE_UPDATE_BUDGET_UNBOUNDED",
    "FULL_CATALOG_SCORE_BUDGET_UNAPPROVED",
]


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
        raise RuntimeError("resource dry-run artifact escapes repository") from error
    return path


def _grid_trial_count(model: Mapping[str, Any]) -> int:
    if "ordered_variants" in model:
        return len(model["ordered_variants"])
    search_space = model.get("search_space", {})
    return len(list(itertools.product(*(search_space[key] for key in search_space)))) if search_space else 1


def expected_work(contract: Mapping[str, Any]) -> dict[str, Any]:
    candidate_count = int(contract["candidate_and_ranking"]["core_movie_count"])
    validation_counts = {
        int(k): int(value)
        for k, value in contract["source_preconditions"]["validation_strict_users_by_k"].items()
    }
    all_contexts = sum(validation_counts.values())
    personalized_contexts = validation_counts[5] + validation_counts[10]
    repeats = {
        model_id: _grid_trial_count(model) * max(1, len(model.get("stochastic_seeds", [])))
        for model_id, model in contract["models"].items()
    }
    score_counts = {
        model_id: (
            0
            if model_id == "B9_RRF"
            else repeat_count
            * (all_contexts if model_id == "B0_MOVIELENS_BAYESIAN_RATING" else personalized_contexts)
            * candidate_count
        )
        for model_id, repeat_count in repeats.items()
    }
    b8 = contract["models"]["B8_LIGHTFM"]
    return {
        "candidate_movies": candidate_count,
        "validation_contexts_all_k": all_contexts,
        "validation_contexts_personalized_k": personalized_contexts,
        "trial_seed_repeats": repeats,
        "full_catalog_user_item_scores_by_model": score_counts,
        "full_catalog_user_item_scores": sum(score_counts.values()),
        "b8_base_update_upper_bound": (
            repeats["B8_LIGHTFM"]
            * int(b8["fixed_parameters"]["epochs"])
            * int(contract["base_training_semantics"]["base_rating_rows"])
        ),
        "trial_user_metric_rows": (
            repeats["B0_MOVIELENS_BAYESIAN_RATING"] * all_contexts
            + sum(value for key, value in repeats.items() if key != "B0_MOVIELENS_BAYESIAN_RATING")
            * personalized_contexts
        ),
        "selected_prediction_rows": (
            validation_counts[0] + len(contract["models"]) * personalized_contexts
        )
        * int(contract["candidate_and_ranking"]["top_candidates"]),
        "maximum_score_buffer_bytes": (
            int(contract["candidate_and_ranking"]["user_batch_size_max"])
            * int(contract["candidate_and_ranking"]["candidate_block_size_max"])
            * 4
        ),
    }


def verify_manifest(manifest_path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    contract_path = root / CONTRACT_PATH.relative_to(ROOT)
    contract = read_json(contract_path)
    manifest = read_json(manifest_path)
    contract_sha = sha256_file(contract_path)
    artifact_contract = contract["resource_dry_run_artifacts"]

    require(manifest.get("schema_version") == 1, "unexpected resource dry-run manifest schema")
    require(manifest.get("evidence_id") == "REC-EV-019C-RESOURCE-DRY-RUN", "unexpected evidence id")
    require(
        manifest.get("status") == "PASS_METADATA_AUDIT_IMPLEMENTATION_BLOCKED",
        "resource dry-run status changed",
    )
    require(manifest.get("contract_sha256") == contract_sha, "resource dry-run contract hash is stale")
    artifacts = manifest.get("artifacts", [])
    require(len(artifacts) == 1, "resource dry-run must track exactly one result")
    artifact = artifacts[0]
    require(artifact.get("path") == artifact_contract["result"], "resource dry-run result path changed")
    result_path = _safe_repo_path(artifact["path"], root=root)
    require(result_path.is_file(), "resource dry-run result is missing")
    require(artifact.get("bytes") == result_path.stat().st_size, "resource result byte count mismatch")
    require(artifact.get("sha256") == sha256_file(result_path), "resource result checksum mismatch")

    result = read_json(result_path)
    for key in artifact_contract["required_result_keys"]:
        require(key in result, f"resource result key missing: {key}")
    require(result.get("status") == manifest["status"], "resource result status differs from manifest")
    require(result.get("contract_sha256") == contract_sha, "resource result contract hash is stale")
    require(result.get("rating_rows_or_feature_vectors_read") is False, "resource dry-run read data rows")
    input_metadata = result.get("input_metadata", {})
    require(set(input_metadata) == set(EXPECTED_INPUT_ROWS), "resource input inventory changed")
    for name, expected_rows in EXPECTED_INPUT_ROWS.items():
        metadata = input_metadata[name]
        require(metadata.get("path_class") == name, f"resource path class changed: {name}")
        require(metadata.get("rows") == expected_rows, f"resource input row count changed: {name}")
        require(int(metadata.get("row_groups", 0)) > 0, f"resource row groups missing: {name}")
        require(int(metadata.get("columns", 0)) > 0, f"resource column count missing: {name}")
        require(int(metadata.get("file_bytes", 0)) > 0, f"resource file size missing: {name}")

    require(result.get("estimated_work") == expected_work(contract), "resource workload estimate differs from contract")
    blockers = result.get("implementation_blockers", [])
    require([item.get("id") for item in blockers] == EXPECTED_BLOCKERS, "resource blocker set changed")
    require(all(item.get("reason") for item in blockers), "resource blocker reason is missing")
    require(result.get("real_validation_ready") is False, "resource dry-run authorized real Validation")
    for key in ("real_validation_executed", "locked_test_opened", "product_policy_changed"):
        require(result.get(key) is False, f"resource dry-run crossed boundary: {key}")
    require(result.get("product_champion") is None, "resource dry-run selected a champion")
    require(
        result.get("next_gate") == "AMEND_B4_PAIR_AND_STOCHASTIC_RESOURCE_BUDGET",
        "resource next Gate changed",
    )
    serialized = json.dumps(result, ensure_ascii=False).lower()
    for forbidden in ("locked-test", "locked_test_evaluation", "test.parquet"):
        require(forbidden not in serialized, f"resource evidence exposed forbidden path text: {forbidden}")

    validation = manifest.get("validation", {})
    require(validation.get("metadata_only") is True, "manifest metadata-only flag changed")
    require(validation.get("rating_rows_or_feature_vectors_read") is False, "manifest says rows were read")
    require(validation.get("real_validation_ready") is False, "manifest authorized real Validation")
    require(validation.get("real_validation_executed") is False, "manifest executed real Validation")
    require(validation.get("locked_test_opened") is False, "manifest opened Locked Test")
    require(validation.get("blocker_ids") == EXPECTED_BLOCKERS, "manifest blocker set changed")
    adoption = manifest.get("adoption", {})
    require(adoption.get("champion") is None, "manifest selected a champion")
    require(adoption.get("product_policy_changed") is False, "manifest changed product policy")
    require(adoption.get("real_validation_authorized") is False, "manifest authorized Validation")
    return {
        "status": "PASS",
        "evidence_id": result["evidence_id"],
        "candidate_movies": result["estimated_work"]["candidate_movies"],
        "full_catalog_user_item_scores": result["estimated_work"]["full_catalog_user_item_scores"],
        "b8_base_update_upper_bound": result["estimated_work"]["b8_base_update_upper_bound"],
        "blockers": EXPECTED_BLOCKERS,
        "real_validation_ready": False,
        "locked_test_opened": False,
        "product_champion": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify REC-EV-019C metadata-only resource dry-run")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_manifest(args.manifest.resolve()), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-019C resource dry-run verification failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
