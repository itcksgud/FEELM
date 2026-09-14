"""One GBT fit on existing R features; no target evaluation labels mounted."""
import argparse
import subprocess
import time
import numpy as np
import pandas as pd
from research343_common import *
from combination340_models import Trees

def prepare():
    reviewed('gbt'); OUT.mkdir(exist_ok=True,parents=True)
    require(not (OUT/'input-lock.json').exists(),'preserve prepared inputs')
    for n,sha in ANCHORS.items(): require(pin(BASE/n)['sha256']==sha,'approved parent anchor '+n)
    pins={}
    for folder,seal_name,names in [
        (OLD,'prepared-seal.json',['ratings.parquet','episodes.parquet','contexts.json','catalog.parquet','texts.parquet']),
        (FOUND,'prepared-seal.json',['R/train.parquet','R/score.parquet','R/feature-info.json']),
        (FOUND,'fit-seal.json',['predictions.npz']),
        (COMBO,'fit-seal.json',['predictions.npz'])]:
        record=read(folder/seal_name); pins[(folder/seal_name).relative_to(ROOT).as_posix()]=pin(folder/seal_name)
        for n in names:
            require(pin(folder/n)==record['files'][n],'sealed source '+n)
            pins[(folder/n).relative_to(ROOT).as_posix()]=pin(folder/n)
    for p in [COMBO/'GBT/partition-identity.json',ROOT/'docs/recommendation/experiments/combination340/config.json',
              ROOT/'scripts/combination340_models.py',BASE/'rec-ev-045/metadata.parquet']:
        pins[p.relative_to(ROOT).as_posix()]=pin(p)
    require(pin(COMBO/'GBT/partition-identity.json')==read(COMBO/'fit-seal.json')['files']['GBT/partition-identity.json'],'original GBT partition pin')
    oldcfg=read(ROOT/'docs/recommendation/experiments/combination340/config.json');cfg=read(DOC/'config.json')
    require(oldcfg['models']['GBT']==cfg['models']['GBT'] and oldcfg['seed']==cfg['seed'] and oldcfg['image_id']==cfg['image_id'],'same old GBT recipe')
    contexts=read(OLD/'contexts.json'); c10=[c for c in contexts if c['cap']==10]
    users=[]
    for positive in [False,True]:
        group=sorted([c for c in c10 if (c['h']>0)==positive],key=lambda c:hashlib.sha256(f"research343-role:{c['uid']}".encode()).digest())
        assert len(group) in [84,186]
        for i,c in enumerate(group): users.append({'uid':c['uid'],'h10':c['h'],'role':'calibration' if i<len(group)//3 else 'comparison'})
    roles=pd.DataFrame(users).sort_values('uid'); roles.to_csv(OUT/'roles.csv',index=False)
    trainuids=pd.read_parquet(OLD/'ratings.parquet',columns=['uid']).uid.unique()
    require(len(trainuids)==39859 and not set(trainuids)&set(roles.uid),'disjoint training and development')
    require(roles.groupby('role').size().to_dict()=={'calibration':90,'comparison':180},'fixed roles')
    pins[(OUT/'roles.csv').relative_to(ROOT).as_posix()]=pin(OUT/'roles.csv')
    write_json(OUT/'input-lock.json',{'files':pins,'training_users':39859,'development_users':270,'evaluation_labels_read':False})
    print('PREPARED',flush=True)

def fit():
    reviewed('gbt'); lock(); cfg=read(DOC/'config.json')
    started_fingerprint=fingerprint('gbt');started_lock=pin(OUT/'input-lock.json')
    require(subprocess.check_output(['docker','image','inspect',cfg['docker_image'],'--format','{{.Id}}'],text=True).strip()==cfg['image_id'],'base image')
    root=OUT/'GBT_R'; root.mkdir(exist_ok=False); name='research343-gbt-r'
    cmd=['docker','run','--rm','--name',name,'--network','none','--hostname','research343','--add-host','research343:127.0.0.1',
         '-e','SPARK_LOCAL_IP=127.0.0.1','--cpus','4','--memory','12g','--memory-swap','12g',
         '--mount',f'type=bind,source={ROOT/"scripts"},target=/scripts,readonly',
         '--mount',f'type=bind,source={FOUND/"R"},target=/base,readonly',
         '--mount',f'type=bind,source={root},target=/data',
         '--mount',f'type=bind,source={DOC},target=/config,readonly',cfg['docker_image'],
         '/opt/spark/bin/spark-submit','--master','local[4]','--driver-memory','8g',
         '--conf','spark.sql.shuffle.partitions=8','--conf','spark.sql.adaptive.enabled=false',
         '--conf','spark.ui.enabled=false','/scripts/combination340_worker.py','GBT']
    write_json(root/'command.json',{'command':cmd,'fingerprint':fingerprint('gbt')})
    t=time.monotonic()
    with (root/'run.log').open('x',encoding='utf-8') as log:
        p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
        try: require(p.wait(timeout=cfg['fit_timeout_seconds'])==0,'GBT_R fit failed; partial preserved')
        except BaseException:
            subprocess.run(['docker','stop','--time','2',name],capture_output=True,timeout=20)
            if p.poll() is None:p.kill()
            raise
    require(read(root/'GBT/partition-identity.json')==read(COMBO/'GBT/partition-identity.json'),'identical B/R partition row order')
    frame=pd.read_parquet(root/'GBT/predictions').sort_values('row_id')
    require(np.array_equal(frame.row_id,np.arange(93230)) and np.isfinite(frame.prediction).all(),'93230 finite predictions')
    x=pd.read_parquet(FOUND/'R/score.parquet',columns=[f'x{i:03d}' for i in range(230)]).to_numpy(np.float32)
    trees=Trees(root/'GBT/native',range(230)); error=float(abs(trees.predict(x)-frame.prediction.to_numpy()).max())
    fixture=np.load(root/'GBT/threshold-fixtures.npz'); boundary=float(abs(trees.predict(fixture['features'])-fixture['predictions']).max())
    require(max(error,boundary)<=1e-8,'native/portable parity')
    np.save(root/'predictions.npy',frame.prediction.to_numpy())
    write_json(root/'checks.json',{'observed_max_error':error,'boundary_max_error':boundary,'wrapper_seconds':time.monotonic()-t})
    reviewed('gbt');lock();require(started_fingerprint==fingerprint('gbt') and started_lock==pin(OUT/'input-lock.json'),'no execution drift')
    seal('gbt-fit-seal.json',[p.relative_to(OUT).as_posix() for p in root.rglob('*') if p.is_file()],execution=started_fingerprint)
    print('GBT_R_COMPLETE',read(root/'GBT/metrics.json'),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['prepare','fit']); a=p.parse_args()
    prepare() if a.action=='prepare' else fit()
