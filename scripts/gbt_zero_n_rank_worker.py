"""Fit a pairwise user/request GBT ranking objective on the frozen 0-N rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from pyspark import StorageLevel
from pyspark.ml.classification import GBTClassificationModel, GBTClassifier
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.functions import vector_to_array
from pyspark.sql import SparkSession, Window, functions as F


def pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def q(alias: str, name: str):
    return F.col(f"{alias}.`{name.replace('`', '``')}`")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--completion-config", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(args.base_config.read_text(encoding="utf-8"))
    completion = json.loads(args.completion_config.read_text(encoding="utf-8"))
    prepared = json.loads((args.prepared_root / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads((args.prepared_root / "feature-schema.json").read_text(encoding="utf-8"))
    profile = completion["rank_profile"]
    if prepared["status"] != "PASS" or prepared["final_test"] != "NOT_WRITTEN":
        raise RuntimeError("prepared input is not a validation-only PASS bundle")
    if profile not in schema["profiles"]:
        raise RuntimeError("rank profile is absent from feature schema")
    if args.output_root.exists():
        raise FileExistsError(f"output already exists: {args.output_root}")
    args.output_root.mkdir(parents=True)

    started = time.monotonic()
    spark = SparkSession.builder.appName("S15P21E106-622-pairwise-rank").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    if spark.version != base["runtime"]["spark_version"]:
        raise RuntimeError(f"Spark version mismatch: {spark.version}")
    columns = schema["profiles"][profile]
    safe = [f"diff_{index:03d}" for index in range(len(columns))]
    assembler = VectorAssembler(inputCols=safe, outputCol="features", handleInvalid="error")

    raw_train = spark.read.parquet(str(args.prepared_root / "train-targets.parquet"))
    left = raw_train.alias("l")
    right = raw_train.alias("r")
    condition = (
        (F.col("l.uid") == F.col("r.uid"))
        & (F.col("l.prediction_at") == F.col("r.prediction_at"))
        & (F.col("l.n") == F.col("r.n"))
        & (F.col("l.target_movie_id") < F.col("r.target_movie_id"))
        & (F.col("l.label") != F.col("r.label"))
    )
    joined = left.join(right, condition, "inner")
    forward = joined.select(
        F.col("l.uid").alias("uid"),
        (F.col("l.label") > F.col("r.label")).cast("double").alias("label"),
        *[(q("l", name) - q("r", name)).alias(alias) for name, alias in zip(columns, safe)],
    )
    reverse = joined.select(
        F.col("l.uid").alias("uid"),
        (F.col("r.label") > F.col("l.label")).cast("double").alias("label"),
        *[(q("r", name) - q("l", name)).alias(alias) for name, alias in zip(columns, safe)],
    )
    pair_rows = forward.unionByName(reverse)
    pair_rows = pair_rows.withColumn("pair_count", F.count("*").over(Window.partitionBy("uid")))
    pair_rows = pair_rows.withColumn("pair_weight", F.lit(1.0) / F.col("pair_count"))
    train = assembler.transform(pair_rows).select("label", "pair_weight", "features")
    train = train.repartition(8).persist(StorageLevel.DISK_ONLY)
    training_pairs = train.count()
    label_count = train.select("label").distinct().count()
    if training_pairs == 0 or label_count != 2:
        raise RuntimeError("pairwise training requires both preference labels")

    estimator = GBTClassifier(
        labelCol="label", featuresCol="features", weightCol="pair_weight",
        predictionCol="class_prediction",
        seed=base["seed"], **completion["rank_estimator"],
    )
    fit_started = time.monotonic()
    model = estimator.fit(train)
    fit_seconds = time.monotonic() - fit_started
    model.write().save(str(args.output_root / "model" / "native"))
    loaded = GBTClassificationModel.load(str(args.output_root / "model" / "native"))

    candidates = spark.read.parquet(str(args.prepared_root / "validation-candidates.parquet"))
    anchor_count = int(completion["rank_anchor_count"])
    candidate = candidates.alias("c")
    anchor = candidates.filter(F.col("candidate_rank") < anchor_count + 1).alias("a")
    comparison = candidate.join(
        anchor,
        (F.col("c.episode_id") == F.col("a.episode_id"))
        & (F.col("c.candidate_movie_id") != F.col("a.candidate_movie_id")),
        "inner",
    ).select(
        *[F.col(f"c.{name}").alias(name) for name in (
            "episode_id", "uid", "target_movie_id", "candidate_movie_id", "prediction_at",
            "n", "n_bucket", "total_history_count", "provided_history_count", "supported_history_count",
            "is_full_history", "is_controlled_prefix",
            "candidate_rank", "label_state", "is_target", "label",
        )],
        F.col("a.candidate_rank").alias("anchor_rank"),
        *[(q("c", name) - q("a", name)).alias(alias) for name, alias in zip(columns, safe)],
    )
    anchor_window = Window.partitionBy("episode_id", "candidate_movie_id").orderBy("anchor_rank")
    comparison = comparison.withColumn("anchor_order", F.row_number().over(anchor_window)).filter(
        F.col("anchor_order") <= anchor_count
    )
    scored_pairs = loaded.transform(assembler.transform(comparison)).select(
        "episode_id", "uid", "target_movie_id", "candidate_movie_id", "prediction_at",
        "n", "n_bucket", "total_history_count", "provided_history_count", "supported_history_count",
        "is_full_history", "is_controlled_prefix",
        "candidate_rank", "label_state", "is_target", "label",
        vector_to_array("probability")[1].alias("pairwise_probability"),
    )
    group_columns = [
        "episode_id", "uid", "target_movie_id", "candidate_movie_id", "prediction_at",
        "n", "n_bucket", "total_history_count", "provided_history_count", "supported_history_count",
        "is_full_history", "is_controlled_prefix",
        "candidate_rank", "label_state", "is_target", "label",
    ]
    scores = scored_pairs.groupBy(*group_columns).agg(
        F.avg("pairwise_probability").alias("prediction"),
        F.count("*").alias("anchor_comparisons"),
    )
    expected_candidates = candidates.count()
    scored_candidates = scores.count()
    if expected_candidates != scored_candidates:
        raise RuntimeError("pairwise scorer did not produce exactly one score per candidate")
    invalid_anchor_counts = scores.filter(F.col("anchor_comparisons") != anchor_count).count()
    if invalid_anchor_counts:
        raise RuntimeError("pairwise scorer did not use the configured anchor count for every candidate")
    scores.write.mode("error").parquet(
        str(args.output_root / "validation-candidate-predictions.parquet")
    )
    scores.filter(F.col("is_target")).write.mode("error").parquet(
        str(args.output_root / "validation-target-rank-predictions.parquet")
    )
    resource = {}
    for path in (Path("/sys/fs/cgroup/memory.peak"), Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes")):
        if path.exists():
            resource = {"peak_memory_bytes": int(path.read_text().strip()), "peak_memory_source": str(path)}
            break
    metrics = {
        "status": "PASS",
        "objective": "PAIRWISE_USER_PREDICTION_AT_N_PREFERENCE",
        "profile": profile,
        "seed": base["seed"],
        "feature_count": len(columns),
        "training_pairs": training_pairs,
        "anchor_count": anchor_count,
        "scored_candidates": scored_candidates,
        "fit_seconds": fit_seconds,
        "total_seconds": time.monotonic() - started,
        "tree_count": len(model.trees),
        "total_nodes": int(model.totalNumNodes),
        "resource": resource,
        "prepared_manifest": pin(args.prepared_root / "manifest.json"),
        "feature_schema": pin(args.prepared_root / "feature-schema.json"),
        "worker": pin(Path(__file__)),
        "base_config": pin(args.base_config),
        "completion_config": pin(args.completion_config),
        "rating_prediction_available": False,
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
