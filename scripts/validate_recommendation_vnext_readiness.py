from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from verify_recommendation_binary_onboarding_preflight import verify as verify_preflight


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def read_json(path: str) -> dict[str, Any]:
    return json.loads(read_text(path))


def read_yaml(path: str) -> dict[str, Any]:
    return yaml.safe_load(read_text(path))


def require_approved_document(path: str) -> None:
    first_lines = "\n".join(read_text(path).splitlines()[:8])
    if "`APPROVED`" not in first_lines:
        raise RuntimeError(f"offline implementation contract is not APPROVED: {path}")


def validate_protocol(protocol: dict[str, Any]) -> None:
    if protocol.get("status") != "APPROVED_FOR_OFFLINE_IMPLEMENTATION_NOT_PRODUCT_APPROVED":
        raise RuntimeError("vNext protocol is not approved for offline implementation")
    if protocol.get("protocol_version") != "rec-eval-vnext-2":
        raise RuntimeError("strict vNext protocol version is not locked")
    if protocol["user_split"] != {
        "algorithm": "uint64_be(SHA256('feelm-rec-vnext-user-split-v1|' + userId)[0:8]) mod 100",
        "base_train_buckets": [0, 39],
        "router_train_buckets": [40, 49],
        "validation_buckets": [50, 59],
        "test_buckets": [60, 99],
        "raw_user_id_in_results": False,
    }:
        raise RuntimeError("user split differs from REC-EV-019P lock")

    required_models = {
        "B0_MOVIELENS_BAYESIAN_RATING",
        "B1_GLOBAL_USER_ITEM_BIAS",
        "B2_ITEM_KNN",
        "B3_EXPLICIT_ALS_FOLD_IN",
        "B4_BPR_MF",
        "B5_EASE",
        "B6_TMDB_STRUCTURED_CONTENT",
        "B7_TMDB_TEXT_CONTENT",
        "B8_LIGHTFM",
        "B9_RRF",
        "B10_K_AWARE_ROUTER",
    }
    if set(protocol["models"]) != required_models:
        raise RuntimeError("strong baseline ladder is incomplete")
    if protocol["inputs"]["binary_k_primary"] != [0, 5, 10]:
        raise RuntimeError("binary K contract changed")
    if protocol["inputs"]["rating_k"] != [0, 1, 3, 5, 10, 20, 30, 50]:
        raise RuntimeError("rating K contract changed")
    if protocol["inputs"].get("binary_to_numeric_rating_forbidden") is not True:
        raise RuntimeError("binary-to-rating prohibition is missing")
    if protocol["candidate"] != {
        "evaluation": "FULL_CATALOG",
        "base_identity_universe": "BASE_TRAIN_MOVIE_INTERSECT_MOVIELENS_LINKS_TMDB_ID_PRESENT",
        "provisional_identity_basis": "MOVIELENS_LINKS_TMDB_ID_PRESENT",
        "final_identity_status_allowlist": ["ML_TMDB_VERIFIED", "RECOVERED_BY_IMDB"],
        "identity_quarantine_excluded_before_scoring": True,
        "missing_model_artifact_policy": "KEEP_WITH_DECLARED_FALLBACK",
        "content_fallback": "B0_BAYESIAN_POPULARITY",
        "positive_injection": False,
        "exclude_user_seen": True,
        "same_universe_across_models": True,
        "top_candidates": 500,
        "top_k": 10,
    }:
        raise RuntimeError("candidate contract changed")
    relevance = protocol["relevance"]
    if relevance.get("minimum_future_ratings") != 10:
        raise RuntimeError("strict eligibility future window changed")
    if relevance.get("minimum_future_positives") != 3:
        raise RuntimeError("strict eligibility minimum positives changed")
    if relevance.get("minimum_candidate_positives") != 1:
        raise RuntimeError("strict eligibility candidate-positive changed")
    if relevance.get("future_positive_definition") != (
        "MIDRANK_WITHIN_FIRST_10_POST_PREFIX_RATINGS"
    ):
        raise RuntimeError("strict eligibility positive definition changed")

    statistics = protocol["statistics"]
    expected_statistics = {
        "minimum_test_users": 5000,
        "bootstrap_repeats": 2000,
        "multiple_testing": "HOLM",
        "ranking_sesoi_absolute_ndcg_at_10": 0.002,
        "ranking_sesoi_relative": 0.05,
        "segment_non_inferiority_ndcg_at_10": -0.002,
    }
    for key, expected in expected_statistics.items():
        if statistics.get(key) != expected:
            raise RuntimeError(f"statistical contract changed: {key}")
    expected_experiments = {f"REC-EV-{number:03d}" for number in range(19, 27)}
    if set(protocol["experiments"]) != expected_experiments:
        raise RuntimeError("REC-EV-019..026 mapping is incomplete")
    if protocol["adoption"].get("current_policy") != "APPROVED_C2A_INTERNAL_POPULARITY_ONLY":
        raise RuntimeError("current fallback policy changed")
    if protocol["adoption"].get("personal_champion") is not None:
        raise RuntimeError("readiness must not invent a personal champion")


def validate_artifact_contracts() -> None:
    contract_a = read_json(
        "docs/recommendation/contracts/rec-ev-019a-artifacts.json"
    )
    contract_b = read_json(
        "docs/recommendation/contracts/rec-ev-019b-artifacts.json"
    )
    for task_id, contract in (
        ("TASK-REC-EV-019A", contract_a),
        ("TASK-REC-EV-019B", contract_b),
    ):
        if contract.get("status") != "APPROVED":
            raise RuntimeError(f"artifact contract is not approved: {task_id}")
        if contract.get("task_id") != task_id:
            raise RuntimeError(f"artifact contract task mismatch: {task_id}")
        if contract.get("protocol_version") != "rec-eval-vnext-2":
            raise RuntimeError(f"artifact contract protocol mismatch: {task_id}")
        implementation = contract.get("implementation", {})
        for key in (
            "runner_to_create",
            "unit_test_to_create",
            "verifier_to_create",
            "build_command",
            "unit_command",
            "verify_command",
        ):
            if not implementation.get(key):
                raise RuntimeError(f"artifact contract command is missing: {task_id}.{key}")
        if "APPROVED_C2A_INTERNAL_POPULARITY_ONLY" not in contract.get(
            "forbidden_changes", []
        ):
            raise RuntimeError(f"product fallback protection is missing: {task_id}")

    if contract_a["gates"].get("locked_test_k10_strict_eligible_min") != 5000:
        raise RuntimeError("019A strict K10 gate changed")
    if contract_a["privacy"].get("raw_user_id_in_outputs") is not False:
        raise RuntimeError("019A raw user ID prohibition changed")
    a_paths = {item["path"] for item in contract_a.get("artifacts", [])}
    required_a_paths = {
        "outputs/recommendation-evidence/rec-ev-019a/base-train-ratings.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/candidate-core-provisional.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/binary-prefixes.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/evaluation-windows.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/router-train-binary-prefixes.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/router-train-evaluation-windows.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/validation-binary-prefixes.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/validation-evaluation-windows.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/locked-test-binary-prefixes.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/locked-test-evaluation-windows.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/cohort-summary.json",
        "outputs/recommendation-evidence/rec-ev-019a/protocol-lock.json",
        "docs/recommendation/evidence/manifests/rec-ev-019a.json",
    }
    if a_paths != required_a_paths:
        raise RuntimeError("019A artifact paths are incomplete")
    for item in contract_a["artifacts"]:
        if item.get("format") == "parquet" and not item.get("columns"):
            raise RuntimeError(f"019A parquet schema is missing: {item['path']}")
    firewall = contract_a.get("role_file_firewall", {})
    if set(firewall.get("validation_model_runner_allowed", [])) != {
        "outputs/recommendation-evidence/rec-ev-019a/validation-binary-prefixes.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/validation-evaluation-windows.parquet",
    }:
        raise RuntimeError("019A Validation role-file allowlist changed")
    forbidden = set(firewall.get("validation_model_runner_forbidden", []))
    if "outputs/recommendation-evidence/global-time-v1/test.parquet" not in forbidden:
        raise RuntimeError("019A Locked Test source firewall is incomplete")

    embedding = contract_b["embedding"]
    if embedding.get("model_id") != "intfloat/multilingual-e5-small":
        raise RuntimeError("019B embedding model changed")
    if embedding.get("model_revision") != (
        "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    ):
        raise RuntimeError("019B embedding revision is not pinned")
    if embedding.get("dimension") != 384 or embedding.get("normalization") != "L2":
        raise RuntimeError("019B embedding schema changed")
    transport = contract_b["transport"]
    if transport.get("maximum_attempts") != 5 or transport.get(
        "checkpoint_every_movies"
    ) != 100 or transport.get("resume_required") is not True:
        raise RuntimeError("019B retry/resume contract changed")
    gates_b = contract_b["gates"]
    if gates_b.get("verified_or_recovered_identity_rate_of_linked_min") != 0.98:
        raise RuntimeError("019B identity coverage gate changed")
    if gates_b.get("structured_feature_eligible_rate_of_identity_eligible_min") != 0.95:
        raise RuntimeError("019B structured coverage gate changed")
    if gates_b.get("text_feature_eligible_rate_of_identity_eligible_min") != 0.95:
        raise RuntimeError("019B text coverage gate changed")
    if contract_b["missing_feature_behavior"].get(
        "remove_from_core_candidate_universe"
    ) is not False:
        raise RuntimeError("019B missing features must not shrink the candidate universe")
    derivation_b = contract_b["candidate_derivation"]
    if derivation_b.get("time_safe_candidate_authority") is not False:
        raise RuntimeError("019B broad feature superset must not claim candidate authority")
    scoring_rule = derivation_b.get("downstream_scoring_rule", "")
    for required_text in ("019A", "cutoff-safe", "ML_TMDB_VERIFIED", "RECOVERED_BY_IMDB"):
        if required_text not in scoring_rule:
            raise RuntimeError(f"019B downstream candidate boundary is incomplete: {required_text}")
    required_forbidden = {
        "popularity",
        "vote_average",
        "vote_count",
        "watch_providers",
    }
    if not required_forbidden.issubset(
        set(contract_b.get("preference_feature_forbidden_fields", []))
    ):
        raise RuntimeError("019B forbidden preference features are incomplete")


def validate_backlog(backlog: dict[str, Any]) -> None:
    if backlog.get("status") != "LOCAL_PRODUCT_BOUNDARIES_APPROVED":
        raise RuntimeError("existing recommendation product-boundary status drifted")
    if backlog.get("vnext_status") != "VNEXT_OFFLINE_IMPLEMENTATION_GO":
        raise RuntimeError("recommendation backlog is not in implementation GO state")
    tasks = {task["id"]: task for task in backlog.get("tasks", [])}
    required = {
        "TASK-REC-EV-019P": "DONE",
        "TASK-REC-EV-019A": "DONE",
        "TASK-REC-EV-019B": "DONE",
        "TASK-REC-EV-019C": "READY",
        "TASK-REC-EV-019": "PENDING",
        "TASK-REC-EV-020": "PENDING",
        "TASK-REC-EV-021": "PENDING",
        "TASK-REC-EV-022": "PENDING",
        "TASK-REC-EV-023": "PENDING",
        "TASK-REC-EV-024": "PENDING",
        "TASK-REC-EV-025": "PENDING",
        "TASK-REC-EV-026": "PENDING",
    }
    for task_id, status in required.items():
        if task_id not in tasks or tasks[task_id].get("status") != status:
            raise RuntimeError(f"backlog task is missing or has wrong status: {task_id}")
        if not tasks[task_id].get("outputs"):
            raise RuntimeError(f"backlog task has no outputs: {task_id}")
    for task_id in ("TASK-REC-EV-019P", "TASK-REC-EV-019A", "TASK-REC-EV-019B", "TASK-REC-EV-019C", "TASK-REC-EV-019"):
        if not tasks[task_id].get("verify"):
            raise RuntimeError(f"executable verification is missing: {task_id}")
    expected_contracts = {
        "TASK-REC-EV-019A": "docs/recommendation/contracts/rec-ev-019a-artifacts.json",
        "TASK-REC-EV-019B": "docs/recommendation/contracts/rec-ev-019b-artifacts.json",
    }
    for task_id, contract_path in expected_contracts.items():
        task = tasks[task_id]
        if task.get("artifact_contract") != contract_path:
            raise RuntimeError(f"artifact contract is missing from backlog: {task_id}")
        commands = task.get("commands", {})
        if set(commands) != {"build", "unit", "verify"}:
            raise RuntimeError(f"task-specific commands are incomplete: {task_id}")
        if not all(str(command).startswith("py -3 ") for command in commands.values()):
            raise RuntimeError(f"task command is not executable: {task_id}")
        if "APPROVED_C2A_INTERNAL_POPULARITY_ONLY" not in task.get(
            "forbidden_changes", []
        ):
            raise RuntimeError(f"task product boundary is missing: {task_id}")
        contract = read_json(contract_path)
        contract_paths = {artifact["path"] for artifact in contract["artifacts"]}
        if set(task["outputs"]) != contract_paths:
            raise RuntimeError(f"task outputs differ from artifact contract: {task_id}")
        expected_commands = {
            "build": contract["implementation"]["build_command"],
            "unit": contract["implementation"]["unit_command"],
            "verify": contract["implementation"]["verify_command"],
        }
        if commands != expected_commands:
            raise RuntimeError(f"task commands differ from artifact contract: {task_id}")
    task_019c = tasks["TASK-REC-EV-019C"]
    contract_019c_path = "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"
    if task_019c.get("artifact_contract") != contract_019c_path:
        raise RuntimeError("019C artifact contract is missing from backlog")
    contract_019c = read_json(contract_019c_path)
    if task_019c.get("current_authorization") != "IMPLEMENTATION_SYNTHETIC_PREFLIGHT_AND_METADATA_DRY_RUN_ONLY":
        raise RuntimeError("019C backlog authorization is too broad")
    expected_019c_outputs = {item["path"] for item in contract_019c["future_artifacts"]}
    if set(task_019c.get("outputs", [])) != expected_019c_outputs:
        raise RuntimeError("019C outputs differ from its artifact contract")
    commands_019c = task_019c.get("commands", {})
    implementation_019c = contract_019c["implementation"]
    expected_019c_commands = {
        "contract": implementation_019c["contract_check_command"],
        "unit": implementation_019c["contract_unit_command"],
        "future_synthetic_preflight": implementation_019c["future_synthetic_preflight_command"],
        "synthetic_preflight_verify": implementation_019c["synthetic_preflight_verify_command"],
        "dependency_smoke_run": implementation_019c["dependency_smoke_run_command"],
        "dependency_smoke_verify": implementation_019c["dependency_smoke_verify_command"],
        "resource_dry_run": implementation_019c["resource_dry_run_command"],
        "resource_dry_run_verify": implementation_019c["resource_dry_run_verify_command"],
        "future_validation": implementation_019c["future_validation_command"],
        "future_verify": implementation_019c["future_verify_command"],
    }
    if commands_019c != expected_019c_commands:
        raise RuntimeError("019C commands differ from its artifact contract")
    verify_019c = "\n".join(tasks["TASK-REC-EV-019C"]["verify"])
    for required_text in ("final identity allowlist", "at least 5000", "B0 fallback"):
        if required_text not in verify_019c:
            raise RuntimeError(f"019C final candidate gate is incomplete: {required_text}")


def validate_current_product_boundary() -> None:
    current = read_text("docs/c2-recommendation/01-business-rules.md")
    for required in (
        "APPROVED_C2A_INTERNAL_POPULARITY_ONLY",
        "BAYESIAN_POPULARITY_ONLY",
        "온보딩 LIKE/DISLIKE",
        "ranking score에는 기여하지 않는다",
    ):
        if required not in current:
            raise RuntimeError(f"current C2 protection is missing: {required}")


def validate_019b_completion_manifest() -> dict[str, Any]:
    manifest = read_json("docs/recommendation/evidence/manifests/rec-ev-019b.json")
    contract = read_json("docs/recommendation/contracts/rec-ev-019b-artifacts.json")
    if manifest.get("status") != "PASS_FULL_GATES" or manifest.get("preflight") is not False:
        raise RuntimeError("REC-EV-019B full feature manifest is not complete")
    if manifest.get("contract_sha256") != manifest.get("source_checksums", {}).get("contract_sha256"):
        raise RuntimeError("REC-EV-019B manifest contract checksums disagree")
    from recommendation_protocol_v4 import sha256_file

    contract_path = ROOT / "docs/recommendation/contracts/rec-ev-019b-artifacts.json"
    if manifest.get("contract_sha256") != sha256_file(contract_path):
        raise RuntimeError("REC-EV-019B manifest points to a stale contract")
    validation = manifest.get("validation", {})
    if validation.get("selected_movies") != 69603:
        raise RuntimeError("REC-EV-019B full feature-superset count changed")
    if validation.get("artifact_scope") != "FULL_BASE_USER_CATALOG_FEATURE_SUPERSET_NOT_CANDIDATE_CORE":
        raise RuntimeError("REC-EV-019B feature-superset scope is missing")
    if validation.get("time_safe_candidate_authority") is not False:
        raise RuntimeError("REC-EV-019B must not claim candidate authority")
    if validation.get("identity_rate", 0) < contract["gates"]["verified_or_recovered_identity_rate_of_linked_min"]:
        raise RuntimeError("REC-EV-019B identity Gate is not recorded as passed")
    if validation.get("structured_rate", 0) < contract["gates"]["structured_feature_eligible_rate_of_identity_eligible_min"]:
        raise RuntimeError("REC-EV-019B structured Gate is not recorded as passed")
    if validation.get("text_rate", 0) < contract["gates"]["text_feature_eligible_rate_of_identity_eligible_min"]:
        raise RuntimeError("REC-EV-019B text Gate is not recorded as passed")
    if validation.get("locked_test_opened") is not False or validation.get("product_policy_changed") is not False:
        raise RuntimeError("REC-EV-019B completion crossed its safety boundary")
    expected_artifacts = {
        item["path"]
        for item in contract["artifacts"]
        if item["path"] != "docs/recommendation/evidence/manifests/rec-ev-019b.json"
    }
    if {item.get("path") for item in manifest.get("artifacts", [])} != expected_artifacts:
        raise RuntimeError("REC-EV-019B tracked artifact inventory changed")
    return manifest


def validate_019a_completion_manifest() -> dict[str, Any]:
    manifest = read_json("docs/recommendation/evidence/manifests/rec-ev-019a.json")
    contract = read_json("docs/recommendation/contracts/rec-ev-019a-artifacts.json")
    if manifest.get("status") != "PASS_COHORT_GATES":
        raise RuntimeError("REC-EV-019A cohort manifest is not complete")
    if manifest.get("contract_sha256") != manifest.get("source_checksums", {}).get("contract_sha256"):
        raise RuntimeError("REC-EV-019A manifest contract checksums disagree")
    from recommendation_protocol_v4 import sha256_file

    contract_path = ROOT / "docs/recommendation/contracts/rec-ev-019a-artifacts.json"
    if manifest.get("contract_sha256") != sha256_file(contract_path):
        raise RuntimeError("REC-EV-019A manifest points to a stale contract")
    validation = manifest.get("validation", {})
    minimum_users = int(contract["gates"]["locked_test_k10_strict_eligible_min"])
    if int(validation.get("locked_test_k10_provisional_eligible", 0)) < minimum_users:
        raise RuntimeError("REC-EV-019A provisional K10 Gate failed")
    if int(validation.get("locked_test_k10_final_identity_eligible", 0)) < minimum_users:
        raise RuntimeError("REC-EV-019A final identity K10 Gate failed")
    if int(validation.get("final_identity_candidate_movies", 0)) != 41625:
        raise RuntimeError("REC-EV-019A final identity candidate count changed")
    if validation.get("prefix_nested_k5_in_k10") is not True:
        raise RuntimeError("REC-EV-019A K5/K10 nesting is not recorded")
    if validation.get("raw_user_ids_stored") is not False:
        raise RuntimeError("REC-EV-019A raw user ID boundary changed")
    if validation.get("locked_test_model_predictions_opened") is not False:
        raise RuntimeError("REC-EV-019A opened Locked Test model predictions")
    if validation.get("product_policy_changed") is not False:
        raise RuntimeError("REC-EV-019A changed the product policy")
    expected_artifacts = {
        item["path"]
        for item in contract["artifacts"]
        if item["path"] != "docs/recommendation/evidence/manifests/rec-ev-019a.json"
    }
    if {item.get("path") for item in manifest.get("artifacts", [])} != expected_artifacts:
        raise RuntimeError("REC-EV-019A tracked artifact inventory changed")
    return manifest


def validate_019c_contract_readiness() -> dict[str, Any]:
    from validate_rec_ev_019c_contract import validate_contract

    contract = read_json(
        "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"
    )
    return validate_contract(contract, root=ROOT)


def validate_019c_synthetic_preflight() -> dict[str, Any]:
    from verify_rec_ev_019c_validation import verify_manifest

    return verify_manifest(
        ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-synthetic-preflight.json",
        root=ROOT,
    )


def validate_019c_dependency_smoke() -> dict[str, Any]:
    from verify_rec_ev_019c_dependency_smoke import verify_manifest

    return verify_manifest(
        ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-lightfm-linux-smoke.json",
        root=ROOT,
    )


def validate_019c_resource_dry_run() -> dict[str, Any]:
    from verify_rec_ev_019c_resource_dry_run import verify_manifest

    return verify_manifest(
        ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-resource-dry-run.json",
        root=ROOT,
    )


def validate() -> dict[str, Any]:
    documents = (
        "docs/recommendation/00-input-signal-contract-vnext.md",
        "docs/recommendation/01-offline-evaluation-protocol-vnext.md",
        "docs/recommendation/serving-contract.md",
        "docs/recommendation/vnext-implementation-readiness.md",
    )
    for document in documents:
        require_approved_document(document)

    protocol = read_json("docs/recommendation/protocols/rec-eval-vnext.json")
    validate_protocol(protocol)
    validate_artifact_contracts()
    backlog = read_yaml("docs/tasks/recommendation-evidence-backlog.yaml")
    validate_backlog(backlog)
    validate_current_product_boundary()
    preflight = verify_preflight(
        ROOT / "docs/recommendation/evidence/manifests/rec-ev-019p.json"
    )
    if preflight["implementation"] != "GO":
        raise RuntimeError("REC-EV-019 preflight did not produce implementation GO")
    cohort_build = validate_019a_completion_manifest()
    feature_build = validate_019b_completion_manifest()
    contract_019c = validate_019c_contract_readiness()
    synthetic_019c = validate_019c_synthetic_preflight()
    dependency_019c = validate_019c_dependency_smoke()
    resource_019c = validate_019c_resource_dry_run()

    return {
        "status": "PASS",
        "decision": "NO_GO_FOR_REAL_VALIDATION_UNTIL_RESOURCE_CONTRACT_AMENDED",
        "scope": "REC-EV-019A_019B_DONE_019C_SYNTHETIC_DEPENDENCY_AND_METADATA_AUDIT_PASS_RESOURCE_BLOCKERS_OPEN",
        "next_ready_tasks": ["TASK-REC-EV-019C"],
        "next_phase": "AMEND_B4_PAIR_SAMPLING_SEED_REPEATS_AND_SCORE_BUDGET",
        "rec_ev_019a_status": cohort_build["status"],
        "rec_ev_019a_final_identity_k10_users": cohort_build["validation"]["locked_test_k10_final_identity_eligible"],
        "rec_ev_019b_status": feature_build["status"],
        "rec_ev_019b_selected_movies": feature_build["validation"]["selected_movies"],
        "rec_ev_019c_contract_status": contract_019c["status"],
        "rec_ev_019c_synthetic_preflight_status": synthetic_019c["status"],
        "rec_ev_019c_dependency_smoke_status": dependency_019c["status"],
        "rec_ev_019c_resource_dry_run_status": resource_019c["status"],
        "rec_ev_019c_resource_blockers": resource_019c["blockers"],
        "rec_ev_019c_full_catalog_user_item_scores": resource_019c["full_catalog_user_item_scores"],
        "rec_ev_019c_b8_base_update_upper_bound": resource_019c["b8_base_update_upper_bound"],
        "real_validation_authorized": False,
        "eligible_k10_test_users": preflight["eligible_test_users"],
        "current_product_policy": "APPROVED_C2A_INTERNAL_POPULARITY_ONLY",
        "product_champion": None,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
