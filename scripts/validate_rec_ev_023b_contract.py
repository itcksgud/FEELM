#!/usr/bin/env python3
"""Fail-closed structural validator for REC-EV-023B."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023b-masked-item-cold-content-screen.json"
EXPECTED_CANONICAL_CONTRACT_SHA256 = "62e7b99e9203c7985f9a74d7ceb4135fdad9465d5f2cdce157d3d1566d70a892"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    canonical = (json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    require(hashlib.sha256(canonical).hexdigest() == EXPECTED_CANONICAL_CONTRACT_SHA256, "canonical contract hash drift")
    require(contract.get("contract_id") == "rec-ev-023b-masked-item-cold-content-screen-v1", "contract identity drift")
    require(contract.get("status") == "APPROVED_FOR_ADAPTIVE_STAGE1_DEVELOPMENT_SCREEN", "status drift")
    require(contract["independent_design_audit"] == {
        "thread_id": "01a06eeb-df86-7252-ac77-afc8d6ec24f7",
        "rounds": 3,
        "final_verdict": "023B_DESIGN_PASS",
    }, "independent design audit drift")
    require(contract["authorization"] == {
        "stage1_reuse": True,
        "locked_test_access": False,
        "stage2_access": False,
        "final_reserve_access": False,
        "champion_selection": False,
        "product_policy_change": False,
    }, "authorization drift")
    require(contract["adaptive_reuse"] == {
        "stage1_users_and_split_feasibility_previously_seen": True,
        "observed_universe_items": 41439,
        "observed_warm_items": 33078,
        "observed_masked_cold_items": 8361,
        "observed_eligible_users": 9520,
        "interval_label": "ADAPTIVE_STAGE1_DESCRIPTIVE_MAX_T_INTERVAL",
        "forbidden_terms": ["FRESH", "PREREGISTERED", "CONFIRMED", "VALIDATED", "STRICT_COLD_START"],
    }, "adaptive boundary drift")
    require(contract["implementation_artifacts"] == [
        "scripts/rec_ev_022a_core.py",
        "scripts/run_rec_ev_023b_masked_cold_screen.py",
        "scripts/validate_rec_ev_023b_contract.py",
    ], "implementation pin set drift")
    expected_paths = {
        "movielens_archive": "C:/higher/projects/MM/data/raw/ml-32m.zip",
        "candidate_identity": "outputs/recommendation-evidence/rec-ev-019c/candidate-core-final.parquet",
        "structured_features": "outputs/recommendation-evidence/rec-ev-019b/structured-features.parquet",
        "text_embeddings": "outputs/recommendation-evidence/rec-ev-019b/text-embeddings.parquet",
        "rec_ev_022b_selection": "outputs/recommendation-evidence/rec-ev-022b/stage2-selection.json",
        "rec_ev_019c_trial_registry": "outputs/recommendation-evidence/rec-ev-019c/trial-registry.json",
        "rec_ev_019b_contract": "docs/recommendation/contracts/rec-ev-019b-artifacts.json",
    }
    actual_paths = {name: value["path"] for name, value in contract["allowed_input_artifacts"].items()}
    require(actual_paths == expected_paths, "allowed input path set drift")
    require(all(int(value["bytes"]) > 0 and len(str(value["sha256"])) == 64 for value in contract["allowed_input_artifacts"].values()), "input pin missing")
    allowed = {Path(path).resolve() if Path(path).is_absolute() else (ROOT / path).resolve() for path in actual_paths.values()}
    forbidden = {(ROOT / path).resolve() for path in contract["forbidden_input_artifacts"]}
    require(allowed.isdisjoint(forbidden), "allowed and forbidden artifacts overlap")
    require(not any("023b" in str(path).lower() for path in contract["allowed_input_artifacts"]), "self-derived artifact allowlisted")

    require(contract["roles_and_reader"] == {
        "old_base_allow": "old_user_bucket(user_id)<=59",
        "train": "old_base_allow AND user_role_bucket(user_id)<6000",
        "stage1": "old_base_allow AND 6000<=user_role_bucket(user_id)<8000",
        "excluded": "PARSE_USER_ID_ONLY_THEN_DISCARD_RAW_LINE",
        "train_sequence": "USER_ROLE_THEN_MOVIE_ID_THEN_WARM_MEMBERSHIP_THEN_RATING",
        "train_masked_cold_rating_parsed": 0,
        "timestamp_parsed_all_roles": 0,
        "candidate_projection_columns": ["movie_id"],
    }, "reader firewall drift")
    require(contract["item_split"] == {
        "name": "INTERACTION_MASKED_ITEM_DISJOINT_PSEUDO_COLD",
        "source_limitation": "019A_019C_CANDIDATE_CORE_ALREADY_REQUIRED_PRIOR_TRAIN_INTERACTION",
        "universe": "CANDIDATE_IDENTITY_INTERSECT_STRUCTURED_ELIGIBLE_POSITIVE_NORM_INTERSECT_E5_ELIGIBLE_FINITE_NORM",
        "salt": "rec-ev-023b-item-holdout-v1",
        "payload": "UTF8(SALT_PIPE_CANONICAL_DECIMAL_MOVIE_ID)",
        "bucket": "SHA256_FULL_DIGEST_BIG_ENDIAN_UNSIGNED_MOD_10000",
        "masked_cold_rule": "BUCKET_LT_2000",
        "expected_items": 41439,
        "expected_warm": 33078,
        "expected_masked_cold": 8361,
    }, "item split drift")
    require(contract["cohort"] == {
        "role": "STAGE1_SELECTION",
        "minimum_warm_eligible_ratings": 14,
        "minimum_masked_cold_eligible_ratings": 20,
        "expected_users": 9520,
        "profile_n": 14,
        "target_n": 20,
        "profile_order_salt": "rec-ev-023b-warm-profile-order-v1",
        "target_order_salt": "rec-ev-023b-cold-target-order-v1",
        "order_payload": "UTF8(SALT_PIPE_LOWERCASE_64HEX_USER_KEY_PIPE_CANONICAL_DECIMAL_MOVIE_ID)",
        "order": "SHA256_DIGEST_BYTES_ASC_THEN_MOVIE_ID_ASC_COLLISION_GUARD",
        "eligibility_and_order_forbidden_inputs": ["RATING", "Q_EVAL", "TIMESTAMP"],
    }, "cohort drift")
    expected_cells = [
        {"encoding": "BINARY_SIGN", "k": 6},
        {"encoding": "PERCENTILE_MAGNITUDE", "k": 6},
        {"encoding": "BINARY_SIGN", "k": 8},
        {"encoding": "PERCENTILE_MAGNITUDE", "k": 8},
        {"encoding": "BINARY_SIGN", "k": 14},
        {"encoding": "PERCENTILE_MAGNITUDE", "k": 14},
    ]
    require(contract["cells"] == expected_cells, "cell set or order drift")
    require(contract["heads"] == ["STRUCTURED", "E5", "AVAILABLE_HEAD_CONTENT_RRF"], "head set drift")
    require(contract["train_warm_prior"]["derived_seal_before_score"] is True, "derived prior seal missing")
    require(contract["train_warm_prior"]["population"] == "TRAIN_USERS_WITH_AT_LEAST_ONE_WARM_UNIVERSE_RATING", "prior population drift")
    require(contract["train_warm_prior"]["dtype"] == "FLOAT64" and contract["train_warm_prior"]["tau"] == 5.0, "prior numeric drift")
    require(contract["q_eval"] == {
        "name": "FULL_STAGE1_RAW_HISTORY_MIDRANK_Q_EVAL",
        "reference": "ALL_RAW_MOVIELENS_RATINGS_FOR_THE_STAGE1_USER_INCLUDING_OUTSIDE_UNIVERSE_WARM_COLD_PROFILE_TARGET_AND_UNSELECTED",
        "formula": "(N_FULL_LT_R_PLUS_0_5_N_FULL_EQ_R)_DIV_N_FULL",
        "use": "METRIC_LABEL_ONLY_AFTER_COMPLETE_SCORE_RANK_INTEGRITY",
        "forbidden_in_score_input_and_score_rank": True,
    }, "q label isolation drift")
    require(contract["features"]["structured_forbidden"] == ["COUNTRY", "IDF", "INTERACTION_FREQUENCY_SCALING"], "structured boundary drift")
    require(contract["features"]["structured_groups"] == ["GENRE", "ORIGINAL_LANGUAGE_DECADE_RUNTIME30", "DIRECTOR_TOP5_CAST", "KEYWORD"], "structured groups drift")
    require(contract["features"]["e5_revision"] == "614241f622f53c4eeff9890bdc4f31cfecc418b3", "E5 revision drift")
    require(contract["features"]["e5_dimension"] == 384, "E5 dimension drift")
    require(contract["scoring"]["inactive_primary"] == "ANALYTIC_RANDOM_EXPECTATION_METRICS_NO_ORDER", "inactive primary drift")
    require(contract["scoring"]["inactive_concordance"] is None, "inactive concordance drift")
    require(contract["scoring"]["standalone_and_rrf_component_rank_identical"] is True, "RRF rank reuse drift")
    require(contract["scoring"]["head_tie_prefix"] == "rec-ev-023b-score-tie-v1|HEAD|", "head tie salt drift")
    require(contract["scoring"]["rrf_final_tie_prefix"] == "rec-ev-023b-score-tie-v1|RRF_FINAL|", "RRF final tie salt drift")
    require(contract["metrics"]["primary_n"] == 2 and contract["metrics"]["random_is_metric_only"] is True, "metric boundary drift")
    require(contract["statistics"] == {
        "unit": "USER",
        "user_order": "USER_KEY_ASC",
        "comparisons_per_cell": [
            ["STRUCTURED", "RANDOM_EXPECTATION"],
            ["E5", "RANDOM_EXPECTATION"],
            ["AVAILABLE_HEAD_CONTENT_RRF", "RANDOM_EXPECTATION"],
            ["E5", "STRUCTURED"],
            ["AVAILABLE_HEAD_CONTENT_RRF", "E5"],
            ["AVAILABLE_HEAD_CONTENT_RRF", "STRUCTURED"],
        ],
        "expected_contrasts": 72,
        "bootstrap_repeats": 10000,
        "seed": 20260925,
        "numpy_version": "1.26.4",
        "bit_generator": "PCG64",
        "shared_resample_vector": True,
        "sample_sd_ddof": 1,
        "zero_se": "EXCLUDE_FROM_MAX_T_AND_SET_LOW_MEAN_HIGH_EQUAL",
        "interval_label": "ADAPTIVE_STAGE1_DESCRIPTIVE_MAX_T_INTERVAL",
    }, "statistics drift")
    require(contract["decision"] == {
        "q_definition": "Q(A,B,CELL)=UTILITY_LOW_GTE_0_005_AND_WORST_LOSS_HIGH_LTE_0_010",
        "utility_margin": 0.005,
        "worst_loss_margin": 0.01,
        "forward_structured": "Q_STRUCTURED_RANDOM",
        "forward_e5": "Q_E5_RANDOM_AND_Q_E5_STRUCTURED",
        "forward_rrf": "Q_RRF_RANDOM_AND_Q_RRF_E5_AND_Q_RRF_STRUCTURED",
        "forward_set": "ALL_HEAD_CELL_PAIRS_PASSING_EXACT_RULE_WITHOUT_POINT_RANKING_OR_CAP",
        "status_signal": "PSEUDO_COLD_DEVELOPMENT_SIGNAL_IFF_FORWARD_SET_NONEMPTY",
        "status_no_signal": "PSEUDO_COLD_DEVELOPMENT_NO_SIGNAL_IFF_FORWARD_SET_EMPTY",
        "retune_after_result": False,
        "champion": None,
    }, "decision drift")
    require(contract["korean_language_descriptive"]["forbidden_outputs"] == ["MODEL_HEAD", "RANK", "DELTA", "CI", "GATE", "FORWARD"], "Korean descriptive boundary drift")
    require(contract["claim_boundary"]["selection_bias"] == "CONDITIONAL_ON_OBSERVED_RATINGS_AND_MINIMUM_WARM_COLD_ACTIVITY", "selection-bias boundary drift")
    require("STRICT_COLD_START" in contract["claim_boundary"]["forbidden"], "strict-cold forbidden claim missing")
    require(contract["output_root"] == "outputs/recommendation-evidence/rec-ev-023b", "output root drift")
    require(set(contract["outputs"]) == {
        "protocol_lock", "source_manifest", "progress", "universe", "train_prior", "structured_full",
        "e5_aligned", "score_input", "score_prepared_integrity", "label_source", "label_source_integrity",
        "rank_parts", "score_rank", "score_rank_integrity", "evaluation_labels",
        "evaluation_labels_integrity", "user_metrics", "user_metrics_integrity", "selection", "result",
    }, "output mapping drift")
    require(contract["resume"]["required"] is True and contract["resume"]["drift"] == "FAIL_CLOSED", "resume drift")
    require(contract["invariants"] == {
        "locked_test_opened": False,
        "stage2_opened": False,
        "final_reserve_opened": False,
        "champion": None,
        "product_policy_updated": False,
    }, "execution invariant drift")
    return {
        "status": "PASS_REC_EV_023B_CONTRACT",
        "cells": 6,
        "heads": 3,
        "expected_users": 9520,
        "expected_contrasts": 72,
        "adaptive_stage1": True,
        "locked_test_access": False,
        "champion": None,
    }


def main() -> int:
    print(json.dumps(validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8"))), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
