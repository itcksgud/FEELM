#!/usr/bin/env python3
"""Fail-closed validator for the locked REC-EV-025A/B execution contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-025ab-feature-transfer-execution.json"
EXPECTED_CANONICAL_SHA256 = "b7e3250c795a1683e58a108086bc2fc29917e64ffe3394acac672834ddb703f5"


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_contract(contract: Mapping[str, Any]) -> None:
    require(hashlib.sha256(canonical_json_bytes(contract)).hexdigest() == EXPECTED_CANONICAL_SHA256, "canonical contract drift")
    require(contract.get("contract_id") == "rec-ev-025ab-feature-transfer-execution-v1", "contract identity drift")
    require(contract.get("status") == "PROPOSED_FOR_INDEPENDENT_EXECUTION_AUDIT", "contract status drift")
    require(contract.get("design_audit", {}).get("verdict") == "NEXT_FEATURE_TRANSFER_DESIGN_PASS", "design audit drift")
    require(contract.get("preflight_audits") == ["REC_EV_025AB_PREFLIGHT_IMPLEMENTATION_PASS", "REC_EV_025AB_PREFLIGHT_RESULT_PASS"], "preflight audit drift")
    auth = contract.get("authorization", {})
    require(auth.get("adaptive_development") is True and auth.get("stage1_stage2_evaluation") is True, "development authorization drift")
    for key in ("timestamp_access", "old_locked_outcome_access", "final_reserve_access", "champion_selection", "product_policy_change"):
        require(auth.get(key) is False, f"forbidden authorization drift: {key}")
    required_impl = {
        "scripts/rec_ev_022a_core.py", "scripts/run_rec_ev_023ef_transfer.py",
        "scripts/run_rec_ev_023ef_preflight.py", "scripts/validate_rec_ev_023ef_contract.py",
        "scripts/run_rec_ev_025ab_preflight.py", "scripts/validate_rec_ev_025ab_contract.py",
        "scripts/run_rec_ev_025_feature_transfer.py", "scripts/validate_rec_ev_025_feature_transfer_contract.py",
        "scripts/tests/test_rec_ev_025_feature_transfer.py",
    }
    require(set(contract.get("implementation_artifacts", [])) == required_impl, "implementation closure drift")
    artifacts = contract.get("allowed_input_artifacts", {})
    require(set(artifacts) == {"movielens_archive", "structured_features", "text_embeddings", "korean_movie_id_projection", "stage2_selection", "train_prior", "joint_design", "preflight_lock", "preflight_manifest", "preflight_progress", "preflight_result", "preflight_integrity"}, "source set drift")
    forbidden = set(contract.get("forbidden_input_artifacts", []))
    require("outputs/recommendation-evidence/global-time-v1/test.parquet" in forbidden, "locked test not forbidden")
    require("outputs/recommendation-evidence/rec-ev-022a/final-reserve-input.parquet" in forbidden, "final reserve not forbidden")
    support = contract.get("common_support", {})
    require(support == {
        "structured": "FEATURE_ELIGIBLE_TRUE_AND_RELEASE_YEAR_NONNULL_AND_CURRENT_FULL_NONZERO",
        "e5_model_id": "intfloat/multilingual-e5-small",
        "e5_revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "e5_dimension": 384,
        "e5": "FEATURE_ELIGIBLE_TRUE_AND_REVISION_EXACT_AND_ALL_FINITE_AND_ABS_L2_MINUS_1_LTE_0_0001",
        "intersection": "STRUCTURED_INTERSECT_E5_BY_UNIQUE_MOVIE_ID",
        "expected_items": 68078,
    }, "common support drift")
    require(list(contract.get("experiments", {})) == ["REC-EV-025A", "REC-EV-025B"], "experiment order drift")
    require(contract["preflight"]["REC-EV-025A"]["expected_users"] == 319, "025A cohort drift")
    require(contract["preflight"]["REC-EV-025B"]["expected_users"] == 685, "025B cohort drift")
    require(all(contract["preflight"][e]["status"] == "FEASIBLE_PRELABEL" for e in contract["experiments"]), "preflight feasibility drift")
    expected_cells = [
        {"encoding": "BINARY_SIGN", "k": 6}, {"encoding": "PERCENTILE_MAGNITUDE", "k": 6},
        {"encoding": "BINARY_SIGN", "k": 8}, {"encoding": "PERCENTILE_MAGNITUDE", "k": 8},
        {"encoding": "BINARY_SIGN", "k": 14}, {"encoding": "PERCENTILE_MAGNITUDE", "k": 14},
    ]
    require(contract.get("cells") == expected_cells, "cell order drift")
    heads = contract.get("heads", {})
    require(heads.get("reporting_order") == ["GENRE_ONLY", "TRANSFER_NO_CONTEXT", "E5", "CURRENT_FULL"], "head order drift")
    require(heads.get("current_baseline") == "CURRENT_FULL", "current baseline drift")
    metrics = contract.get("metrics", {})
    require(metrics.get("absolute_classes") == ["TARGET_IMPROVEMENT", "CONTROL_IMPROVEMENT", "CONDITIONAL_GAP"], "absolute class order drift")
    require(metrics.get("absolute_endpoints") == ["UTILITY_IMPROVEMENT_MODEL_MINUS_RANDOM", "SAFETY_IMPROVEMENT_RANDOM_LOSS_MINUS_MODEL"], "absolute endpoint order drift")
    require(metrics.get("challengers") == ["GENRE_ONLY", "TRANSFER_NO_CONTEXT", "E5"], "challenger order drift")
    require(metrics.get("incremental_domains") == ["TARGET", "CONTROL"], "incremental domain order drift")
    require(metrics.get("incremental_endpoints") == ["UTILITY_CHALLENGER_MINUS_CURRENT", "SAFETY_CHALLENGER_MINUS_CURRENT"], "incremental endpoint order drift")
    statistics = contract.get("statistics", {})
    require(statistics.get("joint_family_each_experiment") == 216 and statistics.get("valid_replicates") == 4000 and statistics.get("attempts") == [0, 7999], "family/bootstrap drift")
    require("CONTRAST_INDEX_ZERO_BASED_CONTIGUOUS" in str(statistics.get("contrast_enumeration")), "contrast enumeration drift")
    decision = contract.get("decision", {})
    require(decision.get("maximum_half_width_strict") == 0.05, "precision threshold drift")
    require(decision.get("absolute_target_margin") == 0.02 and decision.get("absolute_gap_noninferiority") == -0.02, "absolute margin drift")
    require(decision.get("incremental_target_utility_margin") == 0.005 and decision.get("incremental_target_safety_margin") == 0.01 and decision.get("incremental_control_noninferiority") == -0.02, "incremental margin drift")
    require(decision.get("status_precedence") == ["INFEASIBLE_PRELABEL", "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE", "ROBUST_INCREMENTAL_TRANSFER_HEAD", "ROBUST_ABSOLUTE_TRANSFER_HEAD", "CELL_SPECIFIC_SIGNAL_NOT_ROBUST", "NO_ROBUST_TRANSFER_HEAD"], "status precedence drift")
    require(decision.get("result_driven_relaxation") is False and decision.get("champion") is None, "decision boundary drift")
    invariants = contract.get("invariants", {})
    require(invariants == {"evaluation_labels_opened_before_all_four_head_rank_seal": False, "old_locked_ratings_timestamps_metrics_opened": False, "final_reserve_opened": False, "product_policy_updated": False, "champion": None}, "invariant drift")
    require(contract.get("output_roots") == {"REC-EV-025A": "outputs/recommendation-evidence/rec-ev-025a", "REC-EV-025B": "outputs/recommendation-evidence/rec-ev-025b"}, "output root drift")


def main() -> int:
    contract = json.loads(DEFAULT.read_text(encoding="utf-8"))
    validate_contract(contract)
    print("REC_EV_025AB_EXECUTION_CONTRACT_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
