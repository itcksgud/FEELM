from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from recommendation_party_policy import (  # noqa: E402
    BalancedParameters,
    Party,
    evaluate_policy,
    policy_scores,
    relative_utility,
    reversal_examples,
    select_balanced_parameters,
    stable_top_indices,
)


class PartyPolicyTest(unittest.TestCase):
    def test_relative_utility_respects_each_members_rating_style(self) -> None:
        global_counts = np.full(10, 10, dtype=np.int64)
        lower_style = np.array([10, 10, 10, 10, 10, 1, 0, 0, 0, 0])
        higher_style = np.array([0, 0, 0, 0, 1, 10, 10, 10, 10, 10])

        lower_utility = relative_utility(
            np.array([3.0]), lower_style, global_counts, shrinkage=0.0
        )[0]
        higher_utility = relative_utility(
            np.array([3.0]), higher_style, global_counts, shrinkage=0.0
        )[0]

        self.assertGreater(lower_utility, higher_utility)

    def test_average_least_misery_most_happiness_and_balanced_are_distinct(self) -> None:
        utilities = np.array(
            [
                [0.95, 0.65, 0.20],
                [0.35, 0.62, 0.95],
            ]
        )
        candidate_ids = np.array([10, 20, 30])
        balanced = BalancedParameters(floor=0.5, floor_weight=2.0, gap_weight=0.1)

        average = stable_top_indices(policy_scores(utilities, "AVERAGE"), candidate_ids, 1)[0]
        least = stable_top_indices(policy_scores(utilities, "LEAST_MISERY"), candidate_ids, 1)[0]
        most = stable_top_indices(policy_scores(utilities, "MOST_HAPPINESS"), candidate_ids, 1)[0]
        compromise = stable_top_indices(
            policy_scores(utilities, "BALANCED", balanced), candidate_ids, 1
        )[0]

        self.assertEqual(int(average), 0)
        self.assertEqual(int(least), 1)
        self.assertEqual(int(most), 0)
        self.assertEqual(int(compromise), 1)

    def test_validation_parameter_selection_does_not_accept_test_input(self) -> None:
        party = self.party()
        selected, grid = select_balanced_parameters(
            [party],
            top_k=1,
            relevance_loss_budget=0.5,
            observed_mean_loss_budget=0.5,
        )

        self.assertIsInstance(selected, BalancedParameters)
        self.assertEqual(len(grid), 64)
        self.assertTrue(any(row["parameters"] == selected for row in grid))

    def test_public_reversal_examples_contain_no_raw_ids(self) -> None:
        party = self.party()
        examples = reversal_examples(
            [party], BalancedParameters(0.5, 2.0, 0.1), top_k=1
        )
        serialized = repr(examples)

        self.assertNotIn("user_id", serialized)
        self.assertNotIn("movie_id", serialized)
        self.assertNotIn("candidate_ids", serialized)
        self.assertIn("Synthetic title", serialized)

    @staticmethod
    def party() -> Party:
        predicted = np.array([[0.95, 0.65, 0.20], [0.35, 0.62, 0.95]])
        actual = np.array([[0.80, 0.70, 0.20], [0.20, 0.70, 0.90]])
        ratings = np.array([[5.0, 3.0, 1.0], [3.5, 4.5, 5.0]])
        return Party(
            label="TEST-S2-MID-001",
            split="validation",
            group_size=2,
            group_type="MIDDLE",
            similarity=0.5,
            candidate_count=3,
            candidate_ids=np.array([10, 20, 30]),
            titles=["Synthetic title A", "Synthetic title B", "Synthetic title C"],
            predicted_utility=predicted,
            actual_utility=actual,
            actual_ratings=ratings,
        )


if __name__ == "__main__":
    unittest.main()
