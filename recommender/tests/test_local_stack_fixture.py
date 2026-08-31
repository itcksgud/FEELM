from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from feelm_recommender.candidate_export import LocalCandidateStore
from feelm_recommender.local_stack_fixture import (
    LOCAL_CATALOG_VERSION,
    LOCAL_MOVIES,
    export_local_stack_fixture,
    local_catalog_bytes,
    validate_v100_fixture_sql,
)


class LocalStackFixtureTest(unittest.TestCase):
    def test_generator_contract_cannot_drift_from_v100_sql(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        v100 = repository / "backend/src/main/resources/db/local/V100__local_catalog_fixture.sql"
        summary = validate_v100_fixture_sql(v100.read_text(encoding="utf-8"))
        self.assertEqual(summary["catalogVersion"], LOCAL_CATALOG_VERSION)
        self.assertEqual(summary["movieCount"], 8)
        self.assertEqual(summary["uiReadyCount"], 7)

        drifted = v100.read_text(encoding="utf-8").replace(
            "'97204ea5-e6e5-4417-a13f-bc8197660705', 'MOVIE', 'IDENTITY_VERIFIED', 'CATALOG_VISIBLE'",
            "'97204ea5-e6e5-4417-a13f-bc8197660705', 'MOVIE', 'IDENTITY_VERIFIED', 'UI_READY'",
            1,
        )
        with self.assertRaisesRegex(ValueError, "LOCAL_V100_VISIBILITY_DRIFT"):
            validate_v100_fixture_sql(drifted)

    def test_fixture_matches_local_catalog_ids_and_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = export_local_stack_fixture(root)
            tracked = [
                root / "catalog/catalog.jsonl",
                root / "mapping/mapping.json",
                root / "mapping/mapping.metadata.json",
                root / "serving/artifact-set.json",
                root / "candidates/candidate-set.json",
                root / "candidates/quarantine.json",
                root / "candidates/store/active.json",
            ]
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}
            second = export_local_stack_fixture(root)
            after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}

            self.assertEqual(first, second)
            self.assertEqual(before, after)
            self.assertEqual(first["catalogVersion"], LOCAL_CATALOG_VERSION)
            self.assertEqual(first["acceptedRecords"], 7)
            self.assertEqual(first["quarantinedRecords"], 1)
            active = LocalCandidateStore(root / "candidates/store").load_active()
            expected = sorted(movie_id for movie_id, _, visibility in LOCAL_MOVIES if visibility == "UI_READY")
            self.assertEqual(active["movieIds"], expected)
            self.assertNotIn("movielens", str(active).lower())

    def test_imported_catalog_rebinds_mapping_and_candidates_to_exact_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "imported.jsonl"
            source.write_bytes(local_catalog_bytes())

            result = export_local_stack_fixture(root / "artifacts", catalog_source=source)

            self.assertEqual(result["catalogVersion"], LOCAL_CATALOG_VERSION)
            self.assertEqual(result["scope"], "LOCAL_IMPORTED_CATALOG_ONLY_NOT_PRODUCTION_COVERAGE")
            active = LocalCandidateStore(root / "artifacts/candidates/store").load_active()
            self.assertEqual(active["catalogVersion"], LOCAL_CATALOG_VERSION)


if __name__ == "__main__":
    unittest.main()
