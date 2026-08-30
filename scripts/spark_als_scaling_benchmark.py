#!/usr/bin/env python3
"""Measure one reproducible Spark ALS topology run.

The orchestrator is intentionally separate from this process so each measured
topology can start from a fresh Spark application.  The result contains only
aggregate counts and timing; MovieLens user/movie identifiers are never
written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil


PROTOCOL_VERSION = "spark-als-scaling-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--master", required=True)
    parser.add_argument("--topology-id", required=True)
    parser.add_argument("--expected-workers", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-modulus", type=int, default=100)
    parser.add_argument("--sample-buckets", type=int, default=20)
    parser.add_argument("--input-partitions", type=int, default=32)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--max-iter", type=int, default=3)
    parser.add_argument("--reg-param", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--shuffle-partitions", type=int, default=32)
    parser.add_argument("--driver-memory", default="6g")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.expected_workers < 1:
        raise ValueError("expected-workers must be positive")
    if args.sample_modulus < 1 or not 1 <= args.sample_buckets <= args.sample_modulus:
        raise ValueError("sample buckets must be within the positive modulus")
    if args.input_partitions < 1 or args.rank < 1 or args.max_iter < 1:
        raise ValueError("partitions, rank, and iterations must be positive")
    if args.reg_param < 0:
        raise ValueError("reg-param cannot be negative")
    for path in (args.train, args.validation):
        if not path.is_file():
            raise FileNotFoundError(path)
    output = args.output.resolve()
    if output in {args.train.resolve(), args.validation.resolve()}:
        raise ValueError("output must not overwrite a benchmark input")


def protocol(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "algorithm": "Spark ML explicit ALS",
        "model": {
            "rank": args.rank,
            "max_iter": args.max_iter,
            "reg_param": args.reg_param,
            "seed": args.seed,
            "cold_start_strategy": "drop",
            "nonnegative": False,
            "implicit_prefs": False,
            "num_user_blocks": args.input_partitions,
            "num_item_blocks": args.input_partitions,
        },
        "input": {
            "split": "global-time-v1 Train and Validation",
            "deterministic_filter": "pmod(xxhash64(user_id,movie_id,timestamp), sample_modulus) < sample_buckets",
            "sample_modulus": args.sample_modulus,
            "sample_buckets": args.sample_buckets,
            "repartition_by": "userId",
            "input_partitions": args.input_partitions,
        },
        "measurement": {
            "materialize_before_fit": True,
            "fit_seconds_excludes_input_materialization": True,
            "quality_guard": "Validation prediction coverage and RMSE are observed for every topology",
            "raw_ids_written": False,
        },
    }


def canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    args = parse_args()
    validate_args(args)

    from pyspark import StorageLevel
    from pyspark.ml.evaluation import RegressionEvaluator
    from pyspark.ml.recommendation import ALS
    from pyspark.sql import SparkSession, functions as F

    spec = protocol(args)
    process = psutil.Process()
    application_started = time.perf_counter()
    spark = (
        SparkSession.builder.master(args.master)
        .appName(f"feelm-{PROTOCOL_VERSION}-{args.topology_id}")
        .config("spark.driver.memory", args.driver_memory)
        .config("spark.sql.shuffle.partitions", str(args.shuffle_partitions))
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.adaptive.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    try:
        def load(path: Path):
            return (
                spark.read.parquet(str(path.resolve()))
                .where(
                    F.pmod(F.xxhash64("user_id", "movie_id", "timestamp"), F.lit(args.sample_modulus))
                    < F.lit(args.sample_buckets)
                )
                .select(
                    F.col("user_id").cast("int").alias("userId"),
                    F.col("movie_id").cast("int").alias("movieId"),
                    F.col("rating").cast("float").alias("rating"),
                )
                .repartition(args.input_partitions, "userId")
                .persist(StorageLevel.MEMORY_AND_DISK)
            )

        materialize_started = time.perf_counter()
        train = load(args.train)
        validation = load(args.validation)
        train_rows = train.count()
        validation_rows = validation.count()
        materialize_seconds = time.perf_counter() - materialize_started
        if train_rows == 0 or validation_rows == 0:
            raise RuntimeError("deterministic sample produced an empty split")

        als = ALS(
            userCol="userId",
            itemCol="movieId",
            ratingCol="rating",
            rank=args.rank,
            maxIter=args.max_iter,
            regParam=args.reg_param,
            seed=args.seed,
            coldStartStrategy="drop",
            implicitPrefs=False,
            nonnegative=False,
            numUserBlocks=args.input_partitions,
            numItemBlocks=args.input_partitions,
            checkpointInterval=-1,
        )
        fit_started = time.perf_counter()
        model = als.fit(train)
        fit_seconds = time.perf_counter() - fit_started

        evaluation_started = time.perf_counter()
        predictions = model.transform(validation).select("rating", "prediction").persist(
            StorageLevel.MEMORY_AND_DISK
        )
        predicted_rows = predictions.count()
        rmse = RegressionEvaluator(
            metricName="rmse", labelCol="rating", predictionCol="prediction"
        ).evaluate(predictions)
        evaluation_seconds = time.perf_counter() - evaluation_started

        # The Scala map is a Py4J JavaObject rather than a Python iterable on
        # Spark 4.2.  size() is stable across local and standalone masters; the
        # map includes the driver, hence the subtraction.
        executor_memory_entries = int(
            spark.sparkContext._jsc.sc().getExecutorMemoryStatus().size()  # noqa: SLF001
        )
        observed_executors = max(0, executor_memory_entries - 1)
        worker_gate = observed_executors == args.expected_workers if args.master.startswith("spark://") else None
        result = {
            "schema_version": 1,
            "run_id": f"{PROTOCOL_VERSION}-{args.topology_id}",
            "measured_at_utc": datetime.now(timezone.utc).isoformat(),
            "topology": {
                "id": args.topology_id,
                "master": args.master,
                "expected_workers": args.expected_workers,
                "observed_remote_executors": observed_executors,
                "worker_count_gate": worker_gate,
                "default_parallelism": spark.sparkContext.defaultParallelism,
            },
            "protocol": spec,
            "protocol_sha256": canonical_hash(spec),
            "input_aggregates": {
                "train_rows": train_rows,
                "validation_rows": validation_rows,
            },
            "quality": {
                "validation_predicted_rows": predicted_rows,
                "prediction_coverage": predicted_rows / validation_rows,
                "rmse": rmse,
            },
            "timing_seconds": {
                "materialize": materialize_seconds,
                "als_fit": fit_seconds,
                "validation": evaluation_seconds,
                "application_total": time.perf_counter() - application_started,
            },
            "runtime": {
                "python": platform.python_version(),
                "pyspark": __import__("pyspark").__version__,
                "os": platform.system(),
                "logical_cpu_count": os.cpu_count(),
                "process_rss_bytes_observed": process.memory_info().rss,
            },
            "safe_to_track": True,
        }
        if worker_gate is False:
            raise RuntimeError(
                f"topology expected {args.expected_workers} workers but observed {observed_executors}"
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "topology": args.topology_id,
                    "workers": observed_executors,
                    "train_rows": train_rows,
                    "fit_seconds": round(fit_seconds, 3),
                    "rmse": round(rmse, 6),
                },
                sort_keys=True,
            )
        )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
