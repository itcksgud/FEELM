from __future__ import annotations

import unittest

import numpy as np

from scripts.rec_ev_027_core import (
    analytic_random_top2,
    deterministic_order,
    full_history_q,
    profile_weights,
    rrf_scores,
    simultaneous_max_t,
    splitmix_bpr_pairs,
    user_equal_prior,
)


class RecEv027CoreTests(unittest.TestCase):
    def test_percentile_profile_uses_selected_eight_and_tau_five(self) -> None:
        hist = np.ones((2, 10), dtype=np.uint32)
        _, prior = user_equal_prior(hist)
        indices = [0, 1, 2, 3, 6, 7, 8, 9]
        weights = profile_weights(indices, prior, "PERCENTILE_MAGNITUDE")
        self.assertEqual((8,), weights.shape)
        self.assertTrue(np.all(np.diff(weights) > 0))
        np.testing.assert_array_equal(np.sign(weights), profile_weights(indices, prior, "BINARY_SIGN"))

    def test_full_history_q_is_midrank(self) -> None:
        hist = [1, 0, 0, 0, 0, 0, 0, 0, 0, 1]
        np.testing.assert_allclose(full_history_q([0, 9], hist), [0.25, 0.75])

    def test_splitmix_sampler_is_deterministic_bounded_and_unique(self) -> None:
        ids = np.arange(100, 140)
        one = splitmix_bpr_pairs(range(20), range(20, 40), item_ids=ids, track="T", fold="0", seed=17, epoch=0, user_key="u")
        two = splitmix_bpr_pairs(range(20), range(20, 40), item_ids=ids, track="T", fold="0", seed=17, epoch=0, user_key="u")
        self.assertEqual(one, two)
        self.assertEqual(16, len(one))
        self.assertEqual(16, len(set(one)))

    def test_tie_order_is_stable(self) -> None:
        kwargs = dict(phase="SCREEN", track="T", fold="0", model="M", encoding="P", user_key="u")
        np.testing.assert_array_equal(
            deterministic_order([3, 2, 1], [0.0, 0.0, 0.0], **kwargs),
            deterministic_order([3, 2, 1], [0.0, 0.0, 0.0], **kwargs),
        )

    def test_rrf_one_component_preserves_order(self) -> None:
        order = np.asarray([2, 0, 1])
        self.assertEqual(order.tolist(), np.argsort(-rrf_scores([order], 3)).tolist())

    def test_random_top2_exact(self) -> None:
        result = analytic_random_top2([0.1, 0.5, 0.9, 0.9])
        self.assertAlmostEqual(0.5, result["HARM20"])
        self.assertAlmostEqual(0.6, result["TOP2_MEAN_Q"])

    def test_max_t_uses_higher_quantile_and_zero_se(self) -> None:
        values = np.column_stack([np.arange(10, dtype=float), np.ones(10)])
        result = simultaneous_max_t(values, repeats=100, seed=7)
        self.assertEqual(result["point"][1], result["low"][1])
        self.assertEqual(result["point"][1], result["high"][1])


if __name__ == "__main__":
    unittest.main()
