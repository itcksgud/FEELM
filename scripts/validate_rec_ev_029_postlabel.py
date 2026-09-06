"""Fail-closed validator for REC-EV-029C post-label execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-029c-postlabel-execution.json"


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
    require(contract_path.resolve() == DEFAULT.resolve(), "only frozen default execution contract is allowed")
    require(contract.get("schema_version") == 1, "schema drift")
    require(contract.get("contract_id") == "rec-ev-029c-postlabel-execution-v1", "contract id drift")
    require(contract.get("status") == "INDEPENDENT_EXACT_IMPLEMENTATION_AUDIT_REQUIRED", "status drift")
    required = {
        "postlabel_design",
        "postlabel_design_audit",
        "prelabel_execution_contract",
        "actual_prelabel_audit",
        "membership",
        "global_prelabel_score_seal",
        "movielens_archive",
        "analysis_core",
        "postlabel_runner",
        "postlabel_validator",
        "analysis_tests",
        "scoring_tests",
        "membership_tests",
    }
    require(set(contract["inputs"]) == required, "input pin set drift")
    paths = {name: verify(spec) for name, spec in contract["inputs"].items()}
    design = json.loads(paths["postlabel_design"].read_text(encoding="utf-8"))
    design_audit = json.loads(paths["postlabel_design_audit"].read_text(encoding="utf-8"))
    prelabel_audit = json.loads(paths["actual_prelabel_audit"].read_text(encoding="utf-8"))
    score_seal = json.loads(paths["global_prelabel_score_seal"].read_text(encoding="utf-8"))
    require(design_audit.get("status") == "REC_EV_029B_POSTLABEL_DESIGN_PASS", "post-label design audit did not pass")
    require(prelabel_audit.get("status") == "REC_EV_029_ACTUAL_PRELABEL_AUDIT_PASS", "pre-label actual audit did not pass")
    require(score_seal.get("status") == "GLOBAL_PRELABEL_TOP2_SCORE_SEAL_COMPLETE", "pre-label scores not globally sealed")
    require(score_seal.get("selection_target_rating_values_opened") is False, "selection labels already opened in pre-label seal")
    require(score_seal.get("replication_target_rating_values_opened") is False, "replication labels already opened in pre-label seal")
    require(design["selection"]["scope"] == ["R0", "R1", "R2", "R3", "R4"], "selection scope drift")
    require(design["selection"]["proxy_exclusion"] == ["KR", "RECENT"], "proxy exclusion drift")
    require(design["selection"]["minimum_passing_folds"] == 3, "fold gate drift")
    require(design["selection"]["winner_key_ascending"] == [
        "NEGATIVE_MINIMUM_FOLD_HARM20_BENEFIT_OWN_VS_RANDOM",
        "NEGATIVE_MINIMUM_FOLD_HARM20_BENEFIT_OWN_VS_SHUFFLE",
        "NEGATIVE_RANDOM_POOLED_HARM20_BENEFIT_OWN_VS_RANDOM",
        "NEGATIVE_MINIMUM_FOLD_TOP2_MIN_Q_BENEFIT_OWN_VS_RANDOM",
        "NEGATIVE_RANDOM_POOLED_OWN_TOP2_MEAN_Q",
        "K_ASC",
        "FAMILY_COMPLEXITY_STRUCTURED_THEN_E5_THEN_RRF",
        "ENCODING_PERCENTILE_THEN_BINARY",
    ], "winner key drift")
    replication = design["replication"]
    require(replication["user_family_size"] == 72 and replication["item_family_size"] == 48, "inference family drift")
    require(replication["repeats"] == 5000 and replication["confidence"] == 0.95, "bootstrap protocol drift")
    require(replication["user_seed"] == 20261006 and replication["item_seed"] == 20261007, "bootstrap seed drift")
    require(contract["execution"] == {
        "selection_membership_rows": 10849,
        "selection_target_slots": 209652,
        "selection_union_users": 2664,
        "replication_membership_rows": 11055,
        "replication_target_slots": 213036,
        "replication_union_users": 2706,
        "candidate_count": 90,
        "selection_then_winner_then_replication": True,
        "no_winner_keeps_replication_closed": True,
        "resume": "SEALED_ARTIFACTS_MUST_MATCH_CURRENT_UPSTREAM_IDENTITIES_AND_PARTIAL_STATES_FAIL_CLOSED",
    }, "execution constants drift")
    require(contract["firewall"] == {
        "implementation_audit_required_before_selection_labels": True,
        "timestamps_opened": False,
        "future_reserve_opened": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }, "execution firewall drift")
    audit = contract["implementation_audit"]
    require(audit.get("status") in {"PENDING", "REC_EV_029_POSTLABEL_IMPLEMENTATION_AUDIT_PASS"}, "implementation audit status drift")
    if audit.get("status") == "REC_EV_029_POSTLABEL_IMPLEMENTATION_AUDIT_PASS":
        require("auditor_thread_id" in audit and "evidence" in audit, "implementation audit evidence missing")
    require(contract.get("output_root") == design.get("output_root") == "outputs/recommendation-evidence/rec-ev-029/postlabel", "output root drift")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args()
    value = json.loads(args.contract.read_text(encoding="utf-8"))
    validate(value, args.contract)
    print("REC_EV_029_POSTLABEL_CONTRACT_VALID")


if __name__ == "__main__":
    main()
