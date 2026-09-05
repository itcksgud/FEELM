#!/usr/bin/env python3
"""Validate the REC-EV-023D-A1 pre-label numerical amendment contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-023d-a1-feature-only-lightfm-attribution.json"

EXPECTED_TOP_LEVEL = {
    "contract_id", "evidence_id", "status", "base_contract", "output_root", "predecessor",
    "target_fold_in", "implementation_artifacts", "outputs", "resume", "invariants",
}
EXPECTED_IMPLEMENTATIONS = [
    "scripts/rec_ev_022a_core.py",
    "scripts/run_rec_ev_023b_masked_cold_screen.py",
    "scripts/run_rec_ev_023c_crossed_sensitivity.py",
    "scripts/train_rec_ev_023d_lightfm.py",
    "scripts/run_rec_ev_023d_lightfm_attribution.py",
    "scripts/run_rec_ev_023d_a1_lightfm_attribution.py",
    "scripts/validate_rec_ev_023d_a1_contract.py",
]
EXPECTED_OUTPUTS = {
    "protocol_lock": "protocol-lock.json",
    "source_manifest": "source-manifest.json",
    "progress": "run-progress.json",
    "foldin_schedule": "cache/foldin-schedule.npz",
    "foldin_schedule_integrity": "cache/foldin-schedule.integrity.json",
    "rank_root": "cache/rank-seeds",
    "rank_set_integrity": "cache/rank-set.integrity.json",
    "user_metrics": "user-metrics.parquet",
    "user_metrics_integrity": "user-metrics.integrity.json",
    "bootstrap_replicates": "cache/bootstrap-replicates.npz",
    "bootstrap_integrity": "cache/bootstrap-replicates.integrity.json",
    "selection": "feature-only-lightfm-a1-selection.json",
    "result": "feature-only-lightfm-a1-result.json",
    "analysis_integrity": "feature-only-lightfm-a1-result.integrity.json",
}
EXPECTED_PREDECESSOR_KEYS = {
    "predecessor_contract", "predecessor_protocol_lock", "predecessor_source_manifest",
    "predecessor_progress", "predecessor_prepared_integrity", "predecessor_interactions",
    "predecessor_train_users", "predecessor_feature_mask", "predecessor_structured_matched",
    *{
        f"predecessor_s{seed}_{name}"
        for seed in (17, 42, 73, 101, 211)
        for name in ("config", "result", "integrity")
    },
}
EXPECTED_PREDECESSOR_ARTIFACTS_SHA256 = "03e42eb163a44bb27f0cb5f85460c1573503a39777ce43fc7f21524dea684a37"
EXPECTED_TARGET_FOLD_IN_SHA256 = "206f03c817de483b007e9a035329f13bbd14c15ed889f020c97e70be15368dff"
EXPECTED_EFFECTIVE_LOCKED_SPEC_SHA256 = "4afe76f96b0f1aba3242d38e0a1d4c944dcfc37fecc4ac75f91975533bbf2d92"
EXPECTED_TARGET_FOLD_IN_KEYS = {
    "name", "reuse_019c_fold_in", "weights", "labels", "confidence", "initial_user_vector",
    "user_bias", "steps", "base_learning_rate", "safety_factor", "regularization", "score",
    "gradient", "stable_sigmoid", "dtype", "one_class_allowed", "zero_nonzero_weights",
    "lipschitz_bound", "learning_rate_rule", "inactive_row_rule", "step_application", "guard",
    "step_size_inputs", "step_size_forbidden_inputs", "optimizer_exclusions",
    "item_representation_frozen_sha_before_after", "claim",
}
LOCKED_SPEC_KEYS = (
    "purpose", "authorization", "implementation_artifacts", "fixed_reuse", "train_reader",
    "feature_support", "lightfm", "target_fold_in", "heads", "head_semantics", "scoring",
    "metrics", "statistics", "decision", "carry_forward_equivalence", "resume",
    "claim_boundary", "invariants",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _effective_locked_spec(contract: Mapping[str, Any]) -> dict[str, Any]:
    base_path = ROOT / str(contract["base_contract"]["path"])
    inherited = json.loads(base_path.read_text(encoding="utf-8"))
    effective = copy.deepcopy(inherited)
    effective["implementation_artifacts"] = list(contract["implementation_artifacts"])
    effective["target_fold_in"] = copy.deepcopy(contract["target_fold_in"])
    effective["target_fold_in"]["learning_rate"] = float(contract["target_fold_in"]["base_learning_rate"])
    effective["resume"] = copy.deepcopy(contract["resume"])
    effective["invariants"] = copy.deepcopy(contract["invariants"])
    value = {key: effective[key] for key in LOCKED_SPEC_KEYS}
    value.update({
        "evidence_id": contract["evidence_id"],
        "amendment_status": contract["status"],
        "predecessor": contract["predecessor"],
    })
    return value


def _artifact(entry: Mapping[str, Any], name: str) -> None:
    _require(set(entry) == {"path", "bytes", "sha256"}, f"{name} artifact schema drift")
    _require(isinstance(entry["path"], str) and bool(entry["path"]), f"{name} path drift")
    _require(isinstance(entry["bytes"], int) and entry["bytes"] > 0, f"{name} bytes drift")
    digest = entry["sha256"]
    _require(
        isinstance(digest, str) and len(digest) == 64 and digest == digest.lower()
        and all(character in "0123456789abcdef" for character in digest),
        f"{name} SHA-256 drift",
    )


def validate(contract: Mapping[str, Any]) -> None:
    _require(set(contract) == EXPECTED_TOP_LEVEL, "top-level key drift")
    _require(contract["contract_id"] == "rec-ev-023d-a1-feature-only-lightfm-attribution-v1", "contract id drift")
    _require(contract["evidence_id"] == "REC-EV-023D-A1", "evidence id drift")
    _require(contract["status"] == "APPROVED_FOR_PRELABEL_FOLDIN_NUMERICAL_AMENDMENT", "status drift")
    _require(contract["output_root"] == "outputs/recommendation-evidence/rec-ev-023d-a1", "output root drift")
    _artifact(contract["base_contract"], "base_contract")
    _require(
        contract["base_contract"] == {
            "path": "docs/recommendation/contracts/rec-ev-023d-feature-only-lightfm-attribution.json",
            "bytes": 16740,
            "sha256": "d684e84dd58d28c300d267354dc082107bdf8a567af659053d31a5e9d49a40bf",
        },
        "base contract envelope drift",
    )
    base_path = ROOT / str(contract["base_contract"]["path"])
    _require(
        base_path.is_file() and base_path.stat().st_size == contract["base_contract"]["bytes"]
        and _sha256_file(base_path) == contract["base_contract"]["sha256"],
        "base contract file drift",
    )

    predecessor = contract["predecessor"]
    _require(
        set(predecessor) == {
            "evidence_id", "failure_phase", "rank_root", "rank_root_required_absent",
            "evaluation_labels_opened", "progress_phase", "completed_seeds", "reuse",
            "run_signature", "artifacts",
        },
        "predecessor schema drift",
    )
    _require(predecessor["evidence_id"] == "REC-EV-023D", "predecessor evidence drift")
    _require(predecessor["failure_phase"] == "PRE_RANK_FOLDIN_LIPSCHITZ_GUARD", "failure phase drift")
    _require(predecessor["rank_root"] == "outputs/recommendation-evidence/rec-ev-023d/cache/rank-seeds", "rank root drift")
    _require(predecessor["rank_root_required_absent"] is True, "rank absence gate drift")
    _require(predecessor["evaluation_labels_opened"] is False, "predecessor label firewall drift")
    _require(predecessor["progress_phase"] == "FITTING", "predecessor progress drift")
    _require(predecessor["completed_seeds"] == [17, 42, 73, 101, 211], "predecessor seeds drift")
    _require(predecessor["reuse"] == "EXACT_BYTE_IDENTICAL_PRELABEL_FITS_ONLY_NO_REFIT_NO_RESEAL", "reuse drift")
    _require(predecessor["run_signature"] == "8406ac2ad7ce48d50780cf9c157c6b1f2e8e5cd5135ae7c5475694d713a88cee", "old signature drift")
    artifacts = predecessor["artifacts"]
    _require(set(artifacts) == EXPECTED_PREDECESSOR_KEYS, "predecessor artifact set drift")
    for name, entry in artifacts.items():
        _artifact(entry, name)
    _require(
        len({entry["path"] for entry in artifacts.values()}) == len(artifacts),
        "duplicate predecessor artifact path",
    )
    _require(
        hashlib.sha256(_canonical_json_bytes(artifacts)).hexdigest() == EXPECTED_PREDECESSOR_ARTIFACTS_SHA256,
        "predecessor artifact family digest drift",
    )

    fold = contract["target_fold_in"]
    _require(set(fold) == EXPECTED_TARGET_FOLD_IN_KEYS, "target fold-in key drift")
    _require(
        hashlib.sha256(_canonical_json_bytes(fold)).hexdigest() == EXPECTED_TARGET_FOLD_IN_SHA256,
        "target fold-in digest drift",
    )
    _require(fold["name"] == "REC_EV_023D_A1_WEIGHTED_FULL_BATCH_USER_VECTOR_ONLY_80_STEP_ROW_LIPSCHITZ_CAPPED", "fold-in name drift")
    _require(fold["steps"] == 80, "fold-in steps drift")
    _require(fold["base_learning_rate"] == 0.05, "base learning rate drift")
    _require(fold["safety_factor"] == 0.9, "safety factor drift")
    _require(fold["regularization"] == 0.000001, "regularization drift")
    _require(fold["learning_rate_rule"] == "ACTIVE_ROW_I_ETA_I_EQUALS_MIN_BASE_LEARNING_RATE_AND_SAFETY_FACTOR_DIV_L_I", "eta rule drift")
    _require(fold["step_application"] == "ETA_I_COMPUTED_ONCE_BEFORE_STEP_ZERO_AND_FIXED_FOR_ALL_80_STEPS", "step application drift")
    _require(fold["inactive_row_rule"] == "ZERO_NONZERO_WEIGHT_ROW_HAS_ETA_ZERO_AND_NO_USER_VECTOR_UPDATE", "inactive rule drift")
    _require(fold["dtype"] == "FLOAT64", "fold-in dtype drift")
    _require(fold["claim"] == "FIXED_80_STEP_ITERATE_NOT_OPTIMUM", "fold-in claim drift")
    _require(fold["item_representation_frozen_sha_before_after"] is True, "item representation freeze drift")
    for forbidden in ("ITEM_BIAS", "TARGET_POSITION", "TARGET_FACTOR", "TARGET_SCORE", "RANK", "Q", "EVALUATION_LABEL", "METRIC"):
        _require(forbidden in fold["step_size_forbidden_inputs"], f"missing forbidden step-size input: {forbidden}")
    _require("NO_BACKTRACKING" in fold["optimizer_exclusions"] and "NO_EARLY_STOPPING" in fold["optimizer_exclusions"], "optimizer exclusions drift")

    _require(contract["implementation_artifacts"] == EXPECTED_IMPLEMENTATIONS, "implementation artifact order/set drift")
    _require(contract["outputs"] == EXPECTED_OUTPUTS, "output mapping drift")
    _require(
        contract["resume"] == {
            "required": True,
            "drift": "FAIL_CLOSED",
            "predecessor_fit_carry_forward": "VERIFY_OLD_SIGNATURE_AND_EXACT_SHA_NO_COPY_NO_REFIT_NO_RESEAL",
            "foldin_schedule_sealed_before_rank": True,
            "all_five_rank_sets_sealed_before_label_open": True,
            "completed_rank_byte_mtime_unchanged": True,
        },
        "resume policy drift",
    )
    _require(
        contract["invariants"] == {
            "locked_test_opened": False,
            "stage2_opened": False,
            "final_reserve_opened": False,
            "champion": None,
            "product_policy_updated": False,
        },
        "invariant drift",
    )
    _require(
        hashlib.sha256(_canonical_json_bytes(_effective_locked_spec(contract))).hexdigest()
        == EXPECTED_EFFECTIVE_LOCKED_SPEC_SHA256,
        "effective locked-spec digest drift",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    validate(contract)
    print("REC-EV-023D-A1 contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
