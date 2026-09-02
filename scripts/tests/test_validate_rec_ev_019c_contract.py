from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from validate_rec_ev_019c_contract import validate_contract


class ValidateRecEv019CContractTest(unittest.TestCase):
    def setUp(self) -> None:
        path = ROOT / "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json"
        self.contract = json.loads(path.read_text(encoding="utf-8"))

    def test_current_contract_passes_without_running_a_model(self) -> None:
        result = validate_contract(self.contract)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(41625, result["candidate_movies"])
        self.assertFalse(result["locked_test_opened"])

    def test_rejects_real_validation_authorization(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["current_authorization"]["real_validation_fit_or_score"] = True
        with self.assertRaisesRegex(RuntimeError, "unsafe authorization"):
            validate_contract(mutated)

    def test_rejects_locked_test_path_in_allowlist(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["allowed_input_artifacts"]["validation_windows"] = (
            "outputs/recommendation-evidence/rec-ev-019a/locked-test-evaluation-windows.parquet"
        )
        with self.assertRaisesRegex(RuntimeError, "allowed input"):
            validate_contract(mutated)

    def test_rejects_candidate_count_drift(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["candidate_and_ranking"]["core_movie_count"] = 41624
        with self.assertRaisesRegex(RuntimeError, "candidate count"):
            validate_contract(mutated)

    def test_rejects_unrated_negative_sampling(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["base_training_semantics"]["bpr_unrated_negative_sampling_forbidden"] = False
        with self.assertRaisesRegex(RuntimeError, "unrated"):
            validate_contract(mutated)

    def test_rejects_model_trial_count_above_grid(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["models"]["B2_ITEM_KNN"]["trial_count"] = 30
        with self.assertRaisesRegex(RuntimeError, "trial count mismatch"):
            validate_contract(mutated)

    def test_rejects_raw_score_rrf(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["models"]["B9_RRF"]["raw_score_input_forbidden"] = False
        with self.assertRaisesRegex(RuntimeError, "raw scores"):
            validate_contract(mutated)

    def test_rejects_unpinned_lightfm_dependency(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["models"]["B8_LIGHTFM"]["dependency_rule"] = "BEST_EFFORT"
        with self.assertRaisesRegex(RuntimeError, "supply-chain"):
            validate_contract(mutated)

    def test_rejects_product_champion(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["adoption_boundary"]["validation_output_champion"] = "B8_LIGHTFM"
        with self.assertRaisesRegex(RuntimeError, "champion"):
            validate_contract(mutated)


if __name__ == "__main__":
    unittest.main()
