"""Full-catalog scores retained for the subsequent policy comparison."""
import os
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']: os.environ[key]='4'
import json
import time
import numpy as np
import pandas as pd
from combination340_common import *
from combination340_models import Trees,load_factors,direct_als
from foundation340_features import Features
from text339_run import predict

def catalog_fingerprint(): return {'combination340_catalog.py':pin(__file__)}

def verify_catalog():
    record=verify('catalog-seal.json')
    require(record['catalog_code']==catalog_fingerprint(),'catalog code identity')
    require(record['fit_seal']==pin(OUT/'fit-seal.json'),'catalog fit identity'); verify('fit-seal.json')
    return record

def catalog():
    reviewed(); verify('fit-seal.json'); fixed=lock(); selected=fixed['selected']
    audit=json.loads((DOC/'catalog-code-review.json').read_text())
    require(audit['status']=='PASS' and audit['fingerprint']==catalog_fingerprint(),'independent catalog code review')
    require(not (OUT/'catalog-cache').exists() and not (OUT/'catalog-top10.parquet').exists(),'preserve full catalog predictions')
    (OUT/'catalog-cache').mkdir()
    cats=pd.read_parquet(OLD/'catalog.parquet'); ids=cats.movie_id.to_numpy()
    meta=pd.read_parquet(ROOT/foundation.config()['metadata']); require(np.array_equal(meta.movie_id,ids),'metadata movie axis')
    texts=pd.read_parquet(OLD/'texts.parquet',columns=['release_date'])
    dates=pd.to_datetime(texts.release_date,format='%Y-%m-%d',errors='coerce',utc=True)
    allowed_base=dates.notna().to_numpy() & (dates.astype('int64').to_numpy()//10**9<=foundation.config()['catalog_snapshot_timestamp'])
    features=Features(meta,cats)
    b=dict(np.load(foundation.OUT/'B/model/portable.npz')); s=dict(np.load(foundation.OUT/selected/'model/portable.npz'))
    no_response=dict(np.load(OUT/'NO_RESPONSE/portable.npz')); trees=Trees(OUT/'GBT/native',range(230))
    actual=load_factors(OUT/'ALS/item-factors',ids,cats.train_count.to_numpy()>0)
    reference=load_factors(REFERENCE/'item-factors',ids,cats.reference_count.to_numpy()>0)
    contexts=json.loads((OLD/'contexts.json').read_text()); rows=[]; costs=[]; start=time.monotonic()
    for number,c in enumerate([c for c in contexts if c['cap']==10]):
        t=time.monotonic(); allowed=allowed_base.copy(); allowed[np.asarray(c['viewed'],int)]=False; ei=np.flatnonzero(allowed)
        vals={name:np.empty(len(ei)) for name in ['B','S','NO_RESPONSE','GBT']}
        for pos in range(0,len(ei),4096):
            chunk=ei[pos:pos+4096]; sl=slice(pos,pos+len(chunk))
            raw=features.reference(c['oi'],c['stars'],chunk)
            x=features.reference(c['oi'],c['stars'],chunk,True) if selected in ['R','RH'] else raw
            vals['B'][sl]=predict(raw,b)
            vals['S'][sl]=vals['B'][sl] if selected=='B' else predict(x,s)
            vals['NO_RESPONSE'][sl]=predict(x,no_response); vals['GBT'][sl]=trees.predict(x)
        vals['ACTUAL_ALS'],direct,n=direct_als(actual,c['oi'],c['stars'],ei)
        vals['REFERENCE_ALS'],ref_direct,rn=direct_als(reference,c['oi'],c['stars'],ei)
        for name in ['B','S','NO_RESPONSE','GBT']: require(np.isfinite(vals[name]).all(),'full finite content scores')
        for w in config()['blend_weights']:
            blend=vals['S'].copy(); blend[direct]=(1-w)*blend[direct]+w*vals['ACTUAL_ALS'][direct]
            require(np.array_equal(blend[~direct],vals['S'][~direct]),'full catalog fallback identity')
            vals['BLEND_'+str(w)]=blend
        np.savez_compressed(OUT/'catalog-cache'/f"{c['uid']}.npz",ei=ei.astype(np.int32),actual_direct=direct,reference_direct=ref_direct,**vals)
        for name,vector in vals.items():
            valid=np.isfinite(vector); positions=np.flatnonzero(valid)
            order=positions[np.lexsort((ids[ei[positions]],-vector[positions]))[:10]]
            for rank,which in enumerate(order,1):
                i=int(ei[which]); rows.append({'uid':c['uid'],'h':c['h'],'variant':name,'rank':rank,'movie_id':int(ids[i]),
                                             'prediction':float(vector[which]),'train_count':int(cats.train_count.iloc[i]),'blocked':bool(cats.blocked.iloc[i])})
        costs.append({'uid':c['uid'],'h':c['h'],'candidates':len(ei),'actual_als_inputs':n,'reference_als_inputs':rn,
                      'actual_als_candidates':int(direct.sum()),'seconds':time.monotonic()-t})
        if number%10==0:
            print('CATALOG',number,round(time.monotonic()-start,1),flush=True)
            write_json(OUT/'run-progress.json',{'stage':'CATALOG','users_done':number+1,'users_total':270,'seconds':time.monotonic()-start})
    pd.DataFrame(rows).to_parquet(OUT/'catalog-top10.parquet',index=False); pd.DataFrame(costs).to_csv(OUT/'catalog-timing.csv',index=False)
    files=['catalog-top10.parquet','catalog-timing.csv']+[p.relative_to(OUT).as_posix() for p in sorted((OUT/'catalog-cache').glob('*.npz'))]
    seal('catalog-seal.json',files,catalog_code=catalog_fingerprint(),fit_seal=pin(OUT/'fit-seal.json'),target_labels_read=0)
    write_json(OUT/'run-progress.json',{'stage':'CATALOG_COMPLETE','users':270,'seconds':time.monotonic()-start})

if __name__=='__main__':catalog()
