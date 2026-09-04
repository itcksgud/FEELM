from __future__ import annotations

import unittest
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from analyze_rec_ev_019c_validation import (
    BASELINE,
    aggregate_metrics,
    paired_bootstrap_ci,
    paired_summary,
    source_history_depth,
)


class RecEv019cAnalysisTest(unittest.TestCase):
    def test_source_history_depth_uses_last_zero_based_source_position(self) -> None:
        self.assertEqual(source_history_depth(pd.Series([0, 2, 5])), 6)

    def test_paired_bootstrap_is_deterministic(self) -> None:
        values = np.asarray([-0.1, 0.0, 0.2, 0.3], dtype=np.float64)
        first = paired_bootstrap_ci(values, seed=7, iterations=500)
        second = paired_bootstrap_ci(values, seed=7, iterations=500)
        self.assertEqual(first, second)
        self.assertLessEqual(first[0], values.mean())
        self.assertGreaterEqual(first[1], values.mean())

    def test_aggregate_metrics_keeps_nullable_top2_denominators(self) -> None:
        frame = pd.DataFrame({
            "user_key": ["a", "b"],
            "ndcg_at_10": [0.0, 1.0],
            "recall_at_10": [0.0, 1.0],
            "mrr_at_10": [0.0, 1.0],
            "positive_mean_rank_percentile": [0.5, 0.25],
            "candidate_recall_at_500": [0.0, 1.0],
            "harm_at_2": [False, True],
            "miss_at_2": [True, None],
            "both_good_at_2": [False, None],
            "safe_hit_at_2": [False, None],
            "fallback_user": [False, True],
        })
        result = aggregate_metrics(frame)
        self.assertEqual(result["users"], 2)
        self.assertEqual(result["harm_at_2"], 0.5)
        self.assertEqual(result["miss_at_2"], 1.0)

    def test_paired_summary_reports_user_level_benefit_tie_and_harm(self) -> None:
        common = {
            "recall_at_10": 0.0,
            "mrr_at_10": 0.0,
            "positive_mean_rank_percentile": 1.0,
            "candidate_recall_at_500": 0.0,
            "miss_at_2": True,
            "both_good_at_2": False,
            "safe_hit_at_2": False,
            "fallback_user": False,
        }
        rows = []
        for user, baseline, candidate in (("a", 0.1, 0.2), ("b", 0.3, 0.3), ("c", 0.5, 0.4)):
            rows.append({
                "user_key": user, "k": 5, "model_id": BASELINE,
                "ndcg_at_10": baseline, "harm_at_2": False, **common,
            })
            rows.append({
                "user_key": user, "k": 5, "model_id": "B2_ITEM_KNN",
                "ndcg_at_10": candidate, "harm_at_2": False, **common,
            })
        result = paired_summary(pd.DataFrame(rows))
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["benefit_rate"], 1 / 3)
        self.assertAlmostEqual(result[0]["tie_rate"], 1 / 3)
        self.assertAlmostEqual(result[0]["harm_rate"], 1 / 3)


if __name__ == "__main__":
    unittest.main()
