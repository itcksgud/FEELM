#!/usr/bin/env python3
"""Fail-closed structural validator for REC-EV-023A."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023a-content-vector-development-screen.json"
EXPECTED_CANONICAL_CONTRACT_SHA256 = "7be729cd2deb457053cb33834675fd85386e903984c22d49a1c6d4e5784fef6d"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    canonical = (json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    require(hashlib.sha256(canonical).hexdigest() == EXPECTED_CANONICAL_CONTRACT_SHA256, "canonical contract hash drift")
    require(contract.get("contract_id") == "rec-ev-023a-content-vector-development-screen-v1", "contract identity drift")
    require(contract.get("status") == "APPROVED_FOR_ADAPTIVE_STAGE2_DEVELOPMENT_SCREEN", "contract status drift")
    require(contract["independent_design_audit"]["final_verdict"] == "023A_DESIGN_PASS", "design audit missing")
    auth = contract["authorization"]
    require(auth == {
        "stage2_reuse": True,
        "locked_test_access": False,
        "final_reserve_access": False,
        "model_fit_or_retune": False,
        "champion_selection": False,
        "product_policy_change": False,
    }, "authorization drift")
    require(contract["adaptive_reuse"] == {
        "same_stage2_users_and_judged20_seen_in_rec_ev_022b": True,
        "selection_source": "REC_EV_022B_SIX_CELLS",
        "selection_adjusted_in_current_family": False,
        "interval_label": "ADAPTIVE_STAGE2_DESCRIPTIVE_MAX_T_INTERVAL",
        "forbidden_terms": ["PASS", "CONFIRMED", "VALIDATED", "95_PERCENT_FAMILYWISE_EVIDENCE"],
    }, "adaptive reuse boundary drift")
    require(contract["implementation_artifacts"] == [
        "scripts/rec_ev_022a_core.py",
        "scripts/run_rec_ev_022a_stage1.py",
        "scripts/run_rec_ev_023a_content_screen.py",
        "scripts/validate_rec_ev_023a_contract.py",
    ], "implementation pin set drift")
    require(contract["cells"] == [
        {"encoding": "BINARY_SIGN", "k": 6},
        {"encoding": "PERCENTILE_MAGNITUDE", "k": 6},
        {"encoding": "BINARY_SIGN", "k": 8},
        {"encoding": "PERCENTILE_MAGNITUDE", "k": 8},
        {"encoding": "BINARY_SIGN", "k": 14},
        {"encoding": "PERCENTILE_MAGNITUDE", "k": 14},
    ], "selected cells drift")
    require(contract["heads"] == [
        "B0", "ITEMKNN", "STRUCTURED", "E5",
        "AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY", "AVAILABLE_HEAD_HYBRID_POLICY",
    ], "head set drift")
    expected_allowed_paths = {
        "rec_ev_022b_protocol_lock": "outputs/recommendation-evidence/rec-ev-022b/protocol-lock.json",
        "rec_ev_022b_selection": "outputs/recommendation-evidence/rec-ev-022b/stage2-selection.json",
        "stage2_cohort": "outputs/recommendation-evidence/rec-ev-022b/cache/stage2-common30.parquet",
        "train_model": "outputs/recommendation-evidence/rec-ev-022a/cache/train-model.npz",
        "train_z": "outputs/recommendation-evidence/rec-ev-022a/cache/train-z.npz",
        "structured_full": "outputs/recommendation-evidence/rec-ev-022a/cache/structured-full.npz",
        "text_embeddings": "outputs/recommendation-evidence/rec-ev-019b/text-embeddings.parquet",
        "rec_ev_019c_trial_registry": "outputs/recommendation-evidence/rec-ev-019c/trial-registry.json",
        "rec_ev_019c_validation_selection": "outputs/recommendation-evidence/rec-ev-019c/validation-selection.json",
        "rec_ev_019c_validation_manifest": "docs/recommendation/evidence/manifests/rec-ev-019c-validation.json",
    }
    actual_allowed_paths = {name: value["path"] for name, value in contract["allowed_input_artifacts"].items()}
    require(actual_allowed_paths == expected_allowed_paths, "allowed input path set drift")
    expected_forbidden = [
        "outputs/recommendation-evidence/global-time-v1/test.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/locked-test-binary-prefixes.parquet",
        "outputs/recommendation-evidence/rec-ev-019a/locked-test-evaluation-windows.parquet",
        "outputs/recommendation-evidence/rec-ev-022a/stage1-user-metrics.parquet",
        "outputs/recommendation-evidence/rec-ev-022b/stage2-user-metrics.parquet",
        "outputs/recommendation-evidence/rec-ev-022b/stage2-result.json",
    ]
    require(contract["forbidden_input_artifacts"] == expected_forbidden, "forbidden input path set drift")
    allowed_resolved = {(ROOT / path).resolve() for path in actual_allowed_paths.values()}
    forbidden_resolved = {(ROOT / path).resolve() for path in expected_forbidden}
    require(allowed_resolved.isdisjoint(forbidden_resolved), "allowed/forbidden input intersection")
    require(contract["fixed_semantics"] == {
        "candidate_set": "SAME_FIXED_JUDGED20_PER_STAGE2_USER",
        "profile": "SAME_HASH_ORDERED_FIRST30_WITH_FIRST_K_PREFIX",
        "rating_encodings": "BYTE_IDENTICAL_REC_EV_022A_TAU5",
        "b0": "REC_EV_022A_TRAIN_ONLY_BAYESIAN_SCORE_PRIOR100",
        "itemknn": "REC_EV_022A_TRAIN_ONLY_SIGNED_Z_COSINE_SHRINK50_MIN_SUPPORT2_POSITIVE_SIMILARITY_ONLY",
        "structured": "REC_EV_022A_FOUR_GROUP_EQUAL_WEIGHT_L2_COSINE_NONNEGATIVE",
        "e5": "PINNED_384D_L2_NORMALIZED_EMBEDDING_RAW_COSINE_WITH_NEGATIVE_VALUES_PRESERVED",
        "profile_score": "SIMILARITY_MATRIX_TIMES_FULL_FIRST_K_WEIGHTS_DIVIDED_BY_SUM_ABSOLUTE_WEIGHTS_FLOAT64",
        "active_head": "WEIGHT_DENOMINATOR_GT_ZERO_AND_ALL_PERSONAL_SCORES_FINITE_AND_AT_LEAST_TWO_UNIQUE_PERSONAL_SCORES",
        "inactive_standalone": "HEAD_WIDE_B0_FALLBACK",
        "tie_break": ["PERSONAL_DESC", "B0_DESC", "MOVIE_ID_ASC"],
        "b0_tie_break": ["B0_DESC", "MOVIE_ID_ASC"],
    }, "fixed scoring semantics drift")
    require(contract["e5_primary_invariants"] == {
        "target_eligible_exposures": 182140,
        "target_total_exposures": 182140,
        "profile_eligible_exposures_by_k": {"6": 54642, "8": 72856, "14": 127498},
        "profile_total_exposures_by_k": {"6": 54642, "8": 72856, "14": 127498},
        "usable_weight_zero_users_each_cell": 0,
        "on_any_mismatch": "SOURCE_DRIFT_BLOCKED",
        "item_level_mixed_score_fallback": False,
    }, "E5 primary invariant drift")
    require(contract["rrf"] == {
        "c": 10,
        "formula": "SUM_ACTIVE_HEAD_1_OVER_10_PLUS_ONE_BASED_RANK",
        "rank_dtype": "INT32_ONE_TO_TWENTY_EXACT_PERMUTATION",
        "accumulation_dtype": "FLOAT64",
        "content_policy_heads": ["STRUCTURED", "E5"],
        "hybrid_policy_heads": ["ITEMKNN", "STRUCTURED", "E5"],
        "inactive_head_contribution": "NONE_NEVER_REUSE_B0_FALLBACK_RANK",
        "empty_active_set": "HEAD_WIDE_B0_FALLBACK",
        "tie_break": ["RRF_DESC", "B0_DESC", "MOVIE_ID_ASC"],
        "interpretation": "AVAILABLE_HEAD_POLICY_NOT_PURE_FUSION_OR_CAUSAL_ADDITION",
        "provenance": "ONLY_C10_REUSED_FROM_REC_EV_019C_B9_INTERNAL_PER_MODEL_SELECTION_T003_NOT_OVERALL_SINGLE_BEST_OR_CHAMPION",
        "retune_on_stage2": False,
    }, "RRF semantics drift")
    require(contract["metrics"] == {
        "primary_n": 2,
        "utility": "PAIR1_MEAN_Q_HIGHER_BETTER",
        "worst_loss": "PAIR1_WORST_Q_LOSS_LOWER_BETTER",
        "secondary_descriptive_only": ["PAIRWISE_CONCORDANCE", "FALLBACK_RATE", "ACTIVE_HEAD_COMPOSITION"],
    }, "metric semantics drift")
    require(contract["statistics"] == {
        "unit": "USER",
        "user_order": "USER_KEY_ASC",
        "contrasts": {
            "challenger_minus_b0": "6_CELLS_X_5_CHALLENGERS_X_2_ENDPOINTS",
            "e5_minus_structured": "6_CELLS_X_2_ENDPOINTS",
            "content_policy_minus_e5": "6_CELLS_X_2_ENDPOINTS",
            "content_policy_minus_structured": "6_CELLS_X_2_ENDPOINTS",
            "hybrid_policy_minus_itemknn": "6_CELLS_X_2_ENDPOINTS",
        },
        "expected_contrasts": 108,
        "bootstrap_repeats": 10000,
        "seed": 20260924,
        "numpy_version": "1.26.4",
        "bit_generator": "PCG64",
        "shared_resample_vector": True,
        "sample_sd_ddof": 1,
        "zero_se": "ZERO_HALF_WIDTH",
        "interval_label": "ADAPTIVE_STAGE2_DESCRIPTIVE_MAX_T_INTERVAL",
    }, "statistical semantics drift")
    require(contract["decision"] == {
        "q_definition": "Q(A,B,CELL)=UTILITY_LOW_GTE_0_005_AND_WORST_LOSS_HIGH_LTE_0_010",
        "utility_margin": 0.005,
        "worst_loss_margin": 0.01,
        "propositions_reported_separately": [
            "PURE_CONTENT_SIGNAL", "E5_INCREMENTAL_SIGNAL",
            "AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY_SIGNAL", "AVAILABLE_HEAD_HYBRID_POLICY_INCREMENT",
        ],
        "pure_content_signal": "ANY_OF_Q_STRUCTURED_B0_Q_E5_B0_Q_CONTENT_POLICY_B0",
        "e5_incremental_signal": "Q_E5_B0_AND_Q_E5_STRUCTURED",
        "available_head_content_ensemble_policy_signal": "Q_CONTENT_POLICY_B0_AND_Q_CONTENT_POLICY_E5_AND_Q_CONTENT_POLICY_STRUCTURED",
        "available_head_hybrid_policy_increment": "Q_HYBRID_POLICY_B0_AND_Q_HYBRID_POLICY_ITEMKNN",
        "forward_itemknn": "Q_ITEMKNN_B0",
        "forward_structured": "Q_STRUCTURED_B0",
        "forward_e5": "E5_INCREMENTAL_SIGNAL",
        "forward_content_policy": "AVAILABLE_HEAD_CONTENT_ENSEMBLE_POLICY_SIGNAL",
        "forward_hybrid_policy": "AVAILABLE_HEAD_HYBRID_POLICY_INCREMENT",
        "forward_set": "ALL_HEAD_CELL_PAIRS_PASSING_THEIR_EXACT_RULE_NO_POINT_ESTIMATE_RANKING_NO_CAP",
        "content_forward_set": "FORWARD_SET_EXCLUDING_ITEMKNN",
        "status_signal": "DEVELOPMENT_SCREEN_SIGNAL_IFF_CONTENT_FORWARD_SET_NONEMPTY",
        "status_no_signal": "DEVELOPMENT_SCREEN_NO_SIGNAL_IFF_CONTENT_FORWARD_SET_EMPTY",
        "itemknn_only_does_not_change_status": True,
        "pure_content_alone_does_not_imply_forwarding": True,
    }, "decision truth table drift")
    require(contract["claim_boundary"] == {
        "allowed": "CURRENT_METADATA_PREDICTIVE_SIGNAL_FOR_RANKING_ALREADY_RATED_JUDGED20_WITHIN_ADAPTIVELY_REUSED_MOVIELENS_STAGE2",
        "forbidden": [
            "FRESH_CONFIRMATION", "FULL_CATALOG_RETRIEVAL", "CHRONOLOGICAL_ONBOARDING",
            "KOREAN_MOVIE_TRANSFER", "RECENT_MOVIE_TRANSFER", "COLD_ITEM_TRANSFER",
            "SERVICE_USER_PERFORMANCE", "SAFETY", "CHAMPION", "PRODUCT_POLICY",
        ],
    }, "claim boundary drift")
    require(contract["output_root"] == "outputs/recommendation-evidence/rec-ev-023a", "output root drift")
    require(contract["outputs"] == {
        "protocol_lock": "protocol-lock.json",
        "source_manifest": "source-manifest.json",
        "progress": "run-progress.json",
        "e5_aligned": "cache/e5-aligned.npy",
        "e5_available": "cache/e5-available.npy",
        "prepared_integrity": "cache/prepared-integrity.json",
        "metric_parts": "cache/metric-parts",
        "user_metrics": "user-metrics.parquet",
        "user_metrics_integrity": "user-metrics.integrity.json",
        "selection": "development-screen-selection.json",
        "result": "development-screen-result.json",
    }, "output mapping drift")
    resume = contract["resume"]
    require(resume["required"] is True and resume["drift"] == "FAIL_CLOSED", "resume boundary drift")
    require(all(resume[key] is True for key in (
        "source_manifest_sha_verified", "implementation_sha_locked", "prepared_sha_rows_and_run_signature_verified",
        "direct_score_revalidates_prepared", "each_metric_part_sha_rows_user_slice_and_run_signature_verified",
        "expected_metric_part_path_set_exact", "partial_combined_artifact_fails_closed",
        "combined_user_metrics_sha_rows_and_run_signature_verified",
    )), "resume integrity drift")
    require(contract["invariants"] == {
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "model_fitted_or_retuned": False,
        "champion": None,
        "product_policy_updated": False,
    }, "execution invariant drift")
    for name, artifact in contract["allowed_input_artifacts"].items():
        require(int(artifact["bytes"]) > 0 and len(str(artifact["sha256"])) == 64, f"input pin missing: {name}")
    return {
        "status": "PASS_REC_EV_023A_CONTRACT",
        "cells": 6,
        "heads": 6,
        "expected_contrasts": 108,
        "adaptive_stage2": True,
        "locked_test_access": False,
        "champion": None,
    }


def main() -> int:
    print(json.dumps(validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8"))), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
