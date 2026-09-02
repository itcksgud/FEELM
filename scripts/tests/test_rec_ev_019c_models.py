from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from scipy import sparse


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from rec_ev_019c_models import (
    bayesian_rating_scores,
    build_item_neighbor_columns,
    effective_with_b0_fallback,
    fold_in_bpr_user,
    fold_in_logistic_user,
    item_knn_scores,
    percentile_scores,
    signed_dense_profile_scores,
    signed_sparse_profile_scores,
    train_bpr_minibatch,
)


class RecEv019CModelsTest(unittest.TestCase):
    def test_bayesian_and_percentile_fallback(self) -> None:
        b0 = bayesian_rating_scores(np.asarray([10, 1]), np.asarray([5.0, 1.0]), global_mean=3.0, prior_strength=10)
        self.assertGreater(b0[0], b0[1])
        b0p = percentile_scores(b0, np.asarray([True, True]))
        effective, fallback = effective_with_b0_fallback(
            np.asarray([0.2, np.nan]), np.asarray([True, False]), b0p
        )
        self.assertEqual([False, True], fallback.tolist())
        self.assertTrue(np.isfinite(effective).all())

    def test_sparse_and_dense_profiles_need_both_observed_classes(self) -> None:
        matrix = sparse.csr_matrix(np.eye(3, dtype=np.float32))
        scores, available, fallback = signed_sparse_profile_scores(matrix, [0, 1], [1, -1])
        self.assertFalse(fallback)
        self.assertGreater(scores[0], scores[1])
        self.assertTrue(available.all())
        dense_scores, _, dense_fallback = signed_dense_profile_scores(
            np.eye(3, dtype=np.float32), np.asarray([True, True, True]), [0, 1], [1, -1]
        )
        self.assertFalse(dense_fallback)
        self.assertGreater(dense_scores[0], dense_scores[1])

    def test_item_knn_uses_signed_observed_rows(self) -> None:
        matrix = sparse.csr_matrix(np.asarray([
            [1, 1, -1, 0],
            [1, 1, -1, 0],
            [-1, -1, 1, 1],
        ], dtype=np.int8))
        columns = build_item_neighbor_columns(matrix, [0])
        scores, available, fallback = item_knn_scores(
            columns, [0], [1], candidate_count=4, neighbors=3, shrink=0
        )
        self.assertFalse(fallback)
        self.assertTrue(available[1])
        self.assertGreater(scores[1], 0)

    def test_item_knn_batched_pruning_preserves_each_trial_top_neighbor(self) -> None:
        matrix = sparse.csr_matrix(np.asarray([
            [1, 1, 1, -1], [1, 1, -1, -1], [1, -1, 1, -1], [-1, -1, -1, 1],
        ], dtype=np.int8))
        full = build_item_neighbor_columns(matrix, [0])
        pruned = build_item_neighbor_columns(
            matrix, [0], maximum_neighbors=1, shrink_values=[0, 10], anchor_batch_size=1
        )
        self.assertLessEqual(len(pruned[0].candidate_positions), 2)
        for shrink in (0, 10):
            full_scores, _, _ = item_knn_scores(full, [0], [1], candidate_count=4, neighbors=1, shrink=shrink)
            pruned_scores, _, _ = item_knn_scores(pruned, [0], [1], candidate_count=4, neighbors=1, shrink=shrink)
            self.assertEqual(int(np.argmax(full_scores)), int(np.argmax(pruned_scores)))

    def test_bpr_and_fold_in_rank_observed_like_above_dislike(self) -> None:
        matrix = sparse.csr_matrix(np.asarray([[1, -1], [1, -1]], dtype=np.int8))
        factors = train_bpr_minibatch(
            matrix, ["a", "b"], factors=4, regularization=0.001, epochs=20,
            learning_rate=0.1, seed=17, maximum_pairs_per_user_epoch=1, batch_size=2,
        )
        user, fallback = fold_in_bpr_user(
            factors.item_factors, [0], [1], regularization=0.001, learning_rate=0.1
        )
        self.assertFalse(fallback)
        scores = factors.item_factors @ user
        self.assertGreater(scores[0], scores[1])

    def test_logistic_fold_in_ranks_observed_like_above_dislike(self) -> None:
        biases = np.zeros(2, dtype=np.float32)
        items = np.asarray([[1.0, 0.0], [-1.0, 0.0]], dtype=np.float32)
        bias, user, fallback = fold_in_logistic_user(
            biases, items, [0, 1], [1, -1], regularization=0.001, learning_rate=0.05
        )
        self.assertFalse(fallback)
        scores = bias + biases + items @ user
        self.assertGreater(scores[0], scores[1])


if __name__ == "__main__":
    unittest.main()
