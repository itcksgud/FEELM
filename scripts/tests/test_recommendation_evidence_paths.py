from __future__ import annotations

import unittest
from pathlib import Path

from recommendation_evidence_paths import repository_path


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


if __name__ == "__main__":
    unittest.main()
