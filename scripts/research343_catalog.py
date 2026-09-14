"""Same catalog and 180 comparison users; raw-score ranking, UNKNOWN preserved."""
import os
for k in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:os.environ[k]='4'
import time
import numpy as np
import pandas as pd
from research343_common import *
from research343_evaluate import gate,MODELS,label_guard
from research343_lgb_models import Booster
from combination340_models import Trees
from foundation340_features import Features

def catalog():
    started=gate();require(not (OUT/'catalog-cache').exists(),'preserve full predictions');(OUT/'catalog-cache').mkdir()
    parents={}
    for folder,sha in [(FOUND,'29ef52e6dddb00d4c5a361b614e57a9ee8d6fbf37548ad6a5cc122e2e8a6b287'),(COMBO,'d537e976c635407027ed3f0b03472e2e7ad2a9452ee33596944dce9383ec8006')]:
        require(pin(folder/'catalog-seal.json')['sha256']==sha,'original catalog seal')
        record=read(folder/'catalog-seal.json');require(pin(folder/'catalog-top10.parquet')==record['files']['catalog-top10.parquet'],'original top10')
        parents[(folder/'catalog-seal.json').relative_to(ROOT).as_posix()]=pin(folder/'catalog-seal.json')
    cats=pd.read_parquet(OLD/'catalog.parquet');ids=cats.movie_id.to_numpy();meta=pd.read_parquet(BASE/'rec-ev-045/metadata.parquet')
    require(np.array_equal(meta.movie_id,ids),'movie metadata axis')
    dates=pd.to_datetime(pd.read_parquet(OLD/'texts.parquet',columns=['release_date']).release_date,format='%Y-%m-%d',errors='coerce',utc=True)
    allowed_base=dates.notna().to_numpy()&(dates.astype('int64').to_numpy()//10**9<=1788998400)
    roles=pd.read_csv(OUT/'roles.csv');uids=set(roles.loc[roles.role.eq('comparison'),'uid'])
    contexts=[c for c in read(OLD/'contexts.json') if c['cap']==10 and c['uid'] in uids]
    features=Features(meta,cats);models={'GBT_R':Trees(OUT/'GBT_R/GBT/native',range(230)),
        'LGBM_REG_R':Booster(OUT/'LGBM_REG_R/model.txt'),'LGBM_RANK_R':Booster(OUT/'LGBM_RANK_R/model.txt')}
    rows=[];costs=[];supply=[];t=time.monotonic()
    for folder,mapping in [(FOUND,{'B':'FM_B','R':'FM_R','RH':'FM_RH'}),(COMBO,{'GBT':'GBT_B','ACTUAL_ALS':'ALS','BLEND_0.25':'FM_ALS25'})]:
        old=pd.read_parquet(folder/'catalog-top10.parquet');old=old[old.uid.isin(uids)&old.variant.isin(mapping)].copy();old['model']=old.variant.map(mapping)
        rows.extend(old[['uid','h','model','rank','movie_id','prediction']].to_dict('records'))
    oldrecord=read(COMBO/'catalog-seal.json')
    for number,c in enumerate(contexts):
        allowed=allowed_base.copy();allowed[np.asarray(c['viewed'],int)]=False;ei=np.flatnonzero(allowed)
        cached=COMBO/'catalog-cache'/f"{c['uid']}.npz";require(pin(cached)==oldrecord['files'][f"catalog-cache/{c['uid']}.npz"],'cached candidate pin')
        old=np.load(cached);require(np.array_equal(ei,old['ei']),'exact old candidate set')
        alscount=int(old['actual_direct'].sum()); vals={n:np.empty(len(ei)) for n in models};feature_seconds=0;seconds={n:0.0 for n in models}
        for pos in range(0,len(ei),8192):
            chunk=ei[pos:pos+8192];sl=slice(pos,pos+len(chunk));start=time.perf_counter();x=features.reference(c['oi'],c['stars'],chunk,True);feature_seconds+=time.perf_counter()-start
            for name,model in models.items():
                start=time.perf_counter();vals[name][sl]=model.predict(x);seconds[name]+=time.perf_counter()-start
        np.savez_compressed(OUT/'catalog-cache'/f"{c['uid']}.npz",ei=ei.astype(np.int32),**vals)
        for name,v in vals.items():
            require(np.isfinite(v).all(),'full finite new model');start=time.perf_counter();order=np.lexsort((ids[ei],-v))[:10];sortseconds=time.perf_counter()-start
            for rank,j in enumerate(order,1):rows.append({'uid':c['uid'],'h':c['h'],'model':name,'rank':rank,'movie_id':int(ids[ei[j]]),'prediction':float(v[j])})
            costs.append({'uid':c['uid'],'h':c['h'],'model':name,'candidates':len(ei),'feature_seconds_shared':feature_seconds,'prediction_seconds':seconds[name],
                          'top10_seconds':sortseconds,'standalone_total_seconds':feature_seconds+seconds[name]+sortseconds})
        for name in MODELS:supply.append({'uid':c['uid'],'h':c['h'],'model':name,'candidates':len(ei),'finite_scores':alscount if name=='ALS' else len(ei)})
        if number%10==0:print('CATALOG',number+1,'/180',round(time.monotonic()-t,1),flush=True)
    top=pd.DataFrame(rows);require(not top.duplicated(['uid','model','movie_id']).any(),'no duplicate recommendations')
    ix=np.searchsorted(ids,top.movie_id);require(np.array_equal(ids[ix],top.movie_id),'top movie axis')
    label_guard();labels=pd.read_parquet(OLD/'labels.parquet'); known=pd.MultiIndex.from_frame(labels[['uid','movie_id']]);top['unknown']=~pd.MultiIndex.from_frame(top[['uid','movie_id']]).isin(known)
    top['one_vote_ten']=(meta.tmdb_vote_count.to_numpy()[ix]==1)&(meta.tmdb_vote_average.to_numpy()[ix]==10)
    top['support']=cats.train_count.to_numpy()[ix];top['blocked']=cats.blocked.to_numpy()[ix];top['release_year']=dates.dt.year.to_numpy()[ix]
    top['tmdb_vote_count']=meta.tmdb_vote_count.to_numpy()[ix]
    for f in ['genre_ids','keyword_ids','director_ids','top5_cast_ids']:top[f+'_missing']=meta[f].map(len).eq(0).to_numpy()[ix]
    top.to_parquet(OUT/'catalog-top10.parquet',index=False);pd.DataFrame(costs).to_csv(OUT/'catalog-timing.csv',index=False);pd.DataFrame(supply).to_csv(OUT/'catalog-supply.csv',index=False)
    summary=[]
    for hgroup in ['ALL','H_POSITIVE','H_ZERO']:
        eligible=contexts if hgroup=='ALL' else [c for c in contexts if (c['h']>0)==(hgroup=='H_POSITIVE')]
        users={c['uid'] for c in eligible};sub=top[top.uid.isin(users)]
        for model in MODELS:
            for end in [2,4,6]:
                a=sub[sub.model.eq(model)&sub['rank'].between(end-1,end)];counts=a.movie_id.value_counts();returned=len(a);fractions=counts/returned
                summary.append({'h_group':hgroup,'model':model,'end':end,'users':len(users),'slots':len(users)*2,'returned':returned,'missing':len(users)*2-returned,
                                'unknown':int(a.unknown.sum()),'one_vote_ten':int(a.one_vote_ten.sum()),'unique_movies':len(counts),
                                'hhi':float((fractions**2).sum()) if returned else np.nan,'max_share':float(fractions.max()) if returned else np.nan,
                                'support0':int(a.support.eq(0).sum()),'support1_9':int(a.support.between(1,9).sum()),'support10_49':int(a.support.between(10,49).sum()),'support50plus':int(a.support.ge(50).sum()),
                                'blocked':int(a.blocked.sum()),'release2024plus':int(a.release_year.ge(2024).sum()),
                                **{f+'_missing':int(a[f+'_missing'].sum()) for f in ['genre_ids','keyword_ids','director_ids','top5_cast_ids']}})
    pd.DataFrame(summary).to_csv(OUT/'catalog-summary.csv',index=False)
    require(started==gate(),'catalog drift')
    for n,p in parents.items():require(pin(ROOT/n)==p,'unchanged catalog parents')
    files=['catalog-top10.parquet','catalog-timing.csv','catalog-supply.csv','catalog-summary.csv']+[p.relative_to(OUT).as_posix() for p in sorted((OUT/'catalog-cache').glob('*.npz'))]
    seal('catalog-seal.json',files,execution=started,parents=parents,seconds=time.monotonic()-t)
    print('CATALOG_COMPLETE',round(time.monotonic()-t,1),flush=True)

if __name__=='__main__':catalog()
