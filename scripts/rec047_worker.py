"""One sequential Spark fit; container sees only its stage's prepared data."""

from __future__ import annotations
import json
import sys
import time
from pathlib import Path
import numpy as np
from pyspark import StorageLevel
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.linalg import Vectors
from pyspark.ml.recommendation import ALS
from pyspark.ml.regression import (
    FMRegressor,
    LinearRegression,
    GBTRegressor,
    GBTRegressionModel,
)
from pyspark.sql import SparkSession, functions as F


def main():
    level, method = int(sys.argv[1]), sys.argv[2]
    folder = Path("/data")
    cfg = json.loads(Path("/config/config.json").read_text())
    info = json.loads((folder / "feature-info.json").read_text())
    params = cfg["models"][method]
    target = folder / "models" / f"p{level}" / method
    target.mkdir(parents=True)
    start = time.monotonic()
    spark = SparkSession.builder.appName(
        f"rec047-{info['stage']}-{level}-{method}"
    ).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    assert spark.version == "4.1.3"
    metrics = {
        "algorithm": method,
        "level": level,
        "stage": info["stage"],
        "spark": spark.version,
        "params": params,
    }
    if method == "ALS":
        train = (
            spark.read.parquet(str(folder / "ratings.parquet"))
            .filter(F.col("level") <= level)
            .select(
                F.col("uid").alias("user"), F.col("movie_id").alias("item"), "rating"
            )
            .repartition(8)
            .persist(StorageLevel.MEMORY_AND_DISK)
        )
        estimator = ALS(
            **params,
            seed=cfg["seed"],
            numUserBlocks=8,
            numItemBlocks=8,
            implicitPrefs=False,
            coldStartStrategy="nan",
        )
    else:
        cols = [f"x{i:03d}" for i in range(info["dimensions"])]
        assembler = VectorAssembler(inputCols=cols, outputCol="features")
        train = (
            assembler.transform(
                spark.read.parquet(str(folder / "train.parquet")).filter(
                    F.col("level") <= level
                )
            )
            .select("label", "features")
            .repartition(8)
            .persist(StorageLevel.DISK_ONLY)
        )
        if method == "FM":
            estimator = FMRegressor(
                **params, seed=cfg["seed"], solver="adamW", tol=1e-6
            )
        elif method == "RIDGE":
            estimator = LinearRegression(
                **params,
                elasticNetParam=0,
                solver="l-bfgs",
                standardization=False,
                tol=1e-6,
            )
        elif method == "GBT":
            estimator = GBTRegressor(
                **params,
                seed=cfg["seed"],
                maxBins=32,
                featureSubsetStrategy="sqrt",
                subsamplingRate=0.8,
                maxMemoryInMB=128,
                cacheNodeIds=False,
            )
        else:
            raise ValueError(method)
    metrics["training_rows"] = train.count()
    assert metrics["training_rows"] == info["levels"][str(level)]["ratings"]
    t = time.monotonic()
    model = estimator.fit(train)
    metrics["fit_seconds"] = time.monotonic() - t
    if method == "ALS":
        model.itemFactors.write.mode("error").parquet(str(target / "item-factors"))
    else:
        score = assembler.transform(
            spark.read.parquet(str(folder / "score.parquet"))
        ).select("row_id", "features")
        model.transform(score).select("row_id", "prediction").write.mode(
            "error"
        ).parquet(str(target / "predictions"))
        metrics["training_raw_rmse"] = float(
            model.transform(train)
            .select(
                F.sqrt(F.avg((F.col("prediction") - F.col("label")) ** 2)).alias("rmse")
            )
            .first()
            .rmse
        )
        samples = score.orderBy("row_id").limit(12).collect()
        xx = np.vstack([r.features.toArray() for r in samples])
        reference = np.asarray([model.predict(r.features) for r in samples])
        metrics["dense_sparse_parity"] = max(
            abs(
                model.predict(r.features)
                - model.predict(
                    Vectors.sparse(
                        info["dimensions"],
                        [
                            (i, float(v))
                            for i, v in enumerate(r.features.toArray())
                            if v
                        ],
                    )
                )
            )
            for r in samples
        )
        assert metrics["dense_sparse_parity"] < 1e-8
        if method == "FM":
            factors = model.factors.toArray()
            linear = model.linear.toArray()
            intercept = float(model.intercept)
            np.savez_compressed(
                target / "portable.npz",
                factors=factors,
                linear=linear,
                intercept=intercept,
            )
            manual = (
                intercept
                + xx @ linear
                + 0.5
                * np.sum((xx @ factors) ** 2 - (xx * xx) @ (factors * factors), axis=1)
            )
        elif method == "RIDGE":
            coef = model.coefficients.toArray()
            intercept = float(model.intercept)
            np.savez_compressed(
                target / "portable.npz", coefficients=coef, intercept=intercept
            )
            manual = intercept + xx @ coef
        if method in ["FM", "RIDGE"]:
            metrics["portable_parity"] = float(np.max(np.abs(manual - reference)))
            assert metrics["portable_parity"] < 1e-8
            try:
                metrics["objective_history"] = list(
                    map(float, model.summary.objectiveHistory)
                )
                metrics["total_iterations"] = int(model.summary.totalIterations)
            except AttributeError:
                metrics["objective_history_available"] = False
                metrics["optimizer_convergence_verified"] = False
        model.write().save(str(target / "native"))
        if method == "GBT":
            loaded = GBTRegressionModel.load(str(target / "native"))
            metrics["reload_parity"] = max(
                abs(loaded.predict(r.features) - model.predict(r.features))
                for r in samples
            )
            assert metrics["reload_parity"] == 0
    metrics["seconds"] = time.monotonic() - start
    for peak in [
        Path("/sys/fs/cgroup/memory.peak"),
        Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes"),
    ]:
        if peak.exists():
            metrics["cgroup_peak_bytes"] = int(peak.read_text())
            metrics["cgroup_peak_source"] = str(peak)
            break
    (target / "metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False))
    print(json.dumps(metrics), flush=True)
    train.unpersist()
    spark.stop()


if __name__ == "__main__":
    main()
