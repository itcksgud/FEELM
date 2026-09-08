"""Synthetic boundary checks only; no source data or model fit."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rec_ev_035_inputs as m
import numpy as np


class InputTests(unittest.TestCase):
    def test_relative_midrank_shrinkage_and_zero(self):
        prior = np.arange(.05, 1, .1)
        indices = np.array([0, 0, 5, 9], dtype=np.int64)
        w = m.observation_weights(indices, prior)
        expected = (2 * (np.array([1, 1, 2.5, 3.5]) + 5 * prior[indices]) / 9) - 1
        np.testing.assert_allclose(w, expected, atol=1e-15)
        prior[5] = .5
        self.assertEqual(m.observation_weights(np.array([5]), prior)[0], 0)
        self.assertEqual(len(m.observation_weights(np.array([], dtype=np.int64), prior)), 0)

    def test_train_zero_is_observation_and_cast_retains_it(self):
        h = np.zeros((2, 10), dtype=np.int64); h[:, 5] = [1, 4]
        g = np.full(10, .5)
        targets = m.relative_table(h, g)
        values = np.array([targets[0, 5], *([targets[1, 5]] * 4)])
        self.assertEqual(len(values.astype(np.float32)), int(h.sum()))
        self.assertTrue((values.astype(np.float32) == 0).all())

    def test_mapper_uses_training_sign_row_means(self):
        h = np.zeros((2, 10), dtype=np.int64)
        h[0, 1], h[1, 3], h[0, 7], h[1, 9] = 3, 1, 2, 2
        w = np.zeros_like(h, dtype=float); w[:, :5] = -1; w[:, 5:] = 1
        mapper = m.train_mapper(h, w)
        self.assertEqual(mapper['DISLIKE']['target'], 1.25)
        self.assertEqual(mapper['LIKE']['target'], 4.5)
        np.testing.assert_array_equal(m.binary_targets([m.BinaryResponse.LIKE, m.BinaryResponse.DISLIKE], mapper), [4.5, 1.25])
        for response in ['LIKE', 1, True, 5., {'rating': 5}]:
            with self.assertRaises(RuntimeError):
                m.binary_targets([response], mapper)
        with self.assertRaises(RuntimeError):
            m.train_mapper(h, np.ones_like(w))

    def test_pseudo_target_does_not_weaken_raw_validator(self):
        factors = np.array([[1., .2], [.4, 1.]])
        with self.assertRaises(RuntimeError):
            m.fold_targets(factors, [1.25, 4.5], .1, 'RAW')
        result = m.fold_targets(factors, [1.25, 4.5], .1, 'BINARY_PSEUDO')
        self.assertTrue(np.isfinite(result).all())
        scaled = m.fold_targets(factors, [-4., 2.], .1, 'RELATIVE_SCALED')
        np.testing.assert_allclose(scaled, np.linalg.solve(factors.T @ factors + .2 * np.eye(2), factors.T @ [-4., 2.]))

    def test_empty_and_factorless_exclude_all_observed(self):
        ids = np.array([1, 2, 3, 4]); y = np.eye(4, 2); has = np.array([True, True, False, True])
        bayes = np.array([1., 2., 100., 4.])
        ordered, values, diag = m.full_order(ids, bayes, y, has, [3], np.array([], dtype=int), [], .1, 'RELATIVE_SCALED')
        self.assertEqual(ordered.tolist(), [3, 1, 0])
        self.assertTrue(np.isneginf(values[2])); self.assertFalse(diag['active'])
        # Neutral observed movie 2 remains excluded even though only movie 1 enters fold-in.
        ordered, _, _ = m.full_order(ids, bayes, y, has, [1, 2], np.array([0]), [4.], .1, 'RAW')
        self.assertEqual(ordered.tolist(), [3])

    def test_zero_vector_falls_back_without_dropping_user(self):
        ids = np.array([1, 2, 3]); y = np.array([[1.], [1.], [2.]])
        ordered, _, diag = m.full_order(ids, np.array([1., 2., 3.]), y, np.ones(3, bool), [1, 2], np.array([0, 1]), [-1., 1.], .1, 'RELATIVE_SCALED')
        self.assertEqual(ordered.tolist(), [2]); self.assertEqual(diag['fallback'], 'P0_ZERO_PROFILE')

    def test_raw_path_is_exact_existing_solver(self):
        rng = np.random.default_rng(9); y = rng.normal(size=(5, 3)); r = np.array([.5, 2., 3.5, 4., 5.])
        np.testing.assert_array_equal(m.fold_targets(y, r, .1, 'RAW'), m.base.fold_in(y, r, .1))

    def test_target_projection_and_integer_ties(self):
        top, ranks = m.conditional.projected_order(np.array([3, 0, 2]), np.array([0, 1, 2]), 4)
        self.assertEqual(top.tolist(), [0, 2]); self.assertEqual(ranks.tolist(), [2, 0, 3])
        h = np.array([2, 0, 0, 0, 0, 0, 0, 0, 0, 2])
        a, d = m.conditional.metric_parts([.5, 5.], h)
        b, _ = m.conditional.metric_parts([5., .5], h)
        np.testing.assert_array_equal(a-b, [0, 0, 0])
        # Q=.25 for the low tied rating: not called bottom-20 by raw count.
        self.assertEqual(a[2], 0)
        with self.assertRaises(RuntimeError):
            m.conditional.check_supply(np.array([2, 1]))

    def test_evaluation_refuses_unsealed_scores_before_read(self):
        run = object.__new__(m.Run)
        with patch.object(run, 'validate_score', side_effect=RuntimeError('unsealed')):
            with patch.object(m.pd, 'read_parquet') as reader:
                with self.assertRaises(RuntimeError): run.evaluate()
                reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
