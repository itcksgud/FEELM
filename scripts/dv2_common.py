"""Versioned local discovery v2 utilities; old frozen geometry is read-only."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pickle
import threading
import time
import numpy as np
import pandas as pd
import psutil

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/recommendation/experiments/fixed-k8-discovery-v2'
CFG = json.loads((DOC/'config.json').read_text(encoding='utf-8'))
OUT = ROOT / 'outputs/fixed-k8-discovery-v2' / CFG['version']
OLD = Path(CFG['previous_root'])
OLDOUT = OLD/'outputs/fixed-k8-discovery'

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def pin(path):
    p=Path(path);h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(2**20),b''):h.update(b)
    return {'sha256':h.hexdigest(),'bytes':p.stat().st_size}

def array_hash(a):
    a=np.ascontiguousarray(a)
    return hashlib.sha256(str(a.dtype).encode()+str(a.shape).encode()+a.tobytes()).hexdigest()

def load_old():
    path=OLD/'scripts/k8_common.py'
    assert pin(path)['sha256']=='b87f79cca4499f1310c9ab1c96a6ad14f59e21f3d2081212a6e053dd5676a349'
    spec=importlib.util.spec_from_file_location('frozen_v1_helpers',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

old=load_old()
norm=old.norm;combine=old.combine;nearest=old.nearest;transform=old.transform;canonical=old.canonical

def frozen_bundle():
    p=OLDOUT/'final/bundle.pkl'
    assert pin(p)['sha256']=='cbdc51940b9f3fe9a295ae802e4af45009397af64231c6a79884e673d58ef937'
    with p.open('rb') as f:return pickle.load(f)

def rank(scores,ids):
    scores=np.asarray(scores,float);ids=np.asarray(ids)
    assert np.isfinite(scores).all()
    return np.lexsort((ids,-scores))

def fingerprint(stage,files):
    paths=[DOC/'EXECUTION.md',DOC/'config.json',Path(__file__)]+[ROOT/'scripts'/f for f in files]
    return {'stage':stage,'files':{str(p):pin(p) for p in sorted(set(paths))}}

def reviewed(stage,files):
    v=read(DOC/(stage+'-execution-review.json'))
    assert v['status']=='PASS' and v['fingerprint']==fingerprint(stage,files),'exact independent execution review required'

def seal(stage):
    p=OUT/stage
    write(OUT/(stage+'-seal.json'),{'stage':stage,'files':{str(f.relative_to(OUT)):pin(f) for f in sorted(p.rglob('*')) if f.is_file()}})

def verify(stage):
    expected=read(OUT/(stage+'-seal.json'))['files']
    actual={str(f.relative_to(OUT)):pin(f) for f in sorted((OUT/stage).rglob('*')) if f.is_file()}
    assert expected==actual,stage+' output changed'

class Guard:
    def __init__(self,stage):
        self.stage=stage;self.start=time.perf_counter();self.cpu0=time.process_time()
        self.done=threading.Event();self.peak=0;self.free=psutil.virtual_memory().available
    def __enter__(self):
        assert self.free>=CFG['min_host_free_gib']*2**30,'insufficient initial host memory'
        prior_stages={}
        for pattern in ['*/*-resources.json','*/*-resource-stop.json']:
            for p in OUT.parent.glob(pattern):
                key=str(p).replace('-resources.json','').replace('-resource-stop.json','')
                prior_stages[key]=max(prior_stages.get(key,0),read(p).get('wall_seconds',0))
        completed=sum(prior_stages.values())
        assert completed<CFG['max_total_compute_seconds'],'total local compute budget exhausted'
        def monitor():
            strikes=0;ticks=0
            while not self.done.wait(1):
                rss=psutil.Process().memory_info().rss;free=psutil.virtual_memory().available
                self.peak=max(self.peak,rss);self.free=min(self.free,free)
                bad=rss>CFG['max_rss_gib']*2**30 or free<CFG['min_host_free_gib']*2**30
                strikes=strikes+1 if bad else 0
                ticks+=1
                elapsed=time.perf_counter()-self.start
                output_exceeded=ticks%30==0 and sum(p.stat().st_size for p in OUT.parent.rglob('*') if p.is_file())>CFG['max_output_gib']*2**30
                if strikes>=3 or elapsed>CFG['max_stage_seconds'] or completed+elapsed>CFG['max_total_compute_seconds'] or output_exceeded:
                    write(OUT/(self.stage+'-resource-stop.json'),self.metrics());os._exit(86)
        threading.Thread(target=monitor,daemon=True).start();return self
    def metrics(self):
        return {'wall_seconds':time.perf_counter()-self.start,'cpu_seconds':time.process_time()-self.cpu0,'peak_rss':max(self.peak,psutil.Process().memory_info().rss),'minimum_host_free':self.free}
    def __exit__(self,*args):
        self.done.set();write(OUT/(self.stage+'-resources.json'),self.metrics())

def require_fresh(stage,terminal):
    p=OUT/stage;p.mkdir(parents=True,exist_ok=True)
    assert not (p/terminal).exists(),'preserve previous completed stage: '+stage
    return p

def profile(x,c,mode='signed'):
    h=np.asarray(c['history'],int);r=np.asarray(c['stars'],float)
    original_r=np.asarray(c.get('original_stars',c['stars']),float)
    support=np.sum(x[h]**2,axis=1)>1e-12
    if c['raw_pre_count']==0:state='ACTUAL_NO_HISTORY'
    elif c['cap']==0:state='HIDDEN_CAP0'
    elif not len(original_r):state='NO_SELECTED_INPUT'
    elif not len(h):state='UNMAPPED_INPUT'
    elif not support.any():state='UNSUPPORTED_INPUT'
    else:state='VALID'
    prior=CFG['profile_prior'];mean=(original_r.sum()+prior*CFG['profile_prior_mean'])/(len(original_r)+prior)
    anchor=3+.5*(mean-3);w=(r-anchor)[support];xx=x[h[support]]
    absw=float(np.abs(w).sum());wn=float(np.sum(w*w));neff=absw**2/wn if wn else 0.
    if mode=='signed':raw=np.sum(xx*w[:,None],axis=0)/(absw+prior)
    elif mode=='two_branch':
        pos=w>0;neg=w<0
        p=np.sum(xx[pos]*w[pos,None],axis=0)/w[pos].sum() if pos.any() else np.zeros(x.shape[1])
        n=np.sum(xx[neg]*(-w[neg,None]),axis=0)/(-w[neg]).sum() if neg.any() else np.zeros(x.shape[1])
        raw=p-n
    else:raise ValueError(mode)
    length=float(np.linalg.norm(raw))
    if state=='VALID' and length<=CFG['profile_epsilon']:state='CANCELLED_OR_TINY'
    direction=raw/length if state=='VALID' else np.zeros(x.shape[1])
    return direction,{'state':state,'original_inputs':len(original_r),'mapped_inputs':len(h),'supported_inputs':int(support.sum()),'anchor':float(anchor),'raw_norm':length,'effective_n':neff,'confidence':neff/(neff+prior),'positive_inputs':int((w>0).sum()),'negative_inputs':int((w<0).sum()),'mode':mode}

def eligibility(frame,x):
    date=pd.to_datetime(frame.release_date,format='%Y-%m-%d',errors='coerce')
    return (date.notna()&(date<=pd.Timestamp(CFG['candidate_date']))&frame.status.eq('Released')&frame.raw_adult_state.eq('FALSE')&frame.raw_video_state.eq('FALSE')).to_numpy() & (np.sum(x*x,axis=1)>1e-12)

def quality(frame,base,kind='movie_mean',m=None):
    m=CFG['quality_m'] if m is None else m
    valid=frame.quality_state.eq('VALID').to_numpy();use=base&valid
    r=frame.raw_vote_average_number.to_numpy(float);v=frame.raw_vote_count_number.to_numpy(float)
    assert use.any()
    C=float(np.mean(r[use])) if kind=='movie_mean' else float(np.sum(v[use]*r[use])/np.sum(v[use]))
    q=np.full(len(frame),np.nan);q[valid]=(v[valid]*r[valid]+m*C)/(v[valid]+m)
    legal=np.flatnonzero(use);order=legal[np.lexsort((frame.service_movie_id.to_numpy()[legal],-v[legal],-q[legal]))]
    return q,order,{'C':C,'m':m,'C_definition':kind,'reference_count':int(use.sum()),'reference_id_hash':array_hash(frame.service_movie_id.to_numpy()[use])}
