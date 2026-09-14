"""Build the immutable MovieLens training-row lineage required by service-v1 B1.

This stage reconstructs strict-past histories only.  It does not create masked
features, fit a model, read evaluation targets, or map MovieLens IDs to service IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from foundation340_features import Histories


SCHEMA_VERSION = "feelm-service-v1-row-lineage/1"
EXPECTED_HISTORY_PAIRS = 41_750_890
EXPECTED_ZERO_HISTORY_ROWS = 1_051_625
EXPECTED_CAP_COUNTS = {0: 998_592, 1: 986_527, 5: 1_002_302, 10: 1_009_507, 30: 1_000_141}


@dataclass(frozen=True)
class ContractExpectation:
    training_recipe_sha256: str
    artifact_manifest_sha256: str
    prepared_seal_sha256: str
    rh_train_sha256: str
    foundation_features_sha256: str
    source_target_rows: int
    training_users: int
    history_pairs: int
    zero_history_rows: int
    cap_counts: dict[int, int]


CANONICAL_CONTRACT = ContractExpectation(
    training_recipe_sha256="d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b",
    artifact_manifest_sha256="1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343",
    prepared_seal_sha256="d27709a42bd0d5511a36f3740365637325d59b521c7f62eae04850523782fdd8",
    rh_train_sha256="9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45",
    foundation_features_sha256="83e2586d9acc8bfb8aec8dc47b8133f4bc0da8678ba0d82bdd295b5c7bf13cf9",
    source_target_rows=4_997_069,
    training_users=39_859,
    history_pairs=EXPECTED_HISTORY_PAIRS,
    zero_history_rows=EXPECTED_ZERO_HISTORY_ROWS,
    cap_counts=EXPECTED_CAP_COUNTS,
)


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


def verify_exact_sha256(path: Path, expected_sha256: str, label: str) -> None:
    require(path.is_file(), f"{label} is missing")
    require(sha256_file(path) == expected_sha256, f"{label} is not the reviewed canonical input")


def validate_contract(
    training_recipe_path: Path,
    artifact_manifest_path: Path,
    prepared_seal_path: Path,
    ratings_path: Path,
    episodes_path: Path,
    catalog_path: Path,
    rh_train_path: Path,
    expectation: ContractExpectation = CANONICAL_CONTRACT,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    foundation_features_path = Path(__file__).resolve().parent / "foundation340_features.py"
    verify_exact_sha256(training_recipe_path, expectation.training_recipe_sha256, "training recipe")
    verify_exact_sha256(artifact_manifest_path, expectation.artifact_manifest_sha256, "artifact manifest")
    verify_exact_sha256(prepared_seal_path, expectation.prepared_seal_sha256, "prepared seal")
    verify_exact_sha256(rh_train_path, expectation.rh_train_sha256, "RH230 train")
    verify_exact_sha256(
        foundation_features_path,
        expectation.foundation_features_sha256,
        "foundation340_features.py",
    )
    recipe = load_json(training_recipe_path)
    require(recipe.get("rowLineageRequired") is True, "training recipe must require row lineage")
    require(recipe.get("lineageOutput") == "row-lineage.parquet", "unexpected lineage filename")
    require(recipe.get("sourceTargetRows") == expectation.source_target_rows, "unexpected source target row count")
    require(recipe.get("featureGenerationChunkRows") == 4096, "unexpected feature chunk contract")
    require(recipe.get("canonicalMaskMovieId") == "ml:<MovieLens movie_id decimal integer>",
            "unexpected canonical MovieLens key contract")
    require(recipe.get("dropRowsWithoutServiceMapping") is False,
            "lineage must not require service movie mapping")
    lineage_sources = recipe.get("lineageSources")
    require(isinstance(lineage_sources, list), "training recipe lineageSources are missing")
    foundation_entries = [
        item for item in lineage_sources
        if isinstance(item, dict) and item.get("path") == "scripts/foundation340_features.py"
    ]
    require(len(foundation_entries) == 1, "training recipe must pin foundation340_features.py exactly once")
    require(
        foundation_entries[0].get("sha256") == expectation.foundation_features_sha256,
        "training recipe foundation340_features.py pin drift",
    )
    prepared_entries = [
        item for item in lineage_sources
        if isinstance(item, dict)
        and item.get("path") == "outputs/recommendation-evidence/text339/prepared-seal.json"
    ]
    require(len(prepared_entries) == 1, "training recipe must pin prepared seal exactly once")
    require(
        prepared_entries[0].get("sha256") == expectation.prepared_seal_sha256,
        "training recipe prepared seal pin drift",
    )

    prepared = load_json(prepared_seal_path)
    require(prepared.get("target_stars_decoded") == 0,
            "prepared source must not have decoded evaluation target stars")
    prepared_files = prepared.get("files")
    require(isinstance(prepared_files, dict), "prepared seal file pins are missing")
    sources = {
        "ratings": (ratings_path, prepared_files.get("ratings.parquet")),
        "episodes": (episodes_path, prepared_files.get("episodes.parquet")),
        "catalog": (catalog_path, prepared_files.get("catalog.parquet")),
    }
    for label, (path, expected) in sources.items():
        require(isinstance(expected, dict), f"prepared seal lacks {label}")
        verify_pin(path, expected, label)

    artifacts = load_json(artifact_manifest_path).get("artifacts")
    require(isinstance(artifacts, list), "artifact manifest list is missing")
    matches = [item for item in artifacts
               if item.get("source_path") == "outputs/recommendation-evidence/foundation340/RH/train.parquet"]
    require(len(matches) == 1, "artifact manifest must pin exactly one RH230 train file")
    verify_pin(rh_train_path, matches[0], "RH230 train")

    input_pins = {
        "trainingRecipe": pin(training_recipe_path, "contracts/training-recipe.v1.json"),
        "artifactManifest": pin(artifact_manifest_path, "contracts/service-v1-artifacts.json"),
        "preparedSeal": pin(prepared_seal_path, "sources/text339/prepared-seal.json"),
        "ratings": pin(ratings_path, "sources/text339/ratings.parquet"),
        "episodes": pin(episodes_path, "sources/text339/episodes.parquet"),
        "catalog": pin(catalog_path, "sources/text339/catalog.parquet"),
        "rh230Train": pin(rh_train_path, "sources/foundation340/RH/train.parquet"),
        "foundationFeatures": pin(
            foundation_features_path,
            "implementation/foundation340_features.py",
        ),
        "implementation": pin(Path(__file__).resolve(), "implementation/build_service_v1_row_lineage.py"),
    }
    return recipe, input_pins


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


def strict_history_rows(histories: Histories, rows: np.ndarray) -> dict[str, np.ndarray]:
    rows = np.asarray(rows, dtype=np.int64)
    require(rows.ndim == 1 and len(rows) > 0, "rows must be a nonempty vector")
    require(bool((rows >= 0).all()) and bool((rows < len(histories.uid)).all()),
            "row index outside ratings")
    width = 30
    boundary = histories.strict_boundary[rows]
    positions = boundary[:, None] - 1 - np.arange(width, dtype=np.int64)
    mask = ((positions >= histories.user_start[rows, None])
            & (np.arange(width, dtype=np.int64) < histories.cap[rows, None]))
    require(bool(np.array_equal(mask, np.arange(width)[None, :] < mask.sum(axis=1)[:, None])),
            "strict history mask must be a prefix")
    source_rows = histories.past_order[np.maximum(positions, 0)]
    repeated_uid = histories.uid[rows, None]
    repeated_ts = histories.ts[rows, None]
    require(bool((histories.uid[source_rows][mask] == np.broadcast_to(repeated_uid, mask.shape)[mask]).all()),
            "history crossed a user boundary")
    require(bool((histories.ts[source_rows][mask] < np.broadcast_to(repeated_ts, mask.shape)[mask]).all()),
            "history must be strictly earlier than its target")
    require(not bool((histories.movie[source_rows][mask]
                      == np.broadcast_to(histories.movie[rows, None], mask.shape)[mask]).any()),
            "target movie leaked into its own history")
    return {
        "mask": mask,
        "source_rows": source_rows,
        "history_length": mask.sum(axis=1).astype(np.int8),
    }


def list_array(values: np.ndarray, mask: np.ndarray, value_type: pa.DataType) -> pa.ListArray:
    require(values.shape == mask.shape and values.ndim == 2, "list source shape mismatch")
    lengths = mask.sum(axis=1).astype(np.int64)
    require(int(lengths.sum()) < np.iinfo(np.int32).max, "one lineage batch exceeds ListArray capacity")
    offsets = np.empty(len(lengths) + 1, dtype=np.int32)
    offsets[0] = 0
    np.cumsum(lengths, out=offsets[1:])
    flattened = values[mask]
    return pa.ListArray.from_arrays(pa.array(offsets, type=pa.int32()),
                                    pa.array(flattened, type=value_type))


def build_batch(histories: Histories, rows: np.ndarray) -> pa.Table:
    detail = strict_history_rows(histories, rows)
    mask = detail["mask"]
    source = detail["source_rows"]
    movie = histories.movie[rows].astype(np.int32, copy=False)
    uid = histories.uid[rows].astype(np.int32, copy=False)
    timestamps = histories.ts[rows].astype(np.int64, copy=False)
    labels = histories.stars[rows].astype(np.float64, copy=False)
    require(bool(np.isin(labels, np.arange(1, 11, dtype=float) / 2).all()),
            "training labels must be half-star values")
    arrays = [
        pa.array(rows, type=pa.int64()),
        pa.array(uid, type=pa.int32()),
        pa.array(movie, type=pa.int32()),
        pa.array([f"ml:{int(value)}" for value in movie], type=pa.string()),
        pa.array(timestamps, type=pa.int64()),
        pa.array(labels, type=pa.float64()),
        pa.array(histories.cap[rows].astype(np.int8), type=pa.int8()),
        pa.array(detail["history_length"], type=pa.int8()),
        list_array(histories.movie[source].astype(np.int32, copy=False), mask, pa.int32()),
        list_array(histories.stars[source].astype(np.float32, copy=False), mask, pa.float32()),
        list_array(histories.ts[source].astype(np.int64, copy=False), mask, pa.int64()),
    ]
    return pa.Table.from_arrays(arrays, schema=lineage_schema())


def verify_inputs_unchanged(paths: dict[str, Path], pins: dict[str, dict[str, Any]]) -> None:
    for name, path in paths.items():
        verify_pin(path, pins[name], f"input {name}")


def publish(temporary: Path, output_dir: Path) -> None:
    require(not output_dir.exists(), "output directory already exists; versions are immutable")
    os.replace(temporary, output_dir)


def build(
    args: argparse.Namespace,
    *,
    expectation: ContractExpectation = CANONICAL_CONTRACT,
) -> dict[str, Any]:
    require(not args.output_dir.exists(), "output directory already exists; versions are immutable")
    recipe, pins = validate_contract(
        args.training_recipe, args.artifact_manifest, args.prepared_seal,
        args.ratings, args.episodes, args.catalog, args.rh_train,
        expectation,
    )
    input_paths = {
        "trainingRecipe": args.training_recipe,
        "artifactManifest": args.artifact_manifest,
        "preparedSeal": args.prepared_seal,
        "ratings": args.ratings,
        "episodes": args.episodes,
        "catalog": args.catalog,
        "rh230Train": args.rh_train,
        "foundationFeatures": Path(__file__).resolve().parent / "foundation340_features.py",
        "implementation": Path(__file__).resolve(),
    }
    temporary = args.output_dir.parent / f".{args.output_dir.name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(parents=True)
    writer: pq.ParquetWriter | None = None
    source: pq.ParquetFile | None = None
    primary_error: BaseException | None = None
    published = False
    started = time.monotonic()
    try:
        ratings = pd.read_parquet(args.ratings, columns=["uid", "movie_id", "rating", "timestamp"])
        episodes = pd.read_parquet(args.episodes)
        catalog = pd.read_parquet(args.catalog, columns=["movie_id"])
        expected_rows = int(recipe["sourceTargetRows"])
        require(len(ratings) == expected_rows, "ratings row count differs from recipe")
        require(int(ratings["uid"].nunique()) == expectation.training_users, "training user count drift")
        require(
            not bool(ratings.duplicated(subset=["uid", "movie_id"], keep=False).any()),
            "ratings contain duplicate (uid, movie_id) pairs",
        )
        movie_axis = catalog["movie_id"].to_numpy(dtype=np.int64)
        require(len(movie_axis) > 0 and bool(np.all(movie_axis[1:] > movie_axis[:-1])),
                "MovieLens catalog axis must be strictly increasing")
        histories = Histories(ratings, episodes, movie_axis)
        require(set(int(value) for value in np.unique(histories.cap)) == set(expectation.cap_counts),
                "assigned cap values drift")
        source = pq.ParquetFile(args.rh_train)
        writer = pq.ParquetWriter(
            temporary / "row-lineage.parquet", lineage_schema(), compression="zstd",
            use_dictionary=["canonical_target_key"], write_statistics=True,
        )
        offset = 0
        history_pairs = 0
        zero_history_rows = 0
        max_history = 0
        cap_counts: dict[str, int] = {}
        for group in range(source.num_row_groups):
            frame = source.read_row_group(group, columns=["row_id", "uid", "label"]).to_pandas()
            rows = np.arange(offset, offset + len(frame), dtype=np.int64)
            require(bool(np.array_equal(frame["row_id"].to_numpy(dtype=np.int64), rows)),
                    "RH230 row_id order drift")
            require(bool(np.array_equal(frame["uid"].to_numpy(dtype=np.int32), histories.uid[rows])),
                    "RH230 uid differs from ratings")
            require(bool(np.array_equal(frame["label"].to_numpy(dtype=np.float64), histories.stars[rows])),
                    "RH230 label differs from ratings")
            table = build_batch(histories, rows)
            lengths = table["history_length"].to_numpy(zero_copy_only=False)
            history_pairs += int(lengths.sum())
            zero_history_rows += int((lengths == 0).sum())
            max_history = max(max_history, int(lengths.max(initial=0)))
            for value, count in zip(*np.unique(histories.cap[rows], return_counts=True), strict=True):
                key = str(int(value))
                cap_counts[key] = cap_counts.get(key, 0) + int(count)
            writer.write_table(table)
            offset += len(rows)
            if group % 20 == 0:
                print(json.dumps({
                    "stage": "ROW_LINEAGE", "rows": offset, "total": expected_rows,
                    "seconds": round(time.monotonic() - started, 3),
                }), flush=True)
        writer.close()
        writer = None
        require(offset == expected_rows, "lineage lost target rows")
        require(history_pairs == expectation.history_pairs, "strict history pair census drift")
        require(zero_history_rows == expectation.zero_history_rows, "zero-history census drift")
        require(max_history <= 30, "history cap exceeded 30")
        observed_cap_counts = {int(key): value for key, value in cap_counts.items()}
        require(observed_cap_counts == expectation.cap_counts, "cap row census drift")

        lineage_path = temporary / "row-lineage.parquet"
        reload_file = pq.ParquetFile(lineage_path)
        try:
            require(reload_file.metadata.num_rows == expected_rows, "published lineage row count drift")
            require(reload_file.schema_arrow == lineage_schema(), "published lineage schema drift")
        finally:
            reload_file.close()
        output_pin = pin(lineage_path, "row-lineage.parquet")
        verify_inputs_unchanged(input_paths, pins)
        manifest = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "LINEAGE_READY_B1_FEATURES_NOT_BUILT",
            "readyForTraining": False,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "scope": "MOVIELENS_TRAINING_ROWS_ONLY",
            "serviceMovieIdMappingUsed": False,
            "evaluationTargetsRead": False,
            "inputs": pins,
            "rowCounts": {
                "rows": expected_rows,
                "users": int(ratings["uid"].nunique()),
                "historyPairs": history_pairs,
                "zeroHistoryRows": zero_history_rows,
                "maximumHistoryLength": max_history,
                "capCounts": dict(sorted(cap_counts.items(), key=lambda item: int(item[0]))),
            },
            "lineageContract": {
                "targetKey": "ml:<MovieLens movie_id decimal integer>",
                "historyOrder": "STRICTLY_PAST_MOST_RECENT_FIRST",
                "equalTimestampExcluded": True,
                "targetMovieExcluded": True,
                "maximumHistory": 30,
            },
            "runtime": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pandas": pd.__version__,
                "pyarrow": pa.__version__,
                "executableName": Path(sys.executable).name,
                "seconds": time.monotonic() - started,
            },
            "artifacts": {"row-lineage.parquet": output_pin},
            "remainingBeforeB1Fit": [
                "independent result audit of every lineage invariant",
                "build and verify natural/TMDB-masked RH230 views",
                "verify four logical views and GBT weight 0.25 per source row",
                "independent pre-fit review of the exact Spark runner",
            ],
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        verify_inputs_unchanged(input_paths, pins)
        verify_pin(lineage_path, output_pin, "lineage output")
        publish(temporary, args.output_dir)
        published = True
        return manifest
    except BaseException as error:
        primary_error = error
        raise
    finally:
        close_error: BaseException | None = None
        for resource in (writer, source):
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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--ratings", type=Path, required=True)
    result.add_argument("--episodes", type=Path, required=True)
    result.add_argument("--catalog", type=Path, required=True)
    result.add_argument("--rh-train", type=Path, required=True)
    result.add_argument("--prepared-seal", type=Path, required=True)
    result.add_argument("--training-recipe", type=Path, required=True)
    result.add_argument("--artifact-manifest", type=Path, required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    manifest = build(args)
    print(json.dumps({"status": manifest["status"], "output": str(args.output_dir)}))


if __name__ == "__main__":
    main()
