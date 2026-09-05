#!/usr/bin/env python3
"""Fail-closed validator for the REC-EV-023F execution contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023f-recent-release-transfer.json"
JOINT = ROOT / "docs/recommendation/contracts/rec-ev-023ef-joint-transfer-design.json"
EXPECTED_CANONICAL_SHA256 = "c4d490e505c36e27e2bb633eb61019f10b8bc3f13148ed5cecc25ded895fc60e"
JOINT_RAW_SHA256 = "dd30335bdae575134b1447551e8fa1fd74ac33711e7477012543f58e278e5ac7"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical_sha(value: Mapping[str, Any]) -> str:
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    require(canonical_sha(contract) == EXPECTED_CANONICAL_SHA256, "canonical REC-EV-023F contract hash drift")
    require(contract.get("contract_id") == "rec-ev-023f-recent-release-transfer-v1", "contract identity drift")
    require(contract.get("evidence_id") == "REC-EV-023F", "evidence identity drift")
    joint_raw = JOINT.read_bytes()
    require(len(joint_raw) == 13381 and hashlib.sha256(joint_raw).hexdigest() == JOINT_RAW_SHA256, "joint contract pin drift")
    joint = json.loads(joint_raw)
    binding = contract["joint_contract_binding"]
    require(binding == {
        "path": "docs/recommendation/contracts/rec-ev-023ef-joint-transfer-design.json",
        "bytes": 13381,
        "sha256": JOINT_RAW_SHA256,
        "normative_inheritance": "EXACT",
        "inherited_sections": ["roles_and_reader", "universe", "common_design", "experiments.REC-EV-023F", "statistics", "decision", "claim_boundary", "resume", "invariants"],
        "missing_conflicting_or_locally_weakened_rule": "FATAL_BEFORE_LOCK",
    }, "joint normative binding drift")
    require(contract["execution_delta"] == {
        "profile_control_partition": "FOR_EACH_USER_PANEL_HASH_ORDER_PRE_2020_POOL_THEN_PROFILE_FIRST14_AND_CONTROL_NEXT20_WITHOUT_OVERLAP",
        "label_firewall": "BEFORE_COMPLETE_SCORE_RANK_INTEGRITY_TARGET_AND_CONTROL_RATING_BYTES_RATING_INDEX_FULL_HISTOGRAM_Q_PARSE_READ_MATERIALIZE_AND_PERSIST_ALL_FORBIDDEN",
        "label_open_transition": "AFTER_COMPLETE_SCORE_RANK_INTEGRITY_VERIFY_THEN_EVALUATION_LABELS_OPENED_FALSE_TO_TRUE",
        "joint_invariant_scope": "JOINT_EVALUATION_LABELS_OPENED_FALSE_IS_THE_PRELABEL_INITIAL_STATE; ALL_OTHER_JOINT_INVARIANTS_REMAIN_PERMANENT",
        "inactive_contrast": "ANALYTIC_RANDOM_METRICS_AND_EXACT_ZERO_CONTRAST",
        "result_driven_relaxation": False,
    }, "execution delta drift")
    require(contract["implementation_artifacts"] == [
        "scripts/rec_ev_022a_core.py", "scripts/run_rec_ev_023c_crossed_sensitivity.py",
        "scripts/run_rec_ev_023ef_transfer.py", "scripts/validate_rec_ev_023f_contract.py",
        "scripts/tests/test_rec_ev_023ef_transfer.py",
    ], "implementation set drift")
    reader = contract["reader"]
    require(reader["maximum_user_id"] == joint["roles_and_reader"]["maximum_user_id"], "maximum user drift")
    require(reader["roles"] == joint["roles_and_reader"]["evaluation_role_bucket_ranges"], "evaluation role drift")
    require(reader["final_reserve_forbidden"] == joint["roles_and_reader"]["final_reserve_bucket_range_forbidden"], "final reserve drift")
    require(reader["excluded"] == "PARSE_USER_ID_ONLY_THEN_DISCARD_RAW_LINE" and reader["excluded_role_counts"] == "FORBIDDEN", "reader firewall drift")
    require(reader["membership_phase"] == "ALLOWED_USER_MOVIE_ID_ONLY_RATING_AND_TIMESTAMP_BYTES_FORBIDDEN", "membership reader drift")
    require(reader["score_input_phase"] == "AFTER_FIXED_MEMBERSHIP_PARSE_ONLY_SELECTED_PROFILE_RATING_BYTES_TARGET_CONTROL_RATING_BYTES_FORBIDDEN_TIMESTAMP_FORBIDDEN", "score-input reader drift")
    require(reader["rank_phase"] == "RAW_ARCHIVE_AND_LABEL_SOURCE_FORBIDDEN", "rank reader drift")
    require(reader["evaluation_phase"] == "ONLY_AFTER_COMPLETE_SCORE_RANK_INTEGRITY_PARSE_ELIGIBLE_USER_RATINGS_MATERIALIZE_TARGET_CONTROL_LABELS_AND_FULL_HIST_TIMESTAMP_FORBIDDEN", "evaluation reader drift")
    require(reader["duplicate_user_movie"] == "FATAL" and reader["raw_user_id_output"] == "FORBIDDEN", "reader identity drift")
    require(reader["anonymous_user_key"] == joint["roles_and_reader"]["anonymous_user_key"], "anonymous key drift")
    require(contract["universe"] == {
        "definition": joint["universe"]["definition"], "e5_intersection_forbidden": True, "basic_nonzero_required": True,
    }, "universe drift")
    experiment = joint["experiments"]["REC-EV-023F"]
    preflight = contract["preflight"]
    require((preflight["minimum_users"], preflight["minimum_unique_selected_targets"]) == (experiment["minimum_users"], experiment["minimum_unique_target_items"]), "preflight floor drift")
    require(preflight["unique_target_item_floor_population"] == experiment["unique_target_item_floor_population"], "target floor population drift")
    require((preflight["expected_users"], preflight["expected_unique_selected_targets"], preflight["expected_selected_target_memberships"]) == (686, 2866, 54880), "preflight evidence drift")
    cohort = contract["cohort"]
    require(cohort["profile_domain"] == "PRE_2020_STRUCTURED" and cohort["target_domain"] == "RELEASE_2020_2023_STRUCTURED", "domain drift")
    require(cohort["control_domain"] == "PRE_2020_STRUCTURED_DISJOINT_FROM_PROFILE", "control disjointness drift")
    require((cohort["profile_n"], cohort["target_n"], cohort["control_n"], cohort["panels"]) == (14, 20, 20, 4), "panel size drift")
    require((cohort["minimum_profile_control_ratings"], cohort["minimum_target_ratings"]) == (34, 20), "cohort floor drift")
    require(cohort["profile_control_salts"] == joint["common_design"]["panel_salts"]["REC-EV-023F"]["profile_control_order"], "profile/control salt drift")
    require(cohort["target_salts"] == joint["common_design"]["panel_salts"]["REC-EV-023F"]["target_order"], "target salt drift")
    require(contract["cells"] == joint["common_design"]["cells"], "cell family drift")
    require(contract["features"]["heads"] == joint["common_design"]["heads"], "head family drift")
    require(contract["features"]["head_groups"] == joint["common_design"]["head_groups"], "head groups drift")
    scoring = contract["scoring"]
    require(scoring["tie_payload"] == joint["common_design"]["tie_payload"] and scoring["partial_tie"] == joint["common_design"]["partial_tie"], "tie rule drift")
    require(scoring["b0"] == "FORBIDDEN" and "ZERO_CONTRAST" in scoring["inactive"], "fallback drift")
    stats = contract["statistics"]
    require(stats["alpha"] == experiment["alpha"] and stats["classes"] == joint["statistics"]["classes"] and stats["endpoints"] == joint["statistics"]["endpoints"], "family definition drift")
    require(stats["valid_replicates"] == 4000 and stats["attempts"] == [0, 7999], "bootstrap attempts drift")
    require(stats["valid_attempt_selection"] == joint["statistics"]["valid_attempt_selection"], "valid attempt rule drift")
    require(stats["critical"] == joint["statistics"]["critical"] and stats["poisson_inverse"] == "DECIMAL80_POISSON1", "bootstrap calculation drift")
    decision = contract["decision"]
    require(decision["target_predicate"] == joint["decision"]["target_signal"] and decision["gap_predicate"] == joint["decision"]["conditional_noninferiority"], "simultaneous predicate drift")
    require(decision["control_signal"] == joint["decision"]["control_signal"], "control gate drift")
    require(decision["head_hierarchy"] == joint["decision"]["head_hierarchy"] and decision["all_six_cells"] is True, "hierarchy drift")
    require(decision["result_driven_relaxation"] is False and decision["champion"] is None, "adaptive relaxation enabled")
    require(contract["claim_boundary"]["allowed"] == joint["claim_boundary"]["allowed_f"], "allowed claim drift")
    require(contract["claim_boundary"]["forbidden"] == joint["claim_boundary"]["forbidden"], "forbidden claim drift")
    require(contract["resume"] == {"required": True, "partial": "FAIL_CLOSED", "drift": "FAIL_CLOSED"}, "resume drift")
    require(set(contract["outputs"]) == {
        "protocol_lock", "source_manifest", "progress", "item_ids", "feature_basic", "feature_release", "feature_full",
        "score_input", "label_source", "prepared_integrity", "rank_parts", "rank", "rank_integrity",
        "evaluation_labels", "evaluation_labels_integrity", "panel_metrics", "panel_metrics_integrity",
        "user_contrasts", "user_contrasts_integrity", "bootstrap", "bootstrap_integrity", "selection", "result", "result_integrity",
    }, "output set drift")
    require(contract["invariants"] == {
        "evaluation_labels_opened_before_rank_seal": False, "old_locked_rating_timestamp_metric_opened": False,
        "final_reserve_opened": False, "product_policy_updated": False, "champion": None,
    }, "result boundary drift")
    return {"status": "PASS_REC_EV_023F_CONTRACT", "primary_family": 108}


def main() -> int:
    print(json.dumps(validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8"))), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
