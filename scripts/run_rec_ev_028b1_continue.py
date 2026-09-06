"""Continue REC-EV-028B after the audited 3,470-vs-3,517 count amendment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_rec_ev_028b_label_analysis import (
    ResumeError,
    analyze,
    artifact,
    atomic_json,
    load_contract,
    materialize_metrics,
    paths,
    validate_label_seal,
    verify_artifact,
)
from validate_rec_ev_028b1_amendment import DEFAULT as AMENDMENT_DEFAULT
from validate_rec_ev_028b1_amendment import ROOT
from validate_rec_ev_028b1_amendment import validate as validate_amendment


def _load_audit_approval(amendment: dict, amendment_path: Path) -> tuple[dict, Path]:
    approval_path = ROOT / str(amendment["audit_approval_path"])
    if not approval_path.is_file():
        raise ResumeError("independent B1 PASS record is required before metrics")
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    required = {
        "schema_version": 1,
        "status": "REC_EV_028B1_AMENDMENT_PASS",
        "audit_thread": "01a0751b-6621-7a61-94b2-4c401c7a3066",
        "runner_executed_by_auditor": False,
        "ratings_reaccessed_by_auditor": False,
        "files_modified_by_auditor": False,
    }
    for key, expected in required.items():
        if approval.get(key) != expected:
            raise ResumeError(f"independent audit approval drift: {key}")
    if approval.get("amendment") != artifact(amendment_path):
        raise ResumeError("independent audit approval amendment identity drift")
    return approval, approval_path


def _validate_pre_metrics_lock(amendment: dict, p: dict[str, Path]) -> tuple[dict, Path]:
    lock_path = verify_artifact(amendment["pre_metrics_lock"])
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("status") != "LOCKED_AFTER_LABEL_BEFORE_ANY_METRIC_OR_OUTCOME":
        raise ResumeError("pre-metrics lock status drift")
    if lock.get("label_seal") != artifact(p["label_seal"]):
        raise ResumeError("pre-metrics lock label identity drift")
    if lock.get("metrics_or_outcome_artifacts_present") != [] or lock.get("outcome_values_inspected") is not False:
        raise ResumeError("pre-metrics lock absence evidence drift")
    required_false = (
        "timestamps_opened",
        "future_movie_membership_opened",
        "locked_test_opened",
        "final_reserve_opened",
        "product_policy_changed",
    )
    if any(lock.get(key) is not False for key in required_false):
        raise ResumeError("pre-metrics lock firewall drift")
    return lock, lock_path


def _authorization_path(p: dict[str, Path], kind: str) -> Path:
    return p["root"] / f"b1-{kind}-authorization-seal.json"


def _validate_authorization(path: Path, kind: str, amendment_path: Path, approval_path: Path, upstream: Path) -> dict:
    if not path.is_file():
        raise ResumeError(f"B1 {kind} authorization seal is required")
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "status": f"B1_{kind.upper()}_AUTHORIZED_AFTER_INDEPENDENT_AMENDMENT_PASS",
        "amendment": artifact(amendment_path),
        "audit_approval": artifact(approval_path),
        "upstream": artifact(upstream),
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise ResumeError(f"B1 {kind} authorization seal drift")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amendment", type=Path, default=AMENDMENT_DEFAULT)
    parser.add_argument("--phase", choices=("metrics", "analyze", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.resume:
        raise ResumeError("REC-EV-028B1 continuation requires explicit --resume")
    amendment = json.loads(args.amendment.read_text(encoding="utf-8"))
    validate_amendment(amendment, args.amendment.resolve())
    _, approval_path = _load_audit_approval(amendment, args.amendment.resolve())
    contract_path = verify_artifact(amendment["base_contract"])
    contract = load_contract(contract_path)
    p = paths(contract)
    seal, labels = validate_label_seal(contract, p)
    _, pre_metrics_lock_path = _validate_pre_metrics_lock(amendment, p)
    if artifact(p["label_seal"]) != amendment["observed_label_execution"]["label_seal"]:
        raise ResumeError("amendment label seal identity drift")
    if artifact(p["labels"]) != amendment["observed_label_execution"]["labels"]:
        raise ResumeError("amendment labels identity drift")
    expected = amendment["corrected_population"]
    random_users = set(labels.loc[labels["track"].eq("RANDOM_ITEM_COLD"), "user_key"].astype(str))
    all_users = set(labels["user_key"].astype(str))
    if (
        len(labels) != int(expected["membership_rows"])
        or labels["user_key"].nunique() != int(expected["all_outer_union_users"])
        or sum(len(values) for values in labels["target_movie_ids"]) != int(expected["target_labels"])
        or int(seal["reader"]["evaluation_user_rating_values_parsed"]) != int(expected["evaluation_user_rating_values_parsed"])
        or len(random_users) != int(expected["random_pooled_union_users"])
        or len(all_users - random_users) != int(expected["proxy_only_users"])
    ):
        raise ResumeError("corrected population evidence drift")
    metrics_authorization = _authorization_path(p, "metrics")
    analysis_authorization = _authorization_path(p, "analysis")
    phases = ("metrics", "analyze") if args.phase == "all" else (args.phase,)
    for phase in phases:
        if phase == "metrics":
            if p["metrics_seal"].exists():
                _validate_authorization(metrics_authorization, "metrics", args.amendment.resolve(), approval_path, p["metrics_seal"])
                materialize_metrics(contract, p, args.resume)
            else:
                lock = json.loads(pre_metrics_lock_path.read_text(encoding="utf-8"))
                present = [str(ROOT / raw) for raw in lock["absent_metric_or_outcome_paths"] if (ROOT / raw).exists()]
                if present:
                    raise ResumeError(f"pre-approval metric/outcome artifact appeared: {present[0]}")
                materialize_metrics(contract, p, args.resume)
                atomic_json(metrics_authorization, {
                    "status": "B1_METRICS_AUTHORIZED_AFTER_INDEPENDENT_AMENDMENT_PASS",
                    "amendment": artifact(args.amendment.resolve()),
                    "audit_approval": artifact(approval_path),
                    "pre_metrics_lock": artifact(pre_metrics_lock_path),
                    "upstream": artifact(p["metrics_seal"]),
                })
        elif phase == "analyze":
            if not p["metrics_seal"].exists():
                raise ResumeError("analysis requires sealed metrics")
            _validate_authorization(metrics_authorization, "metrics", args.amendment.resolve(), approval_path, p["metrics_seal"])
            if p["analysis"].exists():
                _validate_authorization(analysis_authorization, "analysis", args.amendment.resolve(), approval_path, p["analysis"])
                analyze(contract, p, args.resume)
            else:
                analyze(contract, p, args.resume)
                atomic_json(analysis_authorization, {
                    "status": "B1_ANALYSIS_AUTHORIZED_AFTER_INDEPENDENT_AMENDMENT_PASS",
                    "amendment": artifact(args.amendment.resolve()),
                    "audit_approval": artifact(approval_path),
                    "metrics_authorization": artifact(metrics_authorization),
                    "upstream": artifact(p["analysis"]),
                })


if __name__ == "__main__":
    main()
