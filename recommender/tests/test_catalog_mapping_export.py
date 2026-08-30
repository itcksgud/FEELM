from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from feelm_recommender import (
    ArtifactMetadata,
    ArtifactValidationError,
    ItemIdMapping,
    export_catalog_mapping,
)


MOVIE_A = "00000000-0000-0000-0000-000000000001"
MOVIE_B = "00000000-0000-0000-0000-000000000002"
MOVIE_C = "00000000-0000-0000-0000-000000000003"
ARCHIVE_SHA256 = "a" * 64


def header(catalog_version: str = "catalog-test-v1") -> dict:
    return {
        "recordType": "artifactHeader",
        "schemaVersion": 1,
        "catalogVersion": catalog_version,
        "generatedAt": "2099-01-01T00:00:00Z",
        "sourceChecksums": {"movielensArchiveSha256": ARCHIVE_SHA256},
        "sources": [],
    }


def identity(
    movie_id: str,
    external_id: str,
    verification_status: str = "VERIFIED",
    identity_status: str = "IDENTITY_VERIFIED",
) -> dict:
    return {
        "recordType": "movieIdentity",
        "payload": {
            "movieId": movie_id,
            "identityStatus": identity_status,
            "externalIds": [
                {
                    "source": "MOVIELENS",
                    "externalId": external_id,
                    "verificationStatus": verification_status,
                }
            ],
        },
    }


def write_jsonl(path: Path, records: list[dict | str]) -> None:
    lines = [
        record if isinstance(record, str) else json.dumps(record, ensure_ascii=False)
        for record in records
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class CatalogMappingExportTest(unittest.TestCase):
    def _paths(self, directory: Path, prefix: str = "result") -> tuple[Path, Path, Path]:
        return (
            directory / f"{prefix}.mapping.json",
            directory / f"{prefix}.metadata.json",
            directory / f"{prefix}.quarantine.json",
        )

    def test_exports_verified_and_recovered_ids_with_loadable_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            catalog = directory / "catalog.jsonl"
            write_jsonl(
                catalog,
                [
                    header(),
                    identity(MOVIE_B, "2", "RECOVERED"),
                    identity(MOVIE_A, "1", "VERIFIED"),
                    identity(MOVIE_C, "3", "UNVERIFIED"),
                    identity(MOVIE_C, "0", "VERIFIED"),
                ],
            )
            mapping_path, metadata_path, quarantine_path = self._paths(directory)
            result = export_catalog_mapping(
                catalog_path=catalog,
                mapping_path=mapping_path,
                metadata_path=metadata_path,
                quarantine_path=quarantine_path,
                compatibility_id="test-family-v1",
            )

            self.assertEqual(result.accepted_records, 2)
            metadata = ArtifactMetadata.load(metadata_path)
            mapping = ItemIdMapping.load(mapping_path, metadata)
            self.assertEqual(mapping.by_movielens_id, {1: MOVIE_A, 2: MOVIE_B})
            self.assertEqual(metadata.payload_sha256, hashlib.sha256(mapping_path.read_bytes()).hexdigest())
            report = json.loads(quarantine_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["reason_counts"],
                {
                    "INVALID_MOVIELENS_EXTERNAL_ID": 1,
                    "MOVIELENS_EXTERNAL_ID_UNVERIFIED": 1,
                },
            )
            self.assertEqual(
                report["coverage_scope"], "INPUT_CATALOG_ONLY_NOT_PRODUCTION_COVERAGE"
            )

    def test_duplicate_and_bidirectional_conflicts_are_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            catalog = directory / "catalog.jsonl"
            write_jsonl(
                catalog,
                [
                    header(),
                    identity(MOVIE_A, "1"),
                    identity(MOVIE_A, "1"),
                    identity(MOVIE_B, "1"),
                    identity(MOVIE_C, "2"),
                    identity(MOVIE_C, "3"),
                    identity("00000000-0000-0000-0000-000000000004", "4"),
                ],
            )
            mapping_path, metadata_path, quarantine_path = self._paths(directory)
            export_catalog_mapping(
                catalog_path=catalog,
                mapping_path=mapping_path,
                metadata_path=metadata_path,
                quarantine_path=quarantine_path,
                compatibility_id="test-family-v1",
            )
            mapping = ItemIdMapping.load(mapping_path, ArtifactMetadata.load(metadata_path))
            self.assertEqual(
                mapping.by_movielens_id,
                {4: "00000000-0000-0000-0000-000000000004"},
            )
            reasons = json.loads(quarantine_path.read_text(encoding="utf-8"))[
                "reason_counts"
            ]
            self.assertEqual(reasons["DUPLICATE_MAPPING"], 1)
            self.assertEqual(reasons["SOURCE_ID_CONFLICT"], 2)
            self.assertEqual(reasons["SERVICE_ID_CONFLICT"], 2)

    def test_same_catalog_and_compatibility_id_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            catalog = directory / "catalog.jsonl"
            write_jsonl(catalog, [header(), identity(MOVIE_A, "1")])
            first = self._paths(directory, "first")
            second = self._paths(directory, "second")
            for paths in (first, second):
                export_catalog_mapping(
                    catalog_path=catalog,
                    mapping_path=paths[0],
                    metadata_path=paths[1],
                    quarantine_path=paths[2],
                    compatibility_id="test-family-v1",
                )
            for first_path, second_path in zip(first, second, strict=True):
                self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            metadata_text = first[1].read_text(encoding="utf-8")
            self.assertNotIn("generatedAt", metadata_text)
            self.assertNotIn("2099-01-01", metadata_text)

    def test_invalid_header_and_catalog_without_accepted_rows_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            catalog = directory / "catalog.jsonl"
            paths = self._paths(directory)
            write_jsonl(catalog, [{**header(), "schemaVersion": 2}, identity(MOVIE_A, "1")])
            with self.assertRaisesRegex(ArtifactValidationError, "schemaVersion"):
                export_catalog_mapping(
                    catalog_path=catalog,
                    mapping_path=paths[0],
                    metadata_path=paths[1],
                    quarantine_path=paths[2],
                    compatibility_id="test-family-v1",
                )
            write_jsonl(catalog, [header(), identity(MOVIE_A, "0")])
            with self.assertRaisesRegex(ArtifactValidationError, "no safe"):
                export_catalog_mapping(
                    catalog_path=catalog,
                    mapping_path=paths[0],
                    metadata_path=paths[1],
                    quarantine_path=paths[2],
                    compatibility_id="test-family-v1",
                )


if __name__ == "__main__":
    unittest.main()
