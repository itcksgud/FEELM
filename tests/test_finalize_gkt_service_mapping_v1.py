from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from finalize_gkt_service_mapping_v1 import (  # noqa: E402
    DB_EXPORT_SCHEMA,
    EXPECTED_QUERY,
    finalize,
    sha256_file,
)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def artifact_pin(path: Path) -> dict:
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


class GktServiceMappingTest(unittest.TestCase):
    def fixture(self, root: Path, *, csv_text: str = "id,tmdb_id\n101,2\n102,6\n103,\n104,999\n",
                content_override=None):
        staging = root / "staging"
        staging.mkdir()
        index = pd.DataFrame({
            "rowIndex": np.array([0, 1, 2], dtype=np.int64),
            "referenceServiceMovieId": np.array([1, 4, 7], dtype=np.int64),
            "tmdbId": np.array([2, 6, 9], dtype=np.int64),
            "tasteId": np.array([0, 1, 2], dtype=np.int32),
            "childId": np.array([1, 2, 3], dtype=np.int32),
            "groupId": np.array([1, 130, 259], dtype=np.int32),
            "topSupported": [True, False, False],
            "subSupported": [True, True, False],
            "referenceIdMapped": [True, True, True],
            "referenceGroupMapped": [True, False, False],
            "serviceIdMapped": [False, False, False],
            "groupMapped": [False, False, False],
        })
        index.to_parquet(staging / "movie-index.parquet", index=False)
        f64 = np.arange(3 * 131, dtype=np.float64).reshape(3, 131) / 1000
        f64[1, :19] = 0.0
        f64[2, :] = 0.0
        if content_override is not None:
            f64 = content_override
        else:
            for row in (0, 1):
                f64[row] /= np.linalg.norm(f64[row])
        np.save(root / "content-f64.npy", f64, allow_pickle=False)
        np.save(staging / "content-vectors.float32.npy", f64.astype(np.float32), allow_pickle=False)
        source_recipe = root / "source-recipe.json"
        team_recipe = root / "team-recipe.json"
        source_recipe.write_bytes(b'{\r\n  "value": 1\r\n}\r\n')
        team_recipe.write_bytes(b'{\n  "value": 1\n}\n')
        artifacts = {
            path.name: artifact_pin(path)
            for path in staging.iterdir()
            if path.is_file() and path.name != "manifest.json"
        }
        manifest = {
            "schemaVersion": "feelm-gkt-staging-bundle/1",
            "status": "STAGING_ID_MAPPING_REQUIRED",
            "readyForServing": False,
            "legacyPickleExported": False,
            "pairwiseNeighborsGenerated": False,
            "stagingVersion": "staging-v1",
            "sourceCatalogVersion": "source-v1",
            "geometryVersion": "geometry-v1",
            "groupRecipeSha256": sha256_file(source_recipe),
            "rowCounts": {"rows": 3, "dimensions": 131},
            "sources": {
                "referenceCatalog": {"sha256": "a" * 64},
                "referenceContentVectors": artifact_pin(root / "content-f64.npy"),
            },
            "artifacts": artifacts,
        }
        parity = {
            "G01": {"status": "PASS"},
            "G02": {"status": "PASS"},
            "G05": {"status": "PASS"},
            "S01": {"status": "PARTIAL_PASS"},
        }
        write_json(staging / "parity.json", parity)
        for name in (
            "geometry-arrays.npz", "preprocessor-arrays.npz", "similar-vector-export.json",
            "text-vocabulary.json", "top-names.json", "transform-contract.json",
        ):
            (staging / name).write_text(name + "\n", encoding="utf-8")
        manifest["artifacts"] = {
            path.name: artifact_pin(path)
            for path in staging.iterdir()
            if path.is_file() and path.name != "manifest.json"
        }
        write_json(staging / "manifest.json", manifest)
        db_csv = root / "movies.csv"
        db_csv.write_text(csv_text, encoding="utf-8", newline="")
        db_rows = max(0, len(csv_text.strip().splitlines()) - 1)
        sidecar = root / "movies.sidecar.json"
        write_json(sidecar, {
            "schemaVersion": DB_EXPORT_SCHEMA,
            "sourceName": "fixture-db",
            "query": EXPECTED_QUERY,
            "exportedAt": "2026-09-13T00:00:00Z",
            "rows": db_rows,
            "csv": artifact_pin(db_csv),
        })
        return staging, root / "content-f64.npy", source_recipe, team_recipe, db_csv, sidecar

    def run_fixture(self, root: Path, **kwargs):
        inputs = self.fixture(root, **kwargs)
        output = root / "output"
        result = finalize(*inputs, output, "service-catalog-v1")
        return result, output

    def test_maps_ids_preserves_float64_and_leaves_eligibility_unknown(self) -> None:
        with TemporaryDirectory() as temp:
            result, output = self.run_fixture(Path(temp))
            self.assertEqual("ID_MAPPED_STAGING", result["status"])
            self.assertFalse(result["readyForServing"])
            self.assertEqual(2, result["rowCounts"]["matchedRows"])
            self.assertEqual(1, result["rowCounts"]["currentDbTmdbMissingRows"])
            self.assertEqual(1, result["rowCounts"]["currentDbReferenceNotFoundRows"])
            self.assertEqual(1, result["rowCounts"]["referenceNotInCurrentDbRows"])
            assignments = pd.read_parquet(output / "movie-assignments.parquet")
            self.assertEqual([101, 102], assignments.serviceMovieId.tolist())
            self.assertEqual([2, 6], assignments.tmdbId.tolist())
            self.assertEqual([True, False], assignments.groupMapped.tolist())
            self.assertEqual([True, True], assignments.similaritySupported.tolist())
            self.assertTrue(assignments.candidateEligible.isna().all())
            source = np.load(Path(temp) / "content-f64.npy")
            mapped64 = np.load(output / "content-vectors.float64.npy")
            mapped32 = np.load(output / "content-vectors.float32.npy")
            np.testing.assert_array_equal(source[:2], mapped64)
            np.testing.assert_array_equal(source[:2].astype(np.float32), mapped32)
            self.assertEqual("CRLF_TO_LF_ONLY", result["recipe"]["equivalence"])

    def test_duplicate_service_or_tmdb_ids_fail_and_leave_no_output(self) -> None:
        for csv_text, message in (
            ("id,tmdb_id\n1,2\n1,6\n", "duplicate service"),
            ("id,tmdb_id\n1,2\n2,2\n", "multiple service"),
        ):
            with self.subTest(csv=csv_text), TemporaryDirectory() as temp:
                root = Path(temp)
                inputs = self.fixture(root, csv_text=csv_text)
                with self.assertRaisesRegex(ValueError, message):
                    finalize(*inputs, root / "output", "catalog-v1")
                self.assertFalse((root / "output").exists())
                self.assertFalse(any(path.name.startswith(".output.tmp-") for path in root.iterdir()))

    def test_decimal_parser_rejects_exponent_negative_and_overflow(self) -> None:
        for value in ("1e2", "-1", "0", "9223372036854775808"):
            with self.subTest(value=value), TemporaryDirectory() as temp:
                root = Path(temp)
                inputs = self.fixture(root, csv_text=f"id,tmdb_id\n{value},2\n")
                with self.assertRaises(ValueError):
                    finalize(*inputs, root / "output", "catalog-v1")

    def test_source_hash_drift_fails_before_publish(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = list(self.fixture(root))
            inputs[4].write_text(
                inputs[4].read_text(encoding="utf-8").replace("101,2", "101,9"),
                encoding="utf-8",
                newline="",
            )
            with self.assertRaisesRegex(ValueError, "SHA-256 drift"):
                finalize(*inputs, root / "output", "catalog-v1")
            self.assertFalse((root / "output").exists())

    def test_recipe_content_change_is_not_line_ending_equivalence(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = list(self.fixture(root))
            inputs[3].write_text('{\n  "value": 2\n}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "beyond CRLF/LF"):
                finalize(*inputs, root / "output", "catalog-v1")

    def test_zero_matches_is_blocked_but_mapping_audit_is_not_published(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self.fixture(root, csv_text="id,tmdb_id\n101,999\n")
            with self.assertRaisesRegex(ValueError, "no current DB movie"):
                finalize(*inputs, root / "output", "catalog-v1")
            self.assertFalse((root / "output").exists())

    def test_non_finite_or_wrong_float64_source_fails(self) -> None:
        bad_values = [
            np.full((3, 131), np.nan, dtype=np.float64),
            np.zeros((3, 130), dtype=np.float64),
        ]
        for value in bad_values:
            with self.subTest(shape=value.shape), TemporaryDirectory() as temp:
                root = Path(temp)
                inputs = self.fixture(root, content_override=value)
                with self.assertRaisesRegex(ValueError, "shape or dtype|non-finite"):
                    finalize(*inputs, root / "output", "catalog-v1")

    def test_zero_vector_cannot_claim_support(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self.fixture(root)
            staging = inputs[0]
            index = pd.read_parquet(staging / "movie-index.parquet")
            index.loc[2, "subSupported"] = True
            index.to_parquet(staging / "movie-index.parquet", index=False)
            manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
            manifest["artifacts"]["movie-index.parquet"] = artifact_pin(staging / "movie-index.parquet")
            write_json(staging / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "subSupported"):
                finalize(*inputs, root / "output", "catalog-v1")

    def test_supported_vector_must_be_unit_normalized(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            value = np.zeros((3, 131), dtype=np.float64)
            value[0, 0] = 2.0
            value[1, 20] = 3.0
            inputs = self.fixture(root, content_override=value)
            with self.assertRaisesRegex(ValueError, "unit normalized"):
                finalize(*inputs, root / "output", "catalog-v1")

    def test_missing_required_artifact_or_out_of_range_group_fails(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self.fixture(root)
            staging = inputs[0]
            manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
            del manifest["artifacts"]["geometry-arrays.npz"]
            write_json(staging / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "allowlist"):
                finalize(*inputs, root / "output", "catalog-v1")

        with TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self.fixture(root)
            staging = inputs[0]
            index = pd.read_parquet(staging / "movie-index.parquet")
            index.loc[0, ["tasteId", "childId", "groupId"]] = [8, 0, 1024]
            index.to_parquet(staging / "movie-index.parquet", index=False)
            manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
            manifest["artifacts"]["movie-index.parquet"] = artifact_pin(staging / "movie-index.parquet")
            write_json(staging / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "taste IDs"):
                finalize(*inputs, root / "output", "catalog-v1")

    def test_existing_output_is_immutable(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = self.fixture(root)
            output = root / "output"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                finalize(*inputs, output, "catalog-v1")
            self.assertEqual("keep", marker.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
