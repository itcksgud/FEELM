#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    assert result["schema_version"] == 1
    assert result["aggregate_version"] == "spark-als-scaling-aggregate-v1"
    assert result["safe_to_track"] is True
    assert result["scope"] == "same physical Windows host, Spark standalone master, separate worker JVMs"
    assert result["claim_boundary"] == "local engineering evidence only; not multi-host production capacity"
    assert result["gate"] == {
        "maximum_absolute_rmse_difference": 0.01,
        "maximum_prediction_coverage_difference": 1e-12,
        "minimum_median_als_fit_speedup": 1.2,
    }
    one, two = result["one_worker"], result["two_workers"]
    for topology in (one, two):
        assert topology["repetitions"] == 3
        assert len(topology["als_fit_seconds"]) == 3
        assert math.isclose(
            statistics.median(topology["als_fit_seconds"]),
            topology["als_fit_median_seconds"],
            rel_tol=0,
            abs_tol=1e-12,
        )
        assert len(set(topology["rmse"])) == 1
        assert len(set(topology["prediction_coverage"])) == 1
    speedup = one["als_fit_median_seconds"] / two["als_fit_median_seconds"]
    rmse_difference = abs(one["rmse_median"] - two["rmse_median"])
    coverage_difference = abs(
        one["prediction_coverage_median"] - two["prediction_coverage_median"]
    )
    assert math.isclose(speedup, result["comparison"]["median_als_fit_speedup"], abs_tol=1e-12)
    assert rmse_difference == result["comparison"]["absolute_rmse_difference"]
    assert coverage_difference == result["comparison"]["absolute_prediction_coverage_difference"]
    passed = speedup >= 1.2 and rmse_difference <= 0.01 and coverage_difference <= 1e-12
    assert result["gate_passed"] is passed
    assert result["decision"] == ("LOCAL_SCALE_OUT_SUPPORTED" if passed else "NOT_JUSTIFIED_AT_MEASURED_SCALE")
    # This tracked file backs a completed passing claim. A consistently encoded
    # failure is valid aggregate data, but it cannot satisfy this evidence Gate.
    assert result["gate_passed"] is True
    assert result["decision"] == "LOCAL_SCALE_OUT_SUPPORTED"
    serialized = json.dumps(result, sort_keys=True).lower()
    for forbidden in ("user_id", "movie_id", "authorization", "token", "c:\\\\users\\\\"):
        assert forbidden not in serialized
    print(json.dumps({"status": "PASS", "decision": result["decision"], "speedup": speedup}))


if __name__ == "__main__":
    main()
