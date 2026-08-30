from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from spark_als_scaling_aggregate import (
    aggregate,
    canonical_hash as aggregate_protocol_hash,
    gate_exit_code,
    load,
)
from spark_als_scaling_benchmark import canonical_hash, protocol, validate_args


def fixture(**changes):
    values = {
        "train": __import__("pathlib").Path(__file__),
        "validation": __import__("pathlib").Path(__file__),
        "expected_workers": 1,
        "output": __import__("pathlib").Path(__file__).with_suffix(".json"),
        "sample_modulus": 100,
        "sample_buckets": 20,
        "input_partitions": 32,
        "rank": 16,
        "max_iter": 3,
        "reg_param": 0.1,
        "seed": 42,
    }
    values.update(changes)
    return argparse.Namespace(**values)


class SparkScalingProtocolTest(unittest.TestCase):
    def test_protocol_hash_is_stable(self):
        left = protocol(fixture())
        right = protocol(fixture())
        self.assertEqual(canonical_hash(left), canonical_hash(right))
        self.assertFalse(left["measurement"]["raw_ids_written"])

    def test_model_change_changes_protocol_hash(self):
        self.assertNotEqual(
            canonical_hash(protocol(fixture(rank=16))),
            canonical_hash(protocol(fixture(rank=32))),
        )

    def test_invalid_sample_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_args(fixture(sample_modulus=10, sample_buckets=11))

    def test_output_cannot_overwrite_input(self):
        with self.assertRaises(ValueError):
            validate_args(fixture(output=Path(__file__)))

    def test_aggregate_uses_median_speedup_and_quality_gate(self):
        def result(workers, fit):
            spec = {"version": "fixture-v1"}
            return {
                "schema_version": 1,
                "run_id": f"run-{workers}-{fit}",
                "protocol": spec,
                "protocol_sha256": aggregate_protocol_hash(spec),
                "input_aggregates": {"train_rows": 10, "validation_rows": 5},
                "topology": {
                    "id": f"{workers}w",
                    "master": "spark://127.0.0.1:7077",
                    "expected_workers": workers,
                    "observed_remote_executors": workers,
                    "worker_count_gate": True,
                },
                "quality": {"rmse": 1.0, "prediction_coverage": 0.8},
                "timing_seconds": {"als_fit": fit, "application_total": fit + 1},
                "safe_to_track": True,
            }

        measured = aggregate(
            [result(1, value) for value in (12, 10, 11)],
            [result(2, value) for value in (8, 7, 9)],
            minimum_speedup=1.2,
            maximum_rmse_difference=0.01,
        )
        self.assertTrue(measured["gate_passed"])
        self.assertEqual("LOCAL_SCALE_OUT_SUPPORTED", measured["decision"])

    def test_gate_failure_is_preserved(self):
        def result(workers, suffix, fit):
            spec = {"version": "fixture-v1"}
            return {
                "schema_version": 1,
                "run_id": f"run-{workers}-{suffix}",
                "protocol": spec,
                "protocol_sha256": aggregate_protocol_hash(spec),
                "input_aggregates": {"train_rows": 10, "validation_rows": 5},
                "topology": {
                    "id": f"{workers}w-{suffix}",
                    "master": "spark://127.0.0.1:7077",
                    "expected_workers": workers,
                    "observed_remote_executors": workers,
                    "worker_count_gate": True,
                },
                "quality": {"rmse": 1.0, "prediction_coverage": 0.8},
                "timing_seconds": {"als_fit": fit, "application_total": fit + 1},
                "safe_to_track": True,
            }

        measured = aggregate(
            [result(1, index, 10) for index in range(3)],
            [result(2, index, 9) for index in range(3)],
            minimum_speedup=1.2,
            maximum_rmse_difference=0.01,
        )
        self.assertFalse(measured["gate_passed"])
        self.assertEqual("NOT_JUSTIFIED_AT_MEASURED_SCALE", measured["decision"])
        self.assertEqual(2, gate_exit_code(measured))

    def test_load_rejects_duplicate_runs_and_extra_executors(self):
        spec = {"version": "fixture-v1"}
        base = {
            "schema_version": 1,
            "run_id": "duplicate",
            "protocol": spec,
            "protocol_sha256": aggregate_protocol_hash(spec),
            "input_aggregates": {"train_rows": 10, "validation_rows": 5},
            "topology": {
                "id": "duplicate",
                "master": "spark://127.0.0.1:7077",
                "expected_workers": 1,
                "observed_remote_executors": 2,
                "worker_count_gate": True,
            },
            "quality": {"rmse": 1.0, "prediction_coverage": 0.8},
            "timing_seconds": {"als_fit": 10.0, "application_total": 11.0},
            "safe_to_track": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(3):
                path = Path(directory) / f"run-{index}.json"
                path.write_text(json.dumps(base), encoding="utf-8")
                paths.append(path)
            with self.assertRaises(ValueError):
                load(paths, 1)


if __name__ == "__main__":
    unittest.main()
