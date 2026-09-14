import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hybrid345_catalog as catalog
import hybrid345_evaluate as evaluator


class CandidateAxisTests(unittest.TestCase):
    def test_missing_evaluation_seal_fails_before_work(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "completed evaluation seal"):
                catalog.verify_evaluation_gate(Path(directory), catalog.DEFAULT_CONFIG)

    def test_release_and_seen_exclusion_is_exact(self):
        released = np.array([True, False, True, True, False, True])
        expected = np.array([0, 3, 5])
        actual = catalog.verify_candidate_axis(expected, released, [2])
        np.testing.assert_array_equal(actual, expected)

    def test_candidate_axis_drift_fails(self):
        with self.assertRaises(RuntimeError):
            catalog.verify_candidate_axis(np.array([0]), np.array([True, True, True]), [1])


class PolicyTests(unittest.TestCase):
    def test_router_uses_warm_then_cold_then_fallback_without_als_fill(self):
        out = catalog.combine_policy_scores(
            actual_support=np.array([True, False, True, False]),
            user_factor_available=True,
            als_raw=np.array([1.0, np.nan, np.nan, np.nan]),
            qwen_raw=np.array([3.0, 4.0, 5.0, np.nan]),
            cold_raw=np.array([10.0, 20.0, 30.0, np.nan]),
            fallback_raw=np.array([100.0, 200.0, 300.0, 400.0]),
            als_fit=(0.0, 1.0), qwen_fit=(0.0, 1.0),
            cold_fit=(0.0, 1.0), fallback_fit=(0.0, 1.0), content_weight=0.25,
        )
        np.testing.assert_allclose(out["ALS_QWEN"], [1.5, np.nan, np.nan, np.nan], equal_nan=True)
        np.testing.assert_allclose(out["SELECTED_COLD_HEAD"], [10, 20, 30, np.nan], equal_nan=True)
        np.testing.assert_allclose(out["ROUTER_s339"], [1.5, 20, 300, 400])

    def test_router_without_user_factor_uses_only_fallback(self):
        out = catalog.combine_policy_scores(
            actual_support=np.array([True, False]), user_factor_available=False,
            als_raw=np.array([1.0, np.nan]), qwen_raw=np.array([2.0, 3.0]),
            cold_raw=np.array([4.0, 5.0]), fallback_raw=np.array([6.0, 7.0]),
            als_fit=(0, 1), qwen_fit=(0, 1), cold_fit=(0, 1), fallback_fit=(0, 1),
            content_weight=0.25,
        )
        np.testing.assert_allclose(out["ROUTER_s339"], [6.0, 7.0])

    def test_zero_weight_warm_path_does_not_require_qwen(self):
        out = catalog.combine_policy_scores(
            actual_support=np.array([True]), user_factor_available=True,
            als_raw=np.array([4.2]), qwen_raw=np.array([np.nan]),
            cold_raw=np.array([3.0]), fallback_raw=np.array([3.5]),
            als_fit=(0, 1), qwen_fit=(0, 1), cold_fit=(0, 1), fallback_fit=(0, 1),
            content_weight=0.0,
        )
        self.assertEqual(out["ALS_QWEN"][0], 4.2)
        self.assertEqual(out["ROUTER_s339"][0], 4.2)

    def test_router_matches_evaluator_item_boundaries(self):
        actual = np.array([True, False, True, False])
        als = np.array([1.0, np.nan, 3.0, np.nan])
        qwen = np.array([2.0, 4.0, 6.0, 8.0])
        cold = np.array([10.0, 20.0, 30.0, np.nan])
        fallback = np.array([100.0, 200.0, 300.0, 400.0])
        ours = catalog.combine_policy_scores(
            actual_support=actual, user_factor_available=True,
            als_raw=als, qwen_raw=qwen, cold_raw=cold, fallback_raw=fallback,
            als_fit=(0, 1), qwen_fit=(0, 1), cold_fit=(0, 1), fallback_fit=(0, 1),
            content_weight=0.25,
        )["ROUTER_s339"]
        row_axis = pd.DataFrame({"h": [10] * 4,
                                 "primary_group": np.where(actual, "W_DIRECT", "C")})
        raw = {"ALS": als.copy(), "QWEN_DIRECT": qwen.copy(),
               "ALS_C2F": np.ones(4), "GBT120_s339": fallback.copy(),
               "STRUCTURED_DIRECT": cold.copy()}
        available = {name: np.isfinite(values) for name, values in raw.items()}
        _raw, _available, scores = evaluator.add_derived_models(
            row_axis, raw, available, raw,
            {"warm": {"content_weight": 0.25},
             "cold": {"head": "STRUCTURED_DIRECT"}},
        )
        np.testing.assert_allclose(ours, scores["ROUTER_s339"])

    def test_ranking_is_score_then_movie_id(self):
        result = catalog.rank_top(np.array([2.0, 2.0, np.nan, 1.0]),
                                  np.array([9, 3, 1, 2]), 3)
        np.testing.assert_array_equal(result, [1, 0, 3])


class SummaryTests(unittest.TestCase):
    def test_top2_top4_top6_denominators_and_unknown(self):
        rows = []
        for model in catalog.OUTPUT_MODELS:
            for uid in (1, 2):
                for rank in range(1, 7):
                    rows.append({"uid": uid, "model": model, "rank": rank,
                                 "movie_id": rank if uid == 1 else rank + 10,
                                 "unknown": rank % 2 == 0, "support": rank - 1,
                                 "actual_als_supported": rank > 1, "blocked": False})
        summary = catalog.summarize_top(pd.DataFrame(rows), users=2, eligible_catalog_movies=20)
        row = summary[(summary.model == "QWEN_DIRECT") & (summary.top_n == 2)].iloc[0]
        self.assertEqual(row.returned, 4)
        self.assertEqual(row.unknown, 2)
        self.assertEqual(row.unique_movies, 4)
        self.assertAlmostEqual(row.catalog_coverage, 0.2)
        self.assertAlmostEqual(row.hhi, 0.25)


if __name__ == "__main__":
    unittest.main()
