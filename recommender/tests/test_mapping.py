from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from feelm_recommender import (
    ArtifactCompatibilityError,
    ArtifactKind,
    ArtifactValidationError,
    ItemIdMapping,
)
from helpers import metadata


MOVIE_A = "00000000-0000-0000-0000-000000000001"
MOVIE_B = "00000000-0000-0000-0000-000000000002"
MOVIE_C = "00000000-0000-0000-0000-000000000003"


class ItemIdMappingTest(unittest.TestCase):
    def _write(self, directory: Path, records: list[dict]) -> tuple[Path, str]:
        payload = directory / "mapping.json"
        payload.write_text(
            json.dumps({
                "schema_version": 1,
                "mapping_version": "test-mapping-v1",
                "source_id_space": "movielens-int-v1",
                "target_id_space": "feelm-movie-uuid-v1",
                "records": records,
            }),
            encoding="utf-8",
        )
        return payload, hashlib.sha256(payload.read_bytes()).hexdigest()

    def test_loader_quarantines_both_source_and_service_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload, digest = self._write(Path(temporary), [
                {"movielens_item_id": 1, "service_movie_id": MOVIE_A},
                {"movielens_item_id": 1, "service_movie_id": MOVIE_B},
                {"movielens_item_id": 2, "service_movie_id": MOVIE_C},
                {"movielens_item_id": 3, "service_movie_id": MOVIE_C},
                {"movielens_item_id": 4, "service_movie_id": MOVIE_A},
                {"movielens_item_id": 5, "service_movie_id": "00000000-0000-0000-0000-000000000005"},
            ])
            mapping = ItemIdMapping.load(
                payload, metadata(ArtifactKind.ITEM_ID_MAPPING, checksum=digest)
            )
            self.assertEqual(mapping.by_service_id, {
                "00000000-0000-0000-0000-000000000005": 5
            })
            self.assertEqual(
                {item.reason for item in mapping.quarantined},
                {"SOURCE_ID_CONFLICT", "SERVICE_ID_CONFLICT"},
            )

    def test_loader_deduplicates_identical_rows_and_quarantines_missing_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload, digest = self._write(Path(temporary), [
                {"movielens_item_id": 1, "service_movie_id": MOVIE_A},
                {"movielens_item_id": 1, "service_movie_id": MOVIE_A},
            ])
            mapping = ItemIdMapping.load(
                payload, metadata(ArtifactKind.ITEM_ID_MAPPING, checksum=digest)
            )
            resolved, quarantine = mapping.resolve_service_ids([MOVIE_A, MOVIE_B, "bad-id"])
            self.assertEqual(resolved, [(MOVIE_A, 1)])
            self.assertEqual(
                [item.reason for item in quarantine],
                ["INVALID_SERVICE_ID", "SERVICE_ID_NOT_MAPPED"],
            )

    def test_loader_enforces_checksum_and_versioned_id_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload, digest = self._write(Path(temporary), [
                {"movielens_item_id": 1, "service_movie_id": MOVIE_A}
            ])
            with self.assertRaisesRegex(ArtifactValidationError, "checksum mismatch"):
                ItemIdMapping.load(payload, metadata(ArtifactKind.ITEM_ID_MAPPING))
            incompatible = metadata(
                ArtifactKind.ITEM_ID_MAPPING,
                checksum=digest,
                compatibility={
                    "mapping_version": "other-version",
                    "source_id_space": "movielens-int-v1",
                    "target_id_space": "feelm-movie-uuid-v1",
                },
            )
            with self.assertRaisesRegex(ArtifactCompatibilityError, "mapping_version"):
                ItemIdMapping.load(payload, incompatible)

    def test_loader_rejects_zero_and_negative_movielens_item_ids(self) -> None:
        for invalid_id in (0, -1):
            with self.subTest(invalid_id=invalid_id), tempfile.TemporaryDirectory() as temporary:
                payload, digest = self._write(Path(temporary), [
                    {"movielens_item_id": invalid_id, "service_movie_id": MOVIE_A}
                ])
                with self.assertRaisesRegex(
                    ArtifactValidationError, "invalid MovieLens item ID"
                ):
                    ItemIdMapping.load(
                        payload,
                        metadata(ArtifactKind.ITEM_ID_MAPPING, checksum=digest),
                    )


if __name__ == "__main__":
    unittest.main()
