from __future__ import annotations

import copy
import json
import unittest

from scripts.preflight_rec_ev_027_membership import fold_of
from scripts.validate_rec_ev_027_contract import DEFAULT, validate


class RecEv027ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(DEFAULT.read_text(encoding="utf-8"))

    def test_committed_contract(self) -> None:
        validate(self.contract, verify_files=True)

    def test_locked_test_cannot_be_enabled(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["authorization"]["locked_test_access"] = True
        with self.assertRaisesRegex(RuntimeError, "locked_test_access"):
            validate(changed, verify_files=False)

    def test_cold_item_identity_feature_cannot_be_removed_from_guard(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["strict_item_firewall"]["forbidden_cold_item_access"].remove("ITEM_ID_FEATURE")
        with self.assertRaisesRegex(RuntimeError, "cold firewall"):
            validate(changed, verify_files=False)

    def test_target_slate_is_even(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["tracks"]["KOREAN_ORIGIN_COLD"]["target_n"] = 5
        with self.assertRaisesRegex(RuntimeError, "even"):
            validate(changed, verify_files=False)

    def test_unrated_cannot_be_negative(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["evaluation_labels"]["unrated_semantics"] = "NEGATIVE"
        with self.assertRaisesRegex(RuntimeError, "unrated"):
            validate(changed, verify_files=False)

    def test_catalog_pin_inventory_cannot_be_partial(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["catalog_content_build"]["artifact_pins"] = {}
        with self.assertRaisesRegex(RuntimeError, "pin inventory"):
            validate(changed, verify_files=False)

    def test_approved_contract_requires_audit_and_catalog_pins(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["status"] = "APPROVED_FOR_ADAPTIVE_STRICT_ITEM_COLD_EXECUTION"
        changed["design_audit"].pop("latest_pass", None)
        with self.assertRaisesRegex(RuntimeError, "audit pass"):
            validate(changed, verify_files=False)

    def test_fold_hash_is_stable_and_bounded(self) -> None:
        salt = self.contract["strict_item_firewall"]["fold_salt"]
        self.assertEqual(fold_of(1, salt), fold_of(1, salt))
        self.assertTrue(all(0 <= fold_of(movie_id, salt) < 5 for movie_id in range(1, 100)))

    def test_lightfm_rng_mapping_is_required(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["models"]["FEATURE_ONLY_LIGHTFM"]["index_and_rng"] = "AMBIGUOUS"
        with self.assertRaisesRegex(RuntimeError, "LightFM RNG"):
            validate(changed, verify_files=False)

    def test_replication_union_bootstrap_is_required(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["statistics"]["replication_pooled_estimator"] = "CONTRAST_SPECIFIC"
        with self.assertRaisesRegex(RuntimeError, "bootstrap universe"):
            validate(changed, verify_files=False)

    def test_empty_direct_branch_is_required(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["screen_and_replication"]["empty_direct"] = "CONTINUE"
        with self.assertRaisesRegex(RuntimeError, "empty direct"):
            validate(changed, verify_files=False)

    def test_structured_helper_is_pinned(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["allowed_input_artifacts"].pop("structured_feature_helper")
        with self.assertRaisesRegex(RuntimeError, "source inventory"):
            validate(changed, verify_files=False)

    def test_bpr_pair_sampler_must_be_bounded(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["models"]["E5_TO_WARM_BPR_RIDGE"]["teacher_pairs"] = "CARTESIAN_SHA_SORT"
        with self.assertRaisesRegex(RuntimeError, "pair sampler"):
            validate(changed, verify_files=False)

    def test_bpr_mapper_rejects_untouched_initial_factors(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["models"]["E5_TO_WARM_BPR_RIDGE"]["mapper"] = "ALL_NONZERO_INITIAL_FACTORS"
        with self.assertRaisesRegex(RuntimeError, "untouched BPR"):
            validate(changed, verify_files=False)

    def test_bpr_optimizer_rejects_obsolete_pair_hash_order(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["models"]["E5_TO_WARM_BPR_RIDGE"]["teacher_optimizer"] += "; PAIR_HASH_ASC"
        with self.assertRaisesRegex(RuntimeError, "obsolete BPR"):
            validate(changed, verify_files=False)

    def test_bpr_refit_requires_pair_touched_only(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["models"]["E5_TO_WARM_BPR_RIDGE"]["mapper_refit"] = "ALL_NONZERO_FACTORS"
        with self.assertRaisesRegex(RuntimeError, "refit includes untouched"):
            validate(changed, verify_files=False)


if __name__ == "__main__":
    unittest.main()
