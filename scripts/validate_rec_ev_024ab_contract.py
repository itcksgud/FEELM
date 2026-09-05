#!/usr/bin/env python3
"""Validate the frozen REC-EV-024A/B anchor-policy preflight contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-024ab-anchor-policy-design.json"
EXPECTED_CANONICAL_SHA256 = "1a1ce73e93e33138d58c2cc8de4a1bba468bd0739314a29e680256bf5b1b7902"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_contract(contract: Mapping[str, Any]) -> None:
    canonical = (json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _require(hashlib.sha256(canonical).hexdigest() == EXPECTED_CANONICAL_SHA256, "canonical contract drift")
    required = {
        "schema_version", "contract_id", "status", "purpose", "independent_design_audit",
        "prior_invalid_preflight_incident", "authorization", "implementation_artifacts",
        "allowed_input_artifacts", "forbidden_input_artifacts", "roles_and_reader",
        "serialization", "universe", "common_design", "experiments", "execution_contract",
        "bootstrap", "train_prior_pin", "claim_boundary", "output_root", "outputs", "resume",
        "invariants",
    }
    _require(set(contract) == required, "top-level schema drift")
    _require(contract["schema_version"] == 1, "schema version drift")
    _require(contract["contract_id"] == "rec-ev-024ab-anchor-policy-design-v1", "contract id drift")
    _require(contract["status"] == "APPROVED_FOR_PRELABEL_FIREWALLED_FEASIBILITY", "status drift")
    _require(contract["implementation_artifacts"] == [
        "scripts/rec_ev_022a_core.py",
        "scripts/run_rec_ev_023ef_preflight.py",
        "scripts/validate_rec_ev_023ef_contract.py",
        "scripts/run_rec_ev_024ab_preflight.py",
        "scripts/validate_rec_ev_024ab_contract.py",
        "scripts/tests/test_rec_ev_024ab_preflight.py",
    ], "implementation dependency closure drift")
    audit = contract["independent_design_audit"]
    _require(audit == {
        "thread_id": "01a0704a-ff92-7851-904a-bf3970b3d905",
        "rounds": 3,
        "final_verdict": "NEXT_ANCHOR_DESIGN_PASS",
    }, "design audit drift")
    incident = contract["prior_invalid_preflight_incident"]
    _require(incident == {
        "old_locked_user_movie_ids_parsed": True,
        "rating_values_parsed": False,
        "timestamps_parsed": False,
        "metrics_or_labels_opened": False,
        "discarded_counts_forbidden_as_inputs": [431, 3498],
    }, "incident disclosure drift")
    auth = contract["authorization"]
    _require(auth["stage1_and_stage2_user_movie_id_preflight"] is True, "ID preflight not authorized")
    for key in ("rating_or_timestamp_access", "old_locked_outcome_access", "final_reserve_access", "champion_selection", "product_policy_change"):
        _require(auth[key] is False, f"authorization widened: {key}")
    reader = contract["roles_and_reader"]
    _require(reader["maximum_user_id"] == 300000, "maximum user drift")
    _require(reader["evaluation_role_bucket_ranges"] == [[6000, 7999], [8000, 9199]], "role drift")
    _require(reader["final_reserve_bucket_range_forbidden"] == [9200, 9999], "reserve role drift")
    _require(reader["rating_value_bytes_parsed"] == reader["timestamp_bytes_parsed"] == 0, "reader outcome access")
    serialization = contract["serialization"]
    _require(serialization["pipe"] == "UTF8_0x7C", "pipe drift")
    _require(serialization["canonical_decimal"] == "UNSIGNED_BASE10_NO_LEADING_ZERO", "decimal drift")
    common = contract["common_design"]
    expected_cells = [
        {"encoding": encoding, "k": k}
        for k in (6, 8, 14)
        for encoding in ("BINARY_SIGN", "PERCENTILE_MAGNITUDE")
    ]
    _require(common["profile_master_n"] == 14 and common["anchor_n"] == 2 and common["panels"] == 4, "master counts drift")
    _require(common["cells"] == expected_cells, "022B cell set/order drift")
    _require(common["source_only"] == "P1_THROUGH_PK", "source policy drift")
    _require(common["target2_mixed"] == "P1_THROUGH_P_K_MINUS_2_PLUS_A1_A2", "mixed policy drift")
    _require(common["input_evaluation_disjoint"] == "P_UNION_A_INTERSECTION_UNION_ALL_PANEL_EVAL_AND_CONTROL_IS_EMPTY", "role disjointness drift")
    expected_specs = {
        "REC-EV-024A": {
            "counts": (10, 10, 24, 12, 150, 40, 60),
            "global": ("rec-ev-024a-global-korean-role-v1", "rec-ev-024a-global-nonkorean-role-v1"),
            "target": [f"rec-ev-024a-panel-{panel}-korean-eval-v1" for panel in range(4)],
            "control": [f"rec-ev-024a-panel-{panel}-nonkorean-control-v1" for panel in range(4)],
            "tie": "rec-ev-024a-rank-tie-v1",
        },
        "REC-EV-024B": {
            "counts": (20, 20, 34, 22, 500, 100, 200),
            "global": ("rec-ev-024b-global-recent-role-v1", "rec-ev-024b-global-pre2020-role-v1"),
            "target": [f"rec-ev-024b-panel-{panel}-recent-eval-v1" for panel in range(4)],
            "control": [f"rec-ev-024b-panel-{panel}-pre2020-control-v1" for panel in range(4)],
            "tie": "rec-ev-024b-rank-tie-v1",
        },
    }
    _require(set(contract["experiments"]) == set(expected_specs), "experiment set drift")
    for evidence_id, expected in expected_specs.items():
        spec = contract["experiments"][evidence_id]
        observed_counts = tuple(spec[key] for key in (
            "target_n", "control_n", "minimum_source_ratings", "minimum_target_ratings",
            "minimum_users", "minimum_unique_anchors", "minimum_unique_evaluation_targets",
        ))
        _require(observed_counts == expected["counts"], f"{evidence_id} count/floor drift")
        _require((spec["global_target_salt"], spec["global_source_salt"]) == expected["global"], f"{evidence_id} global salt drift")
        _require(spec["panel_target_salts"] == expected["target"], f"{evidence_id} target salts drift")
        _require(spec["panel_control_salts"] == expected["control"], f"{evidence_id} control salts drift")
        _require(spec["tie_prefix"] == expected["tie"], f"{evidence_id} tie drift")
    execution = contract["execution_contract"]
    _require(execution["feature_head"] == "FULL_CURRENT_STRUCTURED_G_C_P_W", "head drift")
    _require(execution["primary_n"] == 2, "Top2 drift")
    _require(execution["primary_family_each_experiment"] == "6_CELLS_X_2_DOMAINS_X_2_ENDPOINTS_EQUALS_24", "family drift")
    bootstrap = contract["bootstrap"]
    _require(bootstrap["namespace"] == "feelm-bootstrap-v1|rec-ev-024ab-anchor-user-bootstrap-v1|EVIDENCE_ID|ATTEMPT|user|USER_KEY", "bootstrap namespace drift")
    _require(bootstrap["attempt_ids"] == [0, 7999] and bootstrap["valid_replicates"] == 4000, "bootstrap count drift")
    _require(len(bootstrap["golden_fixtures"]) == 4, "bootstrap golden count drift")
    prior = contract["train_prior_pin"]
    _require(prior["sha256"] == "afd18bc16e357871ca6a4dfd01ce9319f09efd1f63b29d4bbd5d6565190b218d", "prior file drift")
    _require(prior["key"] == "g0_mid" and prior["shape"] == [10] and prior["dtype"] == "<f8", "prior array drift")
    _require(prior["c_order_value_bytes_sha256"] == "a1096d7ebdebeb41da6ee84aef1670e3d0925447989a52314ab4b7f6612fb907", "prior value drift")
    invariants = contract["invariants"]
    _require(invariants == {
        "rating_value_bytes_parsed": 0,
        "timestamp_bytes_parsed": 0,
        "old_locked_ratings_timestamps_metrics_opened": False,
        "final_reserve_opened": False,
        "product_policy_updated": False,
        "champion": None,
    }, "invariants drift")
    _require(contract["resume"] == {"required": True, "partial_state": "FAIL_CLOSED", "drift": "FAIL_CLOSED"}, "resume drift")
    _require(contract["output_root"] == "outputs/recommendation-evidence/rec-ev-024ab-preflight", "output root drift")
    _require(contract["outputs"] == {
        "protocol_lock": "protocol-lock.json",
        "source_manifest": "source-manifest.json",
        "progress": "run-progress.json",
        "preflight": "preflight.json",
        "preflight_integrity": "preflight.integrity.json",
    }, "output mapping drift")
    output_root = (ROOT / contract["output_root"]).resolve()
    allowed_root = (ROOT / "outputs/recommendation-evidence/rec-ev-024ab-preflight").resolve()
    _require(output_root == allowed_root, "resolved output root drift")
    for relative in contract["outputs"].values():
        destination = (output_root / str(relative)).resolve()
        _require(destination.is_relative_to(allowed_root), "output escapes preflight root")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    args = parser.parse_args()
    path = Path(args.contract)
    path = path if path.is_absolute() else (ROOT / path).resolve()
    contract = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(contract)
    print("REC_EV_024AB_CONTRACT_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
