"""Validate the REC-EV-030 movie-ID-only membership execution lock."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-030a-membership-execution.json"


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
    require(contract_path.resolve() == DEFAULT.resolve(), "only frozen default contract is executable")
    require(contract.get("schema_version") == 1, "schema drift")
    require(contract.get("contract_id") == "rec-ev-030a-membership-execution-v1", "contract id drift")
    require(contract.get("status") == "INDEPENDENT_IMPLEMENTATION_AUDIT_REQUIRED", "status drift")
    required = {"design_contract", "design_audit", "parent_contract", "movielens_archive", "implementation", "validator", "tests"}
    require(set(contract["inputs"]) == required, "input pins drift")
    paths = {name: verify(spec) for name, spec in contract["inputs"].items()}
    design = json.loads(paths["design_contract"].read_text(encoding="utf-8"))
    audit = json.loads(paths["design_audit"].read_text(encoding="utf-8"))
    require(audit.get("status") == "REC_EV_030_DESIGN_PASS", "design audit did not pass")
    require(design["population"]["eligibility"] == "PARENT_OLD_BUCKET_0_TO_59_AND_PARENT_MODEL_TRAIN_ROLE_0_TO_5999_AND_REC_EV_028_PHASE_9000_TO_9999", "future population drift")
    require(design["membership"]["movie_id_only_preflight"] is True, "movie ID preflight drift")
    require(design["membership"]["profile_n"] == 30, "profile size drift")
    require(design["membership"]["target_n"] == {"R0": 20, "R1": 20, "R2": 20, "R3": 20, "R4": 20, "KR": 4, "RECENT": 4}, "target counts drift")
    require(contract["firewall"] == {
        "rating_values_parsed": False,
        "timestamps_parsed": False,
        "rec_ev_028_attribution_or_fit_membership_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }, "firewall drift")
    require(contract["implementation_audit"].get("status") in {"PENDING", "REC_EV_030_MEMBERSHIP_IMPLEMENTATION_AUDIT_PASS"}, "implementation audit drift")
    require(contract.get("output_root") == design.get("output_root") == "outputs/recommendation-evidence/rec-ev-030", "output root drift")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args()
    value = json.loads(args.contract.read_text(encoding="utf-8"))
    validate(value, args.contract)
    print("REC_EV_030_MEMBERSHIP_CONTRACT_VALID")


if __name__ == "__main__":
    main()
