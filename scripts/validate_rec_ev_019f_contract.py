#!/usr/bin/env python3
"""Fail-closed validator for the REC-EV-019F preregistered contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019f-independent-temporal-routing.json"
REC_EV_019E = ROOT / "docs/recommendation/contracts/rec-ev-019e-no-retune-incremental-applicability-gate.json"
PREREGISTRATION = ROOT / "docs/recommendation/evidence/REC-EV-019F-independent-temporal-routing-preregistration.md"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(contract: dict[str, Any], *, root: Path = ROOT, check_files: bool = True) -> dict[str, Any]:
    require(contract.get("contract_id") == "REC-EV-019F-INDEPENDENT-TEMPORAL-ROUTING-V1", "contract identity drift")
    require(contract.get("status") == "APPROVED_FOR_PREREGISTERED_VALIDATION_ONLY", "contract status drift")
    require(contract.get("task_id") == "TASK-REC-EV-019F", "task identity drift")
    classification = contract["evidence_classification"]
    require(classification["independence_unit"] == "SOURCE_ROW_AND_TEMPORAL_WINDOW", "independence unit drift")
    require(classification["user_independent"] is False, "user independence must be explicitly false")
    require(classification["source_row_independent_from_rec_ev_019a"] is True, "source-row independence missing")
    require(classification["maximum_success_status"] == "PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION", "maximum status drift")
    require(classification["target_domain_confirmation_required"] is True, "target-domain confirmation boundary missing")
    require(classification["eligibility_counts_observed_before_preregistration"] is True, "observed eligibility disclosure missing")
    require(classification["does_not_establish"] == [
        "KOREAN_USER_PERFORMANCE", "KOREAN_MOVIE_PERFORMANCE", "RECENT_MOVIE_PERFORMANCE", "PRODUCT_POLICY_READINESS",
    ], "interpretation boundary drift")
    require(classification["observed_audit_expectations_are_not_blind"] == {
        "structural_users": 1021,
        "strict_users": 802,
        "existing_019a_k10_users": 629,
        "outside_existing_019a_k10_users": 173,
        "completely_new_to_019a_validation_users": 31,
    }, "observed audit expectation drift")

    authorization = contract["current_authorization"]
    for key in ("locked_test_access", "fit_or_refit", "candidate_threshold_or_route_search", "champion_selection", "product_policy_change"):
        require(authorization[key] is False, f"forbidden authorization enabled: {key}")
    require(authorization["validation_full_catalog_scoring"] is True, "Validation scoring authorization missing")
    require(contract["invariants"] == {
        "execution_role": "VALIDATION_019F_TEMPORAL",
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }, "fail-closed invariants drift")

    allowed = contract["allowed_input_artifacts"]
    require(set(allowed) == {
        "evaluation_protocol", "global_time_manifest", "validation_ratings", "historical_validation_prefixes", "historical_validation_windows",
        "candidate_core", "validation_selection", "lightfm_config", "lightfm_interactions", "lightfm_item_features", "lightfm_result",
    }, "input allowlist drift")
    allowed_paths = {str(entry["path"]) for entry in allowed.values()}
    forbidden_paths = set(map(str, contract["forbidden_input_artifacts"]))
    require(not allowed_paths.intersection(forbidden_paths), "allowlist intersects forbidden paths")
    require("outputs/recommendation-evidence/global-time-v1/test.parquet" in forbidden_paths, "Locked Test source is not forbidden")
    require("outputs/recommendation-evidence/rec-ev-019d/predictions.parquet" in forbidden_paths, "019D prediction reuse is not forbidden")
    require(all("locked-test" not in path.lower() and not path.endswith("test.parquet") for path in allowed_paths), "Locked Test artifact is allowlisted")
    firewall = contract["role_firewall"]
    require(firewall["validation_role_literal"] == "validation-019f-temporal", "role literal drift")
    require(firewall["validation_bucket_range"] == [50, 59], "Validation bucket drift")
    require(firewall["file_allowlist_enforced_before_open"] is True and firewall["reject_unknown_paths"] is True, "input firewall weakened")
    require(firewall["raw_user_id_forbidden_in_outputs"] is True, "raw user id output prohibition missing")

    episode = contract["episode"]
    require(episode["source"] == "VALIDATION_PARQUET_BUCKETS_50_THROUGH_59_ONLY", "episode source drift")
    require(episode["tail_start_position"] == "HISTORICAL_K10_TENTH_SOURCE_POSITION_PLUS_11", "tail boundary drift")
    require(episode["relative_utility_reset_at_tail"] is True, "tail utility reset missing")
    require((episode["binary_shrinkage_lambda"], episode["binary_relative_like_min"], episode["binary_relative_dislike_max"]) == (10.0, 0.15, -0.15), "binary thresholds drift")
    require((episode["future_positive_midrank_min"], episode["future_negative_midrank_max"]) == (0.65, 0.35), "future relevance thresholds drift")
    require(episode["future_window"] == "EXACTLY_10_ORIGINAL_OBSERVATIONS_IMMEDIATELY_AFTER_THE_NEW_TENTH_SELECTION", "future window drift")
    require(episode["historical_source_row_overlap_required"] == 0, "source-row overlap Gate drift")
    require(episode["user_overlap_allowed"] is True, "user overlap disclosure missing")

    cohort = contract["cohort"]
    require(cohort["expected_tuning_union_users_observed"] == 477, "tuning union expectation drift")
    require(cohort["expected_structural_users_observed"] == 1021, "structural expectation drift")
    require(cohort["expected_strict_users_observed"] == 802, "strict expectation drift")
    require("BEFORE_FRESH_FUTURE_LABELS" in cohort["tuning_exclusion"], "future-label exclusion order missing")

    model = contract["model"]
    require((model["trial_id"], model["seed"], model["candidate_count"]) == ("B8_LIGHTFM-T003", 17, 41625), "frozen model identity drift")
    require(model["fallback"] == "B0_MOVIELENS_BAYESIAN_RATING_T003", "fallback identity drift")
    require(model["fit_refit_threshold_and_search_forbidden"] is True, "fit/search prohibition missing")
    require(model["rec_ev_019d_prediction_reuse_allowed"] is False, "019D prediction reuse enabled")
    require(model["base_cache"]["exact_hash_required"] is True and model["base_cache"]["external_artifact_uri"] is None, "cache reproducibility disclosure drift")

    rec_ev_019e = json.loads((root / REC_EV_019E.relative_to(ROOT)).read_text(encoding="utf-8"))
    expected_semantics = {"comparator": rec_ev_019e["comparator"], "candidate": rec_ev_019e["candidate"]}
    actual_semantics = contract["frozen_routing_semantics"]
    require(canonical_bytes(actual_semantics) == canonical_bytes(expected_semantics), "routing semantics are not byte-equivalent to REC-EV-019E")
    routing_sha256 = hashlib.sha256(canonical_bytes(actual_semantics)).hexdigest()
    require(actual_semantics["candidate"]["parameters"] == [] and actual_semantics["candidate"]["thresholds"] == [], "routing gained parameters or thresholds")

    scoring = contract["scoring"]
    require(scoring["profiles"] == ["K5_FIRST_FIVE_FRESH_PREFIX_ROWS", "K10_FIRST_TEN_FRESH_PREFIX_ROWS"], "profile definitions drift")
    require(scoring["full_catalog_rescore_required_for_every_strict_user_and_both_profiles"] is True, "both-profile full rescore missing")
    require(scoring["common_seen_mask"] == "ALL_CANDIDATE_VALID_ITEMS_IN_FRESH_K10_PREFIX", "common K10 seen mask drift")
    require(scoring["tie_break"] == ["EFFECTIVE_SCORE_DESC", "MOVIE_ID_ASC"], "tie-break drift")
    require((scoring["top_candidates"], scoring["top_k"], scoring["positive_injection"]) == (500, 10, False), "ranking boundary drift")

    require(contract["bootstrap"] == {
        "unit": "USER", "method": "PERCENTILE", "iterations": 10000, "seed": 20260924,
        "ndcg_interval": "TWO_SIDED_95_PERCENT_2_5_AND_97_5_PERCENTILES",
        "harm_interval": "ONE_SIDED_95_PERCENT_UPPER_95TH_PERCENTILE",
    }, "bootstrap contract drift")
    decision = contract["decision_rule"]
    require(decision["priority_is_normative"] is True and decision["evaluated_on"] == "STRICT_COHORT_ONLY", "decision population/order drift")
    require(decision["thresholds"] == {
        "harm_at_2_delta_one_sided_95_upper_max": 0.005,
        "mean_delta_ndcg_at_10_min": 0.005,
        "ndcg_two_sided_95_lower_strictly_greater_than": 0.0,
    }, "decision threshold drift")
    require([row["status"] for row in decision["priority"]] == [
        "FAIL", "PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION", "INCONCLUSIVE",
    ], "decision priority/status drift")
    lock = contract["leakage_lock"]
    require(lock["path"] == "outputs/recommendation-evidence/rec-ev-019f/protocol-lock.json", "lock path drift")
    for attestation in ("RUNNER_SHA256", "VERIFIER_SHA256", "GIT_DIRTY_FALSE", "RANKING_METRICS_READ_FALSE", "ELIGIBILITY_COUNTS_OBSERVED_TRUE"):
        require(attestation in lock["required_attestations"], f"lock attestation missing: {attestation}")

    outputs = contract["outputs"]
    require(set(outputs) == {
        "source_manifest", "protocol_lock", "structural_cohort", "strict_cohort", "prefixes", "windows", "arm_definitions",
        "rankings", "user_arm_metrics", "paired_deltas", "strata", "result", "progress", "checkpoints",
    }, "output inventory drift")
    require(contract["output_root"] == "outputs/recommendation-evidence/rec-ev-019f", "output root drift")
    if check_files:
        require((root / PREREGISTRATION.relative_to(ROOT)).is_file(), "preregistration is absent")
        for name, expected in allowed.items():
            path = root / expected["path"]
            require(path.is_file(), f"allowlisted source is absent: {name}")
            require(path.stat().st_size == int(expected["bytes"]), f"allowlisted source size drift: {name}")
            require(sha256_file(path) == expected["sha256"], f"allowlisted source hash drift: {name}")
    return {
        "status": "PASS_REC_EV_019F_CONTRACT",
        "routing_semantics_sha256": routing_sha256,
        "independence_unit": "SOURCE_ROW_AND_TEMPORAL_WINDOW",
        "user_independent": False,
        "eligibility_counts_observed": True,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    contract_path = args.contract if args.contract.is_absolute() else ROOT / args.contract
    if contract_path.resolve() != DEFAULT_CONTRACT.resolve():
        raise RuntimeError("unexpected REC-EV-019F contract path")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    print(json.dumps(validate(contract), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
