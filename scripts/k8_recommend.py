"""Actual full-catalog retrieval evaluation with candidate-only reranking."""
from collections import defaultdict
import io
import itertools
import pickle
import time
import zipfile
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
from k8_common import *

class Predictor:
    def __init__(self,x,factors,has_factor,count,total,cfg):
        self.x=x;self.f=factors;self.has=has_factor;self.count=count;self.cfg=cfg
        self.mean=float(total.sum()/count.sum())
        self.bayes=(total+cfg['bayes_mass']*self.mean)/(count+cfg['bayes_mass'])
    def user(self,h,stars):
        h=np.asarray(h,int);stars=np.asarray(stars,float)
        bias=float(np.sum(stars-self.bayes[h])/(len(h)+self.cfg['profile_prior_mass']))
        # Dual ridge avoids a large solve for users with at most30 input films.
        xx=self.x[h];res=stars-self.bayes[h]-bias
        coef=xx.T @ np.linalg.solve(xx@xx.T+self.cfg['ridge_alpha']*np.eye(len(h)),res) if len(h) else np.zeros(self.x.shape[1])
        supported=self.has[h]
        ff=self.f[h[supported]]
        u=np.linalg.solve(ff.T@ff+self.cfg['als_reg']*len(ff)*np.eye(self.f.shape[1]),ff.T@stars[supported]) if len(ff) else None
        return bias,coef,u
    def predict(self,params,ix,kind='shared'):
        ix=np.asarray(ix,int);bias,coef,u=params
        p=self.bayes[ix]+bias+self.x[ix]@coef
        if kind=='bayes':p=self.bayes[ix].copy()
        if kind=='shared' and u is not None:
            use=self.has[ix];p[use]=self.f[ix[use]]@u
        return np.clip(p,.5,5)

def top_unseen(order,seen,limit):
    result=[]
    for i in order:
        if not seen[i]:
            result.append(int(i))
            if len(result)>=limit:break
    return np.asarray(result,int)

def candidates(policy,global_order,lists,reps,profile,under,seen,ids):
    B=policy['budget']
    if policy['kind']=='flat':return top_unseen(global_order,seen,B),{'fill':0,'group_fallback':False}
    legal=np.array([i for i,a in enumerate(lists) if len(a) and under[i]],int)
    if len(legal)==0:
        a=top_unseen(global_order,seen,B)
        return a,{'fill':len(a),'group_fallback':True}
    chosen=legal[rank(reps[legal]@profile,legal)[:policy['probes']]]
    pools=[top_unseen(lists[j],seen,policy['quota']) for j in chosen]
    take=[]
    for q in range(policy['quota']):
        for a in pools:
            if q<len(a):take.append(int(a[q]))
            if len(take)==B:break
        if len(take)==B:break
    before=len(take)
    if len(take)<B:
        selected=set(take)
        for i in global_order:
            if not seen[i] and i not in selected:
                take.append(int(i));selected.add(int(i))
                if len(take)==B:break
    assert len(take)<=B and len(set(take))==len(take)
    return np.asarray(take,int),{'fill':len(take)-before,'group_fallback':False}

def metrics(ranked,retrieved,target_ix,stars,groups,under,genre,history):
    truth=dict(zip(map(int,target_ix),map(float,stars)))
    assert len(truth)==len(target_ix)
    pos={i for i,r in truth.items() if r>=4};valid=bool(pos)
    gains=np.array([max(r-3,0) for r in truth.values()]);ideal=np.sort(gains)[::-1]
    result={'valid_positive':valid,'eligible_targets':len(truth),'positive_targets':len(pos)}
    for k in [2,6,10]:
        gain=np.array([max(truth.get(int(i),0)-3,0) for i in ranked[:k]])
        idcg=float(np.sum(ideal[:k]/np.log2(np.arange(min(k,len(ideal)))+2)))
        dcg=float(np.sum(gain/np.log2(np.arange(len(gain))+2)))
        result['ndcg'+str(k)]=dcg/idcg if valid and idcg else None
    result['recall10']=len(set(map(int,ranked[:10]))&pos)/len(pos) if valid else None
    result['candidate_recall']=len(set(map(int,retrieved))&pos)/len(pos) if valid else None
    known=[truth[int(i)] for i in ranked[:10] if int(i) in truth]
    result['known_stars_mean']=float(np.mean(known)) if known else None
    result['known_slots']=len(known);result['unknown_share']=sum(int(i) not in truth for i in ranked[:10])/max(1,len(ranked[:10]))
    result['underseen_share']=float(np.sum(under[groups[ranked[:10]]])/10)
    seen_genre=(genre[np.asarray(history,int)].sum(axis=0)>0) if len(history) else np.zeros(genre.shape[1],bool)
    nov=[]
    for i in ranked[:10]:
        gg=genre[i]>0
        nov.append(float((gg&~seen_genre).sum()/gg.sum()) if gg.any() else 0.)
    result['genre_novelty']=float(sum(nov)/10)
    result['candidate_count']=len(retrieved);result['returned_count']=len(ranked[:10])
    return result

def aggregate(frame):
    cols=['ndcg2','ndcg6','ndcg10','recall10','candidate_recall','underseen_share','unknown_share','genre_novelty','known_slots','candidate_count','returned_count','latency_ms','fill']
    result={c:float(frame[c].mean()) if frame[c].notna().any() else None for c in cols if c in frame}
    result.update(users=len(frame),positive_users=int(frame.valid_positive.sum()),latency_p50_ms=float(frame.latency_ms.median()),latency_p95_ms=float(frame.latency_ms.quantile(.95)))
    return result

def bootstrap_delta(a,b,cfg):
    d=np.asarray(a,float)-np.asarray(b,float);d=d[np.isfinite(d)]
    if not len(d):return {'n':0,'delta':None,'ci95':[None,None]}
    rng=np.random.default_rng(cfg['seed'])
    values=np.array([d[rng.integers(0,len(d),len(d))].mean() for _ in range(cfg['bootstrap_replicates'])])
    return {'n':len(d),'delta':float(d.mean()),'ci95':np.quantile(values,[.025,.975]).tolist()}

def prepare_interactions(frame,cfg):
    ref=Path(cfg['reference']);dest=OUT/'recommend';dest.mkdir(parents=True,exist_ok=True)
    sources={}
    for name in ['text339/ratings.parquet','text339/contexts.json','text339/catalog.parquet','text339/labels.parquet','text339/prepared-seal.json','combination340/fit-seal.json']:
        sources[name]=pin(ref/name)
    for name,digest in cfg['reference_anchors'].items():assert sources[name]['sha256']==digest,('independent source anchor',name)
    prepared=read(ref/'text339/prepared-seal.json')['files']
    for name in ['ratings.parquet','contexts.json','catalog.parquet']:
        assert sources['text339/'+name]==prepared[name],('source pin',name)
    expected=read(ref/'combination340/fit-seal.json')['files']
    actual_names={p.relative_to(ref/'combination340').as_posix() for p in (ref/'combination340/ALS/item-factors').iterdir() if p.is_file()}
    assert actual_names=={n for n in expected if n.startswith('ALS/item-factors/')},'complete factor inventory'
    for p in (ref/'combination340/ALS/item-factors').iterdir():
        if p.is_file():
            rel=p.relative_to(ref/'combination340').as_posix();sources['combination340/'+rel]=pin(p)
            assert pin(p)==expected[rel]
    factors=pd.read_parquet(ref/'combination340/ALS/item-factors')
    raw=pd.read_parquet(ref/'text339/ratings.parquet');contexts=read(ref/'text339/contexts.json')
    oldaxis=pd.read_parquet(ref/'text339/catalog.parquet').movie_id.to_numpy()
    labels=pd.read_parquet(ref/'text339/labels.parquet')
    assert not labels.duplicated(['uid','movie_id']).any()
    labelmap=labels.set_index(['uid','movie_id']).rating.to_dict()
    users=sorted({c['uid'] for c in contexts})
    assert len(users)==270 and (raw.timestamp<cfg['origin']).all() and not set(raw.uid)&set(users)
    assert not raw.duplicated(['uid','movie_id']).any()
    assert set(factors.id)==set(raw.movie_id)
    matched=frame.mapping_status.eq('MATCHED')
    service_lookup={int(m):i for i,m in enumerate(frame.movielens_movie_id) if matched.iloc[i]}
    old_to_new=np.array([service_lookup.get(int(m),-1) for m in oldaxis])
    count=np.zeros(len(frame));total=np.zeros(len(frame))
    ag=raw.groupby('movie_id').rating.agg(['sum','count'])
    for m,row in ag.iterrows():
        if int(m) in service_lookup:
            i=service_lookup[int(m)];count[i]=row['count'];total[i]=row['sum']
    assert count.sum()>0
    fmat=np.zeros((len(frame),32));has=np.zeros(len(frame),bool)
    for row in factors.itertuples(index=False):
        if int(row.id) in service_lookup:
            i=service_lookup[int(row.id)];fmat[i]=row.features;has[i]=True
    full_viewed=defaultdict(set);raw_pre_counts=CounterLike()
    with zipfile.ZipFile(Path(cfg['snapshot'])/'movie-id-mapping-run-1_20260910.zip') as z:
        parts=sorted(n for n in z.namelist() if n.startswith('run-1/ratings/') and n.endswith('.parquet'))
        assert len(parts)==128
        for n in parts:
            a=pd.read_parquet(io.BytesIO(z.read(n)),columns=['movielens_user_id','movielens_movie_id','rated_at_epoch'])
            a=a[a.movielens_user_id.isin(users)&(a.rated_at_epoch<cfg['origin'])]
            for u,m in zip(a.movielens_user_id,a.movielens_movie_id):
                raw_pre_counts[int(u)]+=1
                if int(m) in service_lookup:full_viewed[int(u)].add(service_lookup[int(m)])
    converted=[];losses=[]
    for c in contexts:
        assert max(c['input_timestamps'],default=0)<cfg['origin']
        assert all(cfg['origin']<=t<cfg['origin']+cfg['horizon_days']*86400 for t in c['target_timestamps'])
        h=old_to_new[np.asarray(c['oi'],int)];hm=h>=0
        ei=old_to_new[np.asarray(c['ei'],int)];em=ei>=0
        viewed=sorted(full_viewed[c['uid']])
        assert set(h[hm])<=set(viewed),'capped inputs must be in complete service viewed history'
        assert not set(ei[em])&set(viewed),'pre-origin/target overlap'
        oldview=old_to_new[np.asarray(c['viewed'],int)];oldview=set(oldview[oldview>=0])
        rr=[labelmap[(c['uid'],int(oldaxis[i]))] for i in c['ei']]
        converted.append({'uid':c['uid'],'cap':c['cap'],'history':h[hm].tolist(),'stars':np.asarray(c['stars'])[hm].tolist(),
                          'viewed':viewed,'target':ei[em].tolist(),'ratings':np.asarray(rr)[em].tolist(),'raw_pre_count':raw_pre_counts[c['uid']]})
        if c['cap']==10:losses.append({'uid':c['uid'],'old_target_count':len(ei),'unmapped_targets':int((~em).sum()),
                                    'old_viewed_count':len(oldview),'full_service_viewed_count':len(viewed),'extra_viewed':len(set(viewed)-oldview),
                                    'raw_pre_count':raw_pre_counts[c['uid']],'unmapped_inputs':int((~hm).sum())})
    write(dest/'contexts.json',converted);write(dest/'interaction-source-audit.json',{'sources':sources,'train_rows':len(raw),'train_users':int(raw.uid.nunique()),
          'train_max_timestamp':int(raw.timestamp.max()),'overlap_users':0,'factor_rows':len(factors),'mapped_factors':int(has.sum()),
          'mapped_train_ratings':int(count.sum()),'viewed_and_mapping_losses':losses,'future_ratings_read_in_raw_scan':False})
    np.savez_compressed(dest/'interaction-model.npz',count=count,total=total,factors=fmat,has_factor=has)
    return converted,count,total,fmat,has

class CounterLike(defaultdict):
    def __init__(self):super().__init__(int)

def evaluate(cfg):
    verify_seal('prepare');verify_seal('train')
    dest=OUT/'recommend';dest.mkdir(parents=True,exist_ok=True)
    assert not (dest/'selection.json').exists(),'preserve completed selection'
    frame=pd.read_parquet(OUT/'prepare/catalog.parquet');ids=frame.service_movie_id.to_numpy()
    with open(OUT/'train/hierarchies.pkl','rb') as f:bundles=pickle.load(f)
    # Use exact preprocessing, not reduced-precision saved diagnostics, for serving.
    blocks,genre=transform(frame,next(iter(bundles.values()))['preprocessor'])
    x=combine(blocks,[.5,.35,.15])
    contexts,count,total,factors,has=prepare_interactions(frame,cfg)
    predictor=Predictor(x,factors,has,count,total,cfg)
    release=pd.to_datetime(frame.release_date,format='%Y-%m-%d',utc=True,errors='coerce')
    dates=release.astype('int64').to_numpy()//10**9
    content=np.sum(x*x,axis=1)>1e-12
    common=release.notna().to_numpy()&frame.status.eq('Released').to_numpy()&~(frame.adult|frame.video).to_numpy()&content
    eligible=common&(dates<=cfg['origin'])
    deployment=common&(dates<=int(pd.Timestamp('2026-09-09',tz='UTC').timestamp()))
    qualities={'count':count,'mean':np.divide(total,count,out=np.full(len(frame),predictor.mean),where=count>0),'bayes':predictor.bayes}
    orders={q:np.flatnonzero(eligible)[rank(v[eligible],ids[eligible])] for q,v in qualities.items()}
    xs={};groups={};reps={};lists={};space_cache={}
    for key,b in bundles.items():
        weight_key=tuple(b['sub_weights'])
        if weight_key not in space_cache:space_cache[weight_key]=combine(blocks,b['sub_weights'])
        xs[key]=space_cache[weight_key]
        groups[key]=np.load(OUT/'train'/ (key+'-groups.npy'))
        reps[key]=norm(np.concatenate(b['child_centers']))
        for q,order in orders.items():lists[key,q]=[order[groups[key][order]==g] for g in range(len(reps[key]))]
    policies=[]
    for key,q,B,P,Q in itertools.product(bundles,cfg['quality_orders'],cfg['budgets'],cfg['probes'],cfg['quotas']):
        policies.append({'id':f'{key}:{q}:B{B}:P{P}:Q{Q}','kind':'group','hierarchy':key,'quality':q,'budget':B,'probes':P,'quota':Q,'n_groups':len(reps[key])})
    for q,B in itertools.product(cfg['quality_orders'],cfg['budgets']):
        policies.append({'id':f'flat:{q}:B{B}','kind':'flat','hierarchy':None,'quality':q,'budget':B,'probes':0,'quota':0,'n_groups':0})
    allusers=sorted({c['uid'] for c in contexts},key=lambda u:hashlib.sha256(f'k8-user:{u}'.encode()).digest())
    val=set(allusers[:cfg['validation_users']]);verify=set(allusers[cfg['validation_users']:])
    write(dest/'roles.json',{'validation':sorted(val),'verification':sorted(verify),'status':'DEVELOPMENT_ONLY_REUSED_USERS'})
    np.save(dest/'eligible.npy',eligible);np.save(dest/'deployment-eligible.npy',deployment)
    def run(c,p,metric_key=None,candidate_mask=eligible):
        start=time.perf_counter();h=np.asarray(c['history'],int);stars=np.asarray(c['stars'],float);viewed=np.asarray(c['viewed'],int)
        seen=np.zeros(len(frame),bool);seen[viewed]=True
        key=p['hierarchy'] or metric_key or next(iter(bundles))
        if p['kind']=='group':
            profile=signed_profile(xs[key],h,stars,predictor.mean,cfg['profile_prior_mass'])
            under,_=underseen(groups[key],viewed,len(reps[key]),cfg['underseen_max_count'],cfg['underseen_max_share'])
        else:profile=None;under=None
        params=predictor.user(h,stars) if p['kind']!='popularity' else None
        if p['kind']=='full':cand=np.flatnonzero(candidate_mask&~seen);extra={'fill':0,'group_fallback':False}
        elif p['kind']=='popularity':cand=top_unseen(orders['count'],seen,10);extra={'fill':0,'group_fallback':False}
        else:cand,extra=candidates(p,orders[p['quality']],lists[key,p['quality']],reps[key],profile,under,seen,ids)
        if p['kind']!='popularity':
            scores=predictor.predict(params,cand);ranked=cand[rank(scores,ids[cand])[:10]]
        else:ranked=cand
        ms=(time.perf_counter()-start)*1000
        target=np.asarray(c['target'],int);ratings=np.asarray(c['ratings'],float);keep=candidate_mask[target]
        target=target[keep];ratings=ratings[keep]
        if under is None or (metric_key is not None and metric_key!=key):
            key=metric_key or key;under,_=underseen(groups[key],viewed,len(reps[key]),cfg['underseen_max_count'],cfg['underseen_max_share'])
        result=metrics(ranked,cand,target,ratings,groups[key],under,genre,viewed)
        result.update(uid=c['uid'],cap=c['cap'],policy=p['id'],latency_ms=ms,**extra,
                      actual_zero_history=c['raw_pre_count']==0,history_count=len(h),eligible_catalog=int(candidate_mask.sum()),
                      unmapped_or_ineligible_targets=len(c['target'])-len(target),
                      zero_train_slots=int((count[ranked]==0).sum()),service_only_slots=int(frame.mapping_status.iloc[ranked].eq('SERVICE_ONLY_CONTENT_BASED').sum()),
                      ranked=ranked.tolist(),candidate_ids=cand.tolist())
        return result
    rows=[]
    warm=next(c for c in contexts if c['cap']==10 and c['uid'] in val)
    for p in policies:run(warm,p)  # Untimed warmup results are discarded.
    for j,c in enumerate(c for c in contexts if c['cap']==10 and c['uid'] in val):
        rotated=policies[j%len(policies):]+policies[:j%len(policies)]
        for p in rotated:rows.append(run(c,p))
        if j%15==0:print('VALIDATION_USERS',j+1,flush=True)
    vf=pd.DataFrame(rows);vf.to_parquet(dest/'validation-per-user.parquet',index=False)
    summaries=[]
    for p in policies:summaries.append({**p,**aggregate(vf[vf.policy==p['id']])})
    sf=pd.DataFrame(summaries);sf.to_csv(dest/'validation-curves.csv',index=False)
    grouped=sf[sf.kind=='group'];best_ndcg=grouped.ndcg10.max();best_recall=grouped.candidate_recall.max()
    band=grouped[(grouped.ndcg10>=best_ndcg-cfg['ndcg_noninferiority'])&(grouped.candidate_recall>=cfg['candidate_recall_fraction']*best_recall)]
    band_empty=band.empty
    if band_empty:band=grouped[grouped.ndcg10==best_ndcg]
    win=band.sort_values(['budget','ndcg10','probes','quota','n_groups','id'],ascending=[True,False,True,True,True,True]).iloc[0]
    group_policy=next(p for p in policies if p['id']==win['id']);chosen_key=group_policy['hierarchy']
    flat=sf[(sf.kind=='flat')&(sf.budget==group_policy['budget'])].sort_values(['ndcg10','id'],ascending=[False,True]).iloc[0]
    flat_policy=next(p for p in policies if p['id']==flat['id'])
    selection={'group':group_policy,'baseline':flat_policy,'band_empty':band_empty,'best_validation_ndcg':float(best_ndcg),'best_validation_candidate_recall':float(best_recall),
               'selected_hierarchy':chosen_key,'validation_users':sorted(val),'verification_users':sorted(verify),'verification_labels_used_for_selection':False}
    write(dest/'selection.json',selection)  # Write BEFORE verification calls.
    final_policies=[group_policy,flat_policy,{'id':'full-shared','kind':'full','hierarchy':chosen_key,'quality':'bayes'},
                    {'id':'popularity10','kind':'popularity','hierarchy':chosen_key,'quality':'count'}]
    final=[];pred_rows=[]
    for p in final_policies:run(warm,p,metric_key=chosen_key)
    for j,c in enumerate(c for c in contexts if c['uid'] in verify):
        for p in final_policies:
            # Full reference is cap10 only; cap diagnostics compare the selected pair.
            if c['cap']!=10 and p['kind'] in ['full','popularity']:continue
            final.append(run(c,p,metric_key=chosen_key))
        if c['cap']==10:
            target=np.asarray(c['target'],int);rr=np.asarray(c['ratings']);keep=eligible[target];target=target[keep];rr=rr[keep]
            params=predictor.user(c['history'],c['stars'])
            for kind in ['shared','content','bayes']:
                pp=predictor.predict(params,target,kind=kind)
                for i,r,pv in zip(target,rr,pp):pred_rows.append({'uid':c['uid'],'index':int(i),'rating':float(r),'prediction':float(pv),'model':kind,'als_item_supported':bool(has[i]),'zero_train':bool(count[i]==0)})
        if j%100==0:print('VERIFICATION_CONTEXTS',j+1,flush=True)
    ff=pd.DataFrame(final);ff.to_parquet(dest/'verification-per-user.parquet',index=False)
    pd.DataFrame(pred_rows).to_parquet(dest/'rating-predictions.parquet',index=False)
    agg=[]
    for (policy,cap),a in ff.groupby(['policy','cap']):agg.append({'policy':policy,'cap':int(cap),**aggregate(a)})
    write(dest/'verification-summary.json',agg)
    a=ff[(ff.policy==group_policy['id'])&(ff.cap==10)].sort_values('uid')
    b=ff[(ff.policy==flat_policy['id'])&(ff.cap==10)].sort_values('uid')
    assert np.array_equal(a.uid,b.uid)
    ndcg=bootstrap_delta(a.ndcg10,b.ndcg10,cfg);discovery=bootstrap_delta(a.underseen_share,b.underseen_share,cfg)
    adopt=ndcg['ci95'][0] is not None and ndcg['ci95'][0]>=-cfg['ndcg_noninferiority'] and discovery['ci95'][0]>0
    write(dest/'decision.json',{'recommendation':'ADOPT_GROUPED' if adopt else 'KEEP_FLAT_BASELINE','ndcg_delta':ndcg,'underseen_delta':discovery,'status':'DEVELOPMENT_ONLY','invariants_pending':True})
    # Separate 2026 supply run with genuinely materialized deployment lists.
    old_orders=orders.copy();old_lists=lists.copy()
    for q,v in qualities.items():orders[q]=np.flatnonzero(deployment)[rank(v[deployment],ids[deployment])]
    for key in bundles:
        for q,order in orders.items():lists[key,q]=[order[groups[key][order]==g] for g in range(len(reps[key]))]
    supply=[]
    for c in (c for c in contexts if c['cap']==10 and c['uid'] in verify):
        for p in [group_policy,flat_policy]:supply.append(run(c,p,metric_key=chosen_key,candidate_mask=deployment))
    pd.DataFrame(supply).to_parquet(dest/'deployment-supply.parquet',index=False)
    # Supply labels are retained for audit but NOT a deployment-quality claim.
    write(dest/'eligibility.json',{'all_movies':len(frame),'historical_snapshot_assisted':int(eligible.sum()),'deployment_snapshot':int(deployment.sum()),
          'historical_service_only':int((eligible&frame.mapping_status.eq('SERVICE_ONLY_CONTENT_BASED').to_numpy()).sum()),'historical_no_train':int((eligible&(count==0)).sum()),
          'history_source':'complete pre-origin service-mapped run-1; capped inputs old contexts',
          'historical_mapping_status':frame.loc[eligible,'mapping_status'].value_counts().to_dict()})
    seal('recommend')

if __name__=='__main__':
    cfg=read(DOC/'config.json');reviewed('recommend')
    with Guard(cfg,'recommend'),threadpool_limits(limits=cfg['threads']):evaluate(cfg)
