import unittest

import pandas as pd

from gbt_zero_n_evaluate import (
    bootstrap_mean_ci,
    candidate_metrics,
    low_history_cohort,
    nested_history_curve,
    select_profile,
)


class GbtZeroNEvaluateTest(unittest.TestCase):
    def test_bootstrap_is_deterministic(self):
        values = pd.Series([-1.0, 0.0, 1.0]).to_numpy()
        self.assertEqual(bootstrap_mean_ci(values, 622, 100), bootstrap_mean_ci(values, 622, 100))

    def test_nested_curve_pairs_each_row_with_same_user_target_n0(self):
        frame = pd.DataFrame([
            {"uid": 1, "target_movie_id": 10, "prediction_at": 100, "n": 0, "n_bucket": "0",
             "squared_error": 4.0, "absolute_error": 2.0},
            {"uid": 1, "target_movie_id": 10, "prediction_at": 100, "n": 1, "n_bucket": "1",
             "squared_error": 1.0, "absolute_error": 1.0},
            {"uid": 2, "target_movie_id": 20, "prediction_at": 100, "n": 0, "n_bucket": "0",
             "squared_error": 1.0, "absolute_error": 1.0},
            {"uid": 2, "target_movie_id": 20, "prediction_at": 100, "n": 1, "n_bucket": "1",
             "squared_error": 4.0, "absolute_error": 2.0},
        ])
        result = nested_history_curve(frame)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["users"], 2)
        self.assertEqual(result[0]["baseline_user_macro_mse_on_same_cohort"], 2.5)
        self.assertEqual(result[0]["user_macro_mse"], 2.5)
        self.assertEqual(result[0]["user_macro_mse_delta_vs_n0"], 0.0)
        self.assertEqual(result[0]["improved_user_target_fraction_by_squared_error"], 0.5)

    def test_selection_rejects_profile_that_hurts_low_n(self):
        results = {
            "steady": {
                "validation_user_macro_mse": 1.0,
                "nested_history_paired_vs_n0": [
                    {"user_macro_mse_delta_vs_n0": -0.01},
                    {"user_macro_mse_delta_vs_n0": -0.02},
                ],
            },
            "unstable": {
                "validation_user_macro_mse": 0.9,
                "nested_history_paired_vs_n0": [
                    {"user_macro_mse_delta_vs_n0": 0.01},
                    {"user_macro_mse_delta_vs_n0": -0.04},
                ],
            },
        }
        config = {
            "profiles": ["steady", "unstable"],
            "selection_metric": "validation_user_macro_mse",
            "selection_gate": {
                "maximum_positive_user_macro_mse_delta": 1e-12,
                "require_strict_improvement": True,
                "minimum_strict_improvement": 1e-6,
            },
        }
        selected, gate = select_profile(results, config)
        self.assertEqual(selected, "steady")
        self.assertEqual(gate["profiles"]["unstable"]["status"], "FAIL")

    def test_candidate_metrics_keep_unknown_slots_in_rank(self):
        frame = pd.DataFrame([
            {"episode_id": "e", "uid": 1, "candidate_movie_id": 1, "prediction": 0.9,
             "label": None, "label_state": "UNKNOWN_SAMPLED", "is_target": False},
            {"episode_id": "e", "uid": 1, "candidate_movie_id": 2, "prediction": 0.8,
             "label": 5.0, "label_state": "POSITIVE_OBSERVED", "is_target": True},
            {"episode_id": "e", "uid": 1, "candidate_movie_id": 3, "prediction": 0.7,
             "label": 2.0, "label_state": "NEGATIVE_OBSERVED", "is_target": False},
        ])
        metrics, ranked = candidate_metrics(frame)
        self.assertEqual(metrics["unknown_slot_fraction_at_10"]["numerator"], 1)
        self.assertEqual(metrics["observed_positive_recall_at_10"]["value"], 1.0)
        self.assertEqual(int(ranked.iloc[0].model_rank), 1)

    def test_low_history_uses_only_full_history_variant(self):
        targets = pd.DataFrame([
            {"episode_id": "e0", "uid": 1, "target_movie_id": 10, "prediction_at": 100,
             "n": 0, "total_history_count": 3, "is_full_history": False,
             "squared_error": 4.0, "absolute_error": 2.0},
            {"episode_id": "e3", "uid": 1, "target_movie_id": 10, "prediction_at": 100,
             "n": 3, "total_history_count": 3, "is_full_history": True,
             "squared_error": 1.0, "absolute_error": 1.0},
        ])
        candidates = pd.DataFrame([
            {"episode_id": "e3", "uid": 1, "candidate_movie_id": 10, "prediction": 4.0,
             "label": 5.0, "label_state": "POSITIVE_OBSERVED", "is_target": True,
             "total_history_count": 3, "is_full_history": True},
            {"episode_id": "e3", "uid": 1, "candidate_movie_id": 11, "prediction": 3.0,
             "label": None, "label_state": "UNKNOWN_SAMPLED", "is_target": False,
             "total_history_count": 3, "is_full_history": True},
        ])
        result = low_history_cohort(targets, candidates)
        self.assertEqual(result[0]["total_history_bucket"], "3-4")
        self.assertEqual(result[0]["user_macro_mse"], 1.0)


if __name__ == "__main__":
    unittest.main()
