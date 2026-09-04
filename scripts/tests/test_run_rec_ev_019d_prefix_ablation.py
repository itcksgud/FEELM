from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from scripts.run_rec_ev_019d_prefix_ablation import (
    InputFirewall,
    InputFirewallError,
    bootstrap_paired,
    build_parser,
    decide,
    midrank_percentiles,
)


class RecEv019dRunnerTests(unittest.TestCase):
    def test_midrank_percentiles_match_contract_formula(self) -> None:
        actual = midrank_percentiles(np.asarray([3.0, 1.0, 1.0, 2.0]))
        np.testing.assert_allclose(actual, np.asarray([0.875, 0.25, 0.25, 0.625], dtype=np.float32))

    def test_decision_priority_fails_safety_before_efficacy(self) -> None:
        actual = decide({
            "ndcg_mean": -0.1,
            "ndcg_two_sided_95": [-0.2, -0.05],
            "harm_one_sided_95_upper": 0.006,
        })
        self.assertEqual(actual, {"status": "FAIL", "reason": "SAFETY_MARGIN_EXCEEDED"})

    def test_decision_pass_requires_all_three_criteria(self) -> None:
        self.assertEqual(
            decide({
                "ndcg_mean": 0.005,
                "ndcg_two_sided_95": [0.0001, 0.01],
                "harm_one_sided_95_upper": 0.005,
            })["status"],
            "PASS",
        )
        self.assertEqual(
            decide({
                "ndcg_mean": 0.0049,
                "ndcg_two_sided_95": [0.0001, 0.01],
                "harm_one_sided_95_upper": 0.005,
            })["status"],
            "INCONCLUSIVE",
        )

    def test_bootstrap_is_deterministic(self) -> None:
        ndcg = np.asarray([0.1, -0.1, 0.2], dtype=np.float64)
        harm = np.asarray([0.0, 1.0, -1.0], dtype=np.float64)
        left = bootstrap_paired(ndcg, harm, iterations=100, seed=20260924)
        right = bootstrap_paired(ndcg, harm, iterations=100, seed=20260924)
        self.assertEqual(left, right)

    def test_firewall_rejects_forbidden_and_unknown_before_open(self) -> None:
        contract = {
            "allowed_input_artifacts": {
                "ok": {"path": "allowed/data.parquet", "bytes": 0, "sha256": "0" * 64},
            },
            "forbidden_input_artifacts": ["forbidden/data.parquet"],
        }
        with tempfile.TemporaryDirectory() as directory:
            firewall = InputFirewall(contract, root=Path(directory))
            self.assertEqual(firewall.validate("ok"), (Path(directory) / "allowed/data.parquet").resolve())
            with self.assertRaises(InputFirewallError):
                firewall.validate_external("forbidden/data.parquet")
            with self.assertRaises(InputFirewallError):
                firewall.validate_external("unknown/data.parquet")

    def test_cli_exposes_only_lock_or_run_validation_role_and_no_test_mode(self) -> None:
        parser = build_parser()
        phase = next(action for action in parser._actions if action.dest == "phase")
        role = next(action for action in parser._actions if action.dest == "role")
        self.assertEqual(tuple(phase.choices), ("lock", "run"))
        self.assertEqual(tuple(role.choices), ("validation-019d",))
        serialized = json.dumps({action.dest: list(action.choices) if action.choices else None for action in parser._actions})
        self.assertNotIn('"test"', serialized.lower())


if __name__ == "__main__":
    unittest.main()
