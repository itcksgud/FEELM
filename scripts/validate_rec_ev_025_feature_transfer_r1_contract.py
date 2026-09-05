#!/usr/bin/env python3
"""Validate and materialize the REC-EV-025AB R1 field-completeness correction."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from validate_rec_ev_025_feature_transfer_contract import validate_contract as validate_base_contract
except ImportError:
    from scripts.validate_rec_ev_025_feature_transfer_contract import validate_contract as validate_base_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-025ab-feature-transfer-execution-r1.json"
EXPECTED_CORRECTION_CANONICAL_SHA256 = "de0e825ea478b60954893e8394255359a15ca095f0e04966592e4f4c8a7dc84f"


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_correction(path: Path = DEFAULT) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(hashlib.sha256(canonical_json_bytes(value)).hexdigest() == EXPECTED_CORRECTION_CANONICAL_SHA256, "R1 correction drift")
    return value


def materialize_contract(path: Path = DEFAULT) -> dict[str, Any]:
    correction = load_correction(path)
    base_entry = correction["base_contract"]
    base_path = (ROOT / str(base_entry["path"])).resolve()
    require(base_path.is_file(), "base execution contract missing")
    require(base_path.stat().st_size == int(base_entry["bytes"]), "base execution contract byte drift")
    require(hashlib.sha256(base_path.read_bytes()).hexdigest() == str(base_entry["sha256"]), "base execution contract hash drift")
    base = json.loads(base_path.read_text(encoding="utf-8"))
    validate_base_contract(base)
    result = copy.deepcopy(base)
    patch = correction["corrections"]
    result["contract_id"] = patch["materialized_contract_id"]
    result["status"] = patch["materialized_status"]
    result["execution_revision"] = patch["execution_revision"]
    result["failed_locked_attempts"] = correction["failed_locked_attempts"]
    result["correction_contract"] = {
        "path": path.relative_to(ROOT).as_posix(),
        "canonical_sha256": EXPECTED_CORRECTION_CANONICAL_SHA256,
        "claim_boundary": correction["claim_boundary"],
    }
    for evidence_id, values in patch["experiments"].items():
        result["experiments"][evidence_id].update(values)
    result["output_roots"] = copy.deepcopy(patch["output_roots"])
    result["implementation_artifacts"] = list(patch["implementation_artifacts"])
    return result


def validate_contract(contract: Mapping[str, Any]) -> None:
    expected = materialize_contract()
    require(canonical_json_bytes(contract) == canonical_json_bytes(expected), "materialized R1 contract drift")
    require(contract["contract_id"] == "rec-ev-025ab-feature-transfer-execution-r1-v1", "R1 identity drift")
    require(contract["status"] == "APPROVED_FOR_ADAPTIVE_FEATURE_TRANSFER_EXECUTION_R1", "R1 status drift")
    require(contract["execution_revision"] == "R1_FIELD_COMPLETENESS_ONLY_NO_ESTIMAND_OR_THRESHOLD_CHANGE", "R1 scope drift")
    require(contract["experiments"]["REC-EV-025A"]["minimum_users"] == 150 and contract["experiments"]["REC-EV-025A"]["minimum_unique_targets"] == 60, "025A floor correction drift")
    require(contract["experiments"]["REC-EV-025B"]["minimum_users"] == 500 and contract["experiments"]["REC-EV-025B"]["minimum_unique_targets"] == 200, "025B floor correction drift")
    require(contract["output_roots"] == {"REC-EV-025A": "outputs/recommendation-evidence/rec-ev-025a-r1", "REC-EV-025B": "outputs/recommendation-evidence/rec-ev-025b-r1"}, "R1 output namespace drift")
    for evidence_id in ("REC-EV-025A", "REC-EV-025B"):
        failure = contract["failed_locked_attempts"][evidence_id]
        require(failure["selected_profile_rating_rows_parsed"] == 0 and failure["evaluation_rating_rows_parsed"] == 0 and failure["timestamp_bytes_parsed"] == 0 and failure["evaluation_labels_opened"] is False, "failed-attempt outcome boundary drift")


def main() -> int:
    validate_contract(materialize_contract())
    print("REC_EV_025AB_R1_CONTRACT_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
