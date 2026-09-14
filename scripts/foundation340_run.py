"""Bounded four-model execution; label-free scoring and persisted evidence."""
import os
for key in ['OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[key] = '4'
import argparse
import json
import subprocess
import time
import numpy as np
import pandas as pd
from foundation340_common import *
from foundation340_features import Features, RATING_COLUMNS
from text339_run import predict

def fit():
    reviewed(); verify('prepared-seal.json')
    if (OUT/'fit-seal.json').exists():
        verify('fit-seal.json'); print('EXISTING_FIT_VERIFIED',flush=True); return
    require(not (OUT/'predictions.npz').exists(),'preserve unsealed final predictions')
    audit=json.loads((DOC/'prepared-review.json').read_text())
    require(audit['status']=='PASS' and audit['prepared_seal']==pin(OUT/'prepared-seal.json'),'independent prepared audit')
    cfg=config()
    require(subprocess.check_output(['docker','image','inspect',cfg['docker_image'],'--format','{{.Id}}'],text=True).strip()==cfg['image_id'],'pinned image')
    vectors=[]; first_identity=None
    for v in VARIANTS:
        folder=OUT/v/'model'; modelseal=folder/'model-seal.json'
        if not modelseal.exists():
            require(not folder.exists(),'preserve incomplete model '+v)
            name='foundation340-'+v.lower(); logs=OUT/'logs'; logs.mkdir(exist_ok=True)
            cmd=['docker','run','--rm','--name',name,'--network','none','--hostname','foundation340','--add-host','foundation340:127.0.0.1',
                 '-e','SPARK_LOCAL_IP=127.0.0.1','--cpus','4','--memory','12g','--memory-swap','12g',
                 '--mount',f'type=bind,source={ROOT/"scripts"},target=/scripts,readonly',
                 '--mount',f'type=bind,source={OUT/v},target=/data',
                 '--mount',f'type=bind,source={DOC},target=/config,readonly',cfg['docker_image'],
                 '/opt/spark/bin/spark-submit','--master','local[4]','--driver-memory','8g',
                 '--conf','spark.sql.shuffle.partitions=8','--conf','spark.sql.adaptive.enabled=false',
                 '--conf','spark.ui.enabled=false','/scripts/foundation340_worker.py',v]
            write_json(OUT/'run-progress.json',{'stage':'FITTING','variant':v,'started_epoch':time.time(),'container':name})
            print('START',v,flush=True)
            with (logs/(name+'.log')).open('w',encoding='utf-8') as log:
                proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
                try: require(proc.wait(timeout=cfg['fit_timeout_seconds'])==0,'fit failed '+v)
                except BaseException:
                    subprocess.run(['docker','stop','--time','2',name],capture_output=True,timeout=20)
                    if proc.poll() is None: proc.kill()
                    raise
            pred=pd.read_parquet(folder/'predictions').sort_values('row_id')
            require(np.array_equal(pred.row_id,np.arange(93230)) and np.isfinite(pred.prediction).all(),'all predictions')
            np.savez_compressed(folder/'aligned.npz',prediction=pred.prediction.to_numpy())
            write_json(modelseal,{'fingerprint':fingerprint(),'prepared_seal':pin(OUT/'prepared-seal.json'),'files':{p.relative_to(folder).as_posix():pin(p) for p in folder.rglob('*') if p.is_file()}})
        verify_model(v)
        identity=json.loads((folder/'partition-identity.json').read_text())
        if first_identity is None: first_identity=identity
        require(identity==first_identity,'identical actual ordered Spark partitions across conditions')
        vectors.append(np.load(folder/'aligned.npz')['prediction'])
        print('FIT_COMPLETE',v,(folder/'metrics.json').read_text(),flush=True)
    np.savez_compressed(OUT/'predictions.npz',names=np.array(VARIANTS),predictions=np.column_stack(vectors))
    seal('fit-seal.json',['predictions.npz']+[f'{v}/model/model-seal.json' for v in VARIANTS],prepared_seal=pin(OUT/'prepared-seal.json'))
    write_json(OUT/'run-progress.json',{'stage':'FIT_COMPLETE','variants':VARIANTS})

def catalog():
    reviewed(); verify('prepared-seal.json'); verify('fit-seal.json')
    require(not (OUT/'catalog-top10.parquet').exists(),'preserve catalog scores')
    cats=pd.read_parquet(OLD/'catalog.parquet'); ids=cats.movie_id.to_numpy()
    meta=pd.read_parquet(ROOT/config()['metadata']); texts=pd.read_parquet(OLD/'texts.parquet',columns=['release_date'])
    features=Features(meta,cats); models={v:dict(np.load(OUT/v/'model/portable.npz')) for v in VARIANTS}
    dates=pd.to_datetime(texts.release_date,format='%Y-%m-%d',errors='coerce',utc=True)
    current=dates.notna().to_numpy() & (dates.astype('int64').to_numpy()//10**9 <= config()['catalog_snapshot_timestamp'])
    contexts=json.loads((OLD/'contexts.json').read_text()); rows=[]; costs=[]
    for number,c in enumerate([c for c in contexts if c['cap']==10]):
        started=time.monotonic(); allowed=current.copy(); allowed[np.array(c['viewed'],int)]=False; ei=np.flatnonzero(allowed)
        scores=np.empty((len(ei),4))
        for pos in range(0,len(ei),4096):
            chunk=ei[pos:pos+4096]; x=features.reference(c['oi'],c['stars'],chunk)
            xr=features.reference(c['oi'],c['stars'],chunk,True)
            for j,v in enumerate(VARIANTS): scores[pos:pos+len(chunk),j]=predict(xr if v in ['R','RH'] else x,models[v])
        require(np.isfinite(scores).all(),'complete finite catalog')
        for j,v in enumerate(VARIANTS):
            order=np.lexsort((ids[ei],-scores[:,j]))[:10]
            for rank,which in enumerate(order,1):
                i=int(ei[which]); rows.append({'uid':c['uid'],'h':c['h'],'variant':v,'rank':rank,'movie_id':int(ids[i]),'prediction':float(scores[which,j]),'train_count':int(cats.train_count.iloc[i]),'blocked':bool(cats.blocked.iloc[i])})
        costs.append({'uid':c['uid'],'h':c['h'],'candidates':len(ei),'joint_seconds':time.monotonic()-started})
        if number%10==0: print('CATALOG',number,flush=True)
    pd.DataFrame(rows).to_parquet(OUT/'catalog-top10.parquet',index=False)
    pd.DataFrame(costs).to_csv(OUT/'catalog-timing.csv',index=False)
    seal('catalog-seal.json',['catalog-top10.parquet','catalog-timing.csv'],fit_seal=pin(OUT/'fit-seal.json'))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['fit','catalog']); args=parser.parse_args()
    fit() if args.action=='fit' else catalog()
