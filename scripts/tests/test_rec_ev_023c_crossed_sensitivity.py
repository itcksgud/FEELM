from __future__ import annotations

import copy
import json
import unittest
from collections import Counter

import numpy as np
import pandas as pd

from scripts.run_rec_ev_023c_crossed_sensitivity import (
    _truth_table,
    build_parser,
    nearest_rank,
    poisson_cutoffs,
    poisson_weight,
    regime_intervals,
    verify_golden,
)
from scripts.validate_rec_ev_023c_contract import CONTRACT, ROOT, validate_contract


class RecEv023cCrossedSensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_contract_is_adaptive_and_forbids_item_generalization(self) -> None:
        result = validate_contract(self.contract)
        self.assertEqual("PASS_REC_EV_023C_CONTRACT", result["status"])
        self.assertIn("ITEM_GENERALIZATION", self.contract["adaptive_boundary"]["forbidden_claims"])
        self.assertFalse(self.contract["authorization"]["prediction_or_ranking_recompute"])

    def test_inverse_poisson_golden(self) -> None:
        cutoffs = poisson_cutoffs(precision=80)
        verify_golden(self.contract, cutoffs)
        protocol = self.contract["bootstrap"]["protocol_version"]
        weight, x_value = poisson_weight(protocol, 17, "user", "a" * 64, cutoffs)
        self.assertEqual((2, 14214353544981560736), (weight, x_value))

    def test_nearest_rank_has_no_interpolation(self) -> None:
        values = np.arange(2000, dtype=np.float64)
        self.assertEqual(1899.0, nearest_rank(values, 0.95))

    def test_regime_interval_marks_zero_se_nonestimable(self) -> None:
        point = np.asarray([1.0, 2.0], dtype=np.float64)
        replicates = np.column_stack([
            np.linspace(0.0, 2.0, 2000),
            np.full(2000, 2.0),
        ])
        result = regime_intervals(point, replicates)
        self.assertTrue(bool(result["active"][0]))
        self.assertFalse(bool(result["active"][1]))
        self.assertEqual(2.0, float(result["low"][1]))
        self.assertEqual(2.0, float(result["high"][1]))

    def test_membership_constants_match_sealed_score_input(self) -> None:
        entry = self.contract["allowed_input_artifacts"]["rec_ev_023b_score_input"]
        frame = pd.read_parquet(ROOT / entry["path"], columns=["target_movie_ids"])
        counts = Counter(int(movie) for row in frame["target_movie_ids"] for movie in row)
        self.assertEqual(9520, len(frame))
        self.assertTrue(all(len(row) == 20 and len(set(row)) == 20 for row in frame["target_movie_ids"]))
        self.assertEqual(190400, sum(counts.values()))
        self.assertEqual(3565, len(counts))
        self.assertEqual(3025, max(counts.values()))
        self.assertEqual(23067, sum(sorted(counts.values())[-10:]))

    def test_truth_table_cannot_add_two_way_only_forward(self) -> None:
        cells = [{"encoding": "BINARY_SIGN", "k": 6}]
        comparisons = [
            ("STRUCTURED", "RANDOM_EXPECTATION"),
            ("E5", "RANDOM_EXPECTATION"),
            ("AVAILABLE_HEAD_CONTENT_RRF", "RANDOM_EXPECTATION"),
            ("E5", "STRUCTURED"),
            ("AVAILABLE_HEAD_CONTENT_RRF", "E5"),
            ("AVAILABLE_HEAD_CONTENT_RRF", "STRUCTURED"),
        ]
        rows = []
        for left, right in comparisons:
            rows.extend([
                {"encoding": "BINARY_SIGN", "k": 6, "left": left, "right": right, "metric": "top2_mean_q", "low": 0.006, "high": 0.007, "estimable": True},
                {"encoding": "BINARY_SIGN", "k": 6, "left": left, "right": right, "metric": "top2_worst_q_loss", "low": -0.02, "high": 0.0, "estimable": True},
            ])
        _, forward = _truth_table(rows, cells, utility_margin=0.005, loss_margin=0.01)
        self.assertEqual(3, len(forward))
        prior_keys = {("BINARY_SIGN", 6, "STRUCTURED")}
        robust = [row for row in forward if (row["encoding"], row["k"], row["head"]) in prior_keys]
        self.assertEqual([{"encoding": "BINARY_SIGN", "k": 6, "head": "STRUCTURED"}], robust)

    def test_cli_has_no_test_stage2_or_final_phase(self) -> None:
        choices = tuple(next(action for action in build_parser()._actions if action.dest == "phase").choices)
        self.assertNotIn("test", choices)
        self.assertNotIn("stage2", choices)
        self.assertNotIn("final", choices)

    def test_validator_rejects_membership_bootstrap_and_claim_mutations(self) -> None:
        mutations = []
        membership = copy.deepcopy(self.contract)
        membership["membership"]["top10_item_degree_sum"] = 23082
        mutations.append(membership)
        bootstrap = copy.deepcopy(self.contract)
        bootstrap["bootstrap"]["valid_replicates"] = 4000
        mutations.append(bootstrap)
        claim = copy.deepcopy(self.contract)
        claim["adaptive_boundary"]["forbidden_claims"].remove("ITEM_GENERALIZATION")
        mutations.append(claim)
        for mutated in mutations:
            with self.assertRaises(RuntimeError):
                validate_contract(mutated)


if __name__ == "__main__":
    unittest.main()
