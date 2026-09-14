"""Common users/movies, capped history and legal temporal training episodes."""
from __future__ import annotations
import argparse
import gc
import json
import zipfile
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from text339_common import *
from text339_features import Features,fit_projection
from rec047_common import role,window_start

def population():
    cfg=config();meta=pd.read_parquet(ROOT/cfg['metadata']);ids=meta.movie_id.to_numpy(np.int64)
    text=pd.read_parquet(OUT/'texts.parquet');part=pd.read_parquet(ROOT/'outputs/recommendation-evidence/cold-item-content/movie-partition.parquet')
    require(np.array_equal(ids,text.movie_id) and np.array_equal(ids,part.movie_id),'same catalog ID order')
    blocked=part.partition.ne('W').to_numpy();origin=cfg['origin']
    dates=pd.to_datetime(text.release_date,format='%Y-%m-%d',errors='coerce',utc=True)
    released=dates.notna().to_numpy()&(dates.astype('int64').to_numpy()//10**9<=origin)
    users=pd.read_parquet(READY/'user-origin-counts.parquet')
    users=users[(users.origin==origin)&(users.role=='evaluation')&(users.targets>0)]
    require(len(users)==271,'predeclared role candidates')
    obs=pd.read_parquet(READY/'development-observations.parquet')
    obs=obs[obs.uid.isin(users.uid)].copy();obs['index']=np.searchsorted(ids,obs.movie_id)
    require(np.array_equal(ids[obs['index']],obs.movie_id),'observed canonical axis')
    before_all=obs[obs.timestamp<origin].sort_values(['uid','timestamp','movie_id'],ascending=[True,False,True])
    before=before_all.groupby('uid',sort=False).head(30)
    future=obs[(obs.timestamp>=origin)&(obs.timestamp<origin+HORIZON)]
    target=future[released[future['index']]].sort_values(['uid','timestamp','movie_id'])
    return meta,ids,blocked,released,users,before_all,before,future,target,part

def preflight():
    require(not (OUT/'preflight.json').exists(),'preserve population preflight')
    for name,expected in config()['sources'].items():require(pin(ROOT/name)==expected,'fixed input '+name)
    source_report=json.loads((OUT/'text-source-report.json').read_text())
    require(pin(OUT/'texts.parquet')==source_report['files']['texts.parquet'],'materialized texts pin')
    meta,ids,blocked,released,users,before_all,before,future,target,part=population()
    sizes=target.groupby('uid').size();eligible=set(sizes.index)
    train_ids=pd.read_parquet(OLD/'ratings.parquet',columns=['uid','movie_id','timestamp'])
    require((train_ids.timestamp<config()['origin']).all() and all(role(int(u))=='train' for u in train_ids.uid.unique()),'legal training role/time')
    ix=np.searchsorted(ids,train_ids.movie_id)
    require((ix<len(ids)).all() and np.array_equal(ids[ix],train_ids.movie_id),'exact train movie identity')
    train_ids=train_ids[~blocked[ix]]
    require(not set(train_ids.uid)&eligible,'disjoint current role users')
    counts=np.bincount(np.searchsorted(ids,train_ids.movie_id),minlength=len(ids))
    catalog=pd.DataFrame({'movie_id':ids,'blocked':blocked,'released_at_origin':released,'train_count':counts,'reference_count':part.reference_t_count,'support':support(counts)})
    catalog.to_parquet(OUT/'catalog.parquet',index=False)
    rows=[]
    for n in [1,2,4,6,10]:
        selected=users[users.uid.isin(sizes[sizes>=n].index)]
        rows.append({'n':n,'users':len(selected),'observations':int(sizes[sizes>=n].sum()),
                     'activity':selected.pre_all.map(activity).value_counts().to_dict()})
    texts=pd.read_parquet(OUT/'texts.parquet',columns=['release_date','T2','T3','restored_body','changed_nonempty'])
    dates=pd.to_datetime(texts.release_date,format='%Y-%m-%d',errors='coerce',utc=True)
    missing=dates.isna().to_numpy();too_new=dates.notna().to_numpy()&~released
    exclusions={}
    for name,mask in [('unknown_release',missing),('not_yet_released',too_new)]:
        a=future[mask[future['index']]]
        exclusions[name]={'rows':len(a),'users':int(a.uid.nunique()),'movies':int(a.movie_id.nunique())}
    histories=before_all.groupby('uid').size().reindex(sorted(eligible),fill_value=0)
    text_coverage={}
    for name,mask in [('T2_nonempty',texts.T2.ne('').to_numpy()),('T3_nonempty',texts.T3.ne('').to_numpy()),
                      ('T3_restored',texts.restored_body.to_numpy()),('T3_changed_nonempty',texts.changed_nonempty.to_numpy())]:
        selected_target=target[mask[target['index']]]
        selected_input=before[before.uid.isin(eligible)&mask[before['index']]]
        text_coverage[name]={'catalog_movies':int(mask.sum()),'target_rows':len(selected_target),'target_movies':int(selected_target.movie_id.nunique()),
                             'target_users':int(selected_target.uid.nunique()),'input_movies_H30':int(selected_input.movie_id.nunique()),
                             'input_users_H30':int(selected_input.uid.nunique())}
    report={'candidate_users':len(users),'eligible_users':len(eligible),'train_users':int(train_ids.uid.nunique()),'train_rows':len(train_ids),
            'blocked_movies':int(blocked.sum()),'released_catalog':int(released.sum()),'future_rows_before_release_filter':len(future),
            'future_rows_after_release_filter':len(target),'future_movies_after':int(target.movie_id.nunique()),
            'release_exclusions':exclusions,'actual_h_distribution':{str(cap):np.minimum(histories,cap).value_counts().sort_index().to_dict() for cap in CAPS},
            'by_n':rows,'target_partition':pd.Series(np.where(blocked[target['index']],'C',np.where(counts[target['index']]==0,'NATURAL_ZERO','W'))).value_counts().to_dict(),
            'target_stars_decoded':0,'input_stars_decoded':0,'training_stars_decoded':0,
            'code':pin(__file__),'texts':pin(OUT/'texts.parquet'),'catalog':pin(OUT/'catalog.parquet')}
    report['text_coverage_without_rating_values']=text_coverage
    write_json(OUT/'preflight.json',report);print(json.dumps(report),flush=True)

class Writer:
    def __init__(self,path):
        self.schema=pa.schema([('row_id',pa.int64()),('uid',pa.int32()),('label',pa.float64())]+[(f'x{i:03d}',pa.float32()) for i in range(554)])
        self.writer=pq.ParquetWriter(path,self.schema,compression='zstd');self.pending=[];self.size=self.offset=0
    def append(self,x,y,uid):
        self.pending.append((x,np.asarray(y),uid));self.size+=len(y)
        if self.size>=8192:self.flush()
    def flush(self):
        if not self.size:return
        x=np.vstack([a[0] for a in self.pending]);y=np.concatenate([a[1] for a in self.pending])
        data={'row_id':np.arange(self.offset,self.offset+len(y)),'uid':np.concatenate([np.full(len(a[1]),a[2]) for a in self.pending]),'label':y}
        data.update({f'x{i:03d}':x[:,i] for i in range(554)})
        self.writer.write_table(pa.Table.from_pydict(data,schema=self.schema));self.offset+=len(y);self.pending=[];self.size=0
    def close(self):self.flush();self.writer.close()

def load_embeddings():
    from text339_encode import encoding_fingerprint
    record=json.loads((OUT/'embedding-seal.json').read_text())
    require(record['fingerprint']==encoding_fingerprint(),'encoder version identity')
    require(record['text_source']==pin(OUT/'texts.parquet'),'embedding source parent')
    for n,p in record['files'].items():require(pin(OUT/n)==p,'embedding artifact')
    return {n:np.load(OUT/(n+'-embeddings.npy'),mmap_mode='r') for n in ['overview','T2','T3']}

def prepare():
    reviewed();require(not (OUT/'train.parquet').exists(),'preserve prepared rows')
    meta,ids,blocked,released,users,before_all,before,future,target,part=population()
    origin=config()['origin'];catalog=pd.read_parquet(OUT/'catalog.parquet')
    train=pd.read_parquet(OLD/'ratings.parquet')
    require((train.timestamp<origin).all() and all(role(int(u))=='train' for u in train.uid.unique()),'legal training')
    require(np.isin(train.rating,np.arange(1,11)/2).all(),'training real half stars')
    ix=np.searchsorted(ids,train.movie_id)
    require((ix<len(ids)).all() and np.array_equal(ids[ix],train.movie_id),'exact train IDs')
    train=train[~blocked[np.searchsorted(ids,train.movie_id)]].copy();train['index']=np.searchsorted(ids,train.movie_id)
    train=train.sort_values(['uid','timestamp','movie_id']);train['window']=window_start(train.timestamp.to_numpy(),origin)
    require(not train.duplicated(['uid','movie_id']).any(),'unique train ratings')
    counts=np.bincount(train['index'],minlength=len(ids))
    require(np.array_equal(counts,catalog.train_count),'preflight training counts')
    train[['uid','movie_id','rating','timestamp']].to_parquet(OUT/'ratings.parquet',index=False)
    embeddings=load_embeddings();present={k:np.linalg.norm(v,axis=1)>0 for k,v in embeddings.items()}
    projections={'overview':fit_projection(embeddings['overview'],(counts>0)&present['overview']),
                 'wiki':fit_projection(np.concatenate([embeddings['T2'],embeddings['T3']]),np.r_[(counts>0)&present['T2'],(counts>0)&present['T3']])}
    np.savez_compressed(OUT/'projection.npz',**{f'{k}_{field}':v for k,p in projections.items() for field,v in p.items()})
    features=Features(meta,embeddings,projections);writer=Writer(OUT/'train.parquet');episodes=[]
    for number,(uid,a) in enumerate(train.groupby('uid',sort=False)):
        ix,ts,r,wins=a['index'].to_numpy(),a.timestamp.to_numpy(),a.rating.to_numpy(),a.window.to_numpy()
        starts=np.r_[0,np.flatnonzero(np.diff(wins))+1];ends=np.r_[starts[1:],len(a)]
        for lo,hi in zip(starts,ends):
            w=int(wins[lo]);cap=allocated_cap(int(uid),w);h=min(cap,int(lo))
            previous=np.lexsort((ids[ix[:lo]],-ts[:lo]))[:h]
            require((ts[previous]<w).all() and not blocked[ix[previous]].any() and not blocked[ix[lo:hi]].any(),'legal episode')
            writer.append(features.transform(ix[previous],r[previous],ix[lo:hi]),r[lo:hi],int(uid))
            episodes.append((int(uid),w,cap,h,int(lo),int(hi-lo)))
        if number%1000==0:print('TRAIN_FEATURES',number,flush=True)
    writer.close();require(writer.offset==len(train),'one target per legal training rating')
    train_rows,train_users=len(train),int(train.uid.nunique());del train;gc.collect()
    pd.DataFrame(episodes,columns=['uid','origin','cap','h','pre_count','targets']).to_parquet(OUT/'episodes.parquet',index=False)
    eligible=set(target.uid);before=before[before.uid.isin(eligible)]
    inputs={(int(u),int(m)) for u,m in before[['uid','movie_id']].itertuples(index=False,name=None)};stars={}
    with zipfile.ZipFile(config()['archive']) as z,z.open('ml-32m/ratings.csv') as f:
        require(f.readline().strip()==b'userId,movieId,rating,timestamp','archive header')
        for row in f:
            ub,rest=row.split(b',',1);u=int(ub)
            if u not in eligible:continue
            mb,rb,tb=rest.strip().split(b',');m,t=int(mb),int(tb)
            if t<origin and (u,m) in inputs:
                value=float(rb);require(value*2 in range(1,11),'real half-star input');stars[(u,m)]=value
    require(len(stars)==len(inputs),'all input values')
    writer=Writer(OUT/'score.parquet');contexts=[]
    groups={int(u):a for u,a in before.groupby('uid')};info=users.set_index('uid').to_dict('index')
    viewed={int(u):a['index'].tolist() for u,a in before_all.groupby('uid')}
    for uid,a in target.groupby('uid',sort=True):
        uid=int(uid);o=groups.get(uid,before.iloc[:0]);ei=a['index'].to_numpy()
        for cap in CAPS:
            use=o.head(cap);oi=use['index'].to_numpy();ov=[stars[(uid,int(m))] for m in use.movie_id]
            start=writer.offset+writer.size
            require(not np.intersect1d(oi,ei).size,'input/target separation')
            writer.append(features.transform(oi,ov,ei),np.zeros(len(ei)),uid)
            contexts.append({'uid':uid,'cap':cap,'h':len(oi),'start':start,'stop':start+len(ei),'oi':oi.tolist(),'stars':ov,'ei':ei.tolist(),
                             'input_timestamps':use.timestamp.tolist(),'target_timestamps':a.timestamp.tolist(),
                             'pre_all':int(info[uid]['pre_all']),'activity':activity(info[uid]['pre_all']),
                             'als_supported_inputs':int((counts[oi]>0).sum()),'viewed':viewed.get(uid,[])})
    writer.close();write_json(OUT/'contexts.json',contexts)
    require(check_contexts(contexts,catalog)==writer.offset,'validated score contexts')
    write_json(OUT/'feature-info.json',{'names':features.names,'indices':features.indices,'train_rows':train_rows,'train_users':train_users,
                                    'score_rows':writer.offset,'target_stars_decoded':0,'input_stars_decoded':len(stars)})
    seal('prepared-seal.json',['train.parquet','ratings.parquet','episodes.parquet','score.parquet','contexts.json','projection.npz','feature-info.json','catalog.parquet','texts.parquet','preflight.json','text-source-report.json'],embedding_seal=pin(OUT/'embedding-seal.json'),target_stars_decoded=0)
    print('PREPARED',train_rows,len(contexts),writer.offset,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['preflight','prepare']);a=p.parse_args()
    preflight() if a.action=='preflight' else prepare()
