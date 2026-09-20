import unittest

import numpy as np
import pandas as pd

from fm_zero_n_evaluate import bootstrap_ci, history_bucket, nested_history


class EvaluationTest(unittest.TestCase):
    def test_history_buckets(self):
        expected = {0: "K0", 1: "K1", 2: "K2", 3: "K3_4", 9: "K5_9", 19: "K10_19",
                    29: "K20_29", 49: "K30_49", 50: "K50_PLUS"}
        self.assertEqual({value: history_bucket(value) for value in expected}, expected)

    def test_nested_history_is_same_target_paired(self):
        target = pd.DataFrame([
            {"episode_id": "a0", "uid": 1, "target_movie_id": 10, "prediction_at": 1, "n": 0, "n_bucket": "0", "squared_error": 1.0, "absolute_error": 1.0},
            {"episode_id": "a1", "uid": 1, "target_movie_id": 10, "prediction_at": 1, "n": 1, "n_bucket": "1", "squared_error": 0.5, "absolute_error": 0.7},
            {"episode_id": "b0", "uid": 2, "target_movie_id": 20, "prediction_at": 1, "n": 0, "n_bucket": "0", "squared_error": 0.2, "absolute_error": 0.4},
            {"episode_id": "b1", "uid": 2, "target_movie_id": 20, "prediction_at": 1, "n": 1, "n_bucket": "1", "squared_error": 0.3, "absolute_error": 0.5},
        ])
        ranks = pd.DataFrame([{"episode_id": key, "observed_ndcg_at_10": value}
                              for key, value in (("a0", 0.5), ("a1", 0.6), ("b0", 0.4), ("b1", 0.3))])
        result = nested_history(target, ranks, 1, 100)[0]
        self.assertAlmostEqual(result["mean_mse_delta_vs_same_target_n0"], -0.2)
        self.assertAlmostEqual(result["worsened_user_fraction_by_mse"], 0.5)

    def test_bootstrap_requires_sample(self):
        self.assertEqual(bootstrap_ci(np.asarray([1.0]), 1, 10), "INSUFFICIENT_SAMPLE")


if __name__ == "__main__":
    unittest.main()
