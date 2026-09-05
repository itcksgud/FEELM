#!/usr/bin/env python3
"""Fail-closed structural validator for REC-EV-023C."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023c-crossed-membership-sensitivity.json"
EXPECTED_CANONICAL_CONTRACT_SHA256 = "6d639d647569b3d3bde7ca3b016c20e52645e8576c4cc03d31697837e997740d"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    canonical = (json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    require(hashlib.sha256(canonical).hexdigest() == EXPECTED_CANONICAL_CONTRACT_SHA256, "canonical contract hash drift")
    require(contract.get("contract_id") == "rec-ev-023c-crossed-membership-sensitivity-v1", "contract identity drift")
    require(contract.get("status") == "APPROVED_FOR_ADAPTIVE_CROSSED_MEMBERSHIP_SENSITIVITY", "status drift")
    require(contract["independent_design_audit"] == {
        "thread_id": "01a06eeb-df86-7252-ac77-afc8d6ec24f7", "rounds": 3, "final_verdict": "023C_DESIGN_PASS",
    }, "design audit drift")
    require(contract["authorization"] == {
        "reuse_rec_ev_023b_fixed_metrics_and_membership": True,
        "prediction_or_ranking_recompute": False,
        "model_fit_or_retune": False,
        "locked_test_access": False,
        "stage2_access": False,
        "final_reserve_access": False,
        "champion_selection": False,
        "product_policy_change": False,
    }, "authorization drift")
    require(contract["adaptive_boundary"]["allowed_claim"] == "OBSERVED_FIXED_JUDGED20_MASKED_COLD_ITEM_MEMBERSHIP_UNCERTAINTY_SENSITIVITY", "claim drift")
    require(contract["adaptive_boundary"]["rec_ev_023b_result_seen_before_design"] is True, "adaptive result-use declaration missing")
    require(set(contract["adaptive_boundary"]["forbidden_claims"]) == {
        "FRESH", "CONFIRMATORY", "ITEM_GENERALIZATION", "FULL_MASKED_COLD_UNIVERSE", "STRICT_COLD_START", "FUTURE_ITEM",
    }, "forbidden claim drift")
    require(contract["implementation_artifacts"] == [
        "scripts/rec_ev_022a_core.py",
        "scripts/run_rec_ev_023b_masked_cold_screen.py",
        "scripts/run_rec_ev_023c_crossed_sensitivity.py",
        "scripts/validate_rec_ev_023c_contract.py",
    ], "implementation pin set drift")
    expected_paths = {
        "rec_ev_023b_contract": "docs/recommendation/contracts/rec-ev-023b-masked-item-cold-content-screen.json",
        "rec_ev_023b_protocol_lock": "outputs/recommendation-evidence/rec-ev-023b/protocol-lock.json",
        "rec_ev_023b_source_manifest": "outputs/recommendation-evidence/rec-ev-023b/source-manifest.json",
        "rec_ev_023b_universe": "outputs/recommendation-evidence/rec-ev-023b/cache/universe.npz",
        "rec_ev_023b_score_input": "outputs/recommendation-evidence/rec-ev-023b/cache/score-input.parquet",
        "rec_ev_023b_score_prepared_integrity": "outputs/recommendation-evidence/rec-ev-023b/cache/score-prepared.integrity.json",
        "rec_ev_023b_user_metrics": "outputs/recommendation-evidence/rec-ev-023b/user-metrics.parquet",
        "rec_ev_023b_user_metrics_integrity": "outputs/recommendation-evidence/rec-ev-023b/user-metrics.integrity.json",
        "rec_ev_023b_result": "outputs/recommendation-evidence/rec-ev-023b/development-screen-result.json",
        "rec_ev_023b_selection": "outputs/recommendation-evidence/rec-ev-023b/development-screen-selection.json",
    }
    actual_paths = {name: value["path"] for name, value in contract["allowed_input_artifacts"].items()}
    require(actual_paths == expected_paths, "allowlist path set drift")
    require(all(int(value["bytes"]) > 0 and len(str(value["sha256"])) == 64 for value in contract["allowed_input_artifacts"].values()), "source pin missing")
    allowed = {(ROOT / path).resolve() for path in actual_paths.values()}
    forbidden = {(ROOT / path).resolve() for path in contract["forbidden_input_artifacts"]}
    require(allowed.isdisjoint(forbidden), "allowed and forbidden artifact overlap")
    require(contract["membership"] == {
        "users": 9520,
        "items_per_user": 20,
        "memberships": 190400,
        "unique_items": 3565,
        "connected_components": 1,
        "maximum_item_degree": 3025,
        "top10_item_degree_sum": 23067,
        "top10_share_exact": "23067/190400",
        "target_items_must_be_masked_cold": True,
        "csr_value": 0.05,
        "membership_recomputed_in_replicate": False,
    }, "membership constants drift")
    require(contract["contrast_family"] == {
        "source": "BYTE_IDENTICAL_REC_EV_023B_BUILD_CONTRASTS",
        "cells": 6,
        "comparisons_per_cell": 6,
        "endpoints": 2,
        "expected_contrasts": 72,
        "point_estimate": "UNWEIGHTED_USER_MEAN_FROM_SEALED_REC_EV_023B_METRICS",
    }, "contrast family drift")
    bootstrap = contract["bootstrap"]
    require(bootstrap["protocol_version"] == "rec-ev-023c-crossed-sensitivity-v1", "bootstrap protocol drift")
    require(bootstrap["digest_prefix"] == "feelm-bootstrap-v1|", "bootstrap prefix drift")
    require((bootstrap["attempts"], bootstrap["valid_replicates"], bootstrap["decimal_precision"]) == (10000, 2000, 80), "bootstrap count or precision drift")
    require(bootstrap["numpy_version"] == "1.26.4" and bootstrap["dtype"] == "FLOAT64", "bootstrap runtime numeric drift")
    require(bootstrap["valid_attempt_selection"] == "FIRST_2000_ATTEMPTS_COMMON_VALID_ACROSS_ALL_THREE_REGIMES", "valid attempt rule drift")
    require(bootstrap["regimes"] == {
        "USER_ONLY": "USER_WEIGHT",
        "ITEM_ONLY": "MEAN_ITEM_WEIGHT_OVER_FIXED_20",
        "TWO_WAY": "USER_WEIGHT_TIMES_MEAN_ITEM_WEIGHT_OVER_FIXED_20",
    }, "bootstrap regimes drift")
    require(bootstrap["common_valid"] == "ALL_THREE_DENOMINATORS_GT_ZERO_FINITE_AND_ALL_216_ESTIMATES_FINITE", "common valid rule drift")
    require(bootstrap["golden"] == [
        {"attempt": 0, "axis": "user", "cluster_id": "0" * 64, "x": 11644714544804977649, "weight": 1},
        {"attempt": 0, "axis": "item", "cluster_id": "1", "x": 7654935420082890939, "weight": 1},
        {"attempt": 17, "axis": "user", "cluster_id": "a" * 64, "x": 14214353544981560736, "weight": 2},
        {"attempt": 1999, "axis": "item", "cluster_id": "999", "x": 2181741186927563465, "weight": 0},
    ], "bootstrap golden drift")
    require(contract["intervals"] == {
        "per_regime": True,
        "se": "STD_OF_2000_WEIGHTED_ESTIMATES_DDOF1",
        "studentized": "MAX_ABS_REPLICATE_MINUS_POINT_DIV_SE_OVER_ACTIVE_72",
        "critical": "NEAREST_RANK_CEIL_0_95_TIMES_2000_NO_INTERPOLATION",
        "ci": "POINT_PLUS_MINUS_CRITICAL_TIMES_SE",
        "zero_se": "NON_ESTIMABLE_AND_GATE_FAIL",
        "all_zero_se": "CRITICAL_ZERO_DIAGNOSTIC_AND_ALL_GATES_FAIL",
    }, "interval semantics drift")
    require(contract["decision"] == {
        "utility_margin": 0.005,
        "worst_loss_margin": 0.01,
        "rec_ev_023b_q_recomputed_from_sealed_intervals": True,
        "two_way_truth_table": "SAME_STRUCTURED_E5_RRF_RULES_AS_REC_EV_023B",
        "robust_forward": "REC_EV_023B_RULE_AND_TWO_WAY_RULE",
        "robust_forward_subset_of_rec_ev_023b_forward": True,
        "user_only_and_item_only": "DECOMPOSITION_DIAGNOSTIC_ONLY",
        "new_forward_from_023c_only": False,
        "retune_or_family_reduction": False,
        "champion": None,
    }, "decision drift")
    require(contract["output_root"] == "outputs/recommendation-evidence/rec-ev-023c", "output root drift")
    require(set(contract["outputs"]) == {
        "protocol_lock", "source_manifest", "progress", "membership", "membership_integrity",
        "replicates", "replicates_integrity", "selection", "result",
    }, "output mapping drift")
    require(contract["resume"]["required"] is True and contract["resume"]["drift"] == "FAIL_CLOSED", "resume drift")
    require(contract["invariants"] == {
        "predictions_recomputed": False,
        "rankings_recomputed": False,
        "q_labels_opened": False,
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "champion": None,
        "product_policy_updated": False,
    }, "invariant drift")
    return {
        "status": "PASS_REC_EV_023C_CONTRACT",
        "users": 9520,
        "items": 3565,
        "contrasts": 72,
        "replicates": 2000,
        "locked_test_access": False,
        "champion": None,
    }


def main() -> int:
    print(json.dumps(validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8"))), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
