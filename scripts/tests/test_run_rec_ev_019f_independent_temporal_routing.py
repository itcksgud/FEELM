from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from scripts.run_rec_ev_019f_independent_temporal_routing import (
    InputFirewall,
    InputFirewallError,
    bootstrap_paired,
    decide,
    derive_episode,
    deterministic_top_indices,
    route_for_applicability,
    user_key,
)


class RecEv019fRunnerTest(unittest.TestCase):
    def test_derives_nonoverlapping_reset_episode(self) -> None:
        # Alternating extremes make every row non-neutral under the locked shrinkage.
        ratings = np.asarray(([0.5, 5.0] * 20), dtype=np.float64)
        frame = pd.DataFrame({
            "user_id": [7] * len(ratings),
            "movie_id": np.arange(1, len(ratings) + 1),
            "rating": ratings,
            "timestamp": np.arange(1000, 1000 + len(ratings)),
        })
        global_midrank = np.linspace(0.01, 0.99, 10)
        key = user_key(7)
        structural, strict, prefixes, windows = derive_episode(
            frame,
            global_midrank=global_midrank,
            candidate_movie_ids=set(range(1, 100)),
            tuning_union=set(),
            historical_k10_users={key},
            historical_any_users={key},
        )
        self.assertEqual(len(structural), 1)
        self.assertEqual(structural[0]["tail_start_source_position"], 20)
        self.assertEqual([row["source_position"] for row in prefixes], list(range(20, 30)))
        self.assertEqual([row["source_position"] for row in windows], list(range(30, 40)))
        self.assertEqual(len(strict), 1)
        self.assertEqual(strict[0]["historical_source_row_overlap"], 0)
        self.assertFalse(strict[0]["user_independent"])

    def test_tuning_user_is_excluded_before_episode_materialization(self) -> None:
        ratings = np.asarray(([0.5, 5.0] * 20), dtype=np.float64)
        frame = pd.DataFrame({
            "user_id": [9] * len(ratings),
            "movie_id": np.arange(1, len(ratings) + 1),
            "rating": ratings,
            "timestamp": np.arange(len(ratings)),
        })
        key = user_key(9)
        outputs = derive_episode(
            frame,
            global_midrank=np.linspace(0.01, 0.99, 10),
            candidate_movie_ids=set(range(1, 100)),
            tuning_union={key},
            historical_k10_users=set(),
            historical_any_users=set(),
        )
        self.assertEqual(tuple(map(len, outputs)), (0, 0, 0, 0))

    def test_route_is_parameter_free_019e_order(self) -> None:
        self.assertEqual(route_for_applicability(True, True), ("BOTH_LIGHTFM", "K5", "K5_FOLD_IN"))
        self.assertEqual(route_for_applicability(False, True), ("K10_NEWLY_APPLICABLE", "K10", "K10_FOLD_IN"))
        self.assertEqual(route_for_applicability(False, False), ("BOTH_FALLBACK", "K5", "B0"))

    def test_harm_first_decision(self) -> None:
        self.assertEqual(decide({"harm_one_sided_95_upper": 0.006, "ndcg_mean": 0.02, "ndcg_two_sided_95": [0.01, 0.03]})["status"], "FAIL")
        self.assertEqual(decide({"harm_one_sided_95_upper": 0.005, "ndcg_mean": 0.005, "ndcg_two_sided_95": [0.0001, 0.01]})["status"], "PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION")
        self.assertEqual(decide({"harm_one_sided_95_upper": 0.005, "ndcg_mean": 0.0049, "ndcg_two_sided_95": [0.0001, 0.01]})["status"], "INCONCLUSIVE")

    def test_bootstrap_is_deterministic(self) -> None:
        first = bootstrap_paired(np.asarray([0.01, 0.02]), np.asarray([0.0, 0.0]), iterations=100, seed=20260924)
        second = bootstrap_paired(np.asarray([0.01, 0.02]), np.asarray([0.0, 0.0]), iterations=100, seed=20260924)
        self.assertEqual(first, second)

    def test_movie_id_ascending_breaks_score_ties(self) -> None:
        ids = np.asarray([3, 1, 2], dtype=np.int64)
        scores = np.asarray([1.0, 1.0, 1.0])
        order = deterministic_top_indices(ids, scores, top_n=3)
        self.assertEqual(ids[order].tolist(), [1, 2, 3])

    def test_firewall_rejects_unknown_and_forbidden(self) -> None:
        contract = {
            "allowed_input_artifacts": {"validation": {"path": "allowed.parquet"}},
            "forbidden_input_artifacts": ["forbidden.parquet"],
        }
        firewall = InputFirewall(contract)
        with self.assertRaises(InputFirewallError):
            firewall.validate("missing")
        with self.assertRaises(InputFirewallError):
            firewall.validate_external("forbidden.parquet")


if __name__ == "__main__":
    unittest.main()
