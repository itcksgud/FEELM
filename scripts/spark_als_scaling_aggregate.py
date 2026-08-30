#!/usr/bin/env python3
"""Aggregate safe Spark ALS topology measurements and apply a predeclared gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGGREGATE_VERSION = "spark-als-scaling-aggregate-v1"


def canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--one-worker", type=Path, nargs="+", required=True)
    parser.add_argument("--two-workers", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-speedup", type=float, default=1.20)
    parser.add_argument("--maximum-rmse-difference", type=float, default=0.01)
    return parser.parse_args()


def load(paths: list[Path], workers: int) -> list[dict[str, Any]]:
    results = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if len(results) < 3:
        raise ValueError("at least three measured repetitions are required per topology")
    run_ids = [result.get("run_id") for result in results]
    topology_ids = [result.get("topology", {}).get("id") for result in results]
    if any(not isinstance(value, str) or not value for value in run_ids + topology_ids):
        raise ValueError("every repetition requires non-empty run and topology IDs")
    if len(set(run_ids)) != len(run_ids) or len(set(topology_ids)) != len(topology_ids):
        raise ValueError("duplicate benchmark repetitions cannot be aggregated")
    for result in results:
        if result.get("schema_version") != 1:
            raise ValueError("unsupported benchmark result schema")
        protocol = result.get("protocol")
        if not isinstance(protocol, dict) or canonical_hash(protocol) != result.get("protocol_sha256"):
            raise ValueError("benchmark protocol checksum mismatch")
        topology = result["topology"]
        if topology.get("master", "").startswith("spark://") is not True:
            raise ValueError(f"standalone master Gate failed for {topology['id']}")
        if topology["expected_workers"] != workers or topology["observed_remote_executors"] != workers:
            raise ValueError(f"worker observation Gate failed for {topology['id']}")
        if topology["worker_count_gate"] is not True:
            raise ValueError(f"standalone worker Gate was not asserted for {topology['id']}")
        if result["safe_to_track"] is not True:
            raise ValueError("unsafe result cannot be aggregated")
        fit = result.get("timing_seconds", {}).get("als_fit")
        total = result.get("timing_seconds", {}).get("application_total")
        rmse = result.get("quality", {}).get("rmse")
        coverage = result.get("quality", {}).get("prediction_coverage")
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (fit, total, rmse, coverage)):
            raise ValueError("benchmark metric must be finite")
        if fit <= 0 or total <= 0 or rmse < 0 or not 0 <= coverage <= 1:
            raise ValueError("benchmark metric outside its valid range")
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    fit = [float(result["timing_seconds"]["als_fit"]) for result in results]
    total = [float(result["timing_seconds"]["application_total"]) for result in results]
    rmse = [float(result["quality"]["rmse"]) for result in results]
    coverage = [float(result["quality"]["prediction_coverage"]) for result in results]
    return {
        "repetitions": len(results),
        "als_fit_seconds": fit,
        "als_fit_median_seconds": statistics.median(fit),
        "application_total_seconds": total,
        "application_total_median_seconds": statistics.median(total),
        "rmse": rmse,
        "rmse_median": statistics.median(rmse),
        "prediction_coverage": coverage,
        "prediction_coverage_median": statistics.median(coverage),
    }


def aggregate(
    one: list[dict[str, Any]],
    two: list[dict[str, Any]],
    *,
    minimum_speedup: float,
    maximum_rmse_difference: float,
) -> dict[str, Any]:
    run_ids = [result["run_id"] for result in one + two]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("a run cannot be reused across topologies")
    protocol_hashes = {result["protocol_sha256"] for result in one + two}
    row_pairs = {
        (result["input_aggregates"]["train_rows"], result["input_aggregates"]["validation_rows"])
        for result in one + two
    }
    if len(protocol_hashes) != 1 or len(row_pairs) != 1:
        raise ValueError("topologies did not use the same protocol and aggregate input")
    one_summary = summarize(one)
    two_summary = summarize(two)
    speedup = one_summary["als_fit_median_seconds"] / two_summary["als_fit_median_seconds"]
    rmse_difference = abs(one_summary["rmse_median"] - two_summary["rmse_median"])
    coverage_difference = abs(
        one_summary["prediction_coverage_median"] - two_summary["prediction_coverage_median"]
    )
    passed = (
        speedup >= minimum_speedup
        and rmse_difference <= maximum_rmse_difference
        and coverage_difference <= 1e-12
    )
    train_rows, validation_rows = next(iter(row_pairs))
    return {
        "schema_version": 1,
        "aggregate_version": AGGREGATE_VERSION,
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "same physical Windows host, Spark standalone master, separate worker JVMs",
        "claim_boundary": "local engineering evidence only; not multi-host production capacity",
        "protocol_sha256": next(iter(protocol_hashes)),
        "input_aggregates": {"train_rows": train_rows, "validation_rows": validation_rows},
        "gate": {
            "minimum_median_als_fit_speedup": minimum_speedup,
            "maximum_absolute_rmse_difference": maximum_rmse_difference,
            "maximum_prediction_coverage_difference": 1e-12,
        },
        "one_worker": one_summary,
        "two_workers": two_summary,
        "comparison": {
            "median_als_fit_speedup": speedup,
            "absolute_rmse_difference": rmse_difference,
            "absolute_prediction_coverage_difference": coverage_difference,
        },
        "decision": "LOCAL_SCALE_OUT_SUPPORTED" if passed else "NOT_JUSTIFIED_AT_MEASURED_SCALE",
        "gate_passed": passed,
        "safe_to_track": True,
    }


def gate_exit_code(result: dict[str, Any]) -> int:
    return 0 if result.get("gate_passed") is True else 2


def main() -> None:
    args = parse_args()
    if args.minimum_speedup <= 1 or args.maximum_rmse_difference < 0:
        raise ValueError("invalid aggregate Gate")
    result = aggregate(
        load(args.one_worker, 1),
        load(args.two_workers, 2),
        minimum_speedup=args.minimum_speedup,
        maximum_rmse_difference=args.maximum_rmse_difference,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    status = "PASS" if result["gate_passed"] else "FAIL"
    print(json.dumps({"status": status, "decision": result["decision"], **result["comparison"]}, sort_keys=True))
    exit_code = gate_exit_code(result)
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
