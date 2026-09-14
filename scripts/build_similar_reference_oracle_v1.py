#!/usr/bin/env python3
"""Build a bounded exact-cosine reference oracle for similar-movie S07.

The artifact produced here is deliberately a local reference, not a serving
index.  It evaluates at most 200 result-blind query movies against the
``subSupported`` reference axis and never materializes an all-pairs matrix or
neighbors for every movie.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import platform
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd


SCHEMA_VERSION = "feelm-similar-reference-oracle/1"
STATUS = "REFERENCE_ONLY"
CANDIDATE_ELIGIBILITY = "UNKNOWN"
METRIC_LABEL = "float32_storage_cosine_with_float64_accumulation"
PGVECTOR_PARITY = "NOT_RUN"
KOBIS_EVIDENCE = "UNAVAILABLE_NO_CONFIRMED_LINK"
DIMENSIONS = 131
GENRE_DIMENSIONS = 19
KEYWORD_DIMENSIONS = 64
MAX_QUERY_COUNT = 200
MAX_MANUAL_REVIEW_COUNT = 30
MAX_REFERENCE_NEIGHBORS = 50
EXACT_AUDIT_CUTOFF = 10
SOURCE_PRECISION_TOLERANCE = 1e-6

REGION_ORDER = ("KOREA", "REST")
ERA_ORDER = ("2020_PLUS", "PRE_2020")
CONTENT_ORDER = ("GENRE_ONLY", "COMPLEX", "GENRE_MISSING")

QUERY_COLUMNS = (
    "rowIndex",
    "referenceServiceMovieId",
    "tmdbId",
    "title",
    "originalTitle",
    "releaseYear",
    "countryCodes",
    "regionStratum",
    "eraStratum",
    "contentStratum",
    "subSupported",
    "candidateEligibility",
    "kobisEvidence",
    "sampleOrder",
)
TOP50_COLUMNS = (
    "querySampleOrder",
    "queryRowIndex",
    "queryReferenceServiceMovieId",
    "queryTmdbId",
    "rank",
    "candidateRowIndex",
    "candidateReferenceServiceMovieId",
    "candidateTmdbId",
    "candidateTitle",
    "candidateOriginalTitle",
    "candidateReleaseYear",
    "candidateCountryCodes",
    "candidateGenreIds",
    "similarity",
    "candidateEligibility",
)
AUDIT_COLUMNS = (
    "querySampleOrder",
    "queryRowIndex",
    "queryReferenceServiceMovieId",
    "queryTmdbId",
    "positiveCandidateCount",
    "exactTop10Denominator",
    "exact10CutoffSimilarity",
    "exact10CutoffTieCount",
    "exact10BoundarySetCount",
    "returnedTop50Count",
    "shortageBelow10",
    "sourcePrecisionComparedPairCount",
    "sourcePrecisionMaximumAbsoluteDifference",
    "sourcePrecisionAuditOnlyNotPgvectorParity",
    "candidateEligibility",
)


class ContractError(RuntimeError):
    """Raised when a reviewed input or safety invariant is violated."""


def require(condition: bool, message: str) -> None:
    """Fail closed without relying on Python's removable ``assert`` statement."""

    if not condition:
        raise ContractError(message)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": int(path.stat().st_size),
        "sha256": sha256_file(path),
    }


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as source:
        value = json.load(source)
    require(isinstance(value, dict), f"{path}: JSON root must be an object")
    return value


def write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")


def _require_file(path: Path, label: str) -> None:
    require(path.is_file(), f"{label} does not exist or is not a file: {path}")


def _verify_file_record(path: Path, expected: dict[str, Any], label: str) -> dict[str, Any]:
    _require_file(path, label)
    actual = file_record(path)
    require(
        int(expected.get("bytes", -1)) == actual["bytes"],
        f"{label} byte-size mismatch: expected {expected.get('bytes')}, got {actual['bytes']}",
    )
    require(
        str(expected.get("sha256", "")) == actual["sha256"],
        f"{label} SHA-256 mismatch",
    )
    return actual


def _artifact_record(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    artifacts = manifest.get("artifacts")
    require(isinstance(artifacts, dict), "staging manifest.artifacts must be an object")
    record = artifacts.get(name)
    require(isinstance(record, dict), f"staging manifest is missing artifact record: {name}")
    return record


def _source_record(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    sources = manifest.get("sources")
    require(isinstance(sources, dict), "staging manifest.sources must be an object")
    record = sources.get(name)
    require(isinstance(record, dict), f"staging manifest is missing source record: {name}")
    return record


def _integer_array(series: pd.Series, label: str, *, positive: bool = False) -> np.ndarray:
    numeric = pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64)
    require(np.all(np.isfinite(numeric)), f"{label} contains missing or non-numeric values")
    require(np.all(numeric == np.floor(numeric)), f"{label} contains non-integral values")
    if positive:
        require(np.all(numeric > 0), f"{label} must contain positive values")
    return numeric.astype(np.int64)


def _strict_bool_array(series: pd.Series, label: str) -> np.ndarray:
    require(not bool(series.isna().any()), f"{label} contains missing values")
    values = series.to_numpy()
    valid = np.array([isinstance(v, (bool, np.bool_)) for v in values], dtype=bool)
    require(bool(np.all(valid)), f"{label} must contain booleans only")
    return values.astype(bool)


def _finite_scan(vectors: np.ndarray, label: str, block_size: int = 8192) -> None:
    for start in range(0, vectors.shape[0], block_size):
        block = np.asarray(vectors[start : start + block_size])
        require(bool(np.all(np.isfinite(block))), f"{label} contains NaN or infinity")


def float32_storage_norms(vectors: np.ndarray, block_size: int = 8192) -> np.ndarray:
    """Norm the stored float32 coordinates with float64 accumulation."""

    require(vectors.ndim == 2, "vectors must be two-dimensional")
    require(vectors.dtype == np.float32, "storage vectors must have dtype float32")
    require(block_size >= 1, "norm block size must be positive")
    norms = np.empty(vectors.shape[0], dtype=np.float64)
    for start in range(0, vectors.shape[0], block_size):
        stop = min(start + block_size, vectors.shape[0])
        block64 = np.asarray(vectors[start:stop], dtype=np.float64)
        squared = np.sum(block64 * block64, axis=1, dtype=np.float64)
        require(bool(np.all(np.isfinite(squared))), "vector norm overflowed")
        norms[start:stop] = np.sqrt(squared)
    return norms


@dataclass(frozen=True)
class ValidatedInputs:
    staging_manifest: dict[str, Any]
    similar_export: dict[str, Any]
    config: dict[str, Any]
    index: pd.DataFrame
    catalog: pd.DataFrame
    float32_vectors: np.ndarray
    float64_vectors: np.ndarray
    storage_norms: np.ndarray
    input_records: dict[str, dict[str, Any]]

    def close(self) -> None:
        _close_numpy_mmap(self.float64_vectors)
        _close_numpy_mmap(self.float32_vectors)


def _close_numpy_mmap(array: np.ndarray | None) -> None:
    """Release the file handle owned by an ``np.load(..., mmap_mode=...)`` result."""

    if array is None:
        return
    mmap_handle = getattr(array, "_mmap", None)
    close = getattr(mmap_handle, "close", None)
    if callable(close):
        close()


def _validate_config(config: dict[str, Any]) -> None:
    require(config.get("schemaVersion") == "feelm-similar-movies/1", "unexpected similar config schema")
    require(int(config.get("dimensions", -1)) == DIMENSIONS, "similar config dimensions must be 131")
    require(config.get("sourcePrecision") == "float64", "source precision must be float64")
    require(config.get("databasePrecision") == "float32", "database precision must be float32")
    require(config.get("metric") == "cosine_on_database_vectors", "unexpected source metric contract")
    require(config.get("requireSubSupported") is True, "subSupported must be required")
    require(config.get("requireTopSupported") is False, "topSupported must not be required")
    require(config.get("restrictToTasteOrChildGroup") is False, "candidate axis must not be group restricted")
    require(float(config.get("minimumSimilarityExclusive", math.nan)) == 0.0, "minimum score must be exclusive zero")
    require(int(config.get("sharedCacheCandidateLimit", -1)) == MAX_REFERENCE_NEIGHBORS, "reference Top50 is required")
    require(
        math.isclose(
            float(config.get("sourceToDatabaseScoreTolerance", math.nan)),
            SOURCE_PRECISION_TOLERANCE,
            rel_tol=0.0,
            abs_tol=0.0,
        ),
        "source precision tolerance must be 1e-6",
    )
    audit = config.get("retrievalAudit")
    require(isinstance(audit, dict), "retrievalAudit config is required")
    require(int(audit.get("maximumQuerySample", -1)) == MAX_QUERY_COUNT, "maximum query sample must be 200")
    require(int(audit.get("seed", -1)) == 339, "query seed must be 339")
    require(int(audit.get("exactNeighborCutoff", -1)) == EXACT_AUDIT_CUTOFF, "exact cutoff must be 10")
    require(audit.get("boundaryTiesAccepted") is True, "boundary ties must be accepted")
    require(
        int(audit.get("maximumManualReviewSources", -1)) == MAX_MANUAL_REVIEW_COUNT,
        "manual-review cap must be 30",
    )
    require(audit.get("servingQualityAlreadyValidated") is False, "serving quality must remain unvalidated")


def load_and_validate_inputs(
    *,
    staging_manifest_path: Path,
    movie_index_path: Path,
    reference_catalog_path: Path,
    float32_vectors_path: Path,
    float64_vectors_path: Path,
    similar_vector_export_path: Path,
    similar_config_path: Path,
) -> ValidatedInputs:
    paths = {
        "stagingManifest": staging_manifest_path,
        "movieIndex": movie_index_path,
        "referenceCatalog": reference_catalog_path,
        "float32Vectors": float32_vectors_path,
        "float64Vectors": float64_vectors_path,
        "similarVectorExport": similar_vector_export_path,
        "similarConfig": similar_config_path,
    }
    for label, path in paths.items():
        _require_file(path, label)

    manifest = read_json(staging_manifest_path)
    similar_export = read_json(similar_vector_export_path)
    config = read_json(similar_config_path)
    _validate_config(config)

    require(manifest.get("schemaVersion") == "feelm-gkt-staging-bundle/1", "unexpected staging manifest schema")
    require(manifest.get("status") == "STAGING_ID_MAPPING_REQUIRED", "unexpected staging status")
    require(manifest.get("readyForServing") is False, "staging input must not claim serving readiness")
    require(manifest.get("pairwiseNeighborsGenerated") is False, "staging input must not contain pairwise neighbors")
    require(manifest.get("similarityStatus") == "SIMILARITY_NOT_READY", "staging similarity must remain not ready")
    row_schema = manifest.get("logicalRowSchema")
    require(isinstance(row_schema, dict), "logical row schema is required")
    require(int(row_schema.get("dimensions", -1)) == DIMENSIONS, "staging dimensions must be 131")
    require(row_schema.get("precision") == "float32", "staging vector precision must be float32")
    require(row_schema.get("vectorJoin") == "index.rowIndex == vector row", "unexpected row join contract")

    require(similar_export.get("schemaVersion") == "feelm-similar-vector-export/1", "unexpected similar export schema")
    require(similar_export.get("status") == "SIMILARITY_NOT_READY", "similar export must not claim readiness")
    require(
        similar_export.get("stagingVersion") == manifest.get("stagingVersion"),
        "similar export stagingVersion differs from manifest",
    )
    require(
        similar_export.get("sourceCatalogVersion") == manifest.get("sourceCatalogVersion"),
        "similar export sourceCatalogVersion differs from manifest",
    )
    require(similar_export.get("sourceVector") == "GROUPS.content_x", "similar export must use GROUPS.content_x")
    require(int(similar_export.get("dimensions", -1)) == DIMENSIONS, "similar export dimensions must be 131")
    require(similar_export.get("precision") == "float32", "similar export precision must be float32")
    require(similar_export.get("metric") == "cosine_on_database_vectors", "similar export metric mismatch")
    require(similar_export.get("requiresSubSupported") is True, "similar export must require subSupported")
    require(similar_export.get("requiresTopSupported") is False, "similar export must not require topSupported")
    require(similar_export.get("candidateEligibilityIncluded") is False, "candidate eligibility must be absent")
    require(
        similar_export.get("verifiedCurrentServiceIdMappingIncluded") is False,
        "verified current service IDs must remain absent",
    )
    require(similar_export.get("pairwiseNeighborsGenerated") is False, "similar export must not contain neighbors")
    require(similar_export.get("movieIndex") == "movie-index.parquet", "similar export movie-index name mismatch")
    require(
        similar_export.get("vectorMatrix") == "content-vectors.float32.npy",
        "similar export vector-matrix name mismatch",
    )
    require(similar_export.get("rowJoinKey") == "rowIndex", "similar export row join key mismatch")
    service_mapping = manifest.get("serviceIdMapping")
    require(isinstance(service_mapping, dict), "staging serviceIdMapping is required")
    require(service_mapping.get("status") == "NOT_APPLIED", "reference-only input must not claim service ID mapping")
    require(int(service_mapping.get("serviceIdMappedRows", -1)) == 0, "reference-only input must have zero mapped service IDs")

    input_records: dict[str, dict[str, Any]] = {"stagingManifest": file_record(staging_manifest_path)}
    input_records["movieIndex"] = _verify_file_record(
        movie_index_path, _artifact_record(manifest, "movie-index.parquet"), "movie index"
    )
    input_records["float32Vectors"] = _verify_file_record(
        float32_vectors_path,
        _artifact_record(manifest, "content-vectors.float32.npy"),
        "float32 vectors",
    )
    input_records["similarVectorExport"] = _verify_file_record(
        similar_vector_export_path,
        _artifact_record(manifest, "similar-vector-export.json"),
        "similar vector export",
    )
    input_records["referenceCatalog"] = _verify_file_record(
        reference_catalog_path,
        _source_record(manifest, "referenceCatalog"),
        "reference catalog",
    )
    input_records["float64Vectors"] = _verify_file_record(
        float64_vectors_path,
        _source_record(manifest, "referenceContentVectors"),
        "float64 source vectors",
    )
    input_records["similarConfig"] = file_record(similar_config_path)
    expected_config_hash = str(manifest.get("similarRecipeSha256", ""))
    require(expected_config_hash != "", "staging manifest has no similarRecipeSha256")
    require(
        input_records["similarConfig"]["sha256"] == expected_config_hash,
        "similar config SHA-256 does not match the reviewed staging recipe",
    )
    reviewed = manifest.get("reviewedFingerprint")
    require(isinstance(reviewed, dict), "reviewedFingerprint is required")
    require(
        reviewed.get("similarRecipeSha256") == expected_config_hash,
        "reviewed fingerprint does not match similar config",
    )

    required_index_columns = {
        "rowIndex",
        "referenceServiceMovieId",
        "tmdbId",
        "subSupported",
    }
    index = pd.read_parquet(movie_index_path)
    require(required_index_columns.issubset(index.columns), "movie index is missing required columns")

    required_catalog_columns = {
        "service_movie_id",
        "tmdb_id",
        "title",
        "original_title",
        "genre_ids",
        "keyword_ids",
        "overview",
        "production_country_codes",
        "origin_country_codes",
        "release_year",
    }
    catalog = pd.read_parquet(reference_catalog_path)
    require(required_catalog_columns.issubset(catalog.columns), "reference catalog is missing required columns")

    float32_vectors: np.ndarray | None = None
    float64_vectors: np.ndarray | None = None
    try:
        float32_vectors = np.load(float32_vectors_path, mmap_mode="r", allow_pickle=False)
        float64_vectors = np.load(float64_vectors_path, mmap_mode="r", allow_pickle=False)
        require(float32_vectors.ndim == 2, "float32 vector matrix must be two-dimensional")
        require(float64_vectors.ndim == 2, "float64 vector matrix must be two-dimensional")
        require(float32_vectors.dtype == np.float32, "database vector export must be float32")
        require(float64_vectors.dtype == np.float64, "source vector matrix must be float64")
        require(float32_vectors.shape[1] == DIMENSIONS, "float32 vector width must be 131")
        require(float64_vectors.shape[1] == DIMENSIONS, "float64 vector width must be 131")

        row_count = len(index)
        require(len(catalog) == row_count, "catalog and movie index row counts differ")
        require(float32_vectors.shape[0] == row_count, "float32 matrix row count differs from index")
        require(float64_vectors.shape[0] == row_count, "float64 matrix row count differs from index")
        counts = manifest.get("rowCounts")
        require(isinstance(counts, dict), "staging rowCounts is required")
        require(int(counts.get("rows", -1)) == row_count, "staging row count mismatch")
        require(int(counts.get("dimensions", -1)) == DIMENSIONS, "staging row-count dimensions mismatch")

        row_indices = _integer_array(index["rowIndex"], "rowIndex")
        require(
            np.array_equal(row_indices, np.arange(row_count, dtype=np.int64)),
            "rowIndex must be contiguous and ordered",
        )
        reference_ids = _integer_array(index["referenceServiceMovieId"], "referenceServiceMovieId", positive=True)
        tmdb_ids = _integer_array(index["tmdbId"], "tmdbId", positive=True)
        require(len(np.unique(reference_ids)) == row_count, "referenceServiceMovieId contains duplicates")
        require(len(np.unique(tmdb_ids)) == row_count, "tmdbId contains duplicates")

        catalog_reference_ids = _integer_array(catalog["service_movie_id"], "catalog.service_movie_id", positive=True)
        catalog_tmdb_ids = _integer_array(catalog["tmdb_id"], "catalog.tmdb_id", positive=True)
        require(
            np.array_equal(reference_ids, catalog_reference_ids),
            "catalog service_movie_id is not aligned with movie index",
        )
        require(np.array_equal(tmdb_ids, catalog_tmdb_ids), "catalog tmdb_id is not aligned with movie index")

        sub_supported = _strict_bool_array(index["subSupported"], "subSupported")
        require(
            int(np.count_nonzero(sub_supported)) == int(counts.get("subSupportedRows", -1)),
            "subSupported row count differs from staging manifest",
        )
        _finite_scan(float32_vectors, "float32 vectors")
        _finite_scan(float64_vectors, "float64 vectors")
        storage_norms = float32_storage_norms(float32_vectors)
        observed_storage_support = storage_norms > 1e-12
        require(
            np.array_equal(observed_storage_support, sub_supported),
            "subSupported mask does not match float32-storage vector support",
        )
        require(
            bool(np.all(storage_norms[sub_supported] > 0.0)),
            "every subSupported row must have a positive float32-storage norm",
        )
        require(
            bool(np.all(np.abs(storage_norms[sub_supported] - 1.0) <= SOURCE_PRECISION_TOLERANCE)),
            "subSupported float32-storage vectors must be unit-normalized within 1e-6",
        )
        source_support = np.empty(row_count, dtype=bool)
        for start in range(0, row_count, 8192):
            stop = min(start + 8192, row_count)
            source_block = np.asarray(float64_vectors[start:stop], dtype=np.float64)
            source_squared = np.sum(source_block * source_block, axis=1, dtype=np.float64)
            require(bool(np.all(np.isfinite(source_squared))), "float64 source vector norm overflowed")
            source_support[start:stop] = source_squared > 1e-12
        require(
            np.array_equal(source_support, sub_supported),
            "subSupported mask does not match float64 source vector support",
        )

        return ValidatedInputs(
            staging_manifest=manifest,
            similar_export=similar_export,
            config=config,
            index=index,
            catalog=catalog,
            float32_vectors=float32_vectors,
            float64_vectors=float64_vectors,
            storage_norms=storage_norms,
            input_records=input_records,
        )
    except BaseException:
        _close_numpy_mmap(float64_vectors)
        _close_numpy_mmap(float32_vectors)
        raise


def _sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, float) and math.isnan(value):
        return []
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        return [part.strip() for part in stripped.split("|") if part.strip()]
    return [value]


def _country_codes(row: pd.Series) -> list[str]:
    values = _sequence(row.get("production_country_codes")) + _sequence(row.get("origin_country_codes"))
    return sorted({str(value).strip().upper() for value in values if str(value).strip()})


def _valid_year(value: Any) -> int | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric != math.floor(numeric):
        return None
    year = int(numeric)
    if year < 1800 or year > 2200:
        return None
    return year


def content_support_stratum(vector: np.ndarray) -> str:
    require(vector.ndim == 1 and vector.shape[0] == DIMENSIONS, "content vector must have 131 coordinates")
    genre_supported = bool(np.any(vector[:GENRE_DIMENSIONS] != np.float32(0.0)))
    other_start = GENRE_DIMENSIONS
    other_supported = bool(np.any(vector[other_start:] != np.float32(0.0)))
    if not genre_supported:
        return "GENRE_MISSING"
    if other_supported:
        return "COMPLEX"
    return "GENRE_ONLY"


@dataclass(frozen=True)
class QuerySelection:
    rows: tuple[int, ...]
    records: tuple[dict[str, Any], ...]
    stratum_population: dict[str, int]
    stratum_sample: dict[str, int]
    missing_year_excluded: int
    missing_country_in_rest: int
    all_movie_guard_reduction: int


def select_query_rows(
    *,
    index: pd.DataFrame,
    catalog: pd.DataFrame,
    vectors: np.ndarray,
    query_limit: int,
    seed: int = 339,
) -> QuerySelection:
    require(seed == 339, "query selection seed is fixed at 339")
    require(1 <= query_limit <= MAX_QUERY_COUNT, "query limit must be between 1 and 200")
    require(len(index) == len(catalog) == vectors.shape[0], "query-selection inputs are not aligned")
    require(vectors.dtype == np.float32 and vectors.shape[1] == DIMENSIONS, "query vectors must be float32 x 131")

    supported = _strict_bool_array(index["subSupported"], "subSupported")
    reference_ids = _integer_array(index["referenceServiceMovieId"], "referenceServiceMovieId", positive=True)
    tmdb_ids = _integer_array(index["tmdbId"], "tmdbId", positive=True)
    candidate_count = int(np.count_nonzero(supported))
    maximum_without_all_movie_generation = max(0, candidate_count - 1)
    effective_limit = min(query_limit, maximum_without_all_movie_generation)
    all_movie_guard_reduction = max(0, min(query_limit, candidate_count) - effective_limit)

    keys = [f"{region}|{era}|{content}" for region in REGION_ORDER for era in ERA_ORDER for content in CONTENT_ORDER]
    buckets: dict[str, list[int]] = {key: [] for key in keys}
    metadata_by_row: dict[int, dict[str, Any]] = {}
    missing_year_excluded = 0
    missing_country_in_rest = 0

    for row_index in np.flatnonzero(supported):
        row = catalog.iloc[int(row_index)]
        year = _valid_year(row.get("release_year"))
        if year is None:
            missing_year_excluded += 1
            continue
        countries = _country_codes(row)
        region = "KOREA" if "KR" in countries else "REST"
        if region == "REST" and not countries:
            missing_country_in_rest += 1
        era = "2020_PLUS" if year >= 2020 else "PRE_2020"
        content = content_support_stratum(np.asarray(vectors[int(row_index)], dtype=np.float32))
        key = f"{region}|{era}|{content}"
        buckets[key].append(int(row_index))
        metadata_by_row[int(row_index)] = {
            "rowIndex": int(row_index),
            "referenceServiceMovieId": int(reference_ids[int(row_index)]),
            "tmdbId": int(tmdb_ids[int(row_index)]),
            "title": str(row.get("title") or ""),
            "originalTitle": str(row.get("original_title") or ""),
            "releaseYear": year,
            "countryCodes": countries,
            "regionStratum": region,
            "eraStratum": era,
            "contentStratum": content,
            "subSupported": True,
            "candidateEligibility": CANDIDATE_ELIGIBILITY,
            "kobisEvidence": KOBIS_EVIDENCE,
        }

    stratum_population = {key: len(buckets[key]) for key in keys}
    shuffled: dict[str, list[int]] = {}
    for ordinal, key in enumerate(keys):
        ordered = sorted(buckets[key], key=lambda row_index: (reference_ids[row_index], row_index))
        if ordered:
            rng = np.random.default_rng(np.random.SeedSequence([seed, ordinal]))
            permutation = rng.permutation(len(ordered))
            shuffled[key] = [ordered[int(position)] for position in permutation]
        else:
            shuffled[key] = []

    positions = {key: 0 for key in keys}
    selected: list[int] = []
    while len(selected) < effective_limit:
        made_progress = False
        for key in keys:
            position = positions[key]
            if position < len(shuffled[key]) and len(selected) < effective_limit:
                selected.append(shuffled[key][position])
                positions[key] = position + 1
                made_progress = True
        if not made_progress:
            break

    records: list[dict[str, Any]] = []
    sample_counts = {key: 0 for key in keys}
    for sample_order, row_index in enumerate(selected, start=1):
        record = dict(metadata_by_row[row_index])
        record["sampleOrder"] = sample_order
        key = f"{record['regionStratum']}|{record['eraStratum']}|{record['contentStratum']}"
        sample_counts[key] += 1
        records.append(record)

    require(len(selected) <= MAX_QUERY_COUNT, "query cap exceeded")
    require(len(set(selected)) == len(selected), "query selection contains duplicate rows")
    require(not (candidate_count > 0 and len(selected) == candidate_count), "all-movie neighbor generation is forbidden")
    return QuerySelection(
        rows=tuple(selected),
        records=tuple(records),
        stratum_population=stratum_population,
        stratum_sample=sample_counts,
        missing_year_excluded=missing_year_excluded,
        missing_country_in_rest=missing_country_in_rest,
        all_movie_guard_reduction=all_movie_guard_reduction,
    )


@dataclass(frozen=True)
class Neighbor:
    row_index: int
    reference_service_movie_id: int
    tmdb_id: int
    similarity: float


@dataclass(frozen=True)
class ExactQueryResult:
    query_row_index: int
    positive_candidate_count: int
    neighbors: tuple[Neighbor, ...]
    exact10_cutoff_similarity: float | None
    exact10_cutoff_tie_count: int
    exact10_boundary_set_count: int


def _scores_for_query_block(
    vectors: np.ndarray,
    storage_norms: np.ndarray,
    query_row: int,
    candidate_rows: np.ndarray,
) -> np.ndarray:
    query64 = np.asarray(vectors[query_row], dtype=np.float64)
    candidates64 = np.asarray(vectors[candidate_rows], dtype=np.float64)
    dots = np.sum(candidates64 * query64, axis=1, dtype=np.float64)
    denominator = storage_norms[candidate_rows] * storage_norms[query_row]
    scores = np.divide(
        dots,
        denominator,
        out=np.full(dots.shape, np.nan, dtype=np.float64),
        where=denominator > 0.0,
    )
    return np.clip(scores, -1.0, 1.0)


def exact_topk_blockwise(
    *,
    vectors: np.ndarray,
    reference_service_movie_ids: np.ndarray,
    tmdb_ids: np.ndarray,
    sub_supported: np.ndarray,
    query_rows: Sequence[int],
    top_k: int = MAX_REFERENCE_NEIGHBORS,
    candidate_block_size: int = 8192,
    query_batch_size: int = 8,
    precomputed_norms: np.ndarray | None = None,
) -> list[ExactQueryResult]:
    require(vectors.ndim == 2 and vectors.shape[1] == DIMENSIONS, "vectors must have shape N x 131")
    require(vectors.dtype == np.float32, "exact reference must use stored float32 coordinates")
    row_count = vectors.shape[0]
    reference_ids = np.asarray(reference_service_movie_ids, dtype=np.int64)
    tmdb = np.asarray(tmdb_ids, dtype=np.int64)
    supported = np.asarray(sub_supported, dtype=bool)
    require(reference_ids.shape == (row_count,), "reference ID shape mismatch")
    require(tmdb.shape == (row_count,), "TMDB ID shape mismatch")
    require(supported.shape == (row_count,), "subSupported shape mismatch")
    require(1 <= top_k <= MAX_REFERENCE_NEIGHBORS, "top_k must be between 1 and 50")
    require(candidate_block_size >= 1, "candidate block size must be positive")
    require(query_batch_size >= 1, "query batch size must be positive")
    require(len(query_rows) <= MAX_QUERY_COUNT, "query count exceeds 200")
    query_array = np.asarray(query_rows, dtype=np.int64)
    require(len(np.unique(query_array)) == len(query_array), "query rows contain duplicates")
    require(bool(np.all((query_array >= 0) & (query_array < row_count))), "query row is out of range")
    require(bool(np.all(supported[query_array])) if len(query_array) else True, "every query must be subSupported")

    candidate_rows = np.flatnonzero(supported).astype(np.int64)
    require(
        not (len(candidate_rows) > 0 and len(query_array) == len(candidate_rows) and set(query_array.tolist()) == set(candidate_rows.tolist())),
        "all-movie neighbor generation is forbidden",
    )
    norms = float32_storage_norms(vectors, candidate_block_size) if precomputed_norms is None else np.asarray(precomputed_norms)
    require(norms.shape == (row_count,), "precomputed norm shape mismatch")
    require(bool(np.all(np.isfinite(norms))), "norms contain non-finite values")
    require(bool(np.all(norms[candidate_rows] > 0.0)), "subSupported candidates must have positive norms")

    keep_count = max(top_k, EXACT_AUDIT_CUTOFF)
    results: list[ExactQueryResult] = []
    for query_start in range(0, len(query_array), query_batch_size):
        query_stop = min(query_start + query_batch_size, len(query_array))
        for query_row_value in query_array[query_start:query_stop]:
            query_row = int(query_row_value)
            query_reference_id = int(reference_ids[query_row])
            query_tmdb_id = int(tmdb[query_row])
            best_rows = np.empty(0, dtype=np.int64)
            best_scores = np.empty(0, dtype=np.float64)
            positive_count = 0

            for candidate_start in range(0, len(candidate_rows), candidate_block_size):
                block_rows = candidate_rows[candidate_start : candidate_start + candidate_block_size]
                scores = _scores_for_query_block(vectors, norms, query_row, block_rows)
                valid = (
                    np.isfinite(scores)
                    & (scores > 0.0)
                    & (reference_ids[block_rows] != query_reference_id)
                    & (tmdb[block_rows] != query_tmdb_id)
                )
                rows = block_rows[valid]
                valid_scores = scores[valid]
                positive_count += int(len(rows))
                if len(rows) == 0:
                    continue
                combined_rows = np.concatenate((best_rows, rows))
                combined_scores = np.concatenate((best_scores, valid_scores))
                ordering = np.lexsort((reference_ids[combined_rows], -combined_scores))
                selected = ordering[:keep_count]
                best_rows = combined_rows[selected]
                best_scores = combined_scores[selected]

            final_order = np.lexsort((reference_ids[best_rows], -best_scores))
            best_rows = best_rows[final_order]
            best_scores = best_scores[final_order]
            exact10_cutoff: float | None = None
            cutoff_tie_count = 0
            boundary_set_count = positive_count
            if positive_count >= EXACT_AUDIT_CUTOFF:
                exact10_cutoff = float(best_scores[EXACT_AUDIT_CUTOFF - 1])
                greater_count = 0
                for candidate_start in range(0, len(candidate_rows), candidate_block_size):
                    block_rows = candidate_rows[candidate_start : candidate_start + candidate_block_size]
                    scores = _scores_for_query_block(vectors, norms, query_row, block_rows)
                    valid = (
                        np.isfinite(scores)
                        & (scores > 0.0)
                        & (reference_ids[block_rows] != query_reference_id)
                        & (tmdb[block_rows] != query_tmdb_id)
                    )
                    valid_scores = scores[valid]
                    greater_count += int(np.count_nonzero(valid_scores > exact10_cutoff))
                    cutoff_tie_count += int(np.count_nonzero(valid_scores == exact10_cutoff))
                boundary_set_count = greater_count + cutoff_tie_count

            neighbors = tuple(
                Neighbor(
                    row_index=int(row_index),
                    reference_service_movie_id=int(reference_ids[row_index]),
                    tmdb_id=int(tmdb[row_index]),
                    similarity=float(score),
                )
                for row_index, score in zip(best_rows[:top_k], best_scores[:top_k], strict=True)
            )
            require(len(neighbors) <= top_k, "neighbor cap exceeded")
            results.append(
                ExactQueryResult(
                    query_row_index=query_row,
                    positive_candidate_count=positive_count,
                    neighbors=neighbors,
                    exact10_cutoff_similarity=exact10_cutoff,
                    exact10_cutoff_tie_count=cutoff_tie_count,
                    exact10_boundary_set_count=boundary_set_count,
                )
            )
    return results


def _source_precision_for_result(
    result: ExactQueryResult,
    source_vectors: np.ndarray,
) -> tuple[int, float | None]:
    if not result.neighbors:
        return 0, None
    query = np.asarray(source_vectors[result.query_row_index], dtype=np.float64)
    query_norm = math.sqrt(float(np.sum(query * query, dtype=np.float64)))
    require(math.isfinite(query_norm) and query_norm > 0.0, "source query vector has an invalid norm")
    maximum = 0.0
    compared = 0
    for neighbor in result.neighbors:
        candidate = np.asarray(source_vectors[neighbor.row_index], dtype=np.float64)
        candidate_norm = math.sqrt(float(np.sum(candidate * candidate, dtype=np.float64)))
        require(math.isfinite(candidate_norm) and candidate_norm > 0.0, "source candidate vector has an invalid norm")
        score = float(np.sum(query * candidate, dtype=np.float64) / (query_norm * candidate_norm))
        difference = abs(score - neighbor.similarity)
        require(math.isfinite(difference), "source precision comparison is non-finite")
        maximum = max(maximum, difference)
        compared += 1
    return compared, maximum


def _json_list(value: Any) -> str:
    return json.dumps(_sequence(value), ensure_ascii=False, separators=(",", ":"))


def _build_output_tables(
    *,
    selection: QuerySelection,
    results: Sequence[ExactQueryResult],
    catalog: pd.DataFrame,
    source_vectors: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    require(len(selection.rows) == len(results), "query selection/result count mismatch")
    record_by_row = {int(record["rowIndex"]): record for record in selection.records}
    query_frame = pd.DataFrame(list(selection.records), columns=QUERY_COLUMNS)
    top_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    compared_total = 0
    global_maximum: float | None = None

    for result in results:
        query = record_by_row[result.query_row_index]
        compared, maximum = _source_precision_for_result(result, source_vectors)
        compared_total += compared
        if maximum is not None:
            global_maximum = maximum if global_maximum is None else max(global_maximum, maximum)
        require(maximum is None or maximum <= SOURCE_PRECISION_TOLERANCE, "source float64 score audit exceeded 1e-6")

        for rank, neighbor in enumerate(result.neighbors, start=1):
            candidate = catalog.iloc[neighbor.row_index]
            top_rows.append(
                {
                    "querySampleOrder": int(query["sampleOrder"]),
                    "queryRowIndex": result.query_row_index,
                    "queryReferenceServiceMovieId": int(query["referenceServiceMovieId"]),
                    "queryTmdbId": int(query["tmdbId"]),
                    "rank": rank,
                    "candidateRowIndex": neighbor.row_index,
                    "candidateReferenceServiceMovieId": neighbor.reference_service_movie_id,
                    "candidateTmdbId": neighbor.tmdb_id,
                    "candidateTitle": str(candidate.get("title") or ""),
                    "candidateOriginalTitle": str(candidate.get("original_title") or ""),
                    "candidateReleaseYear": _valid_year(candidate.get("release_year")),
                    "candidateCountryCodes": _json_list(_country_codes(candidate)),
                    "candidateGenreIds": _json_list(candidate.get("genre_ids")),
                    "similarity": neighbor.similarity,
                    "candidateEligibility": CANDIDATE_ELIGIBILITY,
                }
            )

        denominator = min(EXACT_AUDIT_CUTOFF, result.positive_candidate_count)
        audit_rows.append(
            {
                "querySampleOrder": int(query["sampleOrder"]),
                "queryRowIndex": result.query_row_index,
                "queryReferenceServiceMovieId": int(query["referenceServiceMovieId"]),
                "queryTmdbId": int(query["tmdbId"]),
                "positiveCandidateCount": result.positive_candidate_count,
                "exactTop10Denominator": denominator,
                "exact10CutoffSimilarity": result.exact10_cutoff_similarity,
                "exact10CutoffTieCount": result.exact10_cutoff_tie_count,
                "exact10BoundarySetCount": result.exact10_boundary_set_count,
                "returnedTop50Count": len(result.neighbors),
                "shortageBelow10": result.positive_candidate_count < EXACT_AUDIT_CUTOFF,
                "sourcePrecisionComparedPairCount": compared,
                "sourcePrecisionMaximumAbsoluteDifference": maximum,
                "sourcePrecisionAuditOnlyNotPgvectorParity": True,
                "candidateEligibility": CANDIDATE_ELIGIBILITY,
            }
        )

    top_frame = pd.DataFrame(top_rows, columns=TOP50_COLUMNS)
    audit_frame = pd.DataFrame(audit_rows, columns=AUDIT_COLUMNS)
    source_summary = {
        "status": "PASS" if global_maximum is not None else "NOT_APPLICABLE_NO_POSITIVE_NEIGHBORS",
        "scope": "RETURNED_REFERENCE_TOP50_PAIRS",
        "comparedPairCount": compared_total,
        "maximumAbsoluteScoreDifference": global_maximum,
        "toleranceInclusive": SOURCE_PRECISION_TOLERANCE,
        "sourcePrecisionAuditOnly": True,
        "databaseParity": False,
        "pgvectorParity": PGVECTOR_PARITY,
    }
    return query_frame, top_frame, audit_frame, source_summary


def _metadata_lookup(catalog: pd.DataFrame, row_index: int) -> dict[str, Any]:
    row = catalog.iloc[row_index]
    return {
        "title": str(row.get("title") or ""),
        "originalTitle": str(row.get("original_title") or ""),
        "releaseYear": _valid_year(row.get("release_year")),
        "countries": ", ".join(_country_codes(row)) or "(missing)",
        "genres": ", ".join(str(value) for value in _sequence(row.get("genre_ids"))) or "(missing)",
    }


def render_manual_review_html(
    *,
    selection: QuerySelection,
    results: Sequence[ExactQueryResult],
    catalog: pd.DataFrame,
    maximum_queries: int,
) -> str:
    require(0 <= maximum_queries <= MAX_MANUAL_REVIEW_COUNT, "manual-review query cap must be 0..30")
    by_row = {result.query_row_index: result for result in results}
    selected_records = list(selection.records[:maximum_queries])
    parts = [
        "<!doctype html>",
        '<html lang="ko"><head><meta charset="utf-8">',
        "<title>FEELM similar-movie reference manual review</title>",
        "<style>body{font-family:sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem}",
        "table{border-collapse:collapse;width:100%;margin-bottom:2rem}th,td{border:1px solid #bbb;padding:.35rem;text-align:left}",
        "th{background:#eee}.warning{padding:1rem;background:#fff3cd}</style></head><body>",
        "<h1>비슷한 영화 exact reference 수동 검토</h1>",
        '<p class="warning"><strong>REFERENCE_ONLY</strong> · candidateEligibility=UNKNOWN · '
        "readyForServing=false · pgvectorParity=NOT_RUN · KOBIS evidence=UNAVAILABLE_NO_CONFIRMED_LINK</p>",
        f"<p>Metric: <code>{html.escape(METRIC_LABEL)}</code>. 이 문서는 최대 30개 표본의 메타데이터 검토용이며 서비스 품질 통과 증거가 아니다.</p>",
    ]
    for query in selected_records:
        row_index = int(query["rowIndex"])
        result = by_row[row_index]
        title = html.escape(str(query["title"]))
        parts.append(
            f"<h2>{int(query['sampleOrder'])}. {title} "
            f"(ref {int(query['referenceServiceMovieId'])}, TMDB {int(query['tmdbId'])})</h2>"
        )
        parts.append(
            "<p>"
            + html.escape(
                f"{query['regionStratum']} / {query['eraStratum']} / {query['contentStratum']} / "
                f"year={query['releaseYear']} / countries={','.join(query['countryCodes']) or '(missing)'}"
            )
            + "</p>"
        )
        parts.append(
            "<p>검토: 주제·설정·장르 관련성 [ ] / 설명 오류 [ ] / 장르만 같고 내용 다름 [ ] / 프랜차이즈 집중 [ ] / 메모:</p>"
        )
        parts.append(
            "<table><thead><tr><th>rank</th><th>title</th><th>ref ID</th><th>TMDB</th><th>year</th>"
            "<th>countries</th><th>genres</th><th>similarity</th></tr></thead><tbody>"
        )
        for rank, neighbor in enumerate(result.neighbors[:EXACT_AUDIT_CUTOFF], start=1):
            meta = _metadata_lookup(catalog, neighbor.row_index)
            cells = (
                rank,
                meta["title"],
                neighbor.reference_service_movie_id,
                neighbor.tmdb_id,
                meta["releaseYear"] if meta["releaseYear"] is not None else "",
                meta["countries"],
                meta["genres"],
                f"{neighbor.similarity:.12f}",
            )
            parts.append("<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in cells) + "</tr>")
        if not result.neighbors:
            parts.append('<tr><td colspan="8">positive eligible reference neighbor 없음</td></tr>')
        parts.append("</tbody></table>")
    parts.append("</body></html>\n")
    return "".join(parts)


def _parquet_write(frame: pd.DataFrame, path: Path) -> None:
    frame.to_parquet(path, index=False, engine="pyarrow", compression="zstd")


def _thread_runtime_profile() -> dict[str, Any]:
    environment_names = (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
    )
    environment = {name: os.environ[name] for name in environment_names if name in os.environ}
    detected_pools: list[dict[str, Any]] = []
    detection = "threadpoolctl"
    try:
        from threadpoolctl import threadpool_info

        for pool in threadpool_info():
            detected_pools.append(
                {
                    "internalApi": pool.get("internal_api"),
                    "userApi": pool.get("user_api"),
                    "numThreads": pool.get("num_threads"),
                    "prefix": pool.get("prefix"),
                    "version": pool.get("version"),
                }
            )
    except (ImportError, OSError, RuntimeError) as error:
        detection = f"UNAVAILABLE:{type(error).__name__}"
    return {
        "logicalCpuCount": os.cpu_count(),
        "pythonActiveThreadCountAtMeasurement": threading.active_count(),
        "environment": environment,
        "detectedPools": detected_pools,
        "detection": detection,
    }


def _process_memory_profile() -> dict[str, Any]:
    current_rss: int | None = None
    peak_rss: int | None = None
    methods: list[str] = []
    try:
        import psutil

        memory = psutil.Process(os.getpid()).memory_info()
        current_rss = int(memory.rss)
        peak_value = getattr(memory, "peak_wset", None)
        if peak_value is not None:
            peak_rss = int(peak_value)
        methods.append("psutil")
    except (ImportError, OSError, RuntimeError, ValueError):
        pass

    if peak_rss is None:
        try:
            import resource

            maximum = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            peak_rss = maximum if sys.platform == "darwin" else maximum * 1024
            methods.append("resource.getrusage")
        except (ImportError, OSError, RuntimeError, ValueError):
            pass

    return {
        "rssBytesAtMeasurement": current_rss,
        "peakRssBytes": peak_rss,
        "peakRssScope": "PROCESS_LIFETIME_UP_TO_BENCHMARK_END",
        "methods": methods or ["UNAVAILABLE"],
    }


def _build_reference_oracle_from_validated(
    *,
    validated: ValidatedInputs,
    output_dir: Path,
    query_limit: int = MAX_QUERY_COUNT,
    manual_review_limit: int = MAX_MANUAL_REVIEW_COUNT,
    candidate_block_size: int = 8192,
    query_batch_size: int = 8,
) -> dict[str, Any]:
    require(1 <= query_limit <= MAX_QUERY_COUNT, "query limit must be between 1 and 200")
    require(0 <= manual_review_limit <= MAX_MANUAL_REVIEW_COUNT, "manual-review limit must be 0..30")
    require(candidate_block_size >= 1, "candidate block size must be positive")
    require(query_batch_size >= 1, "query batch size must be positive")
    output_dir = output_dir.resolve()
    require(not output_dir.exists(), f"immutable output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    selection = select_query_rows(
        index=validated.index,
        catalog=validated.catalog,
        vectors=validated.float32_vectors,
        query_limit=query_limit,
        seed=339,
    )

    reference_ids = _integer_array(
        validated.index["referenceServiceMovieId"], "referenceServiceMovieId", positive=True
    )
    tmdb_ids = _integer_array(validated.index["tmdbId"], "tmdbId", positive=True)
    sub_supported = _strict_bool_array(validated.index["subSupported"], "subSupported")
    start = time.perf_counter()
    results = exact_topk_blockwise(
        vectors=validated.float32_vectors,
        reference_service_movie_ids=reference_ids,
        tmdb_ids=tmdb_ids,
        sub_supported=sub_supported,
        query_rows=selection.rows,
        top_k=MAX_REFERENCE_NEIGHBORS,
        candidate_block_size=candidate_block_size,
        query_batch_size=query_batch_size,
        precomputed_norms=validated.storage_norms,
    )
    elapsed_seconds = time.perf_counter() - start
    query_frame, top_frame, audit_frame, source_precision = _build_output_tables(
        selection=selection,
        results=results,
        catalog=validated.catalog,
        source_vectors=validated.float64_vectors,
    )

    candidate_count = int(np.count_nonzero(sub_supported))
    first_pass_score_calculations = len(selection.rows) * candidate_count
    tie_rescan_query_count = sum(result.exact10_cutoff_similarity is not None for result in results)
    tie_rescan_score_calculations = tie_rescan_query_count * candidate_count
    score_calculations_total = first_pass_score_calculations + tie_rescan_score_calculations
    benchmark = {
        "schemaVersion": "feelm-similar-reference-benchmark/1",
        "status": STATUS,
        "readyForServing": False,
        "metric": METRIC_LABEL,
        "pgvectorParity": PGVECTOR_PARITY,
        "queryCount": len(selection.rows),
        "candidateCount": candidate_count,
        "uniqueQueryCandidatePairs": first_pass_score_calculations,
        "firstPassScoreCalculations": first_pass_score_calculations,
        "tieBoundaryRescanQueryCount": tie_rescan_query_count,
        "tieBoundaryRescanScoreCalculations": tie_rescan_score_calculations,
        "scoreCalculationsTotal": score_calculations_total,
        "pairEvaluations": score_calculations_total,
        "pairEvaluationsDefinition": "ACTUAL_DOT_PRODUCT_SCORE_CALCULATIONS_INCLUDING_TIE_BOUNDARY_RESCAN",
        "elapsedSeconds": elapsed_seconds,
        "pairsPerSecond": score_calculations_total / elapsed_seconds if elapsed_seconds > 0.0 else None,
        "timingScope": "EXACT_TOP50_FIRST_PASS_AND_EXACT10_TIE_BOUNDARY_RESCAN_ONLY",
        "timingExcludes": [
            "input_hash_and_schema_validation",
            "float32_norm_precomputation",
            "source_float64_precision_audit",
            "metadata_materialization",
            "artifact_serialization",
        ],
        "candidateBlockSize": candidate_block_size,
        "queryBatchSize": query_batch_size,
        "estimatedMaximumScoreBlockBytes": candidate_block_size * 8,
        "threads": _thread_runtime_profile(),
        "memory": _process_memory_profile(),
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "platform": platform.platform(),
        },
        "interpretation": "LOCAL_REFERENCE_RUNTIME_ONLY_NOT_EC2_OR_DATABASE_GUARANTEE",
    }

    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    published = False
    try:
        query_path = temp_dir / "query-sample.parquet"
        top50_path = temp_dir / "reference-top50.parquet"
        audit_path = temp_dir / "reference-top10-audit.parquet"
        benchmark_path = temp_dir / "benchmark.json"
        review_path = temp_dir / "manual-review.html"
        _parquet_write(query_frame, query_path)
        _parquet_write(top_frame, top50_path)
        _parquet_write(audit_frame, audit_path)
        write_json(benchmark_path, benchmark)
        review_path.write_text(
            render_manual_review_html(
                selection=selection,
                results=results,
                catalog=validated.catalog,
                maximum_queries=min(manual_review_limit, len(selection.rows)),
            ),
            encoding="utf-8",
            newline="\n",
        )

        output_names = (
            "query-sample.parquet",
            "reference-top50.parquet",
            "reference-top10-audit.parquet",
            "benchmark.json",
            "manual-review.html",
        )
        output_records = {name: file_record(temp_dir / name) for name in output_names}
        for record in output_records.values():
            record["path"] = Path(record["path"]).name

        manifest = {
            "schemaVersion": SCHEMA_VERSION,
            "status": STATUS,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "readyForServing": False,
            "candidateEligibility": CANDIDATE_ELIGIBILITY,
            "candidateEligibilityReason": "VERSIONED_CURRENT_SERVICE_ELIGIBILITY_NOT_INCLUDED_IN_STAGING_INPUT",
            "metric": METRIC_LABEL,
            "pgvectorParity": PGVECTOR_PARITY,
            "kobisEvidence": KOBIS_EVIDENCE,
            "sourcePrecisionAudit": source_precision,
            "inputs": validated.input_records,
            "selection": {
                "selectionBeforeSimilarityScoring": True,
                "seed": 339,
                "requestedMaximum": query_limit,
                "selectedQueryCount": len(selection.rows),
                "maximumAllowed": MAX_QUERY_COUNT,
                "queryUniverse": "SUB_SUPPORTED_REFERENCE_ROWS_WITH_VALID_RELEASE_YEAR",
                "strata": [
                    "KOREA_OR_REST",
                    "2020_PLUS_OR_PRE_2020",
                    "GENRE_ONLY_OR_COMPLEX_OR_GENRE_MISSING",
                ],
                "regionRule": "KOREA_IF_PRODUCTION_OR_ORIGIN_COUNTRY_CONTAINS_KR_ELSE_REST",
                "contentRule": "GENRE_BLOCK_ONLY_OR_GENRE_PLUS_ANY_OTHER_BLOCK_OR_ZERO_GENRE_BLOCK",
                "stratumPopulation": selection.stratum_population,
                "stratumSample": selection.stratum_sample,
                "missingReleaseYearExcludedCount": selection.missing_year_excluded,
                "missingCountryIncludedInRestCount": selection.missing_country_in_rest,
                "allMovieGuardReductionCount": selection.all_movie_guard_reduction,
            },
            "candidateUniverse": {
                "rule": "ALL_SUB_SUPPORTED_REFERENCE_AXIS",
                "candidateCount": candidate_count,
                "actualCurrentServiceEligibilityApplied": False,
                "topSupportedRequired": False,
                "tasteOrChildGroupRestricted": False,
            },
            "ranking": {
                "excludeSameReferenceServiceMovieId": True,
                "excludeSameTmdbId": True,
                "minimumSimilarityExclusive": 0.0,
                "order": ["similarity DESC", "referenceServiceMovieId ASC"],
                "referenceTopK": MAX_REFERENCE_NEIGHBORS,
                "exactAuditCutoff": EXACT_AUDIT_CUTOFF,
                "exact10BoundaryTieCountRecorded": True,
                "shortagePolicy": "RETURN_AVAILABLE_NO_UNRELATED_FILL",
            },
            "safety": {
                "queryCountAtMost200": len(selection.rows) <= MAX_QUERY_COUNT,
                "querySetIsStrictSubsetOfCandidateAxis": len(selection.rows) < candidate_count if candidate_count else True,
                "pairwiseMatrixGenerated": False,
                "allMovieNeighborsGenerated": False,
                "blockwiseExactCosine": True,
                "maximumRowsPerQueryWritten": MAX_REFERENCE_NEIGHBORS,
                "atomicPublish": True,
                "immutableNoOverwrite": True,
            },
            "outputs": output_records,
        }
        write_json(temp_dir / "manifest.json", manifest)
        require(not output_dir.exists(), f"immutable output appeared during build: {output_dir}")
        os.rename(temp_dir, output_dir)
        published = True
        return manifest
    finally:
        if not published and temp_dir.exists():
            shutil.rmtree(temp_dir)


def build_reference_oracle(
    *,
    staging_manifest_path: Path,
    movie_index_path: Path,
    reference_catalog_path: Path,
    float32_vectors_path: Path,
    float64_vectors_path: Path,
    similar_vector_export_path: Path,
    similar_config_path: Path,
    output_dir: Path,
    query_limit: int = MAX_QUERY_COUNT,
    manual_review_limit: int = MAX_MANUAL_REVIEW_COUNT,
    candidate_block_size: int = 8192,
    query_batch_size: int = 8,
) -> dict[str, Any]:
    validated = load_and_validate_inputs(
        staging_manifest_path=staging_manifest_path.resolve(),
        movie_index_path=movie_index_path.resolve(),
        reference_catalog_path=reference_catalog_path.resolve(),
        float32_vectors_path=float32_vectors_path.resolve(),
        float64_vectors_path=float64_vectors_path.resolve(),
        similar_vector_export_path=similar_vector_export_path.resolve(),
        similar_config_path=similar_config_path.resolve(),
    )
    try:
        return _build_reference_oracle_from_validated(
            validated=validated,
            output_dir=output_dir,
            query_limit=query_limit,
            manual_review_limit=manual_review_limit,
            candidate_block_size=candidate_block_size,
            query_batch_size=query_batch_size,
        )
    finally:
        validated.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-manifest", type=Path, required=True)
    parser.add_argument("--movie-index", type=Path, required=True)
    parser.add_argument("--reference-catalog", type=Path, required=True)
    parser.add_argument("--float32-vectors", type=Path, required=True)
    parser.add_argument("--float64-vectors", type=Path, required=True)
    parser.add_argument("--similar-vector-export", type=Path, required=True)
    parser.add_argument("--similar-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--query-limit", type=int, default=MAX_QUERY_COUNT)
    parser.add_argument("--manual-review-limit", type=int, default=MAX_MANUAL_REVIEW_COUNT)
    parser.add_argument("--candidate-block-size", type=int, default=8192)
    parser.add_argument("--query-batch-size", type=int, default=8)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = build_reference_oracle(
            staging_manifest_path=args.staging_manifest,
            movie_index_path=args.movie_index,
            reference_catalog_path=args.reference_catalog,
            float32_vectors_path=args.float32_vectors,
            float64_vectors_path=args.float64_vectors,
            similar_vector_export_path=args.similar_vector_export,
            similar_config_path=args.similar_config,
            output_dir=args.output_dir,
            query_limit=args.query_limit,
            manual_review_limit=args.manual_review_limit,
            candidate_block_size=args.candidate_block_size,
            query_batch_size=args.query_batch_size,
        )
    except (ContractError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
