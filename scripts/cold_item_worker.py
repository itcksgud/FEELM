"""One Spark model fit on a fixed feature projection; no future labels mounted."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
import numpy as np
from pyspark import StorageLevel
from pyspark.sql import SparkSession, functions as F
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.regression import FMRegressor, LinearRegression, GBTRegressor, GBTRegressionModel

def main():
    variant, method = sys.argv[1:3]
    folder = Path('/data'); cfg = json.loads(Path('/config/config.json').read_text())
    info = json.loads((folder / 'feature-info.json').read_text())
    columns = [f'x{i:03d}' for i in info['indices'][variant]]
    target = folder / 'models' / variant / method
    target.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    spark = SparkSession.builder.appName('cold-item-'+variant+'-'+method).getOrCreate()
    spark.sparkContext.setLogLevel('ERROR')
    assert spark.version == '4.1.3'
    assembler = VectorAssembler(inputCols=columns, outputCol='features')
    train = assembler.transform(spark.read.parquet(str(folder/'train.parquet'))).select('label','features').repartition(8).persist(StorageLevel.DISK_ONLY)
    n = train.count(); assert n == info['train_rows']
    params = cfg['models'][method]
    if method == 'FM':
        estimator = FMRegressor(**params, seed=47, solver='adamW', tol=1e-6)
    elif method == 'RIDGE':
        estimator = LinearRegression(**params, elasticNetParam=0, solver='l-bfgs', standardization=False, tol=1e-6)
    else:
        assert method == 'GBT'
        estimator = GBTRegressor(**params, seed=47, maxBins=32, featureSubsetStrategy='sqrt', subsamplingRate=.8, maxMemoryInMB=128, cacheNodeIds=False)
    t = time.monotonic(); model = estimator.fit(train); fit_time = time.monotonic()-t
    score = assembler.transform(spark.read.parquet(str(folder/'score.parquet'))).select('row_id','features')
    model.transform(score).select('row_id','prediction').write.mode('error').parquet(str(target/'predictions'))
    metrics = {'variant':variant,'method':method,'dimensions':len(columns),'train_rows':n,'fit_seconds':fit_time,'spark':spark.version,'params':params}
    metrics['train_raw_rmse'] = float(model.transform(train).select(F.sqrt(F.avg((F.col('prediction')-F.col('label'))**2)).alias('rmse')).first().rmse)
    samples = score.orderBy('row_id').limit(12).collect()
    xx = np.vstack([r.features.toArray() for r in samples]); reference = np.array([model.predict(r.features) for r in samples])
    if method == 'FM':
        factors, linear, intercept = model.factors.toArray(), model.linear.toArray(), float(model.intercept)
        np.savez_compressed(target/'portable.npz', factors=factors, linear=linear, intercept=intercept)
        manual = intercept + xx@linear + .5*np.sum((xx@factors)**2-(xx*xx)@(factors*factors), axis=1)
    elif method == 'RIDGE':
        coefficients, intercept = model.coefficients.toArray(), float(model.intercept)
        np.savez_compressed(target/'portable.npz', coefficients=coefficients, intercept=intercept)
        manual = intercept + xx@coefficients
    model.write().save(str(target/'native'))
    if method in ['FM','RIDGE']:
        metrics['portable_parity'] = float(np.max(abs(manual-reference))); assert metrics['portable_parity'] < 1e-8
        try:
            metrics['objective_history'] = list(map(float, model.summary.objectiveHistory))
            metrics['total_iterations'] = int(model.summary.totalIterations)
        except AttributeError:
            metrics['optimizer_history_available'] = False
    else:
        loaded = GBTRegressionModel.load(str(target/'native'))
        metrics['reload_parity'] = max(abs(loaded.predict(r.features)-model.predict(r.features)) for r in samples)
        assert metrics['reload_parity'] == 0
    for peak in [Path('/sys/fs/cgroup/memory.peak'), Path('/sys/fs/cgroup/memory/memory.max_usage_in_bytes')]:
        if peak.exists(): metrics['peak_bytes'] = int(peak.read_text()); break
    metrics['seconds'] = time.monotonic()-start
    (target/'metrics.json').write_text(json.dumps(metrics, indent=2, allow_nan=False))
    print(json.dumps(metrics), flush=True)
    train.unpersist(); spark.stop()

if __name__ == '__main__': main()
