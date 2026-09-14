from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_service_v1_b1_masked_views as masked_views  # noqa: E402


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def file_pin(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": masked_views.sha256_file(path),
    }


class FakeFeatures:
    def __init__(self, metadata: pd.DataFrame, catalog: pd.DataFrame) -> None:
        size = len(metadata)
        index = np.arange(size, dtype=np.float64)
        crowd = np.column_stack([
            0.2 + index / max(size * 10, 1),
            0.3 + index / max(size * 20, 1),
            0.4 + index / max(size * 30, 1),
        ])
        self.original = SimpleNamespace(
            crowd=crowd,
            valid=np.ones((size, 3), dtype=bool),
        )
        self.shrunk = crowd.copy()
        self.shrunk[:, 0] = 0.75 * crowd[:, 0] + 0.1
        self.prior_mean = 6.173088067675869
        self.prior_mass = 48.0
        self.prior_movies = 44_920

    def crowd(
        self,
        oi: np.ndarray,
        stars: np.ndarray,
        mask: np.ndarray,
        ei: np.ndarray,
        shrink: bool = False,
    ) -> np.ndarray:
        return masked_views.Features.crowd(self, oi, stars, mask, ei, shrink=shrink)

    def batch(
        self,
        oi: np.ndarray,
        stars: np.ndarray,
        mask: np.ndarray,
        ei: np.ndarray,
    ) -> np.ndarray:
        rows = len(ei)
        result = np.zeros((rows, 230), dtype=np.float32)
        result[:, 0] = np.asarray(ei, dtype=np.float32) / 100
        result[:, 1] = mask.sum(axis=1).astype(np.float32) / 30
        result[:, 2] = (stars * mask).sum(axis=1).astype(np.float32) / 150
        result[:, 3] = np.asarray(ei, dtype=np.float32) % 2
        result[:, 200:230] = self.crowd(oi, stars, mask, ei, shrink=False)
        return result


@dataclass
class BuildFixture:
    args: argparse.Namespace
    expectation: masked_views.ContractExpectation
    movie_ids: np.ndarray
    target_ids: np.ndarray
    histories: list[list[int]]
    masked_only_history_row: int
    masked_target_and_history_row: int


def dependency_hashes() -> dict[str, str]:
    return {
        name: masked_views.sha256_file(ROOT / "scripts" / name)
        for name in masked_views.DEPENDENCY_NAMES
    }


def selected_movie_ids() -> tuple[list[int], list[int]]:
    masked: list[int] = []
    unmasked: list[int] = []
    value = 1
    while len(masked) < 3 or len(unmasked) < 3:
        (masked if masked_views.movie_is_masked(value) else unmasked).append(value)
        value += 1
    return masked[:3], unmasked[:3]


def history_arrays(
    histories: list[list[int]],
    history_stars: list[list[float]],
    movie_ids: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = len(histories)
    oi = np.zeros((rows, 30), dtype=np.int64)
    stars = np.zeros((rows, 30), dtype=np.float64)
    present = np.zeros((rows, 30), dtype=bool)
    for row, (ids, values) in enumerate(zip(histories, history_stars, strict=True)):
        positions = np.searchsorted(movie_ids, np.asarray(ids, dtype=np.int64))
        oi[row, :len(ids)] = positions
        stars[row, :len(ids)] = values
        present[row, :len(ids)] = True
    return oi, stars, present


def build_fixture(root: Path, output_name: str = "masked-output") -> BuildFixture:
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    masked_ids, unmasked_ids = selected_movie_ids()
    movie_ids = np.asarray(sorted(masked_ids + unmasked_ids), dtype=np.int64)
    m1, m2, m3 = masked_ids
    u1, u2, u3 = unmasked_ids
    target_ids = np.asarray([u1, m1, u2, m2, u3, m3], dtype=np.int32)
    histories = [[], [u1], [m1, u1], [], [m2], [u3, m2]]
    history_stars = [[], [1.0], [2.0, 1.0], [], [4.0], [4.5, 4.0]]
    history_timestamps = [[], [100], [101, 100], [], [100], [101, 100]]
    target_timestamps = np.asarray([100, 101, 102, 100, 101, 102], dtype=np.int64)
    uids = np.asarray([1, 1, 1, 2, 2, 2], dtype=np.int32)
    labels = np.asarray([1.0, 2.0, 3.0, 4.0, 4.5, 5.0], dtype=np.float64)

    metadata = pd.DataFrame({"movie_id": movie_ids})
    catalog = pd.DataFrame({
        "movie_id": movie_ids,
        "blocked": False,
        "released_at_origin": True,
        "train_count": 1,
        "reference_count": 1,
        "support": "SUPPORTED",
    })
    metadata_path = inputs / "metadata.parquet"
    catalog_path = inputs / "catalog.parquet"
    metadata.to_parquet(metadata_path, index=False, engine="pyarrow")
    catalog.to_parquet(catalog_path, index=False, engine="pyarrow")

    natural = FakeFeatures(metadata, catalog)
    oi, hstars, present = history_arrays(histories, history_stars, movie_ids)
    ei = np.searchsorted(movie_ids, target_ids)
    features = natural.batch(oi, hstars, present, ei)
    shrunk = natural.crowd(oi, hstars, present, ei, shrink=True)
    rating_indices = np.asarray(masked_views.RATING_COLUMNS, dtype=np.int64)
    features[:, rating_indices] = shrunk[:, rating_indices - 200]
    rh_values: dict[str, Any] = {
        "row_id": np.arange(6, dtype=np.int64),
        "uid": uids,
        "label": labels,
    }
    rh_values.update({name: features[:, i] for i, name in enumerate(masked_views.FEATURE_COLUMNS)})
    rh_path = inputs / "rh-train.parquet"
    pq.write_table(
        pa.Table.from_pydict(rh_values, schema=masked_views.feature_schema()),
        rh_path,
        row_group_size=4,
    )

    lineage_values = {
        "row_id": np.arange(6, dtype=np.int64),
        "uid": uids,
        "target_ml_movie_id": target_ids,
        "canonical_target_key": [f"ml:{int(value)}" for value in target_ids],
        "target_timestamp": target_timestamps,
        "label": labels,
        "original_cap": np.asarray([2] * 6, dtype=np.int8),
        "history_length": np.asarray([len(value) for value in histories], dtype=np.int8),
        "history_ml_ids": histories,
        "history_stars": history_stars,
        "history_timestamps": history_timestamps,
    }
    lineage_path = inputs / "row-lineage.parquet"
    pq.write_table(
        pa.Table.from_pydict(lineage_values, schema=masked_views.lineage_schema()),
        lineage_path,
        row_group_size=4,
    )
    lineage_manifest_path = inputs / "lineage-manifest.json"
    write_json(lineage_manifest_path, {
        "status": "LINEAGE_READY_B1_FEATURES_NOT_BUILT",
        "readyForTraining": False,
        "serviceMovieIdMappingUsed": False,
        "evaluationTargetsRead": False,
        "rowCounts": {"rows": 6, "users": 2, "historyPairs": 6},
        "artifacts": {"row-lineage.parquet": file_pin(lineage_path)},
    })

    artifact_path = inputs / "service-v1.json"
    rh_pin = file_pin(rh_path)
    rh_pin["source_path"] = "outputs/recommendation-evidence/foundation340/RH/train.parquet"
    write_json(artifact_path, {"artifacts": [rh_pin]})
    recipe_path = inputs / "training-recipe.v1.json"
    write_json(recipe_path, {
        "sourceTargetRows": 6,
        "viewCount": 4,
        "expandedRows": 24,
        "featureGenerationChunkRows": 4096,
        "trainMaskHashPrefix": masked_views.MASK_PREFIX,
        "sourceRowIdColumn": "row_id",
        "viewIdColumn": "view_id",
        "weightColumnGBT": "view_weight",
        "absentChannelMaskViews": "RETAIN_IDENTICAL_ROWS",
        "variants": [{"id": "B1", "dimensions": 230, "newGBTfit": True}],
    })
    target_mask = np.asarray([masked_views.movie_is_masked(v) for v in target_ids], dtype=bool)
    history_masks = [[masked_views.movie_is_masked(v) for v in row] for row in histories]
    history_masked = sum(sum(row) for row in history_masks)
    rows_history = sum(any(row) for row in history_masks)
    rows_union = sum(bool(t) or any(row) for t, row in zip(target_mask, history_masks, strict=True))
    census = masked_views.MaskCensus(
        catalog_movies=len(movie_ids),
        masked_movies=int(masked_views.catalog_mask(movie_ids).sum()),
        target_rows=6,
        masked_target_rows=int(target_mask.sum()),
        history_pairs=sum(len(row) for row in histories),
        masked_history_pairs=history_masked,
        rows_with_masked_history=rows_history,
        rows_with_masked_target_or_history=rows_union,
        users=2,
    )
    args = argparse.Namespace(
        training_recipe=recipe_path,
        artifact_manifest=artifact_path,
        metadata=metadata_path,
        catalog=catalog_path,
        rh_train=rh_path,
        lineage_manifest=lineage_manifest_path,
        lineage=lineage_path,
        output_dir=root / output_name,
    )
    expectation = masked_views.ContractExpectation(
        training_recipe_sha256=masked_views.sha256_file(recipe_path),
        artifact_manifest_sha256=masked_views.sha256_file(artifact_path),
        design_sha256=masked_views.sha256_file(
            ROOT / "docs/recommendation/plans/service-v1-b1-masked-views.md"
        ),
        metadata_sha256=masked_views.sha256_file(metadata_path),
        catalog_sha256=masked_views.sha256_file(catalog_path),
        rh_train_sha256=masked_views.sha256_file(rh_path),
        lineage_manifest_sha256=masked_views.sha256_file(lineage_manifest_path),
        lineage_sha256=masked_views.sha256_file(lineage_path),
        dependency_sha256=dependency_hashes(),
        census=census,
        prior_mean=natural.prior_mean,
        prior_mass=natural.prior_mass,
        prior_movies=natural.prior_movies,
    )
    return BuildFixture(
        args=args,
        expectation=expectation,
        movie_ids=movie_ids,
        target_ids=target_ids,
        histories=histories,
        masked_only_history_row=4,
        masked_target_and_history_row=5,
    )


def refresh_rh_contract(fixture: BuildFixture) -> None:
    rh_pin = file_pin(fixture.args.rh_train)
    rh_pin["source_path"] = "outputs/recommendation-evidence/foundation340/RH/train.parquet"
    write_json(fixture.args.artifact_manifest, {"artifacts": [rh_pin]})
    fixture.expectation = replace(
        fixture.expectation,
        rh_train_sha256=masked_views.sha256_file(fixture.args.rh_train),
        artifact_manifest_sha256=masked_views.sha256_file(fixture.args.artifact_manifest),
    )


def refresh_lineage_contract(fixture: BuildFixture) -> None:
    manifest = json.loads(fixture.args.lineage_manifest.read_text(encoding="utf-8"))
    manifest["artifacts"]["row-lineage.parquet"] = file_pin(fixture.args.lineage)
    write_json(fixture.args.lineage_manifest, manifest)
    fixture.expectation = replace(
        fixture.expectation,
        lineage_sha256=masked_views.sha256_file(fixture.args.lineage),
        lineage_manifest_sha256=masked_views.sha256_file(fixture.args.lineage_manifest),
    )


def temporary_outputs(root: Path, output_name: str) -> list[Path]:
    return list(root.glob(f".{output_name}.tmp-*"))


class MaskAndFeatureUnitTests(unittest.TestCase):
    def test_mask_integer_boundary_is_strict_and_exact(self) -> None:
        boundary = (1 << 256) // 5
        self.assertTrue(masked_views.digest_is_masked(boundary.to_bytes(32, "big")))
        self.assertFalse(masked_views.digest_is_masked((boundary + 1).to_bytes(32, "big")))
        self.assertTrue(masked_views.digest_is_masked(bytes(32)))
        self.assertFalse(masked_views.digest_is_masked(bytes([255]) * 32))

    def test_masked_state_zeros_value_and_presence_but_preserves_prior(self) -> None:
        metadata = pd.DataFrame({"movie_id": [1, 2, 3]})
        catalog = metadata.copy()
        natural = FakeFeatures(metadata, catalog)
        original_crowd = natural.original.crowd.copy()
        original_valid = natural.original.valid.copy()
        masked = masked_views.make_masked_features(natural, np.array([True, False, True]))
        self.assertEqual(masked.prior_mean, natural.prior_mean)
        self.assertEqual(masked.prior_mass, natural.prior_mass)
        self.assertEqual(masked.prior_movies, natural.prior_movies)
        np.testing.assert_array_equal(masked.original.crowd[[0, 2]], 0)
        np.testing.assert_array_equal(masked.original.valid[[0, 2]], False)
        np.testing.assert_array_equal(masked.original.crowd[1], original_crowd[1])
        np.testing.assert_array_equal(natural.original.crowd, original_crowd)
        np.testing.assert_array_equal(natural.original.valid, original_valid)

    def test_process_chunk_rejects_more_than_4096_rows_before_calculation(self) -> None:
        rh = pd.DataFrame(index=np.arange(masked_views.MAX_CHUNK_ROWS + 1))
        with self.assertRaisesRegex(ValueError, "exceeds 4096"):
            masked_views.process_chunk(rh, pa.table({}), SimpleNamespace())


class FullBuildTests(unittest.TestCase):
    def test_fixture_requires_explicit_contract_and_full_build_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            fixture = build_fixture(Path(folder))
            with self.assertRaisesRegex(ValueError, "reviewed canonical input"):
                masked_views.build(fixture.args, feature_factory=FakeFeatures)
            manifest = masked_views.build(
                fixture.args,
                expectation=fixture.expectation,
                feature_factory=FakeFeatures,
            )
            self.assertEqual(manifest["status"], "B1_MASKED_FEATURES_BUILT_PENDING_INDEPENDENT_AUDIT")
            self.assertFalse(manifest["readyForTraining"])
            self.assertFalse(manifest["modelFitPerformed"])
            self.assertEqual(manifest["census"]["targetRows"], 6)
            self.assertEqual(manifest["census"]["logicalViewRows"], 24)
            self.assertLessEqual(
                manifest["parity"]["naturalMaximumAbsoluteDifference"],
                masked_views.NATURAL_PARITY_TOLERANCE,
            )
            output_path = fixture.args.output_dir / "tmdb-masked-rh230.parquet"
            output = pd.read_parquet(output_path)
            natural = pd.read_parquet(fixture.args.rh_train)
            output_file = pq.ParquetFile(output_path)
            try:
                self.assertEqual(output_file.schema_arrow, masked_views.feature_schema())
            finally:
                output_file.close()
            np.testing.assert_array_equal(
                output[[f"x{i:03d}" for i in range(200)]].to_numpy(),
                natural[[f"x{i:03d}" for i in range(200)]].to_numpy(),
            )
            target_row = fixture.masked_target_and_history_row
            np.testing.assert_array_equal(
                output.loc[target_row, [f"x{i:03d}" for i in range(200, 206)]].to_numpy(float),
                np.zeros(6),
            )
            history_row = fixture.masked_only_history_row
            np.testing.assert_array_equal(
                output.loc[history_row, ["x213", "x221", "x229"]].to_numpy(float),
                np.zeros(3),
            )
            views = json.loads(
                (fixture.args.output_dir / "views-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual([row["viewId"] for row in views["views"]], [0, 1, 2, 3])
            self.assertEqual([row["viewWeight"] for row in views["views"]], [0.25] * 4)
            self.assertEqual(
                [row["physicalAxis"] for row in views["views"]],
                ["RH230_NATURAL", "RH230_TMDB_MASKED", "RH230_NATURAL", "RH230_TMDB_MASKED"],
            )
            self.assertEqual(
                set(path.name for path in fixture.args.output_dir.iterdir()),
                {"tmdb-masked-rh230.parquet", "views-manifest.json", "manifest.json"},
            )
            for name, expected in manifest["artifacts"].items():
                self.assertEqual(file_pin(fixture.args.output_dir / name)["sha256"], expected["sha256"])

    def test_natural_full_axis_parity_failure_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = build_fixture(root)
            table = pq.read_table(fixture.args.rh_train)
            values = table["x010"].to_numpy(zero_copy_only=False).copy()
            values[0] += 0.01
            table = table.set_column(table.schema.get_field_index("x010"), "x010", pa.array(values, pa.float32()))
            pq.write_table(table, fixture.args.rh_train, row_group_size=4)
            refresh_rh_contract(fixture)
            with self.assertRaisesRegex(ValueError, "natural RH230 parity"):
                masked_views.build(
                    fixture.args,
                    expectation=fixture.expectation,
                    feature_factory=FakeFeatures,
                )
            self.assertFalse(fixture.args.output_dir.exists())
            self.assertEqual(temporary_outputs(root, fixture.args.output_dir.name), [])

    def test_mask_census_failure_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = build_fixture(root)
            wrong_census = replace(
                fixture.expectation.census,
                masked_history_pairs=fixture.expectation.census.masked_history_pairs + 1,
            )
            wrong = replace(fixture.expectation, census=wrong_census)
            with self.assertRaisesRegex(ValueError, "mask/resource census drift"):
                masked_views.build(
                    fixture.args,
                    expectation=wrong,
                    feature_factory=FakeFeatures,
                )
            self.assertFalse(fixture.args.output_dir.exists())
            self.assertEqual(temporary_outputs(root, fixture.args.output_dir.name), [])

    def test_row_id_uid_and_label_mismatches_fail_closed(self) -> None:
        cases = (("row_id", 0, 99, "row_id"), ("uid", 0, 9, "uid"),
                 ("label", 0, 2.5, "label"))
        for index, (column, row, value, message) in enumerate(cases):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                fixture = build_fixture(root, output_name=f"output-{index}")
                table = pq.read_table(fixture.args.lineage)
                values = table[column].to_numpy(zero_copy_only=False).copy()
                values[row] = value
                table = table.set_column(
                    table.schema.get_field_index(column),
                    column,
                    pa.array(values, type=table.schema.field(column).type),
                )
                pq.write_table(table, fixture.args.lineage, row_group_size=4)
                refresh_lineage_contract(fixture)
                with self.assertRaisesRegex(ValueError, message):
                    masked_views.build(
                        fixture.args,
                        expectation=fixture.expectation,
                        feature_factory=FakeFeatures,
                    )
                self.assertFalse(fixture.args.output_dir.exists())
                self.assertEqual(temporary_outputs(root, fixture.args.output_dir.name), [])

    def test_input_mutation_after_write_is_rejected_and_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = build_fixture(root)
            original_writer = masked_views.pq.ParquetWriter

            class MutatingWriter:
                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    self.inner = original_writer(*args, **kwargs)
                    self.mutated = False

                def write_table(self, table: pa.Table) -> None:
                    self.inner.write_table(table)
                    if not self.mutated:
                        with fixture.args.metadata.open("ab") as stream:
                            stream.write(b"mutation")
                        self.mutated = True

                def close(self) -> None:
                    self.inner.close()

            with mock.patch.object(masked_views.pq, "ParquetWriter", MutatingWriter):
                with self.assertRaisesRegex(ValueError, "input metadata byte count drift"):
                    masked_views.build(
                        fixture.args,
                        expectation=fixture.expectation,
                        feature_factory=FakeFeatures,
                    )
            self.assertFalse(fixture.args.output_dir.exists())
            self.assertEqual(temporary_outputs(root, fixture.args.output_dir.name), [])

    def test_write_close_and_publish_failures_cleanup(self) -> None:
        for failure in ("write", "close", "publish"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                fixture = build_fixture(root, output_name=f"output-{failure}")
                original_writer = masked_views.pq.ParquetWriter

                class WriterProxy:
                    def __init__(self, *args: Any, **kwargs: Any) -> None:
                        self.inner = original_writer(*args, **kwargs)

                    def write_table(self, table: pa.Table) -> None:
                        if failure == "write":
                            raise OSError("synthetic write failure")
                        self.inner.write_table(table)

                    def close(self) -> None:
                        self.inner.close()
                        if failure == "close":
                            raise OSError("synthetic close failure")

                writer_patch = mock.patch.object(masked_views.pq, "ParquetWriter", WriterProxy)
                publish_patch = (
                    mock.patch.object(masked_views, "publish", side_effect=OSError("synthetic publish failure"))
                    if failure == "publish" else mock.patch.object(masked_views, "publish", wraps=masked_views.publish)
                )
                with writer_patch, publish_patch:
                    with self.assertRaisesRegex(OSError, f"synthetic {failure} failure"):
                        masked_views.build(
                            fixture.args,
                            expectation=fixture.expectation,
                            feature_factory=FakeFeatures,
                        )
                self.assertFalse(fixture.args.output_dir.exists())
                self.assertEqual(temporary_outputs(root, fixture.args.output_dir.name), [])

    def test_existing_output_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = build_fixture(root)
            fixture.args.output_dir.mkdir()
            marker = fixture.args.output_dir / "marker.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                masked_views.build(
                    fixture.args,
                    expectation=fixture.expectation,
                    feature_factory=FakeFeatures,
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(temporary_outputs(root, fixture.args.output_dir.name), [])

    def test_success_and_dry_run_release_windows_file_handles(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = build_fixture(root)
            dry = masked_views.dry_run_first_row_group(
                fixture.args,
                expectation=fixture.expectation,
                feature_factory=FakeFeatures,
            )
            self.assertEqual(dry["status"], "DRY_RUN_ONLY_NOT_PUBLISHED")
            self.assertFalse(fixture.args.output_dir.exists())
            masked_views.build(
                fixture.args,
                expectation=fixture.expectation,
                feature_factory=FakeFeatures,
            )
            for path in (fixture.args.rh_train, fixture.args.lineage):
                moved = path.with_suffix(path.suffix + ".moved")
                os.replace(path, moved)
                os.replace(moved, path)
            moved_output = fixture.args.output_dir.with_name("moved-output")
            os.replace(fixture.args.output_dir, moved_output)
            os.replace(moved_output, fixture.args.output_dir)


class StaticContractTests(unittest.TestCase):
    def test_no_python_assert_and_no_cli_bypass(self) -> None:
        paths = [
            ROOT / "scripts/build_service_v1_b1_masked_views.py",
            Path(__file__).resolve(),
        ]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        source = paths[0].read_text(encoding="utf-8")
        self.assertNotIn("--allow-test", source)
        self.assertNotIn("--skip-contract", source)
        parser = masked_views.parser()
        destinations = {action.dest for action in parser._actions}
        self.assertEqual(destinations, {
            "help", "training_recipe", "artifact_manifest", "metadata", "catalog",
            "rh_train", "lineage_manifest", "lineage", "output_dir",
        })

    def test_canonical_dependency_set_is_exact(self) -> None:
        self.assertEqual(set(masked_views.CANONICAL_CONTRACT.dependency_sha256),
                         set(masked_views.DEPENDENCY_NAMES))
        self.assertEqual(len(masked_views.DEPENDENCY_NAMES), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
