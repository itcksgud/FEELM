"""One from-scratch FM/GBT fit in the pinned, offline Spark container."""
import hashlib
import json
import sys
import time
from pathlib import Path
import numpy as np
from pyspark import StorageLevel
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.linalg import Vectors
from pyspark.ml.regression import FMRegressor, GBTRegressor
from pyspark.sql import SparkSession, functions as F

def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False))

def peak():
    for p in (Path('/sys/fs/cgroup/memory.peak'), Path('/sys/fs/cgroup/memory/memory.max_usage_in_bytes')):
        if p.exists():
            n = int(p.read_text())
            return {'peak_bytes': n, 'peak_source': str(p), 'resource_status': 'PASS' if n <= 12 * 1024**3 else 'EXCEPTION'}
    return {'peak_bytes': None, 'peak_source': None, 'resource_status': 'UNKNOWN'}

def main():
    recipe, seed = sys.argv[1], int(sys.argv[2])
    cfg = json.loads(Path('/config/config.json').read_text())
    expected = dict(cfg['resolved_reference']['FM' if recipe.startswith('FM') else 'GBT'])
    expected.update(seed=seed, maxIter=int(recipe.lstrip('FMGBT')))
    assert recipe in ('FM150', 'FM300', 'GBT60', 'GBT120') and seed in (339, 344, 345)
    root = Path('/data/model'); root.mkdir(exist_ok=False)
    started = time.monotonic()
    spark = SparkSession.builder.appName('final344-' + recipe + '-s' + str(seed)).getOrCreate()
    spark.sparkContext.setLogLevel('ERROR')
    assert spark.version == '4.1.3' and spark.conf.get('spark.sql.adaptive.enabled') == 'false'
    assert spark.sparkContext.master == 'local[4]'
    assembler = VectorAssembler(inputCols=[f'x{i:03d}' for i in range(230)], outputCol='features')
    ordered = assembler.transform(spark.read.parquet('/base/train.parquet')).select('row_id', 'label', 'features') \
        .repartition(8, 'row_id').sortWithinPartitions('row_id').persist(StorageLevel.DISK_ONLY)
    def identity(index, rows):
        h = hashlib.sha256(); n = 0; previous = -1
        for row in rows:
            rid = int(row.row_id); assert rid > previous; previous = rid
            h.update(rid.to_bytes(8, 'little')); n += 1
        yield (index, n, h.hexdigest())
    parts = sorted(ordered.select('row_id').rdd.mapPartitionsWithIndex(identity).collect())
    assert len(parts) == 8 and sum(p[1] for p in parts) == 4997069
    assert parts == [tuple(p) for p in cfg['partition_identity']]
    dump(root / 'partition-identity.json', parts)
    train = ordered.select('label', 'features')
    assert train.count() == 4997069
    if recipe.startswith('FM'):
        # Pass only settable estimator params. The metadata's full resolved map is checked below.
        estimator = FMRegressor(**expected)
    else:
        estimator = GBTRegressor(**expected)
    resolved = {p.name: v for p, v in estimator.extractParamMap().items()}
    assert resolved == expected, (resolved, expected)
    dump(root / 'resolved-estimator.json', resolved)
    print('FIT_START ' + recipe + ' seed=' + str(seed), flush=True)
    fit_start = time.monotonic(); model = estimator.fit(train); fit_seconds = time.monotonic() - fit_start
    model.write().save(str(root / 'native'))
    score = assembler.transform(spark.read.parquet('/base/score.parquet')).select('row_id', 'features')
    assert score.count() == 93230
    model.transform(score).select('row_id', 'prediction').write.mode('error').parquet(str(root / 'predictions'))
    metrics = {'recipe': recipe, 'seed': seed, 'configured_max_iter': expected['maxIter'],
               'actual_iterations': None, 'iteration_measurement': 'UNAVAILABLE',
               'optimizer_convergence_verified': False, 'loss_history': None,
               'spark': spark.version, 'dimensions': 230, 'training_rows': 4997069,
               'fit_seconds': fit_seconds, 'resolved_params': resolved, 'row_weight': 1,
               'aqe_enabled': False, 'ordered_partitions': 8}
    if recipe.startswith('FM'):
        np.savez_compressed(root / 'portable.npz', factors=model.factors.toArray(),
                            linear=model.linear.toArray(), intercept=float(model.intercept), indices=np.arange(230))
    else:
        metrics.update(actual_iterations=len(model.trees), iteration_measurement='NATIVE_TREE_COUNT')
        base = np.array(score.orderBy('row_id').first().features.toArray(), float); fixtures = []
        for tree in model.trees:
            if tree.numNodes == 1: continue
            split = tree._java_obj.rootNode().split(); feature = int(split.featureIndex()); threshold = float(split.threshold())
            for value in (np.nextafter(threshold, -np.inf), threshold, np.nextafter(threshold, np.inf)):
                x = base.copy(); x[feature] = value; fixtures.append(x)
        xx = np.vstack(fixtures) if fixtures else base[None, :]
        np.savez_compressed(root / 'threshold-fixtures.npz', features=xx,
                            predictions=np.array([model.predict(Vectors.dense(x)) for x in xx]), indices=np.arange(230))
    metrics['train_raw_rmse'] = float(model.transform(train).select(F.sqrt(F.avg((F.col('prediction') - F.col('label'))**2)).alias('v')).first().v)
    ordered.unpersist(); spark.stop()
    metrics.update(seconds=time.monotonic() - started, **peak())
    dump(root / 'metrics.json', metrics)
    print(json.dumps(metrics), flush=True)

if __name__ == '__main__': main()
