from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import run_rec_ev_025_feature_transfer as base  # noqa: E402
import run_rec_ev_025_feature_transfer_r1 as wrapper  # noqa: E402
import validate_rec_ev_025_feature_transfer_r1_contract as validator  # noqa: E402


CORRECTION = ROOT / "docs/recommendation/contracts/rec-ev-025ab-feature-transfer-execution-r1.json"


class MaterializationTests(unittest.TestCase):
    def test_exact_materialized_contract(self) -> None:
        contract = validator.materialize_contract(CORRECTION)
        validator.validate_contract(contract)
        self.assertEqual(contract["experiments"]["REC-EV-025A"]["minimum_users"], 150)
        self.assertEqual(contract["experiments"]["REC-EV-025B"]["minimum_unique_targets"], 200)

    def test_only_declared_semantics_change(self) -> None:
        correction = validator.load_correction(CORRECTION)
        base_path = ROOT / correction["base_contract"]["path"]
        original = json.loads(base_path.read_text(encoding="utf-8"))
        revised = validator.materialize_contract(CORRECTION)
        for key in correction["unchanged_sections"]:
            self.assertEqual(revised[key], original[key], key)
        for evidence_id in ("REC-EV-025A", "REC-EV-025B"):
            reduced = copy.deepcopy(revised["experiments"][evidence_id])
            reduced.pop("minimum_users")
            reduced.pop("minimum_unique_targets")
            self.assertEqual(reduced, original["experiments"][evidence_id])

    def test_failed_attempt_is_declared_preoutcome(self) -> None:
        contract = validator.materialize_contract(CORRECTION)
        for failure in contract["failed_locked_attempts"].values():
            self.assertEqual(set(failure["files_created"]), {"protocol-lock.json", "source-manifest.json"})
            self.assertEqual(failure["selected_profile_rating_rows_parsed"], 0)
            self.assertEqual(failure["evaluation_rating_rows_parsed"], 0)
            self.assertFalse(failure["evaluation_labels_opened"])

    def test_mutated_materialized_contract_fails(self) -> None:
        contract = validator.materialize_contract(CORRECTION)
        contract["decision"]["absolute_target_margin"] = 0.0
        with self.assertRaises(ValueError):
            validator.validate_contract(contract)


class WrapperTests(unittest.TestCase):
    def test_wrapper_installs_only_validation_and_loader(self) -> None:
        old_validator, old_loader, old_default = base.validate_contract, base.load_contract, base.DEFAULT
        try:
            wrapper.install_r1_validation()
            self.assertIs(base.validate_contract, validator.validate_contract)
            self.assertIs(base.load_contract, wrapper.load_contract)
            self.assertEqual(base.DEFAULT, CORRECTION)
        finally:
            base.validate_contract, base.load_contract, base.DEFAULT = old_validator, old_loader, old_default

    def test_default_cli_loader_materializes_r1(self) -> None:
        old_validator, old_loader, old_default = base.validate_contract, base.load_contract, base.DEFAULT
        try:
            wrapper.install_r1_validation()
            loaded = base.load_contract(base.DEFAULT)
            self.assertEqual(loaded["contract_id"], "rec-ev-025ab-feature-transfer-execution-r1-v1")
            self.assertEqual(loaded["output_roots"]["REC-EV-025A"], "outputs/recommendation-evidence/rec-ev-025a-r1")
        finally:
            base.validate_contract, base.load_contract, base.DEFAULT = old_validator, old_loader, old_default

    def test_corrected_spec_reconstructs_preflight_summary(self) -> None:
        contract = validator.materialize_contract(CORRECTION)
        for evidence_id in ("REC-EV-025A", "REC-EV-025B"):
            spec = contract["experiments"][evidence_id]
            self.assertIn("minimum_users", spec)
            self.assertIn("minimum_unique_targets", spec)

    def test_new_output_namespace_isolation(self) -> None:
        contract = validator.materialize_contract(CORRECTION)
        self.assertNotEqual(base.output_path(contract, "REC-EV-025A", "protocol_lock"), ROOT / "outputs/recommendation-evidence/rec-ev-025a/protocol-lock.json")
        self.assertNotEqual(base.output_path(contract, "REC-EV-025B", "protocol_lock"), ROOT / "outputs/recommendation-evidence/rec-ev-025b/protocol-lock.json")

    def test_absent_r1_lock_fails_before_hashing(self) -> None:
        contract = validator.materialize_contract(CORRECTION)
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            with mock.patch.object(base, "output_path", side_effect=lambda c, e, name: root / e / c["outputs"][name]), mock.patch.object(base, "expected_lock_state") as expected:
                with self.assertRaises(base.ResumeError):
                    base.create_or_verify_lock(contract, "REC-EV-025A", resume=True)
                expected.assert_not_called()


if __name__ == "__main__":
    unittest.main()
