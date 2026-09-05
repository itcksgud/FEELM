#!/usr/bin/env python3
"""Fail-closed structural validation for the REC-EV-022A preregistration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-022a-k-input-encoding-stage1.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    require(contract.get("contract_id") == "rec-ev-022a-k-input-encoding-stage1-v1", "contract identity drift")
    require(contract.get("status") == "APPROVED_FOR_STAGE1_DEVELOPMENT", "contract status drift")
    require(contract["independent_design_audit"]["final_verdict"] == "PASS", "independent audit not passed")
    require(contract["implementation_artifacts"] == [
        "scripts/rec_ev_022a_core.py",
        "scripts/run_rec_ev_022a_stage1.py",
        "scripts/validate_rec_ev_022a_contract.py",
    ], "implementation pin set drift")
    authorization = contract["authorization"]
    require(authorization["locked_test_access"] is False, "Locked Test authorization drift")
    require(authorization["stage2_access"] is False and authorization["final_reserve_access"] is False, "role authorization drift")
    require(authorization["product_policy_change"] is False and authorization["champion_selection"] is False, "adoption boundary drift")
    require(contract["source_population"]["eligible_old_buckets"] == [0, 59], "old non-test source range drift")
    require(contract["source_population"]["excluded_old_buckets"] == [60, 99], "old Locked Test exclusion drift")
    require(contract["prefilter_reader"] == {
        "before_role_decision": "READ_RAW_LINE_AND_PARSE_ONLY_FIRST_USER_ID_FIELD",
        "excluded_row": "DISCARD_RAW_LINE_WITHOUT_PARSING_MOVIE_ID_RATING_OR_TIMESTAMP",
        "allowed_row": "PARSE_MOVIE_ID_RATING_AND_TIMESTAMP_AFTER_OLD_AND_NEW_ROLE_ALLOWLIST",
        "sentinel_test_required": True,
    }, "pre-filter reader drift")
    require(contract["user_roles"]["ranges"] == {
        "TRAIN_USERS": [0, 5999],
        "STAGE1_SELECTION": [6000, 7999],
        "STAGE2_DEVELOPMENT": [8000, 9199],
        "FINAL_RESERVE": [9200, 9999],
    }, "new role split drift")
    require(contract["user_roles"]["learned_quantities_use_train_users_only"] is True, "training isolation drift")
    require(contract["user_roles"]["stage1_may_materialize_roles"] == ["TRAIN_USERS", "STAGE1_SELECTION"], "stage1 allowlist drift")
    require(contract["determinism"]["primary_order_salt"] == "rec-ev-022a-order-primary-v1", "primary order salt drift")
    require(contract["determinism"]["sensitivity_order_salts"] == [
        f"rec-ev-022a-order-sensitivity-{index:02d}-v1" for index in range(1, 10)
    ], "sensitivity salt drift")
    require(contract["determinism"]["numpy_version"] == "1.26.4", "NumPy lock drift")
    require(contract["rating_scale"]["tau_primary"] == 5.0, "rating smoothing drift")
    require(set(contract["encodings"]) == {"BINARY_SIGN", "PERCENTILE_MAGNITUDE", "ORDINAL_RANK", "actual_binary_behavior_claim"}, "encoding set drift")
    require(contract["encodings"]["actual_binary_behavior_claim"] is False, "binary UI claim drift")
    require(set(contract["anchors"]) == {"STRUCTURED_CONTENT_SIM", "USER_DISJOINT_ITEMKNN_SIM"}, "anchor set drift")
    require(contract["anchors"]["USER_DISJOINT_ITEMKNN_SIM"]["shrinkage"] == 50.0, "ItemKNN shrinkage drift")
    require(contract["cohort"]["minimum_common30_users"] == 5000, "minimum cohort gate drift")
    require(contract["evaluation"]["primary_candidate_set"] == "JUDGED20_ONLY", "candidate task drift")
    require(contract["evaluation"]["primary_n"] == 2, "Top-2 unit drift")
    require(contract["evaluation"]["full_catalog_stage1"] is False, "full-catalog scope drift")
    require(contract["evaluation"]["unrated_as_negative"] is False, "unrated semantics drift")
    stats = contract["statistics"]
    require((stats["bootstrap_repeats"], stats["seed"], stats["numpy_version"], stats["bit_generator"]) == (10000, 20260924, "1.26.4", "PCG64"), "bootstrap drift")
    require(stats["sample_sd_ddof"] == 1 and stats["parallel_rng"] is False, "bootstrap implementation drift")
    decision = contract["decision"]
    require((decision["utility_margin"], decision["worst_loss_noninferiority_margin"]) == (0.005, 0.01), "K0 decision margin drift")
    require((decision["plateau_utility_equivalence"], decision["plateau_worst_loss_equivalence"]) == (0.005, 0.01), "plateau margin drift")
    require(decision["maximum_stage2_candidates_total"] == 6 and decision["k30_automatic_candidate"] is False, "candidate bound drift")
    require(contract["resume"]["required_for_run"] is True, "resume requirement drift")
    require(all(contract["resume"][key] is True for key in (
        "source_manifest_sha_verified", "prepared_cache_sha_and_run_signature_verified",
        "each_metric_part_sha_rows_user_slice_and_run_signature_verified",
        "combined_user_metrics_sha_rows_and_run_signature_verified",
        "direct_score_revalidates_prepared_cache", "partial_combined_artifact_fails_closed",
        "expected_metric_part_path_set_exact", "implementation_sha_locked",
    )), "resume integrity drift")
    invariants = contract["invariants"]
    require(invariants == {
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }, "fail-closed invariant drift")
    for name, artifact in contract["allowed_input_artifacts"].items():
        require(int(artifact["bytes"]) > 0, f"input bytes missing: {name}")
        require(len(str(artifact["sha256"])) == 64, f"input hash missing: {name}")
    return {
        "status": "PASS_REC_EV_022A_CONTRACT",
        "independent_design_audit": "PASS",
        "locked_test_access": False,
        "stage1_only": True,
        "k_values": 31,
        "encodings": 3,
        "anchors": 2,
        "primary_n": 2,
    }


def main() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    print(json.dumps(validate_contract(contract), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
