#!/usr/bin/env python3
"""Validate the exact REC-EV-025A/B common-support preflight contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-025ab-feature-transfer-design.json"
EXPECTED_CANONICAL_SHA256 = "a8081673ec61f37873fa26a2c68f77591a9209f6710ad39a9b513db922564b3b"


def validate_contract(contract: Mapping[str, Any]) -> None:
    canonical = (json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != EXPECTED_CANONICAL_SHA256:
        raise ValueError("canonical contract drift")
    if contract["contract_id"] != "rec-ev-025ab-feature-transfer-design-v1":
        raise ValueError("contract identity drift")
    if contract["independent_design_audit"]["verdict"] != "NEXT_FEATURE_TRANSFER_DESIGN_PASS":
        raise ValueError("design audit drift")
    if contract["execution_statistics"]["joint_family_each_experiment"] != 216:
        raise ValueError("family drift")
    if contract["heads"]["reporting_order"] != ["GENRE_ONLY", "TRANSFER_NO_CONTEXT", "E5", "CURRENT_FULL"]:
        raise ValueError("head set/order drift")
    if contract["common_design"]["profile_control_global_disjoint"] is not True:
        raise ValueError("profile/control boundary drift")
    if contract["authorization"]["rating_or_timestamp_access"] is not False:
        raise ValueError("preflight outcome access widened")
    expected_root = (ROOT / "outputs/recommendation-evidence/rec-ev-025ab-preflight").resolve()
    if (ROOT / contract["output_root"]).resolve() != expected_root:
        raise ValueError("output root drift")
    for relative in contract["outputs"].values():
        if not (expected_root / str(relative)).resolve().is_relative_to(expected_root):
            raise ValueError("output escapes root")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default=str(DEFAULT))
    args = parser.parse_args()
    path = Path(args.contract)
    path = path if path.is_absolute() else (ROOT / path).resolve()
    validate_contract(json.loads(path.read_text(encoding="utf-8")))
    print("REC_EV_025AB_CONTRACT_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
