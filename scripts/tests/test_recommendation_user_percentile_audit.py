from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from recommendation_user_percentile_audit import (
    activity_segment,
    effect_label,
    history_segment,
    rank_percentile,
    winner_summary,
)


class RecommendationUserPercentileAuditTest(unittest.TestCase):
    def test_rank_percentile_has_interpretable_endpoints(self) -> None:
        self.assertEqual(1.0, rank_percentile(1, 100))
        self.assertEqual(0.0, rank_percentile(100, 100))
        self.assertEqual(0.0, rank_percentile(None, 100))

    def test_effect_label_separates_benefit_tie_and_harm(self) -> None:
        self.assertEqual("BENEFIT", effect_label(0.01))
        self.assertEqual("TIE", effect_label(0.0))
        self.assertEqual("HARM", effect_label(-0.01))

    def test_user_segments_expose_top_five_percent_and_k30(self) -> None:
        self.assertEqual("P95_100_TOP5", activity_segment(0.951))
        self.assertEqual("K1_9", history_segment(1))
        self.assertEqual("K10_19", history_segment(10))
        self.assertEqual("K20_29", history_segment(20))
        self.assertEqual("K30_49", history_segment(30))

    def test_winner_share_splits_ties_without_inflating_total(self) -> None:
        frame = pd.DataFrame({
            "popularity_rank_percentile": [0.9, 0.5, 0.5],
            "hybrid_rank_percentile": [0.8, 0.7, 0.5],
            "tag_content_rank_percentile": [0.7, 0.6, 0.5],
        })
        summary = winner_summary(frame)
        shares = [summary[key]["fractional_winner_share"] for key in ("popularity", "hybrid", "tag_content")]
        self.assertTrue(np.isclose(sum(shares), 1.0, atol=1e-6))
        self.assertEqual(1, summary["users_with_cross_policy_tie"])


if __name__ == "__main__":
    unittest.main()
