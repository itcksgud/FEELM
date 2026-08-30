from __future__ import annotations

import unittest

import numpy as np

from recommendation_relative_utility_evaluation import (
    metric_block,
    quantize_to_step,
    quantized_midrank_ecdf,
    right_inclusive_ecdf,
)


class RelativeUtilityEvaluationTest(unittest.TestCase):
    def test_quantize_uses_explicit_rating_lattice(self) -> None:
        actual = quantize_to_step(np.array([0.74, 0.75, 3.99, 4.26]), 0.5)
        np.testing.assert_allclose(actual, [0.5, 1.0, 4.0, 4.5])

    def test_midrank_places_ties_in_the_middle(self) -> None:
        history = np.array([3.0, 4.0, 4.0, 5.0])
        value = quantized_midrank_ecdf(history, np.array([4.0]), 0.5)
        self.assertAlmostEqual(float(value[0]), (1 + 1 + 0.5 * 2) / 6)

    def test_quantization_removes_near_boundary_discontinuity(self) -> None:
        history = np.array([3.0, 4.0, 4.0, 5.0])
        baseline = right_inclusive_ecdf(history, np.array([3.99, 4.0]))
        candidate = quantized_midrank_ecdf(history, np.array([3.99, 4.0]), 0.5)
        self.assertNotEqual(float(baseline[0]), float(baseline[1]))
        self.assertEqual(float(candidate[0]), float(candidate[1]))

    def test_metric_block_reports_directional_bias_and_rank(self) -> None:
        metrics = metric_block(np.array([0.2, 0.4, 0.8]), np.array([0.3, 0.5, 0.7]))
        self.assertAlmostEqual(metrics["mae"], 0.1)
        self.assertAlmostEqual(metrics["mean_error"], -0.033333)
        self.assertEqual(metrics["spearman"], 1.0)

    def test_invalid_step_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "rating step"):
            quantize_to_step(np.array([4.0]), 0)


if __name__ == "__main__":
    unittest.main()
