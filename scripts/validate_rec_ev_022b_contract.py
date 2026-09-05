#!/usr/bin/env python3
"""Validate the immutable REC-EV-022B Stage2 confirmation contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-022b-stage2-k-information-confirmation.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    require(contract.get("contract_id") == "rec-ev-022b-stage2-k-information-confirmation-v1", "contract identity drift")
    require(contract.get("status") == "APPROVED_FOR_STAGE2_IMPLEMENTATION_PARITY", "contract status drift")
    require(contract["independent_design_audit"]["final_verdict"] == "PASS", "independent audit missing")
    require(contract["implementation_artifacts"] == [
        "scripts/rec_ev_022a_core.py",
        "scripts/run_rec_ev_022a_stage1.py",
        "scripts/validate_rec_ev_022a_contract.py",
        "scripts/run_rec_ev_022b_stage2.py",
        "scripts/validate_rec_ev_022b_contract.py",
    ], "implementation pin set drift")
    authorization = contract["authorization"]
    require(authorization["locked_test_access"] is False and authorization["final_reserve_access"] is False, "holdout boundary drift")
    require(authorization["stage1_reselection"] is False and authorization["model_retrain_or_retune"] is False, "reuse boundary drift")
    require(contract["role_and_source"]["new_role_bucket_range"] == [8000, 9199], "Stage2 role drift")
    require(contract["role_and_source"]["final_reserve_bucket_range_forbidden"] == [9200, 9999], "Final role drift")
    require(contract["prefilter_reader"] == {
        "before_role_decision": "READ_RAW_LINE_AND_PARSE_ONLY_FIRST_USER_ID_FIELD",
        "excluded_row": "DISCARD_RAW_LINE_WITHOUT_PARSING_MOVIE_ID_RATING_OR_TIMESTAMP",
        "allowed_row": "PARSE_MOVIE_ID_RATING_AFTER_OLD_AND_NEW_ROLE_ALLOWLIST",
        "sentinel_test_required": True,
    }, "pre-filter reader drift")
    require(contract["fixed_semantics"]["minimum_common30_users"] == 5000, "cohort minimum drift")
    require(contract["k_values"]["selection_even_set"] == list(range(2, 31, 2)), "even K set drift")
    require(contract["k_values"]["k30_automatic_candidate"] is False, "K30 auto-candidate drift")
    stats = contract["statistics"]
    require(stats["expected_contrasts"] == 456, "contrast family size drift")
    require((stats["bootstrap_repeats"], stats["seed"]) == (10000, 20260924), "bootstrap drift")
    require((stats["utility_margin"], stats["worst_loss_margin"]) == (0.005, 0.01), "decision margins drift")
    require(stats["separate_precision_gate"] is False, "precision rule drift")
    screen = contract["k_screen"]
    require(screen["onset_required_for_encoding"] is True, "onset eligibility drift")
    require(screen["k_plateau_search"] == list(range(2, 29, 2)), "plateau search drift")
    require(screen["plateau_without_onset_can_enter"] is False, "plateau eligibility drift")
    truth = contract["candidate_truth_table"]
    require(truth["s0"] == "G cross K_CAND", "S0 drift")
    require(truth["reverse_or_undefined_removal"] is False and truth["arbitrary_top6"] is False, "candidate removal drift")
    require(truth["seven_or_more_cells"] == "OVERFLOW_NO_SELECTION", "overflow rule drift")
    require(contract["claim_boundary"]["confirmatory"] == "NONE_STAGE2_SLICE_ALREADY_SEEN_BEFORE_IMPLEMENTATION_REPAIR", "confirmation boundary drift")
    require(contract["claim_boundary"]["fresh_confirmation"] is False, "fresh confirmation claim drift")
    require(all(contract["resume"][key] is True for key in (
        "source_manifest_sha_verified", "cohort_sha_rows_and_run_signature_verified",
        "each_metric_part_sha_rows_user_slice_and_run_signature_verified",
        "combined_user_metrics_sha_rows_and_run_signature_verified",
        "direct_score_revalidates_cohort", "partial_combined_artifact_fails_closed",
        "expected_metric_part_path_set_exact", "implementation_sha_locked",
    )), "resume integrity drift")
    invariants = contract["invariants"]
    require(invariants == {
        "locked_test_opened": False,
        "stage1_user_metrics_opened": False,
        "final_reserve_opened": False,
        "model_retrained": False,
        "product_policy_updated": False,
        "champion": None,
    }, "invariant drift")
    for name, artifact in contract["allowed_input_artifacts"].items():
        require(int(artifact["bytes"]) > 0 and len(str(artifact["sha256"])) == 64, f"input pin missing: {name}")
    return {
        "status": "PASS_REC_EV_022B_CONTRACT",
        "audit": "PASS",
        "expected_contrasts": 456,
        "even_k": list(range(2, 31, 2)),
        "stage2_only": True,
        "final_reserve_access": False,
        "locked_test_access": False,
    }


def main() -> int:
    print(json.dumps(validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8"))), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
