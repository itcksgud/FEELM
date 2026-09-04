#!/usr/bin/env python3
"""Validate the immutable REC-EV-019D preregistration contract without opening data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019d-prefix-ablation-artifacts.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    require(contract.get("contract_id") == "rec-ev-019d-prefix-ablation-artifacts-v1", "contract identity drift")
    require(contract.get("status") == "APPROVED_FOR_BOUNDED_REAL_VALIDATION", "contract status drift")
    require(contract.get("task_id") == "TASK-REC-EV-019D", "task identity drift")
    require(contract.get("protocol_version") == "rec-eval-vnext-2", "protocol version drift")
    require(contract.get("invariants") == {
        "execution_role": "VALIDATION_019D",
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }, "fail-closed invariant drift")
    authorization = contract["current_authorization"]
    require(authorization["locked_test_access"] is False, "Locked Test authorization drift")
    require(authorization["tuning_or_threshold_change"] is False, "confirmatory tuning authorization drift")
    require(authorization["champion_selection"] is False, "champion authorization drift")
    require(authorization["product_policy_change"] is False, "product-policy authorization drift")
    require(contract["role_firewall"]["validation_role_literal"] == "validation-019d", "role literal drift")
    require(contract["role_firewall"]["test_mode_or_test_flag_forbidden"] is True, "Test CLI boundary drift")
    allowed = contract["allowed_input_artifacts"]
    require(set(allowed) == {
        "validation_prefixes", "validation_windows", "candidate_core", "validation_selection",
        "lightfm_config", "lightfm_interactions", "lightfm_item_features", "lightfm_result",
    }, "input allowlist drift")
    for name, artifact in allowed.items():
        require(isinstance(artifact.get("bytes"), int) and artifact["bytes"] > 0, f"input size missing: {name}")
        require(len(str(artifact.get("sha256", ""))) == 64, f"input SHA-256 missing: {name}")
    require(contract["cohort"]["expected_users"] == 1479, "K10 cohort drift")
    require(contract["confirmatory_set"]["expected_excluded_users"] == 426, "exclusion drift")
    require(contract["confirmatory_set"]["expected_users"] == 1053, "confirmatory cohort drift")
    model = contract["model"]
    require((model["primary"], model["trial_id"], model["seed"], model["candidate_count"]) == ("B8_LIGHTFM", "B8_LIGHTFM-T003", 17, 41625), "model lock drift")
    require(model["fallback"] == "B0_MOVIELENS_BAYESIAN_RATING_T003", "fallback drift")
    require(contract["estimands"]["primary"]["id"] == "COMMON_K10_SEEN_MASK_PROFILE_ABLATION", "primary mask drift")
    require(contract["estimands"]["secondary"]["id"] == "ARM_SPECIFIC_SEEN_MASK_END_TO_END", "secondary mask drift")
    require(contract["metrics"]["primary"] == "PER_USER_PAIRED_DELTA_NDCG_AT_10_K10_MINUS_K5", "primary metric drift")
    require(contract["bootstrap"] == {
        "unit": "USER",
        "method": "PERCENTILE",
        "iterations": 10000,
        "seed": 20260924,
        "ndcg_interval": "TWO_SIDED_95_PERCENT_2_5_AND_97_5_PERCENTILES",
        "harm_interval": "ONE_SIDED_95_PERCENT_UPPER_95TH_PERCENTILE",
    }, "bootstrap drift")
    priority = contract["decision_rule"]["priority"]
    require([row["status"] for row in priority] == ["FAIL", "FAIL", "PASS", "INCONCLUSIVE"], "decision priority drift")
    require(priority[0]["reason"] == "SAFETY_MARGIN_EXCEEDED", "safety priority drift")
    thresholds = contract["decision_rule"]["thresholds"]
    require(thresholds == {
        "mean_delta_ndcg_at_10_min": 0.005,
        "ndcg_two_sided_95_lower_strictly_greater_than": 0.0,
        "harm_at_2_delta_one_sided_95_upper_max": 0.005,
    }, "success threshold drift")
    require(contract["rec_ev_019c_prediction_reuse"]["allowed"] is False, "019C prediction reuse drift")
    require(contract["leakage_lock"]["post_run_contract_change_forbidden"] is True, "post-run mutation boundary drift")
    require(contract["resource_bounds"]["resume_required_for_real_run"] is True, "resume boundary drift")
    return {
        "status": "PASS_REC_EV_019D_CONTRACT",
        "cohort_users": 1479,
        "confirmatory_users": 1053,
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
