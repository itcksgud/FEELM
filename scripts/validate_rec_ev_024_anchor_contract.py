#!/usr/bin/env python3
"""Validate exact REC-EV-024A/B execution contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "REC-EV-024A": ROOT / "docs/recommendation/contracts/rec-ev-024a-korean-anchor-policy.json",
    "REC-EV-024B": ROOT / "docs/recommendation/contracts/rec-ev-024b-recent-anchor-policy.json",
}
EXPECTED_CANONICAL_SHA256 = {
    "REC-EV-024A": "8c086ac31e7f4a256fa3f7f96c7dfc6aef46d6cbf552ef8377295af3db167fd7",
    "REC-EV-024B": "dc7b3ede2ed331036a54d4128db865828291aac0b24a67f5b09c03018c10cc09",
}


def validate_contract(contract: Mapping[str, Any]) -> None:
    evidence_id = str(contract.get("evidence_id", ""))
    if evidence_id not in EXPECTED_CANONICAL_SHA256:
        raise ValueError("unsupported evidence id")
    canonical = (json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != EXPECTED_CANONICAL_SHA256[evidence_id]:
        raise ValueError("canonical execution contract drift")
    if contract["status"] != "APPROVED_FOR_ADAPTIVE_ANCHOR_POLICY_EXECUTION":
        raise ValueError("execution status drift")
    expected_root = (ROOT / f"outputs/recommendation-evidence/rec-ev-024{evidence_id[-1].lower()}").resolve()
    if (ROOT / str(contract["output_root"])).resolve() != expected_root:
        raise ValueError("output root drift")
    for relative in contract["outputs"].values():
        if not ((expected_root / str(relative)).resolve()).is_relative_to(expected_root):
            raise ValueError("output path escapes experiment root")
    if contract["decision"]["champion"] is not None or contract["decision"]["result_driven_relaxation"] is not False:
        raise ValueError("decision boundary drift")
    if contract["authorization"]["final_reserve_access"] is not False or contract["authorization"]["product_policy_change"] is not False:
        raise ValueError("authorization widened")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()
    path = DEFAULTS.get(args.contract, Path(args.contract))
    path = path if path.is_absolute() else (ROOT / path).resolve()
    contract = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(contract)
    print(f"{contract['evidence_id'].replace('-', '_')}_CONTRACT_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
