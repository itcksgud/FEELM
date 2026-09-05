from __future__ import annotations

import json
import copy
import unittest

import numpy as np
import pandas as pd

from scripts.run_rec_ev_023a_content_screen import (
    HEADS, _available_head_rrf, _one_based_ranks, build_contrasts, build_parser,
)
from scripts.validate_rec_ev_023a_contract import CONTRACT, validate_contract


class RecEv023aContentScreenTests(unittest.TestCase):
    def test_contract_is_adaptive_development_only(self) -> None:
        result = validate_contract(json.loads(CONTRACT.read_text(encoding="utf-8")))
        self.assertEqual("PASS_REC_EV_023A_CONTRACT", result["status"])
        self.assertTrue(result["adaptive_stage2"])
        self.assertFalse(result["locked_test_access"])
        self.assertIsNone(result["champion"])

    def test_exact_contrast_family_has_108_columns(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        rows = []
        for user_index, user in enumerate(("a" * 64, "b" * 64)):
            for cell in contract["cells"]:
                for head_index, head in enumerate(HEADS):
                    rows.append({
                        "user_key": user,
                        "encoding": cell["encoding"],
                        "k": cell["k"],
                        "head": head,
                        "pair1_mean_q": 0.4 + 0.001 * head_index + 0.01 * user_index,
                        "pair1_worst_q_loss": 0.6 - 0.001 * head_index + 0.01 * user_index,
                    })
        values, metadata, users = build_contrasts(pd.DataFrame(rows), contract["cells"])
        self.assertEqual((2, 108), values.shape)
        self.assertEqual(108, len(metadata))
        self.assertEqual(["a" * 64, "b" * 64], users)

    def test_rrf_never_reuses_inactive_b0_rank(self) -> None:
        movies = np.asarray([30, 10, 20], dtype=np.int64)
        b0 = np.asarray([3.0, 2.0, 1.0], dtype=np.float64)
        first = np.asarray([0.2, 0.1, 0.3], dtype=np.float64)
        inactive = np.asarray([999.0, 0.0, -999.0], dtype=np.float64)
        scores, active, composition = _available_head_rrf(
            movies, b0, [("ACTIVE", first, True), ("INACTIVE", inactive, False)], c=10,
        )
        expected_order = np.asarray([2, 0, 1])
        expected = 1.0 / (10.0 + _one_based_ranks(expected_order))
        np.testing.assert_allclose(expected, scores)
        self.assertTrue(active)
        self.assertEqual("ACTIVE", composition)

    def test_all_inactive_rrf_is_headwide_fallback(self) -> None:
        scores, active, composition = _available_head_rrf(
            np.asarray([1, 2]), np.asarray([0.9, 0.1]),
            [("A", np.asarray([2.0, 1.0]), False)], c=10,
        )
        np.testing.assert_array_equal(np.zeros(2), scores)
        self.assertFalse(active)
        self.assertEqual("", composition)

    def test_cli_has_no_test_or_final_phase(self) -> None:
        choices = tuple(next(action for action in build_parser()._actions if action.dest == "phase").choices)
        self.assertNotIn("test", choices)
        self.assertNotIn("final", choices)

    def test_validator_rejects_contract_path_and_forbidden_list_collusion(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(contract)
        mutated["allowed_input_artifacts"]["stage2_cohort"]["path"] = "outputs/recommendation-evidence/global-time-v1/test.parquet"
        mutated["forbidden_input_artifacts"] = []
        with self.assertRaises(RuntimeError):
            validate_contract(mutated)

    def test_validator_rejects_semantic_truth_table_mutations(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        mutations = []
        adaptive = copy.deepcopy(contract)
        adaptive["adaptive_reuse"]["selection_adjusted_in_current_family"] = True
        mutations.append(adaptive)
        causal = copy.deepcopy(contract)
        causal["rrf"]["interpretation"] = "PURE_CAUSAL_FUSION"
        mutations.append(causal)
        forwarding = copy.deepcopy(contract)
        forwarding["decision"]["forward_e5"] = "Q_E5_B0"
        mutations.append(forwarding)
        output = copy.deepcopy(contract)
        output["output_root"] = "outputs/recommendation-evidence/rec-ev-022b"
        mutations.append(output)
        for mutated in mutations:
            with self.assertRaises(RuntimeError):
                validate_contract(mutated)

    def test_validator_rejects_top_level_resume_and_source_pin_mutations(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        mutations = []
        purpose = copy.deepcopy(contract)
        purpose["purpose"] = "Fresh confirmation and champion selection"
        mutations.append(purpose)
        checkpoint = copy.deepcopy(contract)
        checkpoint["resume"]["checkpoint_after_users"] = 1
        mutations.append(checkpoint)
        source_hash = copy.deepcopy(contract)
        source_hash["allowed_input_artifacts"]["stage2_cohort"]["sha256"] = "0" * 64
        mutations.append(source_hash)
        source_bytes = copy.deepcopy(contract)
        source_bytes["allowed_input_artifacts"]["stage2_cohort"]["bytes"] += 1
        mutations.append(source_bytes)
        for mutated in mutations:
            with self.assertRaises(RuntimeError):
                validate_contract(mutated)


if __name__ == "__main__":
    unittest.main()
