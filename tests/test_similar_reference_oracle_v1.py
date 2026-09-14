from __future__ import annotations

import ast
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

import numpy as np
import pandas as pd


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import build_similar_reference_oracle_v1 as oracle  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_equal(actual: Any, expected: Any, message: str) -> None:
    require(actual == expected, f"{message}: expected {expected!r}, got {actual!r}")


def require_close(actual: float, expected: float, tolerance: float, message: str) -> None:
    require(abs(actual - expected) <= tolerance, f"{message}: expected {expected}, got {actual}")


def expect_contract_error(action: Callable[[], Any], message_fragment: str | None = None) -> None:
    try:
        action()
    except oracle.ContractError as error:
        if message_fragment is not None:
            require(message_fragment in str(error), f"missing error fragment {message_fragment!r}: {error}")
        return
    raise AssertionError("ContractError was not raised")


def normalized_random_vectors(row_count: int, seed: int = 41) -> np.ndarray:
    rng = np.random.default_rng(seed)
    values = rng.normal(size=(row_count, oracle.DIMENSIONS)).astype(np.float32)
    norms = np.sqrt(np.sum(values.astype(np.float64) ** 2, axis=1, dtype=np.float64))
    return (values.astype(np.float64) / norms[:, None]).astype(np.float32)


def brute_force(
    vectors: np.ndarray,
    reference_ids: np.ndarray,
    tmdb_ids: np.ndarray,
    supported: np.ndarray,
    query_row: int,
    top_k: int,
) -> tuple[list[tuple[int, int, float]], int, float | None, int, int]:
    values = vectors.astype(np.float64)
    norms = np.sqrt(np.sum(values * values, axis=1, dtype=np.float64))
    scores = np.sum(values * values[query_row], axis=1, dtype=np.float64) / (norms * norms[query_row])
    scores = np.clip(scores, -1.0, 1.0)
    valid = (
        supported
        & np.isfinite(scores)
        & (scores > 0.0)
        & (reference_ids != reference_ids[query_row])
        & (tmdb_ids != tmdb_ids[query_row])
    )
    rows = np.flatnonzero(valid)
    ordering = np.lexsort((reference_ids[rows], -scores[rows]))
    rows = rows[ordering]
    positive_count = len(rows)
    cutoff = float(scores[rows[9]]) if positive_count >= 10 else None
    tie_count = int(np.count_nonzero(scores[rows] == cutoff)) if cutoff is not None else 0
    boundary_count = int(np.count_nonzero(scores[rows] >= cutoff)) if cutoff is not None else positive_count
    neighbors = [(int(row), int(reference_ids[row]), float(scores[row])) for row in rows[:top_k]]
    return neighbors, positive_count, cutoff, tie_count, boundary_count


def exact_signature(results: list[oracle.ExactQueryResult]) -> list[Any]:
    return [
        (
            result.query_row_index,
            result.positive_candidate_count,
            result.exact10_cutoff_similarity,
            result.exact10_cutoff_tie_count,
            result.exact10_boundary_set_count,
            tuple(
                (
                    neighbor.row_index,
                    neighbor.reference_service_movie_id,
                    neighbor.tmdb_id,
                    neighbor.similarity,
                )
                for neighbor in result.neighbors
            ),
        )
        for result in results
    ]


def write_fixture(root: Path, *, row_count: int = 16) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "similar-movies.v1.json"
    config = {
        "schemaVersion": "feelm-similar-movies/1",
        "policyVersion": "fixture-v1",
        "status": "LOCAL_DESIGN_IMPLEMENTATION_NOT_RUN",
        "dimensions": 131,
        "sourcePrecision": "float64",
        "databasePrecision": "float32",
        "metric": "cosine_on_database_vectors",
        "sourceToDatabaseScoreTolerance": 0.000001,
        "requireSubSupported": True,
        "requireTopSupported": False,
        "restrictToTasteOrChildGroup": False,
        "minimumSimilarityExclusive": 0,
        "sharedCacheCandidateLimit": 50,
        "retrievalAudit": {
            "maximumQuerySample": 200,
            "seed": 339,
            "exactNeighborCutoff": 10,
            "boundaryTiesAccepted": True,
            "maximumManualReviewSources": 30,
            "servingQualityAlreadyValidated": False,
        },
    }
    oracle.write_json(config_path, config)

    vectors = normalized_random_vectors(row_count)
    float32_path = root / "content-vectors.float32.npy"
    float64_path = root / "content-vectors.float64.npy"
    np.save(float32_path, vectors, allow_pickle=False)
    np.save(float64_path, vectors.astype(np.float64), allow_pickle=False)

    reference_ids = np.arange(1001, 1001 + row_count, dtype=np.int64)
    tmdb_ids = np.arange(5001, 5001 + row_count, dtype=np.int64)
    index = pd.DataFrame(
        {
            "rowIndex": np.arange(row_count, dtype=np.int64),
            "referenceServiceMovieId": reference_ids,
            "tmdbId": tmdb_ids,
            "subSupported": np.ones(row_count, dtype=bool),
        }
    )
    index_path = root / "movie-index.parquet"
    index.to_parquet(index_path, index=False, engine="pyarrow")

    catalog = pd.DataFrame(
        {
            "service_movie_id": reference_ids,
            "tmdb_id": tmdb_ids,
            "title": [f"Movie <{number}>" for number in range(row_count)],
            "original_title": [f"Original {number}" for number in range(row_count)],
            "genre_ids": [[18] for _ in range(row_count)],
            "keyword_ids": [[number + 1] for number in range(row_count)],
            "overview": [f"Overview {number}" for number in range(row_count)],
            "production_country_codes": [["KR"] if number % 2 == 0 else ["US"] for number in range(row_count)],
            "origin_country_codes": [[] for _ in range(row_count)],
            "release_year": [2022 if number % 3 == 0 else 2018 for number in range(row_count)],
        }
    )
    catalog_path = root / "catalog.parquet"
    catalog.to_parquet(catalog_path, index=False, engine="pyarrow")

    similar_export_path = root / "similar-vector-export.json"
    similar_export = {
        "schemaVersion": "feelm-similar-vector-export/1",
        "status": "SIMILARITY_NOT_READY",
        "stagingVersion": "fixture-staging-v1",
        "sourceCatalogVersion": "fixture-catalog-v1",
        "sourceVector": "GROUPS.content_x",
        "dimensions": 131,
        "precision": "float32",
        "metric": "cosine_on_database_vectors",
        "requiresSubSupported": True,
        "requiresTopSupported": False,
        "candidateEligibilityIncluded": False,
        "verifiedCurrentServiceIdMappingIncluded": False,
        "pairwiseNeighborsGenerated": False,
        "movieIndex": "movie-index.parquet",
        "vectorMatrix": "content-vectors.float32.npy",
        "rowJoinKey": "rowIndex",
    }
    oracle.write_json(similar_export_path, similar_export)

    manifest_path = root / "manifest.json"
    config_sha = oracle.sha256_file(config_path)
    manifest = {
        "schemaVersion": "feelm-gkt-staging-bundle/1",
        "status": "STAGING_ID_MAPPING_REQUIRED",
        "readyForServing": False,
        "stagingVersion": "fixture-staging-v1",
        "sourceCatalogVersion": "fixture-catalog-v1",
        "similarityStatus": "SIMILARITY_NOT_READY",
        "pairwiseNeighborsGenerated": False,
        "similarRecipeSha256": config_sha,
        "reviewedFingerprint": {"similarRecipeSha256": config_sha},
        "logicalRowSchema": {
            "dimensions": 131,
            "precision": "float32",
            "vectorJoin": "index.rowIndex == vector row",
        },
        "rowCounts": {"rows": row_count, "dimensions": 131, "subSupportedRows": row_count},
        "serviceIdMapping": {"status": "NOT_APPLIED", "serviceIdMappedRows": 0},
        "sources": {
            "referenceCatalog": oracle.file_record(catalog_path),
            "referenceContentVectors": oracle.file_record(float64_path),
        },
        "artifacts": {
            "movie-index.parquet": oracle.file_record(index_path),
            "content-vectors.float32.npy": oracle.file_record(float32_path),
            "similar-vector-export.json": oracle.file_record(similar_export_path),
        },
    }
    oracle.write_json(manifest_path, manifest)
    return {
        "staging_manifest_path": manifest_path,
        "movie_index_path": index_path,
        "reference_catalog_path": catalog_path,
        "float32_vectors_path": float32_path,
        "float64_vectors_path": float64_path,
        "similar_vector_export_path": similar_export_path,
        "similar_config_path": config_path,
    }


def refresh_artifact_hash(paths: dict[str, Path], artifact_name: str, artifact_path: Path) -> None:
    manifest = oracle.read_json(paths["staging_manifest_path"])
    manifest["artifacts"][artifact_name] = oracle.file_record(artifact_path)
    oracle.write_json(paths["staging_manifest_path"], manifest)


class ExactCosineTests(unittest.TestCase):
    def test_blockwise_matches_independent_brute_force(self) -> None:
        vectors = normalized_random_vectors(23)
        reference_ids = np.arange(400, 423, dtype=np.int64)[::-1]
        tmdb_ids = np.arange(900, 923, dtype=np.int64)
        supported = np.ones(23, dtype=bool)
        supported[7] = False
        query_rows = [0, 3, 10]
        actual = oracle.exact_topk_blockwise(
            vectors=vectors,
            reference_service_movie_ids=reference_ids,
            tmdb_ids=tmdb_ids,
            sub_supported=supported,
            query_rows=query_rows,
            top_k=12,
            candidate_block_size=4,
            query_batch_size=2,
        )
        for result in actual:
            expected, count, cutoff, ties, boundary = brute_force(
                vectors, reference_ids, tmdb_ids, supported, result.query_row_index, 12
            )
            require_equal(result.positive_candidate_count, count, "positive count")
            require_equal(result.exact10_cutoff_tie_count, ties, "cutoff tie count")
            require_equal(result.exact10_boundary_set_count, boundary, "boundary set count")
            if cutoff is None:
                require(result.exact10_cutoff_similarity is None, "cutoff should be absent")
            else:
                require_close(result.exact10_cutoff_similarity or 0.0, cutoff, 1e-15, "cutoff score")
            require_equal(len(result.neighbors), len(expected), "neighbor length")
            for neighbor, expected_row in zip(result.neighbors, expected, strict=True):
                require_equal(neighbor.row_index, expected_row[0], "neighbor row")
                require_equal(neighbor.reference_service_movie_id, expected_row[1], "neighbor ID")
                require_close(neighbor.similarity, expected_row[2], 1e-15, "neighbor score")

    def test_block_and_query_batch_sizes_do_not_change_results(self) -> None:
        vectors = normalized_random_vectors(31, seed=9)
        reference_ids = np.arange(2000, 2031, dtype=np.int64)
        tmdb_ids = np.arange(8000, 8031, dtype=np.int64)
        supported = np.ones(31, dtype=bool)
        kwargs = {
            "vectors": vectors,
            "reference_service_movie_ids": reference_ids,
            "tmdb_ids": tmdb_ids,
            "sub_supported": supported,
            "query_rows": [0, 2, 4, 6],
            "top_k": 20,
        }
        first = oracle.exact_topk_blockwise(**kwargs, candidate_block_size=3, query_batch_size=1)
        second = oracle.exact_topk_blockwise(**kwargs, candidate_block_size=17, query_batch_size=3)
        require_equal(exact_signature(first), exact_signature(second), "block/batch invariant result")

    def test_stable_ties_and_same_tmdb_self_nonpositive_filters(self) -> None:
        vectors = np.zeros((16, oracle.DIMENSIONS), dtype=np.float32)
        vectors[0, 0] = 1.0
        vectors[1:13, 0] = 1.0
        vectors[13, 0] = -1.0
        vectors[14, 1] = 1.0
        vectors[15, 0] = 0.5
        reference_ids = np.array([999, 90, 80, 70, 60, 50, 40, 30, 20, 10, 100, 110, 120, 130, 140, 5])
        tmdb_ids = np.arange(300, 316, dtype=np.int64)
        tmdb_ids[1] = tmdb_ids[0]
        supported = np.ones(16, dtype=bool)
        result = oracle.exact_topk_blockwise(
            vectors=vectors,
            reference_service_movie_ids=reference_ids,
            tmdb_ids=tmdb_ids,
            sub_supported=supported,
            query_rows=[0],
            top_k=10,
            candidate_block_size=2,
            query_batch_size=1,
        )[0]
        expected_ids = sorted(reference_ids[list(range(2, 13))].tolist() + [5])[:10]
        require_equal([item.reference_service_movie_id for item in result.neighbors], expected_ids, "tie ID order")
        require_equal(result.positive_candidate_count, 12, "filtered positive count")
        require_equal(result.exact10_cutoff_tie_count, 12, "tie count across full axis")
        require_equal(result.exact10_boundary_set_count, 12, "tie-expanded boundary count")
        require_equal(result.exact10_cutoff_similarity, 1.0, "tie cutoff")

    def test_shortage_returns_available_without_fill(self) -> None:
        vectors = np.zeros((6, oracle.DIMENSIONS), dtype=np.float32)
        vectors[0, 0] = 1.0
        vectors[1, 0] = 0.8
        vectors[1, 1] = 0.2
        vectors[2, 0] = 0.2
        vectors[2, 1] = 0.8
        vectors[3, 0] = -1.0
        vectors[4, 1] = 1.0
        vectors[5, 2] = 1.0
        result = oracle.exact_topk_blockwise(
            vectors=vectors,
            reference_service_movie_ids=np.arange(10, 16),
            tmdb_ids=np.arange(20, 26),
            sub_supported=np.ones(6, dtype=bool),
            query_rows=[0],
            top_k=50,
            candidate_block_size=3,
            query_batch_size=1,
        )[0]
        require_equal(result.positive_candidate_count, 2, "shortage positive count")
        require_equal(len(result.neighbors), 2, "shortage output length")
        require(result.exact10_cutoff_similarity is None, "shortage cutoff must be absent")
        require_equal(result.exact10_cutoff_tie_count, 0, "shortage tie count")

    def test_all_movie_query_set_and_caps_fail_closed(self) -> None:
        vectors = normalized_random_vectors(4)
        common = {
            "vectors": vectors,
            "reference_service_movie_ids": np.arange(1, 5),
            "tmdb_ids": np.arange(11, 15),
            "sub_supported": np.ones(4, dtype=bool),
            "top_k": 10,
            "candidate_block_size": 2,
            "query_batch_size": 1,
        }
        expect_contract_error(
            lambda: oracle.exact_topk_blockwise(**common, query_rows=[0, 1, 2, 3]),
            "all-movie",
        )
        expect_contract_error(
            lambda: oracle.exact_topk_blockwise(**{**common, "top_k": 51}, query_rows=[0]),
            "top_k",
        )
        many_vectors = normalized_random_vectors(202)
        expect_contract_error(
            lambda: oracle.exact_topk_blockwise(
                vectors=many_vectors,
                reference_service_movie_ids=np.arange(202),
                tmdb_ids=np.arange(1000, 1202),
                sub_supported=np.ones(202, dtype=bool),
                query_rows=list(range(201)),
            ),
            "200",
        )


class SelectionTests(unittest.TestCase):
    def test_result_blind_deterministic_balanced_strata_and_missing_year(self) -> None:
        rows: list[dict[str, Any]] = []
        vectors = np.zeros((14, oracle.DIMENSIONS), dtype=np.float32)
        ordinal = 0
        for region in oracle.REGION_ORDER:
            for era in oracle.ERA_ORDER:
                for content in oracle.CONTENT_ORDER:
                    rows.append(
                        {
                            "service_movie_id": 100 + ordinal,
                            "tmdb_id": 500 + ordinal,
                            "title": f"S{ordinal}",
                            "original_title": f"S{ordinal}",
                            "genre_ids": [] if content == "GENRE_MISSING" else [18],
                            "keyword_ids": [1] if content == "COMPLEX" else [],
                            "overview": "plot" if content == "COMPLEX" else "",
                            "production_country_codes": ["KR"] if region == "KOREA" else ["US"],
                            "origin_country_codes": [],
                            "release_year": 2022 if era == "2020_PLUS" else 2018,
                        }
                    )
                    if content != "GENRE_MISSING":
                        vectors[ordinal, 0] = 1.0
                    if content == "COMPLEX" or content == "GENRE_MISSING":
                        vectors[ordinal, oracle.GENRE_DIMENSIONS] = 1.0
                    ordinal += 1
        rows.append(
            {
                "service_movie_id": 112,
                "tmdb_id": 512,
                "title": "Missing year",
                "original_title": "Missing year",
                "genre_ids": [18],
                "keyword_ids": [],
                "overview": "",
                "production_country_codes": ["KR"],
                "origin_country_codes": [],
                "release_year": None,
            }
        )
        vectors[12, 0] = 1.0
        rows.append(
            {
                "service_movie_id": 113,
                "tmdb_id": 513,
                "title": "Unsupported",
                "original_title": "Unsupported",
                "genre_ids": [],
                "keyword_ids": [],
                "overview": "",
                "production_country_codes": [],
                "origin_country_codes": [],
                "release_year": 2021,
            }
        )
        index = pd.DataFrame(
            {
                "referenceServiceMovieId": np.arange(100, 114),
                "tmdbId": np.arange(500, 514),
                "subSupported": [True] * 13 + [False],
            }
        )
        catalog = pd.DataFrame(rows)
        first = oracle.select_query_rows(index=index, catalog=catalog, vectors=vectors, query_limit=12)
        changed_magnitudes = vectors.copy()
        changed_magnitudes[changed_magnitudes != 0] *= np.float32(0.37)
        second = oracle.select_query_rows(index=index, catalog=catalog, vectors=changed_magnitudes, query_limit=12)
        require_equal(first.rows, second.rows, "selection must not depend on similarity magnitudes")
        require_equal(len(first.rows), 12, "one query per populated stratum")
        require_equal(first.missing_year_excluded, 1, "missing-year exclusion count")
        require(all(count == 1 for count in first.stratum_population.values()), "each stratum population should be one")
        require(all(count == 1 for count in first.stratum_sample.values()), "each stratum should be sampled once")

    def test_selection_never_selects_entire_supported_axis(self) -> None:
        vectors = np.zeros((3, oracle.DIMENSIONS), dtype=np.float32)
        vectors[:, 0] = 1.0
        index = pd.DataFrame(
            {
                "referenceServiceMovieId": [1, 2, 3],
                "tmdbId": [11, 12, 13],
                "subSupported": [True, True, True],
            }
        )
        catalog = pd.DataFrame(
            {
                "title": ["a", "b", "c"],
                "original_title": ["a", "b", "c"],
                "production_country_codes": [["KR"], ["US"], ["US"]],
                "origin_country_codes": [[], [], []],
                "release_year": [2022, 2018, 2021],
            }
        )
        selection = oracle.select_query_rows(index=index, catalog=catalog, vectors=vectors, query_limit=3)
        require_equal(len(selection.rows), 2, "all-movie safety reduction")
        require_equal(selection.all_movie_guard_reduction, 1, "all-movie reduction count")


class BundleTests(unittest.TestCase):
    def _build(self, root: Path, **overrides: Any) -> tuple[dict[str, Any], Path]:
        paths = write_fixture(root / "inputs")
        output = root / "reference-oracle-v1"
        kwargs = {
            **paths,
            "output_dir": output,
            "query_limit": 6,
            "manual_review_limit": 2,
            "candidate_block_size": 3,
            "query_batch_size": 2,
        }
        kwargs.update(overrides)
        return oracle.build_reference_oracle(**kwargs), output

    def test_valid_bundle_has_required_outputs_hashes_and_reference_only_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, output = self._build(Path(directory))
            expected = {
                "manifest.json",
                "query-sample.parquet",
                "reference-top50.parquet",
                "reference-top10-audit.parquet",
                "benchmark.json",
                "manual-review.html",
            }
            require_equal({path.name for path in output.iterdir()}, expected, "output file set")
            require_equal(manifest["status"], oracle.STATUS, "reference-only status")
            require(manifest["readyForServing"] is False, "serving readiness must be false")
            require_equal(manifest["candidateEligibility"], oracle.CANDIDATE_ELIGIBILITY, "eligibility label")
            require_equal(manifest["metric"], oracle.METRIC_LABEL, "metric label")
            require_equal(manifest["pgvectorParity"], oracle.PGVECTOR_PARITY, "pgvector parity label")
            require_equal(manifest["kobisEvidence"], oracle.KOBIS_EVIDENCE, "KOBIS label")
            require(manifest["sourcePrecisionAudit"]["databaseParity"] is False, "source audit is not DB parity")
            require(manifest["safety"]["pairwiseMatrixGenerated"] is False, "pairwise matrix must be absent")
            require(manifest["safety"]["allMovieNeighborsGenerated"] is False, "all-movie output must be absent")
            require_equal(len(manifest["inputs"]), 7, "all seven input hashes")
            for name, record in manifest["outputs"].items():
                path = output / name
                require_equal(oracle.sha256_file(path), record["sha256"], f"output hash {name}")
                require_equal(path.stat().st_size, record["bytes"], f"output bytes {name}")

            top50 = pd.read_parquet(output / "reference-top50.parquet")
            require(bool((top50.groupby("queryRowIndex").size() <= 50).all()), "Top50 cap")
            require(bool((top50["similarity"] > 0.0).all()), "positive-only neighbors")
            require(bool((top50["candidateEligibility"] == "UNKNOWN").all()), "candidate labels")
            audit = pd.read_parquet(output / "reference-top10-audit.parquet")
            require("exact10CutoffTieCount" in audit.columns, "exact10 tie count column")
            require("positiveCandidateCount" in audit.columns, "positive-count column")
            benchmark = oracle.read_json(output / "benchmark.json")
            expected_first_pass = benchmark["queryCount"] * benchmark["candidateCount"]
            expected_rescan_queries = int(audit["exact10CutoffSimilarity"].notna().sum())
            expected_rescan = expected_rescan_queries * benchmark["candidateCount"]
            require_equal(benchmark["uniqueQueryCandidatePairs"], expected_first_pass, "unique scored pairs")
            require_equal(benchmark["firstPassScoreCalculations"], expected_first_pass, "first-pass scores")
            require_equal(benchmark["tieBoundaryRescanQueryCount"], expected_rescan_queries, "rescan queries")
            require_equal(benchmark["tieBoundaryRescanScoreCalculations"], expected_rescan, "rescan scores")
            require_equal(
                benchmark["scoreCalculationsTotal"],
                expected_first_pass + expected_rescan,
                "actual score calculations",
            )
            require_equal(benchmark["pairEvaluations"], benchmark["scoreCalculationsTotal"], "pair definition")
            require("threads" in benchmark and "detectedPools" in benchmark["threads"], "thread profile")
            require("memory" in benchmark and "peakRssBytes" in benchmark["memory"], "memory profile")
            require("timingScope" in benchmark and "timingExcludes" in benchmark, "timing scope")
            review = (output / "manual-review.html").read_text(encoding="utf-8")
            require("REFERENCE_ONLY" in review, "manual review status")
            require("pgvectorParity=NOT_RUN" in review, "manual review parity label")
            require_equal(review.count("<h2>"), 2, "manual review query cap")
            require("Movie &lt;" in review, "manual-review HTML escaping")

    def test_memmaps_are_closed_after_success_and_post_load_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_fixture(root / "success-input")
            with mock.patch.object(oracle, "_close_numpy_mmap", wraps=oracle._close_numpy_mmap) as close_spy:
                oracle.build_reference_oracle(**paths, output_dir=root / "success-output", query_limit=3)
                require_equal(close_spy.call_count, 2, "success mmap close count")

            paths = write_fixture(root / "failure-input")
            occupied_output = root / "occupied-output"
            occupied_output.mkdir()
            with mock.patch.object(oracle, "_close_numpy_mmap", wraps=oracle._close_numpy_mmap) as close_spy:
                expect_contract_error(
                    lambda: oracle.build_reference_oracle(
                        **paths,
                        output_dir=occupied_output,
                        query_limit=3,
                    ),
                    "immutable output",
                )
                require_equal(close_spy.call_count, 2, "failure mmap close count")

    def test_first_memmap_is_closed_when_second_load_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_fixture(root / "inputs")
            original_load = oracle.np.load
            load_calls = 0

            def failing_second_load(*args: Any, **kwargs: Any) -> np.ndarray:
                nonlocal load_calls
                load_calls += 1
                if load_calls == 2:
                    raise OSError("synthetic second mmap failure")
                return original_load(*args, **kwargs)

            with (
                mock.patch.object(oracle.np, "load", side_effect=failing_second_load),
                mock.patch.object(oracle, "_close_numpy_mmap", wraps=oracle._close_numpy_mmap) as close_spy,
            ):
                try:
                    oracle.build_reference_oracle(**paths, output_dir=root / "output", query_limit=3)
                except OSError as error:
                    require("synthetic second mmap failure" in str(error), "expected injected load failure")
                else:
                    raise AssertionError("OSError was not raised")
                require_equal(close_spy.call_count, 2, "partial-load close attempts")

    def test_output_is_immutable_and_no_partial_directory_remains_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, output = self._build(root)
            paths = write_fixture(root / "second-input")
            expect_contract_error(
                lambda: oracle.build_reference_oracle(**paths, output_dir=output, query_limit=3),
                "immutable output",
            )
            config = oracle.read_json(paths["similar_config_path"])
            config["unexpected"] = True
            oracle.write_json(paths["similar_config_path"], config)
            failed_output = root / "failed-output"
            expect_contract_error(
                lambda: oracle.build_reference_oracle(**paths, output_dir=failed_output, query_limit=3),
                "SHA-256",
            )
            require(not failed_output.exists(), "failed build must not publish output")
            require(not list(root.glob(".failed-output.tmp-*")), "failed build must remove temp directories")

    def test_input_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_fixture(root / "inputs")
            config = oracle.read_json(paths["similar_config_path"])
            config["status"] = "CHANGED_AFTER_REVIEW"
            oracle.write_json(paths["similar_config_path"], config)
            expect_contract_error(
                lambda: oracle.build_reference_oracle(**paths, output_dir=root / "output", query_limit=3),
                "SHA-256",
            )

    def test_duplicate_tmdb_id_fails_even_with_refreshed_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_fixture(root / "inputs")
            index = pd.read_parquet(paths["movie_index_path"])
            index.loc[1, "tmdbId"] = index.loc[0, "tmdbId"]
            index.to_parquet(paths["movie_index_path"], index=False, engine="pyarrow")
            refresh_artifact_hash(paths, "movie-index.parquet", paths["movie_index_path"])
            expect_contract_error(
                lambda: oracle.build_reference_oracle(**paths, output_dir=root / "output", query_limit=3),
                "tmdbId contains duplicates",
            )

    def test_nonfinite_or_wrong_shape_vectors_fail_with_valid_refreshed_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_fixture(root / "nan-input")
            vectors = np.load(paths["float32_vectors_path"])
            vectors[2, 7] = np.nan
            np.save(paths["float32_vectors_path"], vectors, allow_pickle=False)
            refresh_artifact_hash(paths, "content-vectors.float32.npy", paths["float32_vectors_path"])
            expect_contract_error(
                lambda: oracle.build_reference_oracle(**paths, output_dir=root / "nan-output", query_limit=3),
                "NaN or infinity",
            )

            paths = write_fixture(root / "shape-input")
            wrong = np.zeros((16, 130), dtype=np.float32)
            np.save(paths["float32_vectors_path"], wrong, allow_pickle=False)
            refresh_artifact_hash(paths, "content-vectors.float32.npy", paths["float32_vectors_path"])
            expect_contract_error(
                lambda: oracle.build_reference_oracle(**paths, output_dir=root / "shape-output", query_limit=3),
                "width must be 131",
            )

    def test_sub_supported_mask_must_match_both_vector_precisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_fixture(root / "inputs")
            index = pd.read_parquet(paths["movie_index_path"])
            index.loc[0, "subSupported"] = False
            index.to_parquet(paths["movie_index_path"], index=False, engine="pyarrow")
            manifest = oracle.read_json(paths["staging_manifest_path"])
            manifest["rowCounts"]["subSupportedRows"] = int(index["subSupported"].sum())
            manifest["artifacts"]["movie-index.parquet"] = oracle.file_record(paths["movie_index_path"])
            oracle.write_json(paths["staging_manifest_path"], manifest)
            expect_contract_error(
                lambda: oracle.build_reference_oracle(**paths, output_dir=root / "output", query_limit=3),
                "subSupported mask",
            )

    def test_query_and_manual_caps_fail_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_fixture(root / "inputs")
            expect_contract_error(
                lambda: oracle.build_reference_oracle(**paths, output_dir=root / "q", query_limit=201),
                "between 1 and 200",
            )
            expect_contract_error(
                lambda: oracle.build_reference_oracle(
                    **paths, output_dir=root / "m", query_limit=2, manual_review_limit=31
                ),
                "0..30",
            )


class StaticContractTests(unittest.TestCase):
    def test_no_python_assert_statements_and_no_workspace_path_defaults(self) -> None:
        paths = [
            REPOSITORY_ROOT / "scripts" / "build_similar_reference_oracle_v1.py",
            Path(__file__).resolve(),
        ]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            require(not any(isinstance(node, ast.Assert) for node in ast.walk(tree)), f"assert statement in {path}")
        parser_source = paths[0].read_text(encoding="utf-8")
        require("C:\\higher\\projects" not in parser_source, "CLI must not hardcode the current workspace")
        required_flags = (
            "--staging-manifest",
            "--movie-index",
            "--reference-catalog",
            "--float32-vectors",
            "--float64-vectors",
            "--similar-vector-export",
            "--similar-config",
            "--output-dir",
        )
        for flag in required_flags:
            require(flag in parser_source, f"required CLI flag missing: {flag}")

    def test_exact_fixed_labels(self) -> None:
        require_equal(oracle.STATUS, "REFERENCE_ONLY", "status")
        require_equal(oracle.CANDIDATE_ELIGIBILITY, "UNKNOWN", "eligibility")
        require_equal(
            oracle.METRIC_LABEL,
            "float32_storage_cosine_with_float64_accumulation",
            "metric",
        )
        require_equal(oracle.PGVECTOR_PARITY, "NOT_RUN", "parity")
        require_equal(oracle.KOBIS_EVIDENCE, "UNAVAILABLE_NO_CONFIRMED_LINK", "KOBIS")
        require_equal(oracle.MAX_QUERY_COUNT, 200, "query cap")
        require_equal(oracle.MAX_MANUAL_REVIEW_COUNT, 30, "manual cap")
        require_equal(oracle.MAX_REFERENCE_NEIGHBORS, 50, "neighbor cap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
