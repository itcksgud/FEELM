"""Recover completed B without refitting; run only R/H/RH with cgroup-compatible metrics."""
import json
import shutil
import subprocess
import time
import numpy as np
import pandas as pd
from foundation340_common import *

RUNTIME_FILES=[ROOT/'scripts/foundation340_worker.py',ROOT/'scripts/foundation340_worker_v2.py',
               ROOT/'scripts/foundation340_resume.py',DOC/'RUNTIME-RECOVERY.md']

def runtime_fingerprint(): return {p.relative_to(ROOT).as_posix():pin(p) for p in RUNTIME_FILES}

def check_runtime():
    r=json.loads((DOC/'runtime-review.json').read_text())
    require(r['status']=='PASS' and r['fingerprint']==runtime_fingerprint(),'independent exact runtime recovery review')
    return r

def model_seal(variant,actual_worker):
    check_runtime()
    folder=OUT/variant/'model'
    source=folder/'runtime-sources'; source.mkdir(exist_ok=False)
    paths=RUNTIME_FILES+[DOC/'runtime-review.json']
    if variant=='B': paths.append(DOC/'B-recovery-review.json')
    for path in paths: shutil.copyfile(path,source/path.name)
    write_json(folder/'runtime-manifest.json',{'actual_worker':actual_worker,'worker':pin(ROOT/'scripts'/actual_worker),
                                             'runtime_fingerprint':runtime_fingerprint(),'prepared_seal':pin(OUT/'prepared-seal.json'),
                                             'B_post_fit_metrics_error':variant=='B','B_refit_performed':False})
    frame=pd.read_parquet(folder/'predictions').sort_values('row_id')
    require(np.array_equal(frame.row_id,np.arange(93230)) and np.isfinite(frame.prediction).all(),'complete recovered native predictions')
    np.savez_compressed(folder/'aligned.npz',prediction=frame.prediction.to_numpy())
    write_json(folder/'model-seal.json',{'fingerprint':fingerprint(),'prepared_seal':pin(OUT/'prepared-seal.json'),
                                       'runtime_manifest':pin(folder/'runtime-manifest.json'),
                                       'files':{p.relative_to(folder).as_posix():pin(p) for p in folder.rglob('*') if p.is_file()}})

def run():
    reviewed(); verify('prepared-seal.json'); check_runtime()
    prepared_review=json.loads((DOC/'prepared-review.json').read_text())
    require(prepared_review['status']=='PASS' and prepared_review['prepared_seal']==pin(OUT/'prepared-seal.json'),'independent prepared output review')
    if (OUT/'fit-seal.json').exists(): verify('fit-seal.json'); print('EXISTING_FIT_VERIFIED'); return
    require(not (OUT/'predictions.npz').exists(),'preserve partial combined predictions')
    cfg=config(); folder=OUT/'B/model'
    if not (folder/'model-seal.json').exists():
        audit=json.loads((DOC/'B-recovery-review.json').read_text())
        require(audit['status']=='PASS' and audit['prepared_seal']==pin(OUT/'prepared-seal.json'),'independent completed-B audit')
        for name,expected in audit['files'].items(): require(pin(folder/name)==expected,'actual audited B artifact')
        require(not (folder/'metrics.json').exists() and not (folder/'aligned.npz').exists(),'preserve any previous recovery')
        write_json(folder/'metrics.json',{'variant':'B','spark':'4.1.3','dimensions':230,'train_rows':4997069,'seed':339,'params':cfg['models']['FM'],
                                         'fit_seconds':None,'seconds':None,'train_raw_rmse':None,'peak_bytes':None,'strict_memory_pass':None,
                                         'memory_measurement':'UNAVAILABLE','status':'FIT_AND_PREDICTIONS_VERIFIED_POST_FIT_METRICS_FAILED',
                                         'new_fit_performed_by_recovery':False,'failure_log':pin(OUT/'logs/foundation340-b.log')})
        model_seal('B','foundation340_worker.py')
    verify_model('B')
    identity=json.loads((folder/'partition-identity.json').read_text())
    require(subprocess.check_output(['docker','image','inspect',cfg['docker_image'],'--format','{{.Id}}'],text=True).strip()==cfg['image_id'],'same pinned image')
    for variant in ['R','H','RH']:
        folder=OUT/variant/'model'
        if not (folder/'model-seal.json').exists():
            require(not folder.exists(),'preserve incomplete model '+variant); check_runtime()
            name='foundation340-'+variant.lower(); logs=OUT/'logs'; logs.mkdir(exist_ok=True)
            command=['docker','run','--rm','--name',name,'--network','none','--hostname','foundation340','--add-host','foundation340:127.0.0.1',
                     '-e','SPARK_LOCAL_IP=127.0.0.1','--cpus','4','--memory','12g','--memory-swap','12g',
                     '--mount',f'type=bind,source={ROOT/"scripts"},target=/scripts,readonly',
                     '--mount',f'type=bind,source={OUT/variant},target=/data',
                     '--mount',f'type=bind,source={DOC},target=/config,readonly',cfg['docker_image'],
                     '/opt/spark/bin/spark-submit','--master','local[4]','--driver-memory','8g',
                     '--conf','spark.sql.shuffle.partitions=8','--conf','spark.sql.adaptive.enabled=false',
                     '--conf','spark.ui.enabled=false','/scripts/foundation340_worker_v2.py',variant]
            write_json(OUT/'run-progress.json',{'stage':'FITTING','variant':variant,'started_epoch':time.time(),'container':name,'runtime':'v2_memory_compatibility'})
            print('START',variant,flush=True)
            with (logs/(name+'.log')).open('x',encoding='utf-8') as log:
                process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
                try: require(process.wait(timeout=cfg['fit_timeout_seconds'])==0,'fit failed '+variant)
                except BaseException:
                    subprocess.run(['docker','stop','--time','2',name],capture_output=True,timeout=20)
                    if process.poll() is None: process.kill()
                    raise
            model_seal(variant,'foundation340_worker_v2.py')
        verify_model(variant)
        require(json.loads((folder/'partition-identity.json').read_text())==identity,'same actual eight partitions')
        print('FIT_COMPLETE',variant,(folder/'metrics.json').read_text(),flush=True)
    values=[np.load(OUT/v/'model/aligned.npz')['prediction'] for v in VARIANTS]
    np.savez_compressed(OUT/'predictions.npz',names=np.array(VARIANTS),predictions=np.column_stack(values))
    seal('fit-seal.json',['predictions.npz']+[f'{v}/model/model-seal.json' for v in VARIANTS],
         prepared_seal=pin(OUT/'prepared-seal.json'),runtime_review=pin(DOC/'runtime-review.json'))
    write_json(OUT/'run-progress.json',{'stage':'FIT_COMPLETE','variants':VARIANTS,'B_metrics':'UNAVAILABLE'})

if __name__=='__main__':run()
