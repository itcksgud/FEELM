import unittest

import numpy as np

from hybrid345_score import (
    ResourceBudget,
    choose_mapper,
    mapper_metrics_by_stratum,
    verify_als_factor_source,
)


class Hybrid345ScoreTest(unittest.TestCase):
    def test_mapper_selection_order(self):
        def row(alpha, cosine, rmse):
            return {"alpha": alpha, "validation": {"ALL": {
                "mean_cosine": cosine, "rmse": rmse}}}
        self.assertEqual(choose_mapper([row(1, .8, .2), row(.1, .9, .3)])["alpha"], .1)
        self.assertEqual(choose_mapper([row(1, .9, .2), row(.1, .9, .3)])["alpha"], 1)
        self.assertEqual(choose_mapper([row(1, .9, .2), row(.1, .9, .2)])["alpha"], .1)

    def test_mapper_strata_are_not_pooled(self):
        y = np.eye(6, 2)
        p = y.copy()
        strata = np.array([0, 0, 1, 1, 2, 2])
        result = mapper_metrics_by_stratum(y, p, strata)
        self.assertEqual(result["ALL"]["items"], 6)
        for name in ("SUPPORT_1_9", "SUPPORT_10_49", "SUPPORT_50_PLUS"):
            self.assertEqual(result[name]["items"], 2)

    def test_resource_budget_records_declared_limits(self):
        record = ResourceBudget(10, 10 * 1024**3, "test").start().stop()
        self.assertLessEqual(record["seconds"], record["seconds_limit"])
        self.assertLessEqual(record["process_peak_rss_bytes"],
                             record["host_memory_bytes_limit"])

    def test_resource_budget_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "host memory"):
            ResourceBudget(10, 1, "rss-test").start()
        with self.assertRaisesRegex(ValueError, "time limit"):
            ResourceBudget(1e-12, 10 * 1024**3, "time-test").start()

    def test_actual_als_factor_files_match_training_seal(self):
        record = verify_als_factor_source()
        self.assertEqual(record["file_count"], len(record["files"]))
        self.assertGreater(record["file_count"], 0)


if __name__ == "__main__":
    unittest.main()
