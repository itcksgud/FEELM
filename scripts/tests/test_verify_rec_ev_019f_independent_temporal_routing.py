from __future__ import annotations

import unittest

import numpy as np

from scripts.verify_rec_ev_019f_independent_temporal_routing import (
    base_train_midrank,
    decision,
    selected_users,
    top_indices,
)


class RecEv019fVerifierTest(unittest.TestCase):
    def test_base_train_midrank_uses_locked_counts(self) -> None:
        manifest = {"splits": {"train": {"rating_value_counts": {str(float(value)): 1 for value in np.arange(0.5, 5.01, 0.5)}}}}
        np.testing.assert_allclose(base_train_midrank(manifest), np.arange(0.05, 1.0, 0.1))

    def test_independent_decision_is_harm_first(self) -> None:
        actual = decision({"harm_one_sided_95_upper": 0.006, "ndcg_mean": 0.02, "ndcg_two_sided_95": [0.01, 0.03]})
        self.assertEqual(actual["status"], "FAIL")

    def test_full_rescore_user_selection_is_deterministic(self) -> None:
        users = ["c", "a", "b"]
        self.assertEqual(selected_users(users, 2), selected_users(list(reversed(users)), 2))
        self.assertEqual(selected_users(users, "all"), ["a", "b", "c"])

    def test_independent_tie_break(self) -> None:
        movies = np.asarray([20, 10, 30])
        scores = np.asarray([1.0, 1.0, 0.5])
        order = top_indices(movies, scores, 3)
        self.assertEqual(movies[order].tolist(), [10, 20, 30])


if __name__ == "__main__":
    unittest.main()
