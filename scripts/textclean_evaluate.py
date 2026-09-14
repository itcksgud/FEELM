"""Frozen exploratory comparison of cleaned text with the old T3 development fit."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from textclean_source import ROOT,OLD,OUT,DOC
from textclean_run import verify,guard
from rec046_common import require,pin,write_json
import text339_common as reference
from text339_evaluate import quality,paired_interval,catalog_quality,summarize
from text339_run import verify_model

def evaluate():
    guard();verify('prepared-seal.json');verify('fit-seal.json');verify('catalog-seal.json')
    review=json.loads((DOC/'evaluation-code-review.json').read_text())
    require(review['status']=='PASS' and review['code']==pin(__file__),'independent evaluation code review')
    actual=json.loads((DOC/'prediction-review.json').read_text())
    require(actual['status']=='PASS' and actual['fit_seal']==pin(OUT/'fit-seal.json') and actual['catalog_seal']==pin(OUT/'catalog-seal.json'),'independent actual prediction verification')
    reference.verify('fit-seal.json');reference.verify('catalog-seal.json');reference.verify('evaluation-seal.json')
    verify_model('T3')
    require(not (OUT/'user-metrics.parquet').exists(),'preserve evaluation')
    cats=pd.read_parquet(OUT/'catalog.parquet');ids=cats.movie_id.to_numpy();contexts=json.loads((OUT/'contexts.json').read_text())
    labels=pd.read_parquet(OLD/'labels.parquet');lookup=labels.set_index(['uid','movie_id']).rating.to_dict()
    require(not labels.duplicated(['uid','movie_id']).any(),'unique reference labels')
    old=np.load(OLD/'predictions.npz');require(old['names'].tolist()==['T0','T1','T2','T3'],'reference score axis')
    values=np.column_stack([old['predictions'][:,0],old['predictions'][:,3],np.load(OUT/'models/CLEAN/aligned.npz')['prediction']]);names=['T0','T3','CLEAN']
    require(values.shape==(93230,3) and np.isfinite(values).all(),'common finite score matrix')
    exposure=pd.read_csv(OUT/'text-exposure-users.csv').set_index('uid');rows=[]
    for c in contexts:
        ei=np.asarray(c['ei'],int);truth=np.array([lookup[(c['uid'],int(ids[i]))] for i in ei]);scores=values[c['start']:c['stop']]
        groups={'ALL':np.ones(len(ei),bool)}
        if c['cap']==10:
            blocked=cats.blocked.to_numpy()[ei];counts=cats.train_count.to_numpy()[ei]
            groups.update(C=blocked,W=(~blocked)&(counts>0),NATURAL_ZERO=(~blocked)&(counts==0))
        for group,mask in groups.items():
            if not mask.any():continue
            for j,v in enumerate(names):rows.append({'uid':c['uid'],'cap':c['cap'],'h':c['h'],'activity':c['activity'],'variant':v,'group':group,**quality(truth[mask],scores[mask,j],ids[ei[mask]])})
    user=pd.DataFrame(rows);user.to_parquet(OUT/'user-metrics.parquet',index=False);summarize(user).to_csv(OUT/'metrics.csv',index=False)
    extra=[];primary=user[(user.group=='ALL')&(user.cap==10)].copy()
    for dim in ['h','activity']:
        for name,a in primary.groupby(dim):
            a=a.copy();a['group']=f'{dim}={name}';extra.append(summarize(a))
    for value in [True,False]:
        a=primary[primary.uid.map(exposure.direct_exposure).eq(value)].copy();a['group']='DIRECT_EXPOSURE_'+str(value);extra.append(summarize(a))
    pd.concat(extra).to_csv(OUT/'user-strata.csv',index=False)
    pair=primary[primary.variant=='T3'].set_index('uid').join(primary[primary.variant=='CLEAN'].set_index('uid'),lsuffix='_before',rsuffix='_after',validate='one_to_one').sort_index()
    comparisons={}
    for metric in ['ndcg2','stars2','low2']:
        delta=(pair[metric+'_after']-pair[metric+'_before']).dropna().to_numpy();ci=paired_interval(delta,alpha=.05,seed=339)
        comparisons[metric]={'users':len(delta),'mean_delta':float(delta.mean()) if len(delta) else None,'interval_95':ci}
    lower=comparisons['ndcg2']['interval_95'][0]
    improved=lower is not None and lower>0 and comparisons['stars2']['mean_delta'] is not None and comparisons['low2']['mean_delta'] is not None and comparisons['stars2']['mean_delta']>=-.1 and comparisons['low2']['mean_delta']<=.03
    decision={'comparison':'CLEAN-T3','improvement_criterion_passed':bool(improved),'comparisons':comparisons,
              'T0_adoption_decision':'NOT_TESTED_PRIMARY_T0_REMAINS_REFERENCE','scope':'Already opened development users; conditional on two one-seed fitted models',
              'seed':339,'bootstrap_draws':20000,'safety':'point-estimate veto; not safety certification'}
    decision['status']='SUFFICIENT_DEVELOPMENT_USERS' if lower is not None else 'INSUFFICIENT_PAIRED_USERS'
    write_json(OUT/'decision.json',decision)
    oldtop=pd.read_parquet(OLD/'catalog-top10.parquet');newtop=pd.read_parquet(OUT/'catalog-top10.parquet');top=pd.concat([oldtop[oldtop.variant.isin(['T0','T3'])],newtop],ignore_index=True)
    require(len(top)==270*3*10 and not top.duplicated(['uid','variant','rank']).any(),'same full catalog slots')
    meta=pd.read_parquet(ROOT/reference.config()['metadata']).set_index('movie_id');full=[];aggregate=[]
    for (uid,v),a in top.groupby(['uid','variant'],sort=True):
        a=a.sort_values('rank');truth=np.array([lookup.get((int(uid),int(m)),np.nan) for m in a.movie_id]);require(a['rank'].tolist()==list(range(1,11)),'complete top10')
        for n in [2,4,6,10]:full.append({'uid':int(uid),'variant':v,'h':int(a.h.iloc[0]),'n':n,**catalog_quality(truth,n)})
    full=pd.DataFrame(full);full.to_csv(OUT/'catalog-user-diagnostics.csv',index=False)
    for (v,n),a in full.groupby(['variant','n'],sort=True):
        for group,mask in [('ALL',np.ones(len(a),bool)),('h0',a.h.eq(0).to_numpy()),('h_positive',a.h.gt(0).to_numpy())]:
            b=a[mask];selected=top[(top.variant==v)&(top['rank']<=n)&top.uid.isin(b.uid)];freq=selected.movie_id.value_counts(normalize=True)
            tm=meta.loc[selected.movie_id];one=(tm.tmdb_vote_count.eq(1)&tm.tmdb_vote_average.eq(10))
            aggregate.append({'variant':v,'n':int(n),'group':group,'users':len(b),'slots':int(b.returned.sum()),'known':int(b.known.sum()),'unknown':int(b.unknown.sum()),
                'users_with_known':int(b.known.gt(0).sum()),'known_fraction':float(b.known_fraction.mean()),
                'C_slots':int(selected.blocked.sum()),'W_slots':int(((~selected.blocked)&selected.train_count.gt(0)).sum()),
                'natural_zero_slots':int(((~selected.blocked)&selected.train_count.eq(0)).sum()),
                'unique_movies':len(freq),'exposure_hhi':float((freq**2).sum()),'tmdb_one_vote_ten_slots':int(one.sum()),'fill_fraction':float(b.fill_fraction.mean()),
                'known_only_user_mean_stars':float(b.known_only_stars.mean()) if b.known_only_stars.notna().any() else None})
    pd.DataFrame(aggregate).to_csv(OUT/'catalog-diagnostics.csv',index=False)
    metrics=json.loads((OUT/'models/CLEAN/metrics.json').read_text());oldmetrics=json.loads((OLD/'models/T3/metrics.json').read_text())
    resource={'CLEAN':dict(metrics,strict_peak_12GiB_pass=metrics.get('peak_bytes',float('inf'))<=12*1024**3),
              'T3':dict(oldmetrics,strict_peak_12GiB_pass=oldmetrics.get('peak_bytes',float('inf'))<=12*1024**3)}
    write_json(OUT/'resources.json',resource)
    files=['user-metrics.parquet','metrics.csv','user-strata.csv','decision.json','catalog-user-diagnostics.csv','catalog-diagnostics.csv','resources.json']
    write_json(OUT/'evaluation-seal.json',{'code':pin(__file__),'files':{n:pin(OUT/n) for n in files},'fit_seal':pin(OUT/'fit-seal.json'),
               'catalog_seal':pin(OUT/'catalog-seal.json'),'old_evaluation_seal':pin(OLD/'evaluation-seal.json'),'labels':pin(OLD/'labels.parquet'),'labels_previously_opened':True})
    print(json.dumps(decision),flush=True)

if __name__=='__main__':evaluate()
