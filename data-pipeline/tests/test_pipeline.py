from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feelm_catalog_pipeline.fake_tmdb import FakeTmdbGateway
from feelm_catalog_pipeline.pipeline import CatalogPipeline, PipelineConfig

from support import credits, movie_details, providers, translations, write_movielens_zip


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


class SequenceUuid:
    def __init__(self, values: list[str]) -> None:
        self.values = [uuid.UUID(value) for value in values]
        self.index = 0

    def __call__(self) -> uuid.UUID:
        value = self.values[self.index]
        self.index += 1
        return value


class PipelineTest(unittest.TestCase):
    def test_builds_schema_v1_quality_report_and_reuses_identity_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "ml-test.zip"
            write_movielens_zip(
                archive,
                [
                    {"movieId": 1, "title": "Direct Film (2020)", "genres": "Action"},
                    {"movieId": 2, "title": "TV Only (2000)", "genres": "Drama"},
                ],
                [
                    {"movieId": 1, "imdbId": "0000001", "tmdbId": "1"},
                    {"movieId": 2, "imdbId": "0000002", "tmdbId": ""},
                ],
            )
            gateway = FakeTmdbGateway(
                details={1: movie_details(1, "tt0000001", "Direct Film", 2020)},
                credits={1: credits()},
                translations={1: translations("Direct Film")},
                providers={1: providers()},
                finds={
                    "tt0000002": {
                        "movie_results": [],
                        "tv_results": [{"id": 2, "name": "TV Only"}],
                    }
                },
            )
            identity_output = root / "identity-map.json"
            config = PipelineConfig(
                archive=archive,
                output=root / "catalog.jsonl",
                quality_report=root / "quality.json",
                catalog_version="catalog-test-1",
                identity_map_output=identity_output,
                generated_at=NOW,
                workers=2,
            )
            movie_ids = SequenceUuid(
                [
                    "11111111-1111-4111-8111-111111111111",
                    "22222222-2222-4222-8222-222222222222",
                ]
            )
            snapshots = SequenceUuid(["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"])
            result = CatalogPipeline(
                gateway, movie_uuid_factory=movie_ids, snapshot_uuid_factory=snapshots
            ).run(config)

            lines = [json.loads(line) for line in config.output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual("artifactHeader", lines[0]["recordType"])
            self.assertEqual(1, lines[0]["schemaVersion"])
            self.assertEqual("catalog-test-1", lines[0]["catalogVersion"])
            self.assertTrue(lines[0]["sourceChecksums"]["movielensArchiveSha256"])
            self.assertEqual(2, sum(item["recordType"] == "movieIdentity" for item in lines))
            self.assertEqual(1, sum(item["recordType"] == "movieProjection" for item in lines))
            quality = json.loads(config.quality_report.read_text(encoding="utf-8"))
            self.assertTrue(quality["allPublishGatesPassed"])
            self.assertEqual(1, quality["summary"]["identityStatuses"]["TYPE_MISMATCH_TV"])
            self.assertEqual(result.artifact_sha256, quality["artifactSha256"])

            first_map = json.loads(identity_output.read_text(encoding="utf-8"))
            first_ids = {item["movieId"] for item in first_map["mappings"]}
            second_config = PipelineConfig(
                archive=archive,
                output=root / "catalog-2.jsonl",
                quality_report=root / "quality-2.json",
                catalog_version="catalog-test-2",
                identity_map_input=identity_output,
                identity_map_output=identity_output,
                generated_at=NOW,
                workers=1,
            )
            CatalogPipeline(
                gateway,
                movie_uuid_factory=lambda: uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
                snapshot_uuid_factory=lambda: uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            ).run(second_config)
            second_map = json.loads(identity_output.read_text(encoding="utf-8"))
            second_ids = {item["movieId"] for item in second_map["mappings"]}
            self.assertEqual(first_ids, second_ids)


if __name__ == "__main__":
    unittest.main()
