"""Paired user-macro quality and ALS fidelity on identical observed movies."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from cold_item_common import *

def interval(values, alpha=.05):
    values=np.asarray(values,float);values=values[np.isfinite(values)]
    if len(values)<30:return [None,None]
    rng=np.random.default_rng(47);means=[]
    for _ in range(100):
        ix=rng.integers(len(values),size=(200,len(values)))
        means.extend(values[ix].mean(axis=1))
    return np.quantile(means,[alpha/2,1-alpha/2]).tolist()

def evaluate():
    reviewed();verify_seal(OUT/'prepared-seal.json',OUT);fit=verify_seal(OUT/'fit-seal.json',OUT)
    require(fit['prepared_seal']==pin(OUT/'prepared-seal.json'),'fit input parent')
    require(not (OUT/'metrics.csv').exists(),'preserve evaluation')
    cat=np.load(OUT/'catalog.npz');ids=cat['movie_ids'];groups=support_group(cat['pre_v_counts'])
    contexts=json.loads((OUT/'contexts.json').read_text())
    predictions=np.load(OUT/'predictions.npz');names=predictions['names'].tolist();values=predictions['predictions']
    n=check_contexts(contexts,cat)
    require(values.shape==(n,13) and names==PREDICTION_NAMES and np.isfinite(values).all(),'all planned models/finite/column order')
    needed={(c['uid'],int(ids[i])) for c in contexts for i in c['ei']}
    # The prior development labels are already opened research data, not a new final test.
    source=OLD/'evaluation/labels.parquet'
    require(pin(source)==config()['sources'][source.relative_to(ROOT).as_posix()],'label source unchanged')
    labels=pd.read_parquet(source)
    labels=labels[[pair in needed for pair in labels[['uid','movie_id']].itertuples(index=False,name=None)]]
    require(len(labels)==len(needed) and not labels.duplicated(['uid','movie_id']).any(),'complete unique labels')
    require(np.isin(labels.rating,np.arange(1,11)/2).all(),'real half-star labels')
    labels.to_parquet(OUT/'labels.parquet',index=False)
    lookup=labels.set_index(['uid','movie_id']).rating.to_dict()
    rows=[];movie_sets={}
    for c in contexts:
        ei=np.asarray(c['ei']);a=values[c['start']:c['stop']];ref=a[:,names.index('ALS')]
        y=np.array([lookup[(c['uid'],int(ids[i]))] for i in ei])
        for group in ['ALL','1_9','10_49','50_PLUS']:
            mask=np.ones(len(ei),bool) if group=='ALL' else groups[ei]==group
            if not mask.any():continue
            truth=y[mask];teacher=ref[mask]
            movie_sets[(c['uid'],c['k'],group)]=set(map(int,ids[ei[mask]]))
            for j,name in enumerate(names):
                raw=a[:,j][mask];clipped=np.clip(raw,.5,5)
                pa,n_pairs=concordance(truth,raw)
                agreement,n_refpairs=concordance(teacher,raw)
                rows.append({'uid':c['uid'],'k':c['k'],'method':name,'activity':c['activity'],'support':group,
                             'common_k30':c['pre_warm']>=30,'targets':len(truth),'comparable_pairs':n_pairs,'reference_pairs':n_refpairs,
                             'mse':float(np.mean((clipped-truth)**2)),'mae':float(np.mean(abs(clipped-truth))),
                             'pa':pa,'als_mae':float(np.mean(abs(raw-teacher))),'als_order':agreement,
                             'outside_scale':float(np.mean((raw<.5)|(raw>5)))})
    user=pd.DataFrame(rows);user.to_parquet(OUT/'user-metrics.parquet',index=False)
    def denominators(a, metric):
        valid=a[a[metric].notna()]
        def movies(s):
            result=set()
            for key in s[['uid','k','support']].itertuples(index=False,name=None):result.update(movie_sets[key])
            return len(result)
        return {'users_total':len(a),'users_valid':len(valid),
                'observations_total':int(a.targets.sum()),'observations_valid':int(valid.targets.sum()),
                'movies_total':movies(a),'movies_valid':movies(valid),
                'comparable_pairs_total':int(a.comparable_pairs.sum()),'comparable_pairs_valid':int(valid.comparable_pairs.sum()),
                'reference_pairs_total':int(a.reference_pairs.sum()),'reference_pairs_valid':int(valid.reference_pairs.sum()),
                'status':'NO_DATA' if not len(valid) else 'DESCRIPTIVE_SMALL_N' if len(valid)<30 else 'DESCRIPTIVE'}
    main=[];strata=[]
    for (k,method),a in user[user.support=='ALL'].groupby(['k','method']):
        for cohort in ['eligible_k','common_k30']:
            s=a[a.common_k30] if cohort=='common_k30' else a
            for metric in ['mse','mae','pa','als_mae','als_order','outside_scale']:
                x=s[metric].dropna().to_numpy();ci=interval(x)
                main.append({'k':k,'method':method,'cohort':cohort,'metric':metric,'users':len(x),'mean':float(x.mean()) if len(x) else None,'ci95_low':ci[0],'ci95_high':ci[1],**denominators(s,metric)})
    for k in KS:
        for method in names:
            for act in ['0','1_9','10_29','30_99','100_299','300_PLUS']:
                for group in ['1_9','10_49','50_PLUS']:
                    a=user[(user.k==k)&(user.method==method)&(user.activity==act)&(user.support==group)]
                    for metric in ['mse','pa','als_mae','als_order']:
                        x=a[metric].dropna()
                        strata.append({'k':k,'method':method,'activity':act,'support':group,'metric':metric,'users':len(x),
                                       'mean':float(x.mean()) if len(x) else None,**denominators(a,metric)})
    pd.DataFrame(main).to_csv(OUT/'metrics.csv',index=False);pd.DataFrame(strata).to_csv(OUT/'strata.csv',index=False)
    contrasts=[]
    # Primary K=10 and 30, two actual-quality metrics; familywise Bonferroni intervals.
    pairs=[(VARIANTS[i]+'__'+m,VARIANTS[i-1]+'__'+m,'increment') for m in METHODS for i in range(1,4)]
    pairs += [(v+'__'+m,'ALS','reference') for m in METHODS for v in VARIANTS]
    for k in [10,30]:
        a=user[(user.k==k)&(user.support=='ALL')]
        for newer,older,family in pairs:
            left=a[a.method==newer].set_index('uid');right=a[a.method==older].set_index('uid')
            require(left.index.equals(right.index),'paired users')
            for metric in ['mse','pa']:
                delta=(left[metric]-right[metric]).dropna();ci=interval(delta,alpha=.05/(36 if family=='increment' else 48))
                contrasts.append({'family':family,'k':k,'new':newer,'reference':older,'metric':metric,'users':len(delta),
                                  'difference':float(delta.mean()) if len(delta) else None,'ci_low':ci[0],'ci_high':ci[1]})
    pd.DataFrame(contrasts).to_csv(OUT/'contrasts.csv',index=False)
    write_json(OUT/'completion.json',{'status':'CALCULATED_PENDING_INDEPENDENT_REVIEW','fingerprint':fingerprint(),'fit_seal':pin(OUT/'fit-seal.json'),
               'unique_users':len({c['uid'] for c in contexts}),'unique_movies':int(labels.movie_id.nunique()),'unique_observations':len(labels),
               'scope':'previously opened development ratings, synthetic cold target movies, 2026 crowd snapshot exploratory',
               'files':{n:pin(OUT/n) for n in ['labels.parquet','user-metrics.parquet','metrics.csv','strata.csv','contrasts.csv']}})
    print('CALCULATED_PENDING_INDEPENDENT_REVIEW',len(labels),flush=True)

if __name__=='__main__':evaluate()
