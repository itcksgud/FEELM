"""Four reviewed fits, portable whole-catalog scoring and immutable predictions."""
from __future__ import annotations
import os
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='4'
import argparse
import json
import subprocess
import time
import numpy as np
import pandas as pd
from text339_common import *
from text339_features import Features
from text339_prepare import load_embeddings

def predict(x,model):
    x=x[:,model['indices']].astype(np.float64)
    f=model['factors'];linear=model['linear']
    return float(model['intercept'])+x@linear+.5*np.sum((x@f)**2-(x*x)@(f*f),axis=1)

def verify_model(variant):
    folder=OUT/'models'/variant
    record=json.loads((folder/'model-seal.json').read_text())
    require(record['fingerprint']==fingerprint() and record['prepared_seal']==pin(OUT/'prepared-seal.json'),'model parents')
    for n,p in record['files'].items():require(pin(folder/n)==p,'model artifact '+variant+'/'+n)
    return record

def fit():
    reviewed();verify('prepared-seal.json');cfg=config()
    require(subprocess.check_output(['docker','image','inspect',cfg['docker_image'],'--format','{{.Id}}'],text=True).strip()==cfg['image_id'],'pinned Docker image')
    info=json.loads((OUT/'feature-info.json').read_text());vectors=[]
    require(check_contexts(json.loads((OUT/'contexts.json').read_text()),pd.read_parquet(OUT/'catalog.parquet'))==info['score_rows'],'checked prediction population')
    for variant in VARIANTS:
        folder=OUT/'models'/variant;seal_path=folder/'model-seal.json'
        if not seal_path.exists():
            require(not folder.exists(),'preserve unfinished model '+variant)
            name='text339-'+variant.lower();logs=OUT/'logs';logs.mkdir(exist_ok=True)
            cmd=['docker','run','--rm','--name',name,'--network','none','--hostname','text339','--add-host','text339:127.0.0.1',
                 '-e','SPARK_LOCAL_IP=127.0.0.1','--cpus','4','--memory','12g','--memory-swap','12g',
                 '--mount',f'type=bind,source={ROOT/"scripts"},target=/scripts,readonly',
                 '--mount',f'type=bind,source={OUT},target=/data',
                 '--mount',f'type=bind,source={DOC},target=/config,readonly',cfg['docker_image'],
                 '/opt/spark/bin/spark-submit','--master','local[4]','--driver-memory','8g',
                 '--conf','spark.sql.shuffle.partitions=8','--conf','spark.ui.enabled=false','/scripts/text339_worker.py',variant]
            print('START',variant,flush=True)
            with (logs/(name+'.log')).open('w',encoding='utf-8') as log:
                proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
                try:require(proc.wait(timeout=cfg['fit_timeout_seconds'])==0,'fit failed '+variant)
                except BaseException:
                    subprocess.run(['docker','stop','--time','2',name],capture_output=True,timeout=20)
                    if proc.poll() is None:proc.kill()
                    raise
            pred=pd.read_parquet(folder/'predictions').sort_values('row_id')
            require(np.array_equal(pred.row_id,np.arange(info['score_rows'])) and np.isfinite(pred.prediction).all(),'ordered predictions')
            np.savez_compressed(folder/'aligned.npz',prediction=pred.prediction.to_numpy())
            write_json(seal_path,{'fingerprint':fingerprint(),'prepared_seal':pin(OUT/'prepared-seal.json'),
                                  'files':{p.relative_to(folder).as_posix():pin(p) for p in folder.rglob('*') if p.is_file()}})
        verify_model(variant)
        vectors.append(np.load(folder/'aligned.npz')['prediction'])
        print('SEALED',variant,json.loads((folder/'metrics.json').read_text())['fit_seconds'],flush=True)
    require(not (OUT/'fit-seal.json').exists(),'preserve fit seal')
    np.savez_compressed(OUT/'predictions.npz',names=np.array(VARIANTS),predictions=np.column_stack(vectors))
    seal('fit-seal.json',['predictions.npz']+[f'models/{v}/model-seal.json' for v in VARIANTS],prepared_seal=pin(OUT/'prepared-seal.json'),target_stars_decoded=0)

def catalog():
    reviewed();verify('prepared-seal.json');verify('fit-seal.json')
    require(not (OUT/'catalog-top10.parquet').exists(),'preserve catalog predictions')
    cfg=config();meta=pd.read_parquet(ROOT/cfg['metadata']);ids=meta.movie_id.to_numpy()
    text=pd.read_parquet(OUT/'texts.parquet');cats=pd.read_parquet(OUT/'catalog.parquet')
    pc=np.load(OUT/'projection.npz');projections={k:{n:pc[k+'_'+n] for n in ['mean','basis','scale']} for k in ['overview','wiki']}
    features=Features(meta,load_embeddings(),projections)
    for variant in VARIANTS:verify_model(variant)
    models={v:dict(np.load(OUT/'models'/v/'portable.npz')) for v in VARIANTS}
    contexts=json.loads((OUT/'contexts.json').read_text());rows=[];costs=[]
    dates=pd.to_datetime(text.release_date,format='%Y-%m-%d',errors='coerce',utc=True)
    current=dates.notna().to_numpy()&(dates.astype('int64').to_numpy()//10**9<=cfg['catalog_snapshot_timestamp'])
    for number,c in enumerate([c for c in contexts if c['cap']==10]):
        start=time.monotonic();excluded=np.asarray(c['viewed'],int)
        allowed=current.copy();allowed[excluded]=False;ei=np.flatnonzero(allowed)
        scores=np.empty((len(ei),4))
        for pos in range(0,len(ei),4096):
            chunk=ei[pos:pos+4096];x=features.transform(c['oi'],c['stars'],chunk)
            for j,v in enumerate(VARIANTS):scores[pos:pos+len(chunk),j]=predict(x,models[v])
        require(np.isfinite(scores).all(),'finite complete catalog scoring')
        for j,v in enumerate(VARIANTS):
            order=np.lexsort((ids[ei],-scores[:,j]))[:10]
            for rank,ix in enumerate(order,1):
                i=int(ei[ix]);rows.append({'uid':c['uid'],'cap':10,'h':c['h'],'variant':v,'rank':rank,'movie_id':int(ids[i]),'prediction':float(scores[ix,j]),
                                        'train_count':int(cats.train_count.iloc[i]),'blocked':bool(cats.blocked.iloc[i]),'wiki_T2_available':bool(text.T2.iloc[i]),'wiki_T3_available':bool(text.T3.iloc[i]),
                                        'variant_wiki_present':bool(text[v].iloc[i]) if v in ['T2','T3'] else False})
        costs.append({'uid':c['uid'],'cap':10,'h':c['h'],'candidates':len(ei),'joint_4model_seconds':time.monotonic()-start})
        if number%10==0:print('CATALOG',number,flush=True)
    pd.DataFrame(rows).to_parquet(OUT/'catalog-top10.parquet',index=False);pd.DataFrame(costs).to_csv(OUT/'catalog-timing.csv',index=False)
    seal('catalog-seal.json',['catalog-top10.parquet','catalog-timing.csv'],fit_seal=pin(OUT/'fit-seal.json'),target_stars_decoded=0)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['fit','catalog']);args=p.parse_args()
    fit() if args.action=='fit' else catalog()
