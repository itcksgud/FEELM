"""Run one pinned Spark GBT profile against prepared 0-N Parquet rows."""

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
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    if prepared["status"] != "PASS" or prepared["final_test"] != "NOT_WRITTEN":
        raise RuntimeError("prepared input is not a validation-only PASS bundle")
    if args.profile not in config["profiles"] or args.profile not in schema["profiles"]:
        raise RuntimeError("profile is not preregistered")
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    args.output_root.mkdir(parents=True)

    started = time.monotonic()
    spark = SparkSession.builder.appName(f"S15P21E106-622-{args.profile}").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    if spark.version != config["runtime"]["spark_version"]:
        raise RuntimeError(f"Spark version mismatch: {spark.version}")
    columns = schema["profiles"][args.profile]
    safe_columns = [f"feature_{index:03d}" for index in range(len(columns))]
    assembler = VectorAssembler(inputCols=safe_columns, outputCol="features", handleInvalid="error")

    def add_safe_feature_columns(frame):
        # Spark resolves dots in a column name as nested-field separators. Keep the
        # contracted names in Parquet/metrics and alias only inside this worker.
        aliases = [F.col(f"`{name.replace('`', '``')}`").alias(safe)
                   for name, safe in zip(columns, safe_columns)]
        return frame.select("*", *aliases)

    train_raw = spark.read.parquet(str(args.prepared_root / "train-targets.parquet"))
    validation_raw = spark.read.parquet(str(args.prepared_root / "validation-targets.parquet"))
    candidate_raw = spark.read.parquet(str(args.prepared_root / "validation-candidates.parquet"))
    train = assembler.transform(add_safe_feature_columns(train_raw)).select(
        "label", "sample_weight", "features"
    ).repartition(8)
    train = train.persist(StorageLevel.DISK_ONLY)
    training_rows = train.count()
    if training_rows != prepared["rows"]["train_targets"]:
        raise RuntimeError("training row count mismatch")
    estimator = GBTRegressor(
        labelCol="label",
        featuresCol="features",
        predictionCol="prediction",
        weightCol="sample_weight",
        seed=config["seed"],
        **config["estimator"],
    )
    fit_started = time.monotonic()
    model = estimator.fit(train)
    fit_seconds = time.monotonic() - fit_started
    model.write().save(str(args.output_root / "model" / "native"))
    loaded = GBTRegressionModel.load(str(args.output_root / "model" / "native"))

    target_projection = ["episode_id", "uid", "evaluation_split", "target_movie_id", "prediction_at", "n", "n_bucket",
                         "total_history_count", "provided_history_count", "supported_history_count", "is_full_history", "is_controlled_prefix",
                         "label", "sample_weight", "prediction"]
    for name in ("policy.popular_count_score", "policy.popular_bayes_score"):
        if name in validation_raw.columns:
            target_projection.append(F.col(f"`{name}`").alias(name))
    target_predictions = loaded.transform(assembler.transform(add_safe_feature_columns(validation_raw))).select(
        *target_projection
    )
    target_predictions.write.mode("error").parquet(str(args.output_root / "validation-target-predictions.parquet"))
    candidate_projection = ["episode_id", "uid", "evaluation_split", "target_movie_id", "candidate_movie_id", "prediction_at",
                            "n", "n_bucket", "total_history_count", "provided_history_count", "supported_history_count",
                            "is_full_history", "is_controlled_prefix", "candidate_rank", "label_state", "is_target", "label",
                            "prediction"]
    for name in ("policy.popular_count_score", "policy.popular_bayes_score"):
        if name in candidate_raw.columns:
            candidate_projection.append(F.col(f"`{name}`").alias(name))
    candidate_predictions = loaded.transform(assembler.transform(add_safe_feature_columns(candidate_raw))).select(
        *candidate_projection
    )
    candidate_predictions.write.mode("error").parquet(str(args.output_root / "validation-candidate-predictions.parquet"))

    target_metric = target_predictions.select(
        F.sqrt(F.avg((F.col("prediction") - F.col("label")) ** 2)).alias("rmse"),
        F.avg(F.abs(F.col("prediction") - F.col("label"))).alias("mae"),
        F.count("*").alias("rows"),
    ).first()
    train_rmse = model.transform(train).select(
        F.sqrt(F.avg((F.col("prediction") - F.col("label")) ** 2)).alias("rmse")
    ).first().rmse
    resource = {}
    for path in (Path("/sys/fs/cgroup/memory.peak"), Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes")):
        if path.exists():
            resource = {"peak_memory_bytes": int(path.read_text().strip()), "peak_memory_source": str(path)}
            break
    metrics = {
        "status": "PASS",
        "profile": args.profile,
        "feature_count": len(columns),
        "feature_names": columns,
        "spark_version": spark.version,
        "estimator": config["estimator"],
        "seed": config["seed"],
        "training_rows": training_rows,
        "fit_seconds": fit_seconds,
        "total_seconds": time.monotonic() - started,
        "tree_count": len(model.trees),
        "total_nodes": int(model.totalNumNodes),
        "training_rmse": float(train_rmse),
        "validation_target_rows": int(target_metric.rows),
        "validation_target_rmse": float(target_metric.rmse),
        "validation_target_mae": float(target_metric.mae),
        "resource": resource,
        "input_manifest": pin(args.prepared_root / "manifest.json"),
        "feature_schema": pin(args.prepared_root / "feature-schema.json"),
        "runtime_source": {
            "worker": pin(Path(__file__)),
            "config": pin(args.config),
            "source_bundle_digest": prepared["source_bundle"]["digest"],
        },
        "final_test_opened": False,
    }
    (args.output_root / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    train.unpersist()
    spark.stop()
    print(json.dumps(metrics, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
