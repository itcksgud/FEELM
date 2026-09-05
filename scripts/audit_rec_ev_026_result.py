#!/usr/bin/env python3
"""Read-only deterministic audit for the sealed REC-EV-026 result."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

try:
    import run_rec_ev_026_experiment as base
except ImportError:
    from scripts import run_rec_ev_026_experiment as base


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-026-result-audit-amendment.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_amendment(value: Mapping[str, Any]) -> None:
    if value["contract_id"] != "rec-ev-026-result-audit-amendment-v1" or value["status"] != "POST_RUN_READ_ONLY_AUDIT":
        raise RuntimeError("amendment identity drift")
    if value["result_effect"] != "NONE_RESULT_VALUES_INDEPENDENTLY_MATCHED_BEFORE_INCIDENT":
        raise RuntimeError("result-effect drift")
    for spec in value["pinned"].values():
        path = resolve(spec["path"])
        if not path.is_file() or path.stat().st_size != spec["bytes"] or sha256(path) != spec["sha256"]:
            raise RuntimeError(f"pinned artifact drift: {path}")
    audit = value["audit"]
    if audit != {"required_initial_phase": "METRICS_BOOTSTRAP_RESULT_SEAL", "advance_behavior": "VALIDATE_CURRENT_FINAL_PROGRESS_THEN_NO_OP_NO_WRITE", "verification_order": ["MAPPER_FIT_GATE", "PROFILE_RATING_OPEN", "ALL_HEAD_RANK_SEAL", "EVALUATION_LABEL_OPEN", "METRICS_BOOTSTRAP_RESULT_SEAL"], "before_after_full_file_inventory_sha256_equal": True, "timestamp_read": False, "locked_test_access": False, "final_reserve_access": False, "product_policy_change": False, "champion": None}:
        raise RuntimeError("audit boundary drift")


def inventory(root: Path) -> dict[str, tuple[int, str]]:
    return {path.relative_to(root).as_posix(): (path.stat().st_size, sha256(path)) for path in sorted(root.rglob("*")) if path.is_file()}


def no_write_advance(contract: Mapping[str, Any], _phase: str) -> None:
    if base.current_phase(contract) != "METRICS_BOOTSTRAP_RESULT_SEAL":
        raise base.ResumeError("audit requires final sealed progress")


def audit(value: Mapping[str, Any]) -> dict[str, Any]:
    validate_amendment(value)
    contract = base.load_contract(resolve(value["pinned"]["execution_contract"]["path"]))
    root = resolve(str(contract["output_root"]))
    if base.current_phase(contract) != "METRICS_BOOTSTRAP_RESULT_SEAL":
        raise base.ResumeError("audit requires final sealed progress")
    before = inventory(root)
    original_advance = base.advance
    base.advance = no_write_advance
    try:
        base.fit_mapper(contract)
        base.profile_phase(contract)
        base.rank_phase(contract)
        base.label_phase(contract)
        result = base.analyze_phase(contract)
    finally:
        base.advance = original_advance
    after = inventory(root)
    if before != after:
        raise RuntimeError("read-only audit modified sealed artifacts")
    if len(before) != 18 or result["status"] != "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE":
        raise RuntimeError("sealed result inventory/status drift")
    return {"status": "REC_EV_026_RESULT_READ_ONLY_AUDIT_PASS", "files": len(before), "result_status": result["status"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args()
    if args.contract.resolve() != DEFAULT.resolve():
        raise RuntimeError("only default amendment accepted")
    print(json.dumps(audit(load(args.contract)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
