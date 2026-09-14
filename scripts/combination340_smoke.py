"""No-fit Spark native threshold fixtures for the existing sealed GBT."""
import json
from pathlib import Path
import numpy as np
from pyspark.sql import SparkSession
from pyspark.ml.regression import GBTRegressionModel
from pyspark.ml.linalg import Vectors

spark=SparkSession.builder.appName('combination340-boundary-smoke').getOrCreate()
spark.sparkContext.setLogLevel('ERROR')
model=GBTRegressionModel.load('/reference')
fixtures=[]
for tree in model.trees:
    if tree.numNodes==1: continue
    split=tree._java_obj.rootNode().split()
    feature=int(split.featureIndex()); threshold=float(split.threshold())
    for value in [np.nextafter(threshold,-np.inf),threshold,np.nextafter(threshold,np.inf)]:
        x=np.zeros(model.numFeatures); x[feature]=value; fixtures.append(x)
xx=np.vstack(fixtures)
pred=np.array([model.predict(Vectors.dense(x)) for x in xx])
out=Path('/output'); assert not (out/'existing-gbt-boundaries.npz').exists()
np.savez_compressed(out/'existing-gbt-boundaries.npz',features=xx,predictions=pred)
print(json.dumps({'rows':len(xx),'new_fits':0,'spark':spark.version}),flush=True)
spark.stop()
