"""Fail-closed validator for the REC-EV-029A pre-label execution contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-029a-prelabel-execution.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(spec: Mapping[str, Any]) -> Path:
    raw = Path(str(spec["path"]))
    path = raw if raw.is_absolute() else ROOT / raw
    if (
        not path.is_file()
        or path.stat().st_size != int(spec["bytes"])
        or sha256_file(path) != str(spec["sha256"])
    ):
        raise ValueError(f"artifact drift: {path}")
    return path.resolve()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(contract: Mapping[str, Any], contract_path: Path = DEFAULT) -> None:
    require(contract_path.resolve() == DEFAULT.resolve(), "only the frozen default contract is executable")
    require(contract.get("schema_version") == 1, "schema version drift")
    require(contract.get("contract_id") == "rec-ev-029a-prelabel-execution-v1", "contract id drift")
    require(contract.get("status") == "INDEPENDENT_EXACT_IMPLEMENTATION_AUDIT_REQUIRED", "status drift")
    inputs = contract["inputs"]
    required_inputs = {
        "design_contract",
        "membership_implementation",
        "membership_preflight",
        "membership",
        "membership_independent_audit",
        "parent_contract",
        "movielens_archive",
        "structured_features",
        "e5_embeddings",
        "core_implementation",
        "runner_implementation",
        "validator_implementation",
        "core_tests",
        "membership_tests",
    }
    require(set(inputs) == required_inputs, "input pin set drift")
    paths = {name: verify(spec) for name, spec in inputs.items()}
    design = json.loads(paths["design_contract"].read_text(encoding="utf-8"))
    audit = json.loads(paths["membership_independent_audit"].read_text(encoding="utf-8"))
    preflight = json.loads(paths["membership_preflight"].read_text(encoding="utf-8"))
    require(audit.get("status") == "REC_EV_029_MEMBERSHIP_AUDIT_PASS", "membership audit did not pass")
    require(preflight.get("status") == "REC_EV_029_MEMBERSHIP_PASS", "membership preflight did not pass")
    require(int(preflight.get("rows", -1)) == 21_904, "membership row count drift")
    require(preflight.get("rating_values_parsed") is False, "membership rating firewall drift")
    require(preflight.get("timestamps_parsed") is False, "membership timestamp firewall drift")
    require(design["candidate_grid"]["even_k"] == list(range(2, 31, 2)), "K grid drift")
    require(design["candidate_grid"]["encodings"] == ["PERCENTILE_MAGNITUDE", "BINARY_SIGN"], "encoding grid drift")
    require(design["candidate_grid"]["candidate_count"] == 90, "candidate count drift")
    protocol = contract["protocol"]
    require(protocol == {
        "split_salt": "rec-ev-029-direct-user-split-v1",
        "calibration_rating_scope": "ALL_MOVIELENS_RATINGS_OF_USERS_PASSING_PARENT_OLD_0_59_PARENT_ROLE_0_5999_REC_EV_028_PHASE_0_7999_AND_NEW_SPLIT_0_7999",
        "profile_rating_scope": "EXACT_FROZEN_PROFILE30_SLOTS_FOR_SELECTION_AND_REPLICATION_MEMBERSHIP_ROWS",
        "target_rating_values_before_global_score_seal": "ZERO",
        "timestamp_values": "ZERO",
        "persisted_score_sufficient_statistic": "ACTIVE_FLAG_AND_DETERMINISTIC_TOP2_TARGET_INDICES_FOR_EACH_OF_90_OWN_POLICIES_AND_ITS_PAIRED_SHUFFLE",
        "rrf_internal_rank_scope": "FULL_TARGET_LIST_WITH_C_10_BEFORE_PERSISTING_TOP2",
        "resume": "ATOMIC_PER_COHORT_OUTER_BUNDLE_PLUS_INTEGRITY_AND_FAIL_CLOSED_ON_PARTIAL_OR_DRIFT",
    }, "pre-label protocol drift")
    require(contract.get("candidate_rows") == {
        "own_policy_count": 90,
        "paired_shuffle_count": 90,
        "cohort_outer_cells": 14,
        "membership_rows": 21904,
    }, "candidate row declaration drift")
    gates = contract["firewall"]
    require(gates == {
        "membership_audit_pass_required": True,
        "exact_implementation_audit_pass_required_before_prepare": True,
        "selection_target_labels_opened": False,
        "replication_target_labels_opened": False,
        "rec_ev_028_attribution_or_future_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }, "firewall drift")
    approval = contract["implementation_audit"]
    require(approval.get("status") in {"PENDING", "REC_EV_029_PRELABEL_IMPLEMENTATION_AUDIT_PASS"}, "implementation audit status drift")
    if approval.get("status") == "REC_EV_029_PRELABEL_IMPLEMENTATION_AUDIT_PASS":
        require("auditor_thread_id" in approval and "evidence" in approval, "audit evidence missing")
    require(contract.get("output_root") == "outputs/recommendation-evidence/rec-ev-029/prelabel", "output root drift")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args()
    value = json.loads(args.contract.read_text(encoding="utf-8"))
    validate(value, args.contract)
    print("REC_EV_029_PRELABEL_CONTRACT_VALID")


if __name__ == "__main__":
    main()
