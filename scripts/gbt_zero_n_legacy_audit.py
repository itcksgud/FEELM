"""Audit the sealed GBT120_s339 artifact as a non-comparable legacy baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments" / "gbt-zero-n" / "completion-config.json"


def pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    expected = json.loads(CONFIG.read_text(encoding="utf-8"))["legacy_baseline"]
    outcome = json.loads((args.legacy_root / "outcome.json").read_text(encoding="utf-8"))
    metrics = json.loads((args.legacy_root / "model" / "metrics.json").read_text(encoding="utf-8"))
    checks = json.loads((args.legacy_root / "checks.json").read_text(encoding="utf-8"))
    if outcome["status"] != "SUCCESS" or outcome["fit_id"] != expected["fit_id"]:
        raise RuntimeError("legacy fit identity/status mismatch")
    if metrics["seed"] != expected["expected_seed"] or metrics["dimensions"] != expected["expected_dimensions"]:
        raise RuntimeError("legacy seed/dimension mismatch")
    if not checks["partition_equal"] or not checks["params_equal"]:
        raise RuntimeError("legacy artifact failed its own parity checks")
    pinned = {}
    for relative in (
        "command.json", "outcome.json", "checks.json", "model/metrics.json",
        "model/partition-identity.json", "model/resolved-estimator.json", "predictions.npy",
    ):
        pinned[relative] = pin(args.legacy_root / relative)
    report = {
        "status": "PASS",
        "fit_id": outcome["fit_id"],
        "legacy_execution_status": outcome["status"],
        "seed": metrics["seed"],
        "dimensions": metrics["dimensions"],
        "training_rows": metrics["training_rows"],
        "actual_iterations": metrics["actual_iterations"],
        "fit_seconds": metrics["fit_seconds"],
        "peak_bytes": metrics["peak_bytes"],
        "resource_status": metrics["resource_status"],
        "comparison_policy": expected["comparison_policy"],
        "eligible_for_v7_same_cohort_comparison": False,
        "non_comparable_reasons": [
            "different user and time cohorts",
            "different RH230 feature schema and row construction",
            "different training volume and sample-weight policy",
            "no arbitrary-N or LOW_HISTORY_COHORT contract",
        ],
        "files": pinned,
        "calculator": pin(Path(__file__)),
        "final_test_opened": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
