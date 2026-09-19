import unittest

from gbt_zero_n_completion_verify import promotion_decision


class CompletionDecisionTest(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "require_validation_selection_gate": True,
            "require_all_seeds_nested_non_worse": True,
            "require_pairwise_rank_positive_ci": True,
        }
        self.validation = {
            "selected_profile_on_validation": "history_aggregate",
            "selection_gate": {"eligible_profiles": ["history_aggregate"]},
            "profiles": {"history_aggregate": {
                "low_history_cohort": [
                    {"total_history_bucket": "1", "users": 30},
                    {"total_history_bucket": "2", "users": 31},
                ],
                "candidate_metrics": {"unknown_slot_fraction_at_10": {"value": 0.5}},
            }},
        }
        self.sensitivity = {
            "all_seeds_pass_nested_point_estimate_gate": True,
            "user_macro_mse_range": [0.9, 0.91],
            "user_macro_mse_span": 0.01,
        }
        self.rank = {
            "paired_user_macro_ndcg_at_10_delta": 0.02,
            "paired_user_macro_ndcg_at_10_delta_bootstrap_95_ci": [0.01, 0.03],
        }

    def test_promotes_only_when_all_evidence_gates_pass(self):
        result = promotion_decision(self.validation, self.sensitivity, self.rank, self.policy)
        self.assertEqual("PROMOTE_TO_FINAL_TEST", result["decision"])

    def test_rejects_unstable_seed_and_worse_rank(self):
        self.sensitivity["all_seeds_pass_nested_point_estimate_gate"] = False
        self.rank["paired_user_macro_ndcg_at_10_delta_bootstrap_95_ci"] = [-0.03, -0.01]
        result = promotion_decision(self.validation, self.sensitivity, self.rank, self.policy)
        self.assertEqual("DO_NOT_PROMOTE", result["decision"])
        self.assertFalse(result["gates"]["all_seeds_nested_non_worse"])
        self.assertFalse(result["gates"]["pairwise_rank_positive_ci"])
        self.assertEqual(2, len(result["limitations"]))


if __name__ == "__main__":
    unittest.main()
