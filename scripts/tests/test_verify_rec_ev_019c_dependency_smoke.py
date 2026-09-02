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

from verify_rec_ev_019c_dependency_smoke import verify_manifest


class RecEv019CDependencySmokeVerifierTest(unittest.TestCase):
    def test_current_dependency_smoke_passes(self) -> None:
        result = verify_manifest(
            ROOT / "docs/recommendation/evidence/manifests/rec-ev-019c-lightfm-linux-smoke.json"
        )
        self.assertEqual("PASS", result["status"])
        self.assertFalse(result["locked_test_opened"])

    def _isolated(self) -> tuple[tempfile.TemporaryDirectory, Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        isolated = Path(temporary.name)
        paths = [
            "docs/recommendation/contracts/rec-ev-019c-validation-artifacts.json",
            "requirements-rec-ev-019c.lock",
            "docs/recommendation/evidence/results/rec-ev-019c-lightfm-linux-smoke.json",
            "docs/recommendation/evidence/manifests/rec-ev-019c-lightfm-linux-smoke.json",
        ]
        for relative in paths:
            target = isolated / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        return (
            temporary,
            isolated,
            isolated / paths[2],
            isolated / paths[3],
        )

    @staticmethod
    def _rewrite_result_and_manifest(result_path: Path, manifest_path: Path, result: dict) -> None:
        payload = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        result_path.write_bytes(payload)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["bytes"] = len(payload)
        manifest["artifacts"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_rejects_pairwise_loss_even_with_updated_checksum(self) -> None:
        temporary, isolated, result_path, manifest_path = self._isolated()
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["loss"] = "warp"
            self._rewrite_result_and_manifest(result_path, manifest_path, result)
            with self.assertRaisesRegex(RuntimeError, "pairwise loss"):
                verify_manifest(manifest_path, root=isolated)
        finally:
            temporary.cleanup()

    def test_rejects_champion_even_with_updated_checksum(self) -> None:
        temporary, isolated, result_path, manifest_path = self._isolated()
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["product_champion"] = "B8_LIGHTFM"
            self._rewrite_result_and_manifest(result_path, manifest_path, result)
            with self.assertRaisesRegex(RuntimeError, "champion"):
                verify_manifest(manifest_path, root=isolated)
        finally:
            temporary.cleanup()

    def test_rejects_runtime_lock_drift(self) -> None:
        temporary, isolated, _, manifest_path = self._isolated()
        try:
            lock_path = isolated / "requirements-rec-ev-019c.lock"
            lock_path.write_text(lock_path.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "lock hash"):
                verify_manifest(manifest_path, root=isolated)
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
