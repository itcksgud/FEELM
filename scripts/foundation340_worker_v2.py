"""One deterministic-row-partition FM fit, inside the pinned Spark container."""
import json
import sys
import time
import hashlib
from pathlib import Path
import numpy as np
from pyspark import StorageLevel
from pyspark.sql import SparkSession, functions as F
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.regression import FMRegressor

def read_peak():
    for path in [Path('/sys/fs/cgroup/memory.peak'), Path('/sys/fs/cgroup/memory/memory.max_usage_in_bytes')]:
        if path.exists():
            value=int(path.read_text())
            return {'peak_bytes':value,'peak_source':str(path),'strict_memory_pass':value<=12*1024**3,'memory_measurement':'MEASURED'}
    return {'peak_bytes':None,'peak_source':None,'strict_memory_pass':None,'memory_measurement':'UNAVAILABLE'}

def main():
    variant = sys.argv[1]; root = Path('/data'); cfg = json.loads(Path('/config/config.json').read_text())
    info = json.loads((root / 'feature-info.json').read_text())
    target = root / 'model'; target.mkdir(exist_ok=False); started = time.monotonic()
    spark = SparkSession.builder.appName('foundation340-' + variant).getOrCreate()
    spark.sparkContext.setLogLevel('ERROR'); assert spark.version == '4.1.3'
    assert spark.conf.get('spark.sql.adaptive.enabled') == 'false'
    columns = [f'x{i:03d}' for i in info['indices'][variant]]
    assembler = VectorAssembler(inputCols=columns, outputCol='features')
    frame = assembler.transform(spark.read.parquet(str(root / 'train.parquet')))
    ordered = frame.select('row_id', 'label', 'features').repartition(8, 'row_id').sortWithinPartitions('row_id').persist(StorageLevel.DISK_ONLY)
    def partition_identity(index, rows):
        digest = hashlib.sha256(); count = 0; previous = -1
        for row in rows:
            rid = int(row.row_id); assert rid > previous; previous = rid
            digest.update(rid.to_bytes(8, 'little')); count += 1
        yield (index, count, digest.hexdigest())
    identity = sorted(ordered.select('row_id').rdd.mapPartitionsWithIndex(partition_identity).collect())
    assert len(identity) == 8 and sum(row[1] for row in identity) == info['train_rows']
    (target / 'partition-identity.json').write_text(json.dumps(identity))
    train = ordered.select('label', 'features')
    n = train.count(); assert n == info['train_rows']
    estimator = FMRegressor(**cfg['models']['FM'], seed=cfg['seed'], solver='adamW', tol=1e-6)
    t = time.monotonic(); model = estimator.fit(train); fit_seconds = time.monotonic()-t
    score = assembler.transform(spark.read.parquet(str(root / 'score.parquet'))).select('row_id','features')
    model.transform(score).select('row_id','prediction').write.mode('error').parquet(str(target / 'predictions'))
    factors, linear, intercept = model.factors.toArray(), model.linear.toArray(), float(model.intercept)
    np.savez_compressed(target / 'portable.npz', factors=factors, linear=linear, intercept=intercept, indices=info['indices'][variant])
    model.write().save(str(target / 'native'))
    metrics = {'variant': variant, 'spark': spark.version, 'dimensions':len(columns), 'train_rows':n,
               'fit_seconds':fit_seconds, 'params':cfg['models']['FM'], 'seed':cfg['seed'],
               'train_raw_rmse':float(model.transform(train).select(F.sqrt(F.avg((F.col('prediction')-F.col('label'))**2)).alias('rmse')).first().rmse),
               'seconds':time.monotonic()-started, 'row_partition':'hash(row_id), sorted row_id',
               'aqe_enabled':False, 'ordered_partitions':8,
               'peak_bytes':None}
    metrics.update(read_peak())
    (target / 'metrics.json').write_text(json.dumps(metrics, indent=2)); print(json.dumps(metrics), flush=True)
    ordered.unpersist(); spark.stop()

if __name__ == '__main__': main()
