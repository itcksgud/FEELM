"""Fail-closed validator for the REC-EV-028B label-analysis continuation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-028b-label-analysis.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_spec(spec: Mapping[str, Any], label: str) -> Path:
    raw = Path(str(spec.get("path", "")))
    path = raw if raw.is_absolute() else ROOT / raw
    require(path.is_file(), f"{label} missing: {path}")
    require(path.stat().st_size == int(spec.get("bytes", -1)), f"{label} byte drift")
    require(sha256_file(path) == str(spec.get("sha256", "")), f"{label} hash drift")
    return path.resolve()


def validate(value: Mapping[str, Any], path: Path = DEFAULT) -> None:
    require(path.resolve() == DEFAULT.resolve(), "contract path drift")
    require(value.get("schema_version") == 1, "schema drift")
    require(value.get("contract_id") == "rec-ev-028b-label-analysis-v1", "contract id drift")
    require(value.get("status") == "READY_FOR_INDEPENDENT_EXACT_AUDIT", "status drift")
    authorization = value.get("authorization", {})
    require(authorization == {
        "attribution_evaluation_target_rating_access": True,
        "full_allowed_attribution_user_history_rating_access": True,
        "timestamp_access": False,
        "future_model_evaluation_reserve_access": False,
        "locked_test_access": False,
        "final_reserve_access": False,
        "product_policy_change": False,
        "model_or_hyperparameter_selection": False,
    }, "authorization drift")
    for name in ("base_contract", "a2_amendment"):
        verify_spec(value[name], name)
    execution = value.get("prelabel_execution", {})
    require(execution.get("root") == "outputs/recommendation-evidence/rec-ev-028a2/attribution", "prelabel root drift")
    require(execution.get("independent_audit_truth") == "REC_EV_028A2_ACTUAL_PRELABEL_EXECUTION_PASS", "audit truth drift")
    require(execution.get("independent_audit_thread") == "01a0751b-6621-7a61-94b2-4c401c7a3066", "audit thread drift")
    for name in ("protocol_lock", "global_score_seal", "membership", "membership_preflight"):
        verify_spec(execution[name], f"prelabel {name}")
    verify_spec(value["movielens_archive"], "MovieLens archive")
    implementations = value.get("implementations", [])
    require(
        [spec.get("path") for spec in implementations]
        == [
            "scripts/run_rec_ev_028b_label_analysis.py",
            "scripts/rec_ev_028b_core.py",
            "scripts/validate_rec_ev_028b_contract.py",
            "scripts/rec_ev_027_core.py",
            "scripts/rec_ev_022a_core.py",
        ],
        "implementation set/order drift",
    )
    for spec in implementations:
        verify_spec(spec, f"implementation {spec.get('path')}")
    label = value.get("label_open", {})
    require(label.get("reader") == "ONE_GLOBAL_SEQUENTIAL_SCAN_AFTER_RECURSIVE_PRELABEL_SEAL_VERIFICATION", "label reader drift")
    require(label.get("allowed_users") == "ONLY_THE_3470_UNION_USERS_PRESENT_IN_A2_MEMBERSHIP", "label user scope drift")
    require(label.get("nonmember_row_policy") == "PARSE_USER_ID_ONLY_THEN_DISCARD_RAW_ROW", "nonmember firewall drift")
    require(label.get("q_formula") == "FULL_HISTORY_MIDRANK_(N_LT_PLUS_0_5_N_EQ)_DIV_N", "Q formula drift")
    require(label.get("rating_bins") == [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0], "rating bins drift")
    require(label.get("timestamps_parsed") == 0 and label.get("future_membership_opened") is False, "label firewall drift")
    metrics = value.get("metrics", {})
    require(metrics.get("output_n") == 2, "output_n drift")
    require(metrics.get("primary_cell") == "PERCENTILE_MAGNITUDE_K8", "primary cell drift")
    require(metrics.get("user") == ["HARM20", "TOP2_MEAN_Q", "TOP2_MIN_Q"], "user metrics drift")
    require(metrics.get("item") == ["ITEM_MACRO_UTILITY_CONTRIBUTION", "ITEM_MACRO_LOW_SLOT_CONTRIBUTION"], "item metrics drift")
    require(metrics.get("sensitivity_cells") == ["BINARY_SIGN_K8", "PERCENTILE_MAGNITUDE_K4", "PERCENTILE_MAGNITUDE_K12"], "sensitivity cells drift")
    inference = value.get("inference", {})
    require(inference == {
        "confidence": 0.95,
        "repeats": 5000,
        "rng": "NUMPY_1_26_4_PCG64_EXPONENTIAL_MEAN_ONE",
        "user_seed": 20261004,
        "item_multiway_seed": 20261005,
        "user_batch_size": 100,
        "item_batch_size": 50,
        "estimands": ["R0", "R1", "R2", "R3", "R4", "KR", "RECENT", "RANDOM_POOLED"],
        "contrasts": [
            "FULL_PERSONALIZED_VS_RANDOM_EXPECTATION",
            "BIAS_ONLY_VS_RANDOM_EXPECTATION",
            "PROFILE_SHUFFLE_VS_RANDOM_EXPECTATION",
            "STRUCTURED_DIRECT_VS_RANDOM_EXPECTATION",
            "FULL_PERSONALIZED_VS_BIAS_ONLY",
            "FULL_PERSONALIZED_VS_PROFILE_SHUFFLE",
            "FULL_PERSONALIZED_VS_STRUCTURED_DIRECT",
        ],
        "user_family_size": 168,
        "item_family_size": 112,
        "user_random_pooled": "AVERAGE_EACH_USERS_AVAILABLE_R0_TO_R4_CONTRASTS_THEN_EQUAL_USER_MEAN",
        "item_random_pooled": "CONCATENATE_SINGLE_FOLD_MOVIE_OCCURRENCES_THEN_EQUAL_MOVIE_MEAN",
        "shared_user_weights": True,
        "shared_movie_weights": True,
        "critical_quantile_method": "HIGHER",
        "item_draw_order_per_repeat": "ALL_SORTED_UNION_USER_EXPONENTIALS_THEN_ALL_SORTED_UNION_MOVIE_EXPONENTIALS",
    }, "inference drift")
    require(value.get("truth_table") == "USE_EXACT_BASE_REC_EV_028_TRUTH_TABLE_WITHOUT_RELAXATION", "truth table drift")
    require(value.get("resume_required") is True, "resume requirement drift")
    require(value.get("future_transition") == "NEVER_OPEN_IN_THIS_RUN_REQUIRES_NEW_EXACT_AUDITED_CONTRACT_EVEN_IF_STAGE_PASS", "future transition drift")
    require(value.get("output_root") == "outputs/recommendation-evidence/rec-ev-028b", "output root drift")


def main() -> None:
    value = json.loads(DEFAULT.read_text(encoding="utf-8"))
    validate(value, DEFAULT)
    print("REC_EV_028B_LABEL_ANALYSIS_CONTRACT_PASS")


if __name__ == "__main__":
    main()
