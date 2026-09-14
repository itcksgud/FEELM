"""Finite continuous LightGBM native-text inference, checked against native JVM."""
from pathlib import Path
import numpy as np

class Booster:
    def __init__(self,path):
        text=Path(path).read_text(encoding='utf-8'); self.trees=[]
        assert 'num_class=1\n' in text and 'num_tree_per_iteration=1\n' in text
        assert 'max_feature_idx=229\n' in text and 'average_output' not in text
        for block in text.split('Tree=')[1:]:
            d={}
            for line in block.splitlines()[1:]:
                if not line.strip():break
                k,v=line.split('=',1); d[k]=v
            n=int(d['num_leaves']); assert int(d['num_cat'])==0
            t={k:np.fromstring(d[k],sep=' ',dtype=int if k in ['split_feature','left_child','right_child','decision_type'] else float)
               for k in ['split_feature','threshold','left_child','right_child','leaf_value','decision_type']}
            assert len(t['leaf_value'])==n and all(len(t[k])==n-1 for k in t if k!='leaf_value')
            assert np.all((t['decision_type']&1)==0) and np.all(((t['decision_type']>>2)&3)!=1)
            assert np.isfinite(t['threshold']).all() and np.isfinite(t['leaf_value']).all()
            assert np.all((t['split_feature']>=0)&(t['split_feature']<230))
            visited=set();leaves=set();stack=[0] if n>1 else [-1]
            while stack:
                node=int(stack.pop())
                if node<0:
                    leaf=-node-1;assert 0<=leaf<n and leaf not in leaves;leaves.add(leaf)
                else:
                    assert 0<=node<n-1 and node not in visited;visited.add(node)
                    stack.extend([t['left_child'][node],t['right_child'][node]])
            assert len(visited)==n-1 and len(leaves)==n
            self.trees.append(t)
        assert self.trees
    def predict(self,x):
        x=np.asarray(x,float); assert x.ndim==2 and x.shape[1]==230 and np.isfinite(x).all()
        result=np.zeros(len(x))
        for t in self.trees:
            if not len(t['threshold']):result+=t['leaf_value'][0];continue
            nodes=np.zeros(len(x),int)
            while np.any(nodes>=0):
                rows=np.flatnonzero(nodes>=0); n=nodes[rows]
                left=x[rows,t['split_feature'][n]]<=t['threshold'][n]
                nodes[rows]=np.where(left,t['left_child'][n],t['right_child'][n])
            result+=t['leaf_value'][-nodes-1]
        return result
    def fixtures(self,base):
        rows=[]
        # Exercise each tree root at exact threshold and adjacent float64 values.
        for t in self.trees:
            if not len(t['threshold']):continue
            f=int(t['split_feature'][0]); v=t['threshold'][0]
            for z in [np.nextafter(v,-np.inf),v,np.nextafter(v,np.inf)]:
                a=np.asarray(base,float).copy();a[f]=z;rows.append(a)
        return np.array(rows) if rows else np.asarray(base,float)[None,:]
