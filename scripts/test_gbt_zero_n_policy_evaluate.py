import unittest

import numpy as np
import pandas as pd

from gbt_zero_n_policy_evaluate import holm_adjust, policy_scores


class GbtZeroNPolicyEvaluateTest(unittest.TestCase):
    def test_switch_uses_gbt_at_exact_threshold(self):
        frame = pd.DataFrame({
            "candidate_movie_id": [1, 2, 3, 4],
            "prediction": [0.1, 0.2, 0.3, 0.4],
            "n": [4, 5, 9, 10],
        })
        popularity = pd.DataFrame({
            "count_score": [4.0, 3.0, 2.0, 1.0],
            "bayes_score": [3.5, 3.4, 3.3, 3.2],
        }, index=[1, 2, 3, 4])
        scores = policy_scores(frame, popularity, 3.0)
        np.testing.assert_allclose(scores["COUNT_TO_GBT_K5"], [4.0, 0.2, 0.3, 0.4])
        np.testing.assert_allclose(scores["BAYES_TO_GBT_K10"], [3.5, 3.4, 3.3, 0.4])

    def test_holm_adjustment_is_monotone_in_sorted_order(self):
        adjusted = holm_adjust({"a": 0.01, "b": 0.03, "c": 0.2})
        self.assertAlmostEqual(adjusted["a"], 0.03)
        self.assertAlmostEqual(adjusted["b"], 0.06)
        self.assertAlmostEqual(adjusted["c"], 0.2)

    def test_embedded_asof_popularity_takes_precedence(self):
        frame = pd.DataFrame({
            "candidate_movie_id": [1, 2],
            "prediction": [0.1, 0.2],
            "n": [0, 5],
            "policy.popular_count_score": [7.0, 6.0],
            "policy.popular_bayes_score": [4.1, 3.9],
        })
        scores = policy_scores(frame)
        np.testing.assert_allclose(scores["POPULAR_COUNT"], [7.0, 6.0])
        np.testing.assert_allclose(scores["COUNT_TO_GBT_K5"], [7.0, 0.2])


if __name__ == "__main__":
    unittest.main()
