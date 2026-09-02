from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from verify_rec_ev_019c_resource_dry_run import verify_manifest


class RecEv019CResourceDryRunVerifierTest(unittest.TestCase):
    def test_current_resource_dry_run_passes(self) -> None:
        result = verify_manifest(
            ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-resource-dry-run.json"
        )
        self.assertEqual("PASS", result["status"])
        self.assertFalse(result["real_validation_ready"])
        self.assertFalse(result["locked_test_opened"])

    def _isolated(self) -> tuple[tempfile.TemporaryDirectory, Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        isolated = Path(temporary.name)
        paths = [
            "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json",
            "docs/recommendation/evidence/results/rec-ev-019c-resource-dry-run.json",
            "docs/recommendation/evidence/manifests/rec-ev-019c-resource-dry-run.json",
        ]
        for relative in paths:
            target = isolated / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        return temporary, isolated, isolated / paths[1], isolated / paths[2]

    @staticmethod
    def _rewrite_result_and_manifest(result_path: Path, manifest_path: Path, result: dict) -> None:
        payload = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        result_path.write_bytes(payload)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["bytes"] = len(payload)
        manifest["artifacts"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_rejects_row_read_even_with_updated_checksum(self) -> None:
        temporary, isolated, result_path, manifest_path = self._isolated()
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["rating_rows_or_feature_vectors_read"] = True
            self._rewrite_result_and_manifest(result_path, manifest_path, result)
            with self.assertRaisesRegex(RuntimeError, "read data rows"):
                verify_manifest(manifest_path, root=isolated)
        finally:
            temporary.cleanup()

    def test_rejects_missing_blocker_even_with_updated_checksum(self) -> None:
        temporary, isolated, result_path, manifest_path = self._isolated()
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["implementation_blockers"].pop()
            self._rewrite_result_and_manifest(result_path, manifest_path, result)
            with self.assertRaisesRegex(RuntimeError, "blocker set"):
                verify_manifest(manifest_path, root=isolated)
        finally:
            temporary.cleanup()

    def test_rejects_locked_test_opened_even_with_updated_checksum(self) -> None:
        temporary, isolated, result_path, manifest_path = self._isolated()
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["locked_test_opened"] = True
            self._rewrite_result_and_manifest(result_path, manifest_path, result)
            with self.assertRaisesRegex(RuntimeError, "crossed boundary"):
                verify_manifest(manifest_path, root=isolated)
        finally:
            temporary.cleanup()

    def test_rejects_workload_tamper_even_with_updated_checksum(self) -> None:
        temporary, isolated, result_path, manifest_path = self._isolated()
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["estimated_work"]["full_catalog_user_item_scores"] -= 1
            self._rewrite_result_and_manifest(result_path, manifest_path, result)
            with self.assertRaisesRegex(RuntimeError, "workload estimate"):
                verify_manifest(manifest_path, root=isolated)
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
