from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from recommendation_baseline_calibration import (  # noqa: E402
    apply_isotonic,
    choose_later_boundary,
    fit_isotonic,
    fit_regularized_bias,
    identity_state,
    predict_bias,
    regression_metrics,
    sampled_ranking_metrics,
    score_factor_pairs,
)


class RecommendationBaselineTest(unittest.TestCase):
    def test_calibration_boundary_puts_ties_in_later_half(self) -> None:
        timestamps = np.array([10, 10, 20, 20, 30, 30, 40, 50])
        boundary = choose_later_boundary(timestamps, 0.5)
        earlier = timestamps[timestamps < boundary]
        later = timestamps[timestamps >= boundary]
        self.assertLess(int(earlier.max()), int(later.min()))
        self.assertFalse(bool((earlier == boundary).any()))
        self.assertTrue(bool((later == boundary).any()))

    def test_bias_model_uses_known_effects_and_falls_back(self) -> None:
        users = np.array([1, 1, 2, 2], dtype=np.int64)
        movies = np.array([1, 2, 1, 2], dtype=np.int64)
        ratings = np.array([5.0, 4.0, 2.0, 1.0])
        model = fit_regularized_bias(
            users,
            movies,
            ratings,
            user_size=4,
            movie_size=4,
            reg_user=0.1,
            reg_item=0.1,
            iterations=5,
        )
        predictions = predict_bias(
            np.array([1, 2, 3]),
            np.array([1, 2, 3]),
            float(model["global_mean"]),
            model["user_bias"],
            model["movie_bias"],
        )
        self.assertGreater(predictions[0], predictions[1])
        self.assertAlmostEqual(predictions[2], 3.0, places=6)

    def test_identity_state_covers_four_cases(self) -> None:
        user_counts = np.array([0, 2, 0])
        movie_counts = np.array([0, 3, 0])
        states = identity_state(
            np.array([1, 2, 1, 2]),
            np.array([1, 1, 2, 2]),
            user_counts,
            movie_counts,
        )
        self.assertEqual(
            states.tolist(),
            [
                "KNOWN_USER_KNOWN_ITEM",
                "NEW_USER_KNOWN_ITEM",
                "KNOWN_USER_NEW_ITEM",
                "NEW_USER_NEW_ITEM",
            ],
        )

    def test_isotonic_predictions_stay_on_star_scale(self) -> None:
        raw = np.array([-1.0, 1.0, 2.0, 4.0, 7.0])
        actual = np.array([0.5, 1.5, 2.5, 4.5, 5.0])
        model = fit_isotonic(raw, actual)
        predicted = apply_isotonic(model, np.array([-10.0, 3.0, 10.0]))
        self.assertTrue(bool((predicted >= 0.5).all()))
        self.assertTrue(bool((predicted <= 5.0).all()))
        self.assertTrue(bool((np.diff(predicted) >= 0).all()))

    def test_regression_metrics_report_partial_coverage(self) -> None:
        metrics = regression_metrics(
            np.array([1.0, 2.0, 3.0]),
            np.array([1.5, np.nan, 2.5]),
            expected_rows=3,
        )
        self.assertEqual(metrics["rows"], 2)
        self.assertAlmostEqual(float(metrics["coverage"]), 2 / 3, places=6)
        self.assertAlmostEqual(float(metrics["mae"]), 0.5)

    def test_sampled_ranking_uses_positive_rank(self) -> None:
        candidates = pd.DataFrame(
            {
                "user_id": [1, 1, 1, 2, 2, 2],
                "is_positive": [0, 1, 0, 0, 0, 1],
                "score": [0.2, 0.9, 0.1, 0.9, 0.8, 0.1],
            }
        )
        metrics = sampled_ranking_metrics(candidates, "score")
        self.assertEqual(metrics["users"], 2)
        self.assertEqual(metrics["hit_rate_at_10"], 1.0)
        self.assertEqual(metrics["median_rank"], 2.0)

    def test_exported_factor_dot_product_and_unknown_ids(self) -> None:
        scores = score_factor_pairs(
            np.array([1, 2, 9]),
            np.array([3, 4, 3]),
            np.array([1, 2]),
            np.array([[1.0, 2.0], [2.0, 1.0]], dtype=np.float32),
            np.array([3, 4]),
            np.array([[3.0, 4.0], [4.0, 3.0]], dtype=np.float32),
        )
        self.assertAlmostEqual(scores[0], 11.0)
        self.assertAlmostEqual(scores[1], 11.0)
        self.assertTrue(np.isnan(scores[2]))


if __name__ == "__main__":
    unittest.main()
