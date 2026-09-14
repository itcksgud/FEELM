import unittest

import numpy as np
from scipy import sparse

from hybrid345_models import (
    apply_affine,
    dense_profile_scores,
    factor_reconstruction,
    fit_nonnegative_affine,
    fold_in_scores,
    percentile_weights,
    rating_indices,
    ridge_from_statistics,
    ridge_sufficient_statistics,
    sparse_profile_scores,
    stable_order,
    stratified_mapper_split,
)


class Hybrid345ModelsTest(unittest.TestCase):
    def test_rating_grid_and_profile_parity(self):
        prior = np.linspace(0.05, 0.95, 10)
        stars = np.array([1.0, 3.5, 5.0])
        self.assertEqual(rating_indices(stars).tolist(), [1, 6, 9])
        with self.assertRaises(ValueError):
            rating_indices([3.3])
        vectors = np.eye(4, dtype=np.float64)
        dense, active = dense_profile_scores(vectors, [0, 1], stars[:2], [2, 3], prior)
        sparse_scores, sparse_active = sparse_profile_scores(sparse.csr_matrix(vectors), [0, 1], stars[:2], [2, 3], prior)
        self.assertTrue(active and sparse_active)
        np.testing.assert_allclose(dense, sparse_scores, atol=1e-12)
        self.assertTrue(np.isfinite(percentile_weights(stars, prior)).all())

    def test_fold_in_exact(self):
        factors = np.array([[1.0, 0.0], [0.0, 1.0]])
        scores, active = fold_in_scores(factors, [4.0, 2.0], factors, reg=0.0)
        self.assertTrue(active)
        np.testing.assert_allclose(scores, [4.0, 2.0])

    def test_mapper_split_is_stable_and_stratified(self):
        ids = np.arange(1, 31)
        support = np.r_[np.arange(1, 11), np.arange(10, 20), np.arange(50, 60)]
        a = stratified_mapper_split(ids, support, "salt|")
        b = stratified_mapper_split(ids, support, "salt|")
        np.testing.assert_array_equal(a[0], b[0])
        for stratum in range(3):
            self.assertGreater((a[0] & (a[2] == stratum)).sum(), 0)
            self.assertGreater((a[1] & (a[2] == stratum)).sum(), 0)

    def test_ridge_and_factor_metrics(self):
        rng = np.random.default_rng(5)
        x = rng.normal(size=(100, 5))
        beta = rng.normal(size=(5, 2))
        y = x @ beta + np.array([0.2, -0.4])
        stats = ridge_sufficient_statistics(x, y)
        coefficient, intercept = ridge_from_statistics(*stats, 1e-8)
        prediction = x @ coefficient + intercept
        self.assertLess(factor_reconstruction(y, prediction)["rmse"], 1e-7)

    def test_affine_and_stable_tie(self):
        a, b, rows, _, _ = fit_nonnegative_affine([1, 2, 3], [3, 2, 1])
        self.assertEqual(b, 0.0)
        self.assertEqual(rows, 3)
        np.testing.assert_allclose(apply_affine([0, 1], a, b), [2, 2])
        self.assertEqual(stable_order([1, 1, 2], [9, 3, 8]).tolist(), [2, 1, 0])


if __name__ == "__main__":
    unittest.main()
