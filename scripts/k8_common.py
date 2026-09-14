"""Pure helpers and immutable numerical serving rules for the K8 experiment."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import numpy as np
import psutil
import pandas as pd
from scipy import sparse

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/recommendation/experiments/fixed-k8-discovery'
OUT = ROOT / 'outputs/fixed-k8-discovery'

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

def pin(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return {'sha256': h.hexdigest(), 'bytes': Path(path).stat().st_size}

def hash_buckets(ids, prefix, modulo=100):
    return np.array([int.from_bytes(hashlib.sha256((prefix+str(int(i))).encode()).digest()[:8], 'big') % modulo for i in ids])

def norm(x):
    x = np.asarray(x, dtype=np.float64)
    z = np.sqrt(np.sum(x*x, axis=1, keepdims=True))
    return np.divide(x, z, out=np.zeros_like(x), where=z>1e-12)

def combine(blocks, weights):
    return norm(np.concatenate([np.asarray(x)*np.sqrt(w) for x,w in zip(blocks,weights)], axis=1))

def nearest(x, centers, batch=2048):
    """Fixed axis reductions, float64 distances; lower frozen ID breaks exact ties.

    No matrix multiplication here: BLAS may change summation with batch shape.
    """
    labels = np.empty(len(x), np.int32)
    loss = np.empty(len(x), np.float64)
    for start in range(0,len(x),batch):
        a=np.asarray(x[start:start+batch],dtype=np.float64)
        dist=np.stack([np.sum((a-c)**2,axis=1) for c in np.asarray(centers,dtype=np.float64)],axis=1)
        labels[start:start+len(a)]=dist.argmin(axis=1)
        loss[start:start+len(a)]=dist.min(axis=1)
    return labels,loss

def canonical(centers):
    return np.asarray(sorted(np.asarray(centers,dtype=np.float64),key=lambda c:tuple(np.round(c,12))))

def csr_ids(rows, vocabulary, idf=None):
    lookup={int(v):j for j,v in enumerate(vocabulary)}
    indices=[]; ptr=[0]
    for row in rows:
        indices.extend(sorted({lookup[int(v)] for v in row if int(v) in lookup}))
        ptr.append(len(indices))
    a=sparse.csr_matrix((np.ones(len(indices)),np.asarray(indices,np.int32),np.asarray(ptr,np.int64)),shape=(len(rows),len(vocabulary)))
    if idf is not None:
        a=a.multiply(idf).tocsr()
    return a

def transform(frame, prep):
    frame=frame.copy()
    for c in ['genre_ids','keyword_ids']:
        if c not in frame:frame[c]=[[] for _ in range(len(frame))]
        frame[c]=frame[c].map(lambda v: v if isinstance(v,(list,tuple,np.ndarray)) else ([] if pd.isna(v) else v))
        if not all(isinstance(v,(list,tuple,np.ndarray)) for v in frame[c]):raise ValueError(c+' must contain lists of integer IDs')
    if 'overview' not in frame:frame['overview']=''
    g=csr_ids(frame.genre_ids,prep['genres']).toarray()
    k=csr_ids(frame.keyword_ids,prep['keywords'],prep['keyword_idf'])
    # Sparse row normalization and fixed explicit SVD sums preserve row independence.
    from sklearn.preprocessing import normalize
    k=normalize(k,copy=False)
    kv=norm(k @ prep['keyword_components'].T)
    t=prep['text_vectorizer'].transform(frame.overview.fillna('').tolist())
    tv=norm(t @ prep['text_components'].T)
    return [norm(g),kv,tv],g

def assign(frame, bundle):
    blocks,_=transform(frame,bundle['preprocessor'])
    topx=combine(blocks,bundle['top_weights'])
    top,loss=nearest(topx,bundle['top_centers'])
    subx=combine(blocks,bundle['sub_weights'])
    child=np.zeros(len(frame),np.int32)
    for j,centers in enumerate(bundle['child_centers']):
        ix=np.flatnonzero(top==j)
        child[ix]=nearest(subx[ix],centers)[0]
    offsets=np.cumsum([0]+[len(x) for x in bundle['child_centers']])
    return {'taste_id':top,'child_id':child,'group_id':offsets[top]+child,
            'top_distance':loss,'top_supported':np.sum(topx*topx,axis=1)>1e-12,
            'content_supported':np.sum(combine(blocks,[.5,.35,.15])**2,axis=1)>1e-12,
            'genre_supported':np.sum(blocks[0]**2,axis=1)>1e-12,
            'keyword_supported':np.sum(blocks[1]**2,axis=1)>1e-12,
            'overview_supported':np.sum(blocks[2]**2,axis=1)>1e-12}

def signed_profile(vectors, history, stars, global_mean, prior=5):
    if len(history)==0:
        return np.zeros(vectors.shape[1])
    stars=np.asarray(stars,float)
    mean=(stars.sum()+prior*global_mean)/(len(stars)+prior)
    w=stars-mean
    p=(np.asarray(vectors[history]).T @ w)/(np.abs(w).sum()+prior)
    z=np.linalg.norm(p)
    return p/z if z>1e-12 else np.zeros_like(p)

def underseen(groups, viewed, n_groups, max_count=2, max_share=.2):
    count=np.bincount(groups[np.asarray(viewed,int)],minlength=n_groups)
    return (count<=max_count)&(count/max(1,len(viewed))<=max_share),count

def rank(scores, ids):
    return np.lexsort((np.asarray(ids),-np.asarray(scores)))

class Guard:
    def __init__(self,cfg,stage):
        self.cfg=cfg; self.stage=stage; self.start=time.perf_counter()
        self.peak=0; self.free=psutil.virtual_memory().available; self.done=threading.Event()
    def __enter__(self):
        if self.free<3*2**30: raise RuntimeError('Less than 3GiB available before stage')
        def monitor():
            strikes=0
            while not self.done.wait(1):
                rss=psutil.Process().memory_info().rss; free=psutil.virtual_memory().available
                self.peak=max(self.peak,rss);self.free=min(self.free,free)
                strikes=strikes+1 if rss>self.cfg['max_rss_gib']*2**30 or free<self.cfg['min_host_free_gib']*2**30 else 0
                if strikes>=3:
                    write(OUT/(self.stage+'-resource-stop.json'),self.metrics())
                    os._exit(86)
        threading.Thread(target=monitor,daemon=True).start()
        return self
    def metrics(self):
        return {'seconds':time.perf_counter()-self.start,'peak_rss':max(self.peak,psutil.Process().memory_info().rss),'minimum_host_free':self.free}
    def __exit__(self,*args):
        self.done.set();write(OUT/(self.stage+'-resources.json'),self.metrics())

def fingerprint():
    files=[DOC/'DESIGN.md',DOC/'config.json']+sorted((ROOT/'scripts').glob('k8_*.py'))
    return {p.relative_to(ROOT).as_posix():pin(p) for p in files}

def reviewed(stage):
    review=read(DOC/'execution-review.json')
    assert review['status']=='PASS' and stage in review['stages'], 'independent execution review required'
    assert review['fingerprint']==fingerprint(), 'reviewed design/code changed'

def seal(stage):
    write(OUT/(stage+'-seal.json'),{'fingerprint':fingerprint(),'files':{p.relative_to(OUT).as_posix():pin(p) for p in sorted((OUT/stage).rglob('*')) if p.is_file()}})

def verify_seal(stage):
    value=read(OUT/(stage+'-seal.json'))
    actual={p.relative_to(OUT).as_posix():pin(p) for p in sorted((OUT/stage).rglob('*')) if p.is_file()}
    assert value['files']==actual,stage+' artifact inventory/hash mismatch'
    assert value['fingerprint']==fingerprint(),stage+' reviewed lineage changed'
    return value
