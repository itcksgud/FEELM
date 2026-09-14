"""Independent algebra/role/small-input tests for REC045."""

import unittest

import numpy as np
import pandas as pd
from scipy import sparse

from feelm_joint_preferences import (
    BLOCKS,
    categorical,
    input_selection,
    predict_user,
    shared_fit,
    training_statistics,
    unique_user_means,
)


class JointTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(5)
        self.matrices = {
            name: sparse.csr_matrix(rng.normal(size=(8, 3)))
            for name in (*BLOCKS, "PERSON_ERA")
        }
        self.oi = np.array([0, 1, 2])
        self.ei = np.array([3, 4, 5, 6, 7])
        self.ratings = np.array([2.0, 3.5, 5.0])
        self.b = np.array([3.0, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7])
        self.c = np.arange(8) * 0.02

    def test_every_ablation_matches_primal_independent_svd(self):
        pred, _ = predict_user(
            self.matrices, self.b, self.c, self.oi, self.ratings, self.ei, True
        )
        g = self.b + self.c
        offset = (self.ratings - g[self.oi]).mean()
        residual = self.ratings - g[self.oi] - offset
        selections = (
            [["GENRE"], list(BLOCKS)]
            + [[b for b in BLOCKS if b != removed] for removed in BLOCKS]
            + [[*BLOCKS, "PERSON_ERA"]]
        )
        for col, names in enumerate(selections, 2):
            x = np.hstack([self.matrices[name].toarray() for name in names])
            u, s, vt = np.linalg.svd(x[self.oi], full_matrices=False)
            beta = vt.T @ ((s / (s**2 + 5)) * (u.T @ residual))
            np.testing.assert_allclose(
                pred[:, col], g[self.ei] + offset + x[self.ei] @ beta, atol=1e-12
            )

    def test_only_selected_input_affects_offset(self):
        a, _ = predict_user(
            self.matrices, self.b, self.c, self.oi[:2], self.ratings[:2], self.ei
        )
        ratings = self.ratings.copy()
        ratings[2] = 0.5
        b, _ = predict_user(
            self.matrices, self.b, self.c, self.oi[:2], ratings[:2], self.ei
        )
        np.testing.assert_array_equal(a, b)
        np.testing.assert_allclose(
            a[:, 0], self.b[self.ei] + (ratings[:2] - self.b[self.oi[:2]]).mean()
        )

    def test_selection_nested_and_permutation_invariant(self):
        movies = np.arange(30) + 100
        five = input_selection("key", movies, 5, 1)
        ten = input_selection("key", movies, 10, 1)
        np.testing.assert_array_equal(five, ten[:5])
        np.testing.assert_array_equal(
            movies[five], movies[::-1][input_selection("key", movies[::-1], 5, 1)]
        )

    def test_zero_features_fallback_and_no_overlap_rejected(self):
        matrices = {name: sparse.csr_matrix((8, 1)) for name in (*BLOCKS, "PERSON_ERA")}
        result, support = predict_user(
            matrices, self.b, self.c, self.oi, self.ratings, self.ei, True
        )
        np.testing.assert_allclose(
            result[:, 1:], np.repeat(result[:, 1, None], 12, axis=1)
        )
        self.assertEqual(int(support.sum()), 0)
        with self.assertRaises(Exception):
            predict_user(matrices, self.b, self.c, self.oi, self.ratings, np.array([0]))

    def test_unique_people_not_round_rows(self):
        keys, values = unique_user_means(
            np.array(["a", "a", "b"]), np.array([[1.0], [3.0], [5.0]])
        )
        self.assertEqual(keys.tolist(), ["a", "b"])
        self.assertEqual(values.mean(), 3.5)

    def test_training_roles_and_stats(self):
        train = pd.DataFrame(
            {
                "user_id": [1, 1, 2, 2],
                "movie_id": [10, 20, 10, 20],
                "rating": [1.0, 2.0, 4.0, 5.0],
            }
        )
        stats, x = training_statistics(train, np.array([10, 20, 30]), [1, 2], [3])
        np.testing.assert_array_equal(stats["counts"], [2, 2, 0])
        self.assertEqual(stats["global_mean"], 3)
        self.assertEqual(stats["bayes"][2], 3)
        self.assertTrue(np.isfinite(x.data).all())
        with self.assertRaises(Exception):
            training_statistics(train, np.array([10, 20, 30]), [1, 2], [2])

    def test_shared_weighted_ridge_matches_dense(self):
        rng = np.random.default_rng(3)
        matrices = {name: sparse.csr_matrix(rng.normal(size=(8, 2))) for name in BLOCKS}
        stats = {
            "counts": np.array([0, 2, 4, 6, 8, 10, 12, 14.0]),
            "item_residual_sum": np.array([0, 1, -1, 3, -3, 1, -1, 2.0]),
        }
        correction, _ = shared_fit(matrices, stats)
        x = np.hstack([matrices[name].toarray() for name in BLOCKS])
        expected = x @ np.linalg.solve(
            x.T @ (stats["counts"][:, None] * x) + 50 * np.eye(x.shape[1]),
            x.T @ stats["item_residual_sum"],
        )
        np.testing.assert_allclose(correction, expected, atol=1e-7)

    def test_outside_catalog_rows_filtered_after_role_check(self):
        train = pd.DataFrame(
            {"user_id": [1, 1, 2], "movie_id": [10, 99, 10], "rating": [1.0, 5.0, 3.0]}
        )
        stats, _ = training_statistics(train, np.array([10, 20]), [1, 2], [3])
        self.assertEqual(int(stats["source_training_rows"]), 3)
        self.assertEqual(int(stats["training_rows"]), 2)
        self.assertEqual(int(stats["outside_catalog_rows"]), 1)
        self.assertEqual(float(stats["global_mean"]), 2)
        with self.assertRaises(Exception):
            training_statistics(train, np.array([10, 20]), [1, 2], [1])

    def test_shared_null_target_has_exact_zero_solution(self):
        matrices = {name: sparse.eye(4, format="csr") for name in BLOCKS}
        correction, info = shared_fit(
            matrices, {"counts": np.ones(4), "item_residual_sum": np.zeros(4)}
        )
        np.testing.assert_array_equal(correction, np.zeros(4))
        self.assertEqual(info["stop"], 0)

    def test_multiple_categories_preserved_and_missing_zero(self):
        matrix, _ = categorical([["a", "b", "b"], ["b"], []])
        np.testing.assert_allclose(
            matrix.toarray(), [[1 / np.sqrt(2), 1 / np.sqrt(2)], [0, 1], [0, 0]]
        )


if __name__ == "__main__":
    unittest.main()
