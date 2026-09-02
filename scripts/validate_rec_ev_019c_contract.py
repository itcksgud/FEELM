#!/usr/bin/env python3
"""Validate the REC-EV-019C implementation contract without running models."""

from __future__ import annotations

import argparse
import hashlib
import json
from math import prod
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = (
    ROOT / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"
)
EXPECTED_MODELS = {
    "B0_MOVIELENS_BAYESIAN_RATING",
    "B2_ITEM_KNN",
    "B4_BPR_MF",
    "B6_TMDB_STRUCTURED_CONTENT",
    "B7_TMDB_TEXT_CONTENT",
    "B8_LIGHTFM",
    "B9_RRF",
}
EXPECTED_ALLOWED_INPUTS = {
    "base_train_ratings": "outputs/recommendation-evidence/rec-ev-019a/base-train-ratings.parquet",
    "candidate_core_provisional": "outputs/recommendation-evidence/rec-ev-019a/candidate-core-provisional.parquet",
    "validation_prefixes": "outputs/recommendation-evidence/rec-ev-019a/validation-binary-prefixes.parquet",
    "validation_windows": "outputs/recommendation-evidence/rec-ev-019a/validation-evaluation-windows.parquet",
    "movie_identity": "outputs/recommendation-evidence/rec-ev-019b/movie-identity.parquet",
    "structured_features": "outputs/recommendation-evidence/rec-ev-019b/structured-features.parquet",
    "text_embeddings": "outputs/recommendation-evidence/rec-ev-019b/text-embeddings.parquet",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def expanded_trial_count(model: dict[str, Any]) -> int:
    if "ordered_variants" in model:
        return len(model["ordered_variants"])
    search_space = model.get("search_space", {})
    return prod(len(values) for values in search_space.values()) if search_space else 1


def _artifact_paths(manifest: dict[str, Any]) -> set[str]:
    return {str(item.get("path")) for item in manifest.get("artifacts", [])}


def validate_contract(contract: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    require(contract.get("contract_id") == "rec-ev-019c-validation-artifacts-v1", "unexpected 019C contract id")
    require(
        contract.get("status") == "APPROVED_FOR_IMPLEMENTATION_AND_SYNTHETIC_PREFLIGHT_ONLY",
        "019C authorization status changed",
    )
    require(contract.get("task_id") == "TASK-REC-EV-019C", "019C task id changed")
    require(contract.get("protocol_version") == "rec-eval-vnext-2", "019C protocol version changed")

    authorization = contract["current_authorization"]
    for key in ("contract_validation", "runner_implementation", "synthetic_preflight"):
        require(authorization.get(key) is True, f"019C safe implementation authorization missing: {key}")
    for key in ("real_validation_fit_or_score", "locked_test_access", "champion_selection", "product_policy_change"):
        require(authorization.get(key) is False, f"019C unsafe authorization opened: {key}")

    sources = contract["source_contracts"]
    source_docs = {name: root / path for name, path in sources.items()}
    for name, path in source_docs.items():
        require(path.is_file(), f"019C source document missing: {name}")
    protocol = read_json(source_docs["protocol"])
    cohort_contract = read_json(source_docs["cohort_contract"])
    cohort_manifest = read_json(source_docs["cohort_manifest"])
    feature_contract = read_json(source_docs["feature_contract"])
    feature_manifest = read_json(source_docs["feature_manifest"])

    require(protocol.get("protocol_version") == contract["protocol_version"], "019C protocol source mismatch")
    require(cohort_manifest.get("status") == contract["source_preconditions"]["cohort_manifest_status"], "019A status precondition failed")
    require(feature_manifest.get("status") == contract["source_preconditions"]["feature_manifest_status"], "019B status precondition failed")
    require(cohort_manifest.get("contract_sha256") == sha256_file(source_docs["cohort_contract"]), "019A contract checksum is stale")
    require(feature_manifest.get("contract_sha256") == sha256_file(source_docs["feature_contract"]), "019B contract checksum is stale")

    allowed = contract["allowed_input_artifacts"]
    require(allowed == EXPECTED_ALLOWED_INPUTS, "019C allowed input artifact list changed")
    cohort_artifacts = _artifact_paths(cohort_manifest)
    feature_artifacts = _artifact_paths(feature_manifest)
    for name, path in allowed.items():
        expected_inventory = feature_artifacts if name in {"movie_identity", "structured_features", "text_embeddings"} else cohort_artifacts
        require(path in expected_inventory, f"019C allowed artifact is not checksum-tracked: {name}")

    forbidden = set(contract["forbidden_input_artifacts"])
    require(not forbidden.intersection(allowed.values()), "019C allowed and forbidden artifacts overlap")
    for required_path in (
        "outputs/recommendation-evidence/global-time-v1/test.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/binary-prefixes.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/evaluation-windows.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/locked-test-binary-prefixes.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/locked-test-evaluation-windows.parquet",
    ):
        require(required_path in forbidden, f"019C role firewall is missing: {required_path}")
    role_firewall = contract["role_firewall"]
    require(role_firewall.get("execution_role") == "VALIDATION", "019C must be Validation-only")
    require(role_firewall.get("file_allowlist_enforced_before_data_read") is True, "019C file allowlist must run before reads")
    require(role_firewall.get("validation_runner_must_not_offer_test_flag") is True, "019C runner could expose Test")

    preconditions = contract["source_preconditions"]
    validation = cohort_manifest["validation"]
    require(int(preconditions["provisional_candidate_movies"]) == 42123, "019C provisional candidate count changed")
    require(int(preconditions["final_identity_candidate_movies"]) == int(validation["final_identity_candidate_movies"]) == 41625, "019C final candidate count changed")
    require(int(preconditions["locked_test_k10_structural_users_recorded_but_not_read"]) == int(validation["locked_test_k10_final_identity_eligible"]) == 5476, "019C structural Test count changed")
    strict_counts = validation.get("strict_eligible_users_by_role_and_k", {})
    require(
        strict_counts.get("VALIDATION") == preconditions["validation_strict_users_by_k"],
        "019C Validation user counts differ from the tracked 019A manifest",
    )
    require(int(preconditions["minimum_locked_test_k10_users"]) == 5000, "019C minimum user Gate changed")
    require(preconditions.get("feature_superset_is_candidate_authority") is False, "019B feature superset became candidate authority")
    require(preconditions.get("identity_allowlist") == protocol["candidate"]["final_identity_status_allowlist"], "019C identity allowlist changed")

    signals = contract["binary_input_semantics"]
    require(signals.get("k_values") == [0, 5, 10], "019C binary K changed")
    require(signals.get("allowed_labels") == [-1, 1], "019C binary labels changed")
    for key in ("numeric_rating_conversion_forbidden", "unrated_as_negative_forbidden", "neutral_as_dislike_forbidden"):
        require(signals.get(key) is True, f"019C input safety boundary missing: {key}")
    require(signals.get("system_visible_seen_items") == "ONLY_MOVIE_IDS_IN_THE_USER_K_PREFIX", "019C seen-item meaning changed")

    candidate = contract["candidate_and_ranking"]
    require(candidate.get("core_movie_count") == 41625, "019C ranking candidate count changed")
    require(candidate.get("same_core_across_models") is True, "019C model candidate universes may differ")
    require(candidate.get("positive_injection") is False, "019C positive injection opened")
    require(candidate.get("full_score_matrix_persistence_forbidden") is True, "019C full score matrix persistence opened")
    require(candidate.get("top_candidates") == 500 and candidate.get("top_k") == 10, "019C ranking depths changed")
    require(candidate.get("tie_break") == ["effective_score_desc", "movie_id_asc"], "019C tie break changed")
    require(candidate.get("raw_cross_model_score_sum_forbidden") is True, "019C raw score mixing opened")
    require(candidate.get("missing_model_feature_policy") == "KEEP_CANDIDATE_AND_USE_B0_PERCENTILE_SCORE", "019C missing-feature fallback changed")

    training = contract["base_training_semantics"]
    require(training.get("training_users") == "BASE_TRAIN_ONLY", "019C base training user role changed")
    require(training.get("router_validation_test_ratings_forbidden") is True, "019C held-out users could enter base fit")
    require(training.get("bpr_unrated_negative_sampling_forbidden") is True, "019C BPR could treat unrated as negative")
    require(training.get("target_user_updates_must_not_change_base_item_parameters") is True, "019C target fold-in could mutate base items")

    models = contract["models"]
    require(set(models) == EXPECTED_MODELS, "019C required model suite changed")
    maximum_trials = int(contract["trial_execution"]["maximum_trials_per_model"])
    require(maximum_trials == int(protocol["selection"]["maximum_trials_per_model"]) == 30, "019C trial limit changed")
    trial_counts: dict[str, int] = {}
    for model_id, model in models.items():
        expanded = expanded_trial_count(model)
        declared = int(model["trial_count"])
        require(expanded == declared, f"019C trial count mismatch: {model_id}")
        require(declared <= maximum_trials, f"019C model exceeds trial limit: {model_id}")
        trial_counts[model_id] = declared
    require(models["B4_BPR_MF"].get("pair_semantics") == "BASE_AND_TARGET_PAIRS_REQUIRE_ONE_OBSERVED_LIKE_AND_ONE_OBSERVED_DISLIKE", "019C BPR pair semantics changed")
    require(models["B8_LIGHTFM"].get("dependency_rule") == "AN_EXACT_VERSION_AND_HASHED_LOCK_MUST_EXIST_BEFORE_REAL_VALIDATION_RUN", "019C LightFM supply-chain Gate missing")
    require(models["B7_TMDB_TEXT_CONTENT"].get("model_revision") == feature_contract["embedding"]["model_revision"], "019C E5 revision differs from 019B")
    require(models["B9_RRF"].get("raw_score_input_forbidden") is True, "019C RRF could consume raw scores")
    require(models["B9_RRF"]["head_sets"]["ALL_NONBASE"] == ["B2_ITEM_KNN", "B4_BPR_MF", "B6_TMDB_STRUCTURED_CONTENT", "B7_TMDB_TEXT_CONTENT", "B8_LIGHTFM"], "019C RRF full head set changed")

    execution = contract["trial_execution"]
    require(execution.get("model_order") == list(models), "019C model execution order differs from contract order")
    require(execution.get("rrf_runs_after_single_head_selection") is True, "019C RRF dependency ordering changed")
    require(execution.get("checkpoint_after_each_trial_seed_and_user_batch") is True, "019C checkpoint boundary weakened")
    require(execution.get("skip_required_model_forbidden") is True, "019C required model could be silently skipped")

    selection = contract["validation_selection"]
    require(selection.get("role") == "VALIDATION_ONLY", "019C selection role changed")
    require(selection.get("primary_metric") == protocol["selection"]["primary_metric"], "019C primary metric changed")
    require(selection.get("k0_rule") == "B0_ONLY; PERSONALIZED_HEAD_EQUIVALENCE_TO_B0_IS_DIAGNOSTIC_NOT_A_WIN", "019C K0 rule changed")
    require(selection.get("selection_lock_required_before_any_locked_test_path_can_be_opened") is True, "019C Test lock boundary missing")
    require(selection.get("test_retuning_forbidden") is True, "019C Test retuning opened")

    artifact_paths = [item["path"] for item in contract["future_artifacts"]]
    require(len(artifact_paths) == len(set(artifact_paths)) == 9, "019C future artifact inventory changed")
    for artifact in contract["future_artifacts"]:
        columns = artifact.get("columns", [])
        column_names = {column[0] for column in columns}
        require("user_id" not in column_names, f"019C raw user_id column declared: {artifact['path']}")
    prediction = next(item for item in contract["future_artifacts"] if item["path"].endswith("validation-predictions.parquet"))
    require({"rank", "effective_score", "fallback_used", "fallback_reason"}.issubset({column[0] for column in prediction["columns"]}), "019C prediction audit columns missing")

    implementation = contract["implementation"]
    for key in (
        "contract_validator",
        "contract_unit_test",
        "runner_to_create",
        "runner_unit_test_to_create",
        "result_verifier_to_create",
        "contract_check_command",
        "contract_unit_command",
        "future_synthetic_preflight_command",
        "future_validation_command",
        "future_verify_command",
    ):
        require(bool(implementation.get(key)), f"019C implementation field missing: {key}")
    require(contract["adoption_boundary"].get("validation_output_champion") is None, "019C contract invented a champion")
    require(contract["adoption_boundary"].get("current_product_policy") == "APPROVED_C2A_INTERNAL_POPULARITY_ONLY", "019C product policy changed")

    return {
        "status": "PASS",
        "decision": "GO_FOR_RUNNER_IMPLEMENTATION_AND_SYNTHETIC_PREFLIGHT_ONLY",
        "contract_id": contract["contract_id"],
        "candidate_movies": 41625,
        "validation_users_k10": preconditions["validation_strict_users_by_k"]["10"],
        "locked_test_opened": False,
        "models": list(models),
        "trial_counts": trial_counts,
        "maximum_trials_per_model": maximum_trials,
        "product_champion": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate REC-EV-019C contract")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    try:
        contract = read_json(args.contract.resolve())
        print(json.dumps(validate_contract(contract), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-019C contract validation failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
