"""One bounded Spark model fit; no evaluation labels are accessible here."""

from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import numpy as np
from pyspark import StorageLevel
from pyspark.ml.linalg import VectorUDT, Vectors
from pyspark.ml.recommendation import ALS
from pyspark.ml.regression import (
    FMRegressor,
    GBTRegressor,
    LinearRegression,
    GBTRegressionModel,
)
from pyspark.sql import SparkSession, functions as F


def main():
    round_id, algorithm, variant = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    folder = Path("/data") / f"r{round_id}"
    cfg = json.loads(Path("/config/config.json").read_text())
    info = json.loads((folder / "feature-info.json").read_text())
    params = cfg["models"][algorithm][variant]
    target = folder / "models" / f"{algorithm}-{variant}"
    target.mkdir(parents=True)
    started = time.monotonic()
    spark = SparkSession.builder.appName(
        f"rec046-r{round_id}-{algorithm}-{variant}"
    ).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    require_version = spark.version == "4.1.3"
    assert require_version, spark.version
    metrics = {
        "spark": spark.version,
        "algorithm": algorithm,
        "variant": variant,
        "params": params,
    }
    if algorithm == "ALS":
        train = (
            spark.read.schema("user INT,item INT,rating DOUBLE")
            .csv(str(folder / "als.csv"))
            .repartition(8)
            .persist(StorageLevel.MEMORY_AND_DISK)
        )
        metrics["training_rows"] = train.count()
        t = time.monotonic()
        assert metrics["training_rows"] == info["training_ratings"], (
            "ALS row count changed"
        )
        model = ALS(
            **params,
            seed=cfg["seed"],
            numUserBlocks=8,
            numItemBlocks=8,
            implicitPrefs=False,
            coldStartStrategy="nan",
        ).fit(train)
        metrics["fit_seconds"] = time.monotonic() - t
        model.itemFactors.write.mode("error").parquet(str(target / "item-factors"))
    else:
        dims = info["pair_columns"]
        make_vector = F.udf(lambda ix, va: Vectors.sparse(dims, ix, va), VectorUDT())
        train = (
            spark.read.parquet(str(folder / "train.parquet"))
            .withColumn("features", make_vector("indices", "values"))
            .select("label", "features")
            .repartition(8)
            .persist(StorageLevel.MEMORY_AND_DISK)
        )
        metrics["training_rows"] = train.count()
        assert (
            metrics["training_rows"] == cfg["train_users"] * cfg["training_targets"]
        ), "supervised row count changed"
        if algorithm == "FM":
            estimator = FMRegressor(
                **params, seed=cfg["seed"], solver="adamW", tol=1e-6
            )
        elif algorithm == "RIDGE":
            estimator = LinearRegression(
                **params,
                elasticNetParam=0,
                solver="l-bfgs",
                standardization=False,
                tol=1e-6,
            )
        elif algorithm == "GBT":
            estimator = GBTRegressor(
                **params,
                seed=cfg["seed"],
                maxBins=32,
                featureSubsetStrategy="sqrt",
                subsamplingRate=0.8,
            )
        else:
            raise ValueError(algorithm)
        t = time.monotonic()
        model = estimator.fit(train)
        metrics["fit_seconds"] = time.monotonic() - t
        metrics["training_raw_rmse"] = float(
            model.transform(train)
            .select(
                F.sqrt(F.avg(F.pow(F.col("prediction") - F.col("label"), 2))).alias(
                    "rmse"
                )
            )
            .first()
            .rmse
        )
        score = (
            spark.read.parquet(str(folder / "score.parquet"))
            .withColumn("features", make_vector("indices", "values"))
            .select("row_id", "features")
        )
        t = time.monotonic()
        result = model.transform(score).select("row_id", "prediction")
        result.write.mode("error").parquet(str(target / "predictions"))
        metrics["score_seconds"] = time.monotonic() - t
        samples = score.orderBy("row_id").limit(8).collect()
        dense_parity = []
        for row in samples:
            dense_parity.append(
                abs(
                    float(model.predict(row.features))
                    - float(model.predict(Vectors.dense(row.features.toArray())))
                )
            )
        metrics["dense_sparse_parity"] = max(dense_parity, default=0)
        assert metrics["dense_sparse_parity"] < 1e-8
        if algorithm == "FM":
            factors = model.factors.toArray()
            linear = model.linear.toArray()
            intercept = float(model.intercept)
            np.savez_compressed(
                target / "portable.npz",
                factors=factors,
                linear=linear,
                intercept=intercept,
            )
            xx = np.vstack([row.features.toArray() for row in samples])
            vv = factors
            manual = (
                intercept
                + xx @ linear
                + 0.5 * np.sum((xx @ vv) ** 2 - (xx * xx) @ (vv * vv), axis=1)
            )
            reference = np.array([model.predict(row.features) for row in samples])
            metrics["portable_parity_max_error"] = float(
                np.max(np.abs(manual - reference))
            )
            assert metrics["portable_parity_max_error"] < 1e-8
        elif algorithm == "RIDGE":
            coef = model.coefficients.toArray()
            intercept = float(model.intercept)
            np.savez_compressed(
                target / "portable.npz", coefficients=coef, intercept=intercept
            )
            metrics["portable_parity_max_error"] = max(
                abs(
                    float(row.features.toArray() @ coef + intercept)
                    - model.predict(row.features)
                )
                for row in samples
            )
            assert metrics["portable_parity_max_error"] < 1e-8
        if algorithm in ("FM", "RIDGE"):
            try:
                metrics["objective_history"] = [
                    float(v) for v in model.summary.objectiveHistory
                ]
                metrics["total_iterations"] = int(model.summary.totalIterations)
            except AttributeError:
                metrics["objective_history_available"] = False
                metrics["optimizer_convergence_verified"] = False
        model.write().save(str(target / "native"))
        if algorithm == "GBT":
            loaded = GBTRegressionModel.load(str(target / "native"))
            metrics["reload_parity"] = max(
                abs(loaded.predict(row.features) - model.predict(row.features))
                for row in samples
            )
            assert metrics["reload_parity"] == 0
    metrics["seconds"] = time.monotonic() - started
    peak = Path("/sys/fs/cgroup/memory.peak")
    if peak.exists():
        metrics["cgroup_peak_bytes"] = int(peak.read_text())
    (target / "metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False))
    print(json.dumps(metrics), flush=True)
    train.unpersist()
    spark.stop()


if __name__ == "__main__":
    main()
