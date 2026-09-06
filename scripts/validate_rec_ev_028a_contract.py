"""Fail-closed validator for the independently audited REC-EV-028A contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from build_rec_ev_019b_features import sha256_file


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-028-personalization-attribution.json"
AUDITED_SHA256 = "025ed71d3bdcfab16e4a501e255d90bfda098c2ad353833e0fb724ace195bbde"
AUDITED_BYTES = 18_646


def validate(contract: dict[str, Any], path: Path = DEFAULT, *, verify_files: bool = True) -> None:
    if path.stat().st_size != AUDITED_BYTES or sha256_file(path) != AUDITED_SHA256:
        raise RuntimeError("REC-EV-028A audited contract pin drift")
    if contract["contract_id"] != "rec-ev-028-personalization-attribution-v1":
        raise RuntimeError("unexpected REC-EV-028A contract")
    if contract["status"] != "PROPOSED_FOR_INDEPENDENT_EXACT_AUDIT":
        raise RuntimeError("audited contract status drift")
    if any(contract["authorization"][key] for key in (
        "locked_test_access", "final_reserve_access", "timestamp_access",
        "product_policy_change", "champion_selection",
    )):
        raise RuntimeError("forbidden REC-EV-028A authorization")
    primary = contract["input_policy"]["primary"]
    if primary != {"encoding": "PERCENTILE_MAGNITUDE", "k": 8, "output_n": 2}:
        raise RuntimeError("primary input policy drift")
    if contract["population"]["future_model_evaluation_reserve_inclusive"] != [9000, 9999]:
        raise RuntimeError("future reserve boundary drift")
    if "NO_MEMBERSHIP_MATERIALIZATION" not in contract["membership"]["future_model_evaluation_reserve"]:
        raise RuntimeError("future membership firewall drift")
    if len(contract["inference"]["contrasts"]) != 7:
        raise RuntimeError("contrast family drift")
    if verify_files:
        parent = contract["inputs"]["parent_contract"]
        parent_path = ROOT / parent["path"]
        if parent_path.stat().st_size != int(parent["bytes"]) or sha256_file(parent_path) != parent["sha256"]:
            raise RuntimeError("parent contract pin drift")


def main() -> int:
    contract = json.loads(DEFAULT.read_text(encoding="utf-8"))
    validate(contract)
    print(f"REC_EV_028A_CONTRACT_PASS {AUDITED_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
