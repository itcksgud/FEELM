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
from typing import Any
from unittest import mock

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_service_v1_row_lineage as lineage  # noqa: E402
from foundation340_features import Histories  # noqa: E402


def histories_fixture(cap: int = 30) -> Histories:
    ratings = pd.DataFrame({
        "uid": pd.Series([1, 1, 1, 1], dtype="int32"),
        "movie_id": pd.Series([10, 11, 12, 13], dtype="int32"),
        "rating": [1.0, 2.0, 3.0, 4.0],
        "timestamp": pd.Series([100, 100, 101, 102], dtype="int64"),
    })
    episodes = pd.DataFrame({
        "uid": [1], "origin": [0], "cap": [cap], "h": [0],
        "pre_count": [0], "targets": [4],
    })
    return Histories(ratings, episodes, np.array([10, 11, 12, 13], dtype=np.int64))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_pin(path: Path) -> dict[str, Any]:
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": lineage.sha256_file(path)}


@dataclass
class BuildFixture:
    args: argparse.Namespace
    expectation: lineage.ContractExpectation


def refresh_fixture_contract(fixture: BuildFixture) -> None:
    args = fixture.args
    prepared = lineage.load_json(args.prepared_seal)
    prepared["files"] = {
        "ratings.parquet": file_pin(args.ratings),
        "episodes.parquet": file_pin(args.episodes),
        "catalog.parquet": file_pin(args.catalog),
    }
    write_json(args.prepared_seal, prepared)

    artifact = lineage.load_json(args.artifact_manifest)
    train_pin = file_pin(args.rh_train)
    train_pin["source_path"] = "outputs/recommendation-evidence/foundation340/RH/train.parquet"
    artifact["artifacts"] = [train_pin]
    write_json(args.artifact_manifest, artifact)

    recipe = lineage.load_json(args.training_recipe)
    for source in recipe["lineageSources"]:
        if source["path"] == "outputs/recommendation-evidence/text339/prepared-seal.json":
            source["sha256"] = lineage.sha256_file(args.prepared_seal)
    write_json(args.training_recipe, recipe)

    fixture.expectation = replace(
        fixture.expectation,
        training_recipe_sha256=lineage.sha256_file(args.training_recipe),
        artifact_manifest_sha256=lineage.sha256_file(args.artifact_manifest),
        prepared_seal_sha256=lineage.sha256_file(args.prepared_seal),
        rh_train_sha256=lineage.sha256_file(args.rh_train),
    )


def build_fixture(root: Path, *, output_name: str = "lineage-output") -> BuildFixture:
    input_dir = root / "inputs"
    input_dir.mkdir(parents=True)
    ratings = pd.DataFrame({
        "uid": pd.Series([1, 1, 1, 1], dtype="int32"),
        "movie_id": pd.Series([10, 11, 12, 13], dtype="int32"),
        "rating": pd.Series([1.0, 2.0, 3.0, 4.0], dtype="float64"),
        "timestamp": pd.Series([100, 100, 101, 102], dtype="int64"),
    })
    episodes = pd.DataFrame({
        "uid": pd.Series([1], dtype="int32"),
        "origin": pd.Series([0], dtype="int64"),
        "cap": pd.Series([2], dtype="int8"),
        "h": pd.Series([0], dtype="int64"),
        "pre_count": pd.Series([0], dtype="int64"),
        "targets": pd.Series([4], dtype="int64"),
    })
    catalog = pd.DataFrame({"movie_id": pd.Series([10, 11, 12, 13], dtype="int32")})
    rh_train = pd.DataFrame({
        "row_id": pd.Series([0, 1, 2, 3], dtype="int64"),
        "uid": pd.Series([1, 1, 1, 1], dtype="int32"),
        "label": pd.Series([1.0, 2.0, 3.0, 4.0], dtype="float64"),
    })
    ratings_path = input_dir / "ratings.parquet"
    episodes_path = input_dir / "episodes.parquet"
    catalog_path = input_dir / "catalog.parquet"
    rh_train_path = input_dir / "train.parquet"
    ratings.to_parquet(ratings_path, index=False, engine="pyarrow")
    episodes.to_parquet(episodes_path, index=False, engine="pyarrow")
    catalog.to_parquet(catalog_path, index=False, engine="pyarrow")
    rh_train.to_parquet(rh_train_path, index=False, engine="pyarrow", row_group_size=2)

    prepared_path = input_dir / "prepared-seal.json"
    write_json(prepared_path, {
        "target_stars_decoded": 0,
        "files": {
            "ratings.parquet": file_pin(ratings_path),
            "episodes.parquet": file_pin(episodes_path),
            "catalog.parquet": file_pin(catalog_path),
        },
    })
    artifact_path = input_dir / "service-v1.json"
    train_pin = file_pin(rh_train_path)
    train_pin["source_path"] = "outputs/recommendation-evidence/foundation340/RH/train.parquet"
    write_json(artifact_path, {"artifacts": [train_pin]})

    foundation_path = ROOT / "scripts" / "foundation340_features.py"
    recipe_path = input_dir / "training-recipe.v1.json"
    write_json(recipe_path, {
        "rowLineageRequired": True,
        "lineageOutput": "row-lineage.parquet",
        "sourceTargetRows": 4,
        "featureGenerationChunkRows": 4096,
        "canonicalMaskMovieId": "ml:<MovieLens movie_id decimal integer>",
        "dropRowsWithoutServiceMapping": False,
        "lineageSources": [
            {
                "path": "scripts/foundation340_features.py",
                "sha256": lineage.sha256_file(foundation_path),
            },
            {
                "path": "outputs/recommendation-evidence/text339/prepared-seal.json",
                "sha256": lineage.sha256_file(prepared_path),
            },
        ],
    })
    args = argparse.Namespace(
        ratings=ratings_path,
        episodes=episodes_path,
        catalog=catalog_path,
        rh_train=rh_train_path,
        prepared_seal=prepared_path,
        training_recipe=recipe_path,
        artifact_manifest=artifact_path,
        output_dir=root / output_name,
    )
    expectation = lineage.ContractExpectation(
        training_recipe_sha256=lineage.sha256_file(recipe_path),
        artifact_manifest_sha256=lineage.sha256_file(artifact_path),
        prepared_seal_sha256=lineage.sha256_file(prepared_path),
        rh_train_sha256=lineage.sha256_file(rh_train_path),
        foundation_features_sha256=lineage.sha256_file(foundation_path),
        source_target_rows=4,
        training_users=1,
        history_pairs=4,
        zero_history_rows=2,
        cap_counts={2: 4},
    )
    return BuildFixture(args=args, expectation=expectation)


def temporary_outputs(root: Path, output_name: str) -> list[Path]:
    return list(root.glob(f".{output_name}.tmp-*"))


class RowLineageUnitTests(unittest.TestCase):
    def test_equal_timestamp_is_excluded_and_cap_is_applied(self) -> None:
        histories = histories_fixture(cap=2)
        detail = lineage.strict_history_rows(histories, np.array([0, 1, 2, 3]))
        self.assertEqual(detail["history_length"].tolist(), [0, 0, 2, 2])
        source = detail["source_rows"]
        mask = detail["mask"]
        values = [histories.movie[row].tolist() for row in source]
        selected = [[movie for movie, keep in zip(row, keepers, strict=True) if keep]
                    for row, keepers in zip(values, mask, strict=True)]
        self.assertEqual(selected, [[], [], [10, 11], [12, 10]])

    def test_build_batch_uses_exact_schema_and_variable_lists(self) -> None:
        table = lineage.build_batch(histories_fixture(cap=2), np.array([2, 3], dtype=np.int64))
        self.assertEqual(table.schema, lineage.lineage_schema())
        self.assertEqual(table["row_id"].to_pylist(), [2, 3])
        self.assertEqual(table["canonical_target_key"].to_pylist(), ["ml:12", "ml:13"])
        self.assertEqual(table["history_ml_ids"].to_pylist(), [[10, 11], [12, 10]])
        self.assertEqual(table["history_stars"].to_pylist(), [[1.0, 2.0], [3.0, 1.0]])
        self.assertEqual(table["history_timestamps"].to_pylist(), [[100, 100], [101, 100]])

    def test_repeated_target_movie_is_rejected(self) -> None:
        ratings = pd.DataFrame({
            "uid": pd.Series([1, 1], dtype="int32"),
            "movie_id": pd.Series([10, 10], dtype="int32"),
            "rating": [2.0, 3.0],
            "timestamp": pd.Series([100, 101], dtype="int64"),
        })
        episodes = pd.DataFrame({
            "uid": [1], "origin": [0], "cap": [30], "h": [0],
            "pre_count": [0], "targets": [2],
        })
        histories = Histories(ratings, episodes, np.array([10], dtype=np.int64))
        with self.assertRaisesRegex(ValueError, "target movie leaked"):
            lineage.strict_history_rows(histories, np.array([1]))

    def test_list_array_requires_matching_shapes(self) -> None:
        with self.assertRaisesRegex(ValueError, "shape mismatch"):
            lineage.list_array(
                np.ones((2, 2)),
                np.ones((2, 1), dtype=bool),
                lineage.lineage_schema()[8].type.value_type,
            )


class FullBuildTests(unittest.TestCase):
    def test_fixture_contract_requires_explicit_injection_and_build_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            fixture = build_fixture(Path(folder))
            with self.assertRaisesRegex(ValueError, "reviewed canonical input"):
                lineage.build(fixture.args)
            manifest = lineage.build(fixture.args, expectation=fixture.expectation)
            self.assertEqual(manifest["status"], "LINEAGE_READY_B1_FEATURES_NOT_BUILT")
            self.assertFalse(manifest["readyForTraining"])
            self.assertEqual(manifest["rowCounts"]["rows"], 4)
            self.assertEqual(manifest["rowCounts"]["historyPairs"], 4)
            self.assertEqual(manifest["rowCounts"]["zeroHistoryRows"], 2)
            self.assertEqual(manifest["rowCounts"]["capCounts"], {"2": 4})
            output = pd.read_parquet(fixture.args.output_dir / "row-lineage.parquet")
            self.assertEqual(output["row_id"].tolist(), [0, 1, 2, 3])
            observed_histories = [list(values) for values in output["history_ml_ids"].tolist()]
            self.assertEqual(observed_histories, [[], [], [10, 11], [12, 10]])
            self.assertEqual(set(fixture.args.output_dir.iterdir()), {
                fixture.args.output_dir / "row-lineage.parquet",
                fixture.args.output_dir / "manifest.json",
            })

    def test_row_id_uid_and_label_mismatch_fail_closed(self) -> None:
        cases = (("row_id", 1, 99, "row_id order"), ("uid", 1, 2, "uid differs"),
                 ("label", 1, 4.5, "label differs"))
        for case_index, (column, row, value, message) in enumerate(cases):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as folder:
                fixture = build_fixture(Path(folder), output_name=f"output-{case_index}")
                frame = pd.read_parquet(fixture.args.rh_train)
                frame.loc[row, column] = value
                frame.to_parquet(fixture.args.rh_train, index=False, engine="pyarrow", row_group_size=2)
                refresh_fixture_contract(fixture)
                with self.assertRaisesRegex(ValueError, message):
                    lineage.build(fixture.args, expectation=fixture.expectation)
                self.assertFalse(fixture.args.output_dir.exists())
                self.assertEqual(temporary_outputs(Path(folder), fixture.args.output_dir.name), [])

    def test_duplicate_user_movie_is_rejected_before_history_build(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            fixture = build_fixture(Path(folder))
            ratings = pd.read_parquet(fixture.args.ratings)
            ratings.loc[1, "movie_id"] = ratings.loc[0, "movie_id"]
            ratings.to_parquet(fixture.args.ratings, index=False, engine="pyarrow")
            refresh_fixture_contract(fixture)
            with self.assertRaisesRegex(ValueError, r"duplicate \(uid, movie_id\)"):
                lineage.build(fixture.args, expectation=fixture.expectation)
            self.assertFalse(fixture.args.output_dir.exists())

    def test_input_mutation_between_checks_fails_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = build_fixture(root)
            original_verify = lineage.verify_inputs_unchanged
            calls = 0

            def mutate_after_first_check(paths: dict[str, Path], pins: dict[str, dict[str, Any]]) -> None:
                nonlocal calls
                original_verify(paths, pins)
                calls += 1
                if calls == 1:
                    paths["ratings"].write_bytes(paths["ratings"].read_bytes() + b"mutation")

            with mock.patch.object(lineage, "verify_inputs_unchanged", side_effect=mutate_after_first_check):
                with self.assertRaisesRegex(ValueError, "input ratings byte count drift"):
                    lineage.build(fixture.args, expectation=fixture.expectation)
            self.assertEqual(calls, 1)
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
                lineage.build(fixture.args, expectation=fixture.expectation)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(temporary_outputs(root, fixture.args.output_dir.name), [])

    def test_write_close_and_publish_failures_cleanup(self) -> None:
        for failure in ("write", "close", "publish"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                fixture = build_fixture(root)
                original_writer = lineage.pq.ParquetWriter

                class WriterProxy:
                    def __init__(self, *args: Any, **kwargs: Any) -> None:
                        self.inner = original_writer(*args, **kwargs)

                    def write_table(self, table: Any) -> None:
                        if failure == "write":
                            raise OSError("synthetic write failure")
                        self.inner.write_table(table)

                    def close(self) -> None:
                        self.inner.close()
                        if failure == "close":
                            raise OSError("synthetic close failure")

                patches = [mock.patch.object(lineage.pq, "ParquetWriter", WriterProxy)]
                if failure == "publish":
                    patches.append(mock.patch.object(lineage, "publish", side_effect=OSError("synthetic publish failure")))
                with patches[0]:
                    if len(patches) == 2:
                        with patches[1], self.assertRaisesRegex(OSError, f"synthetic {failure} failure"):
                            lineage.build(fixture.args, expectation=fixture.expectation)
                    else:
                        with self.assertRaisesRegex(OSError, f"synthetic {failure} failure"):
                            lineage.build(fixture.args, expectation=fixture.expectation)
                self.assertFalse(fixture.args.output_dir.exists())
                self.assertEqual(temporary_outputs(root, fixture.args.output_dir.name), [])

    def test_parquet_handles_are_released_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = build_fixture(root)
            lineage.build(fixture.args, expectation=fixture.expectation)
            moved_train = fixture.args.rh_train.with_suffix(".moved")
            os.replace(fixture.args.rh_train, moved_train)
            os.replace(moved_train, fixture.args.rh_train)
            moved_output = fixture.args.output_dir.with_name("moved-output")
            os.replace(fixture.args.output_dir, moved_output)
            os.replace(moved_output, fixture.args.output_dir)

    def test_census_failure_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture = build_fixture(root)
            wrong = replace(fixture.expectation, history_pairs=5)
            with self.assertRaisesRegex(ValueError, "history pair census drift"):
                lineage.build(fixture.args, expectation=wrong)
            self.assertFalse(fixture.args.output_dir.exists())
            self.assertEqual(temporary_outputs(root, fixture.args.output_dir.name), [])


class StaticContractTests(unittest.TestCase):
    def test_no_python_assert_and_cli_has_no_fixture_bypass(self) -> None:
        paths = [
            ROOT / "scripts" / "build_service_v1_row_lineage.py",
            Path(__file__).resolve(),
        ]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        implementation = paths[0].read_text(encoding="utf-8")
        self.assertNotIn("--allow-test", implementation)
        self.assertNotIn("--skip-contract", implementation)
        self.assertEqual(lineage.CANONICAL_CONTRACT.cap_counts, lineage.EXPECTED_CAP_COUNTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
