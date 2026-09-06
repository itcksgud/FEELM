"""Fail-closed validator for the audited REC-EV-028A1 amendment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from build_rec_ev_019b_features import sha256_file
from validate_rec_ev_028a_contract import DEFAULT as BASE_DEFAULT
from validate_rec_ev_028a_contract import ROOT, validate as validate_base


DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-028a1-balanced-target-amendment.json"
AUDITED_SHA256 = "5a42336d6bec5ed6f609b4209be2e1b1e31a1b29047876964619e6a4cf64fb2d"
AUDITED_BYTES = 5_033


def _verify_pin(spec: dict[str, Any]) -> None:
    path = ROOT / spec["path"]
    if path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != spec["sha256"]:
        raise RuntimeError(f"pinned artifact drift: {spec['path']}")


def validate(amendment: dict[str, Any], path: Path = DEFAULT, *, verify_files: bool = True) -> None:
    if path.stat().st_size != AUDITED_BYTES or sha256_file(path) != AUDITED_SHA256:
        raise RuntimeError("REC-EV-028A1 audited amendment pin drift")
    if amendment["contract_id"] != "rec-ev-028a1-balanced-target-amendment-v1":
        raise RuntimeError("unexpected REC-EV-028A1 amendment")
    if amendment["status"] != "PROPOSED_FOR_INDEPENDENT_EXACT_AUDIT":
        raise RuntimeError("audited amendment status drift")
    if amendment["override_scope"]["only"] != [
        "membership.target_selection",
        "coverage_preflight_requirement",
        "output_root",
    ]:
        raise RuntimeError("REC-EV-028A1 override scope drift")
    if amendment["balanced_target_selection"]["selection_salt"] != "rec-ev-028a1-balanced-target-v1":
        raise RuntimeError("balanced selection salt drift")
    if not amendment["coverage_preflight"]["must_complete_before_any_rating_value"]:
        raise RuntimeError("rating firewall drift")
    if verify_files:
        base = json.loads(BASE_DEFAULT.read_text(encoding="utf-8"))
        validate_base(base, BASE_DEFAULT)
        _verify_pin(amendment["base_contract"])
        trigger = amendment["triggering_rating_free_preflight"]
        _verify_pin(trigger["summary"])
        _verify_pin(trigger["membership"])


def main() -> int:
    amendment = json.loads(DEFAULT.read_text(encoding="utf-8"))
    validate(amendment)
    print(f"REC_EV_028A1_AMENDMENT_PASS {AUDITED_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
