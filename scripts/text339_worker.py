"""One fixed Spark FM fit in a network-disabled container; no future labels."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
import numpy as np
from pyspark import StorageLevel
from pyspark.sql import SparkSession,functions as F
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.regression import FMRegressor

def main():
    variant=sys.argv[1];root=Path('/data');cfg=json.loads(Path('/config/config.json').read_text())
    info=json.loads((root/'feature-info.json').read_text());columns=[f'x{i:03d}' for i in info['indices'][variant]]
    target=root/'models'/variant;target.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    spark=SparkSession.builder.appName('text339-'+variant).getOrCreate();spark.sparkContext.setLogLevel('ERROR')
    assert spark.version=='4.1.3'
    assembler=VectorAssembler(inputCols=columns,outputCol='features')
    train=assembler.transform(spark.read.parquet(str(root/'train.parquet'))).select('label','features').repartition(8).persist(StorageLevel.DISK_ONLY)
    n=train.count();assert n==info['train_rows']
    estimator=FMRegressor(**cfg['models']['FM'],seed=cfg['seed'],solver='adamW',tol=1e-6)
    t=time.monotonic();model=estimator.fit(train);fit_seconds=time.monotonic()-t
    score=assembler.transform(spark.read.parquet(str(root/'score.parquet'))).select('row_id','features')
    model.transform(score).select('row_id','prediction').write.mode('error').parquet(str(target/'predictions'))
    factors,linear,intercept=model.factors.toArray(),model.linear.toArray(),float(model.intercept)
    np.savez_compressed(target/'portable.npz',factors=factors,linear=linear,intercept=intercept,indices=info['indices'][variant])
    sample=score.orderBy('row_id').limit(32).collect();xx=np.vstack([r.features.toArray() for r in sample]);ref=np.array([model.predict(r.features) for r in sample])
    manual=intercept+xx@linear+.5*np.sum((xx@factors)**2-(xx*xx)@(factors*factors),axis=1)
    parity=float(abs(manual-ref).max());assert parity<1e-8
    model.write().save(str(target/'native'))
    metrics={'variant':variant,'spark':spark.version,'dimensions':len(columns),'train_rows':n,'fit_seconds':fit_seconds,'portable_parity':parity,'params':cfg['models']['FM'],'seed':cfg['seed']}
    metrics['train_raw_rmse']=float(model.transform(train).select(F.sqrt(F.avg((F.col('prediction')-F.col('label'))**2)).alias('rmse')).first().rmse)
    metrics['seconds']=time.monotonic()-started
    for peak in [Path('/sys/fs/cgroup/memory.peak'),Path('/sys/fs/cgroup/memory/memory.max_usage_in_bytes')]:
        if peak.exists():metrics['peak_bytes']=int(peak.read_text());break
    (target/'metrics.json').write_text(json.dumps(metrics,indent=2,allow_nan=False));print(json.dumps(metrics),flush=True)
    train.unpersist();spark.stop()

if __name__=='__main__':main()
