from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/run_rec_ev_026_preflight.py"
SPEC = importlib.util.spec_from_file_location("run_rec_ev_026_preflight", PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def contract() -> dict:
    return module.load_contract(module.DEFAULT)


class ReaderTests(unittest.TestCase):
    def test_movie_reader_qualifies_user_before_movie_and_never_parses_suffix(self) -> None:
        allowed = next(value for value in range(1, 10000) if module.current_user_allowed(value))
        blocked = next(value for value in range(1, 10000) if not module.current_user_allowed(value))
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            archive = Path(temp) / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("ml-32m/ratings.csv", f"userId,movieId,rating,timestamp\n{blocked},BROKEN,not-read,not-read\n{allowed},7,BROKEN,BROKEN\n")
            self.assertEqual(list(module.movie_id_only_rows(archive, "ml-32m/ratings.csv", 300000)), [(allowed, 7)])

    def test_registry_reads_only_declared_id_columns(self) -> None:
        key = "a" * 64
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            path = Path(temp) / "ids.parquet"
            pd.DataFrame({"user_key": [key], "target_movie_ids": [np.asarray([2, 1], dtype=np.int32)], "rating": [5.0], "timestamp": [99]}).to_parquet(path, index=False)
            value = contract()
            value["exposure_registry"]["sources"] = [{"id": "FIXTURE", "path": str(path), "bytes": path.stat().st_size, "sha256": module.sha256_file(path), "namespace": "022", "columns": ["user_key", "target_movie_ids"]}]
            result = module.build_exposure_registry(value)
            self.assertEqual(result.to_dict("records"), [{"namespace": "022", "user_key": key, "movie_id": 1}, {"namespace": "022", "user_key": key, "movie_id": 2}])


class MembershipTests(unittest.TestCase):
    def test_selection_is_deterministic_unique_disjoint_and_anonymous(self) -> None:
        value = contract()
        support = pd.DataFrame({
            "movie_id": list(range(1, 31)) + list(range(101, 111)) + list(range(201, 211)),
            "release_year": [2010] * 30 + [2015] * 10 + [2021] * 10,
            "is_korean": [False] * 30 + [True] * 10 + [False] * 10,
            "has_teacher_factor": [True] * 50,
        })
        raw_user = 123
        pairs = [(raw_user, movie) for movie in support["movie_id"]]
        empty_registry = pd.DataFrame(columns=["namespace", "user_key", "movie_id"])
        with mock.patch.object(module, "movie_id_only_rows", return_value=iter(pairs)):
            first, summary = module.select_membership(value, support, empty_registry)
        with mock.patch.object(module, "movie_id_only_rows", return_value=iter(pairs)):
            second, _ = module.select_membership(value, support, empty_registry)
        pd.testing.assert_frame_equal(first, second)
        self.assertNotIn("user_id", first.columns)
        self.assertNotIn(raw_user, first.astype(str).to_numpy())
        for evidence_id, group in first.groupby("evidence_id"):
            profile = set(group.loc[group["role"] == "PROFILE", "movie_id"])
            self.assertEqual(len(profile), 14)
            for _panel, panel in group.loc[group["role"] != "PROFILE"].groupby("panel"):
                targets = panel.loc[panel["role"] == "TARGET", "movie_id"].tolist()
                controls = panel.loc[panel["role"] == "CONTROL", "movie_id"].tolist()
                self.assertEqual((len(set(targets)), len(set(controls))), (4, 4))
                self.assertFalse(profile.intersection(controls))
        self.assertEqual(summary["movie_id_rows_parsed"], len(pairs))

    def test_role_head_is_not_part_of_membership_order(self) -> None:
        key = "0" * 64
        expected = module.role_order(key, [4, 3, 2, 1], "salt", "TARGET")
        self.assertEqual(expected, module.role_order(key, [1, 2, 3, 4], "salt", "TARGET"))
        self.assertNotEqual(expected, module.role_order(key, [1, 2, 3, 4], "salt", "CONTROL"))


class ResumeTests(unittest.TestCase):
    def test_resume_without_lock_fails_before_compute(self) -> None:
        value = contract()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            root = Path(temp)
            with mock.patch.object(module, "output_root", return_value=root), mock.patch.object(module, "output_path", side_effect=lambda c, key: root / c["outputs"][key]), mock.patch.object(module, "expected_lock_state") as expected, mock.patch.object(module, "compute_and_write") as compute:
                with self.assertRaises(module.ResumeError):
                    module.preflight(value, resume=True)
                expected.assert_not_called()
                compute.assert_not_called()

    def test_partial_lock_and_partial_result_fail_closed(self) -> None:
        value = contract()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            root = Path(temp)
            root.mkdir(exist_ok=True)
            lock_path = root / value["outputs"]["protocol_lock"]
            lock_path.write_text("{}", encoding="utf-8")
            with mock.patch.object(module, "output_root", return_value=root), mock.patch.object(module, "output_path", side_effect=lambda c, key: root / c["outputs"][key]):
                with self.assertRaises(module.ResumeError):
                    module.create_or_verify_lock(value, resume=True)
            lock_path.unlink()
            with mock.patch.object(module, "output_root", return_value=root), mock.patch.object(module, "output_path", side_effect=lambda c, key: root / c["outputs"][key]):
                module.create_or_verify_lock(value, resume=False)
                (root / value["outputs"]["preflight"]).write_text("{}", encoding="utf-8")
                with self.assertRaises(module.ResumeError):
                    module.preflight(value, resume=True)

    def test_downstream_result_without_lock_refuses_new_lock(self) -> None:
        value = contract()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            root = Path(temp)
            (root / value["outputs"]["preflight"]).write_text("{}", encoding="utf-8")
            with mock.patch.object(module, "output_root", return_value=root), mock.patch.object(module, "output_path", side_effect=lambda c, key: root / c["outputs"][key]), mock.patch.object(module, "expected_lock_state") as expected:
                with self.assertRaisesRegex(module.ResumeError, "without lock"):
                    module.create_or_verify_lock(value, resume=False)
                expected.assert_not_called()

    def test_forged_integrity_inventory_and_counters_are_rejected(self) -> None:
        value = contract()
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            root = Path(temp)
            paths = [root / value["outputs"][key] for key in ("preflight", "progress", "preflight_integrity", "exposure_registry")]
            paths.extend([root / module.REGISTRY_INTEGRITY_PATH, root / module.MEMBERSHIP_PATH, root / module.MEMBERSHIP_INTEGRITY_PATH])
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            forged = {"schema_version": 1, "run_signature": "sig", "artifacts": {}, "rating_value_bytes_parsed": 999, "timestamp_bytes_parsed": 999}
            original_read = module.read_json
            def fake_read(path: Path):
                if path.resolve() == (root / value["outputs"]["preflight_integrity"]).resolve():
                    return forged
                return original_read(path)
            with mock.patch.object(module, "output_root", return_value=root), mock.patch.object(module, "output_path", side_effect=lambda c, key: root / c["outputs"][key]), mock.patch.object(module, "run_signature", return_value="sig"), mock.patch.object(module, "read_json", side_effect=fake_read):
                with self.assertRaisesRegex(module.ResumeError, "metadata|inventory"):
                    module.verify_existing_result(value)

    def test_exact_completed_result_is_read_only_on_resume(self) -> None:
        value = contract()
        fake = {"experiments": {"REC-EV-026A": {}, "REC-EV-026B": {}}}
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as temp:
            root = Path(temp)
            paths = [root / value["outputs"][key] for key in ("preflight", "progress", "preflight_integrity", "exposure_registry")]
            paths.extend([root / module.REGISTRY_INTEGRITY_PATH, root / module.MEMBERSHIP_PATH, root / module.MEMBERSHIP_INTEGRITY_PATH])
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            with mock.patch.object(module, "create_or_verify_lock"), mock.patch.object(module, "output_root", return_value=root), mock.patch.object(module, "output_path", side_effect=lambda c, key: root / c["outputs"][key]), mock.patch.object(module, "verify_existing_result", return_value=fake), mock.patch.object(module, "compute_and_write") as compute:
                result = module.preflight(value, resume=True)
        self.assertEqual(result["status"], "REUSED_EXACT_PREFLIGHT")
        compute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
