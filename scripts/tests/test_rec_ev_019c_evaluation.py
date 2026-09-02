from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from rec_ev_019c_evaluation import (
    ScoreResult,
    aggregate_user_metrics,
    build_validation_contexts,
    evaluate_contexts,
    metrics_from_top_ranking,
    select_trial,
)


class RecEv019CEvaluationTest(unittest.TestCase):
    def test_contexts_filter_to_final_core_and_k0_has_no_prefix(self) -> None:
        prefixes = pd.DataFrame({
            "user_key": ["u", "u", "u", "u", "u"], "k": [5] * 5,
            "input_rank": [1, 2, 3, 4, 5], "movie_id": [1, 2, 3, 99, 100],
            "binary_label": [1, -1, 1, -1, 1],
        })
        windows = pd.DataFrame({
            "user_key": ["u"] * 4, "k": [0, 0, 5, 5], "window_rank": [1, 2, 1, 2],
            "movie_id": [1, 2, 1, 2], "midrank_utility": [0.9, 0.1, 0.9, 0.1],
            "is_positive": [True, False, True, False], "is_negative": [False, True, False, True],
        })
        contexts = build_validation_contexts(prefixes, windows, {1: 0, 2: 1, 3: 2})
        self.assertEqual(0, len(contexts[0][0].anchor_positions))
        self.assertEqual([0, 1, 2], contexts[5][0].anchor_positions.tolist())

    def test_evaluation_excludes_seen_and_falls_back_per_item(self) -> None:
        context_frame = pd.DataFrame({
            "user_key": ["u"] * 5, "k": [5] * 5, "input_rank": [1, 2, 3, 4, 5],
            "movie_id": [1, 98, 99, 100, 101], "binary_label": [1, -1, 1, -1, 1],
        })
        windows = pd.DataFrame({
            "user_key": ["u"], "k": [5], "window_rank": [1], "movie_id": [2],
            "midrank_utility": [0.9], "is_positive": [True], "is_negative": [False],
        })
        contexts = build_validation_contexts(context_frame, windows, {1: 0, 2: 1, 3: 2})[5]
        rows, predictions = evaluate_contexts(
            contexts,
            lambda _: ScoreResult(
                np.asarray([1.0, 0.8, np.nan]), np.asarray([True, True, False]), False, "missing"
            ),
            candidate_ids=np.asarray([1, 2, 3]),
            b0_percentiles=np.asarray([0.2, 0.5, 0.8]),
            top_candidates=3,
        )
        self.assertNotIn(1, [row["movie_id"] for row in predictions["u"]])
        self.assertTrue(any(row["fallback_used"] for row in predictions["u"]))
        self.assertGreater(rows[0]["ndcg_at_10"], 0)

    def test_selection_uses_precommitted_tie_break(self) -> None:
        rows = [
            {"trial_id": "B-T002", "user_macro_ndcg_at_10": 0.2, "candidate_recall_at_500": 0.7, "fallback_user_rate": 0.1},
            {"trial_id": "B-T001", "user_macro_ndcg_at_10": 0.2, "candidate_recall_at_500": 0.7, "fallback_user_rate": 0.1},
        ]
        self.assertEqual("B-T001", select_trial(rows)["trial_id"])
        aggregate = aggregate_user_metrics([
            {"ndcg_at_10": 0.2, "recall_at_10": 0.1, "mrr_at_10": 0.3,
             "positive_mean_rank_percentile": 0.4, "candidate_recall_at_500": 1,
             "fallback_user": False, "harm_at_2": False, "miss_at_2": True,
             "both_good_at_2": False, "safe_hit_at_2": False}
        ])
        self.assertEqual(0.2, aggregate["user_macro_ndcg_at_10"])

    def test_top_only_metrics_use_supplied_exact_rank_for_tail_positive(self) -> None:
        metrics = metrics_from_top_ranking(
            [1, 2],
            [{"movie_id": 3, "midrank_utility": 0.9, "is_positive": True, "is_negative": False}],
            candidate_set={1, 2, 3, 4}, candidate_count_after_seen=4,
            exact_rank_provider=lambda movie_id: 3 if movie_id == 3 else None,
            top_candidates=2,
        )
        self.assertEqual(0.0, metrics["candidate_recall_at_500"])
        self.assertAlmostEqual(2 / 3, metrics["positive_mean_rank_percentile"])


if __name__ == "__main__":
    unittest.main()
