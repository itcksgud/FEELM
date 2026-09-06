from __future__ import annotations

import unittest

import numpy as np
from scipy import sparse

from scripts.rec_ev_028b_core import (
    ItemEstimand,
    benefit,
    classify_signal,
    item_multiway_multiplier_max_t,
    max_t_intervals,
    user_multiplier_max_t,
)


class RecEv028BCoreTests(unittest.TestCase):
    def test_benefit_orientation(self) -> None:
        model = np.asarray([0.1, 0.8])
        comparator = np.asarray([0.3, 0.5])
        np.testing.assert_allclose(benefit(model, comparator, "HARM20"), [0.2, -0.3])
        np.testing.assert_allclose(benefit(model, comparator, "TOP2_MEAN_Q"), [-0.2, 0.3])

    def test_max_t_zero_se_is_point_interval(self) -> None:
        boot = np.asarray([[1.0, 2.0], [1.0, 4.0], [1.0, 3.0]])
        result = max_t_intervals(boot, np.asarray([1.0, 3.0]), 0.95)
        self.assertEqual(float(result["low"][0]), 1.0)
        self.assertEqual(float(result["high"][0]), 1.0)

    def test_user_multiplier_point_and_reproducibility(self) -> None:
        matrices = {
            "A": (["a", "b"], np.asarray([[1.0, 2.0], [3.0, 4.0]])),
            "B": (["b", "c"], np.asarray([[2.0, 4.0], [6.0, 8.0]])),
        }
        first = user_multiplier_max_t(
            union_users=["a", "b", "c"], matrices=matrices, repeats=20, seed=7, confidence=0.95, batch_size=6
        )
        second = user_multiplier_max_t(
            union_users=["a", "b", "c"], matrices=matrices, repeats=20, seed=7, confidence=0.95, batch_size=6
        )
        np.testing.assert_allclose(first["point"], [2.0, 3.0, 4.0, 6.0])
        np.testing.assert_array_equal(first["estimates"], second["estimates"])

    def test_item_multiway_point_and_transform(self) -> None:
        # movie 10 has two occurrences and movie 20 has one.  Dividing each
        # occurrence by N_i makes the all-one point an equal-movie mean.
        denominator = sparse.csr_matrix(np.asarray([[0.5, 1.0], [0.5, 0.0]]))
        contribution = sparse.csr_matrix(np.asarray([[0.5, 3.0], [1.5, 0.0]]))
        value = ItemEstimand(("a", "b"), (10, 20), denominator, (contribution,))
        result = item_multiway_multiplier_max_t(
            union_users=["a", "b"],
            union_movies=[10, 20],
            estimands={"A": value},
            repeats=20,
            seed=11,
            confidence=0.95,
            linear_transform=np.asarray([[2.0]]),
            batch_size=7,
        )
        # Within movie means are 2 and 3; equal-movie mean 2.5, then ×2.
        self.assertAlmostEqual(float(result["point"][0]), 5.0)
        self.assertEqual(np.asarray(result["estimates"]).shape, (20, 1))

    def test_attributed_truth_requires_user_and_item_personalization(self) -> None:
        systems = ["FULL_PERSONALIZED", "BIAS_ONLY", "PROFILE_SHUFFLE", "STRUCTURED_DIRECT"]
        active = {system: 1.0 for system in systems}
        user = {}
        for model in systems:
            user[(model, "RANDOM_EXPECTATION", "HARM20")] = (0.0, 0.1)
            user[(model, "RANDOM_EXPECTATION", "TOP2_MEAN_Q")] = (0.01, 0.1)
            user[(model, "RANDOM_EXPECTATION", "TOP2_MIN_Q")] = (0.01, 0.1)
        for comparator in ("BIAS_ONLY", "PROFILE_SHUFFLE", "STRUCTURED_DIRECT"):
            user[("FULL_PERSONALIZED", comparator, "HARM20")] = (0.0, 0.1)
            user[("FULL_PERSONALIZED", comparator, "TOP2_MEAN_Q")] = (0.01, 0.1)
            user[("FULL_PERSONALIZED", comparator, "TOP2_MIN_Q")] = (0.01, 0.1)
        item = {}
        for model in systems:
            item[(model, "RANDOM_EXPECTATION", "ITEM_MACRO_UTILITY_CONTRIBUTION")] = (0.01, 0.1)
            item[(model, "RANDOM_EXPECTATION", "ITEM_MACRO_LOW_SLOT_CONTRIBUTION")] = (0.0, 0.1)
        for comparator in ("BIAS_ONLY", "PROFILE_SHUFFLE", "STRUCTURED_DIRECT"):
            item[("FULL_PERSONALIZED", comparator, "ITEM_MACRO_UTILITY_CONTRIBUTION")] = (0.01, 0.1)
            item[("FULL_PERSONALIZED", comparator, "ITEM_MACRO_LOW_SLOT_CONTRIBUTION")] = (0.0, 0.1)
        self.assertEqual(
            classify_signal(active_rates=active, user_bounds=user, item_bounds=item),
            "ATTRIBUTED_PERSONALIZED_CONTENT_SIGNAL",
        )


if __name__ == "__main__":
    unittest.main()
