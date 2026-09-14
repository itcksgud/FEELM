"""Observed-candidate quality and separate full-catalog UNKNOWN diagnostics."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from text339_common import *

def paired_interval(values,alpha=.05,seed=339):
    values=np.asarray(values,float);values=values[np.isfinite(values)]
    if len(values)<30:return [None,None]
    rng=np.random.default_rng(seed);means=[]
    for _ in range(100):means.extend(values[rng.integers(len(values),size=(200,len(values)))].mean(1))
    return np.quantile(means,[alpha/2,1-alpha/2],method='linear').tolist()

def quality(truth,pred,ids):
    truth,pred,ids=np.asarray(truth),np.asarray(pred),np.asarray(ids)
    require(len(truth)==len(pred)==len(ids)>0 and np.isfinite(pred).all(),'finite complete scoring set')
    require(np.isin(truth,np.arange(1,11)/2).all(),'real half stars')
    rank=np.lexsort((ids,-pred));clipped=np.clip(pred,.5,5)
    out={'mse':float(np.mean((truth-clipped)**2)),'mae':float(np.mean(abs(truth-clipped))),
         'raw_mse':float(np.mean((truth-pred)**2)),'raw_mae':float(np.mean(abs(truth-pred))),
         'outside_scale':float(np.mean((pred<.5)|(pred>5))),'j':len(truth),'good_total':int((truth>=4).sum()),
         'prediction_ties':int(len(pred)-len(np.unique(pred)))}
    i,j=np.triu_indices(len(truth),1);dt=truth[i]-truth[j];dp=pred[i]-pred[j];valid=dt!=0
    out['pairs']=int(valid.sum());out['pa']=float(np.mean(np.where(abs(dp[valid])<=1e-12,.5,(dp[valid]*dt[valid]>0).astype(float)))) if valid.any() else np.nan
    gain=(truth-.5)/4.5
    for n in [2,4,6,10]:
        if len(truth)<n:continue
        top=truth[rank[:n]];discount=1/np.log2(np.arange(n)+2);ideal=np.sort(gain)[::-1][:n]@discount
        out[f'ndcg{n}']=float(gain[rank[:n]]@discount/ideal) if ideal>0 else np.nan
        out[f'stars{n}']=float(top.mean());out[f'precision{n}']=float(np.mean(top>=4));out[f'hit{n}']=float(np.any(top>=4))
        out[f'recall{n}']=float((top>=4).sum()/(truth>=4).sum()) if np.any(truth>=4) else np.nan
        out[f'low{n}']=float(np.mean(top<=2));out[f'any_low{n}']=float(np.any(top<=2))
    return out

def summarize(user):
    ignore={'uid','cap','h','activity','variant','group','j','good_total','pairs','prediction_ties'}
    metrics=[c for c in user.columns if c not in ignore];rows=[]
    for (cap,variant,group),a in user.groupby(['cap','variant','group'],sort=True):
        for cohort in ['eligible_n','common_j6']:
            b=a[a.j>=6] if cohort=='common_j6' else a
            for name in metrics:
                # Common J>=6 is genuinely common only for Top2/4/6.
                if cohort=='common_j6' and (name.endswith('10') or name in ['mse','mae','raw_mse','raw_mae','pa','outside_scale']):continue
                v=b[name].dropna()
                rows.append({'cap':cap,'variant':variant,'group':group,'cohort':cohort,'metric':name,'users_total':len(b),
                             'users_valid':len(v),'mean':float(v.mean()) if len(v) else None,'status':'DESCRIPTIVE_SMALL_N' if len(v)<30 else 'DESCRIPTIVE'})
    return pd.DataFrame(rows)

def catalog_quality(ratings,requested_n):
    v=np.asarray(ratings,float)[:requested_n];r=len(v);known=np.isfinite(v);good=int((v>=4).sum());unknown=int((~known).sum())
    return {'returned':r,'missing_slots':requested_n-r,'fill_fraction':r/requested_n,'known':int(known.sum()),'unknown':unknown,
            'known_fraction':float(known.sum()/r) if r else np.nan,
            'known_only_stars':float(v[known].mean()) if known.any() else np.nan,
            'like_lower_bound':good/r if r else np.nan,'like_upper_bound':(good+unknown)/r if r else np.nan}

def evaluate():
    reviewed();verify('prepared-seal.json');verify('fit-seal.json');verify('catalog-seal.json')
    require(not (OUT/'metrics.csv').exists(),'preserve evaluated results')
    cfg=config();contexts=json.loads((OUT/'contexts.json').read_text());catalog=pd.read_parquet(OUT/'catalog.parquet');ids=catalog.movie_id.to_numpy()
    texts=pd.read_parquet(OUT/'texts.parquet');source=OLD/'labels.parquet'
    require(pin(source)==cfg['sources'][source.relative_to(ROOT).as_posix()],'previously opened development labels')
    labels=pd.read_parquet(source);labels=labels[labels.uid.isin([c['uid'] for c in contexts])]
    require(not labels.duplicated(['uid','movie_id']).any() and np.isin(labels.rating,np.arange(1,11)/2).all(),'unique real observed labels')
    lookup=labels.set_index(['uid','movie_id']).rating.to_dict();pred=np.load(OUT/'predictions.npz')
    require(pred['names'].tolist()==VARIANTS,'prediction names')
    values=pred['predictions'];require(values.shape==(contexts[-1]['stop'],4) and np.isfinite(values).all(),'aligned complete prediction matrix')
    require(check_contexts(contexts,catalog)==len(values),'checked evaluation contexts')
    rows=[];movie_errors=[];candidate_sets={}
    years=pd.to_datetime(texts.release_date,format='%Y-%m-%d',errors='coerce',utc=True).dt.year.fillna(-1).to_numpy(int)
    decades=np.where(years>=0,years//10*10,-1)
    for c in contexts:
        ei=np.asarray(c['ei'],int);truth=np.array([lookup[(c['uid'],int(ids[i]))] for i in ei]);scores=values[c['start']:c['stop']]
        group_masks={'ALL':np.ones(len(ei),bool)}
        if c['cap']==10:
            blocked=catalog.blocked.to_numpy()[ei];counts=catalog.train_count.to_numpy()[ei]
            group_masks.update({'C':blocked,'W':(~blocked)&(counts>0),'NATURAL_ZERO':(~blocked)&(counts==0),
                                'WIKI_SOURCE':texts.exclusion_reason.eq('').to_numpy()[ei],
                                'WIKI_PAIRED_NONEMPTY':(texts.T2.ne('')&texts.T3.ne('')).to_numpy()[ei],
                                'WIKI_RESTORED':texts.restored_body.to_numpy()[ei],
                                'WIKI_CHANGED_NONEMPTY':texts.changed_nonempty.to_numpy()[ei],
                                'WIKI_MISSING_BOTH':(texts.T2.eq('')&texts.T3.eq('')).to_numpy()[ei],
                                'WIKI_KO':(texts.language.eq('ko')&texts.exclusion_reason.eq('')).to_numpy()[ei],
                                'WIKI_EN':(texts.language.eq('en')&texts.exclusion_reason.eq('')).to_numpy()[ei]})
            for g in ['0','1_9','10_49','50_PLUS']:group_masks['POST_SUPPORT_'+g]=support(counts)==g
            for g in ['0','1_9','10_49','50_PLUS']:group_masks['PRE_SUPPORT_'+g]=support(catalog.reference_count.to_numpy()[ei])==g
            for decade in sorted(set(decades[ei])):group_masks['RELEASE_DECADE_'+str(decade)]=decades[ei]==decade
        for group,mask in group_masks.items():
            if not mask.any():continue
            candidate_sets[(c['uid'],c['cap'],group)]=set(map(int,ids[ei[mask]]))
            for j,variant in enumerate(VARIANTS):
                rows.append({'uid':c['uid'],'cap':c['cap'],'h':c['h'],'activity':c['activity'],'variant':variant,'group':group,
                             **quality(truth[mask],scores[mask,j],ids[ei[mask]])})
        if c['cap']==10:
            for j,variant in enumerate(VARIANTS):
                for i,y,p in zip(ei,truth,scores[:,j]):movie_errors.append({'movie_id':int(ids[i]),'variant':variant,'squared_error':float((np.clip(p,.5,5)-y)**2),'absolute_error':float(abs(np.clip(p,.5,5)-y))})
    user=pd.DataFrame(rows);user.to_parquet(OUT/'user-metrics.parquet',index=False)
    denominators=[]
    for (cap,group),a in user[user.variant.eq('T0')].groupby(['cap','group']):
        for n in [1,2,4,6,10]:
            b=a[a.j>=n];movie_ids=set()
            for uid in b.uid:movie_ids.update(candidate_sets[(uid,cap,group)])
            denominators.append({'cap':cap,'group':group,'n':n,'users_total':len(a),'users_with_enough_candidates':len(b),
                                 'users_short':int((a.j<n).sum()),'observations_eligible':int(b.j.sum()),'unique_movies_eligible':len(movie_ids),
                                 'users_no_good':int((b.good_total==0).sum()),'users_prediction_ties_T0':int((b.prediction_ties>0).sum()),
                                 'users_idcg_zero':int(b.get('ndcg'+str(n),pd.Series(dtype=float)).isna().sum()) if n>1 else None})
    pd.DataFrame(denominators).to_csv(OUT/'cohort-denominators.csv',index=False)
    table=summarize(user);table.to_csv(OUT/'metrics.csv',index=False)
    # Activity and actual h are user groups, retaining the original ALL candidate pool.
    extra=[]
    for dim in ['activity','h']:
        for name,a in user[user.group.eq('ALL')&user.cap.eq(10)].groupby(dim):
            a=a.copy();a['group']=f'{dim}={name}';extra.append(summarize(a))
    pd.concat(extra).to_csv(OUT/'user-strata.csv',index=False)
    primary=user[(user.group=='ALL')&(user.cap==10)].copy();comparisons=[];selection='T0';stop=False
    for before,after in zip(VARIANTS,VARIANTS[1:]):
        pair=primary[primary.variant==before].set_index('uid').join(primary[primary.variant==after].set_index('uid'),lsuffix='_before',rsuffix='_after',validate='one_to_one').sort_index()
        record={'comparison':after+'-'+before,'selected_before':selection,'stage_was_reached':not stop}
        for name in ['ndcg2','stars2','low2']:
            delta=(pair[name+'_after']-pair[name+'_before']).dropna().to_numpy()
            alpha=.05/3 if name=='ndcg2' else .05
            ci=paired_interval(delta,alpha)
            record[name]={'users':len(delta),'mean_delta':float(delta.mean()) if len(delta) else None,'interval':ci,'interval_alpha':alpha}
        improve=record['ndcg2']['interval'][0] is not None and record['ndcg2']['interval'][0]>0
        no_large_observed_loss=record['stars2']['mean_delta'] is not None and record['stars2']['mean_delta']>=-.1 and record['low2']['mean_delta']<=.03
        record['advance']=bool(not stop and improve and no_large_observed_loss)
        if record['advance']:selection=after
        else:stop=True
        comparisons.append(record)
    write_json(OUT/'selection.json',{'selected':selection,'comparisons':comparisons,'claim':'Development representation decision only; no fresh final or current Korean quality claim','safety_rule':'Observed point-estimate veto, not simultaneous safety certification'})
    pd.DataFrame(movie_errors).groupby(['movie_id','variant']).agg(observations=('squared_error','size'),mse=('squared_error','mean'),mae=('absolute_error','mean')).reset_index().to_csv(OUT/'movie-sensitivity.csv',index=False)
    top=pd.read_parquet(OUT/'catalog-top10.parquet');full=[]
    for c in [c for c in contexts if c['cap']==10]:
        uid=c['uid']
        for variant in VARIANTS:
            a=top[(top.uid==uid)&(top.variant==variant)].sort_values('rank');r=np.array([lookup.get((int(uid),int(m)),np.nan) for m in a.movie_id])
            for n in [2,4,6,10]:
                full.append({'uid':int(uid),'variant':variant,'n':n,**catalog_quality(r,n),
                             'zero_support_fraction':float((a.train_count.head(n)==0).mean()) if len(a) else np.nan})
    full=pd.DataFrame(full);full.to_csv(OUT/'catalog-user-diagnostics.csv',index=False)
    aggregates=full.groupby(['variant','n']).agg(users=('uid','size'),users_with_returned=('known_fraction','count'),users_with_known=('known_only_stars','count'),
              returned=('returned','mean'),missing_slots=('missing_slots','mean'),fill_fraction=('fill_fraction','mean'),known_fraction=('known_fraction','mean'),
              unknown=('unknown','mean'),known_only_stars=('known_only_stars','mean'),like_lower_bound=('like_lower_bound','mean'),like_upper_bound=('like_upper_bound','mean'),zero_support_fraction=('zero_support_fraction','mean')).reset_index()
    for row in aggregates.itertuples():
        shown=top[(top.variant==row.variant)&(top['rank']<=row.n)];freq=shown.movie_id.value_counts(normalize=True)
        ix=(aggregates.variant==row.variant)&(aggregates.n==row.n)
        aggregates.loc[ix,'unique_movies']=len(freq);aggregates.loc[ix,'exposure_hhi']=float((freq**2).sum())
    aggregates.to_csv(OUT/'catalog-diagnostics.csv',index=False)
    labels.to_parquet(OUT/'labels.parquet',index=False)
    files=['metrics.csv','cohort-denominators.csv','user-strata.csv','user-metrics.parquet','selection.json','movie-sensitivity.csv','catalog-user-diagnostics.csv','catalog-diagnostics.csv','labels.parquet']
    seal('evaluation-seal.json',files,fit_seal=pin(OUT/'fit-seal.json'),catalog_seal=pin(OUT/'catalog-seal.json'),labels_previously_opened=True)
    print('EVALUATED',selection,flush=True)

if __name__=='__main__':evaluate()
