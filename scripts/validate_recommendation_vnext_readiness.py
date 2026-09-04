from __future__ import annotations

import json
import hashlib
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
        "TASK-REC-EV-019C": "DONE",
        "TASK-REC-EV-019D": "DONE",
        "TASK-REC-EV-019E": "DONE",
        "TASK-REC-EV-019F": "DONE",
        "TASK-REC-EV-019": "PENDING",
        "TASK-REC-EV-020": "PENDING",
        "TASK-REC-EV-021": "PENDING",
        "TASK-REC-EV-021V": "PREFLIGHT_DONE_EXTERNAL_COLLECTION_BLOCKED",
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
    for task_id in ("TASK-REC-EV-019P", "TASK-REC-EV-019A", "TASK-REC-EV-019B", "TASK-REC-EV-019C", "TASK-REC-EV-019D", "TASK-REC-EV-019E", "TASK-REC-EV-019F", "TASK-REC-EV-019", "TASK-REC-EV-021V"):
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
    if task_019c.get("current_authorization") != "VALIDATION_COMPLETE_LOCKED_TEST_FORBIDDEN":
        raise RuntimeError("019C completion boundary is missing")
    if task_019c.get("validation_result") != "PASS_VALIDATION_SELECTION_LOCKED":
        raise RuntimeError("019C Validation result is missing from backlog")
    if task_019c.get("analysis_result") != "PASS_VALIDATION_ANALYSIS_ONLY":
        raise RuntimeError("019C analysis result is missing from backlog")
    if task_019c.get("locked_test_authorization") != "FORBIDDEN":
        raise RuntimeError("019C Locked Test boundary is missing")
    expected_019c_outputs = {item["path"] for item in contract_019c["future_artifacts"]}
    if set(task_019c.get("outputs", [])) != expected_019c_outputs:
        raise RuntimeError("019C outputs differ from its artifact contract")
    expected_analysis_outputs = {
        "outputs/recommendation-evidence/rec-ev-019c/analysis-summary.json",
        "docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json",
        "docs/recommendation/evidence/REC-EV-019C-validation-analysis.md",
        "docs/presentation/FEELM-REC-EV-019C-results.pptx",
    }
    if set(task_019c.get("analysis_outputs", [])) != expected_analysis_outputs:
        raise RuntimeError("019C analysis outputs are incomplete")
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
    expected_analysis_commands = {
        "analyze": "py -3 scripts/analyze_rec_ev_019c_validation.py",
        "unit": "py -3 -m unittest scripts/tests/test_analyze_rec_ev_019c_validation.py scripts/tests/test_verify_rec_ev_019c_analysis.py",
        "verify": "py -3 scripts/verify_rec_ev_019c_analysis.py --manifest docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json",
    }
    if task_019c.get("analysis_commands") != expected_analysis_commands:
        raise RuntimeError("019C analysis commands are incomplete")
    verify_019c = "\n".join(tasks["TASK-REC-EV-019C"]["verify"])
    for required_text in ("final identity allowlist", "at least 5000", "B0 fallback"):
        if required_text not in verify_019c:
            raise RuntimeError(f"019C final candidate gate is incomplete: {required_text}")

    task_019d = tasks["TASK-REC-EV-019D"]
    if task_019d.get("artifact_contract") != "docs/recommendation/contracts/rec-ev-019d-prefix-ablation-artifacts.json":
        raise RuntimeError("019D artifact contract is missing from backlog")
    if task_019d.get("current_authorization") != "VALIDATION_COMPLETE_LOCKED_TEST_FORBIDDEN":
        raise RuntimeError("019D completion boundary is missing")
    if task_019d.get("validation_result") != "FAIL_SAFETY_MARGIN_EXCEEDED":
        raise RuntimeError("019D safety failure is not preserved")
    if task_019d.get("locked_test_authorization") != "FORBIDDEN":
        raise RuntimeError("019D Locked Test boundary is missing")
    expected_019d_commands = {
        "contract": "py -3 scripts/validate_rec_ev_019d_contract.py && py -3 -m unittest scripts/tests/test_rec_ev_019d_contract.py scripts/tests/test_run_rec_ev_019d_prefix_ablation.py scripts/tests/test_verify_rec_ev_019d_prefix_ablation.py",
        "lock": "py -3 scripts/run_rec_ev_019d_prefix_ablation.py --phase lock --role validation-019d",
        "run": "py -3 scripts/run_rec_ev_019d_prefix_ablation.py --phase run --role validation-019d --resume",
        "verify": "py -3 scripts/verify_rec_ev_019d_prefix_ablation.py --manifest docs/recommendation/evidence/manifests/rec-ev-019d-validation.json",
        "full_rescore_verify": "py -3 scripts/verify_rec_ev_019d_prefix_ablation.py --manifest docs/recommendation/evidence/manifests/rec-ev-019d-validation.json --full-rescore-users all",
    }
    if task_019d.get("commands") != expected_019d_commands:
        raise RuntimeError("019D commands differ from the completed experiment")
    verify_019d = "\n".join(task_019d.get("verify", []))
    for required_text in ("1479", "426", "1053", "661/277/115", "5916", "FAIL_SAFETY_MARGIN_EXCEEDED"):
        if required_text not in verify_019d:
            raise RuntimeError(f"019D verification boundary is incomplete: {required_text}")
    task_019e = tasks["TASK-REC-EV-019E"]
    if task_019e.get("artifact_contract") != "docs/recommendation/contracts/rec-ev-019e-no-retune-incremental-applicability-gate.json":
        raise RuntimeError("019E artifact contract is missing from backlog")
    if task_019e.get("depends_on") != ["TASK-REC-EV-019D"]:
        raise RuntimeError("019E does not depend on the completed 019D result")
    if task_019e.get("current_authorization") != "POST_HOC_VALIDATION_COMPLETE_FRESH_CONFIRMATION_REQUIRED":
        raise RuntimeError("019E post-hoc authority boundary is missing")
    if task_019e.get("validation_result") != "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION":
        raise RuntimeError("019E limited result status is missing")
    if task_019e.get("locked_test_authorization") != "FORBIDDEN":
        raise RuntimeError("019E Locked Test boundary is missing")
    expected_019e_commands = {
        "contract": "py -3 scripts/validate_rec_ev_019e_contract.py && py -3 -m unittest scripts/tests/test_rec_ev_019e_contract.py scripts/tests/test_run_rec_ev_019e_no_retune_incremental_applicability.py scripts/tests/test_verify_rec_ev_019e_no_retune_incremental_applicability.py",
        "lock": "py -3 scripts/run_rec_ev_019e_no_retune_incremental_applicability.py --phase lock --role validation-019e-post-hoc",
        "run": "py -3 scripts/run_rec_ev_019e_no_retune_incremental_applicability.py --phase run --role validation-019e-post-hoc --resume",
        "verify": "py -3 scripts/verify_rec_ev_019e_no_retune_incremental_applicability.py --manifest docs/recommendation/evidence/manifests/rec-ev-019e-validation.json",
    }
    if task_019e.get("commands") != expected_019e_commands:
        raise RuntimeError("019E commands differ from the completed experiment")
    verify_019e = "\n".join(task_019e.get("verify", []))
    for required_text in ("1053", "661/277/115", "10000", "70/957/26", "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION"):
        if required_text not in verify_019e:
            raise RuntimeError(f"019E verification boundary is incomplete: {required_text}")
    task_019f = tasks["TASK-REC-EV-019F"]
    if task_019f.get("artifact_contract") != "docs/recommendation/contracts/rec-ev-019f-independent-temporal-routing.json":
        raise RuntimeError("019F artifact contract is missing from backlog")
    if task_019f.get("depends_on") != ["TASK-REC-EV-019E"]:
        raise RuntimeError("019F does not depend on frozen 019E routing")
    if task_019f.get("current_authorization") != "VALIDATION_COMPLETE_TARGET_DOMAIN_CONFIRMATION_STILL_REQUIRED":
        raise RuntimeError("019F authority boundary is missing")
    if task_019f.get("validation_result") != "INCONCLUSIVE" or task_019f.get("locked_test_authorization") != "FORBIDDEN":
        raise RuntimeError("019F completed result boundary differs")
    if task_019f.get("commands") != {
        "contract": "npm run recommendation:019f:contract:check",
        "lock": "npm run recommendation:019f:lock",
        "run": "npm run recommendation:019f:run",
        "full_rescore_verify": "npm run recommendation:019f:check",
    }:
        raise RuntimeError("019F commands are incomplete")
    verify_019f = "\n".join(task_019f.get("verify", []))
    for required_text in ("1021", "802", "629", "173", "31", "1604", "INCONCLUSIVE", "source-row/window", "user_independent=false"):
        if required_text not in verify_019f:
            raise RuntimeError(f"019F completion boundary is incomplete: {required_text}")
    if tasks["TASK-REC-EV-019"].get("depends_on") != ["TASK-REC-EV-019F"]:
        raise RuntimeError("binary product decision does not depend on 019F temporal confirmation")
    task_021v = tasks["TASK-REC-EV-021V"]
    if task_021v.get("artifact_contract") != "docs/recommendation/contracts/rec-ev-021v-kr-recent-niche-pooled-judgment.json":
        raise RuntimeError("021V artifact contract is missing from backlog")
    if task_021v.get("depends_on") != ["TASK-REC-EV-019B", "TASK-REC-EV-019F"]:
        raise RuntimeError("021V dependencies do not preserve feature and 019F evidence provenance")
    if task_021v.get("current_authorization") != "SYNTHETIC_PREFLIGHT_ONLY":
        raise RuntimeError("021V authorization exceeds recruitment preflight")
    if task_021v.get("target_evidence_status") != "NO_ACTUAL_TARGET_DOMAIN_EVIDENCE":
        raise RuntimeError("021V target evidence boundary differs")
    if task_021v.get("locked_test_authorization") != "FORBIDDEN":
        raise RuntimeError("021V Locked Test boundary differs")
    if task_021v.get("commands") != {
        "preflight": "npm run recommendation:021v:preflight:run",
        "verify": "npm run recommendation:021v:preflight:check",
    }:
        raise RuntimeError("021V commands are incomplete")
    verify_021v = "\n".join(task_021v.get("verify", []))
    for required_text in ("100", "K10", "48", "12", "NO_ACTUAL_TARGET_DOMAIN_EVIDENCE", "INSUFFICIENT_TARGET_DOMAIN_EVIDENCE", "locked_test_used=false"):
        if required_text not in verify_021v:
            raise RuntimeError(f"021V preflight boundary is incomplete: {required_text}")


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


def validate_019c_completion_manifests() -> dict[str, Any]:
    validation_path = "docs/recommendation/evidence/manifests/rec-ev-019c-validation.json"
    validation = read_json(validation_path)
    if validation.get("evidence_id") != "REC-EV-019C":
        raise RuntimeError("REC-EV-019C Validation evidence id differs")
    if validation.get("status") != "PASS_VALIDATION_SELECTION_LOCKED":
        raise RuntimeError("REC-EV-019C Validation is not selection-locked")
    validation_boundary = validation.get("validation", {})
    if validation_boundary.get("locked_test_opened") is not False:
        raise RuntimeError("REC-EV-019C opened Locked Test")
    if validation_boundary.get("selection_lock_created") is not True:
        raise RuntimeError("REC-EV-019C selection lock is missing")
    adoption = validation.get("adoption", {})
    if adoption.get("champion") is not None:
        raise RuntimeError("REC-EV-019C invented a product champion")
    if adoption.get("product_policy_changed") is not False:
        raise RuntimeError("REC-EV-019C changed product policy")
    if adoption.get("current_product_policy") != "APPROVED_C2A_INTERNAL_POPULARITY_ONLY":
        raise RuntimeError("REC-EV-019C product policy boundary differs")

    analysis = read_json("docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json")
    if analysis.get("status") != "PASS_VALIDATION_ANALYSIS_ONLY":
        raise RuntimeError("REC-EV-019C analysis status differs")
    if analysis.get("validation") != {
        "champion": None,
        "champion_selected": False,
        "locked_test_opened": False,
        "locked_test_used": False,
        "post_hoc_results_are_confirmatory": False,
        "product_policy_changed": False,
        "product_policy_updated": False,
        "tuning_panel_excluded_paired_is_confirmatory_auxiliary": True,
    }:
        raise RuntimeError("REC-EV-019C analysis boundary differs")
    source = analysis.get("source_validation_manifest", {})
    if source.get("path") != validation_path:
        raise RuntimeError("REC-EV-019C analysis source manifest differs")
    digest = hashlib.sha256((ROOT / validation_path).read_bytes()).hexdigest()
    if source.get("sha256") != digest:
        raise RuntimeError("REC-EV-019C analysis source checksum differs")
    return {
        "validation_status": validation["status"],
        "analysis_status": analysis["status"],
        "locked_test_opened": False,
        "product_champion": None,
        "product_policy_changed": False,
    }


def validate_019d_completion_manifest() -> dict[str, Any]:
    contract_path = ROOT / "docs/recommendation/contracts/rec-ev-019d-prefix-ablation-artifacts.json"
    manifest_path = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019d-validation.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(contract_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if manifest.get("evidence_id") != "REC-EV-019D" or manifest.get("contract_sha256") != digest:
        raise RuntimeError("REC-EV-019D manifest contract identity differs")
    if manifest.get("status") != "FAIL":
        raise RuntimeError("REC-EV-019D safety failure status is missing")
    result = manifest.get("result", {})
    if result.get("reason") != "SAFETY_MARGIN_EXCEEDED":
        raise RuntimeError("REC-EV-019D safety failure reason is missing")
    cohort = result.get("cohort", {})
    if cohort != {"k10_users": 1479, "tuning_panel_excluded": 426, "confirmatory_users": 1053}:
        raise RuntimeError("REC-EV-019D cohort differs")
    primary = result.get("primary_estimand", {})
    bootstrap = primary.get("bootstrap", {})
    if primary.get("paired_users") != 1053 or bootstrap.get("iterations") != 10000 or bootstrap.get("seed") != 20260924:
        raise RuntimeError("REC-EV-019D paired bootstrap contract differs")
    if bootstrap.get("harm_one_sided_95_upper", 0) <= 0.005:
        raise RuntimeError("REC-EV-019D safety failure was weakened")
    strata = result.get("strata", {})
    if strata.get("mutually_exclusive_counts") != {
        "BOTH_FALLBACK": 115,
        "BOTH_LIGHTFM": 661,
        "K10_NEWLY_APPLICABLE": 277,
    }:
        raise RuntimeError("REC-EV-019D strata differ")
    if strata.get("raw_both_but_candidate_anchor_loss") != {"K10": 34, "K5": 61}:
        raise RuntimeError("REC-EV-019D candidate-anchor loss differs")
    for payload in (manifest, result):
        if payload.get("locked_test_used") is not False:
            raise RuntimeError("REC-EV-019D opened Locked Test")
        if payload.get("champion") is not None:
            raise RuntimeError("REC-EV-019D invented a champion")
        if payload.get("product_policy_updated") is not False:
            raise RuntimeError("REC-EV-019D changed product policy")
    return {
        "status": manifest["status"],
        "reason": result["reason"],
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


def validate_019e_completion_manifest() -> dict[str, Any]:
    contract_path = ROOT / "docs/recommendation/contracts/rec-ev-019e-no-retune-incremental-applicability-gate.json"
    manifest_path = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019e-validation.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(contract_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if manifest.get("evidence_id") != "REC-EV-019E" or manifest.get("contract_sha256") != digest:
        raise RuntimeError("REC-EV-019E manifest contract identity differs")
    expected_status = "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION"
    if manifest.get("status") != expected_status:
        raise RuntimeError("REC-EV-019E limited post-hoc status differs")
    result = manifest.get("result", {})
    if result.get("status") != expected_status or result.get("fresh_target_independent_validation_required") is not True:
        raise RuntimeError("REC-EV-019E fresh confirmation boundary differs")
    evidence = result.get("evidence_classification", {})
    if evidence.get("post_hoc") is not True or evidence.get("independent_confirmatory_evidence") is not False:
        raise RuntimeError("REC-EV-019E post-hoc disclosure differs")
    if evidence.get("reuses_same_confirmatory_users") != 1053:
        raise RuntimeError("REC-EV-019E reused cohort disclosure differs")
    if result.get("cohort") != {"k10_users": 1479, "tuning_panel_excluded": 426, "confirmatory_users": 1053}:
        raise RuntimeError("REC-EV-019E cohort differs")
    if result.get("routing_counts_confirmatory") != {
        "BOTH_FALLBACK": 115,
        "BOTH_LIGHTFM": 661,
        "K10_NEWLY_APPLICABLE": 277,
    }:
        raise RuntimeError("REC-EV-019E routing counts differ")
    paired = result.get("paired_confirmatory", {})
    bootstrap = paired.get("bootstrap", {})
    if bootstrap.get("iterations") != 10000 or bootstrap.get("seed") != 20260924:
        raise RuntimeError("REC-EV-019E bootstrap contract differs")
    if bootstrap.get("harm_one_sided_95_upper", 1.0) > 0.005:
        raise RuntimeError("REC-EV-019E safety Gate differs")
    if bootstrap.get("ndcg_mean", 0.0) < 0.005 or bootstrap.get("ndcg_two_sided_95", [0.0])[0] <= 0.0:
        raise RuntimeError("REC-EV-019E efficacy Gate differs")
    if paired.get("benefit_harm_user_counts") != {"benefit": 70, "neutral": 957, "harm": 26}:
        raise RuntimeError("REC-EV-019E benefit/harm counts differ")
    lock_path = ROOT / contract["leakage_lock"]["path"]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("future_metrics_read") is not False:
        raise RuntimeError("REC-EV-019E lock timing boundary differs")
    if lock.get("rec_ev_019d_result_and_harm_decomposition_already_observed") is not True:
        raise RuntimeError("REC-EV-019E post-hoc source disclosure missing from lock")
    git = lock.get("git", {})
    if len(str(git.get("revision", ""))) != 40 or not isinstance(git.get("dirty"), bool) or len(str(git.get("status_sha256", ""))) != 64:
        raise RuntimeError("REC-EV-019E git attestation differs")
    if set(lock.get("source_code", {})) != {"runner", "verifier", "contract_validator"}:
        raise RuntimeError("REC-EV-019E source-code attestation differs")
    for payload in (manifest, result, lock):
        if payload.get("locked_test_used") is not False:
            raise RuntimeError("REC-EV-019E opened Locked Test")
        if payload.get("champion") is not None:
            raise RuntimeError("REC-EV-019E invented a champion")
        if payload.get("product_policy_updated") is not False:
            raise RuntimeError("REC-EV-019E changed product policy")
    return {
        "status": expected_status,
        "post_hoc": True,
        "fresh_target_independent_validation_required": True,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


def validate_019f_completion_manifest() -> dict[str, Any]:
    contract_path = ROOT / "docs/recommendation/contracts/rec-ev-019f-independent-temporal-routing.json"
    manifest_path = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019f-validation.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(contract_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if manifest.get("evidence_id") != "REC-EV-019F" or manifest.get("contract_sha256") != digest:
        raise RuntimeError("REC-EV-019F manifest contract identity differs")
    if manifest.get("status") != "INCONCLUSIVE":
        raise RuntimeError("REC-EV-019F inconclusive status differs")
    result = manifest.get("result", {})
    if result.get("status") != "INCONCLUSIVE" or result.get("reason") != "TEMPORAL_WINDOW_SUCCESS_NOT_ESTABLISHED":
        raise RuntimeError("REC-EV-019F decision differs")
    if result.get("independence_unit") != "SOURCE_ROW_AND_TEMPORAL_WINDOW" or result.get("user_independent") is not False:
        raise RuntimeError("REC-EV-019F independence boundary differs")
    if result.get("cohort") != {
        "completely_new_to_019a_validation_users": 31,
        "existing_019a_k10_users": 629,
        "outside_existing_019a_k10_users": 173,
        "strict_users": 802,
        "structural_users": 1021,
    }:
        raise RuntimeError("REC-EV-019F cohort/overlap differs")
    if result.get("routing_counts") != {
        "BOTH_FALLBACK": 70,
        "BOTH_LIGHTFM": 568,
        "K10_NEWLY_APPLICABLE": 164,
    }:
        raise RuntimeError("REC-EV-019F routing strata differ")
    paired = result.get("paired_strict", {})
    bootstrap = paired.get("bootstrap", {})
    if paired.get("users") != 802 or bootstrap.get("iterations") != 10000 or bootstrap.get("seed") != 20260924:
        raise RuntimeError("REC-EV-019F paired bootstrap contract differs")
    if bootstrap.get("harm_one_sided_95_upper", 1.0) > 0.005:
        raise RuntimeError("REC-EV-019F Harm Gate differs")
    if bootstrap.get("ndcg_two_sided_95", [0.0])[0] <= 0.0 or bootstrap.get("ndcg_mean", 1.0) >= 0.005:
        raise RuntimeError("REC-EV-019F inconclusive efficacy boundary differs")
    if paired.get("benefit_harm_user_counts") != {"benefit": 27, "harm": 13, "neutral": 762}:
        raise RuntimeError("REC-EV-019F benefit/neutral/harm differs")
    non_gate = result.get("non_gate_degradation", {})
    if non_gate.get("candidate_recall_at_500_degraded") is not True or non_gate.get("positive_mean_rank_percentile_degraded") is not True:
        raise RuntimeError("REC-EV-019F non-Gate degradation disclosure differs")
    if result.get("maximum_success_status") != "PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION":
        raise RuntimeError("REC-EV-019F maximum status boundary differs")
    execution = manifest.get("execution", {})
    if execution.get("git_dirty_at_lock") is not False or len(str(execution.get("git_revision", ""))) != 40:
        raise RuntimeError("REC-EV-019F clean git lock attestation differs")
    if execution.get("ranking_metrics_read_at_lock") is not False or execution.get("eligibility_counts_observed_before_lock") is not True:
        raise RuntimeError("REC-EV-019F lock timing/disclosure differs")
    if set(manifest.get("source_code", {})) != {"runner", "verifier", "contract_validator", "binary_label_helper", "user_key_helper"}:
        raise RuntimeError("REC-EV-019F source-code attestation differs")
    evidence = result.get("evidence_classification", {})
    if evidence.get("source_row_independent_from_rec_ev_019a") is not True:
        raise RuntimeError("REC-EV-019F source-row overlap differs")
    artifact_paths = {artifact.get("path") for artifact in manifest.get("artifacts", [])}
    for required_path in (
        contract["leakage_lock"]["path"],
        f'{contract["output_root"]}/{contract["outputs"]["strata"]}',
        f'{contract["output_root"]}/{contract["outputs"]["result"]}',
    ):
        if required_path not in artifact_paths:
            raise RuntimeError(f"REC-EV-019F artifact attestation is missing: {required_path}")
    for payload in (manifest, result):
        if payload.get("locked_test_used") is not False:
            raise RuntimeError("REC-EV-019F opened Locked Test")
        if payload.get("champion") is not None:
            raise RuntimeError("REC-EV-019F invented a champion")
        if payload.get("product_policy_updated") is not False:
            raise RuntimeError("REC-EV-019F changed product policy")
    return {
        "status": "INCONCLUSIVE",
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "strict_users": 802,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


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
    completion_019c = validate_019c_completion_manifests()
    from validate_rec_ev_019d_contract import validate_contract as validate_019d_contract
    contract_019d = validate_019d_contract(read_json(
        "docs/recommendation/contracts/rec-ev-019d-prefix-ablation-artifacts.json"
    ))
    completion_019d = validate_019d_completion_manifest()
    from validate_rec_ev_019e_contract import validate_contract as validate_019e_contract
    contract_019e = validate_019e_contract(read_json(
        "docs/recommendation/contracts/rec-ev-019e-no-retune-incremental-applicability-gate.json"
    ))
    completion_019e = validate_019e_completion_manifest()
    from validate_rec_ev_019f_contract import validate as validate_019f_contract
    contract_019f = validate_019f_contract(read_json(
        "docs/recommendation/contracts/rec-ev-019f-independent-temporal-routing.json"
    ), root=ROOT, check_files=True)
    completion_019f = validate_019f_completion_manifest()
    from verify_rec_ev_021v_preflight import verify as verify_021v_preflight
    completion_021v = verify_021v_preflight()

    return {
        "status": "PASS",
        "decision": "REC_EV_019F_INCONCLUSIVE_SOURCE_ROW_WINDOW_CONFIRMATION_TEST_LOCKED",
        "scope": "REC-EV-019F_INCONCLUSIVE_SOURCE_ROW_WINDOW_NOT_USER_INDEPENDENT_LOCKED_TEST_AND_PRODUCT_POLICY_UNCHANGED",
        "next_ready_tasks": [],
        "next_phase": "APPROVED_REC_EV_021V_EXTERNAL_INPUTS_AND_RECRUITMENT_OR_NEW_PREREGISTERED_HYPOTHESIS_WITHOUT_OPENING_LOCKED_TEST",
        "rec_ev_019a_status": cohort_build["status"],
        "rec_ev_019a_final_identity_k10_users": cohort_build["validation"]["locked_test_k10_final_identity_eligible"],
        "rec_ev_019b_status": feature_build["status"],
        "rec_ev_019b_selected_movies": feature_build["validation"]["selected_movies"],
        "rec_ev_019c_contract_status": contract_019c["status"],
        "rec_ev_019c_synthetic_preflight_status": synthetic_019c["status"],
        "rec_ev_019c_dependency_smoke_status": dependency_019c["status"],
        "rec_ev_019c_resource_dry_run_status": resource_019c["status"],
        "rec_ev_019c_resource_blockers": resource_019c["blockers"],
        "rec_ev_019c_validation_status": completion_019c["validation_status"],
        "rec_ev_019c_analysis_status": completion_019c["analysis_status"],
        "rec_ev_019c_locked_test_opened": completion_019c["locked_test_opened"],
        "rec_ev_019d_validation_status": completion_019d["status"],
        "rec_ev_019d_validation_reason": completion_019d["reason"],
        "rec_ev_019d_contract_status": contract_019d["status"],
        "rec_ev_019e_validation_status": completion_019e["status"],
        "rec_ev_019e_contract_status": contract_019e["status"],
        "rec_ev_019e_fresh_confirmation_required": completion_019e["fresh_target_independent_validation_required"],
        "rec_ev_019f_contract_status": contract_019f["status"],
        "rec_ev_019f_validation_status": completion_019f["status"],
        "rec_ev_019f_independence_unit": completion_019f["independence_unit"],
        "rec_ev_019f_user_independent": completion_019f["user_independent"],
        "rec_ev_019f_strict_users": completion_019f["strict_users"],
        "rec_ev_021v_preflight_status": completion_021v["status"],
        "rec_ev_021v_infrastructure_status": completion_021v["infrastructure_status"],
        "rec_ev_021v_target_evidence_status": completion_021v["target_evidence_status"],
        "rec_ev_019c_full_catalog_user_item_scores": resource_019c["full_catalog_user_item_scores"],
        "rec_ev_019c_b8_base_update_upper_bound": resource_019c["b8_base_update_upper_bound"],
        "rec_ev_019c_b4_pair_update_upper_bound": resource_019c["b4_pair_update_upper_bound"],
        "real_validation_authorized": False,
        "eligible_k10_test_users": preflight["eligible_test_users"],
        "current_product_policy": "APPROVED_C2A_INTERNAL_POPULARITY_ONLY",
        "product_champion": None,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
