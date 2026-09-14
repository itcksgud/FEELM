from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import link_kobis_expanded_v3 as linker  # noqa: E402


def observation(
    source_row_id: str,
    code: str | None,
    title: str = "테스트 영화",
    open_date: str = "2020-01-02",
    nation: str = "한국",
    screening_year: int = 2020,
) -> dict[str, object]:
    return {
        "source_row_id": source_row_id,
        "screening_year": screening_year,
        "period_end": f"{screening_year}-12-31",
        "is_partial_year": False,
        "kobis_movie_code": code,
        "title": title,
        "open_date": open_date,
        "representative_nation": nation,
        "annual_admissions": 10,
        "annual_sales_krw": 100,
        "cumulative_admissions_at_period_end": 10,
        "cumulative_sales_krw_at_period_end": 100,
        "has_negative_adjustment": False,
    }


def catalog_item(
    tmdb_id: int,
    title: str = "테스트 영화",
    release_date: str = "2020-01-02",
    country: str = "KR",
) -> dict[str, object]:
    return {
        "service_movie_id": tmdb_id + 10_000,
        "tmdb_id": tmdb_id,
        "title": title,
        "original_title": title,
        "release_date": release_date,
        "production_country_codes": [country],
        "movielens_movie_id": tmdb_id + 20_000,
        "mapping_status": "MATCHED",
        "raw_vote_count_number": 123,
        "raw_vote_average_number": 7.5,
        "quality_state": "USABLE",
    }


def movielens_item(movie_id: int) -> dict[str, object]:
    return {
        "movieId": movie_id,
        "title": "Movie (2020)",
        "ml_year": 2020,
        "ml_count": 50,
        "ml_mean_5": 4.0,
    }


class LinkKobisExpandedV3Test(unittest.TestCase):
    def test_v3_has_no_python_assert_statements(self) -> None:
        source = Path(linker.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))

    def test_code_free_row_is_only_a_kobis_proposal(self) -> None:
        code_free = observation("code-free-row", None)
        code_free["cumulative_admissions_at_period_end"] = 999
        code_free["cumulative_sales_krw_at_period_end"] = 9_999
        original = pd.DataFrame(
            [
                observation("direct-row", "ABCD1234"),
                code_free,
            ]
        )
        linked = linker.resolve_identity(original)
        code_free = linked.loc[linked.source_row_id.eq("code-free-row")].iloc[0]

        self.assertIsNone(code_free.resolved_kobis_movie_code)
        self.assertEqual(code_free.proposed_kobis_movie_code, "ABCD1234")
        self.assertEqual(
            code_free.identity_method,
            "PROPOSED_EXACT_TITLE_FULL_DATE_NATION_UNIQUE_DIRECT_CODE",
        )
        self.assertEqual(
            json.loads(code_free.kobis_identity_candidate_evidence_by_code),
            {"ABCD1234": ["direct-row"]},
        )
        pd.testing.assert_frame_equal(linked[original.columns], original)
        movies = linker.rollup_movies(linked)
        self.assertEqual(int(movies.iloc[0].last_observed_cumulative_admissions), 10)
        self.assertEqual(int(movies.iloc[0].last_observed_cumulative_sales_krw), 100)

    def test_ambiguous_kobis_candidates_preserve_per_code_source_rows(self) -> None:
        original = pd.DataFrame(
            [
                observation("code-one", "CODE0001"),
                observation("code-two", "CODE0002"),
                observation("code-free", None),
            ]
        )
        linked = linker.resolve_identity(original)
        code_free = linked.loc[linked.source_row_id.eq("code-free")].iloc[0]

        self.assertIsNone(code_free.resolved_kobis_movie_code)
        self.assertIsNone(code_free.proposed_kobis_movie_code)
        self.assertEqual(code_free.identity_method, "UNRESOLVED_MULTIPLE_DIRECT_CODES")
        self.assertEqual(
            json.loads(code_free.kobis_identity_candidate_evidence_by_code),
            {"CODE0001": ["code-one"], "CODE0002": ["code-two"]},
        )

    def test_unique_tmdb_candidate_stays_proposal_only(self) -> None:
        original = pd.DataFrame([observation("row-one", "CODE0001")])
        catalog = pd.DataFrame([catalog_item(101)])
        ml = pd.DataFrame([movielens_item(20_101)])
        linked = linker.resolve_identity(original)
        movies = linker.rollup_movies(linked)
        linked, movies = linker.map_tmdb(linked, movies, catalog, ml)
        movie = movies.iloc[0]

        self.assertEqual(movie.tmdb_match_status, "PROPOSED_UNIQUE")
        self.assertEqual(movie.proposed_tmdb_id, 101)
        self.assertTrue(pd.isna(movie.tmdb_id))
        self.assertTrue(pd.isna(movie.service_movie_id))
        self.assertTrue(pd.isna(movie.movielens_movie_id))
        self.assertTrue(pd.isna(movie.ml_count))
        self.assertEqual(
            json.loads(movie.tmdb_candidate_evidence_by_id), {"101": ["row-one"]}
        )
        self.assertTrue(linked.resolved_tmdb_id.isna().all())

    def test_ambiguous_tmdb_candidates_preserve_per_id_source_rows(self) -> None:
        original = pd.DataFrame(
            [
                observation("direct", "CODE0001"),
                observation("code-free", None),
            ]
        )
        catalog = pd.DataFrame([catalog_item(101), catalog_item(102)])
        ml = pd.DataFrame([movielens_item(20_101), movielens_item(20_102)])
        linked = linker.resolve_identity(original)
        movies = linker.rollup_movies(linked)
        linked, movies = linker.map_tmdb(linked, movies, catalog, ml)
        movie = movies.iloc[0]
        code_free = linked.loc[linked.source_row_id.eq("code-free")].iloc[0]

        self.assertEqual(movie.tmdb_match_status, "AMBIGUOUS_TMDB_CANDIDATES")
        self.assertTrue(pd.isna(movie.proposed_tmdb_id))
        self.assertEqual(
            json.loads(movie.tmdb_candidate_evidence_by_id),
            {"101": ["direct"], "102": ["direct"]},
        )
        self.assertEqual(code_free.row_tmdb_proposal_status, "ROW_TMDB_AMBIGUOUS")
        self.assertEqual(
            json.loads(code_free.row_tmdb_candidate_evidence_by_id),
            {"101": ["code-free"], "102": ["code-free"]},
        )
        self.assertFalse(code_free.row_tmdb_proposal_is_confirmed_movie_identity)

    def test_multiple_kobis_codes_for_one_tmdb_require_review(self) -> None:
        original = pd.DataFrame(
            [
                observation("first", "CODE0001", open_date="2020-01-02"),
                observation(
                    "second",
                    "CODE0002",
                    open_date="2021-01-02",
                    screening_year=2021,
                ),
            ]
        )
        catalog = pd.DataFrame([catalog_item(101, release_date="2020-06-01")])
        ml = pd.DataFrame([movielens_item(20_101)])
        linked = linker.resolve_identity(original)
        movies = linker.rollup_movies(linked)
        _, movies = linker.map_tmdb(linked, movies, catalog, ml)

        self.assertEqual(
            set(movies.tmdb_match_status),
            {"MULTIPLE_KOBIS_CODES_ONE_TMDB_REQUIRES_REVIEW"},
        )
        self.assertEqual(set(movies.proposed_tmdb_id.dropna().astype(int)), {101})
        evidence = {
            row.kobis_movie_code: json.loads(row.tmdb_candidate_evidence_by_id)
            for row in movies.itertuples()
        }
        self.assertEqual(
            evidence,
            {"CODE0001": {"101": ["first"]}, "CODE0002": {"101": ["second"]}},
        )
        self.assertTrue(movies[linker.NULL_CROSS_SOURCE_COLUMNS].isna().all().all())

    def test_all_code_free_input_fails_with_explicit_contract_error(self) -> None:
        original = pd.DataFrame([observation("code-free", None)])
        linked = linker.resolve_identity(original)

        with self.assertRaisesRegex(
            linker.ContractError, "At least one directly observed KOBIS code"
        ):
            linker.rollup_movies(linked)

    def test_python_optimized_mode_rejects_missing_execute_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            output = parent / "must-not-exist"
            result = subprocess.run(
                [
                    sys.executable,
                    "-O",
                    str(Path(linker.__file__)),
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Execution requires reviewed --execute", result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(list(parent.glob(f".{output.name}.tmp-*")))

    def test_existing_output_is_preserved_and_failure_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            files = self._write_fixture_inputs(parent)

            existing = parent / "existing"
            existing.mkdir()
            sentinel = existing / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                linker.run_linking(self._config(files, existing))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse(list(parent.glob(f".{existing.name}.tmp-*")))

            failed = parent / "failed"

            def fail_after_write(stage: str) -> None:
                if stage == "after_write":
                    raise RuntimeError("injected failure")

            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                linker.run_linking(
                    self._config(files, failed), failure_injector=fail_after_write
                )
            self.assertFalse(failed.exists())
            self.assertFalse(list(parent.glob(f".{failed.name}.tmp-*")))

    def test_expected_contract_mismatch_fails_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            files = self._write_fixture_inputs(parent)
            output = parent / "contract-mismatch"
            config = self._config(files, output)
            config = linker.RunConfig(
                **{
                    **config.__dict__,
                    "expect_catalog_sha256": "0" * 64,
                }
            )

            with self.assertRaisesRegex(linker.ContractError, "catalog sha256 mismatch"):
                linker.run_linking(config)
            self.assertFalse(output.exists())
            self.assertFalse(list(parent.glob(f".{output.name}.tmp-*")))

    def test_source_drift_after_write_blocks_atomic_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            files = self._write_fixture_inputs(parent)
            output = parent / "source-drift"

            def mutate_input_after_write(stage: str) -> None:
                if stage == "after_write":
                    files["input"].write_bytes(b"mutated after outputs were staged")

            with self.assertRaisesRegex(linker.ContractError, "source drift"):
                linker.run_linking(
                    self._config(files, output),
                    failure_injector=mutate_input_after_write,
                )
            self.assertFalse(output.exists())
            self.assertFalse(list(parent.glob(f".{output.name}.tmp-*")))

    def test_successful_publication_manifest_uses_final_logical_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            files = self._write_fixture_inputs(parent)
            output = parent / "published"

            summary = linker.run_linking(self._config(files, output))
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(summary["confirmed_cross_source_tmdb_ids"], 0)
            self.assertTrue(output.is_dir())
            self.assertTrue(manifest["proposal_only"])
            self.assertEqual(len(manifest["inputs"]), 4)
            self.assertFalse(
                any(item["path"].endswith("collection-summary.json") for item in manifest["inputs"])
            )
            for item in manifest["inputs"] + manifest["outputs"]:
                self.assertFalse(Path(item["path"]).is_absolute())
                self.assertNotIn(".tmp-", item["path"])
                self.assertGreater(item["bytes"], 0)
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            self.assertFalse(list(parent.glob(f".{output.name}.tmp-*")))

    @staticmethod
    def _write_fixture_inputs(parent: Path) -> dict[str, Path]:
        input_path = parent / "annual-observations.parquet"
        catalog_path = parent / "catalog.parquet"
        ml_path = parent / "movie-rating-aggregates.parquet"
        pd.DataFrame([observation("row-one", "CODE0001")]).to_parquet(
            input_path, index=False
        )
        pd.DataFrame([catalog_item(101)]).to_parquet(catalog_path, index=False)
        pd.DataFrame([movielens_item(20_101)]).to_parquet(ml_path, index=False)
        return {"input": input_path, "catalog": catalog_path, "ml": ml_path}

    @staticmethod
    def _config(files: dict[str, Path], output: Path) -> linker.RunConfig:
        return linker.RunConfig(
            execute=True,
            input_path=files["input"],
            catalog_path=files["catalog"],
            movielens_aggregates_path=files["ml"],
            output_path=output,
            expect_input_sha256=str(linker.pin(files["input"])["sha256"]),
            expect_input_rows=1,
            expect_catalog_sha256=str(linker.pin(files["catalog"])["sha256"]),
            expect_catalog_rows=1,
            expect_movielens_aggregates_sha256=str(linker.pin(files["ml"])["sha256"]),
            expect_movielens_aggregates_rows=1,
        )


if __name__ == "__main__":
    unittest.main()
