"""Three bounded post-foundation fits; target labels remain inaccessible."""
import hashlib
import json
import sys
import time
from pathlib import Path
import numpy as np
from pyspark import StorageLevel
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.linalg import Vectors
from pyspark.ml.regression import FMRegressor,GBTRegressor
from pyspark.ml.recommendation import ALS
from pyspark.sql import SparkSession,functions as F

def read_peak():
    for path in [Path('/sys/fs/cgroup/memory.peak'),Path('/sys/fs/cgroup/memory/memory.max_usage_in_bytes')]:
        if path.exists():
            value=int(path.read_text())
            return {'peak_bytes':value,'peak_source':str(path),'strict_memory_pass':value<=12*1024**3,'memory_measurement':'MEASURED'}
    return {'peak_bytes':None,'peak_source':None,'strict_memory_pass':None,'memory_measurement':'UNAVAILABLE'}

def main():
    method=sys.argv[1]; cfg=json.loads(Path('/config/config.json').read_text()); root=Path('/data')/method
    root.mkdir(exist_ok=False); start=time.monotonic()
    spark=SparkSession.builder.appName('combination340-'+method).getOrCreate(); spark.sparkContext.setLogLevel('ERROR')
    assert spark.version=='4.1.3' and spark.conf.get('spark.sql.adaptive.enabled')=='false'
    if method=='ALS':
        source=spark.read.parquet('/ratings.parquet')
        assert source.filter(F.col('timestamp')>=1672531200).count()==0
        train=source.select(F.col('uid').alias('user'),F.col('movie_id').alias('item'),'rating').repartition(8,'user').sortWithinPartitions('user','item').persist(StorageLevel.MEMORY_AND_DISK)
        estimator=ALS(**cfg['models']['ALS'],seed=cfg['seed'],numUserBlocks=8,numItemBlocks=8,implicitPrefs=False,coldStartStrategy='nan')
    else:
        indices=[i for i in range(230) if method!='NO_RESPONSE' or i not in cfg['response_removed_columns']]
        assembler=VectorAssembler(inputCols=[f'x{i:03d}' for i in indices],outputCol='features')
        ordered=assembler.transform(spark.read.parquet('/base/train.parquet')).select('row_id','label','features').repartition(8,'row_id').sortWithinPartitions('row_id').persist(StorageLevel.DISK_ONLY)
        def identity(index,rows):
            digest=hashlib.sha256(); count=0; previous=-1
            for row in rows:
                rid=int(row.row_id); assert rid>previous; previous=rid; digest.update(rid.to_bytes(8,'little')); count+=1
            yield(index,count,digest.hexdigest())
        parts=sorted(ordered.select('row_id').rdd.mapPartitionsWithIndex(identity).collect())
        assert len(parts)==8 and sum(row[1] for row in parts)==4997069
        (root/'partition-identity.json').write_text(json.dumps(parts)); train=ordered.select('label','features')
        if method=='NO_RESPONSE': estimator=FMRegressor(**cfg['models']['FM'],seed=cfg['seed'],solver='adamW',tol=1e-6)
        else: estimator=GBTRegressor(**cfg['models']['GBT'],seed=cfg['seed'],maxBins=32,featureSubsetStrategy='sqrt',subsamplingRate=.8,maxMemoryInMB=128,cacheNodeIds=False)
    assert train.count()==4997069
    t=time.monotonic(); model=estimator.fit(train); fit_seconds=time.monotonic()-t
    model.write().save(str(root/'native'))
    metrics={'method':method,'training_rows':4997069,'fit_seconds':fit_seconds,'seed':cfg['seed'],'spark':spark.version,'aqe_enabled':False,'optimizer_convergence_verified':False}
    if method=='ALS':
        model.itemFactors.write.mode('error').parquet(str(root/'item-factors'))
        metrics['items']=model.itemFactors.count(); assert metrics['items']==45074
    else:
        score=assembler.transform(spark.read.parquet('/base/score.parquet')).select('row_id','features')
        model.transform(score).select('row_id','prediction').write.mode('error').parquet(str(root/'predictions'))
        metrics['dimensions']=len(indices)
        metrics['train_raw_rmse']=float(model.transform(train).select(F.sqrt(F.avg((F.col('prediction')-F.col('label'))**2)).alias('rmse')).first().rmse)
        if method=='NO_RESPONSE':
            np.savez_compressed(root/'portable.npz',factors=model.factors.toArray(),linear=model.linear.toArray(),intercept=float(model.intercept),indices=indices)
        else:
            # Each root is exercised at its exact float64 threshold and its neighbours.
            base=np.array(score.orderBy('row_id').first().features.toArray(),float); fixtures=[]
            for tree in model.trees:
                if tree.numNodes==1: continue
                split=tree._java_obj.rootNode().split(); feature=int(split.featureIndex()); threshold=float(split.threshold())
                for value in [np.nextafter(threshold,-np.inf),threshold,np.nextafter(threshold,np.inf)]:
                    x=base.copy(); x[feature]=value; fixtures.append(x)
            xx=np.vstack(fixtures) if fixtures else base[None,:]; pred=np.array([model.predict(Vectors.dense(x)) for x in xx])
            np.savez_compressed(root/'threshold-fixtures.npz',features=xx,predictions=pred,indices=indices)
    metrics['seconds']=time.monotonic()-start; metrics.update(read_peak())
    (root/'metrics.json').write_text(json.dumps(metrics,indent=2)); print(json.dumps(metrics),flush=True)
    if method=='ALS': train.unpersist()
    else: ordered.unpersist()
    spark.stop()

if __name__=='__main__':main()
