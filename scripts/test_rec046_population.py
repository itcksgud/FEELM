"""Guard stratification boundaries and user uncertainty behavior on synthetic inputs."""

import unittest
import numpy as np

from audit_rec046_population import activity_bin, item_bin, bootstrap_mean


class PopulationChecks(unittest.TestCase):
    def test_activity_thresholds_keep_missing_cohorts_separate(self):
        np.testing.assert_array_equal(
            activity_bin(np.array([0, 1, 9, 10, 29, 30, 99, 100, 299, 300])),
            [
                "0",
                "1_9",
                "1_9",
                "10_29",
                "10_29",
                "30_99",
                "30_99",
                "100_299",
                "100_299",
                "300_PLUS",
            ],
        )

    def test_item_zero_is_not_low_positive_support(self):
        np.testing.assert_array_equal(
            item_bin(np.array([0, 1, 9, 10, 49, 50])),
            ["0", "1_9", "1_9", "10_49", "10_49", "50_PLUS"],
        )

    def test_bootstrap_uses_finite_user_denominator_and_threshold(self):
        a = np.array([0.5] * 29 + [np.nan] * 100)
        self.assertTrue(np.isnan(bootstrap_mean(a, "under30")).all())
        self.assertEqual(bootstrap_mean(np.array([0.5] * 30), "30"), (0.5, 0.5))

    def test_bootstrap_is_deterministic_and_preserves_constant_shift(self):
        a = np.linspace(0, 1, 100)
        x = bootstrap_mean(a, "same")
        self.assertEqual(x, bootstrap_mean(a, "same"))
        np.testing.assert_allclose(np.array(x) + 2, bootstrap_mean(a + 2, "same"))


if __name__ == "__main__":
    unittest.main()
