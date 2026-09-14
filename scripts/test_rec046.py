"""Meaningful synthetic leakage/axis/formula checks; no real rating payload."""

import unittest
import numpy as np
from scipy import sparse
from rec046_common import (
    BLOCKS,
    fit_features,
    pair_features,
    pair_accuracy,
    als_predict,
    fm_manual,
    development,
    withheld,
)


class ComparisonTests(unittest.TestCase):
    def test_vocabulary_ignores_cold_movie_tokens(self):
        tokens = [[{"warm"} for _ in BLOCKS], [{"cold"} for _ in BLOCKS]]
        x, v, _ = fit_features(tokens, [0], {b: 8 for b in BLOCKS})
        self.assertTrue(all(values == ["warm"] for values in v.values()))
        self.assertEqual(x.shape, (2, 14))
        self.assertEqual(x[0].multiply(x[1]).nnz, 0)

    def test_target_input_overlap_rejected(self):
        with self.assertRaises(RuntimeError):
            pair_features(sparse.eye(3, format="csr"), [0], [4], [0], 3)

    def test_half_stars_not_coerced(self):
        with self.assertRaises(RuntimeError):
            pair_features(sparse.eye(3, format="csr"), [0], [4.1], [1], 3)

    def test_single_input_keeps_rating_information(self):
        x = sparse.csr_matrix([[1, 0], [1, 1]])
        a = pair_features(x, [0], [1], [1], 3).toarray()
        b = pair_features(x, [0], [5], [1], 3).toarray()
        self.assertFalse(np.array_equal(a, b))
        self.assertGreater(np.count_nonzero(a), 0)

    def test_shared_cross_terms_change_order(self):
        x = sparse.eye(2, format="csr")
        a = pair_features(
            sparse.vstack([x, x], format="csr"), [0], [5], [2, 3], 3
        ).toarray()
        b = pair_features(
            sparse.vstack([x, x], format="csr"), [1], [5], [2, 3], 3
        ).toarray()
        coef = np.zeros(a.shape[1])
        coef[6:8] = 1
        self.assertGreater((a @ coef)[0], (a @ coef)[1])
        self.assertLess((b @ coef)[0], (b @ coef)[1])

    def test_pair_accuracy_matches_bruteforce(self):
        rng = np.random.default_rng(5)
        for _ in range(40):
            y = rng.integers(1, 11, 15) / 2
            p = rng.integers(0, 5, 15)
            scores = []
            for i in range(len(y)):
                for j in range(i):
                    if y[i] == y[j]:
                        continue
                    product = (y[i] - y[j]) * (p[i] - p[j])
                    scores.append(1 if product > 0 else 0.5 if product == 0 else 0)
            self.assertAlmostEqual(pair_accuracy(y, p), np.mean(scores))
        self.assertIsNone(pair_accuracy([3, 3], [1, 2]))

    def test_missing_als_stays_in_denominator(self):
        factors = np.array([[1.0, 0], [0, 1], [np.nan, np.nan]])
        p, s, n = als_predict(factors, np.arange(3), [0], [4], [1, 2], 0.1, 3.5)
        self.assertEqual(len(p), 2)
        self.assertEqual(s.tolist(), [True, False])
        self.assertEqual(n, 1)
        p, s, n = als_predict(factors, np.arange(3), [2], [4], [0, 1], 0.1, 3.5)
        self.assertFalse(s.any())
        self.assertTrue(np.isfinite(p).all())
        self.assertEqual(n, 0)

    def test_fm_formula_explicit_pairs(self):
        rng = np.random.default_rng(1)
        x = rng.normal(size=(7, 5))
        v = rng.normal(size=(5, 3))
        w = rng.normal(size=5)
        expected = 2 + x @ w
        for i in range(5):
            for j in range(i):
                expected += x[:, i] * x[:, j] * (v[i] @ v[j])
        np.testing.assert_allclose(
            fm_manual(sparse.csr_matrix(x), 2, w, v), expected, atol=1e-12
        )

    def test_role_and_holdout_deterministic(self):
        self.assertFalse(development(0))
        self.assertFalse(development(200949))
        self.assertEqual(withheld(10), withheld(10))


if __name__ == "__main__":
    unittest.main()
