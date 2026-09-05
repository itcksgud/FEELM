from __future__ import annotations

import copy
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/validate_rec_ev_026_design.py"
SPEC = importlib.util.spec_from_file_location("validate_rec_ev_026_design", PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class FakeArchive:
    def __init__(self, array: np.ndarray, files: list[str] | None = None) -> None:
        self.array = array
        self.files = files or ["item_factors"]

    def __enter__(self) -> "FakeArchive":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __getitem__(self, _key: str) -> np.ndarray:
        return self.array


class RecEv026DesignTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = module.load(module.DEFAULT)

    def reject_fast(self, changed: dict, message: str) -> None:
        with mock.patch.object(module, "verify_artifact"), mock.patch.object(module, "verify_teacher"), mock.patch.object(module, "verify_reuse_and_no_outcome"):
            with self.assertRaisesRegex(RuntimeError, message):
                module.validate(changed)

    def test_default_contract(self) -> None:
        module.validate(self.contract)

    def test_rejects_teacher_user_overlap(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["source_users"]["old_user_bucket_inclusive"] = [0, 59]
        self.reject_fast(changed, "teacher/evaluation")

    def test_rejects_e5_revision(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["common_support"]["e5_revision"] = "latest"
        self.reject_fast(changed, "common support")

    def test_rejects_registry_namespace_and_outcome_column(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["exposure_registry"]["sources"][0]["namespace"] = "022"
        self.reject_fast(changed, "registry namespace")
        changed = copy.deepcopy(self.contract)
        changed["exposure_registry"]["sources"][0]["columns"].append("rating")
        self.reject_fast(changed, "outcome column")

    def test_rejects_reuse_proof_and_control_floor(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["exposure_registry"]["reuse_proofs"].pop("023A")
        self.reject_fast(changed, "reuse proof|canonical")
        changed = copy.deepcopy(self.contract)
        changed["experiments"]["REC-EV-026B"]["minimum_unique_controls"] = 199
        self.reject_fast(changed, "floor")

    def test_rejects_duplicate_salt_and_rating_ban(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["experiments"]["REC-EV-026A"]["control_salts"][0] = changed["experiments"]["REC-EV-026A"]["target_salts"][0]
        self.reject_fast(changed, "duplicate salt")
        changed = copy.deepcopy(self.contract)
        changed["membership"]["selection_forbidden"].remove("RATING")
        self.reject_fast(changed, "leakage ban")

    def test_rejects_mapper_ridge_fit_gate_and_candidate_access(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["mapper"]["ridge"] = "SKLEARN_DEFAULT_WITH_INTERCEPT"
        self.reject_fast(changed, "ridge")
        changed = copy.deepcopy(self.contract)
        changed["mapper"]["fit_gate"] = []
        self.reject_fast(changed, "fit gate")
        changed = copy.deepcopy(self.contract)
        changed["mapper"]["candidate_factor_access"] = "ALLOW"
        self.reject_fast(changed, "candidate factor")

    def test_rejects_tie_head_timestamp_q_and_loss_sign(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["heads"]["tie_payload"] = changed["heads"]["tie_payload"].replace("|MOVIE_ID)", "|HEAD|MOVIE_ID)")
        self.reject_fast(changed, "tie payload")
        changed = copy.deepcopy(self.contract)
        changed["rating_scale"]["evaluation_q"] = "TIMESTAMP_PREFIX_Q"
        self.reject_fast(changed, "rating scale")
        changed = copy.deepcopy(self.contract)
        changed["metrics"]["loss"] = "MIN_TOP2_Q"
        self.reject_fast(changed, "loss sign")

    def test_rejects_contrast_count_order_and_sign(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["statistics"]["contrast_enumeration"]["count"] = 311
        self.reject_fast(changed, "contrast count")
        changed = copy.deepcopy(self.contract)
        changed["heads"]["reporting_order"] = list(reversed(changed["heads"]["reporting_order"]))
        self.reject_fast(changed, "head order")
        changed = copy.deepcopy(self.contract)
        changed["metrics"]["incremental_endpoints"][1] = "SAFETY_E5_TO_BPR_LOSS_MINUS_BASELINE_LOSS"
        self.reject_fast(changed, "incremental endpoint sign")

    def test_rejects_bootstrap_namespace_margin_and_postlabel_retune(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["statistics"]["bootstrap_namespace"] = "different"
        self.reject_fast(changed, "bootstrap namespace")
        changed = copy.deepcopy(self.contract)
        changed["decision"]["incremental_target_utility_margin"] = 0.0
        self.reject_fast(changed, "incremental decision margin")
        changed = copy.deepcopy(self.contract)
        changed["resume"]["post_label_change_requires_new_evidence_id"].remove("ALPHA")
        self.reject_fast(changed, "resume/post-label")

    def test_bootstrap_golden_fixtures(self) -> None:
        for fixture in self.contract["statistics"]["bootstrap_golden_fixtures"]:
            self.assertEqual(fixture["uint64"], module.bootstrap_uint64(fixture["attempt"], fixture["user_key022"]))
            self.assertEqual(fixture["weight"], module.bootstrap_weight(fixture["attempt"], fixture["user_key022"]))

    def test_rejects_factor_shape_dtype_nan_key_seed_and_order(self) -> None:
        core = pd.DataFrame({"movie_id": np.arange(41625, dtype=np.int64)})
        arrays = [
            (np.zeros((1, 128), dtype=np.float32), ["item_factors"], "shape"),
            (np.zeros((41625, 128), dtype=np.float64), ["item_factors"], "dtype"),
            (np.full((41625, 128), np.nan, dtype=np.float32), ["item_factors"], "nonfinite"),
            (np.zeros((1, 1), dtype=np.float32), ["wrong"], "NPZ key"),
        ]
        for array, files, message in arrays:
            with self.subTest(message=message), mock.patch.object(module, "verify_artifact"), mock.patch.object(module.pd, "read_parquet", return_value=core), mock.patch.object(module.np, "load", return_value=FakeArchive(array, files)):
                with self.assertRaisesRegex(RuntimeError, message):
                    module.verify_teacher(self.contract)
        changed = copy.deepcopy(self.contract)
        changed["teacher"]["seeds"] = [42, 17, 73, 101, 211]
        with self.assertRaisesRegex(RuntimeError, "teacher seeds"):
            module.verify_teacher(changed)
        changed = copy.deepcopy(self.contract)
        changed["teacher"]["factor_artifacts"] = {key: changed["teacher"]["factor_artifacts"][key] for key in ["42", "17", "73", "101", "211"]}
        with self.assertRaisesRegex(RuntimeError, "factor order"):
            module.verify_teacher(changed)

    def test_rejects_nested_outcome_in_failed_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = []
            for name in ("protocol-lock.json", "source-manifest.json"):
                path = root / name
                path.write_text("{}\n", encoding="utf-8")
                files.append({"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            nested = root / "cache"
            nested.mkdir()
            (nested / "outcome.parquet").write_bytes(b"outcome")
            proof = {"files": files, "forbidden_additional_files": True}
            with self.assertRaisesRegex(RuntimeError, "contains outcome"):
                module.verify_failed_root(proof, "FIXTURE")


if __name__ == "__main__":
    unittest.main()
