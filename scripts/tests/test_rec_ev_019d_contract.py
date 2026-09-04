from __future__ import annotations

import json
import copy
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019d-prefix-ablation-artifacts.json"
AMENDMENT = ROOT / "docs/recommendation/contracts/rec-ev-019d-post-run-audit-amendment.json"

from scripts.validate_rec_ev_019d_contract import validate_contract


class RecEv019dContractTests(unittest.TestCase):
    def test_preregistered_contract_is_fail_closed_and_exact(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(contract["cohort"]["expected_users"], 1479)
        self.assertEqual(contract["confirmatory_set"]["expected_users"], 1053)
        self.assertEqual(contract["confirmatory_set"]["expected_excluded_users"], 426)
        self.assertEqual(contract["model"]["trial_id"], "B8_LIGHTFM-T003")
        self.assertEqual(contract["model"]["seed"], 17)
        self.assertEqual(contract["model"]["candidate_count"], 41625)
        self.assertEqual(contract["bootstrap"], {
            "unit": "USER",
            "method": "PERCENTILE",
            "iterations": 10000,
            "seed": 20260924,
            "ndcg_interval": "TWO_SIDED_95_PERCENT_2_5_AND_97_5_PERCENTILES",
            "harm_interval": "ONE_SIDED_95_PERCENT_UPPER_95TH_PERCENTILE",
        })
        self.assertFalse(contract["current_authorization"]["locked_test_access"])
        self.assertFalse(contract["current_authorization"]["champion_selection"])
        self.assertFalse(contract["current_authorization"]["product_policy_change"])
        self.assertEqual(contract["invariants"], {
            "execution_role": "VALIDATION_019D",
            "locked_test_used": False,
            "champion": None,
            "product_policy_updated": False,
        })
        self.assertEqual([row["status"] for row in contract["decision_rule"]["priority"]], ["FAIL", "FAIL", "PASS", "INCONCLUSIVE"])
        self.assertFalse(contract["rec_ev_019c_prediction_reuse"]["allowed"])
        self.assertEqual(validate_contract(contract)["status"], "PASS_REC_EV_019D_CONTRACT")
        amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
        self.assertEqual(
            amendment["authoritative_cache_policy_for_any_reverification_or_follow_up"]["cache_absent_action"],
            "FAIL_CLOSED_NO_REFIT",
        )

    def test_mutation_of_safety_priority_is_rejected(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(contract)
        mutated["decision_rule"]["priority"][0]["reason"] = "IGNORED"
        with self.assertRaisesRegex(RuntimeError, "safety priority"):
            validate_contract(mutated)

    def test_mutation_of_locked_test_boundary_is_rejected(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(contract)
        mutated["current_authorization"]["locked_test_access"] = True
        with self.assertRaisesRegex(RuntimeError, "Locked Test"):
            validate_contract(mutated)

    def test_mutation_of_effective_refit_policy_is_rejected(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(amendment)
        mutated["authoritative_cache_policy_for_any_reverification_or_follow_up"]["implicit_or_explicit_refit_allowed"] = True
        with self.assertRaisesRegex(RuntimeError, "refit policy"):
            validate_contract(contract, mutated)


if __name__ == "__main__":
    unittest.main()
