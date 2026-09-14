"""Label-blind query construction and sequential bounded SynapseML fits."""
import argparse
import subprocess
import time
import numpy as np
import pandas as pd
from research343_common import *
from research343_lgb_models import Booster

def prepare():
    reviewed('lgb');lock(); require(not (OUT/'queries.parquet').exists(),'preserve query sidecar')
    ep=pd.read_parquet(OLD/'episodes.parquet'); ratings=pd.read_parquet(OLD/'ratings.parquet',columns=['uid','rating'])
    base=pd.read_parquet(FOUND/'R/train.parquet',columns=['row_id','uid','label'])
    qid=np.repeat(np.arange(len(ep),dtype=np.int32),ep.targets.to_numpy())
    require(len(ep)==73771 and len(qid)==4997069,'query census')
    require(np.array_equal(base.row_id,np.arange(len(base))) and np.array_equal(base.uid,ratings.uid) and np.array_equal(base.label,ratings.rating),'training row alignment')
    require(np.array_equal(base.uid,np.repeat(ep.uid,ep.targets)),'query user identity')
    groups=base.assign(qid=qid).groupby('qid').label.agg(['size','nunique'])
    require(int(groups['size'].eq(1).sum())==2739 and int(groups['nunique'].gt(1).sum())==70057 and int(groups.loc[groups['nunique'].gt(1),'size'].sum())==4988691,'query rating support')
    pd.DataFrame({'row_id':base.row_id,'qid':qid}).to_parquet(OUT/'queries.parquet',index=False)
    write_json(OUT/'query-census.json',{'queries':len(ep),'rows':len(qid),'singletons':2739,'unequal_queries':70057,'unequal_rows':4988691})
    seal('query-seal.json',['queries.parquet','query-census.json'],execution=fingerprint('lgb'))

def fit(method):
    reviewed('lgb');lock();verify('query-seal.json');cfg=read(DOC/'config.json')
    started_fingerprint=fingerprint('lgb');started_lock=pin(OUT/'input-lock.json')
    started_query=pin(OUT/'query-seal.json')
    runtime=read(BASE/'research343-runtime-probe/build/result.json')
    runtimepin=pin(BASE/'research343-runtime-probe/build/result.json')
    require(runtimepin==read(DOC/'lgb-review.json')['runtime_result'],'reviewed runtime pin')
    require(runtime['status']=='PASS_BUILT_EXACT_PROBE_RUNTIME','pinned isolated runtime')
    tag='feelm-research343-synapse:local'; imageid=subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip()
    require(imageid==runtime['image_id'],'runtime image identity')
    if method=='LGBM_RANK_R':
        reg=verify('LGBM_REG_R-fit-seal.json')
        require(reg['execution']==started_fingerprint and reg['query_seal']==started_query and reg['runtime_result']==runtimepin,'matched paired regression parents')
    require(not (OUT/method).exists(),'preserve partial model')
    name='research343-'+method.lower().replace('_','-'); logs=OUT/'logs';logs.mkdir(exist_ok=True)
    cmd=['docker','run','--rm','--name',name,'--network','none','--hostname','research343','--add-host','research343:127.0.0.1',
         '-e','SPARK_LOCAL_IP=127.0.0.1','--cpus','4','--memory','12g','--memory-swap','12g',
         '--mount',f'type=bind,source={ROOT/"scripts"},target=/scripts,readonly',
         '--mount',f'type=bind,source={FOUND/"R"},target=/base,readonly',
         '--mount',f'type=bind,source={OUT/"queries.parquet"},target=/queries.parquet,readonly',
         '--mount',f'type=bind,source={OUT},target=/data',
         '--mount',f'type=bind,source={DOC},target=/config,readonly',tag,
         'python3','-c','import os,sys; os.execv("/opt/spark/bin/spark-submit",["spark-submit","--jars",os.environ["FEELM_SYNAPSE_JARS"],*sys.argv[1:]])',
         '--master','local[4]','--driver-memory','8g',
         '--conf','spark.sql.shuffle.partitions=2','--conf','spark.sql.adaptive.enabled=false',
         '--conf','spark.ui.enabled=false','/scripts/research343_lgb_worker.py',method]
    write_json(logs/(method+'-command.json'),{'command':cmd,'runtime':runtime,'execution':fingerprint('lgb')})
    with (logs/(method+'.log')).open('x',encoding='utf-8') as log:
        p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
        try:require(p.wait(timeout=cfg['fit_timeout_seconds'])==0,'fit failed '+method)
        except BaseException:
            subprocess.run(['docker','stop','--time','2',name],capture_output=True,timeout=20)
            if p.poll() is None:p.kill()
            raise
    folder=OUT/method
    x=pd.read_parquet(FOUND/'R/score.parquet',columns=[f'x{i:03d}' for i in range(230)]).to_numpy(np.float32)
    model=Booster(folder/'model.txt'); pred=np.load(folder/'predictions.npy');error=float(abs(model.predict(x)-pred).max())
    fixture=np.load(folder/'threshold-fixtures.npz');boundary=float(abs(model.predict(fixture['features'])-fixture['predictions']).max())
    require(max(error,boundary)<=1e-8,'native/portable prediction parity')
    if method=='LGBM_RANK_R':
        verify('LGBM_REG_R-fit-seal.json')
        require(read(folder/'partition-identity.json')==read(OUT/'LGBM_REG_R/partition-identity.json'),'matched query partitions')
    write_json(folder/'checks.json',{'observed_max_error':error,'boundary_max_error':boundary})
    reviewed('lgb');lock();require(started_fingerprint==fingerprint('lgb') and started_lock==pin(OUT/'input-lock.json'),'no execution drift')
    verify('query-seal.json');require(started_query==pin(OUT/'query-seal.json') and runtimepin==pin(BASE/'research343-runtime-probe/build/result.json'),'query/runtime drift')
    seal(method+'-fit-seal.json',[p.relative_to(OUT).as_posix() for p in folder.rglob('*') if p.is_file()]+[f'logs/{method}.log',f'logs/{method}-command.json'],execution=started_fingerprint,query_seal=pin(OUT/'query-seal.json'),runtime_result=runtimepin)
    print('FIT_COMPLETE',method,read(folder/'metrics.json'),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','LGBM_REG_R','LGBM_RANK_R']);a=p.parse_args()
    prepare() if a.action=='prepare' else fit(a.action)
