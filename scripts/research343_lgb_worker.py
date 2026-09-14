"""Paired 60-tree regression/ranking fits on identical R training queries."""
import hashlib
import json
import os
import sys
import time
from pathlib import Path
import numpy as np
from pyspark import StorageLevel
from pyspark.sql import SparkSession,functions as F
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.linalg import Vectors
from synapse.ml.lightgbm import LightGBMRegressor,LightGBMRanker,LightGBMRegressionModel,LightGBMRankerModel
from combination340_worker import read_peak
from research343_lgb_models import Booster

method=sys.argv[1]; assert method in ['LGBM_REG_R','LGBM_RANK_R']
root=Path('/data')/method; root.mkdir(exist_ok=False); start=time.monotonic()
cfg=json.loads(Path('/config/config.json').read_text())
spark=SparkSession.builder.appName('research343-'+method).config('spark.jars',os.environ['FEELM_SYNAPSE_JARS']).getOrCreate(); spark.sparkContext.setLogLevel('ERROR')
assert spark.version=='4.1.3' and spark._jvm.scala.util.Properties.versionNumberString()=='2.13.17'
assembler=VectorAssembler(inputCols=[f'x{i:03d}' for i in range(230)],outputCol='features')
source=spark.read.parquet('/base/train.parquet')
query=spark.read.parquet('/queries.parquet')
assert query.count()==4997069 and query.select('row_id').distinct().count()==4997069
train=assembler.transform(source.join(query,'row_id')).select('row_id','qid','label','features')
train=train.withColumn('relevance',((F.col('label')-.5)*2).cast('double')).repartition(2,'qid').sortWithinPartitions('qid','row_id').persist(StorageLevel.DISK_ONLY)
def identity(pid,it):
    h=hashlib.sha256(); n=0; groups=0; prev=(-1,-1)
    for r in it:
        current=(int(r.qid),int(r.row_id)); assert current>prev
        groups+=current[0]!=prev[0];prev=current
        h.update(current[0].to_bytes(8,'little'));h.update(current[1].to_bytes(8,'little'));n+=1
    yield (pid,n,groups,h.hexdigest())
parts=sorted(train.select('qid','row_id').rdd.mapPartitionsWithIndex(identity).collect())
assert sum(x[1] for x in parts)==4997069 and sum(x[2] for x in parts)==73771
(root/'partition-identity.json').write_text(json.dumps(parts))
common=dict(**cfg['lgb'],featuresCol='features',predictionCol='prediction',numTasks=2,numThreads=4,
            repartitionByGroupingColumn=False,useBarrierExecutionMode=True,dataTransferMode='streaming',
            useSingleDatasetMode=True,samplingMode='global',seed=339,zeroAsMissing=False,
            passThroughArgs='deterministic=true force_col_wise=true',timeout=3600)
if method=='LGBM_REG_R':
    estimator=LightGBMRegressor(labelCol='label',objective='regression',**common); cls=LightGBMRegressionModel
else:
    common['passThroughArgs']+=' lambdarank_truncation_level=6'
    estimator=LightGBMRanker(labelCol='relevance',groupCol='qid',objective='lambdarank',labelGain=list(range(10)),evalAt=[1,2,4,6],maxPosition=6,**common);cls=LightGBMRankerModel
(root/'parameters.json').write_text(json.dumps({p.name:v for p,v in estimator.extractParamMap().items()},indent=2))
print('FIT_START',method,flush=True); t=time.monotonic(); model=estimator.fit(train); fit_seconds=time.monotonic()-t
native=model.getNativeModel(); (root/'model.txt').write_text(native)
if method=='LGBM_RANK_R':
    assert '[lambdarank_truncation_level: 6]' in native and '[label_gain: 0,1,2,3,4,5,6,7,8,9]' in native
model.write().save(str(root/'native'))
score=assembler.transform(spark.read.parquet('/base/score.parquet')).select('row_id','features')
actual=model.transform(score).select('row_id','prediction').orderBy('row_id').collect()
rid=np.array([r.row_id for r in actual]);pred=np.array([r.prediction for r in actual])
assert np.array_equal(rid,np.arange(93230)) and np.isfinite(pred).all()
reloaded=cls.loadNativeModelFromString(native)
again=np.array([r.prediction for r in reloaded.transform(score).select('row_id','prediction').orderBy('row_id').collect()])
assert np.max(abs(again-pred))<=1e-8
np.save(root/'predictions.npy',pred)
portable=Booster(root/'model.txt'); base=np.array(score.orderBy('row_id').first().features.toArray(),float)
xx=portable.fixtures(base); ff=spark.createDataFrame([(i,Vectors.dense(x)) for i,x in enumerate(xx)],['row_id','features'])
pp=np.array([r.prediction for r in model.transform(ff).select('row_id','prediction').orderBy('row_id').collect()])
np.savez_compressed(root/'threshold-fixtures.npz',features=xx,predictions=pp)
metrics={'method':method,'training_rows':4997069,'queries':73771,'fit_seconds':fit_seconds,'seconds':time.monotonic()-start,
         'spark':spark.version,'native_tree_count':len(portable.trees),'native_roundtrip_max_error':float(abs(again-pred).max()),
         'optimizer_convergence_verified':False,**read_peak()}
(root/'metrics.json').write_text(json.dumps(metrics,indent=2));print(json.dumps(metrics),flush=True)
train.unpersist();spark.stop()
