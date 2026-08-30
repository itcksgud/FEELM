from __future__ import annotations

import hashlib
import json
import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np

from feelm_recommender.benchmark import (
    BENCHMARK_VERSION,
    CANDIDATE_COUNTS,
    RATING_K_VALUES,
    PINNED_HTTPX_VERSION,
    _percentile,
    run_benchmark,
    write_result,
)


class RecEv007BenchmarkTest(unittest.TestCase):
    def test_nearest_rank_percentiles_are_fixed_before_measurement(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(_percentile(values, 0.50), 3.0)
        self.assertEqual(_percentile(values, 0.95), 5.0)
        self.assertEqual(_percentile(values, 0.99), 5.0)
        with self.assertRaises(ValueError):
            _percentile([], 0.95)

    def test_quick_loopback_benchmark_separates_active_serving_and_fold_in_core(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            factor_path = root / "factors.npz"
            item_ids = np.arange(1, 1201, dtype=np.int64)
            factors = np.stack(
                [np.sin(item_ids / (index + 1)) for index in range(8)], axis=1
            ).astype(np.float32)
            np.savez(factor_path, movie_ids=item_ids, movie_factors=factors)
            checksum = hashlib.sha256(factor_path.read_bytes()).hexdigest()
            factor_manifest = root / "factor-manifest.json"
            factor_manifest.write_text(
                json.dumps(
                    {
                        "evidence_id": "REC-EV-003",
                        "model": {"als": {"reg_param": 0.1}},
                        "artifacts": {
                            "cohort_excluded_item_factors": {"sha256": checksum}
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_benchmark(
                factor_path=factor_path,
                factor_manifest_path=factor_manifest,
                warmup=0,
                serving_iterations=2,
                concurrent_iterations=2,
                reload_iterations=2,
                fold_in_iterations=2,
            )

            self.assertEqual(result["benchmark_version"], BENCHMARK_VERSION)
            self.assertEqual(result["environment"]["packages"]["httpx"], PINNED_HTTPX_VERSION)
            self.assertEqual(result["serving_http"]["ranking_alpha"], 0.0)
            self.assertEqual(result["serving_http"]["star_policy"], "DISABLED")
            self.assertEqual(
                len(result["serving_http"]["sequential"]),
                len(CANDIDATE_COUNTS) * len(RATING_K_VALUES),
            )
            self.assertTrue(all(result["gate_results"].values()))
            self.assertEqual(
                result["technical_recommendation"]["expected_star_activation"],
                "PROHIBITED_BY_DN_C2_008",
            )
            self.assertTrue(
                result["artifact_lifecycle"]["invalid_reload"]["previous_ready_retained"]
            )
            self.assertEqual(
                result["inactive_fold_in_core"]["warning"],
                "DIAGNOSTIC_ONLY_NOT_ACTIVE_RANKING_OR_PRODUCT_STAR",
            )

            serialized = json.dumps(result, sort_keys=True)
            self.assertNotIn("test-c2-service-token", serialized)
            self.assertNotIn("10000000-0000-0000-0000-000000000007", serialized)
            self.assertNotIn(str(factor_path), serialized)
            self.assertNotIn("movieId", serialized)

            result_path = root / "result.json"
            manifest_path = root / "manifest.json"
            write_result(result, result_path, manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["result_file"], "result.json")
            self.assertEqual(
                manifest["result_sha256"], hashlib.sha256(result_path.read_bytes()).hexdigest()
            )
            self.assertFalse(manifest["privacy"]["raw_user_ids_persisted"])
            self.assertFalse(manifest["privacy"]["raw_movie_ids_persisted"])
            self.assertIn("CHECKSUM_CHANGES_BY_DESIGN", manifest["result_checksum_policy"])
            self.assertIn("generated_at", manifest["protocol_hash_excludes"])
            self.assertNotIn(str(factor_path), json.dumps(manifest, sort_keys=True))
            self.assertNotIn("path", manifest["source_factor_evidence"])

            later = copy.deepcopy(result)
            later["generated_at"] = "2099-01-01T00:00:00Z"
            later_result_path = root / "later-result.json"
            later_manifest_path = root / "later-manifest.json"
            write_result(later, later_result_path, later_manifest_path)
            later_manifest = json.loads(later_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["protocol_sha256"], later_manifest["protocol_sha256"])
            self.assertNotEqual(manifest["result_sha256"], later_manifest["result_sha256"])


if __name__ == "__main__":
    unittest.main()
