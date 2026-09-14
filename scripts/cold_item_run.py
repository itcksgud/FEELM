"""Fixed model fits; reference ALS uses only historical training-user factors."""
from __future__ import annotations
import os
for env_name in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:
    os.environ[env_name] = '1'
import json
import subprocess
import time
import numpy as np
import pandas as pd
from cold_item_common import *

def reference_als():
    cat = np.load(OUT/'catalog.npz'); ids = cat['movie_ids']; counts = cat['reference_counts']
    # Verify the exact previously sealed factor files, not just the cached predictions.
    base = OLD/'evaluation/models/p100/ALS'
    oldseal = json.loads((base/'model-seal.json').read_text())
    for name, expected in oldseal['files'].items():
        require(pin(base/name) == expected, 'reference ALS factor seal')
    f = pd.read_parquet(base/'item-factors')
    require(np.array_equal(np.sort(f.id),ids[counts>0]), 'reference factor support')
    vectors = np.full((len(ids),32),np.nan)
    vectors[np.searchsorted(ids,f.id)] = np.vstack(f.features)
    contexts = json.loads((OUT/'contexts.json').read_text())
    values = np.zeros(contexts[-1]['stop'])
    for c in contexts:
        oi, ei, stars = np.asarray(c['oi']),np.asarray(c['ei']),np.asarray(c['stars'])
        require(len(oi)>0 and np.isfinite(vectors[oi]).all() and np.isfinite(vectors[ei]).all(), 'ALS direct prediction only')
        y = vectors[oi]
        u = np.linalg.solve(y.T@y + .1*len(oi)*np.eye(32), y.T@stars)
        values[c['start']:c['stop']] = vectors[ei]@u
    require(np.isfinite(values).all(), 'reference finite')
    return values

def run():
    reviewed(); verify_seal(OUT/'prepared-seal.json',OUT)
    cfg=config()
    require(subprocess.check_output(['docker','image','inspect',cfg['docker_image'],'--format','{{.Id}}'],text=True).strip()==cfg['image_id'],'image identity')
    if (OUT/'fit-seal.json').exists():
        seal=verify_seal(OUT/'fit-seal.json',OUT)
        require(seal['prepared_seal']==pin(OUT/'prepared-seal.json'),'existing fit parent')
        return
    info=json.loads((OUT/'feature-info.json').read_text())
    contexts=json.loads((OUT/'contexts.json').read_text())
    require(check_contexts(contexts,np.load(OUT/'catalog.npz'))==info['score_rows'],'score context count')
    logs=OUT/'logs';logs.mkdir(exist_ok=True)
    vectors={'ALS':reference_als()}
    for variant in VARIANTS:
        # Fit lighter models first; every planned model is reported, not selected here.
        for method in ['RIDGE','GBT','FM']:
            target=OUT/'models'/variant/method; seal=target/'model-seal.json'
            key=variant+'__'+method
            if seal.exists():
                s=verify_seal(seal,target)
                require(s['prepared_seal']==pin(OUT/'prepared-seal.json'),'prepared parent')
                vectors[key]=np.load(target/'aligned.npz')['prediction'];continue
            require(not target.exists(),'preserve unfinished model '+key)
            name='cold-item-'+variant.lower().replace('_','-')+'-'+method.lower()
            cmd=['docker','run','--rm','--name',name,'--network','none','--hostname','cold-item','--add-host','cold-item:127.0.0.1',
                 '-e','SPARK_LOCAL_IP=127.0.0.1','--cpus','4','--memory','12g','--memory-swap','12g',
                 '--mount',f'type=bind,source={ROOT / "scripts"},target=/scripts,readonly',
                 '--mount',f'type=bind,source={OUT},target=/data',
                 '--mount',f'type=bind,source={DOC},target=/config,readonly',cfg['docker_image'],
                 '/opt/spark/bin/spark-submit','--master','local[4]','--driver-memory','8g',
                 '--conf','spark.sql.shuffle.partitions=8','--conf','spark.ui.enabled=false','/scripts/cold_item_worker.py',variant,method]
            print('START '+key,flush=True);start=time.monotonic()
            with (logs/(name+'.log')).open('w',encoding='utf-8') as log:
                proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
                try:
                    result=proc.wait(timeout=cfg['fit_timeout_seconds']);require(result==0,'fit failed '+key)
                except BaseException:
                    subprocess.run(['docker','stop','--time','2',name],capture_output=True,timeout=20)
                    if proc.poll() is None:proc.kill()
                    write_json(OUT/(name+'-failure.json'),{'seconds':time.monotonic()-start,'returncode':proc.poll(),'fingerprint':fingerprint()})
                    raise
            p=pd.read_parquet(target/'predictions').sort_values('row_id')
            require(np.array_equal(p.row_id,np.arange(info['score_rows'])) and np.isfinite(p.prediction).all(),'aligned finite predictions')
            vectors[key]=p.prediction.to_numpy(float)
            np.savez_compressed(target/'aligned.npz',prediction=vectors[key])
            write_json(seal,{'fingerprint':fingerprint(),'prepared_seal':pin(OUT/'prepared-seal.json'),
                            'files':{p.relative_to(target).as_posix():pin(p) for p in target.rglob('*') if p.is_file()}})
            m=json.loads((target/'metrics.json').read_text())
            print(f'DONE {key}: {m["fit_seconds"]:.1f}s fit, {m.get("peak_bytes",0)/1024**3:.2f}GiB peak',flush=True)
    verify_seal(OUT/'prepared-seal.json',OUT)
    for variant in VARIANTS:
        for method in METHODS:
            target=OUT/'models'/variant/method
            seal=verify_seal(target/'model-seal.json',target)
            require(seal['prepared_seal']==pin(OUT/'prepared-seal.json'),'final model prepared parent')
    require(list(vectors)==PREDICTION_NAMES,'prediction column names/order')
    require(all(p.shape==(info['score_rows'],) and np.isfinite(p).all() for p in vectors.values()),'all prediction axes/finite')
    np.savez_compressed(OUT/'predictions.npz',names=np.array(list(vectors)),predictions=np.column_stack(list(vectors.values())))
    files={p.relative_to(OUT).as_posix():pin(p) for p in (OUT/'models').rglob('*') if p.is_file()}
    files['predictions.npz']=pin(OUT/'predictions.npz')
    write_json(OUT/'fit-seal.json',{'fingerprint':fingerprint(),'prepared_seal':pin(OUT/'prepared-seal.json'),'files':files,'target_stars_decoded':0})
    print('ALL_FITS_SEALED',flush=True)

if __name__=='__main__':run()
