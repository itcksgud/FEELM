from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from recommendation_evidence_paths import artifact_matches, repository_path


class RecommendationEvidencePathTest(unittest.TestCase):
    def test_windows_manifest_path_is_portable(self) -> None:
        self.assertEqual(
            Path("docs/recommendation/evidence/results/result.json"),
            repository_path(r"docs\recommendation\evidence\results\result.json"),
        )

    def test_posix_manifest_path_is_unchanged(self) -> None:
        self.assertEqual(
            Path("docs/recommendation/evidence/results/result.json"),
            repository_path("docs/recommendation/evidence/results/result.json"),
        )

    def test_git_line_ending_normalization_is_the_only_accepted_byte_drift(self) -> None:
        expected = b'{\r\n  "status": "PASS"\r\n}\r\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "result.json")
            path.write_bytes(expected.replace(b"\r\n", b"\n"))
            record = {
                "bytes": len(expected),
                "sha256": hashlib.sha256(expected).hexdigest(),
            }
            self.assertTrue(artifact_matches(path, record))
            path.write_bytes(b'{\n  "status": "FAIL"\n}\n')
            self.assertFalse(artifact_matches(path, record))


if __name__ == "__main__":
    unittest.main()
