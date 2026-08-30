from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from recommendation_cold_start_curve import (  # noqa: E402
    bootstrap_mean_difference,
    build_dense_item_factors,
    first_k_events,
    fold_in_factors,
    score_fold_in,
    select_cohort,
)


class ColdStartCurveTest(unittest.TestCase):
    def test_select_cohort_requires_both_future_halves_and_history(self) -> None:
        validation = pd.DataFrame(
            {
                "user_id": [1, 1, 2, 2, 3, 3],
                "timestamp": [10, 30, 10, 15, 30, 40],
                "identity_state": ["KNOWN_USER_KNOWN_ITEM"] * 6,
            }
        )
        profiles = pd.DataFrame(
            {
                "user_id": [1, 2, 3],
                "rating_count": [20, 20, 19],
                "rating_mean": [3.0, 3.5, 4.0],
                "rating_std": [1.0, 1.0, 1.0],
                "rating_mean_quartile": ["Q1", "Q2", "Q3"],
                "train_history_bucket": ["K20-49"] * 3,
            }
        )
        cohort = select_cohort(validation, profiles, 20, 20)
        self.assertEqual(cohort["user_id"].tolist(), [1])

    def test_first_k_events_uses_time_then_movie_order(self) -> None:
        train = pd.DataFrame(
            {
                "user_id": [1, 1, 1, 1],
                "movie_id": [9, 3, 2, 1],
                "rating": [2.0, 3.0, 4.0, 5.0],
                "timestamp": [30, 10, 10, 20],
            }
        )
        result = first_k_events(train, np.array([1]), 3)
        self.assertEqual(result["movie_id"].tolist(), [2, 3, 1])
        self.assertEqual(result["onboarding_order"].tolist(), [1, 2, 3])

    def test_fold_in_solves_regularized_factor_and_scores(self) -> None:
        item_ids = np.array([1, 2])
        item_values = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        dense = build_dense_item_factors(item_ids, item_values)
        onboarding = pd.DataFrame(
            {
                "user_id": [7, 7],
                "movie_id": [1, 2],
                "rating": [4.0, 2.0],
                "onboarding_order": [1, 2],
            }
        )
        factors, counts = fold_in_factors(
            np.array([7]), onboarding, dense, 2, reg_param=0.5
        )
        # A = I + (0.5 * 2)I = 2I, therefore x = [2, 1].
        np.testing.assert_allclose(factors[0], np.array([2.0, 1.0]))
        self.assertEqual(counts.tolist(), [2])
        scores, direct = score_fold_in(
            np.array([7, 8]),
            np.array([1, 1]),
            np.array([7]),
            factors,
            dense,
        )
        self.assertAlmostEqual(scores[0], 2.0)
        self.assertTrue(direct[0])
        self.assertTrue(np.isnan(scores[1]))
        self.assertFalse(direct[1])

    def test_paired_bootstrap_reports_candidate_minus_baseline(self) -> None:
        candidate = pd.Series([0.4, 0.5, 0.6], index=[1, 2, 3])
        baseline = pd.Series([0.5, 0.6, 0.7], index=[1, 2, 3])
        result = bootstrap_mean_difference(candidate, baseline, 100, 42)
        self.assertEqual(result["users"], 3)
        self.assertAlmostEqual(result["mean_difference"], -0.1)
        self.assertLess(float(result["ci95_high"]), 0)


if __name__ == "__main__":
    unittest.main()
