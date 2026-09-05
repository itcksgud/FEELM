#!/usr/bin/env python3
"""Fail-closed structural validator for the joint REC-EV-023E/F design."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023ef-joint-transfer-design.json"
EXPECTED_CANONICAL_CONTRACT_SHA256 = "c5e2e04b4e88627c6b983a4c94af5dc07e1da58583ba76e58ddfaccff5a438c6"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    canonical = (json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    require(hashlib.sha256(canonical).hexdigest() == EXPECTED_CANONICAL_CONTRACT_SHA256, "canonical contract hash drift")
    require(contract.get("contract_id") == "rec-ev-023ef-joint-transfer-design-v1", "contract identity drift")
    require(contract.get("status") == "APPROVED_FOR_PRELABEL_FIREWALLED_FEASIBILITY", "status drift")
    require(contract["independent_design_audit"] == {
        "thread_id": "01a0704a-ff92-7851-904a-bf3970b3d905", "rounds": 3, "final_verdict": "NEXT_DESIGN_PASS",
    }, "design audit drift")
    incident = contract["prior_invalid_preflight_incident"]
    require(incident["old_locked_user_movie_ids_parsed"] is True, "incident disclosure missing")
    require(incident["rating_values_parsed"] is False and incident["timestamps_parsed"] is False, "incident scope drift")
    require(incident["counts_discarded_and_forbidden_as_contract_inputs"] == [431, 3498], "discarded counts drift")
    authorization = contract["authorization"]
    require(authorization["train_users_prior_only"] is True, "train boundary drift")
    require(authorization["old_locked_ratings_timestamps_or_metrics"] is False, "locked outcome access enabled")
    require(authorization["final_reserve_access"] is False, "final reserve enabled")
    require(authorization["champion_selection"] is False and authorization["product_policy_change"] is False, "product boundary drift")
    require(contract["implementation_artifacts"] == [
        "scripts/rec_ev_022a_core.py", "scripts/run_rec_ev_023ef_preflight.py", "scripts/validate_rec_ev_023ef_contract.py",
        "scripts/tests/test_rec_ev_023ef_preflight.py",
    ], "implementation set drift")
    expected_paths = {
        "movielens_archive": "C:/higher/projects/MM/data/raw/ml-32m.zip",
        "structured_features": "outputs/recommendation-evidence/rec-ev-019b/structured-features.parquet",
        "korean_movie_id_projection": "docs/recommendation/evidence/results/korean-origin-movie-id-projection-v1.json",
        "korean_coverage_evidence": "docs/recommendation/evidence/REC-DATA-002-korean-origin-coverage.md",
        "stage2_selection": "outputs/recommendation-evidence/rec-ev-022b/stage2-selection.json",
        "train_prior": "outputs/recommendation-evidence/rec-ev-022a/cache/train-model.npz",
    }
    require({name: row["path"] for name, row in contract["allowed_input_artifacts"].items()} == expected_paths, "input paths drift")
    require(all(int(row["bytes"]) > 0 and len(str(row["sha256"])) == 64 for row in contract["allowed_input_artifacts"].values()), "input pin missing")
    roles = contract["roles_and_reader"]
    require(roles["evaluation_role_bucket_ranges"] == [[6000, 7999], [8000, 9199]], "evaluation roles drift")
    require(roles["final_reserve_bucket_range_forbidden"] == [9200, 9999], "final reserve boundary drift")
    require(roles["preflight_rating_value_bytes_parsed"] == 0 and roles["preflight_timestamp_bytes_parsed"] == 0, "preflight outcome parse enabled")
    require(roles["excluded_counts_forbidden"] is True and roles["raw_user_id_forbidden_in_outputs"] is True, "reader privacy drift")
    require(contract["universe"]["e5_intersection_forbidden"] is True, "E5 intersection reintroduced")
    require(contract["universe"]["recent"] == "2020_LE_RELEASE_YEAR_LE_2023", "recent period drift")
    common = contract["common_design"]
    require(common["panels"] == 4 and common["profile_n"] == 14 and common["primary_n"] == 2, "panel or Top2 drift")
    require(common["cells"] == [
        {"encoding": "BINARY_SIGN", "k": 6}, {"encoding": "PERCENTILE_MAGNITUDE", "k": 6},
        {"encoding": "BINARY_SIGN", "k": 8}, {"encoding": "PERCENTILE_MAGNITUDE", "k": 8},
        {"encoding": "BINARY_SIGN", "k": 14}, {"encoding": "PERCENTILE_MAGNITUDE", "k": 14},
    ], "cell set drift")
    require(common["heads"] == ["BASIC", "RELEASE_PROXY", "FULL_CURRENT"], "head set drift")
    require(common["head_groups"] == {"BASIC": ["G", "C"], "RELEASE_PROXY": ["G", "C", "P"], "FULL_CURRENT": ["G", "C", "P", "W"]}, "head groups drift")
    require(common["b0_or_target_popularity"] == "FORBIDDEN", "popularity enabled")
    require(common["panel_salts"] == {
        "REC-EV-023E": {
            "profile_control_order": [f"rec-ev-023e-panel-{index}-non-korean-order-v1" for index in range(4)],
            "target_order": [f"rec-ev-023e-panel-{index}-korean-target-order-v1" for index in range(4)],
            "tie_prefix": "rec-ev-023e-rank-tie-v1",
        },
        "REC-EV-023F": {
            "profile_control_order": [f"rec-ev-023f-panel-{index}-pre2020-order-v1" for index in range(4)],
            "target_order": [f"rec-ev-023f-panel-{index}-recent-target-order-v1" for index in range(4)],
            "tie_prefix": "rec-ev-023f-rank-tie-v1",
        },
    }, "panel salts drift")
    require(common["tie_payload"] == "UTF8(TIE_PREFIX|USER_KEY|PANEL_INDEX|DOMAIN|HEAD|ENCODING|K|MOVIE_ID)", "tie payload drift")
    require(common["partial_tie"] == "SHA256(TIE_PREFIX|USER_KEY|PANEL_INDEX|DOMAIN|HEAD|ENCODING|K|MOVIE_ID)_DIGEST_ASC_THEN_MOVIE_ID_ASC", "partial tie drift")
    e = contract["experiments"]["REC-EV-023E"]
    f = contract["experiments"]["REC-EV-023F"]
    require((e["target_n"], e["control_n"], e["minimum_profile_control_ratings"], e["minimum_target_ratings"]) == (10, 10, 24, 10), "023E cohort drift")
    require((e["minimum_users"], e["minimum_unique_target_items"], e["alpha"]) == (150, 60, 0.025), "023E floor drift")
    require(e["unique_target_item_floor_population"] == "UNION_OF_ACTUALLY_SELECTED_TARGET10_ACROSS_FOUR_FIXED_PANELS_AND_ELIGIBLE_USERS", "023E target floor population drift")
    require((f["target_n"], f["control_n"], f["minimum_profile_control_ratings"], f["minimum_target_ratings"]) == (20, 20, 34, 20), "023F cohort drift")
    require((f["minimum_users"], f["minimum_unique_target_items"], f["alpha"]) == (500, 200, 0.025), "023F floor drift")
    require(f["unique_target_item_floor_population"] == "UNION_OF_ACTUALLY_SELECTED_TARGET20_ACROSS_FOUR_FIXED_PANELS_AND_ELIGIBLE_USERS", "023F target floor population drift")
    stats = contract["statistics"]
    require(stats["primary_unit"] == "USER_AFTER_ARITHMETIC_MEAN_OF_FOUR_PANEL_CONTRASTS", "primary unit drift")
    require((stats["valid_replicates"], stats["attempt_ids"], stats["expected_primary_family"]) == (4000, [0, 7999], 108), "bootstrap family drift")
    require(stats["valid_attempt_selection"] == "FIRST_4000_ATTEMPTS_WITH_POSITIVE_USER_WEIGHT_DENOMINATOR_AND_ALL_108_ESTIMATES_FINITE", "valid attempt rule drift")
    require(stats["poisson_golden_fixture_required"] is True, "Poisson golden fixture missing")
    require(stats["poisson_protocol"] == "SHA256_UTF8(feelm-bootstrap-v1|rec-ev-023ef-user-bootstrap-v1|EVIDENCE_ID|ATTEMPT|user|USER_KEY)_FIRST_UINT64_BE_THEN_DECIMAL80_POISSON1_INVERSE_CDF", "Poisson protocol drift")
    require(stats["poisson_golden_fixtures"] == [
        {"evidence_id": "REC-EV-023E", "attempt": 0, "user_key": "0" * 64, "uint64": 11217529067192843872, "weight": 1},
        {"evidence_id": "REC-EV-023E", "attempt": 7999, "user_key": "f" * 64, "uint64": 17955834813087592270, "weight": 3},
        {"evidence_id": "REC-EV-023F", "attempt": 17, "user_key": "0123456789abcdef" * 4, "uint64": 6667954965483306694, "weight": 0},
        {"evidence_id": "REC-EV-023F", "attempt": 2026, "user_key": "abcdef0123456789" * 4, "uint64": 12004171215448210824, "weight": 1},
    ], "Poisson golden fixtures drift")
    require(stats["critical"] == "NEAREST_RANK_CEIL_0_975_TIMES_4000", "critical rule drift")
    require(stats["fixed_panel_membership_reweighting"] == "DIAGNOSTIC_ONLY_NOT_PRIMARY_GATE_OR_ITEM_GENERALIZATION", "item diagnostic overclaim")
    decision = contract["decision"]
    require(decision["head_hierarchy"] == ["BASIC", "RELEASE_PROXY", "FULL_CURRENT"], "head hierarchy drift")
    require(decision["cell_conjunction"] == "ALL_SIX_CELLS" and decision["result_driven_relaxation"] is False, "decision relaxation enabled")
    require(decision["champion"] is None, "champion set")
    require(decision["status_precedence"].startswith("INFEASIBLE_PRELABEL_THEN_INCONCLUSIVE"), "status precedence drift")
    require("KOREAN_USER_PERFORMANCE" in contract["claim_boundary"]["forbidden"], "Korean-user claim not forbidden")
    require("ITEM_OR_CATALOG_GENERALIZATION_FROM_USER_ONLY_CI" in contract["claim_boundary"]["forbidden"], "item generalization not forbidden")
    require(set(contract["outputs"]) == {"protocol_lock", "source_manifest", "progress", "preflight", "preflight_integrity"}, "output set drift")
    require(contract["resume"] == {"required": True, "partial_state": "FAIL_CLOSED", "drift": "FAIL_CLOSED"}, "resume drift")
    require(contract["invariants"] == {
        "old_locked_rating_timestamp_metric_opened": False,
        "final_reserve_opened": False,
        "evaluation_labels_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }, "invariants drift")
    return {"status": "PASS_REC_EV_023EF_JOINT_CONTRACT", "experiments": ["REC-EV-023E", "REC-EV-023F"], "primary_family_each": 108}


def main() -> int:
    print(json.dumps(validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8"))), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
