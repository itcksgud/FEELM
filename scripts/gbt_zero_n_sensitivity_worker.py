"""Fit one alternate-seed GBT and write validation target predictions only."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from pyspark import StorageLevel
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.regression import GBTRegressor, GBTRegressionModel
from pyspark.sql import SparkSession, functions as F


def pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    if prepared["status"] != "PASS" or prepared["final_test"] != "NOT_WRITTEN":
        raise RuntimeError("prepared input is not a validation-only PASS bundle")
    if args.profile not in schema["profiles"]:
        raise RuntimeError("unknown feature profile")
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    args.output_root.mkdir(parents=True)

    started = time.monotonic()
    spark = SparkSession.builder.appName(f"S15P21E106-622-sensitivity-{args.seed}").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    if spark.version != config["runtime"]["spark_version"]:
        raise RuntimeError(f"Spark version mismatch: {spark.version}")
    columns = schema["profiles"][args.profile]
    safe = [f"feature_{index:03d}" for index in range(len(columns))]
    assembler = VectorAssembler(inputCols=safe, outputCol="features", handleInvalid="error")

    def transform(frame):
        aliases = [F.col(f"`{name.replace('`', '``')}`").alias(alias)
                   for name, alias in zip(columns, safe)]
        return assembler.transform(frame.select("*", *aliases))

    train = transform(spark.read.parquet(str(args.prepared_root / "train-targets.parquet"))).select(
        "label", "sample_weight", "features"
    ).repartition(8).persist(StorageLevel.DISK_ONLY)
    rows = train.count()
    if rows != prepared["rows"]["train_targets"]:
        raise RuntimeError("training row count mismatch")
    estimator = GBTRegressor(
        labelCol="label", featuresCol="features", predictionCol="prediction",
        weightCol="sample_weight", seed=args.seed, **config["estimator"],
    )
    fit_started = time.monotonic()
    model = estimator.fit(train)
    fit_seconds = time.monotonic() - fit_started
    model.write().save(str(args.output_root / "model" / "native"))
    loaded = GBTRegressionModel.load(str(args.output_root / "model" / "native"))
    validation = transform(spark.read.parquet(str(args.prepared_root / "validation-targets.parquet")))
    projection = [
        "episode_id", "uid", "target_movie_id", "prediction_at", "n", "n_bucket",
        "total_history_count", "supported_history_count", "is_full_history", "label", "prediction",
    ]
    predictions = loaded.transform(validation).select(*projection)
    predictions.write.mode("error").parquet(
        str(args.output_root / "validation-target-predictions.parquet")
    )
    metric = predictions.select(
        F.avg((F.col("prediction") - F.col("label")) ** 2).alias("mse"),
        F.avg(F.abs(F.col("prediction") - F.col("label"))).alias("mae"),
        F.count("*").alias("rows"),
    ).first()
    resource = {}
    for path in (Path("/sys/fs/cgroup/memory.peak"), Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes")):
        if path.exists():
            resource = {"peak_memory_bytes": int(path.read_text().strip()), "peak_memory_source": str(path)}
            break
    report = {
        "status": "PASS",
        "profile": args.profile,
        "seed": args.seed,
        "training_rows": rows,
        "validation_rows": int(metric.rows),
        "validation_row_mse": float(metric.mse),
        "validation_row_mae": float(metric.mae),
        "fit_seconds": fit_seconds,
        "total_seconds": time.monotonic() - started,
        "tree_count": len(model.trees),
        "resource": resource,
        "prepared_manifest": pin(args.prepared_root / "manifest.json"),
        "feature_schema": pin(args.prepared_root / "feature-schema.json"),
        "worker": pin(Path(__file__)),
        "config": pin(args.config),
        "final_test_opened": False,
    }
    (args.output_root / "metrics.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    train.unpersist()
    spark.stop()
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
