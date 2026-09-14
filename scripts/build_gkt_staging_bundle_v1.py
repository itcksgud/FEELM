"""Build the reviewed, versioned GKT131 staging export for FEELM service v1.

The frozen research artifacts are inputs only.  This module never rewrites them,
never serializes the legacy bundle, and never creates an all-pairs neighbour
table.  A full export is deliberately gated by an independent review file whose
fingerprint covers this script and both approved recipes.

The reference ``service_movie_id`` axis is *not* the current service database
axis.  Until a verified server-side TMDB -> serviceMovieId crosswalk is applied,
the export remains STAGING and every final ``serviceIdMapped``/``groupMapped``
flag is false.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import pickle
import platform
import shutil
import sys
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.preprocessing import normalize as sparse_normalize


SCHEMA_VERSION = "feelm-gkt-staging-bundle/1"
INDEX_SCHEMA_VERSION = "feelm-gkt-staging-index/1"
REVIEW_SCHEMA_VERSION = "feelm-gkt-staging-pre-execution-review/1"
DEFAULT_GROUP_RECIPE = Path("docs/recommendation/service-v1-20260913/group-recipe.v1.json")
DEFAULT_SIMILAR_RECIPE = Path("docs/recommendation/service-v1-20260913/similar-movies.v1.json")
ALLOWED_INDEX_COLUMNS = (
    "rowIndex",
    "referenceServiceMovieId",
    "tmdbId",
    "tasteId",
    "childId",
    "groupId",
    "topSupported",
    "subSupported",
    "referenceIdMapped",
    "referenceGroupMapped",
    "serviceIdMapped",
    "groupMapped",
)
ALLOWED_GEOMETRY_ARRAYS = (
    "top_centers",
    "child_centers",
    "group_means",
    "offsets",
    "top_weights",
    "sub_weights",
)
ALLOWED_PREPROCESSOR_ARRAYS = (
    "genre_vocabulary",
    "keyword_vocabulary",
    "keyword_idf",
    "keyword_components",
    "text_idf",
    "text_components",
)
REQUIRED_SOURCE_NAMES = (
    "topAndPreprocessor",
    "childrenAndMeans",
    "referenceAssignments",
    "referenceCatalog",
    "referenceContentVectors",
)
FORBIDDEN_LEGACY_KEYS = {
    "child_centers",
    "sub_weights",
    "representatives",
    "policy",
    "predictor",
    "quality",
    "runtime_rules",
    "research_decision",
}


class ContractError(RuntimeError):
    """Raised when a frozen input, review, or output violates the contract."""


@dataclass(frozen=True)
class FrozenInputs:
    top_bundle: Mapping[str, Any]
    hierarchy: Mapping[str, Any]
    assignments: Mapping[str, np.ndarray]
    catalog_path: Path
    content_path: Path
    sources: Mapping[str, Mapping[str, Any]]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"cannot read JSON contract: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON contract: {path}: {exc}") from exc
    _require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def _json_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.write_text(encoded, encoding="utf-8")


def file_record(path: Path, *, logical_path: str | None = None) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    result: dict[str, Any] = {
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }
    if logical_path is not None:
        result["path"] = logical_path
    return result


def array_hash(value: np.ndarray) -> str:
    """Match the frozen DV2 array_hash encoding pinned by group-recipe.v1."""
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("utf-8"))
    digest.update(str(array.shape).encode("utf-8"))
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _compact_json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_inside_repo(repo_root: Path, path_value: str | Path) -> Path:
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ContractError(f"path escapes repository: {path_value}") from exc
    return resolved


def _runtime_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for package in ("numpy", "scipy", "scikit-learn", "pandas", "pyarrow"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "NOT_INSTALLED"
    return versions


def _close_memmap(value: Any) -> None:
    """Flush and release a NumPy memmap so Windows can move/delete its directory."""
    if isinstance(value, np.memmap):
        mapped_file = getattr(value, "_mmap", None)
        if mapped_file is not None and getattr(mapped_file, "closed", False):
            return
        value.flush()
        if mapped_file is not None:
            mapped_file.close()


def _release_memmaps(values: Iterable[Any]) -> None:
    for value in values:
        if value is not None:
            _close_memmap(value)


def _publish_staged_directory(
    temporary: Path, output: Path, open_memmaps: Iterable[Any] = ()
) -> None:
    """Release staged files before an atomic same-volume directory publish."""
    _release_memmaps(open_memmaps)
    _require(not output.exists(), "versioned output directory appeared before publish")
    os.replace(temporary, output)


def _cleanup_staged_directory(temporary: Path, open_memmaps: Iterable[Any] = ()) -> None:
    """Release staged files before fail-closed cleanup on Windows."""
    _release_memmaps(open_memmaps)
    if temporary.exists():
        shutil.rmtree(temporary)


def review_fingerprint(script_path: Path, group_recipe_path: Path, similar_recipe_path: Path) -> dict[str, str]:
    return {
        "scriptSha256": file_record(script_path)["sha256"],
        "groupRecipeSha256": file_record(group_recipe_path)["sha256"],
        "similarRecipeSha256": file_record(similar_recipe_path)["sha256"],
    }


def validate_review(review: Mapping[str, Any], fingerprint: Mapping[str, str]) -> None:
    _require(review.get("schemaVersion") == REVIEW_SCHEMA_VERSION, "independent review schema mismatch")
    _require(review.get("status") == "PASS", "independent pre-execution review has not passed")
    _require(review.get("scope") == "FULL_REFERENCE_EXPORT", "review does not cover the full reference export")
    reviewer = review.get("reviewer")
    _require(isinstance(reviewer, str) and bool(reviewer.strip()), "independent reviewer identity is missing")
    _require(review.get("reviewedFingerprint") == dict(fingerprint), "reviewed script/recipe fingerprint changed")


def normalize_positive_ids(value: Any) -> tuple[list[int], int, str | None]:
    """Apply the v1 ID-list contract without permissive integer coercion."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return [], 0, None
    if not isinstance(value, (list, tuple, np.ndarray)):
        return [], 0, "INVALID_TYPE"
    accepted: set[int] = set()
    invalid = 0
    for item in value:
        if isinstance(item, (bool, np.bool_)) or not isinstance(item, (int, np.integer)):
            invalid += 1
            continue
        integer = int(item)
        if integer <= 0:
            invalid += 1
            continue
        accepted.add(integer)
    return sorted(accepted), invalid, None


def normalize_overview(value: Any) -> tuple[str, str | None]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "", None
    if not isinstance(value, str):
        return "", "INVALID_TYPE"
    return value.strip(), None


def l2_rows(value: np.ndarray, threshold: float = 1e-12) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    _require(array.ndim == 2, "row normalization requires a two-dimensional array")
    norm = np.sqrt(np.sum(array * array, axis=1, keepdims=True))
    return np.divide(array, norm, out=np.zeros_like(array), where=norm > threshold)


def combine_blocks(blocks: Sequence[np.ndarray], weights: Sequence[float]) -> np.ndarray:
    _require(len(blocks) == len(weights), "block/weight length mismatch")
    _require(all(float(weight) >= 0 for weight in weights), "negative content weight")
    return l2_rows(np.concatenate([np.asarray(block, dtype=np.float64) * math.sqrt(float(weight))
                                   for block, weight in zip(blocks, weights)], axis=1))


def fixed_axis_nearest(value: np.ndarray, centers: np.ndarray, batch_size: int = 2048) -> tuple[np.ndarray, np.ndarray]:
    """Squared-distance argmin with fixed-axis sums and lowest-ID exact ties."""
    points = np.asarray(value, dtype=np.float64)
    frozen_centers = np.asarray(centers, dtype=np.float64)
    _require(points.ndim == 2 and frozen_centers.ndim == 2, "nearest inputs must be matrices")
    _require(points.shape[1] == frozen_centers.shape[1], "nearest dimension mismatch")
    _require(len(frozen_centers) > 0, "nearest requires at least one center")
    _require(batch_size > 0, "batch size must be positive")
    labels = np.empty(len(points), dtype=np.int32)
    losses = np.empty(len(points), dtype=np.float64)
    for start in range(0, len(points), batch_size):
        current = points[start:start + batch_size]
        distances = np.stack(
            [np.sum((current - center) ** 2, axis=1) for center in frozen_centers],
            axis=1,
        )
        labels[start:start + len(current)] = np.argmin(distances, axis=1)
        losses[start:start + len(current)] = np.min(distances, axis=1)
    return labels, losses


def _id_csr(rows: Sequence[Sequence[int]], vocabulary: Sequence[int], idf: np.ndarray | None = None) -> sparse.csr_matrix:
    lookup = {int(value): index for index, value in enumerate(vocabulary)}
    indices: list[int] = []
    pointer = [0]
    for row in rows:
        indices.extend(sorted({lookup[value] for value in row if value in lookup}))
        pointer.append(len(indices))
    result = sparse.csr_matrix(
        (
            np.ones(len(indices), dtype=np.float64),
            np.asarray(indices, dtype=np.int32),
            np.asarray(pointer, dtype=np.int64),
        ),
        shape=(len(rows), len(vocabulary)),
    )
    if idf is not None:
        result = result.multiply(np.asarray(idf, dtype=np.float64)).tocsr()
    return result


def transform_chunk(frame: pd.DataFrame, preprocessor: Mapping[str, Any]) -> tuple[list[np.ndarray], dict[str, int]]:
    """Reproduce the frozen G/K/T transform while applying the approved input guards."""
    normalized_genres: list[list[int]] = []
    normalized_keywords: list[list[int]] = []
    overviews: list[str] = []
    diagnostics = {
        "invalidGenreElements": 0,
        "invalidKeywordElements": 0,
        "invalidGenreFields": 0,
        "invalidKeywordFields": 0,
        "invalidOverviewFields": 0,
        "unknownGenreIds": 0,
        "unknownKeywordIds": 0,
    }
    genre_vocabulary = [int(value) for value in preprocessor["genres"]]
    keyword_vocabulary = [int(value) for value in preprocessor["keywords"]]
    genre_set = set(genre_vocabulary)
    keyword_set = set(keyword_vocabulary)
    for genre_value, keyword_value, overview_value in zip(
        frame["genre_ids"], frame["keyword_ids"], frame["overview"]
    ):
        genres, invalid_genres, genre_reason = normalize_positive_ids(genre_value)
        keywords, invalid_keywords, keyword_reason = normalize_positive_ids(keyword_value)
        overview, overview_reason = normalize_overview(overview_value)
        diagnostics["invalidGenreElements"] += invalid_genres
        diagnostics["invalidKeywordElements"] += invalid_keywords
        diagnostics["invalidGenreFields"] += int(genre_reason is not None)
        diagnostics["invalidKeywordFields"] += int(keyword_reason is not None)
        diagnostics["invalidOverviewFields"] += int(overview_reason is not None)
        diagnostics["unknownGenreIds"] += sum(value not in genre_set for value in genres)
        diagnostics["unknownKeywordIds"] += sum(value not in keyword_set for value in keywords)
        normalized_genres.append(genres)
        normalized_keywords.append(keywords)
        overviews.append(overview)

    genre = l2_rows(_id_csr(normalized_genres, genre_vocabulary).toarray())
    keyword_sparse = _id_csr(normalized_keywords, keyword_vocabulary, preprocessor["keyword_idf"])
    keyword_sparse = sparse_normalize(keyword_sparse, copy=False)
    keyword = l2_rows(keyword_sparse @ np.asarray(preprocessor["keyword_components"], dtype=np.float64).T)
    text_sparse = preprocessor["text_vectorizer"].transform(overviews)
    text = l2_rows(text_sparse @ np.asarray(preprocessor["text_components"], dtype=np.float64).T)
    return [genre, keyword, text], diagnostics


def assign_chunk(
    top_vectors: np.ndarray,
    content_vectors: np.ndarray,
    top_centers: np.ndarray,
    child_centers: np.ndarray,
    *,
    batch_size: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    taste, _ = fixed_axis_nearest(top_vectors, top_centers, batch_size=batch_size)
    child = np.empty(len(taste), dtype=np.int32)
    for taste_id in range(len(top_centers)):
        indices = np.flatnonzero(taste == taste_id)
        if len(indices):
            child[indices] = fixed_axis_nearest(
                content_vectors[indices], child_centers[taste_id], batch_size=batch_size
            )[0]
    per_top = child_centers.shape[1]
    group = taste.astype(np.int32) * np.int32(per_top) + child
    return taste, child, group


def staging_index(
    reference_service_ids: np.ndarray,
    tmdb_ids: np.ndarray,
    taste_ids: np.ndarray,
    child_ids: np.ndarray,
    group_ids: np.ndarray,
    top_supported: np.ndarray,
    sub_supported: np.ndarray,
) -> pd.DataFrame:
    count = len(reference_service_ids)
    arrays = (tmdb_ids, taste_ids, child_ids, group_ids, top_supported, sub_supported)
    _require(all(len(value) == count for value in arrays), "staging index axis length mismatch")
    reference_id_mapped = (
        (np.asarray(reference_service_ids) > 0)
        & (np.asarray(tmdb_ids) > 0)
    )
    result = pd.DataFrame({
        "rowIndex": np.arange(count, dtype=np.int64),
        "referenceServiceMovieId": np.asarray(reference_service_ids, dtype=np.int64),
        "tmdbId": np.asarray(tmdb_ids, dtype=np.int64),
        "tasteId": np.asarray(taste_ids, dtype=np.int32),
        "childId": np.asarray(child_ids, dtype=np.int32),
        "groupId": np.asarray(group_ids, dtype=np.int32),
        "topSupported": np.asarray(top_supported, dtype=bool),
        "subSupported": np.asarray(sub_supported, dtype=bool),
        "referenceIdMapped": reference_id_mapped.astype(bool),
        "referenceGroupMapped": (reference_id_mapped & top_supported & sub_supported).astype(bool),
        "serviceIdMapped": np.zeros(count, dtype=bool),
        "groupMapped": np.zeros(count, dtype=bool),
    })
    _require(tuple(result.columns) == ALLOWED_INDEX_COLUMNS, "staging index allowlist changed")
    return result


def _source_path(repo_root: Path, recipe: Mapping[str, Any], name: str) -> Path:
    sources = recipe.get("sources")
    _require(isinstance(sources, dict) and name in sources, f"missing source recipe: {name}")
    return _resolve_inside_repo(repo_root, sources[name]["path"])


def _verify_sources(repo_root: Path, recipe: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    sources = recipe.get("sources")
    _require(isinstance(sources, dict), "group recipe sources must be an object")
    records: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for name, contract in sources.items():
        try:
            path = _resolve_inside_repo(repo_root, contract["path"])
            _require(path.is_file(), f"source file missing: {name}")
            actual = file_record(path, logical_path=Path(contract["path"]).as_posix())
            actual["expectedBytes"] = int(contract["bytes"])
            actual["expectedSha256"] = str(contract["sha256"])
            actual["verified"] = (
                actual["bytes"] == actual["expectedBytes"]
                and actual["sha256"] == actual["expectedSha256"]
            )
            if not actual["verified"]:
                errors.append(f"source hash/size mismatch: {name}")
            records[name] = actual
        except Exception as exc:  # report all source failures before stopping
            errors.append(f"{name}: {exc}")
    for name in REQUIRED_SOURCE_NAMES:
        if name not in sources:
            errors.append(f"required source missing from recipe: {name}")
    return records, errors


def _load_verified_pickle(path: Path, expected_sha256: str) -> Mapping[str, Any]:
    actual = file_record(path)
    _require(actual["sha256"] == expected_sha256, f"refusing unverified pickle: {path}")
    with path.open("rb") as stream:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            value = pickle.load(stream)
    _require(isinstance(value, dict), f"pickle root is not an object: {path}")
    return value


def _text_idf(vectorizer: Any) -> np.ndarray:
    _require(hasattr(vectorizer, "idf_"), "frozen text vectorizer has no idf_ array")
    return np.asarray(vectorizer.idf_, dtype=np.float64)


def _ordered_text_vocabulary(vectorizer: Any) -> list[str]:
    vocabulary = getattr(vectorizer, "vocabulary_", None)
    _require(isinstance(vocabulary, dict), "frozen text vocabulary is missing")
    _require(set(vocabulary.values()) == set(range(len(vocabulary))), "text vocabulary indices are not contiguous")
    ordered = [""] * len(vocabulary)
    for token, index in vocabulary.items():
        _require(isinstance(token, str), "text vocabulary contains a non-string token")
        ordered[int(index)] = token
    return ordered


def _validate_frozen_structure(recipe: Mapping[str, Any], frozen: FrozenInputs) -> list[str]:
    checks: list[str] = []
    top = frozen.top_bundle
    hierarchy = frozen.hierarchy
    extraction = recipe["sourceExtraction"]
    allow_top = set(extraction["topAndPreprocessor"]["allowKeys"])
    _require(allow_top == {"preprocessor", "top_centers", "top_weights", "top_names"},
             "top allowlist differs from approved recipe")
    _require(FORBIDDEN_LEGACY_KEYS == set(extraction["topAndPreprocessor"]["forbiddenKeys"]),
             "legacy forbidden-key set differs from approved recipe")
    _require(allow_top.issubset(top.keys()), "approved top/preprocessor keys are absent")
    checks.append("G01_TOP_ALLOWLIST_PRESENT")

    preprocessor = top["preprocessor"]
    required_preprocessor = {
        "genres", "keywords", "keyword_idf", "keyword_components", "text_vectorizer", "text_components"
    }
    _require(required_preprocessor.issubset(preprocessor), "frozen preprocessor keys are incomplete")
    geometry_recipe = recipe["geometry"]
    preprocessor_recipe = recipe["preprocessor"]
    top_centers = np.asarray(top["top_centers"])
    _require(list(top_centers.shape) == geometry_recipe["topCenters"]["shape"], "top center shape mismatch")
    _require(array_hash(top_centers) == geometry_recipe["topCenters"]["sha256"], "top center hash mismatch")
    _require(list(top["top_weights"]) == geometry_recipe["topWeights"], "top weights mismatch")
    _require(list(hierarchy["sub_weights"]) == geometry_recipe["subWeights"], "sub weights mismatch")
    _require(list(np.asarray(hierarchy["offsets"]).astype(int)) == geometry_recipe["offsets"], "offset mismatch")

    child = np.stack([np.asarray(value, dtype=np.float64) for value in hierarchy["centers"]])
    _require(child.shape == (8, 128, 131), "child-center geometry must be 8x128x131")
    for index, expected in enumerate(geometry_recipe["childCenters"]):
        _require(array_hash(child[index]) == expected["sha256"], f"child center hash mismatch: {index}")
    means = np.asarray(hierarchy["representatives"]["mean"], dtype=np.float64)
    _require(means.shape == (1024, 131), "group mean geometry must be 1024x131")
    _require(array_hash(means) == geometry_recipe["groupMeans"]["sha256"], "group mean hash mismatch")
    _require(hierarchy["geometry_version"] == recipe["sourceGeometryVersion"], "source geometry version mismatch")
    _require(hierarchy["space_hashes"] == geometry_recipe["sourceSpaceHashes"], "source space hash mismatch")
    checks.append("G01_GEOMETRY_ARRAYS_VERIFIED")

    expected_arrays = {
        "keyword_idf": preprocessor_recipe["keywordIdf"],
        "keyword_components": preprocessor_recipe["keywordComponents"],
        "text_components": preprocessor_recipe["textComponents"],
    }
    for key, expected in expected_arrays.items():
        value = np.asarray(preprocessor[key])
        _require(list(value.shape) == expected["shape"], f"{key} shape mismatch")
        _require(array_hash(value) == expected["sha256"], f"{key} hash mismatch")
    text_idf = _text_idf(preprocessor["text_vectorizer"])
    expected_text_idf = preprocessor_recipe["textIdf"]
    _require(list(text_idf.shape) == expected_text_idf["shape"], "text idf shape mismatch")
    _require(array_hash(text_idf) == expected_text_idf["sha256"], "text idf hash mismatch")
    genre_vocabulary = [int(value) for value in preprocessor["genres"]]
    keyword_vocabulary = [int(value) for value in preprocessor["keywords"]]
    _require(genre_vocabulary == preprocessor_recipe["genreVocabulary"], "genre vocabulary order mismatch")
    _require(_compact_json_hash(keyword_vocabulary) == preprocessor_recipe["keywordVocabularySha256"],
             "keyword vocabulary hash mismatch")
    text_vocabulary = getattr(preprocessor["text_vectorizer"], "vocabulary_")
    sorted_text_vocabulary = sorted((str(token), int(index)) for token, index in text_vocabulary.items())
    _require(_compact_json_hash(sorted_text_vocabulary) == preprocessor_recipe["textVocabularySha256"],
             "text vocabulary hash mismatch")
    checks.append("G01_PREPROCESSOR_ARRAYS_VERIFIED")

    assignments = frozen.assignments
    _require(set(assignments) == set(extraction["referenceAssignments"]["allowKeys"]),
             "reference assignment NPZ keys differ from allowlist")
    for key, expected in geometry_recipe["sourceAssignmentArrays"].items():
        value = np.asarray(assignments[key])
        _require(list(value.shape) == expected["shape"], f"assignment shape mismatch: {key}")
        _require(str(value.dtype) == expected["dtype"], f"assignment dtype mismatch: {key}")
        _require(array_hash(value) == expected["sha256"], f"assignment hash mismatch: {key}")
    checks.append("G01_ASSIGNMENT_ARRAYS_VERIFIED")

    content = np.load(frozen.content_path, mmap_mode="r", allow_pickle=False)
    _require(content.shape == (recipe["parity"]["sourceRows"], 131), "reference content shape mismatch")
    _require(str(content.dtype) == "float64", "reference content precision mismatch")
    catalog_schema = pd.read_parquet(
        frozen.catalog_path,
        columns=["service_movie_id", "tmdb_id", "genre_ids", "keyword_ids", "overview"],
    ).dtypes
    _require(len(catalog_schema) == 5, "reference catalog columns are incomplete")
    checks.append("G02_REFERENCE_INPUT_STRUCTURE_VERIFIED")
    return checks


def load_frozen_inputs(repo_root: Path, recipe: Mapping[str, Any]) -> FrozenInputs:
    source_records, errors = _verify_sources(repo_root, recipe)
    _require(not errors, "; ".join(errors))
    top_path = _source_path(repo_root, recipe, "topAndPreprocessor")
    hierarchy_path = _source_path(repo_root, recipe, "childrenAndMeans")
    assignments_path = _source_path(repo_root, recipe, "referenceAssignments")
    top = _load_verified_pickle(top_path, recipe["sources"]["topAndPreprocessor"]["sha256"])
    hierarchy = _load_verified_pickle(hierarchy_path, recipe["sources"]["childrenAndMeans"]["sha256"])
    with np.load(assignments_path, allow_pickle=False) as archive:
        assignments = {key: archive[key].copy() for key in archive.files}
    return FrozenInputs(
        top_bundle=top,
        hierarchy=hierarchy,
        assignments=assignments,
        catalog_path=_source_path(repo_root, recipe, "referenceCatalog"),
        content_path=_source_path(repo_root, recipe, "referenceContentVectors"),
        sources=source_records,
    )


def build_preflight_report(
    repo_root: Path,
    script_path: Path,
    group_recipe_path: Path,
    similar_recipe_path: Path,
) -> dict[str, Any]:
    group_recipe = _json_read(group_recipe_path)
    similar_recipe = _json_read(similar_recipe_path)
    source_records, errors = _verify_sources(repo_root, group_recipe)
    checks: list[str] = []
    if not errors:
        try:
            frozen = load_frozen_inputs(repo_root, group_recipe)
            checks = _validate_frozen_structure(group_recipe, frozen)
        except Exception as exc:
            errors.append(str(exc))
    expected_runtime = group_recipe["runtime"]
    actual_runtime = _runtime_versions()
    runtime_warnings = []
    package_key = {"python": "python", "numpy": "numpy", "scipy": "scipy", "sklearn": "scikit-learn"}
    for expected_key, actual_key in package_key.items():
        if str(expected_runtime[expected_key]) != str(actual_runtime[actual_key]):
            runtime_warnings.append(
                f"{expected_key}: source={expected_runtime[expected_key]}, builder={actual_runtime[actual_key]}"
            )
    fingerprint = review_fingerprint(script_path, group_recipe_path, similar_recipe_path)
    return {
        "schemaVersion": "feelm-gkt-staging-preflight/1",
        "createdAt": _utc_now(),
        "status": "BLOCKED" if errors else "AWAITING_INDEPENDENT_REVIEW",
        "exportExecuted": False,
        "fullPairwiseNeighborsGenerated": False,
        "reviewRequired": True,
        "reviewFingerprint": fingerprint,
        "requiredReviewTemplate": {
            "schemaVersion": REVIEW_SCHEMA_VERSION,
            "status": "PASS",
            "scope": "FULL_REFERENCE_EXPORT",
            "reviewer": "<independent task/session>",
            "reviewedAt": "<UTC timestamp>",
            "reviewedFingerprint": fingerprint,
            "findings": [],
        },
        "sourceFiles": source_records,
        "structureChecks": checks,
        "errors": errors,
        "runtime": {
            "source": expected_runtime,
            "builder": actual_runtime,
            "warnings": runtime_warnings,
            "policy": "different runtime is allowed only if full vector and assignment parity passes",
        },
        "contractCoverageBeforeServer": {
            "G01": "PREFLIGHT_IMPLEMENTED",
            "G02": "FULL_EXPORT_NOT_RUN",
            "G03": "SYNTHETIC_TEST",
            "G04": "SYNTHETIC_TEST",
            "G05": "SYNTHETIC_TEST_AND_RAW_MEAN_HASH",
            "G06": "BLOCKED_UNTIL_VERIFIED_CURRENT_SERVICE_ID_MAPPING",
            "S01": "VECTOR_EXPORT_IMPLEMENTED_BUT_NOT_RUN",
            "S02": "SERVER_PGVECTOR_NOT_RUN",
            "S03": "SERVER_API_NOT_RUN",
            "S04": "SERVER_FAILURE_BUDGET_NOT_RUN",
            "S05": "SERVER_VERSION_SWITCH_NOT_RUN",
            "S06": "SERVER_REASON_EVENT_NOT_RUN",
            "S07": "SEARCH_AND_HUMAN_QUALITY_NOT_RUN",
            "S08": "SERVER_COST_PORTABILITY_NOT_RUN",
        },
        "similarMovies": {
            "recipeSchemaVersion": similar_recipe.get("schemaVersion"),
            "sourceVector": similar_recipe.get("sourceVector"),
            "vectorsWillReuseGroupExport": True,
            "candidateEligibilityIncluded": False,
            "pairwiseNeighborMaterialization": False,
            "status": "SIMILARITY_NOT_READY",
        },
    }


def _export_preprocessor(destination: Path, recipe: Mapping[str, Any], preprocessor: Mapping[str, Any]) -> None:
    arrays = {
        "genre_vocabulary": np.asarray(preprocessor["genres"], dtype=np.int64),
        "keyword_vocabulary": np.asarray(preprocessor["keywords"], dtype=np.int64),
        "keyword_idf": np.asarray(preprocessor["keyword_idf"], dtype=np.float64),
        "keyword_components": np.asarray(preprocessor["keyword_components"], dtype=np.float64),
        "text_idf": _text_idf(preprocessor["text_vectorizer"]),
        "text_components": np.asarray(preprocessor["text_components"], dtype=np.float64),
    }
    _require(tuple(arrays) == ALLOWED_PREPROCESSOR_ARRAYS, "preprocessor export allowlist changed")
    np.savez(destination / "preprocessor-arrays.npz", **arrays)
    _json_write(destination / "text-vocabulary.json", {
        "schemaVersion": "feelm-gkt-text-vocabulary/1",
        "axis": _ordered_text_vocabulary(preprocessor["text_vectorizer"]),
    })
    _json_write(destination / "transform-contract.json", {
        "schemaVersion": "feelm-gkt-transform/1",
        "fitAllowed": False,
        "inputFields": recipe["input"]["fields"],
        "textVectorizerParams": recipe["preprocessor"]["textVectorizerParams"],
        "numeric": recipe["numeric"],
        "contentWeights": recipe["geometry"]["contentWeights"],
        "topWeights": recipe["geometry"]["topWeights"],
    })


def _export_geometry(destination: Path, recipe: Mapping[str, Any], frozen: FrozenInputs) -> None:
    arrays = {
        "top_centers": np.asarray(frozen.top_bundle["top_centers"], dtype=np.float64),
        "child_centers": np.stack([np.asarray(value, dtype=np.float64) for value in frozen.hierarchy["centers"]]),
        "group_means": np.asarray(frozen.hierarchy["representatives"]["mean"], dtype=np.float64),
        "offsets": np.asarray(frozen.hierarchy["offsets"], dtype=np.int32),
        "top_weights": np.asarray(frozen.top_bundle["top_weights"], dtype=np.float64),
        "sub_weights": np.asarray(frozen.hierarchy["sub_weights"], dtype=np.float64),
    }
    _require(tuple(arrays) == ALLOWED_GEOMETRY_ARRAYS, "geometry export allowlist changed")
    _require(array_hash(arrays["group_means"]) == recipe["geometry"]["groupMeans"]["sha256"],
             "raw group mean changed before export")
    np.savez(destination / "geometry-arrays.npz", **arrays)
    _json_write(destination / "top-names.json", {
        "schemaVersion": "feelm-gkt-top-names/1",
        "topNames": recipe["geometry"]["sourceTopNames"],
    })


def _artifact_inventory(directory: Path, excluded: Iterable[str] = ()) -> dict[str, dict[str, Any]]:
    excluded_set = set(excluded)
    inventory: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            if relative not in excluded_set:
                inventory[relative] = file_record(path, logical_path=relative)
    return inventory


def _validate_written_bundle(directory: Path, expected_rows: int) -> dict[str, Any]:
    index = pd.read_parquet(directory / "movie-index.parquet")
    _require(tuple(index.columns) == ALLOWED_INDEX_COLUMNS, "reloaded index allowlist mismatch")
    _require(len(index) == expected_rows, "reloaded index row count mismatch")
    _require(np.array_equal(index["rowIndex"].to_numpy(), np.arange(expected_rows, dtype=np.int64)),
             "row index is not contiguous")
    _require(not bool(index["serviceIdMapped"].any()), "staging export claims current service mapping")
    _require(not bool(index["groupMapped"].any()), "staging export claims final group mapping")
    _require(index["referenceServiceMovieId"].is_unique, "reference service ID is not unique")
    _require(index["tmdbId"].is_unique, "TMDB ID is not unique")

    vectors = np.load(directory / "content-vectors.float32.npy", mmap_mode="r", allow_pickle=False)
    try:
        _require(vectors.shape == (expected_rows, 131), "reloaded vector shape mismatch")
        _require(str(vectors.dtype) == "float32", "reloaded vector precision mismatch")
        supported = index["subSupported"].to_numpy(bool)
        maximum_norm_error = 0.0
        for start in range(0, expected_rows, 8192):
            stop = min(start + 8192, expected_rows)
            chunk = np.asarray(vectors[start:stop], dtype=np.float64)
            _require(bool(np.isfinite(chunk).all()), f"reloaded vectors contain non-finite values at {start}:{stop}")
            norms = np.sqrt(np.sum(chunk * chunk, axis=1))
            supported_chunk = supported[start:stop]
            _require(bool(np.all(norms[~supported_chunk] <= 1e-12)),
                     f"unsupported exported vector is nonzero at {start}:{stop}")
            if bool(supported_chunk.any()):
                maximum_norm_error = max(
                    maximum_norm_error,
                    float(np.max(np.abs(norms[supported_chunk] - 1.0))),
                )
    finally:
        _close_memmap(vectors)
    _require(maximum_norm_error <= 1e-6, "float32 exported vectors are not normalized within tolerance")

    with np.load(directory / "geometry-arrays.npz", allow_pickle=False) as geometry:
        _require(tuple(geometry.files) == ALLOWED_GEOMETRY_ARRAYS, "reloaded geometry allowlist mismatch")
        _require(geometry["top_centers"].shape == (8, 131), "reloaded top geometry mismatch")
        _require(geometry["child_centers"].shape == (8, 128, 131), "reloaded child geometry mismatch")
        _require(geometry["group_means"].shape == (1024, 131), "reloaded group means mismatch")
    with np.load(directory / "preprocessor-arrays.npz", allow_pickle=False) as preprocessor:
        _require(tuple(preprocessor.files) == ALLOWED_PREPROCESSOR_ARRAYS,
                 "reloaded preprocessor allowlist mismatch")
    _require(not list(directory.rglob("*.pkl")), "legacy pickle leaked into staging export")
    _require(not any("neighbor" in path.name.lower() for path in directory.rglob("*")),
             "pairwise neighbour artifact unexpectedly exists")
    return {
        "rows": expected_rows,
        "dimensions": 131,
        "subSupportedRows": int(supported.sum()),
        "referenceGroupMappedRows": int(index["referenceGroupMapped"].sum()),
        "serviceGroupMappedRows": int(index["groupMapped"].sum()),
        "maximumFloat32NormError": maximum_norm_error,
    }


def _pair_score_cast_audit(source: np.ndarray, exported: np.ndarray, sample_size: int = 512) -> dict[str, Any]:
    """Bounded deterministic cast audit; this is S01 evidence, not S07 retrieval quality."""
    supported_parts: list[np.ndarray] = []
    for start in range(0, len(source), 8192):
        stop = min(start + 8192, len(source))
        chunk = np.asarray(source[start:stop], dtype=np.float64)
        local = np.flatnonzero(np.sum(chunk * chunk, axis=1) > 1e-12)
        if len(local):
            supported_parts.append(local + start)
    supported = np.concatenate(supported_parts) if supported_parts else np.empty(0, dtype=np.int64)
    if len(supported) < 2:
        return {"samplePairs": 0, "maximumCosineDifference": 0.0}
    selected = supported[np.linspace(0, len(supported) - 1, min(sample_size, len(supported)), dtype=int)]
    left = selected
    right = np.roll(selected, 1)
    source_scores = np.sum(source[left] * source[right], axis=1)
    exported_left = np.asarray(exported[left], dtype=np.float64)
    exported_right = np.asarray(exported[right], dtype=np.float64)
    exported_left_norm = np.sqrt(np.sum(exported_left * exported_left, axis=1))
    exported_right_norm = np.sqrt(np.sum(exported_right * exported_right, axis=1))
    exported_scores = np.sum(exported_left * exported_right, axis=1) / (
        exported_left_norm * exported_right_norm
    )
    difference = np.abs(source_scores - exported_scores)
    return {
        "samplePairs": int(len(selected)),
        "sampleRule": "evenly spaced supported rows, paired with one-position rotation",
        "maximumCosineDifference": float(np.max(difference)),
    }


def export_bundle(
    *,
    repo_root: Path,
    script_path: Path,
    group_recipe_path: Path,
    similar_recipe_path: Path,
    review_path: Path,
    output_dir: Path,
    staging_version: str,
    source_catalog_version: str,
    batch_size: int,
) -> dict[str, Any]:
    _require(bool(staging_version.strip()), "staging version is required")
    _require(bool(source_catalog_version.strip()), "source catalog version is required")
    _require(batch_size > 0, "batch size must be positive")
    allowed_output_root = (repo_root / "outputs" / "recommendation-evidence").resolve()
    resolved_output = output_dir.resolve()
    try:
        resolved_output.relative_to(allowed_output_root)
    except ValueError as exc:
        raise ContractError("output directory must be under outputs/recommendation-evidence") from exc
    _require(not resolved_output.exists(), "versioned output directory already exists")

    group_recipe = _json_read(group_recipe_path)
    similar_recipe = _json_read(similar_recipe_path)
    fingerprint = review_fingerprint(script_path, group_recipe_path, similar_recipe_path)
    validate_review(_json_read(review_path), fingerprint)
    frozen = load_frozen_inputs(repo_root, group_recipe)
    structure_checks = _validate_frozen_structure(group_recipe, frozen)

    temporary = resolved_output.parent / f".{resolved_output.name}.tmp-{uuid.uuid4().hex}"
    _require(not temporary.exists(), "temporary export directory collision")
    temporary.mkdir(parents=True, exist_ok=False)
    source_content: np.ndarray | None = None
    exported_vectors: np.ndarray | None = None
    audit_vectors: np.ndarray | None = None
    try:
        catalog = pd.read_parquet(
            frozen.catalog_path,
            columns=["service_movie_id", "tmdb_id", "genre_ids", "keyword_ids", "overview"],
        )
        count = int(group_recipe["parity"]["sourceRows"])
        _require(len(catalog) == count, "catalog row count differs from the frozen contract")
        _require(catalog["service_movie_id"].is_unique, "reference service ID is not unique")
        _require(catalog["tmdb_id"].is_unique, "reference TMDB ID is not unique")
        _require(bool((catalog["service_movie_id"] > 0).all()), "reference service ID is nonpositive")
        _require(bool((catalog["tmdb_id"] > 0).all()), "reference TMDB ID is nonpositive")

        assignments = frozen.assignments
        reference_ids = catalog["service_movie_id"].to_numpy(np.int64)
        tmdb_ids = catalog["tmdb_id"].to_numpy(np.int64)
        _require(np.array_equal(reference_ids, assignments["service_movie_id"]),
                 "catalog and assignment reference ID axes differ")
        source_content = np.load(frozen.content_path, mmap_mode="r", allow_pickle=False)
        _require(source_content.shape == (count, 131), "reference content vector shape mismatch")

        exported_vectors = np.lib.format.open_memmap(
            temporary / "content-vectors.float32.npy",
            mode="w+",
            dtype=np.float32,
            shape=(count, 131),
        )
        calculated_taste = np.empty(count, dtype=np.int32)
        calculated_child = np.empty(count, dtype=np.int32)
        calculated_group = np.empty(count, dtype=np.int32)
        top_supported = np.empty(count, dtype=bool)
        sub_supported = np.empty(count, dtype=bool)
        maximum_vector_difference = 0.0
        diagnostic_totals: dict[str, int] = {}
        top_centers = np.asarray(frozen.top_bundle["top_centers"], dtype=np.float64)
        child_centers = np.stack([np.asarray(value, dtype=np.float64) for value in frozen.hierarchy["centers"]])
        preprocessor = frozen.top_bundle["preprocessor"]
        top_weights = group_recipe["geometry"]["topWeights"]
        content_weights = group_recipe["geometry"]["contentWeights"]

        for start in range(0, count, batch_size):
            stop = min(start + batch_size, count)
            frame = catalog.iloc[start:stop]
            blocks, diagnostics = transform_chunk(frame, preprocessor)
            for key, value in diagnostics.items():
                diagnostic_totals[key] = diagnostic_totals.get(key, 0) + int(value)
            top_vector = combine_blocks(blocks, top_weights)
            regenerated_content = combine_blocks(blocks, content_weights)
            source_chunk = np.asarray(source_content[start:stop], dtype=np.float64)
            _require(bool(np.isfinite(source_chunk).all()), f"non-finite source content at rows {start}:{stop}")
            difference = float(np.max(np.abs(regenerated_content - source_chunk))) if len(source_chunk) else 0.0
            maximum_vector_difference = max(maximum_vector_difference, difference)
            _require(difference <= float(group_recipe["parity"]["maxAbsVectorDifference"]),
                     f"content transform parity failed at rows {start}:{stop}: {difference}")
            taste, child, group = assign_chunk(
                top_vector,
                regenerated_content,
                top_centers,
                child_centers,
                batch_size=batch_size,
            )
            calculated_taste[start:stop] = taste
            calculated_child[start:stop] = child
            calculated_group[start:stop] = group
            top_supported[start:stop] = np.sum(top_vector * top_vector, axis=1) > 1e-12
            regenerated_sub_supported = (
                np.sum(regenerated_content * regenerated_content, axis=1) > 1e-12
            )
            source_sub_supported = np.sum(source_chunk * source_chunk, axis=1) > 1e-12
            _require(
                np.array_equal(regenerated_sub_supported, source_sub_supported),
                f"content support parity failed at rows {start}:{stop}",
            )
            sub_supported[start:stop] = regenerated_sub_supported
            exported_vectors[start:stop] = source_chunk.astype(np.float32)
        exported_vectors.flush()

        mismatch = {
            "tasteId": int(np.count_nonzero(calculated_taste != assignments["taste_id"])),
            "childId": int(np.count_nonzero(calculated_child != assignments["child_id"])),
            "groupId": int(np.count_nonzero(calculated_group != assignments["group_id"])),
        }
        _require(all(value == 0 for value in mismatch.values()), f"reference assignment parity failed: {mismatch}")
        _require(np.array_equal(calculated_group, calculated_taste * 128 + calculated_child),
                 "group ID formula mismatch")

        index = staging_index(
            reference_ids,
            tmdb_ids,
            calculated_taste,
            calculated_child,
            calculated_group,
            top_supported,
            sub_supported,
        )
        index.to_parquet(temporary / "movie-index.parquet", index=False)
        _export_preprocessor(temporary, group_recipe, preprocessor)
        _export_geometry(temporary, group_recipe, frozen)

        audit_vectors = np.load(
            temporary / "content-vectors.float32.npy", mmap_mode="r", allow_pickle=False
        )
        cast_audit = _pair_score_cast_audit(source_content, audit_vectors)
        _close_memmap(audit_vectors)
        audit_vectors = None
        _require(cast_audit["maximumCosineDifference"] <= float(similar_recipe["sourceToDatabaseScoreTolerance"]),
                 "float32 cosine cast audit exceeded similar-movies tolerance")
        validation = _validate_written_bundle(temporary, count)
        parity = {
            "schemaVersion": "feelm-gkt-staging-parity/1",
            "status": "PASS_FOR_REFERENCE_STAGING",
            "G01": {
                "status": "PASS",
                "checks": structure_checks,
                "legacyServingKeysExported": [],
            },
            "G02": {
                "status": "PASS",
                "sourceRows": count,
                "maximumAbsoluteVectorDifference": maximum_vector_difference,
                "assignmentMismatches": mismatch,
            },
            "G03": {"status": "COVERED_BY_SYNTHETIC_TESTS", "sourceDiagnostics": diagnostic_totals},
            "G04": {"status": "COVERED_BY_SYNTHETIC_TESTS_AND_FULL_BATCH_EXPORT"},
            "G05": {
                "status": "PASS",
                "groupMeanHash": group_recipe["geometry"]["groupMeans"]["sha256"],
                "renormalized": False,
            },
            "G06": {
                "status": "BLOCKED",
                "reason": "VERIFIED_CURRENT_SERVICE_ID_MAPPING_REQUIRED",
                "serviceIdMappedRows": 0,
            },
            "S01": {
                "status": "PARTIAL_PASS",
                "vectorExport": validation,
                "float32CosineAudit": cast_audit,
                "remaining": "current service IDs and candidate eligibility",
            },
            "S02-S08": {
                "status": "NOT_RUN",
                "reason": "require target PostgreSQL/pgvector, Spring/API/cache, retrieval and human quality checks",
            },
        }
        _json_write(temporary / "parity.json", parity)

        similar_export = {
            "schemaVersion": "feelm-similar-vector-export/1",
            "status": "SIMILARITY_NOT_READY",
            "stagingVersion": staging_version,
            "sourceCatalogVersion": source_catalog_version,
            "sourceVector": similar_recipe["sourceVector"],
            "dimensions": 131,
            "precision": "float32",
            "metric": similar_recipe["metric"],
            "movieIndex": "movie-index.parquet",
            "vectorMatrix": "content-vectors.float32.npy",
            "rowJoinKey": "rowIndex",
            "requiresSubSupported": True,
            "requiresTopSupported": False,
            "candidateEligibilityIncluded": False,
            "verifiedCurrentServiceIdMappingIncluded": False,
            "pairwiseNeighborsGenerated": False,
            "remaining": [
                "apply verified current tmdbId -> serviceMovieId crosswalk",
                "attach versioned candidate eligibility",
                "load vector(131) rows and build partial HNSW on target PostgreSQL",
                "execute S02-S08",
            ],
        }
        _json_write(temporary / "similar-vector-export.json", similar_export)

        artifacts = _artifact_inventory(temporary, excluded=("manifest.json",))
        manifest = {
            "schemaVersion": SCHEMA_VERSION,
            "indexSchemaVersion": INDEX_SCHEMA_VERSION,
            "status": "STAGING_ID_MAPPING_REQUIRED",
            "readyForServing": False,
            "groupBundleStatus": "GROUP_BUNDLE_NOT_READY",
            "similarityStatus": "SIMILARITY_NOT_READY",
            "stagingVersion": staging_version,
            "createdAt": _utc_now(),
            "sourceCatalogVersion": source_catalog_version,
            "geometryVersion": group_recipe["targetGeometryVersion"],
            "groupRecipeSha256": fingerprint["groupRecipeSha256"],
            "similarRecipeSha256": fingerprint["similarRecipeSha256"],
            "reviewedFingerprint": fingerprint,
            "review": file_record(review_path, logical_path=review_path.relative_to(repo_root).as_posix()),
            "runtime": {
                "frozenSource": group_recipe["runtime"],
                "exporter": _runtime_versions(),
            },
            "sources": frozen.sources,
            "logicalRowSchema": {
                "indexColumns": list(ALLOWED_INDEX_COLUMNS),
                "vectorFile": "content-vectors.float32.npy",
                "vectorJoin": "index.rowIndex == vector row",
                "dimensions": 131,
                "precision": "float32",
            },
            "rowCounts": validation,
            "artifacts": artifacts,
            "legacyPickleExported": False,
            "modelTrainingPerformed": False,
            "pairwiseNeighborsGenerated": False,
            "serviceIdMapping": {
                "status": "NOT_APPLIED",
                "referenceIdColumn": "referenceServiceMovieId",
                "joinKeyForServer": "tmdbId",
                "serviceIdMappedRows": 0,
                "finalGroupMappedRows": 0,
            },
            "remainingBeforeServing": [
                "verified current DB tmdbId -> serviceMovieId export and one-to-one validation",
                "final per-row catalogVersion/geometryVersion/groupRecipeHash/vectorHash/sourceHash materialization",
                "candidate eligibility and group source ranks for the same catalog version",
                "server reload, PostgreSQL/pgvector and G06/S02-S08 verification",
            ],
        }
        _json_write(temporary / "manifest.json", manifest)
        _require(file_record(temporary / "manifest.json")["bytes"] > 0, "manifest write failed")
        _publish_staged_directory(
            temporary,
            resolved_output,
            (audit_vectors, exported_vectors, source_content),
        )
        audit_vectors = None
        exported_vectors = None
        source_content = None
        return manifest
    except Exception:
        _cleanup_staged_directory(
            temporary,
            (audit_vectors, exported_vectors, source_content),
        )
        raise


def _command_preflight(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    script_path = Path(__file__).resolve()
    group_recipe_path = _resolve_inside_repo(repo_root, args.group_recipe)
    similar_recipe_path = _resolve_inside_repo(repo_root, args.similar_recipe)
    output_path = _resolve_inside_repo(repo_root, args.output)
    report = build_preflight_report(repo_root, script_path, group_recipe_path, similar_recipe_path)
    _json_write(output_path, report)
    print(json.dumps({"status": report["status"], "output": str(output_path)}, ensure_ascii=False))
    return 0 if report["status"] == "AWAITING_INDEPENDENT_REVIEW" else 2


def _command_export(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    script_path = Path(__file__).resolve()
    group_recipe_path = _resolve_inside_repo(repo_root, args.group_recipe)
    similar_recipe_path = _resolve_inside_repo(repo_root, args.similar_recipe)
    review_path = _resolve_inside_repo(repo_root, args.review)
    output_dir = _resolve_inside_repo(repo_root, args.output_dir)
    manifest = export_bundle(
        repo_root=repo_root,
        script_path=script_path,
        group_recipe_path=group_recipe_path,
        similar_recipe_path=similar_recipe_path,
        review_path=review_path,
        output_dir=output_dir,
        staging_version=args.staging_version,
        source_catalog_version=args.source_catalog_version,
        batch_size=args.batch_size,
    )
    print(json.dumps({"status": manifest["status"], "output": str(output_dir)}, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    result.add_argument("--group-recipe", default=str(DEFAULT_GROUP_RECIPE))
    result.add_argument("--similar-recipe", default=str(DEFAULT_SIMILAR_RECIPE))
    commands = result.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="verify frozen sources and emit the review fingerprint")
    preflight.add_argument(
        "--output",
        default="outputs/recommendation-evidence/gkt-staging-bundle-v1-20260913/preflight.json",
    )
    preflight.set_defaults(handler=_command_preflight)
    export = commands.add_parser("export", help="perform the reviewed full reference export")
    export.add_argument("--review", required=True)
    export.add_argument("--output-dir", required=True)
    export.add_argument("--staging-version", required=True)
    export.add_argument("--source-catalog-version", required=True)
    export.add_argument("--batch-size", type=int, default=2048)
    export.set_defaults(handler=_command_export)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ContractError as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
