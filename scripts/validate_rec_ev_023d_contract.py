#!/usr/bin/env python3
"""Fail-closed structural validator for REC-EV-023D."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023d-feature-only-lightfm-attribution.json"
LOCKED_KEYS = (
    "purpose", "authorization", "implementation_artifacts", "fixed_reuse", "train_reader",
    "feature_support", "lightfm", "target_fold_in", "heads", "head_semantics", "scoring",
    "metrics", "statistics", "decision", "carry_forward_equivalence", "resume",
    "claim_boundary", "invariants",
)
EXPECTED_LOCKED_SPEC_SHA256 = "a384791a5e31dcede93aa800fee04d5d94341e08cafa9a01abbc8edaccf1445f"
EXPECTED_ALLOWED_INPUTS_SHA256 = "4270e828e4671d365c88a99d692b7d4dce06d543452308676b76430865305884"
EXPECTED_FORBIDDEN_INPUTS_SHA256 = "8fc108d05e0958b20e79ad5213ca850c13b7daa4d2e2b8e5c28d17d0cfdcb700"
EXPECTED_OUTPUTS_SHA256 = "f9d32c7cd87d9f1fee7f93d9442af2d77bae45666e02d8ff19a45696cfe37a2a"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate(contract: dict[str, Any], *, root: Path = ROOT, verify_files: bool = True) -> None:
    require(contract["contract_id"] == "rec-ev-023d-feature-only-lightfm-attribution-v1", "contract id drift")
    require(contract["status"] == "APPROVED_FOR_ADAPTIVE_STAGE1_FEATURE_ONLY_LIGHTFM_ATTRIBUTION", "status drift")
    audit = contract["independent_design_audit"]
    require(audit["rounds"] == 2 and audit["first_verdict"] == "023D_DESIGN_REVISE_REQUIRED", "audit revision missing")
    require(audit["final_verdict"] == "023D_DESIGN_PASS", "design audit did not pass")

    authorization = contract["authorization"]
    require(authorization["adaptive_stage1_reuse"] and authorization["train_warm_lightfm_fit"], "fit authorization missing")
    for key in ("locked_test_access", "stage2_access", "final_reserve_access", "champion_selection", "product_policy_change"):
        require(authorization[key] is False, f"forbidden authorization: {key}")
    require(contract["implementation_artifacts"] == [
        "scripts/rec_ev_022a_core.py",
        "scripts/run_rec_ev_023b_masked_cold_screen.py",
        "scripts/run_rec_ev_023c_crossed_sensitivity.py",
        "scripts/train_rec_ev_023d_lightfm.py",
        "scripts/run_rec_ev_023d_lightfm_attribution.py",
        "scripts/validate_rec_ev_023d_contract.py",
    ], "implementation artifact family drift")

    fixed = contract["fixed_reuse"]
    require((fixed["universe_items"], fixed["warm_items"], fixed["masked_cold_items"]) == (41439, 33078, 8361), "item universe drift")
    require((fixed["users"], fixed["profile_n"], fixed["target_n"]) == (9520, 14, 20), "cohort drift")
    require(fixed["target_selection_is_rating_blind"] and fixed["q_eval_is_metric_only_after_all_rank_seals"], "label firewall drift")
    expected_cells = [
        {"encoding": "BINARY_SIGN", "k": 6},
        {"encoding": "PERCENTILE_MAGNITUDE", "k": 6},
        {"encoding": "BINARY_SIGN", "k": 8},
        {"encoding": "PERCENTILE_MAGNITUDE", "k": 8},
        {"encoding": "BINARY_SIGN", "k": 14},
        {"encoding": "PERCENTILE_MAGNITUDE", "k": 14},
    ]
    require(fixed["cells"] == expected_cells, "cell set or order drift")

    reader = contract["train_reader"]
    require(reader["read_order"] == "USER_ROLE_THEN_MOVIE_ID_THEN_WARM_MEMBERSHIP_THEN_RATING", "TRAIN read order drift")
    require(reader["label"] == "ENCODING_WEIGHTS_BINARY_SIGN_OVER_ALL_WARM_RATINGS_PER_TRAIN_USER_WITH_REC_EV_023B_G0_MID_TAU_5", "TRAIN label drift")
    require(reader["coo_order"] == "USER_KEY_ASC_THEN_MOVIE_ID_ASC", "COO order drift")
    require(reader["masked_cold_train_rating_parsed"] == 0 and reader["timestamp_parsed"] == 0, "TRAIN firewall drift")
    require(reader["zero"] == "OMIT" and reader["unrated_or_neutral_as_negative"] is False, "negative semantics drift")
    require(reader["interaction_values"] == [-1, 1] and reader["sample_weight"] == 1.0, "signed COO drift")
    require(reader["objective_weighting"] == "INTERACTION_EQUAL" and reader["duplicates"] == "FAIL", "training objective ambiguity")

    features = contract["feature_support"]
    require(features["identity_features"] is False and features["expected_columns"] is None, "feature-only boundary drift")
    for key in ("all_universe_rows_positive_norm", "all_masked_cold_rows_positive_norm", "retained_column_has_final_interaction_support", "mask_and_matrix_identical_across_seeds", "sealed_before_label_open"):
        require(features[key] is True, f"feature support guard missing: {key}")

    model = contract["lightfm"]
    require((model["loss"], model["dimension"], model["learning_schedule"]) == ("logistic", 128, "adagrad"), "base LightFM freeze drift")
    require((model["learning_rate"], model["item_alpha"], model["user_alpha"], model["epochs"], model["threads"]) == (0.05, 0.000001, 0.000001, 10, 1), "base hyperparameter drift")
    require(model["seeds"] == [17, 42, 73, 101, 211] and model["primary_seed"] == 17 and model["seed_selection"] is False, "seed policy drift")
    require(model["base_hyperparameters_frozen_from_019c_t003_only"] and model["reuse_019c_cache"] is False, "T003 reuse boundary drift")
    dependency = model["dependency"]
    require((dependency["distribution"], dependency["version"], dependency["platform"]) == ("lightfm-next", "1.19.0", "linux/amd64"), "dependency drift")

    fold = contract["target_fold_in"]
    require(fold["reuse_019c_fold_in"] is False and fold["user_bias"] is False and fold["one_class_allowed"] is True, "023D fold-in identity drift")
    require((fold["steps"], fold["learning_rate"], fold["regularization"], fold["dtype"]) == (80, 0.05, 0.000001, "FLOAT64"), "fold-in optimizer drift")
    require(fold["stable_sigmoid"] == "BRANCH_STABLE_EXP_NO_CLIP", "stable sigmoid not fixed")
    require(fold["lipschitz_guard"] == "0.05_TIMES_1E_MINUS_6_PLUS_WEIGHTED_MEAN_FACTOR_NORM_SQUARED_DIV_4_LTE_1", "Lipschitz guard drift")
    require(fold["zero_nonzero_weights"] == "ANALYTIC_RANDOM_BEFORE_CONFIDENCE_MEAN", "zero weight guard drift")
    require(fold["item_representation_frozen_sha_before_after"] and fold["claim"] == "FIXED_80_STEP_ITERATE_NOT_OPTIMUM", "fold-in claim drift")

    expected_heads = ["RANDOM_EXPECTATION", "STRUCTURED_ORIGINAL", "STRUCTURED_MATCHED", "BIAS_ONLY", "EMBEDDED_DIRECT", "LIGHTFM_FULL", "RRF_ORIGINAL_LIGHTFM"]
    require(contract["heads"] == expected_heads, "attribution heads drift")
    semantics = contract["head_semantics"]
    require(set(semantics) == set(expected_heads) - {"RANDOM_EXPECTATION"}, "head semantics incomplete")
    require("NO_BIAS_NO_FOLD_IN" in semantics["EMBEDDED_DIRECT"], "embedded attribution not isolated")
    require("RANK_ONLY" in semantics["RRF_ORIGINAL_LIGHTFM"], "RRF raw score fusion allowed")
    scoring = contract["scoring"]
    require(scoring["head_tie_payload"] == "UTF8(rec-ev-023d-score-tie-v1|SEED|HEAD|ENCODING|K|USER_KEY|MOVIE_ID); STRUCTURED_ORIGINAL_EXACTLY_REUSES_023B_ORDER", "head tie drift")
    require(scoring["rrf_tie_payload"] == "UTF8(rec-ev-023d-score-tie-v1|SEED|RRF_FINAL|ENCODING|K|USER_KEY|MOVIE_ID)", "RRF tie drift")
    require(scoring["embedded_direct_negative_cosine"] == "PRESERVED" and scoring["rrf_c"] == 10, "scoring drift")

    expected_comparisons = [
        ["STRUCTURED_ORIGINAL", "RANDOM_EXPECTATION"],
        ["LIGHTFM_FULL", "RANDOM_EXPECTATION"],
        ["RRF_ORIGINAL_LIGHTFM", "RANDOM_EXPECTATION"],
        ["LIGHTFM_FULL", "STRUCTURED_ORIGINAL"],
        ["RRF_ORIGINAL_LIGHTFM", "LIGHTFM_FULL"],
        ["RRF_ORIGINAL_LIGHTFM", "STRUCTURED_ORIGINAL"],
        ["STRUCTURED_MATCHED", "RANDOM_EXPECTATION"],
        ["STRUCTURED_ORIGINAL", "STRUCTURED_MATCHED"],
        ["BIAS_ONLY", "RANDOM_EXPECTATION"],
        ["LIGHTFM_FULL", "BIAS_ONLY"],
        ["EMBEDDED_DIRECT", "RANDOM_EXPECTATION"],
        ["EMBEDDED_DIRECT", "STRUCTURED_MATCHED"],
        ["LIGHTFM_FULL", "STRUCTURED_MATCHED"],
    ]
    stats = contract["statistics"]
    require(stats["comparisons_per_cell"] == expected_comparisons, "comparison family drift")
    require((stats["contrasts_per_cell"], stats["expected_contrasts"], stats["valid_replicates"]) == (26, 156, 2000), "family size drift")
    require(stats["regimes"] == ["USER_ONLY", "ITEM_ONLY", "TWO_WAY"] and stats["max_t_family"] == "ALL_156_CONTRASTS_PER_REGIME", "max-T family drift")
    require(stats["critical_and_intervals_recomputed"] and stats["seed_inference"] == "PRIMARY_SEED_17_ONLY", "inference drift")
    require(stats["seed_descriptive"] == "FOR_EACH_OF_156_CONTRASTS_MEAN_SD_DDOF1_MIN_MAX_RANGE_OVER_FIVE_FIXED_SEED_POINT_ESTIMATES_DESCRIPTIVE_ONLY", "five-seed descriptive drift")
    require(stats["training_and_seed_uncertainty_in_interval"] is False, "uncertainty boundary drift")

    decision = contract["decision"]
    require((decision["utility_margin"], decision["worst_loss_margin"]) == (0.005, 0.01), "decision margin drift")
    require((decision["q_utility_operator"], decision["q_loss_operator"], decision["stability_utility_operator"], decision["stability_loss_operator"]) == ("GTE", "LTE", "GTE", "LT"), "decision operators drift")
    require(decision["point_ranking_or_cap"] is False and decision["retune_after_result"] is False, "decision procedure drift")
    require(decision["embedding_mechanism_signal"] == "Q_EMBEDDED_MATCHED_AND_Q_EMBEDDED_RANDOM_AND_Q_FULL_BIAS_AND_ALL_FIVE_POINT_MARGIN_FOR_SAME_THREE", "embedding truth table drift")
    require(decision["lightfm_full_forward"] == "Q_FULL_RANDOM_AND_Q_FULL_ORIGINAL_AND_Q_FULL_MATCHED_AND_Q_FULL_BIAS_AND_ALL_FIVE_POINT_MARGIN_FOR_SAME_FOUR", "LightFM truth table drift")
    require(decision["rrf_forward"] == "Q_RRF_RANDOM_AND_Q_RRF_FULL_AND_Q_RRF_ORIGINAL_AND_ALL_FIVE_POINT_MARGIN_FOR_SAME_THREE", "RRF truth table drift")
    require(decision["status_rule"] == "END_TO_END_INCREMENTAL_SIGNAL_IF_ANY_FULL_OR_RRF_FORWARD_ELSE_EMBEDDING_MECHANISM_ONLY_SIGNAL_IF_ANY_MECHANISM_ELSE_NO_INCREMENTAL_SIGNAL", "status truth table drift")
    require(decision["champion"] is None, "champion selected")
    carry = contract["carry_forward_equivalence"]
    require(carry["required_equal_to_rec_ev_023c"] == ["POINT", "TWO_WAY_REPLICATE_VECTOR", "STANDARD_ERROR"], "carry-forward equality drift")
    require(carry["not_required_equal_due_to_larger_family"] == ["CRITICAL", "INTERVAL", "Q"], "family enlargement not acknowledged")

    resume = contract["resume"]
    require(resume["required"] and resume["drift"] == "FAIL_CLOSED" and resume["all_five_rank_sets_sealed_before_label_open"], "resume/label seal drift")
    invariants = contract["invariants"]
    require(invariants == {"locked_test_opened": False, "stage2_opened": False, "final_reserve_opened": False, "champion": None, "product_policy_updated": False}, "invariants drift")
    forbidden_claims = set(contract["claim_boundary"]["forbidden"])
    require({"FRESH", "STRICT_COLD_START", "FUTURE_RELEASE", "KOREAN_USER", "CHAMPION", "PRODUCT_POLICY"} <= forbidden_claims, "claim boundary incomplete")

    outputs = contract["outputs"]
    require(len(outputs) == len(set(outputs.values())), "duplicate output path")
    require(contract["output_root"] == "outputs/recommendation-evidence/rec-ev-023d", "output root drift")
    forbidden_inputs = contract["forbidden_input_artifacts"]
    require(any("locked-test" in path for path in forbidden_inputs) and any("B8_LIGHTFM-T003-S17" in path for path in forbidden_inputs), "forbidden inputs incomplete")
    output_root = (root / contract["output_root"]).resolve()
    for relative in outputs.values():
        require((output_root / relative).resolve().is_relative_to(output_root), "output escapes REC-EV-023D root")
    require(canonical_digest({key: contract[key] for key in LOCKED_KEYS}) == EXPECTED_LOCKED_SPEC_SHA256, "approved locked spec digest drift")
    require(canonical_digest(contract["allowed_input_artifacts"]) == EXPECTED_ALLOWED_INPUTS_SHA256, "allowed input family drift")
    require(canonical_digest(contract["forbidden_input_artifacts"]) == EXPECTED_FORBIDDEN_INPUTS_SHA256, "forbidden input family drift")
    require(canonical_digest(contract["outputs"]) == EXPECTED_OUTPUTS_SHA256, "output mapping drift")

    if verify_files:
        for name, artifact in contract["allowed_input_artifacts"].items():
            path = Path(artifact["path"])
            if not path.is_absolute():
                path = root / path
            require(path.is_file(), f"missing allowed input: {name}")
            require(path.stat().st_size == int(artifact["bytes"]), f"allowed input byte drift: {name}")
            require(sha256_file(path) == artifact["sha256"], f"allowed input SHA drift: {name}")


def main() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    validate(contract)
    print("REC-EV-023D contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
