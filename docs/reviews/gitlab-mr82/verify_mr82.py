"""Independent, temporary checks of GitLab MR 82; no production data or writes."""

from __future__ import annotations

import contextlib
import io
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from pyspark.sql import SparkSession, functions as F

from batch.train.inputs import validate_ratings, validate_service_movie_map
from batch.train.settings import AlsJobSettings
from data.pipelines.movie_id_mapping.io import write_pipeline
from data.pipelines.movie_id_mapping.pipeline import build_pipeline
from tests.fixtures.als_baseline import (
    RATING_ROWS,
    RATINGS_SCHEMA,
    SERVICE_MOVIE_MAP_ROWS,
    SERVICE_MOVIE_MAP_SCHEMA,
)


def verify_settings() -> None:
    for label, environ, argv in [
        ("invalid env seed + valid CLI", {"RECOMMENDATION_ALS_SEED": "invalid"}, ["--seed", "11"]),
        ("invalid env ratio + valid CLI", {"RECOMMENDATION_TRAIN_RATIO": "invalid"}, ["--train-ratio", "0.8"]),
        ("NaN regParam", {}, ["--als-config", "64:nan:10"]),
        ("infinite regParam", {}, ["--als-config", "64:inf:10"]),
    ]:
        try:
            settings = AlsJobSettings.from_sources(environ=environ, argv=argv)
            print(f"OBSERVED {label}: ACCEPTED reg_param={settings.als_configs[0].reg_param}", flush=True)
        except ValueError as error:
            print(f"OBSERVED {label}: REJECTED {error}", flush=True)


def rejected(label, operation) -> None:
    try:
        operation()
    except ValueError as error:
        print(f"CONFIRMED {label}: rejected ({error})", flush=True)
    else:
        raise AssertionError(f"{label} unexpectedly accepted")


verify_settings()
spark = (
    SparkSession.builder.master("local[1]")
    .appName("MR82-independent-review")
    .config("spark.ui.enabled", "false")
    .config("spark.sql.shuffle.partitions", "1")
    .config("spark.sql.session.timeZone", "UTC")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("ERROR")

with tempfile.TemporaryDirectory(prefix="mr82-integration-") as temporary:
    fixture_root = Path("/review/data/tests/movie_id_mapping/fixtures")
    read = lambda name: spark.read.option("header", True).csv(str(fixture_root / name))
    result = build_pipeline(read("ratings.csv"), read("movies.csv"), read("links.csv"), read("service-movies.csv"))
    assert result.validation_report["all_checks_passed"]
    generated_root = Path(temporary) / "118-output"
    write_pipeline(result, str(generated_root), "errorifexists")
    print("CONFIRMED 118 producer generated Parquet successfully", flush=True)

    ratings = spark.createDataFrame(RATING_ROWS, RATINGS_SCHEMA)
    mapping = spark.createDataFrame(SERVICE_MOVIE_MAP_ROWS, SERVICE_MOVIE_MAP_SCHEMA)
    rejected("empty ratings", lambda: validate_ratings(ratings.limit(0)))
    rejected("wrong ID", lambda: validate_ratings(ratings.withColumn("movielens_user_id", F.lit(0))))
    rejected("wrong rating type double", lambda: validate_ratings(ratings.withColumn("rating", F.col("rating").cast("double"))))
    rejected("empty mapping", lambda: validate_service_movie_map(mapping.limit(0)))
    rejected("no MATCHED mapping", lambda: validate_service_movie_map(mapping.filter("mapping_status != 'MATCHED'")))
    rejected("duplicate service IDs", lambda: validate_service_movie_map(mapping.unionByName(mapping.limit(1))))

    _, extra_count = validate_ratings(ratings.withColumn("extra_metadata", F.lit("unused")))
    assert extra_count == 60
    print("CONFIRMED extra columns allowed, returned frame selects required columns", flush=True)

    _, mismatch_count = validate_ratings(ratings.withColumn("rated_at_utc", F.to_timestamp(F.lit("2000-01-01 00:00:00"))))
    assert mismatch_count == 60
    print("BOUNDARY epoch/UTC inconsistency is not checked", flush=True)

    isolated_map = mapping.withColumn("movielens_movie_id", F.when(F.col("mapping_status") == "MATCHED", F.col("movielens_movie_id") + 1000).otherwise(F.col("movielens_movie_id")))
    _, _, isolated_matches = validate_service_movie_map(isolated_map)
    assert isolated_matches == 12
    print("BOUNDARY MATCHED IDs need not have any ratings; intersection is not checked", flush=True)
    spark.stop()

    env = {name: value for name, value in os.environ.items() if not name.startswith("RECOMMENDATION_")}
    output_uri = Path(temporary) / "unused-model-output"
    run = subprocess.run(
        [sys.executable, "-m", "jobs.batch.als_baseline", "--spark-master", "local[1]", "--input-uri", str(generated_root), "--output-uri", str(output_uri)],
        text=True, capture_output=True, env=env,
    )
    if run.returncode:
        print(run.stdout)
        print(run.stderr)
        raise RuntimeError(f"ALS CLI failed with exit {run.returncode}")
    assert "ALS input validation completed: ratings=2, service_movies=3, matched_service_movies=1" in run.stdout
    assert not output_uri.exists()
    print(run.stdout.strip(), flush=True)
    print("CONFIRMED actual CLI exited 0 and did not create the supplied output URI", flush=True)

print("Independent review checks completed.", flush=True)
