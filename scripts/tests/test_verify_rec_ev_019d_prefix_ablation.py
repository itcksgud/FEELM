from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from scripts.verify_rec_ev_019d_prefix_ablation import (
    bootstrap_paired,
    decision,
    deterministic_full_rescore_users,
    metrics_from_full_scores,
    metrics_from_top500,
)


class RecEv019dVerifierTests(unittest.TestCase):
    def test_top500_metric_recomputation(self) -> None:
        prediction = pd.DataFrame({
            "rank": [1, 2, 3],
            "movie_id": [10, 20, 30],
            "effective_score": [0.9, 0.8, 0.7],
        })
        future = [
            {"movie_id": 20, "midrank_utility": 1.0, "is_positive": True, "is_negative": False},
            {"movie_id": 10, "midrank_utility": 0.0, "is_positive": False, "is_negative": True},
        ]
        actual = metrics_from_top500(prediction, future, {10, 20, 30})
        self.assertAlmostEqual(actual["ndcg_at_10"], 1.0 / np.log2(3.0))
        self.assertEqual(actual["recall_at_10"], 1.0)
        self.assertEqual(actual["mrr_at_10"], 0.5)
        self.assertTrue(actual["harm_at_2"])

    def test_bootstrap_and_decision_are_deterministic(self) -> None:
        values = bootstrap_paired(
            np.asarray([0.01, 0.02, 0.03]),
            np.asarray([0.0, 0.0, 0.0]),
            iterations=100,
            seed=20260924,
        )
        self.assertEqual(values, bootstrap_paired(
            np.asarray([0.01, 0.02, 0.03]),
            np.asarray([0.0, 0.0, 0.0]),
            iterations=100,
            seed=20260924,
        ))
        self.assertEqual(decision(values)["status"], "PASS")

    def test_full_score_metrics_include_exact_positive_rank_percentile(self) -> None:
        candidate_ids = np.asarray([10, 20, 30, 40], dtype=np.int64)
        scores = np.asarray([0.5, 0.5, 0.4, -np.inf], dtype=np.float32)
        future = [
            {"movie_id": 20, "midrank_utility": 1.0, "is_positive": True, "is_negative": False},
            {"movie_id": 10, "midrank_utility": 0.0, "is_positive": False, "is_negative": True},
        ]
        metrics, top500, top501 = metrics_from_full_scores(
            candidate_ids,
            scores,
            future,
            top_candidates=3,
            fallback_user=False,
        )
        np.testing.assert_array_equal(top500, np.asarray([0, 1, 2]))
        np.testing.assert_array_equal(top501, np.asarray([0, 1, 2]))
        self.assertEqual(metrics["positive_mean_rank_percentile"], 0.5)
        self.assertTrue(metrics["harm_at_2"])

    def test_bounded_full_rescore_selection_is_deterministic(self) -> None:
        users = ["u3", "u1", "u2", "u4"]
        self.assertEqual(
            deterministic_full_rescore_users(users, 2),
            deterministic_full_rescore_users(list(reversed(users)), 2),
        )
        self.assertEqual(deterministic_full_rescore_users(users, "all"), sorted(users))


if __name__ == "__main__":
    unittest.main()
