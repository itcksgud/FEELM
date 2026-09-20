import unittest

import numpy as np
from scipy import sparse

from fm_v2_train import CrossFM
from fm_v3_train import factor_objective_and_gradients, fit_factor_only


class FMV3TrainTest(unittest.TestCase):
    def model(self):
        return CrossFM(
            1.2,
            np.array([0.1, -0.2, 0.3, -0.1]),
            np.array([0, 1], dtype=np.int32),
            np.array([2], dtype=np.int32),
            np.array([3], dtype=np.int32),
            np.array([[0.2, -0.1], [0.05, 0.3]]),
            np.array([[0.4, -0.2]]),
            np.array([[-0.3, 0.1]]),
        )

    def test_factor_gradient_matches_finite_difference(self):
        X = sparse.csr_matrix(np.array([[1.0, 0.0, 1.0, 0.0],
                                        [0.5, 1.0, 0.0, 1.0],
                                        [0.0, 1.0, 1.0, 0.0]]))
        y = np.array([2.0, 1.0, 2.5])
        weight = np.array([1.0, 2.0, 0.5])
        model = self.model()
        reg = 0.013
        _, gradients = factor_objective_and_gradients(model, X, y, weight, reg)
        epsilon = 1e-6
        for name in ("candidate_factors", "positive_factors", "negative_factors"):
            values = getattr(model, name)
            for index in np.ndindex(values.shape):
                original = values[index]
                values[index] = original + epsilon
                plus, _ = factor_objective_and_gradients(model, X, y, weight, reg)
                values[index] = original - epsilon
                minus, _ = factor_objective_and_gradients(model, X, y, weight, reg)
                values[index] = original
                numeric = (plus - minus) / (2.0 * epsilon)
                self.assertAlmostEqual(numeric, gradients[name][index], places=6)

    def test_factor_fit_never_mutates_additive_parameters(self):
        X = sparse.csr_matrix(np.array([[1.0, 0.0, 1.0, 0.0],
                                        [0.5, 1.0, 0.0, 1.0],
                                        [0.0, 1.0, 1.0, 0.0]]))
        y = np.array([2.0, 1.0, 2.5])
        weight = np.ones(3)
        model = self.model()
        intercept = model.intercept
        linear = model.linear.copy()
        fitted, _ = fit_factor_only(
            X, y, weight, X, y, weight, model, 0.01, 0.001,
            {"max_epochs": 2, "patience": 2, "beta1": 0.9, "beta2": 0.999,
             "epsilon": 1e-8, "gradient_clip_norm": 10.0},
        )
        self.assertEqual(fitted.intercept, intercept)
        np.testing.assert_array_equal(fitted.linear, linear)


if __name__ == "__main__":
    unittest.main()
