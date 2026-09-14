"""Synthetic runtime check only: no MovieLens input and no quality conclusion."""
import json
import time
from pathlib import Path

import numpy as np
from pyspark.ml.linalg import Vectors
from pyspark.ml.recommendation import ALS
from pyspark.ml.regression import FMRegressor, GBTRegressor, LinearRegression
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("rec046-synthetic-smoke").getOrCreate()
spark.sparkContext.setLogLevel("ERROR")
rng = np.random.default_rng(46)
x = rng.uniform(-1, 1, (1200, 32))
y = 3 + x[:, 0] * x[:, 1] + .2 * x[:, 2]
df = spark.createDataFrame([(float(a), Vectors.dense(b)) for a, b in zip(y, x)], ["label", "features"]).repartition(4).cache()
df.count()
results = {"synthetic_only": True, "spark": spark.version, "models": {}}
for name, estimator in [
    ("RIDGE", LinearRegression(regParam=.1, elasticNetParam=0, solver="l-bfgs", maxIter=10)),
    ("FM", FMRegressor(factorSize=4, maxIter=10, stepSize=.01, seed=46)),
    ("GBT", GBTRegressor(maxDepth=3, maxIter=5, seed=46)),
]:
    started = time.monotonic()
    model = estimator.fit(df)
    p = np.array([r.prediction for r in model.transform(df).select("prediction").collect()])
    assert len(p) == len(y) and np.isfinite(p).all()
    results["models"][name] = {"seconds": time.monotonic() - started, "predictions": len(p)}
    if name == "FM":
        z = x[:7]
        factors = model.factors.toArray()
        manual = model.intercept + z @ model.linear.toArray() + .5 * np.sum((z @ factors)**2 - (z*z) @ (factors*factors), axis=1)
        parity = np.array([model.predict(Vectors.dense(row)) for row in z])
        assert np.max(np.abs(manual - parity)) < 1e-10
        results["fm_numpy_parity_max_error"] = float(np.max(np.abs(manual-parity)))
ratings = spark.createDataFrame([(u, i, float(1 + (u+i) % 9) / 2) for u in range(40) for i in range(50) if (u+i) % 3], ["user", "item", "rating"])
t = time.monotonic()
model = ALS(rank=4, maxIter=3, seed=46).fit(ratings)
assert model.itemFactors.count() == 50
results["models"]["ALS"] = {"seconds": time.monotonic()-t, "items": 50}
Path("/out/smoke.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results), flush=True)
spark.stop()
