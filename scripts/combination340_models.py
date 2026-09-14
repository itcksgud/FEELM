"""Direct ALS without fabricated scores; native Spark GBT tree traversal."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from rec046_common import require

def direct_als(vectors, oi, stars, ei, reg=.1):
    oi, ei, stars=np.asarray(oi,int),np.asarray(ei,int),np.asarray(stars,float)
    support=np.isfinite(vectors).all(1); valid=support[oi]
    direct=support[ei] & valid.any(); prediction=np.full(len(ei),np.nan)
    if valid.any():
        y=vectors[oi[valid]].astype(float)
        user=np.linalg.solve(y.T@y+reg*valid.sum()*np.eye(y.shape[1]),y.T@stars[valid])
        prediction[direct]=vectors[ei[direct]]@user
    require(np.isfinite(prediction[direct]).all() and np.isnan(prediction[~direct]).all(),'explicit ALS direct availability')
    return prediction,direct,int(valid.sum())

def load_factors(path,ids,expected):
    frame=pd.read_parquet(path)
    require(frame.id.is_unique and np.array_equal(np.sort(frame.id),ids[expected]),'actual factor support identity')
    result=np.full((len(ids),32),np.nan); result[np.searchsorted(ids,frame.id)]=np.vstack(frame.features)
    require(np.isfinite(result[expected]).all(),'finite supported factors')
    return result

class Trees:
    def __init__(self,path,indices):
        path=Path(path); self.indices=np.asarray(indices,int)
        metadata_files=[p for p in (path/'metadata').glob('part-*') if p.is_file()]
        require(len(metadata_files)==1,'single native metadata')
        metadata=json.loads(metadata_files[0].read_text())
        require(metadata['class'].endswith('GBTRegressionModel') and metadata['numFeatures']==len(indices),'native GBT type/dimensions')
        frame=pd.read_parquet(path/'data'); weights=pd.read_parquet(path/'treesMetadata')
        require(weights['_1'].is_unique and len(weights)==metadata['numTrees'],'unique tree weight IDs')
        weight_map={int(row['_1']):float(row['_3']) for row in weights.to_dict('records')}
        require(set(frame.treeID)==set(weight_map) and len(weight_map)==metadata['numTrees'],'tree and weight identity')
        self.trees=[]
        for tid,part in frame.groupby('treeID',sort=True):
            nodes={int(n['id']):n for n in part.nodeData}
            require(len(nodes)==len(part) and 0 in nodes,'unique nodes and root')
            visited=set(); stack=[0]
            while stack:
                nid=stack.pop(); require(nid in nodes and nid not in visited,'acyclic present child'); visited.add(nid)
                node=nodes[nid]; left,right=int(node['leftChild']),int(node['rightChild'])
                if left==-1:
                    require(right==-1 and np.isfinite(node['prediction']),'finite leaf')
                else:
                    split=node['split']; feature=int(split['featureIndex']); threshold=split['leftCategoriesOrThreshold']
                    require(right>=0 and split['numCategories']==-1 and len(threshold)==1 and np.isfinite(threshold[0]) and 0<=feature<len(indices),'continuous finite split')
                    stack.extend([left,right])
            require(visited==set(nodes) and np.isfinite(weight_map[int(tid)]),'reachable tree and finite weight')
            self.trees.append((weight_map[int(tid)],nodes))

    def predict(self,features):
        x=np.asarray(features[:,self.indices],np.float64); result=np.zeros(len(x))
        require(np.isfinite(x).all(),'finite GBT features')
        for weight,nodes in self.trees:
            stack=[(0,np.arange(len(x)))]; leaf=np.empty(len(x))
            while stack:
                nid,rows=stack.pop()
                if not len(rows): continue
                node=nodes[nid]
                if node['leftChild']==-1: leaf[rows]=node['prediction']; continue
                split=node['split']; goes_left=x[rows,int(split['featureIndex'])]<=float(split['leftCategoriesOrThreshold'][0])
                stack.extend([(int(node['leftChild']),rows[goes_left]),(int(node['rightChild']),rows[~goes_left])])
            result+=weight*leaf
        return result
