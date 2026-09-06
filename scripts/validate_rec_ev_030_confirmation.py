"""Validate the exact REC-EV-030 confirmation execution lock."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-030b-confirmation-execution.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(spec: Mapping[str, Any]) -> Path:
    raw = Path(str(spec["path"]))
    path = raw if raw.is_absolute() else ROOT / raw
    if not path.is_file() or path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != str(spec["sha256"]):
        raise ValueError(f"artifact drift: {path}")
    return path.resolve()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(contract: Mapping[str, Any], contract_path: Path = DEFAULT) -> None:
    require(contract_path.resolve() == DEFAULT.resolve(), "only frozen default confirmation contract is executable")
    require(contract.get("schema_version") == 1, "schema drift")
    require(contract.get("contract_id") == "rec-ev-030b-confirmation-execution-v1", "contract id drift")
    require(contract.get("status") == "INDEPENDENT_EXACT_IMPLEMENTATION_AUDIT_REQUIRED", "status drift")
    required = {"design_contract", "membership_execution_contract", "membership_preflight", "membership", "membership_audit", "parent_contract", "movielens_archive", "calibration_prior", "implementation", "validator", "tests"}
    require(set(contract["inputs"]) == required, "input pins drift")
    paths = {name: verify(spec) for name, spec in contract["inputs"].items()}
    design = json.loads(paths["design_contract"].read_text(encoding="utf-8"))
    membership_preflight = json.loads(paths["membership_preflight"].read_text(encoding="utf-8"))
    membership_audit = json.loads(paths["membership_audit"].read_text(encoding="utf-8"))
    require(design.get("status") == "INDEPENDENT_EXACT_DESIGN_AUDIT_REQUIRED", "design status drift")
    require(membership_preflight.get("status") == "REC_EV_030_MEMBERSHIP_PASS", "membership did not pass")
    require(membership_audit.get("status") == "REC_EV_030_MEMBERSHIP_RESULT_AUDIT_PASS", "membership audit did not pass")
    require(int(membership_preflight.get("rows", -1)) == 13910 and int(membership_preflight.get("users", -1)) == 3398, "membership constants drift")
    require(design["frozen_policy"] == {
        "family": "STRUCTURED_DIRECT",
        "encoding": "PERCENTILE_MAGNITUDE",
        "k": 30,
        "output_n": 2,
        "score": "NORMALIZED_TMDB_STRUCTURED_TARGET_BY_PROFILE_COSINE_TIMES_USER_PROFILE_PERCENTILE_WEIGHT_DIV_SUM_ABS_WEIGHT",
        "global_item_bias": False,
        "comparison": ["OWN_PROFILE", "CYCLIC_SHUFFLED_PROFILE", "ANALYTIC_RANDOM"],
    }, "frozen policy drift")
    primary = design["primary_inference"]
    require(primary["estimand"] == "RANDOM_POOLED_ONLY", "primary estimand drift")
    require(primary["contrasts"] == ["OWN_VS_RANDOM", "OWN_VS_SHUFFLE"], "primary contrasts drift")
    require(primary["user_family_size"] == 6 and primary["item_family_size"] == 4, "family size drift")
    require(primary["repeats"] == 5000 and primary["user_seed"] == 20261008 and primary["item_seed"] == 20261009, "bootstrap drift")
    require(contract["execution"] == {
        "membership_rows": 13910,
        "union_users": 3398,
        "profile_slots": 417300,
        "target_slots": 268136,
        "profile_then_score_then_label": True,
        "resume": "CURRENT_UPSTREAM_IDENTITY_REQUIRED_AND_PARTIAL_STATES_FAIL_CLOSED",
    }, "execution constants drift")
    require(contract["firewall"] == {
        "implementation_audit_required_before_profile_ratings": True,
        "target_ratings_before_global_score_seal": False,
        "timestamps_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }, "firewall drift")
    audit = contract["implementation_audit"]
    require(audit.get("status") in {"PENDING", "REC_EV_030_CONFIRMATION_IMPLEMENTATION_AUDIT_PASS"}, "implementation audit drift")
    if audit.get("status") == "REC_EV_030_CONFIRMATION_IMPLEMENTATION_AUDIT_PASS":
        require("auditor_thread_id" in audit and "evidence" in audit, "implementation audit evidence missing")
    require(contract.get("output_root") == design.get("output_root") == "outputs/recommendation-evidence/rec-ev-030", "output root drift")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    validate(contract, args.contract)
    print("REC_EV_030_CONFIRMATION_CONTRACT_VALID")


if __name__ == "__main__":
    main()
