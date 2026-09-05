"""Validate the exact REC-EV-026 execution contract and its pinned inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from validate_rec_ev_026_design import canonical_sha256, sha256_file, validate as validate_design
except ImportError:
    from scripts.validate_rec_ev_026_design import canonical_sha256, sha256_file, validate as validate_design


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-026-content-cf-alignment-execution.json"
EXPECTED_CANONICAL_SHA256 = "05b4e4e7a537f5f10a60b75a6ca5435fbd1eb5a96ef9bb736b28d0d26d15fc12"
EXPECTED_INPUTS = [
    "design", "preflight_lock", "preflight_manifest", "preflight_registry", "preflight_registry_integrity",
    "preflight_membership", "preflight_membership_integrity", "preflight_result", "preflight_progress",
    "preflight_integrity", "movielens_archive", "structured_features", "text_embeddings", "train_prior",
    "candidate_core", "factor_s17", "factor_s42", "factor_s73", "factor_s101", "factor_s211",
]


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(spec: dict[str, Any]) -> None:
    require(set(spec) >= {"path", "bytes", "sha256"}, "artifact spec incomplete")
    path = resolve(spec["path"])
    require(path.is_file(), f"missing artifact: {path}")
    require(path.stat().st_size == spec["bytes"], f"artifact byte drift: {path}")
    require(sha256_file(path) == spec["sha256"], f"artifact hash drift: {path}")


def validate(contract: dict[str, Any]) -> None:
    require(contract["contract_id"] == "rec-ev-026-content-cf-alignment-execution-v1", "contract id drift")
    require(contract["status"] == "PROPOSED_FOR_EXECUTION_AUDIT", "contract status drift")
    require(list(contract["allowed_input_artifacts"]) == EXPECTED_INPUTS, "input inventory/order drift")
    for spec in contract["allowed_input_artifacts"].values():
        verify(spec)
    design = load(resolve(contract["allowed_input_artifacts"]["design"]["path"]))
    validate_design(design)
    require(contract["audits"] == {"thread_id": "01a0704a-ff92-7851-904a-bf3970b3d905", "design": "REC_EV_026_DESIGN_PASS_EXACT_CONTRACT", "preflight_implementation": "REC_EV_026_PREFLIGHT_IMPLEMENTATION_PASS", "preflight_result": "REC_EV_026_PREFLIGHT_RESULT_PASS", "required_execution": "REC_EV_026_EXECUTION_PASS_EXACT_CONTRACT_AND_IMPLEMENTATION"}, "audit chain drift")
    require(contract["authorization"] == {"mapper_feature_and_frozen_teacher_read": True, "profile_rating_read_after_mapper_gate": True, "evaluation_rating_read_after_rank_seal": True, "timestamp_read": False, "locked_test_access": False, "final_reserve_access": False, "champion_selection": False, "product_policy_change": False}, "authorization drift")
    require(contract["forbidden_input_artifacts"] == ["outputs/recommendation-evidence/global-time-v1/test.parquet", "outputs/recommendation-evidence/rec-ev-019a/locked-test-binary-prefixes.parquet", "outputs/recommendation-evidence/rec-ev-019a/locked-test-evaluation-windows.parquet", "outputs/recommendation-evidence/rec-ev-022a/final-reserve-input.parquet"], "forbidden input drift")
    preflight = load(resolve(contract["allowed_input_artifacts"]["preflight_result"]["path"]))
    require(preflight["common_support"]["items"] == 68078, "preflight support drift")
    require((preflight["experiments"]["REC-EV-026A"]["eligible_users"], preflight["experiments"]["REC-EV-026A"]["unique_targets"], preflight["experiments"]["REC-EV-026A"]["unique_controls"]) == (181, 136, 1853), "A preflight drift")
    require((preflight["experiments"]["REC-EV-026B"]["eligible_users"], preflight["experiments"]["REC-EV-026B"]["unique_targets"], preflight["experiments"]["REC-EV-026B"]["unique_controls"]) == (445, 774, 2570), "B preflight drift")
    require(all(row["status"] == "FEASIBLE_PRELABEL" for row in preflight["experiments"].values()), "preflight infeasible")
    require(contract["phase_order"] == ["PROTOCOL_LOCK", "MAPPER_FIT_GATE", "PROFILE_RATING_OPEN", "ALL_HEAD_RANK_SEAL", "EVALUATION_LABEL_OPEN", "METRICS_BOOTSTRAP_RESULT_SEAL"], "phase order drift")
    require(contract["reader"] == {"maximum_user_id": 300000, "rating_parse": "PARSE_USER_ID_THEN_MOVIE_ID_THEN_ONLY_IF_ALLOWLISTED_PARSE_RATING_TO_THIRD_COMMA", "timestamp_parse": False, "raw_user_id_output": "FORBIDDEN"}, "reader drift")
    require(contract["mapper"] == {"experiments": ["REC-EV-026A", "REC-EV-026B"], "seeds": [17, 42, 73, 101, 211], "alpha_grid": [0.1, 1.0, 10.0, 100.0, 1000.0], "fit_before_profile_rating": True, "target_and_control_actual_factor_access": False, "gate": "FIVE_VALIDATION_MEAN_COSINES_STRICTLY_POSITIVE_AND_ALL_REFIT_COEFFICIENTS_FINITE_AND_ALL_SELECTED_TARGET_CONTROL_PREDICTIONS_FINITE_NONZERO_BEFORE_PROFILE_RATING"}, "mapper execution drift")
    ranking = contract["ranking"]
    require(ranking["heads"] == ["CURRENT_FULL", "E5", "E5_TO_BPR"], "head drift")
    require(ranking["cells"] == design["cells"] and (ranking["slate_n"], ranking["top_n"]) == (4, 2), "cell/slate drift")
    require(ranking["tie_payload"] == design["heads"]["tie_payload"] and ranking["inactive_metric"] == "EXACT_ANALYTIC_RANDOM_TOP2", "tie/fallback drift")
    require(contract["statistics"] == {"contrasts": 312, "metadata_sha256": "50105fbaff9a80bca249d2725ab469cfbd1da01f02b747902f9502c24b9d8775", "union_user_shared_keyed_poisson": True, "valid_replicates": 4000, "attempt_ids": [0, 7999], "max_t_nearest_rank": 0.975, "ddof": 1}, "statistics drift")
    require(contract["resume"] == {"post_lock_requires_resume": True, "partial_or_drift": "FAIL_CLOSED_NO_OVERWRITE", "phase_artifact_hash_required": True, "post_label_change_requires_new_evidence_id": True}, "resume drift")
    require(contract["result_boundary"] == {"allowed": "ADAPTIVE_MECHANISM_SCREEN_WITH_USER_MOVIE_PAIR_NONREUSE_SINCE_REC_EV_019A_ONLY", "locked_test_opened": False, "final_reserve_opened": False, "champion": None, "product_policy_updated": False}, "result boundary drift")
    require(canonical_sha256(contract) == EXPECTED_CANONICAL_SHA256, "execution contract canonical hash drift")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args()
    require(args.contract.resolve() == DEFAULT.resolve(), "only default execution contract accepted")
    validate(load(args.contract))
    print("REC_EV_026_EXECUTION_CONTRACT_VALID")


if __name__ == "__main__":
    main()
