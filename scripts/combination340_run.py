"""Three additional fits and common observed predictions; no evaluation labels read."""
import os
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']: os.environ[key]='4'
import argparse
import json
import subprocess
import time
import numpy as np
import pandas as pd
from combination340_common import *
from combination340_models import direct_als,load_factors,Trees
from text339_run import predict

def prepare():
    reviewed(); verify_reference(); OUT.mkdir(parents=True,exist_ok=True)
    require(not (OUT/'input-lock.json').exists(),'preserve extension lock')
    selected=json.loads((foundation.OUT/'selection.json').read_text())['selected']
    paths=[foundation.OUT/'selection.json',foundation.OUT/'evaluation-seal.json',foundation.DOC/'result-review.json',
           foundation.OUT/'prepared-seal.json',REFERENCE/'model-seal.json',OLD/'ratings.parquet',OLD/'contexts.json',OLD/'catalog.parquet']
    paths += [foundation.OUT/selected/name for name in ['train.parquet','score.parquet','feature-info.json','model/partition-identity.json']]
    write_json(OUT/'input-lock.json',{'selected':selected,'parents':{p.relative_to(ROOT).as_posix():pin(p) for p in paths}})
    print('LOCKED',selected,flush=True)

def fit():
    reviewed(); fixed=lock(); selected=fixed['selected']; cfg=config()
    if (OUT/'fit-seal.json').exists(): verify('fit-seal.json'); print('EXISTING_FIT_VERIFIED'); return
    require(not (OUT/'predictions.npz').exists(),'preserve partial combined predictions')
    require(subprocess.check_output(['docker','image','inspect',cfg['docker_image'],'--format','{{.Id}}'],text=True).strip()==cfg['image_id'],'pinned Spark image')
    expected_identity=json.loads((foundation.OUT/selected/'model/partition-identity.json').read_text())
    for method in METHODS:
        folder=OUT/method; record_path=folder/'model-seal.json'
        if not record_path.exists():
            require(not folder.exists(),'preserve unfinished extension '+method)
            name='combination340-'+method.lower().replace('_','-'); logs=OUT/'logs'; logs.mkdir(exist_ok=True)
            cmd=['docker','run','--rm','--name',name,'--network','none','--hostname','combination340','--add-host','combination340:127.0.0.1',
                 '-e','SPARK_LOCAL_IP=127.0.0.1','--cpus','4','--memory','12g','--memory-swap','12g',
                 '--mount',f'type=bind,source={ROOT/"scripts"},target=/scripts,readonly',
                 '--mount',f'type=bind,source={foundation.OUT/selected},target=/base,readonly',
                 '--mount',f'type=bind,source={OLD/"ratings.parquet"},target=/ratings.parquet,readonly',
                 '--mount',f'type=bind,source={OUT},target=/data',
                 '--mount',f'type=bind,source={DOC},target=/config,readonly',cfg['docker_image'],
                 '/opt/spark/bin/spark-submit','--master','local[4]','--driver-memory','8g',
                 '--conf','spark.sql.shuffle.partitions=8','--conf','spark.sql.adaptive.enabled=false',
                 '--conf','spark.ui.enabled=false','/scripts/combination340_worker.py',method]
            write_json(OUT/'run-progress.json',{'stage':'FITTING','method':method,'container':name,'started_epoch':time.time()})
            print('START',method,flush=True)
            with (logs/(name+'.log')).open('x',encoding='utf-8') as log:
                process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
                try: require(process.wait(timeout=cfg['fit_timeout_seconds'])==0,'extension fit failed '+method)
                except BaseException:
                    subprocess.run(['docker','stop','--time','2',name],capture_output=True,timeout=20)
                    if process.poll() is None: process.kill()
                    raise
            if method!='ALS':
                require(json.loads((folder/'partition-identity.json').read_text())==expected_identity,'same actual ordered row partitions')
            seal(method+'/model-seal.json',[p.relative_to(OUT).as_posix() for p in folder.rglob('*') if p.is_file()])
        verify(method+'/model-seal.json'); print('FIT_COMPLETE',method,flush=True)
    cols=[f'x{i:03d}' for i in range(230)]
    x=pd.read_parquet(foundation.OUT/selected/'score.parquet',columns=cols).to_numpy(np.float32)
    original=np.load(foundation.OUT/'predictions.npz'); s=original['predictions'][:,original['names'].tolist().index(selected)]
    scores={'S':s}; validation={}
    for method in ['NO_RESPONSE','GBT']:
        frame=pd.read_parquet(OUT/method/'predictions').sort_values('row_id')
        require(np.array_equal(frame.row_id,np.arange(93230)) and np.isfinite(frame.prediction).all(),'complete extension predictions')
        actual=frame.prediction.to_numpy()
        if method=='NO_RESPONSE': manual=predict(x,dict(np.load(OUT/method/'portable.npz')))
        else:
            trees=Trees(OUT/method/'native',range(230)); manual=trees.predict(x)
            fixtures=np.load(OUT/method/'threshold-fixtures.npz'); error=float(abs(trees.predict(fixtures['features'])-fixtures['predictions']).max())
            require(error<=1e-8,'native exact threshold parity'); validation['GBT_threshold_max_error']=error
        error=float(abs(actual-manual).max()); require(error<=1e-8,'all native/portable predictions')
        validation[method+'_max_error']=error; scores[method]=actual
    cats=pd.read_parquet(OLD/'catalog.parquet'); ids=cats.movie_id.to_numpy(); count=cats.train_count.to_numpy()
    actual_factors=load_factors(OUT/'ALS/item-factors',ids,count>0)
    reference_factors=load_factors(REFERENCE/'item-factors',ids,cats.reference_count.to_numpy()>0)
    contexts=json.loads((OLD/'contexts.json').read_text()); availability={}; input_rows=[]
    for name,factors in [('ACTUAL_ALS',actual_factors),('REFERENCE_ALS',reference_factors)]:
        pred=np.full(93230,np.nan); direct=np.zeros(93230,bool)
        for c in contexts:
            p,d,n=direct_als(factors,c['oi'],c['stars'],c['ei']); sl=slice(c['start'],c['stop']); pred[sl]=p; direct[sl]=d
            input_rows.append({'model':name,'uid':c['uid'],'cap':c['cap'],'h':c['h'],'supported_inputs':n,'target_rows':len(p),'direct_rows':int(d.sum())})
        scores[name]=pred; availability[name]=direct
    for w in cfg['blend_weights']:
        values=s.copy(); direct=availability['ACTUAL_ALS']; values[direct]=(1-w)*s[direct]+w*scores['ACTUAL_ALS'][direct]
        require(np.array_equal(values[~direct],s[~direct]),'unsupported blend exactly content')
        scores['BLEND_'+str(w)]=values
    np.savez_compressed(OUT/'predictions.npz',names=np.array(list(scores)),predictions=np.column_stack(list(scores.values())),actual_direct=availability['ACTUAL_ALS'],reference_direct=availability['REFERENCE_ALS'])
    pd.DataFrame(input_rows).to_csv(OUT/'als-availability.csv',index=False)
    write_json(OUT/'numerical-checks.json',validation)
    files=['predictions.npz','als-availability.csv','numerical-checks.json']
    files += [p.relative_to(OUT).as_posix() for method in METHODS for p in (OUT/method).rglob('*') if p.is_file()]
    seal('fit-seal.json',files)
    write_json(OUT/'run-progress.json',{'stage':'FIT_COMPLETE','selected_foundation':selected})
    print('EXTENSION_FIT_SEALED',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['prepare','fit']); args=parser.parse_args()
    prepare() if args.action=='prepare' else fit()
