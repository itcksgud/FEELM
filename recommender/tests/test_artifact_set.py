from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from feelm_recommender.artifact_set import (
    assemble_artifact_set,
    export_fixture_artifact_set,
    load_artifact_set,
)
from feelm_recommender.errors import ArtifactValidationError
from feelm_recommender.errors import CandidateNotEnabledError


def directory_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


class ArtifactSetTest(unittest.TestCase):
    def test_fixture_export_is_byte_identical_and_loadable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first_manifest = export_fixture_artifact_set(first)
            second_manifest = export_fixture_artifact_set(second)
            self.assertEqual(directory_hashes(first), directory_hashes(second))
            loaded = load_artifact_set(first_manifest)
            self.assertEqual(loaded.set_kind, "FIXTURE")
            self.assertIn("NOT_PRODUCTION_COVERAGE", loaded.coverage)
            self.assertEqual(loaded.core.policy.ranking_alpha, 0.0)
            with self.assertRaises(CandidateNotEnabledError):
                loaded.core.estimate_stars(
                    target_item_ids=[1],
                    onboarding_item_ids=[],
                    onboarding_ratings=[],
                    k=0,
                )
            experiment_loaded = load_artifact_set(
                first_manifest, enable_candidate=True
            )
            self.assertEqual(
                experiment_loaded.core.estimate_stars(
                    target_item_ids=[1],
                    onboarding_item_ids=[],
                    onboarding_ratings=[],
                    k=0,
                ).k,
                0,
            )
            self.assertEqual(first_manifest.read_bytes(), second_manifest.read_bytes())

    def test_manifest_version_and_payload_checksum_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = export_fixture_artifact_set(directory)
            (directory / "bias.npz").write_bytes((directory / "bias.npz").read_bytes() + b"x")
            with self.assertRaises(ArtifactValidationError):
                load_artifact_set(manifest)

            export_fixture_artifact_set(directory)
            root = json.loads(manifest.read_text(encoding="utf-8"))
            root["artifact_set_version"] = "c2-serving-set-v1-wrong"
            manifest.write_text(json.dumps(root), encoding="utf-8")
            with self.assertRaises(ArtifactValidationError):
                load_artifact_set(manifest)

    def test_evidence_bounded_assembly_copies_and_validates_all_four_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            export_fixture_artifact_set(source)
            assembled = assemble_artifact_set(
                root / "assembled",
                artifacts={
                    "bias": (source / "bias.npz", source / "bias.metadata.json"),
                    "factors": (source / "factors.npz", source / "factors.metadata.json"),
                    "calibration": (
                        source / "calibration.json",
                        source / "calibration.metadata.json",
                    ),
                    "mapping": (source / "mapping.json", source / "mapping.metadata.json"),
                },
                catalog_version="c2-fixture-catalog-v1",
            )
            loaded = load_artifact_set(assembled)
            self.assertEqual(loaded.set_kind, "EVIDENCE_BOUNDED")
            self.assertIn("NOT_PRODUCTION_APPROVAL", loaded.coverage)


if __name__ == "__main__":
    unittest.main()
