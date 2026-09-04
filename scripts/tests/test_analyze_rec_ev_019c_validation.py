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
    CONFIRMATORY_BOOTSTRAP_ITERATIONS,
    LIGHTFM,
    aggregate_metrics,
    common_user_k_diagnostic,
    confirmatory_paired_summary,
    fallback_anchor_diagnostics,
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

    def test_confirmatory_summary_excludes_k_specific_tuning_users(self) -> None:
        rows = []
        for k in (5, 10):
            for user, baseline, lightfm in (("tuned", 0.9, 0.0), ("held", 0.1, 0.4)):
                rows.append({"user_key": user, "k": k, "model_id": BASELINE, "ndcg_at_10": baseline})
                rows.append({"user_key": user, "k": k, "model_id": LIGHTFM, "ndcg_at_10": lightfm})
        result = confirmatory_paired_summary(
            pd.DataFrame(rows),
            {"5": ["tuned"], "10": ["tuned"]},
        )
        self.assertEqual([row["users"] for row in result], [1, 1])
        self.assertTrue(all(row["tuning_panel_users_excluded"] == 1 for row in result))
        self.assertTrue(all(abs(row["delta_ndcg_mean"] - 0.3) < 1e-12 for row in result))
        self.assertTrue(
            all(row["bootstrap"]["iterations"] == CONFIRMATORY_BOOTSTRAP_ITERATIONS for row in result)
        )

    def test_common_user_k_diagnostic_does_not_claim_same_future_window(self) -> None:
        rows = []
        for model_id, by_k in (
            (BASELINE, {5: [0.1, 0.2], 10: [0.05, 0.15]}),
            (LIGHTFM, {5: [0.3, 0.4], 10: [0.31, 0.39]}),
        ):
            for k, values in by_k.items():
                for user, value in zip(("a", "b"), values, strict=True):
                    rows.append({"user_key": user, "k": k, "model_id": model_id, "ndcg_at_10": value})
        result = common_user_k_diagnostic(pd.DataFrame(rows))
        self.assertEqual(result["users"], 2)
        self.assertFalse(result["same_future_window"])
        self.assertEqual(result["required_next_test"], "SAME_USERS_SAME_FUTURE_WINDOW_PREFIX_ABLATION")

    def test_fallback_anchor_diagnostic_separates_raw_and_valid_anchors(self) -> None:
        metrics = pd.DataFrame({
            "user_key": ["lost", "valid", "one-sided"],
            "k": [5, 5, 5],
            "model_id": [LIGHTFM, LIGHTFM, LIGHTFM],
            "fallback_user": [True, False, True],
        })
        contexts = pd.DataFrame({
            "user_key": ["lost", "valid", "one-sided"],
            "k": [5, 5, 5],
            "raw_both_signals": [True, True, False],
            "valid_candidate_both_signals": [False, True, False],
            "candidate_anchor_loss_forces_fallback": [True, False, False],
        })
        row = fallback_anchor_diagnostics(metrics, contexts)[0]
        self.assertEqual(row["raw_both_but_candidate_anchor_loss_users"], 1)
        self.assertEqual(row["raw_one_sided_fallback_users"], 1)
        self.assertTrue(row["fallback_is_design_precondition_not_signal_effect"])


if __name__ == "__main__":
    unittest.main()
