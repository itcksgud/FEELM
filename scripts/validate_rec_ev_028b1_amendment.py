"""Validate the REC-EV-028B1 union-user count amendment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-028b1-union-count-amendment.json"


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
    require(path.is_file(), f"{label} missing")
    require(path.stat().st_size == int(spec.get("bytes", -1)), f"{label} byte drift")
    require(sha256_file(path) == str(spec.get("sha256", "")), f"{label} hash drift")
    return path.resolve()


def validate(value: Mapping[str, Any], path: Path = DEFAULT) -> None:
    require(path.resolve() == DEFAULT.resolve(), "amendment path drift")
    require(value.get("schema_version") == 1, "schema drift")
    require(value.get("contract_id") == "rec-ev-028b1-union-count-amendment-v1", "id drift")
    require(value.get("status") == "READY_FOR_INDEPENDENT_EXACT_AUDIT", "status drift")
    verify_spec(value["base_contract"], "base contract")
    trigger = value.get("trigger", {})
    require(trigger.get("declared_all_outer_union_users") == 3470, "declared count drift")
    require(trigger.get("actual_all_outer_union_users") == 3517, "actual count drift")
    require(trigger.get("cause") == "3470_IS_RANDOM_R0_TO_R4_UNION_ONLY;_KR_OR_RECENT_ADD_47_PROXY_ONLY_USERS", "cause drift")
    require(trigger.get("detected_before_metrics_or_outcome_computation") is True, "detection timing drift")
    require(trigger.get("outcome_values_inspected_before_amendment") is False, "outcome inspection drift")
    corrected = value.get("corrected_population", {})
    require(corrected == {
        "membership_rows": 14889,
        "all_outer_union_users": 3517,
        "random_pooled_union_users": 3470,
        "proxy_only_users": 47,
        "target_labels": 288036,
        "evaluation_user_rating_values_parsed": 977423,
    }, "corrected population drift")
    observed = value.get("observed_label_execution", {})
    for name in ("label_seal", "labels"):
        verify_spec(observed[name], name)
    require(observed.get("timestamps_parsed") == 0, "timestamp firewall drift")
    require(observed.get("future_movie_membership_opened") == 0, "future firewall drift")
    require(observed.get("locked_test_opened") is False and observed.get("final_reserve_opened") is False, "test firewall drift")
    lock_path = verify_spec(value["pre_metrics_lock"], "pre-metrics lock")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    require(lock.get("status") == "LOCKED_AFTER_LABEL_BEFORE_ANY_METRIC_OR_OUTCOME", "pre-metrics lock status drift")
    require(lock.get("label_seal") == observed["label_seal"], "pre-metrics label identity drift")
    require(lock.get("metrics_or_outcome_artifacts_present") == [], "pre-metrics absence drift")
    require(lock.get("outcome_values_inspected") is False, "pre-metrics outcome drift")
    required_absent = [
        "outputs/recommendation-evidence/rec-ev-028b/metrics/metrics-seal.json",
        *[
            f"outputs/recommendation-evidence/rec-ev-028b/outers/{outer}/{name}"
            for outer in ("R0", "R1", "R2", "R3", "R4", "KR", "RECENT")
            for name in ("user-metrics.parquet", "item-occurrences.parquet")
        ],
        "outputs/recommendation-evidence/rec-ev-028b/analysis/user-bootstrap-estimates.npz",
        "outputs/recommendation-evidence/rec-ev-028b/analysis/item-bootstrap-estimates.npz",
        "outputs/recommendation-evidence/rec-ev-028b/analysis/simultaneous-inference.json",
        "outputs/recommendation-evidence/rec-ev-028b/analysis/input-policy-sensitivity.json",
        "outputs/recommendation-evidence/rec-ev-028b/analysis/user-segments.parquet",
        "outputs/recommendation-evidence/rec-ev-028b/analysis/movie-segments.parquet",
        "outputs/recommendation-evidence/rec-ev-028b/analysis/final-analysis.json",
    ]
    require(lock.get("absent_metric_or_outcome_paths") == required_absent, "pre-metrics absent path set/order drift")
    for key in ("timestamps_opened", "future_movie_membership_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed"):
        require(lock.get(key) is False, f"pre-metrics firewall drift: {key}")
    require(value.get("decision") == {
        "membership_changed": False,
        "target_membership_or_order_changed": False,
        "ratings_rescanned": False,
        "model_scores_changed": False,
        "metrics_may_continue_only_after_this_amendment_passes_independent_audit": True,
        "interpretation": "CORRECT_NON_OUTCOME_POPULATION_COUNT_AND_RETAIN_ALL_FROZEN_A2_MEMBERSHIP_ROWS",
    }, "decision drift")
    implementations = value.get("implementations", [])
    require([spec.get("path") for spec in implementations] == [
        "scripts/run_rec_ev_028b1_continue.py",
        "scripts/validate_rec_ev_028b1_amendment.py",
        "scripts/run_rec_ev_028b_label_analysis.py",
        "scripts/rec_ev_028b_core.py",
    ], "implementation set/order drift")
    for spec in implementations:
        verify_spec(spec, f"implementation {spec.get('path')}")
    require(value.get("audit_approval_path") == "docs/recommendation/evidence/rec-ev-028b1-independent-audit.json", "audit approval path drift")
    require(value.get("output_root") == "outputs/recommendation-evidence/rec-ev-028b", "output root drift")


def main() -> None:
    value = json.loads(DEFAULT.read_text(encoding="utf-8"))
    validate(value, DEFAULT)
    print("REC_EV_028B1_UNION_COUNT_AMENDMENT_PASS")


if __name__ == "__main__":
    main()
