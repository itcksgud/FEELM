"""Fit one sparse FM/linear profile in the pinned Spark runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path

from pyspark import StorageLevel
from pyspark.ml.linalg import VectorUDT, Vectors
from pyspark.ml.regression import FMRegressor, LinearRegression
from pyspark.sql import SparkSession, functions as F


def file_pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def tree_digest(path: Path) -> str:
    files = {str(item.relative_to(path)).replace("\\", "/"): file_pin(item)
             for item in sorted(path.rglob("*")) if item.is_file()}
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cgroup_peak() -> dict:
    for path in (Path("/sys/fs/cgroup/memory.peak"), Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes")):
        if path.exists():
            return {"peak_ram_bytes": int(path.read_text().strip()), "peak_ram_source": str(path)}
    return {"peak_ram_bytes": "NOT_EXPOSED", "peak_ram_source": "NOT_EXPOSED"}


def make_vectorizer(profile_indices: list[int]):
    remap = {int(source): target for target, source in enumerate(profile_indices)}
    size = len(profile_indices)

    @F.udf(returnType=VectorUDT())
    def vectorize(indices, values):
        pairs = [(remap[int(index)], float(value)) for index, value in zip(indices or [], values or []) if int(index) in remap and float(value) != 0.0]
        pairs.sort()
        return Vectors.sparse(size, [pair[0] for pair in pairs], [pair[1] for pair in pairs])

    return vectorize


def fit(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    if prepared["status"] != "PASS" or not prepared["final_test"].endswith("NOT_WRITTEN"):
        raise RuntimeError("prepared input is not PASS or FINAL_TEST is not sealed")
    if args.profile not in config["profiles"] or args.profile not in schema["profiles"]:
        raise RuntimeError(f"unknown profile: {args.profile}")
    for filename in ("train-targets.parquet", "validation-targets.parquet", "validation-candidates.parquet",
                     "feature-schema.json", "split-distribution-report.json"):
        expected = prepared["files"][filename]
        if file_pin(args.prepared_root / filename) != expected:
            raise RuntimeError(f"prepared artifact hash mismatch: {filename}")
    runtime_image_id = os.environ.get("FM_RUNTIME_IMAGE_ID")
    if runtime_image_id != config["runtime"]["image_id"]:
        raise RuntimeError("runtime image ID mismatch")
    args.output_root.mkdir(parents=True)
    spark = (SparkSession.builder.appName(f"fm-zero-n-{args.profile}-{args.seed}")
             .master(config["runtime"]["master"])
             .config("spark.sql.adaptive.enabled", "false")
             .config("spark.sql.shuffle.partitions", "12")
             .config("spark.default.parallelism", "12")
             .config("spark.driver.maxResultSize", "2g")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    if spark.version != config["runtime"]["spark_version"]:
        raise RuntimeError(f"Spark version mismatch: {spark.version}")
    profile = schema["profiles"][args.profile]
    vectorizer = make_vectorizer(profile["indices"])

    def read(name: str):
        return spark.read.parquet(str(args.prepared_root / name)).withColumn("features", vectorizer("feature_indices", "feature_values"))

    train = read("train-targets.parquet").select("uid", "label", "features").persist(StorageLevel.MEMORY_AND_DISK)
    validation = read("validation-targets.parquet").persist(StorageLevel.MEMORY_AND_DISK)
    training_rows = train.count()
    if training_rows != int(prepared["rows"]["train_targets"]):
        raise RuntimeError("training row count mismatch")
    fit_started = time.monotonic()
    if args.profile == "sparse_linear_only":
        estimator_kind = "LINEAR_ONLY_BASELINE"
        estimator = LinearRegression(featuresCol="features", labelCol="label", predictionCol="raw_prediction",
                                     **config["linear_estimator"])
    else:
        estimator_kind = "FACTORIZATION_MACHINE_WITH_PAIRWISE_INTERACTIONS"
        estimator = FMRegressor(featuresCol="features", labelCol="label", predictionCol="raw_prediction",
                                seed=args.seed, **config["estimator"])
    model = estimator.fit(train)
    fit_seconds = time.monotonic() - fit_started
    model_path = args.output_root / "model"
    model.save(str(model_path))

    bounds = config["prediction_bounds"]

    def predictions(frame):
        scored = model.transform(frame.filter("supported = true"))
        invalid = scored.filter(F.isnan("raw_prediction") | (F.abs("raw_prediction") == float("inf"))).count()
        if invalid:
            raise RuntimeError(f"NaN/Inf predictions: {invalid}")
        scored = scored.withColumn("prediction", F.greatest(F.lit(float(bounds[0])), F.least(F.lit(float(bounds[1])), F.col("raw_prediction"))))
        unsupported = (frame.filter("supported = false")
                       .withColumn("raw_prediction", F.lit(None).cast("double"))
                       .withColumn("prediction", F.lit(None).cast("double")))
        return scored.unionByName(unsupported, allowMissingColumns=True)

    output_columns = [
        "episode_id", "role", "uid", "target_movie_id", "candidate_movie_id", "prediction_at",
        "n", "n_bucket", "total_history_count", "supported_history_count", "label", "candidate_rank",
        "label_state", "is_target", "candidate_genres", "supported", "unsupported_reason",
        "raw_prediction", "prediction",
    ]
    inference_started = time.monotonic()
    target_predictions = predictions(validation).select(*output_columns).persist(StorageLevel.DISK_ONLY)
    target_count = target_predictions.count()
    target_predictions.write.mode("errorifexists").parquet(str(args.output_root / "validation-target-predictions.parquet"))
    target_inference_seconds = time.monotonic() - inference_started

    candidate_started = time.monotonic()
    candidates = read("validation-candidates.parquet")
    candidate_predictions = predictions(candidates).select(*output_columns).persist(StorageLevel.DISK_ONLY)
    candidate_count = candidate_predictions.count()
    candidate_predictions.write.mode("errorifexists").parquet(str(args.output_root / "validation-candidate-predictions.parquet"))
    candidate_inference_seconds = time.monotonic() - candidate_started
    if target_count != int(prepared["rows"]["validation_targets"]) or candidate_count != int(prepared["rows"]["validation_candidates"]):
        raise RuntimeError("prediction row count mismatch; rows may not be silently removed")

    train_scored = predictions(train.withColumn("supported", F.lit(True)).withColumn("unsupported_reason", F.lit(None).cast("string")))
    train_error = train_scored.select(
        F.avg(F.pow(F.col("prediction") - F.col("label"), 2)).alias("mse"),
        F.avg(F.abs(F.col("prediction") - F.col("label"))).alias("mae"),
    ).first()
    model_params = {param.name: model.getOrDefault(param) for param in model.params if model.isDefined(param)}
    metrics = {
        "schema_version": 1,
        "status": "PASS",
        "profile": args.profile,
        "estimator_kind": estimator_kind,
        "interaction_enabled": args.profile != "sparse_linear_only",
        "seed": args.seed,
        "spark_version": spark.version,
        "runtime_image_id": runtime_image_id,
        "feature_count": len(profile["indices"]),
        "feature_ordered_names_sha256": hashlib.sha256("\n".join(profile["ordered_names"]).encode()).hexdigest(),
        "training_rows": training_rows,
        "validation_target_rows": target_count,
        "validation_candidate_rows": candidate_count,
        "training_mse": float(train_error.mse),
        "training_mae": float(train_error.mae),
        "fit_seconds": fit_seconds,
        "target_inference_seconds": target_inference_seconds,
        "candidate_inference_seconds": candidate_inference_seconds,
        "cpu_inference_rows_per_second": candidate_count / candidate_inference_seconds,
        "total_seconds": time.monotonic() - started,
        "model_size_bytes": tree_size(model_path),
        "parameters": model_params,
        "actual_iterations": "NOT_EXPOSED",
        "objective_loss_curve": "NOT_EXPOSED",
        "converged": "NOT_EXPOSED",
        "weight_col_behavior": "NOT_USED; FM public wrapper exposes weightCol but the pinned FM training path does not establish weighted fitting",
        "resource": {**cgroup_peak(), "disk_spill_bytes": "NOT_EXPOSED"},
        "prepared_manifest": file_pin(args.prepared_root / "manifest.json"),
        "feature_schema": file_pin(args.prepared_root / "feature-schema.json"),
        "model_artifact_sha256": tree_digest(model_path),
        "target_predictions_artifact": tree_digest(args.output_root / "validation-target-predictions.parquet"),
        "candidate_predictions_artifact": tree_digest(args.output_root / "validation-candidate-predictions.parquet"),
        "failed_rows": 0,
        "final_test_opened": False,
        "gpu_training": False,
    }
    (args.output_root / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    target_predictions.unpersist()
    candidate_predictions.unpersist()
    train.unpersist()
    validation.unpersist()
    spark.stop()
    print(json.dumps(metrics, indent=2, ensure_ascii=False), flush=True)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--config", type=Path, required=True)
    fit(parser.parse_args())


if __name__ == "__main__":
    main()
