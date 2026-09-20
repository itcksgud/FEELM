import unittest

import numpy as np
from scipy import sparse

from fm_v2_train import initialize_fm
from fm_v5_train import fit_factor_only


class FmV5TrainTest(unittest.TestCase):
    def test_factor_only_fit_preserves_matched_linear_parameters(self):
        dense = np.zeros((30, 6), dtype=np.float64)
        dense[:, 0] = 1.0
        dense[:, 1] = np.arange(30) % 2
        dense[:, 2] = (np.arange(30) + 1) % 2
        matrix = sparse.csr_matrix(dense)
        target = 2.0 + 0.5 * dense[:, 0] * dense[:, 1]
        weight = np.ones(30)
        intercept, linear = 2.0, np.linspace(0.0, 0.5, 6)
        initial = initialize_fm(intercept, linear, {
            "candidate_factor_indices": [0],
            "positive_factor_indices": [1],
            "negative_factor_indices": [2],
        }, 2, 623)
        model, trace = fit_factor_only(
            matrix[:24], target[:24], weight[:24], matrix[24:], target[24:], weight[24:], initial,
            0.01, 0.001, {"max_epochs": 3, "patience": 3, "beta1": 0.9, "beta2": 0.999,
                           "epsilon": 1e-8, "gradient_clip_norm": 10.0},
        )
        self.assertEqual(model.intercept, intercept)
        self.assertTrue(np.array_equal(model.linear, linear))
        self.assertTrue(trace["linear_frozen"])
        self.assertTrue(np.isfinite(model.predict(matrix)).all())


if __name__ == "__main__":
    unittest.main()
