from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.validate_rec_ev_019e_contract import validate_contract


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019e-no-retune-incremental-applicability-gate.json"


class RecEv019eContractTests(unittest.TestCase):
    def test_contract_is_post_hoc_fail_closed_and_parameter_free(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        summary = validate_contract(contract)
        self.assertEqual(summary["status"], "PASS_REC_EV_019E_CONTRACT")
        self.assertTrue(summary["post_hoc"])
        self.assertEqual(summary["confirmatory_users"], 1053)
        self.assertEqual(contract["candidate"]["parameters"], [])
        self.assertEqual(contract["candidate"]["thresholds"], [])
        self.assertFalse(contract["current_authorization"]["locked_test_access"])
        self.assertIsNone(contract["invariants"]["champion"])
        self.assertFalse(contract["invariants"]["product_policy_updated"])

    def test_mutation_of_post_hoc_authority_is_rejected(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(contract)
        mutated["evidence_classification"]["independent_confirmatory_evidence"] = True
        with self.assertRaisesRegex(RuntimeError, "confirmatory authority"):
            validate_contract(mutated)

    def test_mutation_of_routing_is_rejected(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(contract)
        mutated["candidate"]["routing_priority"][0]["source_arm"] = "K10"
        with self.assertRaisesRegex(RuntimeError, "routing priority"):
            validate_contract(mutated)

    def test_mutation_of_safety_priority_is_rejected(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(contract)
        mutated["decision_rule"]["priority"].reverse()
        with self.assertRaisesRegex(RuntimeError, "decision priority"):
            validate_contract(mutated)


if __name__ == "__main__":
    unittest.main()
