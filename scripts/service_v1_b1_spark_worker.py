"""Container-only Spark actions for the reviewed service-v1 B1 GBT run.

This module deliberately has no evaluation action and never accepts evaluation labels.
PySpark is imported lazily so the deterministic guards can be unit-tested on the host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


SCHEMA_VERSION = "feelm-service-v1-b1-spark-worker/1"
SPARK_VERSION = "4.1.3"
SOURCE_ROWS = 4_997_069
LOGICAL_ROWS = 19_988_276
SCORE_ROWS = 93_230
PARTITIONS = 8
FEATURE_COLUMNS = tuple(f"x{index:03d}" for index in range(230))
TRAIN_COLUMNS = ("row_id", "uid", "label", *FEATURE_COLUMNS)
SCORE_COLUMNS = ("row_id", "uid", *FEATURE_COLUMNS)
VIEW_IDS = (0, 1, 2, 3)
VIEW_WEIGHT = 0.25
NATURAL_RG0_ROWS = 8_231
MASKED_RG0_ROWS = 4_096
DRY_RUN_ROWS = 4_096
MAX_MEMORY_BYTES = 12 * 1024**3

CANONICAL_PINS: dict[str, dict[str, Any]] = {
    "natural": {
        "bytes": 832_717_601,
        "sha256": "9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45",
    },
    "masked": {
        "bytes": 891_461_814,
        "sha256": "27aee771597ba230654b2e99c1eea047fd25e3265161c51b6873474d47c65f01",
    },
    "masked_manifest": {
        "bytes": 5_176,
        "sha256": "82eb3635f1ae914a056c2813c4786dfa2de56abbb37aaf5ce852685f6eb6a557",
    },
    "views_manifest": {
        "bytes": 1_710,
        "sha256": "df8dd8bbfea4a71e506958c5b7e1499b5010350a326cbfac2c9bfe23a607792a",
    },
    "masked_review": {
        "bytes": 10_174,
        "sha256": "f7e322206d95c8ad418926a08394625f84a25b611f4e9595c97dd03cca6c7b1c",
    },
    "training_recipe": {
        "bytes": 15_764,
        "sha256": "d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b",
    },
}

EXPECTED_PARTITIONS = (
    (0, 624_054, 2_496_216, "21fbf39b23490f46a935ff54dcdab20a1f38361351840780f278a27c4c8de4ea"),
    (1, 624_438, 2_497_752, "b54c60df6c9ebf0b1268c60124f9908b2d86a7bbb9f2fceffce520a344123891"),
    (2, 624_481, 2_497_924, "5c072bdc8ec9396af8e6dad574d95b13c20988765acf1177d46da6e2c82500d8"),
    (3, 625_449, 2_501_796, "84692cce25046923f3a23751f3c1ca2902324f0e44650140dcdf607322cb8f7d"),
    (4, 624_427, 2_497_708, "51e8100ac0aba07e8b3a0852f375924ae15a6baa2c81bb80f5953396efd2d2a1"),
    (5, 624_773, 2_499_092, "14b8332eebabecb4cdb90975220967a2c258f7b2aae1ebd20bcbcc410ac6d4d0"),
    (6, 624_390, 2_497_560, "442632597415d7f1e194fb6133a1675997a0e5d741bc9242dded088a0bb35923"),
    (7, 625_057, 2_500_228, "2f51a1ffa91329f80d2802843df72e11ad387b2e304f82a653fcee3294336344"),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def file_pin(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"missing file: {path}")
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path)}


def verify_pin(path: Path, expected: dict[str, Any], name: str) -> None:
    actual = file_pin(path)
    require(actual == expected, f"{name} immutable pin drift: {actual}")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    require(not path.exists(), f"refusing to replace existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def feature_schema() -> pa.Schema:
    return pa.schema(
        [("row_id", pa.int64()), ("uid", pa.int32()), ("label", pa.float64())]
        + [(name, pa.float32()) for name in FEATURE_COLUMNS]
    )


def score_output_schema() -> pa.Schema:
    return pa.schema([
        ("row_id", pa.int64()),
        ("uid", pa.int32()),
        ("prediction", pa.float64()),
    ])


def read_and_validate_score_axis(path: Path, expected_rows: int = SCORE_ROWS) -> tuple[np.ndarray, np.ndarray]:
    table = pq.read_table(path, columns=["row_id", "uid"])
    require(table.num_rows == expected_rows, "natural score-axis row count drift")
    require(table.schema == pa.schema([("row_id", pa.int64()), ("uid", pa.int32())]), "score-axis schema drift")
    row_ids = table["row_id"].to_numpy(zero_copy_only=False)
    uids = table["uid"].to_numpy(zero_copy_only=False)
    expected = np.arange(expected_rows, dtype=np.int64)
    require(np.array_equal(row_ids, expected), "score row_id must be unique, strictly increasing 0..93229")
    return row_ids, uids


def validate_prediction_file(path: Path, expected_row_ids: np.ndarray, expected_uids: np.ndarray) -> dict[str, Any]:
    table = pq.read_table(path)
    require(table.schema == score_output_schema(), "prediction schema drift")
    require(table.num_rows == len(expected_row_ids), "prediction row count drift")
    row_ids = table["row_id"].to_numpy(zero_copy_only=False)
    uids = table["uid"].to_numpy(zero_copy_only=False)
    predictions = table["prediction"].to_numpy(zero_copy_only=False)
    require(np.array_equal(row_ids, expected_row_ids), "prediction duplicate/missing/reordered row_id")
    require(np.array_equal(uids, expected_uids), "prediction UID swap or score-axis mismatch")
    require(np.isfinite(predictions).all(), "non-finite B1 prediction")
    return {
        "rows": len(row_ids),
        "minimumPrediction": float(predictions.min()),
        "maximumPrediction": float(predictions.max()),
    }


def _pin_matches(record: Any, expected: dict[str, Any]) -> bool:
    return (
        isinstance(record, dict)
        and record.get("bytes") == expected["bytes"]
        and record.get("sha256") == expected["sha256"]
    )


def verify_masked_contract(
    natural: Path,
    masked: Path,
    masked_manifest_path: Path,
    views_manifest_path: Path,
    masked_review_path: Path,
) -> None:
    verify_pin(natural, CANONICAL_PINS["natural"], "natural RH230")
    verify_pin(masked, CANONICAL_PINS["masked"], "TMDB-masked RH230")
    verify_pin(masked_manifest_path, CANONICAL_PINS["masked_manifest"], "masked manifest")
    verify_pin(views_manifest_path, CANONICAL_PINS["views_manifest"], "views manifest")
    verify_pin(masked_review_path, CANONICAL_PINS["masked_review"], "masked result review")

    masked_parent = masked.parent.resolve()
    require(
        masked_parent == masked_manifest_path.parent.resolve() == views_manifest_path.parent.resolve(),
        "masked bundle files do not share one directory",
    )
    require(natural.parent.resolve() != masked_parent, "natural input shares the masked bundle directory")
    require(masked_review_path.parent.resolve() != masked_parent, "masked review shares the masked bundle directory")
    names = {path.name for path in masked.parent.iterdir()}
    require(
        names == {"manifest.json", "tmdb-masked-rh230.parquet", "views-manifest.json"},
        "masked bundle file set is not exactly the reviewed three files",
    )

    manifest = load_json(masked_manifest_path)
    require(manifest.get("modelFitPerformed") is False, "masked build performed a model fit")
    require(manifest.get("evaluationTargetsRead") is False, "masked build read evaluation targets")
    artifacts = manifest.get("artifacts")
    require(isinstance(artifacts, dict), "masked manifest artifacts are missing")
    require(
        _pin_matches(artifacts.get("tmdb-masked-rh230.parquet"), CANONICAL_PINS["masked"]),
        "masked manifest does not pin masked parquet",
    )
    require(
        _pin_matches(artifacts.get("views-manifest.json"), CANONICAL_PINS["views_manifest"]),
        "masked manifest does not pin views manifest",
    )

    views = load_json(views_manifest_path)
    require(views.get("sourceRows") == SOURCE_ROWS, "view source row count drift")
    require(views.get("logicalRows") == LOGICAL_ROWS, "view logical row count drift")
    require(views.get("requiredViewIdsPerSourceRow") == list(VIEW_IDS), "view ID contract drift")
    require(views.get("requiredWeightSumPerSourceRow") == 1.0, "view weight-sum contract drift")
    rows = views.get("views")
    require(isinstance(rows, list) and len(rows) == 4, "four-view definition is missing")
    require([row.get("viewId") for row in rows] == list(VIEW_IDS), "view ordering drift")
    require([row.get("viewWeight") for row in rows] == [VIEW_WEIGHT] * 4, "view weights drift")
    require(
        [row.get("physicalAxis") for row in rows]
        == ["RH230_NATURAL", "RH230_TMDB_MASKED", "RH230_NATURAL", "RH230_TMDB_MASKED"],
        "view physical-axis mapping drift",
    )

    review = load_json(masked_review_path)
    require(review.get("status") == "PASS", "masked independent review is not PASS")
    require(review.get("allRowsChecked") is True, "masked independent review did not check all rows")
    require(review.get("checkedRows") == SOURCE_ROWS, "masked review checked-row count drift")
    require(review.get("modelFitPerformed") is False, "masked review reports model fitting")
    require(review.get("immutableDirectoryModified") is False, "masked review modified target bundle")
    targets = review.get("targetHashes")
    require(isinstance(targets, dict), "masked review target hashes are missing")
    for name, pin_name in (
        ("manifest.json", "masked_manifest"),
        ("tmdb-masked-rh230.parquet", "masked"),
        ("views-manifest.json", "views_manifest"),
    ):
        require(_pin_matches(targets.get(name), CANONICAL_PINS[pin_name]), f"masked review target drift: {name}")
        require(targets[name].get("verified") is True, f"masked review target is not verified: {name}")


def verify_recipe(path: Path) -> dict[str, Any]:
    verify_pin(path, CANONICAL_PINS["training_recipe"], "training recipe")
    recipe = load_json(path)
    require(recipe.get("runtime") == "Spark 4.1.3", "Spark recipe runtime drift")
    require(recipe.get("sourceTargetRows") == SOURCE_ROWS, "recipe source rows drift")
    require(recipe.get("viewCount") == 4, "recipe view count drift")
    require(recipe.get("expandedRows") == LOGICAL_ROWS, "recipe expanded rows drift")
    require(recipe.get("partitionCount") == PARTITIONS, "recipe partition count drift")
    require(recipe.get("partitionKey") == "row_id", "recipe partition key drift")
    require(recipe.get("sortWithinPartition") == ["row_id", "view_id"], "recipe sort contract drift")
    require(recipe.get("weightColumnGBT") == "view_weight", "recipe weight column drift")
    require(recipe.get("aqeEnabled") is False, "recipe AQE drift")
    require(recipe.get("absentChannelMaskViews") == "RETAIN_IDENTICAL_ROWS", "view retention drift")
    model = recipe.get("models", {}).get("GBT")
    require(isinstance(model, dict) and isinstance(model.get("parameters"), dict), "GBT recipe missing")
    return model["parameters"]


def parquet_footer(path: Path, expected_rows: int) -> tuple[int, int]:
    source = pq.ParquetFile(path)
    try:
        require(source.schema_arrow == feature_schema(), f"feature schema drift: {path}")
        require(source.metadata.num_rows == expected_rows, f"row count drift: {path}")
        require(source.num_row_groups > 0, f"no row groups: {path}")
        return source.num_row_groups, source.metadata.row_group(0).num_rows
    finally:
        source.close()


def verify_full_row_id_axis(path: Path, expected_rows: int) -> None:
    source = pq.ParquetFile(path)
    offset = 0
    try:
        for group_index in range(source.num_row_groups):
            row_ids = source.read_row_group(group_index, columns=["row_id"])["row_id"].to_numpy(
                zero_copy_only=False
            )
            expected = np.arange(offset, offset + len(row_ids), dtype=np.int64)
            require(np.array_equal(row_ids, expected), f"physical row_id order drift at group {group_index}: {path}")
            offset += len(row_ids)
        require(offset == expected_rows, f"physical row_id count drift: {path}")
    finally:
        source.close()


def prepare_dry_run_parquets(
    natural: Path,
    masked: Path,
    scratch: Path,
    *,
    natural_group_rows: int = NATURAL_RG0_ROWS,
    masked_group_rows: int = MASKED_RG0_ROWS,
    common_rows: int = DRY_RUN_ROWS,
) -> dict[str, Any]:
    natural_file = pq.ParquetFile(natural)
    masked_file = pq.ParquetFile(masked)
    try:
        require(natural_file.schema_arrow == feature_schema(), "natural RG0 schema drift")
        require(masked_file.schema_arrow == feature_schema(), "masked RG0 schema drift")
        require(natural_file.metadata.row_group(0).num_rows == natural_group_rows, "natural RG0 row count drift")
        require(masked_file.metadata.row_group(0).num_rows == masked_group_rows, "masked RG0 row count drift")
        natural_table = natural_file.read_row_group(0)
        masked_table = masked_file.read_row_group(0)
    finally:
        natural_file.close()
        masked_file.close()

    natural_ids = natural_table["row_id"].to_numpy(zero_copy_only=False)
    masked_ids = masked_table["row_id"].to_numpy(zero_copy_only=False)
    require(np.array_equal(natural_ids, np.arange(natural_group_rows, dtype=np.int64)), "natural RG0 domain drift")
    require(np.array_equal(masked_ids, np.arange(masked_group_rows, dtype=np.int64)), "masked RG0 domain drift")
    require(natural_group_rows - common_rows == natural_group_rows - masked_group_rows, "dry-run domain contract drift")
    require(
        np.array_equal(natural_ids[common_rows:], np.arange(common_rows, natural_group_rows, dtype=np.int64)),
        "natural-only RG0 domain drift",
    )

    natural_common = natural_table.filter(pc.less_equal(natural_table["row_id"], pa.scalar(common_rows - 1)))
    masked_common = masked_table.filter(pc.less_equal(masked_table["row_id"], pa.scalar(common_rows - 1)))
    left_ids = natural_common["row_id"].to_numpy(zero_copy_only=False)
    right_ids = masked_common["row_id"].to_numpy(zero_copy_only=False)
    expected_ids = np.arange(common_rows, dtype=np.int64)
    require(np.array_equal(left_ids, expected_ids), "natural common-window domain drift")
    require(np.array_equal(right_ids, expected_ids), "masked common-window domain drift")
    require(np.setdiff1d(left_ids, right_ids).size == 0, "natural-to-masked anti-join is non-empty")
    require(np.setdiff1d(right_ids, left_ids).size == 0, "masked-to-natural anti-join is non-empty")
    require(
        np.array_equal(
            natural_common["uid"].to_numpy(zero_copy_only=False),
            masked_common["uid"].to_numpy(zero_copy_only=False),
        ),
        "dry-run uid parity drift",
    )
    left_label = natural_common["label"].to_numpy(zero_copy_only=False)
    right_label = masked_common["label"].to_numpy(zero_copy_only=False)
    require(np.array_equal(left_label.view(np.uint64), right_label.view(np.uint64)), "dry-run label bit parity drift")
    require(np.isfinite(left_label).all() and np.isfinite(right_label).all(), "dry-run non-finite label")
    for name in FEATURE_COLUMNS:
        require(np.isfinite(natural_common[name].to_numpy(zero_copy_only=False)).all(), f"natural non-finite {name}")
        require(np.isfinite(masked_common[name].to_numpy(zero_copy_only=False)).all(), f"masked non-finite {name}")

    natural_out = scratch / "natural-rg0-common.parquet"
    masked_out = scratch / "masked-rg0-common.parquet"
    pq.write_table(natural_common, natural_out, row_group_size=common_rows)
    pq.write_table(masked_common, masked_out, row_group_size=common_rows)
    return {
        "natural": natural_out,
        "masked": masked_out,
        "naturalPhysicalRows": natural_group_rows,
        "maskedPhysicalRows": masked_group_rows,
        "commonRows": common_rows,
        "naturalExcludedRows": natural_group_rows - common_rows,
    }


def _spark_types(include_label: bool) -> Any:
    from pyspark.sql.types import DoubleType, FloatType, IntegerType, LongType, StructField, StructType

    fields = [StructField("row_id", LongType(), False), StructField("uid", IntegerType(), False)]
    if include_label:
        fields.append(StructField("label", DoubleType(), False))
    fields.extend(StructField(name, FloatType(), False) for name in FEATURE_COLUMNS)
    return StructType(fields)


def open_spark(app_name: str, scratch: Path) -> Any:
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName(app_name).getOrCreate()
    try:
        spark.sparkContext.setLogLevel("ERROR")
        require(spark.version == SPARK_VERSION, f"unexpected Spark version: {spark.version}")
        require(spark.sparkContext.master == "local[4]", f"unexpected Spark master: {spark.sparkContext.master}")
        require(spark.conf.get("spark.sql.adaptive.enabled") == "false", "AQE must be disabled")
        require(int(spark.conf.get("spark.sql.shuffle.partitions")) == PARTITIONS, "shuffle partition drift")
        require(
            int(spark.conf.get("spark.sql.debug.maxToStringFields")) == 1000,
            "analyzed-plan field rendering limit drift",
        )
        checkpoint = scratch / "checkpoint"
        checkpoint.mkdir(parents=True, exist_ok=True)
        spark.sparkContext.setCheckpointDir(str(checkpoint))
        return spark
    except BaseException:
        spark.stop()
        raise


def runtime_versions(spark: Any) -> dict[str, str]:
    java_version = str(spark.sparkContext._jvm.java.lang.System.getProperty("java.version"))
    python_version = platform.python_version()
    require(spark.version == SPARK_VERSION, f"unexpected Spark version: {spark.version}")
    require(java_version.split(".", 1)[0] == "21", f"unexpected Java version: {java_version}")
    require(bool(python_version), "Python version is unavailable")
    return {
        "sparkVersion": spark.version,
        "javaVersion": java_version,
        "pythonVersion": python_version,
    }


def build_ordered_views(spark: Any, natural: Path, masked: Path) -> Any:
    from pyspark import StorageLevel
    from pyspark.ml.feature import VectorAssembler
    from pyspark.sql import functions as functions

    schema = _spark_types(include_label=True)
    natural_frame = spark.read.schema(schema).parquet(str(natural))
    masked_frame = spark.read.schema(schema).parquet(str(masked))
    columns = list(TRAIN_COLUMNS)

    def view(frame: Any, view_id: int) -> Any:
        return frame.select(*columns).withColumn("view_id", functions.lit(view_id).cast("int")).withColumn(
            "view_weight", functions.lit(VIEW_WEIGHT).cast("double")
        )

    logical = view(natural_frame, 0)
    logical = logical.unionByName(view(masked_frame, 1))
    logical = logical.unionByName(view(natural_frame, 2))
    logical = logical.unionByName(view(masked_frame, 3))
    assembler = VectorAssembler(
        inputCols=list(FEATURE_COLUMNS), outputCol="features", handleInvalid="error"
    )
    return (
        assembler.transform(logical)
        .select("row_id", "uid", "label", "view_id", "view_weight", "features")
        .repartition(PARTITIONS, "row_id")
        .sortWithinPartitions("row_id", "view_id")
        .persist(StorageLevel.DISK_ONLY)
    )


def _partition_identity(index: int, rows: Iterable[Any]) -> Iterator[dict[str, Any]]:
    view0_digest = hashlib.sha256()
    logical_digest = hashlib.sha256()
    source_rows = 0
    logical_rows = 0
    previous_row_id: int | None = None
    group: list[Any] = []

    def finish(values: Sequence[Any]) -> None:
        nonlocal source_rows
        require(len(values) == 4, f"partition {index} source row does not have four views")
        require([int(row.view_id) for row in values] == list(VIEW_IDS), f"partition {index} view order drift")
        row_id = int(values[0].row_id)
        require(all(int(row.row_id) == row_id for row in values), f"partition {index} row grouping drift")
        require(len({int(row.uid) for row in values}) == 1, f"partition {index} uid parity drift")
        label_bits = {struct.pack("<d", float(row.label)) for row in values}
        require(len(label_bits) == 1, f"partition {index} label bit parity drift")
        label = float(values[0].label)
        require(math.isfinite(label), f"partition {index} non-finite label")
        require(0.5 <= label <= 5.0 and label * 2.0 == round(label * 2.0), "half-star label drift")
        require(all(float(row.view_weight) == VIEW_WEIGHT for row in values), "view weight drift")
        vectors = [np.asarray(row.features.toArray(), dtype=np.float64) for row in values]
        require(all(vector.shape == (230,) for vector in vectors), "feature dimension drift")
        require(all(np.isfinite(vector).all() for vector in vectors), "non-finite feature")
        require(np.array_equal(vectors[0], vectors[2]), "view0/view2 feature parity drift")
        require(np.array_equal(vectors[1], vectors[3]), "view1/view3 feature parity drift")
        view0_digest.update(row_id.to_bytes(8, "little", signed=True))
        source_rows += 1

    for row in rows:
        row_id = int(row.row_id)
        if previous_row_id is None:
            previous_row_id = row_id
        elif row_id != previous_row_id:
            require(row_id > previous_row_id, f"partition {index} row_id order drift")
            finish(group)
            group = []
            previous_row_id = row_id
        group.append(row)
        logical_digest.update(
            struct.pack(
                "<qiidd",
                row_id,
                int(row.view_id),
                int(row.uid),
                float(row.label),
                float(row.view_weight),
            )
        )
        logical_rows += 1
    if group:
        finish(group)
    yield {
        "partition": index,
        "sourceRows": source_rows,
        "logicalRows": logical_rows,
        "view0RowIdSha256": view0_digest.hexdigest(),
        "logicalIdentitySha256": logical_digest.hexdigest(),
    }


def collect_identity(ordered: Any, expected_source_rows: int, require_full_identity: bool) -> dict[str, Any]:
    records = sorted(
        ordered.rdd.mapPartitionsWithIndex(_partition_identity).collect(),
        key=lambda value: value["partition"],
    )
    require([row["partition"] for row in records] == list(range(PARTITIONS)), "partition identity set drift")
    require(sum(row["sourceRows"] for row in records) == expected_source_rows, "source row census drift")
    require(sum(row["logicalRows"] for row in records) == 4 * expected_source_rows, "logical row census drift")
    for row in records:
        require(row["logicalRows"] == 4 * row["sourceRows"], "partition four-view census drift")
    if require_full_identity:
        observed = tuple(
            (
                row["partition"],
                row["sourceRows"],
                row["logicalRows"],
                row["view0RowIdSha256"],
            )
            for row in records
        )
        require(observed == EXPECTED_PARTITIONS, "full partition identity drift")
    return {
        "schemaVersion": "feelm-service-v1-b1-partition-identity/1",
        "partitions": records,
        "sourceRows": expected_source_rows,
        "logicalRows": 4 * expected_source_rows,
        "viewIds": list(VIEW_IDS),
        "viewWeight": VIEW_WEIGHT,
    }


def _cgroup_peak() -> dict[str, Any]:
    for candidate in (
        Path("/sys/fs/cgroup/memory.peak"),
        Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes"),
    ):
        try:
            if candidate.is_file():
                raw = candidate.read_text(encoding="utf-8").strip()
                if raw and raw != "max":
                    value = int(raw)
                    return {
                        "peakBytes": value,
                        "peakSource": str(candidate),
                        "resourceStatus": "PASS" if value <= MAX_MEMORY_BYTES else "RESOURCE_STOP",
                    }
        except (OSError, ValueError):
            continue
    return {"peakBytes": None, "peakSource": None, "resourceStatus": "UNKNOWN"}


def dry_run_action(args: argparse.Namespace) -> dict[str, Any]:
    verify_masked_contract(
        args.natural,
        args.masked,
        args.masked_manifest,
        args.views_manifest,
        args.masked_review,
    )
    natural_groups, natural_first = parquet_footer(args.natural, SOURCE_ROWS)
    masked_groups, masked_first = parquet_footer(args.masked, SOURCE_ROWS)
    require(natural_groups == 599, "natural row-group count drift")
    require(natural_first == NATURAL_RG0_ROWS, "natural RG0 count drift")
    require(masked_first == MASKED_RG0_ROWS, "masked RG0 count drift")
    temporary = args.scratch / f".dry-run-first-row-group-{uuid.uuid4().hex}"
    require(not temporary.exists(), "dry-run scratch collision")
    temporary.mkdir(parents=True)
    spark = None
    try:
        prepared = prepare_dry_run_parquets(args.natural, args.masked, temporary)
        spark = open_spark("service-v1-b1-dry-run", temporary)
        runtime = runtime_versions(spark)
        ordered = build_ordered_views(spark, prepared["natural"], prepared["masked"])
        try:
            identity = collect_identity(ordered, DRY_RUN_ROWS, require_full_identity=False)
        finally:
            ordered.unpersist()
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": "DRY_RUN_ONLY_NOT_PUBLISHED",
            "modelFitPerformed": False,
            "scorePerformed": False,
            "naturalRowGroups": natural_groups,
            "maskedRowGroups": masked_groups,
            **{key: value for key, value in prepared.items() if key not in {"natural", "masked"}},
            "logicalRows": identity["logicalRows"],
            "partitionCount": len(identity["partitions"]),
            "identity": identity,
            "runtimeVersions": runtime,
        }
    finally:
        try:
            if spark is not None:
                spark.stop()
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
            require(not temporary.exists(), "dry-run scratch cleanup failed")


def _validated_full_views(args: argparse.Namespace, app_name: str) -> tuple[Any, Any, dict[str, Any]]:
    verify_masked_contract(
        args.natural,
        args.masked,
        args.masked_manifest,
        args.views_manifest,
        args.masked_review,
    )
    verify_recipe(args.training_recipe)
    natural_groups, natural_first = parquet_footer(args.natural, SOURCE_ROWS)
    _, masked_first = parquet_footer(args.masked, SOURCE_ROWS)
    require(natural_groups == 599, "natural row-group count drift")
    require(natural_first == NATURAL_RG0_ROWS, "natural RG0 count drift")
    require(masked_first == MASKED_RG0_ROWS, "masked RG0 count drift")
    verify_full_row_id_axis(args.natural, SOURCE_ROWS)
    verify_full_row_id_axis(args.masked, SOURCE_ROWS)
    spark = open_spark(app_name, args.scratch)
    ordered = None
    try:
        ordered = build_ordered_views(spark, args.natural, args.masked)
        identity = collect_identity(ordered, SOURCE_ROWS, require_full_identity=True)
        return spark, ordered, identity
    except BaseException:
        try:
            if ordered is not None:
                ordered.unpersist()
        finally:
            spark.stop()
        raise


def preflight_action(args: argparse.Namespace) -> dict[str, Any]:
    spark = None
    ordered = None
    try:
        spark, ordered, identity = _validated_full_views(args, "service-v1-b1-full-preflight")
        runtime = runtime_versions(spark)
        write_json(args.output / "partition-identity.json", identity)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": "B1_FULL_PREFLIGHT_WORKER_COMPLETE",
            "modelFitPerformed": False,
            "scorePerformed": False,
            "sourceRows": SOURCE_ROWS,
            "logicalRows": LOGICAL_ROWS,
            "partitionCount": PARTITIONS,
            "runtimeVersions": runtime,
        }
    finally:
        try:
            if ordered is not None:
                ordered.unpersist()
        finally:
            if spark is not None:
                spark.stop()


def _normalise_params(values: dict[Any, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for param, value in values.items():
        name = getattr(param, "name", str(param))
        if isinstance(value, np.generic):
            value = value.item()
        result[name] = value
    return result


def _tree_splits(model: Any) -> list[tuple[int, float]]:
    splits: list[tuple[int, float]] = []

    def visit(node: Any) -> None:
        class_name = str(node.getClass().getName())
        if class_name == "org.apache.spark.ml.tree.LeafNode":
            return
        require(
            class_name == "org.apache.spark.ml.tree.InternalNode",
            f"unsupported Spark tree node class: {class_name}",
        )
        split = node.split()
        splits.append((int(split.featureIndex()), float(split.threshold())))
        visit(node.leftChild())
        visit(node.rightChild())

    for tree in model.trees:
        visit(tree._java_obj.rootNode())
    return splits


def fit_action(args: argparse.Namespace) -> dict[str, Any]:
    from pyspark.ml.linalg import Vectors
    from pyspark.ml.regression import GBTRegressor
    from pyspark.sql import functions as functions

    expected = verify_recipe(args.training_recipe)
    spark = None
    ordered = None
    started = time.monotonic()
    try:
        spark, ordered, identity = _validated_full_views(args, "service-v1-b1-gbt120-s339-fit")
        runtime = runtime_versions(spark)
        write_json(args.output / "partition-identity.json", identity)
        estimator = GBTRegressor(**expected)
        resolved = _normalise_params(estimator.extractParamMap())
        require(resolved == expected, f"resolved GBT parameter drift: {resolved}")
        write_json(args.output / "resolved-estimator.json", resolved)
        training = ordered.select("label", "features", "view_weight")
        require(training.count() == LOGICAL_ROWS, "training logical row count drift")
        fit_started = time.monotonic()
        model = estimator.fit(training)
        fit_seconds = time.monotonic() - fit_started
        require(len(model.trees) == 120, "native GBT tree count drift")
        native = args.output / "model" / "native"
        require(not native.exists(), "native model path already exists")
        model.write().save(str(native))

        base_row = ordered.where("view_id = 0").orderBy("row_id").select("features").first()
        require(base_row is not None, "threshold fixture base row unavailable")
        base = np.asarray(base_row.features.toArray(), dtype=np.float64)
        fixture_rows: list[np.ndarray] = []
        split_features: list[int] = []
        split_thresholds: list[float] = []
        for feature_index, threshold in _tree_splits(model):
            require(0 <= feature_index < 230 and math.isfinite(threshold), "invalid native tree split")
            for value in (np.nextafter(threshold, -np.inf), threshold, np.nextafter(threshold, np.inf)):
                row = base.copy()
                row[feature_index] = value
                fixture_rows.append(row)
                split_features.append(feature_index)
                split_thresholds.append(threshold)
        require(bool(fixture_rows), "no non-leaf GBT split found")
        fixture_matrix = np.vstack(fixture_rows)
        native_predictions = np.asarray(
            [float(model.predict(Vectors.dense(row))) for row in fixture_matrix], dtype=np.float64
        )
        np.savez_compressed(
            args.output / "threshold-fixtures.npz",
            features=fixture_matrix,
            predictions=native_predictions,
            indices=np.arange(230, dtype=np.int32),
            split_features=np.asarray(split_features, dtype=np.int32),
            split_thresholds=np.asarray(split_thresholds, dtype=np.float64),
        )

        aggregate = model.transform(training).select(
            functions.sum(
                functions.col("view_weight")
                * (functions.col("prediction") - functions.col("label")) ** 2
            ).alias("weightedSquaredError"),
            functions.sum("view_weight").alias("weightSum"),
        ).first()
        require(aggregate is not None and float(aggregate.weightSum) > 0.0, "weighted RMSE denominator invalid")
        weighted_rmse = math.sqrt(float(aggregate.weightedSquaredError) / float(aggregate.weightSum))
        require(math.isfinite(weighted_rmse), "weighted training RMSE is not finite")
        metrics = {
            "schemaVersion": "feelm-service-v1-b1-fit-metrics/1",
            "model": "GBT120_s339_B1",
            "sourceRows": SOURCE_ROWS,
            "logicalRows": LOGICAL_ROWS,
            "viewWeight": VIEW_WEIGHT,
            "weightedRawRmse": weighted_rmse,
            "treeCount": len(model.trees),
            "nonLeafSplitCount": len(fixture_rows) // 3,
            "thresholdFixtureRows": len(fixture_rows),
            "fitSeconds": fit_seconds,
            "workerSeconds": time.monotonic() - started,
            "portableParity": "PENDING_HOST_CHECK",
            "runtimeVersions": runtime,
        }
        write_json(args.output / "fit-metrics.json", metrics)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": "B1_MODEL_FIT_WORKER_COMPLETE",
            "modelFitPerformed": True,
            "scorePerformed": False,
            "treeCount": len(model.trees),
            "thresholdFixtureRows": len(fixture_rows),
            "weightedRawRmse": weighted_rmse,
            "runtimeVersions": runtime,
        }
    finally:
        try:
            if ordered is not None:
                ordered.unpersist()
        finally:
            if spark is not None:
                spark.stop()


def score_action(args: argparse.Namespace) -> dict[str, Any]:
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.regression import GBTRegressionModel

    parquet = pq.ParquetFile(args.score_input)
    try:
        expected_physical = feature_schema()
        require(parquet.schema_arrow == expected_physical, "physical score schema drift")
        require(parquet.metadata.num_rows == SCORE_ROWS, "physical score row count drift")
    finally:
        parquet.close()
    expected_row_ids, expected_uids = read_and_validate_score_axis(args.score_input)

    spark = open_spark("service-v1-b1-natural-score", args.scratch)
    try:
        runtime = runtime_versions(spark)
        projected = spark.read.schema(_spark_types(include_label=False)).parquet(str(args.score_input))
        assembler = VectorAssembler(
            inputCols=list(FEATURE_COLUMNS), outputCol="features", handleInvalid="error"
        )
        features = assembler.transform(projected).select("row_id", "uid", "features")
        analyzed = features._jdf.queryExecution().analyzed().toString()
        require("label" not in analyzed.lower(), "score analyzed plan exposes evaluation label")
        require(all(name in analyzed for name in FEATURE_COLUMNS), "score analyzed plan omits feature fields")
        plan_path = args.output / "score" / "analyzed-plan.txt"
        require(not plan_path.exists(), "score analyzed-plan output already exists")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(analyzed + "\n", encoding="utf-8")

        model = GBTRegressionModel.load(str(args.model_dir))
        require(len(model.trees) == 120, "scoring model tree count drift")
        frame = model.transform(features).select("row_id", "uid", "prediction").orderBy("row_id").toPandas()
        require(len(frame) == SCORE_ROWS, "prediction row count drift")
        row_ids = frame["row_id"].to_numpy(dtype=np.int64, copy=False)
        require(np.array_equal(row_ids, np.arange(SCORE_ROWS, dtype=np.int64)), "prediction row_id axis drift")
        predictions = frame["prediction"].to_numpy(dtype=np.float64, copy=False)
        require(np.isfinite(predictions).all(), "non-finite B1 prediction")
        table = pa.Table.from_arrays(
            [
                pa.array(row_ids, type=pa.int64()),
                pa.array(frame["uid"].to_numpy(dtype=np.int32, copy=False), type=pa.int32()),
                pa.array(predictions, type=pa.float64()),
            ],
            schema=score_output_schema(),
        )
        prediction_path = args.output / "score" / "predictions.parquet"
        require(not prediction_path.exists(), "prediction output already exists")
        temporary_path = prediction_path.with_name(f".{prediction_path.name}.tmp-{uuid.uuid4().hex}")
        require(not temporary_path.exists(), "prediction temporary output collision")
        try:
            pq.write_table(table, temporary_path, compression="zstd", write_statistics=True)
            written = pq.ParquetFile(temporary_path)
            try:
                require(written.schema_arrow == score_output_schema(), "written prediction schema drift")
                require(written.metadata.num_rows == SCORE_ROWS, "written prediction row count drift")
            finally:
                written.close()
            with temporary_path.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary_path, prediction_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        written_summary = validate_prediction_file(prediction_path, expected_row_ids, expected_uids)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": "B1_NATURAL_SCORE_WORKER_COMPLETE",
            "modelFitPerformed": False,
            "scorePerformed": True,
            **written_summary,
            "runtimeVersions": runtime,
        }
    finally:
        spark.stop()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    actions = result.add_subparsers(dest="action", required=True)

    def masked_inputs(command: argparse.ArgumentParser) -> None:
        command.add_argument("--natural", type=Path, required=True)
        command.add_argument("--masked", type=Path, required=True)
        command.add_argument("--masked-manifest", type=Path, required=True)
        command.add_argument("--views-manifest", type=Path, required=True)
        command.add_argument("--masked-review", type=Path, required=True)
        command.add_argument("--scratch", type=Path, required=True)

    dry = actions.add_parser("dry-run-first-row-group")
    masked_inputs(dry)

    preflight = actions.add_parser("preflight")
    masked_inputs(preflight)
    preflight.add_argument("--training-recipe", type=Path, required=True)
    preflight.add_argument("--output", type=Path, required=True)

    fit = actions.add_parser("fit")
    masked_inputs(fit)
    fit.add_argument("--training-recipe", type=Path, required=True)
    fit.add_argument("--output", type=Path, required=True)

    score = actions.add_parser("score")
    score.add_argument("--score-input", type=Path, required=True)
    score.add_argument("--model-dir", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--scratch", type=Path, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    started = time.monotonic()
    if args.action == "dry-run-first-row-group":
        output = dry_run_action(args)
    elif args.action == "preflight":
        output = preflight_action(args)
    elif args.action == "fit":
        output = fit_action(args)
    elif args.action == "score":
        output = score_action(args)
    else:
        raise ValueError(f"unsupported action: {args.action}")
    output["workerSeconds"] = time.monotonic() - started
    output["resourceObservation"] = _cgroup_peak()
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
