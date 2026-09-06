"""Fail-closed validator for the audited REC-EV-028A2 amendment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from build_rec_ev_019b_features import sha256_file
from validate_rec_ev_028a1_amendment import validate as validate_a1
from validate_rec_ev_028a_contract import ROOT, validate as validate_base


DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-028a2-global-label-firewall-amendment.json"
AUDITED_BYTES = 7_406
AUDITED_SHA256 = "312fde5d04f2232c957cc92c1352a8aa7dea3aa58ed976fce9b576d609c0b37f"


def _verify_pin(spec: dict[str, Any]) -> Path:
    path = ROOT / spec["path"]
    if path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != spec["sha256"]:
        raise RuntimeError(f"pinned artifact drift: {spec['path']}")
    return path


def validate(amendment: dict[str, Any], path: Path = DEFAULT, *, verify_files: bool = True) -> None:
    if path.stat().st_size != AUDITED_BYTES or sha256_file(path) != AUDITED_SHA256:
        raise RuntimeError("REC-EV-028A2 audited amendment pin drift")
    if amendment["contract_id"] != "rec-ev-028a2-global-label-firewall-amendment-v1":
        raise RuntimeError("unexpected REC-EV-028A2 amendment")
    if amendment["status"] != "PROPOSED_FOR_INDEPENDENT_EXACT_AUDIT":
        raise RuntimeError("audited amendment status drift")
    if amendment["output_root"] != "outputs/recommendation-evidence/rec-ev-028a2":
        raise RuntimeError("REC-EV-028A2 output root drift")
    if amendment["global_target_aware_profile_selection"]["required_assertion"].find("EMPTY_INTERSECTION") < 0:
        raise RuntimeError("global target/profile firewall drift")
    if amendment["override_scope"]["models_metrics_inference_truth_thresholds_roles_firewalls"] != "UNCHANGED":
        raise RuntimeError("REC-EV-028A2 scientific scope drift")
    if verify_files:
        base_path = _verify_pin(amendment["base_contract"])
        a1_path = _verify_pin(amendment["a1_amendment"])
        base = json.loads(base_path.read_text(encoding="utf-8"))
        a1 = json.loads(a1_path.read_text(encoding="utf-8"))
        validate_base(base, base_path)
        validate_a1(a1, a1_path)
        _verify_pin(amendment["a1_balanced_membership"]["summary"])
        _verify_pin(amendment["a1_balanced_membership"]["membership"])
        _verify_pin(amendment["triggering_prelabel_implementation_audit"]["failed_runner"])


def main() -> int:
    amendment = json.loads(DEFAULT.read_text(encoding="utf-8"))
    validate(amendment)
    print(f"REC_EV_028A2_AMENDMENT_PASS {AUDITED_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
