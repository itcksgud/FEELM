from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from rec_ev_019c_bounded_core import (
    BudgetExceeded,
    BudgetLedger,
    sample_observed_pairs,
    select_tuning_panel,
    user_ranking_metrics,
)


class RecEv019CBoundedCoreTest(unittest.TestCase):
    def test_tuning_panel_is_bounded_deterministic_and_order_independent(self) -> None:
        users = {0: ["c", "a", "b"], 5: ["f", "d", "e"], 10: ["i", "g", "h"]}
        first = select_tuning_panel(users, {0: 2, 5: 2, 10: 2})
        second = select_tuning_panel({k: list(reversed(v)) for k, v in users.items()}, {0: 2, 5: 2, 10: 2})
        self.assertEqual(first, second)
        self.assertEqual({0: 2, 5: 2, 10: 2}, {k: len(v) for k, v in first.items()})

    def test_observed_pairs_are_unique_bounded_and_reproducible(self) -> None:
        first = sample_observed_pairs([1, 2, 3], [7, 8, 9], user_key="u", model_seed=17, epoch=2, maximum_pairs=4)
        second = sample_observed_pairs([3, 2, 1], [9, 8, 7], user_key="u", model_seed=17, epoch=2, maximum_pairs=4)
        self.assertEqual(first, second)
        self.assertEqual(4, len(first))
        self.assertEqual(len(first), len(set(first)))
        self.assertTrue(all(like in {1, 2, 3} and dislike in {7, 8, 9} for like, dislike in first))

    def test_observed_pair_sampling_avoids_large_cartesian_materialization(self) -> None:
        pairs = sample_observed_pairs(
            list(range(10000)), list(range(10000, 20000)),
            user_key="large", model_seed=17, epoch=0, maximum_pairs=16,
        )
        self.assertEqual(16, len(pairs))
        self.assertEqual(16, len(set(pairs)))

    def test_pair_sampler_rejects_label_overlap(self) -> None:
        with self.assertRaisesRegex(ValueError, "both LIKE and DISLIKE"):
            sample_observed_pairs([1], [1], user_key="u", model_seed=17, epoch=0)

    def test_budget_blocks_before_crossing_limit(self) -> None:
        limits = {
            "maximum_full_catalog_user_item_scores": 10,
            "maximum_b8_base_updates": 10,
            "maximum_b4_pair_updates": 10,
            "maximum_rrf_rank_contributions": 10,
            "wall_clock_soft_limit_seconds": 5,
            "wall_clock_hard_limit_seconds": 10,
        }
        ledger = BudgetLedger(limits, started_at=100.0)
        self.assertEqual(8, ledger.charge("full_catalog_user_item_scores", 8))
        with self.assertRaisesRegex(BudgetExceeded, "would exceed"):
            ledger.charge("full_catalog_user_item_scores", 3)
        self.assertEqual(8, ledger.counters["full_catalog_user_item_scores"])
        self.assertEqual("SOFT_LIMIT", ledger.check_wall_clock(now=106.0))
        with self.assertRaisesRegex(BudgetExceeded, "hard limit"):
            ledger.check_wall_clock(now=110.0)

    def test_metrics_treat_unobserved_as_unknown_and_flag_observed_bad(self) -> None:
        candidates = np.asarray([1, 2, 3, 4], dtype=np.int64)
        scores = np.asarray([0.7, 0.9, 0.8, 0.1], dtype=np.float64)
        rows = [
            {"movie_id": 1, "midrank_utility": 0.9, "is_positive": True, "is_negative": False},
            {"movie_id": 2, "midrank_utility": 0.1, "is_positive": False, "is_negative": True},
            {"movie_id": 4, "midrank_utility": 0.8, "is_positive": True, "is_negative": False},
        ]
        metrics, ranked = user_ranking_metrics(candidates, scores, rows, top_candidates=4)
        self.assertEqual([2, 3, 1, 4], ranked)
        self.assertTrue(metrics["harm_at_2"])
        self.assertTrue(metrics["miss_at_2"])
        self.assertFalse(metrics["safe_hit_at_2"])
        self.assertGreater(metrics["ndcg_at_10"], 0.0)


if __name__ == "__main__":
    unittest.main()
