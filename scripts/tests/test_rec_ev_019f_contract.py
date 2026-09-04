from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.validate_rec_ev_019f_contract import validate


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "docs/recommendation/contracts/rec-ev-019f-independent-temporal-routing.json").read_text(encoding="utf-8"))


class RecEv019fContractTest(unittest.TestCase):
    def test_contract_passes_without_opening_result_artifacts(self) -> None:
        result = validate(copy.deepcopy(CONTRACT), root=ROOT, check_files=False)
        self.assertEqual(result["status"], "PASS_REC_EV_019F_CONTRACT")
        self.assertEqual(result["independence_unit"], "SOURCE_ROW_AND_TEMPORAL_WINDOW")
        self.assertFalse(result["user_independent"])

    def test_mutation_user_independent_fails(self) -> None:
        mutated = copy.deepcopy(CONTRACT)
        mutated["evidence_classification"]["user_independent"] = True
        with self.assertRaisesRegex(RuntimeError, "user independence"):
            validate(mutated, root=ROOT, check_files=False)

    def test_mutation_routing_semantics_fails(self) -> None:
        mutated = copy.deepcopy(CONTRACT)
        mutated["frozen_routing_semantics"]["candidate"]["routing_priority"][1]["source_arm"] = "K5"
        with self.assertRaisesRegex(RuntimeError, "byte-equivalent"):
            validate(mutated, root=ROOT, check_files=False)

    def test_mutation_test_path_in_allowlist_fails(self) -> None:
        mutated = copy.deepcopy(CONTRACT)
        mutated["allowed_input_artifacts"]["validation_ratings"]["path"] = "outputs/recommendation-evidence/global-time-v1/test.parquet"
        with self.assertRaisesRegex(RuntimeError, "intersects forbidden"):
            validate(mutated, root=ROOT, check_files=False)

    def test_mutation_gate_order_fails(self) -> None:
        mutated = copy.deepcopy(CONTRACT)
        mutated["decision_rule"]["priority"].reverse()
        with self.assertRaisesRegex(RuntimeError, "priority"):
            validate(mutated, root=ROOT, check_files=False)

    def test_mutation_observed_count_disclosure_fails(self) -> None:
        mutated = copy.deepcopy(CONTRACT)
        mutated["evidence_classification"]["observed_audit_expectations_are_not_blind"]["strict_users"] = 801
        with self.assertRaisesRegex(RuntimeError, "observed audit"):
            validate(mutated, root=ROOT, check_files=False)


if __name__ == "__main__":
    unittest.main()
