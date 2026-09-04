#!/usr/bin/env python3
"""Validate the REC-EV-019E post-hoc no-retune contract without opening data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019e-no-retune-incremental-applicability-gate.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    require(contract.get("contract_id") == "REC-EV-019E-NO-RETUNE-INCREMENTAL-APPLICABILITY-GATE", "contract identity drift")
    require(contract.get("status") == "APPROVED_POST_HOC_VALIDATION_ONLY_BEFORE_RESULT", "contract status drift")
    require(contract.get("task_id") == "TASK-REC-EV-019E", "task identity drift")
    require(contract.get("invariants") == {
        "execution_role": "VALIDATION_019E_POST_HOC",
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }, "fail-closed invariant drift")
    classification = contract["evidence_classification"]
    require(classification["post_hoc"] is True, "post-hoc disclosure drift")
    require(classification["reuses_same_confirmatory_users"] == 1053, "reused cohort disclosure drift")
    require(classification["independent_confirmatory_evidence"] is False, "confirmatory authority drift")
    require(classification["maximum_success_status"] == "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION", "success status drift")
    require(classification["fresh_target_independent_validation_required"] is True, "fresh confirmation boundary drift")
    authorization = contract["current_authorization"]
    for key in ("locked_test_access", "candidate_or_threshold_search", "retuning", "champion_selection", "product_policy_change"):
        require(authorization[key] is False, f"authorization drift: {key}")
    require(contract["role_firewall"]["validation_role_literal"] == "validation-019e-post-hoc", "role literal drift")
    require(contract["role_firewall"]["test_mode_or_test_flag_forbidden"] is True, "Test CLI boundary drift")
    allowed = contract["allowed_input_artifacts"]
    required_inputs = {
        "validation_prefixes", "validation_windows", "candidate_core", "validation_selection",
        "lightfm_config", "lightfm_interactions", "lightfm_item_features", "lightfm_result",
        "rec_ev_019d_predictions", "rec_ev_019d_user_arm_metrics", "rec_ev_019d_cohort",
        "rec_ev_019d_arm_definitions", "rec_ev_019d_result", "rec_ev_019d_protocol_lock",
        "rec_ev_019d_source_manifest", "rec_ev_019d_validation_manifest",
    }
    require(set(allowed) == required_inputs, "input allowlist drift")
    for name, artifact in allowed.items():
        require(isinstance(artifact.get("bytes"), int) and artifact["bytes"] > 0, f"input size missing: {name}")
        require(len(str(artifact.get("sha256", ""))) == 64, f"input SHA-256 missing: {name}")
    require(contract["population"] == {
        "source": "REC-EV-019D K10 cohort and confirmatory exclusion",
        "k10_cohort_users": 1479,
        "tuning_union_excluded_users": 426,
        "confirmatory_users": 1053,
        "evaluation_population": "CONFIRMATORY_ONLY",
    }, "population drift")
    require(contract["comparator"]["source_arm"] == "K5", "comparator arm drift")
    require(contract["comparator"]["source_estimand"] == "COMMON_K10_SEEN_MASK", "comparator mask drift")
    candidate = contract["candidate"]
    require(candidate["parameters"] == [] and candidate["thresholds"] == [], "candidate parameter drift")
    require(candidate["candidate_search_allowed"] is False, "candidate search drift")
    routes = candidate["routing_priority"]
    require([(row["order"], row["stratum"], row["source_arm"], row["model"]) for row in routes] == [
        (1, "BOTH_LIGHTFM", "K5", "K5_FOLD_IN"),
        (2, "K10_NEWLY_APPLICABLE", "K10", "K10_FOLD_IN"),
        (3, "BOTH_FALLBACK", "K5", "B0"),
    ], "routing priority drift")
    require(contract["source_ranking_reuse"]["no_new_model_fit_or_scoring"] is True, "no-retune ranking boundary drift")
    require(contract["bootstrap"] == {
        "unit": "USER",
        "method": "PERCENTILE",
        "iterations": 10000,
        "seed": 20260924,
        "ndcg_interval": "TWO_SIDED_95_PERCENT_2_5_AND_97_5_PERCENTILES",
        "harm_interval": "ONE_SIDED_95_PERCENT_UPPER_95TH_PERCENTILE",
    }, "bootstrap drift")
    decision = contract["decision_rule"]
    require(decision["priority_is_normative"] is True, "decision priority authority drift")
    require([row["status"] for row in decision["priority"]] == [
        "FAIL_SAFETY_MARGIN_EXCEEDED",
        "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION",
        "INCONCLUSIVE_POST_HOC_VALIDATION",
    ], "decision priority drift")
    require(decision["thresholds"] == {
        "harm_at_2_delta_one_sided_95_upper_max": 0.005,
        "mean_delta_ndcg_at_10_min": 0.005,
        "ndcg_two_sided_95_lower_strictly_greater_than": 0.0,
    }, "decision threshold drift")
    attestations = set(contract["leakage_lock"]["required_attestations"])
    for required in ("RUNNER_SHA256", "VERIFIER_SHA256", "GIT_REVISION", "GIT_DIRTY_STATUS_AND_STATUS_SHA256", "FUTURE_METRICS_READ_FALSE"):
        require(required in attestations, f"lock attestation missing: {required}")
    require(contract["resource_bounds"]["resume_required_for_real_run"] is True, "resume boundary drift")
    return {
        "status": "PASS_REC_EV_019E_CONTRACT",
        "post_hoc": True,
        "confirmatory_users": 1053,
        "maximum_success_status": "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION",
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


def main() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    print(json.dumps(validate_contract(contract), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
