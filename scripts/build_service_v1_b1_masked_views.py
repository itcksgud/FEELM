"""Build the immutable TMDB-masked RH230 physical axis for service-v1 B1.

The builder recreates the complete natural RH230 feature vector from the audited
row lineage before it writes a masked row.  It creates feature data only: no
model is fitted and no evaluation target, KOBIS value, or service movie ID is
read.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import shutil
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from foundation340_features import Features, RATING_COLUMNS


SCHEMA_VERSION = "feelm-service-v1-b1-masked-views/1"
VIEWS_SCHEMA_VERSION = "feelm-service-v1-b1-logical-views/1"
MASK_PREFIX = "train_mask:339:ml:"
MAX_CHUNK_ROWS = 4096
NATURAL_PARITY_TOLERANCE = 2e-6
FEATURE_COLUMNS = [f"x{i:03d}" for i in range(230)]
DEPENDENCY_NAMES = (
    "foundation340_features.py",
    "cold_item_features.py",
    "text339_relations.py",
    "rec047_features.py",
    "rec046_common.py",
)


@dataclass(frozen=True)
class MaskCensus:
    catalog_movies: int
    masked_movies: int
    target_rows: int
    masked_target_rows: int
    history_pairs: int
    masked_history_pairs: int
    rows_with_masked_history: int
    rows_with_masked_target_or_history: int
    users: int


@dataclass(frozen=True)
class ContractExpectation:
    training_recipe_sha256: str
    artifact_manifest_sha256: str
    design_sha256: str
    metadata_sha256: str
    catalog_sha256: str
    rh_train_sha256: str
    lineage_manifest_sha256: str
    lineage_sha256: str
    dependency_sha256: dict[str, str]
    census: MaskCensus
    prior_mean: float
    prior_mass: float
    prior_movies: int


CANONICAL_CONTRACT = ContractExpectation(
    training_recipe_sha256="d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b",
    artifact_manifest_sha256="1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343",
    design_sha256="77e209e85ade4c81b12999ded35c61f3dac51d8d5138c2501206731c07589792",
    metadata_sha256="4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d",
    catalog_sha256="0bde668e0e26f5f82b5c41d90d62c7569fd350bf2a2fd4b59bb4402f438c5947",
    rh_train_sha256="9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45",
    lineage_manifest_sha256="3cb6862bdba78fb3014971c4d7ffdc26069e09b261bbe7479bdbdf7add10c265",
    lineage_sha256="44fd15d301e5fe9c076ab3df6de3a063bf3a0b43248b1610d0c550e1478d4bce",
    dependency_sha256={
        "foundation340_features.py": "83e2586d9acc8bfb8aec8dc47b8133f4bc0da8678ba0d82bdd295b5c7bf13cf9",
        "cold_item_features.py": "78ed5bbea151dc12d7a8bb144fa389333c779550fcfdff66d28d90c6900a4cec",
        "text339_relations.py": "fdc970a6506cfd27dcc2df15cd2785ce5f221f76bd74eaeb24aa6d73c5ccd4d6",
        "rec047_features.py": "a6f3fdc26cc88cceea2f6b1fe1244285bd9e29f7175874f7ab2eb8fae5904268",
        "rec046_common.py": "42dd14e83ecbee0c02c5e4233abb4b6a8d807ad2213c24a22cfb82350bc47848",
    },
    census=MaskCensus(
        catalog_movies=85_517,
        masked_movies=17_099,
        target_rows=4_997_069,
        masked_target_rows=966_274,
        history_pairs=41_750_890,
        masked_history_pairs=8_074_083,
        rows_with_masked_history=2_640_486,
        rows_with_masked_target_or_history=3_095_318,
        users=39_859,
    ),
    prior_mean=6.173088067675869,
    prior_mass=48.0,
    prior_movies=44_920,
)


@dataclass
class FeatureState:
    movie_ids: np.ndarray
    movie_mask: np.ndarray
    natural: Any
    masked: Any


@dataclass
class ChunkResult:
    table: pa.Table
    rows: int
    users: set[int]
    target_masked: int
    history_pairs: int
    history_masked: int
    rows_with_masked_history: int
    rows_with_masked_target_or_history: int
    natural_max_abs_difference: float


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def pin(path: Path, logical_path: str | None = None) -> dict[str, Any]:
    require(path.is_file(), f"missing input: {path}")
    return {
        "path": logical_path or path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path.name} must contain an object")
    return value


def verify_pin(path: Path, expected: dict[str, Any], label: str) -> None:
    require(path.is_file(), f"{label} is missing")
    require(path.stat().st_size == expected.get("bytes"), f"{label} byte count drift")
    require(sha256_file(path) == expected.get("sha256"), f"{label} SHA-256 drift")


def verify_exact_sha256(path: Path, expected: str, label: str) -> None:
    require(path.is_file(), f"{label} is missing")
    require(sha256_file(path) == expected, f"{label} is not the reviewed canonical input")


def dependency_paths() -> dict[str, Path]:
    scripts = Path(__file__).resolve().parent
    return {name: scripts / name for name in DEPENDENCY_NAMES}


def feature_schema() -> pa.Schema:
    return pa.schema(
        [("row_id", pa.int64()), ("uid", pa.int32()), ("label", pa.float64())]
        + [(name, pa.float32()) for name in FEATURE_COLUMNS]
    )


def lineage_schema() -> pa.Schema:
    return pa.schema([
        ("row_id", pa.int64()),
        ("uid", pa.int32()),
        ("target_ml_movie_id", pa.int32()),
        ("canonical_target_key", pa.string()),
        ("target_timestamp", pa.int64()),
        ("label", pa.float64()),
        ("original_cap", pa.int8()),
        ("history_length", pa.int8()),
        ("history_ml_ids", pa.list_(pa.int32())),
        ("history_stars", pa.list_(pa.float32())),
        ("history_timestamps", pa.list_(pa.int64())),
    ])


def digest_is_masked(digest: bytes) -> bool:
    require(len(digest) == hashlib.sha256().digest_size, "mask digest must be SHA-256")
    return 5 * int.from_bytes(digest, "big") < (1 << 256)


def movie_is_masked(movie_id: int) -> bool:
    canonical = f"{MASK_PREFIX}{int(movie_id)}".encode("utf-8")
    return digest_is_masked(hashlib.sha256(canonical).digest())


def catalog_mask(movie_ids: np.ndarray) -> np.ndarray:
    ids = np.asarray(movie_ids)
    require(ids.ndim == 1, "catalog movie IDs must be one-dimensional")
    return np.fromiter((movie_is_masked(int(value)) for value in ids), dtype=bool, count=len(ids))


def make_masked_features(natural: Any, movie_mask: np.ndarray) -> Any:
    mask = np.asarray(movie_mask, dtype=bool)
    require(mask.ndim == 1, "movie mask must be one-dimensional")
    require(len(mask) == len(natural.original.crowd), "movie mask length drift")
    masked = copy.copy(natural)
    masked.original = copy.copy(natural.original)
    masked.original.crowd = np.asarray(natural.original.crowd).copy()
    masked.original.valid = np.asarray(natural.original.valid).copy()
    masked.shrunk = np.asarray(natural.shrunk).copy()
    masked.original.crowd[mask, :] = 0
    masked.original.valid[mask, :] = False
    masked.shrunk[mask, :] = 0
    require(np.array_equal(masked.original.crowd[~mask], natural.original.crowd[~mask]),
            "unmasked crowd values changed")
    require(np.array_equal(masked.original.valid[~mask], natural.original.valid[~mask]),
            "unmasked crowd presence changed")
    require(np.array_equal(masked.shrunk[~mask], natural.shrunk[~mask]),
            "unmasked shrunk crowd changed")
    require(not bool(masked.original.valid[mask].any()), "masked crowd remains present")
    require(not bool(masked.original.crowd[mask].any()), "masked crowd value remains")
    require(not bool(masked.shrunk[mask].any()), "masked shrunk crowd value remains")
    require(masked.prior_mean == natural.prior_mean, "masked prior mean changed")
    require(masked.prior_mass == natural.prior_mass, "masked prior mass changed")
    require(masked.prior_movies == natural.prior_movies, "masked prior population changed")
    return masked


def validate_contract(
    args: argparse.Namespace,
    expectation: ContractExpectation = CANONICAL_CONTRACT,
) -> tuple[dict[str, Any], dict[str, Path], dict[str, dict[str, Any]]]:
    design_path = Path(__file__).resolve().parents[1] / "docs/recommendation/plans/service-v1-b1-masked-views.md"
    dependencies = dependency_paths()
    exact = {
        "trainingRecipe": (args.training_recipe, expectation.training_recipe_sha256),
        "artifactManifest": (args.artifact_manifest, expectation.artifact_manifest_sha256),
        "designPlan": (design_path, expectation.design_sha256),
        "metadata": (args.metadata, expectation.metadata_sha256),
        "catalog": (args.catalog, expectation.catalog_sha256),
        "rh230Train": (args.rh_train, expectation.rh_train_sha256),
        "lineageManifest": (args.lineage_manifest, expectation.lineage_manifest_sha256),
        "rowLineage": (args.lineage, expectation.lineage_sha256),
    }
    for name, path in dependencies.items():
        require(name in expectation.dependency_sha256, f"missing reviewed dependency hash: {name}")
        exact[f"dependency:{name}"] = (path, expectation.dependency_sha256[name])
    for label, (path, digest) in exact.items():
        verify_exact_sha256(path, digest, label)

    recipe = load_json(args.training_recipe)
    require(recipe.get("sourceTargetRows") == expectation.census.target_rows,
            "training recipe row count drift")
    require(recipe.get("viewCount") == 4, "training recipe view count drift")
    require(recipe.get("expandedRows") == 4 * expectation.census.target_rows,
            "training recipe logical row count drift")
    require(recipe.get("featureGenerationChunkRows") == MAX_CHUNK_ROWS,
            "training recipe chunk size drift")
    require(recipe.get("trainMaskHashPrefix") == MASK_PREFIX,
            "training recipe mask prefix drift")
    require(recipe.get("sourceRowIdColumn") == "row_id", "training recipe row key drift")
    require(recipe.get("viewIdColumn") == "view_id", "training recipe view key drift")
    require(recipe.get("weightColumnGBT") == "view_weight", "training recipe weight key drift")
    require(recipe.get("absentChannelMaskViews") == "RETAIN_IDENTICAL_ROWS",
            "training recipe absent-channel rule drift")
    variants = recipe.get("variants")
    require(isinstance(variants, list), "training recipe variants missing")
    b1 = [item for item in variants if isinstance(item, dict) and item.get("id") == "B1"]
    require(len(b1) == 1 and b1[0].get("dimensions") == 230 and b1[0].get("newGBTfit") is True,
            "training recipe B1 contract drift")

    artifacts = load_json(args.artifact_manifest).get("artifacts")
    require(isinstance(artifacts, list), "artifact manifest list missing")
    rh = [item for item in artifacts
          if item.get("source_path") == "outputs/recommendation-evidence/foundation340/RH/train.parquet"]
    require(len(rh) == 1, "artifact manifest must pin one RH230 train")
    verify_pin(args.rh_train, rh[0], "artifact manifest RH230 train")

    lineage_manifest = load_json(args.lineage_manifest)
    require(lineage_manifest.get("status") == "LINEAGE_READY_B1_FEATURES_NOT_BUILT",
            "row lineage status drift")
    require(lineage_manifest.get("readyForTraining") is False,
            "row lineage must not claim training readiness")
    require(lineage_manifest.get("serviceMovieIdMappingUsed") is False,
            "row lineage used service mapping")
    require(lineage_manifest.get("evaluationTargetsRead") is False,
            "row lineage read evaluation targets")
    counts = lineage_manifest.get("rowCounts", {})
    require(counts.get("rows") == expectation.census.target_rows, "row lineage row census drift")
    require(counts.get("users") == expectation.census.users, "row lineage user census drift")
    require(counts.get("historyPairs") == expectation.census.history_pairs,
            "row lineage history census drift")
    lineage_artifact = lineage_manifest.get("artifacts", {}).get("row-lineage.parquet")
    require(isinstance(lineage_artifact, dict), "row lineage artifact pin missing")
    verify_pin(args.lineage, lineage_artifact, "row lineage artifact")

    logical_paths = {
        "trainingRecipe": "contracts/training-recipe.v1.json",
        "artifactManifest": "contracts/service-v1-artifacts.json",
        "designPlan": "plans/service-v1-b1-masked-views.md",
        "metadata": "sources/rec-ev-045/metadata.parquet",
        "catalog": "sources/text339/catalog.parquet",
        "rh230Train": "sources/foundation340/RH/train.parquet",
        "lineageManifest": "sources/row-lineage-v1/manifest.json",
        "rowLineage": "sources/row-lineage-v1/row-lineage.parquet",
    }
    paths = {label: path for label, (path, _) in exact.items()}
    pins = {
        label: pin(path, logical_paths.get(label, f"implementation/{path.name}"))
        for label, path in paths.items()
    }
    for label, (_, expected_digest) in exact.items():
        require(pins[label]["sha256"] == expected_digest,
                f"{label} changed while canonical pins were captured")
    pins["implementation"] = pin(
        Path(__file__).resolve(), "implementation/build_service_v1_b1_masked_views.py"
    )
    paths["implementation"] = Path(__file__).resolve()
    return recipe, paths, pins


def load_feature_state(
    args: argparse.Namespace,
    expectation: ContractExpectation,
    feature_factory: Callable[[pd.DataFrame, pd.DataFrame], Any] = Features,
) -> FeatureState:
    metadata = pd.read_parquet(args.metadata)
    catalog = pd.read_parquet(args.catalog)
    require(len(metadata) == expectation.census.catalog_movies, "metadata row count drift")
    require(len(catalog) == expectation.census.catalog_movies, "catalog row count drift")
    metadata_ids = metadata["movie_id"].to_numpy(dtype=np.int64)
    catalog_ids = catalog["movie_id"].to_numpy(dtype=np.int64)
    require(bool(np.array_equal(metadata_ids, catalog_ids)), "metadata/catalog movie axis differs")
    require(len(catalog_ids) > 0 and bool((catalog_ids[1:] > catalog_ids[:-1]).all()),
            "metadata/catalog movie axis must be unique and increasing")
    mask = catalog_mask(catalog_ids)
    require(int(mask.sum()) == expectation.census.masked_movies, "catalog mask census drift")
    natural = feature_factory(metadata, catalog)
    require(abs(float(natural.prior_mean) - expectation.prior_mean) <= 1e-12,
            "natural prior mean drift")
    require(float(natural.prior_mass) == expectation.prior_mass, "natural prior mass drift")
    require(int(natural.prior_movies) == expectation.prior_movies, "natural prior population drift")
    masked = make_masked_features(natural, mask)
    return FeatureState(movie_ids=catalog_ids, movie_mask=mask, natural=natural, masked=masked)


def scalar_numpy(table: pa.Table, name: str, dtype: np.dtype[Any]) -> np.ndarray:
    column = table[name].combine_chunks()
    require(column.null_count == 0, f"{name} contains nulls")
    return np.asarray(column.to_numpy(zero_copy_only=False), dtype=dtype)


def list_numpy(
    table: pa.Table,
    name: str,
    dtype: np.dtype[Any],
) -> tuple[np.ndarray, np.ndarray]:
    column = table[name].combine_chunks()
    require(column.null_count == 0, f"{name} contains null lists")
    raw_offsets = np.asarray(column.offsets.to_numpy(zero_copy_only=False), dtype=np.int64)
    first, last = int(raw_offsets[0]), int(raw_offsets[-1])
    offsets = raw_offsets - first
    values = np.asarray(
        column.values.slice(first, last - first).to_numpy(zero_copy_only=False),
        dtype=dtype,
    )
    return offsets, values


def map_movie_ids(movie_ids: np.ndarray, axis: np.ndarray, label: str) -> np.ndarray:
    values = np.asarray(movie_ids, dtype=np.int64)
    positions = np.searchsorted(axis, values)
    require(bool((positions < len(axis)).all()), f"{label} is outside metadata axis")
    require(np.array_equal(axis[positions], values), f"{label} does not map exactly")
    return positions.astype(np.int64, copy=False)


def process_chunk(
    rh: pd.DataFrame,
    lineage: pa.Table,
    state: FeatureState,
    parity_tolerance: float = NATURAL_PARITY_TOLERANCE,
) -> ChunkResult:
    rows = len(rh)
    require(0 < rows <= MAX_CHUNK_ROWS, "feature calculation chunk exceeds 4096 rows")
    require(lineage.num_rows == rows, "RH/lineage chunk size differs")
    expected_columns = ["row_id", "uid", "label", *FEATURE_COLUMNS]
    require(list(rh.columns) == expected_columns, "RH230 column order/schema drift")
    row_ids = rh["row_id"].to_numpy(dtype=np.int64, copy=False)
    uids = rh["uid"].to_numpy(dtype=np.int32, copy=False)
    labels = rh["label"].to_numpy(dtype=np.float64, copy=False)
    require(np.array_equal(scalar_numpy(lineage, "row_id", np.int64), row_ids),
            "RH/lineage row_id differs")
    require(np.array_equal(scalar_numpy(lineage, "uid", np.int32), uids),
            "RH/lineage uid differs")
    require(np.array_equal(scalar_numpy(lineage, "label", np.float64), labels),
            "RH/lineage label differs")
    require(bool(np.isin(labels, np.arange(1, 11, dtype=np.float64) / 2).all()),
            "RH labels must be half-star values")

    target_ids = scalar_numpy(lineage, "target_ml_movie_id", np.int64)
    canonical_keys = lineage["canonical_target_key"].combine_chunks()
    require(canonical_keys.null_count == 0, "canonical target key contains nulls")
    require(all(
        key == f"ml:{int(movie_id)}"
        for key, movie_id in zip(canonical_keys.to_pylist(), target_ids, strict=True)
    ), "canonical target key differs from target movie")
    target_indices = map_movie_ids(target_ids, state.movie_ids, "target movie")
    target_timestamps = scalar_numpy(lineage, "target_timestamp", np.int64)
    caps = scalar_numpy(lineage, "original_cap", np.int8)
    declared_lengths = scalar_numpy(lineage, "history_length", np.int8)
    id_offsets, history_ids = list_numpy(lineage, "history_ml_ids", np.int64)
    star_offsets, history_stars_flat = list_numpy(lineage, "history_stars", np.float32)
    time_offsets, history_timestamps = list_numpy(lineage, "history_timestamps", np.int64)
    require(np.array_equal(id_offsets, star_offsets) and np.array_equal(id_offsets, time_offsets),
            "lineage list offsets differ")
    lengths = np.diff(id_offsets)
    require(np.array_equal(lengths, declared_lengths.astype(np.int64)),
            "lineage declared/list lengths differ")
    require(bool((lengths >= 0).all()) and bool((lengths <= caps).all())
            and bool((lengths <= 30).all()), "lineage history cap drift")
    require(bool(np.isin(history_stars_flat, np.arange(1, 11, dtype=np.float32) / 2).all()),
            "history stars must be half-star values")
    history_indices_flat = map_movie_ids(history_ids, state.movie_ids, "history movie")
    repeated_rows = np.repeat(np.arange(rows, dtype=np.int64), lengths)
    if len(history_ids):
        require(bool((history_timestamps < target_timestamps[repeated_rows]).all()),
                "lineage history is not strictly earlier")
        require(not bool((history_ids == target_ids[repeated_rows]).any()),
                "target movie appears in lineage history")
        same_row = repeated_rows[1:] == repeated_rows[:-1]
        require(bool((history_timestamps[1:][same_row]
                      <= history_timestamps[:-1][same_row]).all()),
                "lineage history is not most-recent-first")
        tied = same_row & (history_timestamps[1:] == history_timestamps[:-1])
        require(bool((history_ids[1:][tied] >= history_ids[:-1][tied]).all()),
                "lineage timestamp tie order drift")

    history_indices = np.zeros((rows, 30), dtype=np.int64)
    history_stars = np.zeros((rows, 30), dtype=np.float64)
    history_present = np.arange(30, dtype=np.int64)[None, :] < lengths[:, None]
    if len(history_ids):
        columns = np.arange(len(history_ids), dtype=np.int64) - np.repeat(id_offsets[:-1], lengths)
        history_indices[repeated_rows, columns] = history_indices_flat
        history_stars[repeated_rows, columns] = history_stars_flat

    rh_features = rh[FEATURE_COLUMNS].to_numpy(dtype=np.float32, copy=True)
    require(bool(np.isfinite(rh_features).all()), "RH230 contains nonfinite values")
    natural = state.natural.batch(
        history_indices, history_stars, history_present, target_indices
    )
    natural_shrunk = state.natural.crowd(
        history_indices, history_stars, history_present, target_indices, shrink=True
    )
    rating_indices = np.asarray(RATING_COLUMNS, dtype=np.int64)
    require(np.array_equal(rating_indices, np.array([200, 207, 208, 210, 211, 212])),
            "RH shrinkage overwrite columns drift")
    natural[:, rating_indices] = natural_shrunk[:, rating_indices - 200]
    require(natural.shape == rh_features.shape and bool(np.isfinite(natural).all()),
            "natural RH230 recreation shape/finite drift")
    natural_max = float(np.max(np.abs(natural.astype(np.float64) - rh_features.astype(np.float64))))
    require(natural_max <= parity_tolerance, "natural RH230 parity exceeds 2e-6")

    masked_crowd = state.masked.crowd(
        history_indices, history_stars, history_present, target_indices, shrink=False
    )
    masked_shrunk = state.masked.crowd(
        history_indices, history_stars, history_present, target_indices, shrink=True
    )
    require(masked_crowd.shape == (rows, 30) and masked_shrunk.shape == (rows, 30),
            "masked crowd feature dimension drift")
    masked_crowd[:, rating_indices - 200] = masked_shrunk[:, rating_indices - 200]
    masked_features = rh_features.copy()
    masked_features[:, 200:230] = masked_crowd
    require(np.array_equal(masked_features[:, :200], rh_features[:, :200]),
            "masked output changed x000..x199")
    require(bool(np.isfinite(masked_features).all()), "masked RH230 contains nonfinite values")

    target_mask = state.movie_mask[target_indices]
    history_movie_mask = np.zeros_like(history_present)
    if len(history_ids):
        history_movie_mask[repeated_rows, columns] = state.movie_mask[history_indices_flat]
    masked_history_by_row = (history_movie_mask & history_present).any(axis=1)
    output = {
        "row_id": row_ids,
        "uid": uids,
        "label": labels,
    }
    output.update({name: masked_features[:, index] for index, name in enumerate(FEATURE_COLUMNS)})
    table = pa.Table.from_pydict(output, schema=feature_schema())
    return ChunkResult(
        table=table,
        rows=rows,
        users=set(int(value) for value in np.unique(uids)),
        target_masked=int(target_mask.sum()),
        history_pairs=int(lengths.sum()),
        history_masked=int((history_movie_mask & history_present).sum()),
        rows_with_masked_history=int(masked_history_by_row.sum()),
        rows_with_masked_target_or_history=int((target_mask | masked_history_by_row).sum()),
        natural_max_abs_difference=natural_max,
    )


def verify_source_files(
    rh_source: pq.ParquetFile,
    lineage_source: pq.ParquetFile,
    expected_rows: int,
) -> None:
    require(rh_source.schema_arrow == feature_schema(), "RH230 Arrow schema drift")
    require(lineage_source.schema_arrow == lineage_schema(), "row lineage Arrow schema drift")
    require(rh_source.metadata.num_rows == expected_rows, "RH230 row count drift")
    require(lineage_source.metadata.num_rows == expected_rows, "row lineage row count drift")
    require(rh_source.num_row_groups == lineage_source.num_row_groups,
            "RH230/lineage row-group count differs")
    for group in range(rh_source.num_row_groups):
        require(rh_source.metadata.row_group(group).num_rows
                == lineage_source.metadata.row_group(group).num_rows,
                f"RH230/lineage row-group {group} size differs")


def verify_inputs_unchanged(paths: dict[str, Path], pins: dict[str, dict[str, Any]]) -> None:
    for name, path in paths.items():
        verify_pin(path, pins[name], f"input {name}")


def memory_rss_bytes() -> int | None:
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except (ImportError, OSError):
        return None


def views_document(rows: int, rh_pin: dict[str, Any], masked_pin: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": VIEWS_SCHEMA_VERSION,
        "status": "LOGICAL_VIEWS_DEFINED_MODEL_NOT_FIT",
        "readyForTraining": False,
        "modelFitPerformed": False,
        "sourceRows": rows,
        "logicalRows": 4 * rows,
        "requiredViewIdsPerSourceRow": [0, 1, 2, 3],
        "weightColumn": "view_weight",
        "requiredWeightSumPerSourceRow": 1.0,
        "physicalAxesMustNotBeCollapsedToTwoRows": True,
        "views": [
            {"viewId": 0, "name": "NATURAL", "physicalAxis": "RH230_NATURAL",
             "artifact": rh_pin, "viewWeight": 0.25},
            {"viewId": 1, "name": "TMDB_MASKED", "physicalAxis": "RH230_TMDB_MASKED",
             "artifact": masked_pin, "viewWeight": 0.25},
            {"viewId": 2, "name": "KOBIS_MASKED", "physicalAxis": "RH230_NATURAL",
             "artifact": rh_pin, "viewWeight": 0.25},
            {"viewId": 3, "name": "BOTH_MASKED", "physicalAxis": "RH230_TMDB_MASKED",
             "artifact": masked_pin, "viewWeight": 0.25},
        ],
    }


def publish(temporary: Path, output_dir: Path) -> None:
    require(not output_dir.exists(), "output directory already exists; versions are immutable")
    os.replace(temporary, output_dir)


def build(
    args: argparse.Namespace,
    *,
    expectation: ContractExpectation = CANONICAL_CONTRACT,
    feature_factory: Callable[[pd.DataFrame, pd.DataFrame], Any] = Features,
) -> dict[str, Any]:
    require(not args.output_dir.exists(), "output directory already exists; versions are immutable")
    recipe, input_paths, input_pins = validate_contract(args, expectation)
    state = load_feature_state(args, expectation, feature_factory)
    temporary = args.output_dir.parent / f".{args.output_dir.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(parents=True)
    writer: pq.ParquetWriter | None = None
    rh_source: pq.ParquetFile | None = None
    lineage_source: pq.ParquetFile | None = None
    primary_error: BaseException | None = None
    published = False
    started = time.monotonic()
    peak_rss = memory_rss_bytes()
    try:
        rh_source = pq.ParquetFile(args.rh_train)
        lineage_source = pq.ParquetFile(args.lineage)
        verify_source_files(rh_source, lineage_source, expectation.census.target_rows)
        writer = pq.ParquetWriter(
            temporary / "tmdb-masked-rh230.parquet",
            feature_schema(),
            compression="zstd",
            write_statistics=True,
        )
        totals = Counter()
        users: set[int] = set()
        natural_max = 0.0
        maximum_chunk = 0
        for group in range(rh_source.num_row_groups):
            rh_group = rh_source.read_row_group(group).to_pandas()
            lineage_group = lineage_source.read_row_group(group)
            require(len(rh_group) == lineage_group.num_rows,
                    f"RH230/lineage row-group {group} read size differs")
            first_row = totals["rows"]
            require(np.array_equal(
                rh_group["row_id"].to_numpy(dtype=np.int64, copy=False),
                np.arange(first_row, first_row + len(rh_group), dtype=np.int64),
            ), f"RH230 row_id is not contiguous in row-group {group}")
            for start in range(0, len(rh_group), MAX_CHUNK_ROWS):
                stop = min(start + MAX_CHUNK_ROWS, len(rh_group))
                result = process_chunk(
                    rh_group.iloc[start:stop].reset_index(drop=True),
                    lineage_group.slice(start, stop - start),
                    state,
                )
                writer.write_table(result.table)
                totals.update({
                    "rows": result.rows,
                    "targetMasked": result.target_masked,
                    "historyPairs": result.history_pairs,
                    "historyMasked": result.history_masked,
                    "rowsWithMaskedHistory": result.rows_with_masked_history,
                    "rowsWithMaskedTargetOrHistory": result.rows_with_masked_target_or_history,
                })
                users.update(result.users)
                natural_max = max(natural_max, result.natural_max_abs_difference)
                maximum_chunk = max(maximum_chunk, result.rows)
                current_rss = memory_rss_bytes()
                if current_rss is not None:
                    peak_rss = current_rss if peak_rss is None else max(peak_rss, current_rss)
            if group % 20 == 0:
                print(json.dumps({
                    "stage": "B1_MASKED_VIEWS",
                    "rowGroup": group,
                    "rows": totals["rows"],
                    "total": expectation.census.target_rows,
                    "seconds": round(time.monotonic() - started, 3),
                }), flush=True)

        writer.close()
        writer = None
        rh_source.close()
        rh_source = None
        lineage_source.close()
        lineage_source = None
        observed = MaskCensus(
            catalog_movies=len(state.movie_ids),
            masked_movies=int(state.movie_mask.sum()),
            target_rows=totals["rows"],
            masked_target_rows=totals["targetMasked"],
            history_pairs=totals["historyPairs"],
            masked_history_pairs=totals["historyMasked"],
            rows_with_masked_history=totals["rowsWithMaskedHistory"],
            rows_with_masked_target_or_history=totals["rowsWithMaskedTargetOrHistory"],
            users=len(users),
        )
        require(observed == expectation.census, "B1 mask/resource census drift")
        require(maximum_chunk <= MAX_CHUNK_ROWS, "calculation chunk exceeded 4096 rows")
        require(natural_max <= NATURAL_PARITY_TOLERANCE,
                "natural RH230 full-axis parity drift")

        masked_path = temporary / "tmdb-masked-rh230.parquet"
        reload_file = pq.ParquetFile(masked_path)
        try:
            require(reload_file.schema_arrow == feature_schema(), "masked output schema drift")
            require(reload_file.metadata.num_rows == expectation.census.target_rows,
                    "masked output row count drift")
        finally:
            reload_file.close()
        masked_pin = pin(masked_path, "tmdb-masked-rh230.parquet")
        rh_view_pin = dict(input_pins["rh230Train"])
        views = views_document(expectation.census.target_rows, rh_view_pin, masked_pin)
        views_path = temporary / "views-manifest.json"
        views_path.write_text(
            json.dumps(views, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        views_pin = pin(views_path, "views-manifest.json")
        manifest = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "B1_MASKED_FEATURES_BUILT_PENDING_INDEPENDENT_AUDIT",
            "readyForTraining": False,
            "modelFitPerformed": False,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "scope": "MOVIELENS_TRAINING_FEATURES_ONLY",
            "serviceMovieIdMappingUsed": False,
            "evaluationTargetsRead": False,
            "kobisDataRead": False,
            "inputs": input_pins,
            "mask": {
                "prefix": MASK_PREFIX,
                "formula": "5*int.from_bytes(SHA256(UTF8(prefix+movie_id_decimal)),'big') < 2^256",
                "channels": ["tmdb_vote_average", "tmdb_vote_count", "tmdb_popularity"],
                "maskedValue": 0,
                "maskedPresent": False,
                "priorReestimatedAfterMask": False,
            },
            "prior": {
                "mean": float(state.natural.prior_mean),
                "mass": float(state.natural.prior_mass),
                "movies": int(state.natural.prior_movies),
            },
            "census": {
                "catalogMovies": observed.catalog_movies,
                "maskedMovies": observed.masked_movies,
                "targetRows": observed.target_rows,
                "maskedTargetRows": observed.masked_target_rows,
                "historyPairs": observed.history_pairs,
                "maskedHistoryPairs": observed.masked_history_pairs,
                "rowsWithMaskedHistory": observed.rows_with_masked_history,
                "rowsWithMaskedTargetOrHistory": observed.rows_with_masked_target_or_history,
                "users": observed.users,
                "logicalViewRows": 4 * observed.target_rows,
            },
            "parity": {
                "naturalRecomputedColumns": 230,
                "naturalMaximumAbsoluteDifference": natural_max,
                "tolerance": NATURAL_PARITY_TOLERANCE,
                "shrunkOverwriteColumns": list(RATING_COLUMNS),
                "maskedCopiedColumns": "x000..x199",
                "maskedRecomputedColumns": "x200..x229",
            },
            "resources": {
                "maximumCalculationChunkRows": maximum_chunk,
                "peakProcessRssBytesObserved": peak_rss,
                "fullFourViewMatrixMaterialized": False,
            },
            "runtime": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pandas": pd.__version__,
                "pyarrow": pa.__version__,
                "executableName": Path(sys.executable).name,
                "seconds": time.monotonic() - started,
            },
            "artifacts": {
                "tmdb-masked-rh230.parquet": masked_pin,
                "views-manifest.json": views_pin,
            },
            "remainingBeforeB1Fit": [
                "independent result audit of masked features and four-view contract",
                "build and independently review the exact Spark four-view runner",
                "verify 19,988,276 logical rows and per-source weight sum 1 before fit",
            ],
        }
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        verify_inputs_unchanged(input_paths, input_pins)
        verify_pin(masked_path, masked_pin, "masked output")
        verify_pin(views_path, views_pin, "views manifest output")
        publish(temporary, args.output_dir)
        published = True
        return manifest
    except BaseException as error:
        primary_error = error
        raise
    finally:
        close_error: BaseException | None = None
        for resource in (writer, rh_source, lineage_source):
            if resource is None:
                continue
            try:
                resource.close()
            except BaseException as error:
                if close_error is None:
                    close_error = error
        if not published and temporary.exists():
            try:
                shutil.rmtree(temporary)
            except BaseException as error:
                if close_error is None:
                    close_error = error
        if primary_error is None and close_error is not None:
            raise close_error


def dry_run_first_row_group(
    args: argparse.Namespace,
    *,
    expectation: ContractExpectation = CANONICAL_CONTRACT,
    feature_factory: Callable[[pd.DataFrame, pd.DataFrame], Any] = Features,
) -> dict[str, Any]:
    _, input_paths, input_pins = validate_contract(args, expectation)
    state = load_feature_state(args, expectation, feature_factory)
    rh_source: pq.ParquetFile | None = None
    lineage_source: pq.ParquetFile | None = None
    primary_error: BaseException | None = None
    try:
        rh_source = pq.ParquetFile(args.rh_train)
        lineage_source = pq.ParquetFile(args.lineage)
        verify_source_files(rh_source, lineage_source, expectation.census.target_rows)
        rows = min(MAX_CHUNK_ROWS, rh_source.metadata.row_group(0).num_rows)
        rh = rh_source.read_row_group(0).slice(0, rows).to_pandas()
        lineage = lineage_source.read_row_group(0).slice(0, rows)
        result = process_chunk(rh, lineage, state)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        close_error: BaseException | None = None
        for resource in (rh_source, lineage_source):
            if resource is None:
                continue
            try:
                resource.close()
            except BaseException as error:
                if close_error is None:
                    close_error = error
        if primary_error is None and close_error is not None:
            raise close_error
    verify_inputs_unchanged(input_paths, input_pins)
    return {
        "status": "DRY_RUN_ONLY_NOT_PUBLISHED",
        "rows": result.rows,
        "naturalMaximumAbsoluteDifference": result.natural_max_abs_difference,
        "targetMaskedRows": result.target_masked,
        "historyPairs": result.history_pairs,
        "maskedHistoryPairs": result.history_masked,
        "outputSchema": str(result.table.schema),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--training-recipe", type=Path, required=True)
    result.add_argument("--artifact-manifest", type=Path, required=True)
    result.add_argument("--metadata", type=Path, required=True)
    result.add_argument("--catalog", type=Path, required=True)
    result.add_argument("--rh-train", type=Path, required=True)
    result.add_argument("--lineage-manifest", type=Path, required=True)
    result.add_argument("--lineage", type=Path, required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    manifest = build(args)
    print(json.dumps({"status": manifest["status"], "output": str(args.output_dir)}))


if __name__ == "__main__":
    main()
