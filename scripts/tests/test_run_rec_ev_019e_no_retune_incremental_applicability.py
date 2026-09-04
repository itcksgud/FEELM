from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from scripts.run_rec_ev_019e_no_retune_incremental_applicability import (
    InputFirewall,
    InputFirewallError,
    bootstrap_paired,
    build_parser,
    decide,
    route_for_stratum,
)


class RecEv019eRunnerTests(unittest.TestCase):
    def test_routing_is_exact_and_parameter_free(self) -> None:
        self.assertEqual(route_for_stratum("BOTH_LIGHTFM"), ("K5", "K5_FOLD_IN"))
        self.assertEqual(route_for_stratum("K10_NEWLY_APPLICABLE"), ("K10", "K10_FOLD_IN"))
        self.assertEqual(route_for_stratum("BOTH_FALLBACK"), ("K5", "B0"))
        with self.assertRaisesRegex(RuntimeError, "unknown applicability"):
            route_for_stratum("OTHER")

    def test_decision_priority_and_limited_pass_status(self) -> None:
        self.assertEqual(decide({
            "ndcg_mean": 0.1,
            "ndcg_two_sided_95": [0.05, 0.15],
            "harm_one_sided_95_upper": 0.006,
        })["status"], "FAIL_SAFETY_MARGIN_EXCEEDED")
        self.assertEqual(decide({
            "ndcg_mean": 0.005,
            "ndcg_two_sided_95": [0.0001, 0.01],
            "harm_one_sided_95_upper": 0.005,
        })["status"], "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION")
        self.assertEqual(decide({
            "ndcg_mean": 0.0049,
            "ndcg_two_sided_95": [0.0001, 0.01],
            "harm_one_sided_95_upper": 0.0,
        })["status"], "INCONCLUSIVE_POST_HOC_VALIDATION")

    def test_bootstrap_is_deterministic(self) -> None:
        ndcg = np.asarray([0.0, 0.1, -0.05], dtype=np.float64)
        harm = np.asarray([0.0, 1.0, -1.0], dtype=np.float64)
        self.assertEqual(
            bootstrap_paired(ndcg, harm, iterations=100, seed=20260924),
            bootstrap_paired(ndcg, harm, iterations=100, seed=20260924),
        )

    def test_firewall_rejects_unknown_and_forbidden_before_open(self) -> None:
        contract = {
            "allowed_input_artifacts": {"ok": {"path": "allowed/data.parquet"}},
            "forbidden_input_artifacts": ["forbidden/test.parquet"],
        }
        with tempfile.TemporaryDirectory() as directory:
            firewall = InputFirewall(contract, root=Path(directory))
            self.assertEqual(firewall.validate("ok"), (Path(directory) / "allowed/data.parquet").resolve())
            with self.assertRaises(InputFirewallError):
                firewall.validate_external("forbidden/test.parquet")
            with self.assertRaises(InputFirewallError):
                firewall.validate_external("unknown/data.parquet")

    def test_cli_is_validation_only_and_has_resume(self) -> None:
        parser = build_parser()
        phase = next(action for action in parser._actions if action.dest == "phase")
        role = next(action for action in parser._actions if action.dest == "role")
        self.assertEqual(tuple(phase.choices), ("lock", "run"))
        self.assertEqual(tuple(role.choices), ("validation-019e-post-hoc",))
        self.assertIn("resume", {action.dest for action in parser._actions})
        serialized = json.dumps({action.dest: list(action.choices) if action.choices else None for action in parser._actions})
        self.assertNotIn('"test"', serialized.lower())


if __name__ == "__main__":
    unittest.main()
