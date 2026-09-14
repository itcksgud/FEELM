"""Predeclared paired development decisions; UNKNOWN remains unscored."""
import json
import numpy as np
import pandas as pd
from foundation340_common import *
from text339_evaluate import quality, paired_interval, summarize

def evaluation_fingerprint():
    return {**{n:pin(ROOT/'scripts'/n) for n in ['foundation340_evaluate.py','text339_evaluate.py']},
            'evaluation-contract.json':pin(DOC/'evaluation-contract.json')}

def interval_record(pair, metric, alpha=.05):
    values=(pair[metric+'_after']-pair[metric+'_before']).dropna().to_numpy()
    return {'users':len(values),'mean_delta':float(values.mean()) if len(values) else None,
            'interval':paired_interval(values,alpha,340),'alpha':alpha}

def evaluate():
    reviewed(); verify('prepared-seal.json'); verify('fit-seal.json'); verify('catalog-seal.json')
    audit=json.loads((DOC/'evaluation-code-review.json').read_text())
    require(audit['status']=='PASS' and audit['fingerprint']==evaluation_fingerprint(),'independent evaluation code review')
    audit=json.loads((DOC/'prediction-review.json').read_text())
    require(audit['status']=='PASS' and audit['fit_seal']==pin(OUT/'fit-seal.json') and audit['catalog_seal']==pin(OUT/'catalog-seal.json'),'independent predictions review')
    for name in ['evaluation-seal.json','user-metrics.parquet','metrics.csv','selection.json','catalog-diagnostics.csv']:
        require(not (OUT/name).exists(),'preserve evaluation output '+name)
    contexts=json.loads((OLD/'contexts.json').read_text()); cats=pd.read_parquet(OLD/'catalog.parquet')
    meta=pd.read_parquet(ROOT/config()['metadata']); ids=cats.movie_id.to_numpy()
    require(np.array_equal(meta.movie_id.to_numpy(),ids),'metadata catalog identity')
    contract=json.loads((DOC/'evaluation-contract.json').read_text())
    for name,expected in contract['parents'].items(): require(pin(ROOT/name)==expected,'fixed label source parent')
    old_eval=json.loads((OLD/'evaluation-seal.json').read_text())
    require(pin(OLD/'labels.parquet')==old_eval['files']['labels.parquet'],'previously opened development label identity')
    labels=pd.read_parquet(OLD/'labels.parquet')
    require(not labels.duplicated(['uid','movie_id']).any(),'unique labels')
    lookup=labels.set_index(['uid','movie_id']).rating.to_dict()
    saved=np.load(OUT/'predictions.npz'); require(saved['names'].tolist()==VARIANTS,'model order')
    values=saved['predictions']; require(values.shape==(93230,4) and np.isfinite(values).all(),'complete finite scores')
    rows=[]
    votes=meta.tmdb_vote_count.to_numpy(float)
    for c in contexts:
        ei=np.array(c['ei'],int); truth=np.array([lookup[(c['uid'],int(ids[i]))] for i in ei])
        scores=values[c['start']:c['stop']]
        masks={'ALL':np.ones(len(ei),bool)}
        if c['cap']==10:
            blocked=cats.blocked.to_numpy()[ei]; count=cats.train_count.to_numpy()[ei]
            masks.update({'C':blocked,'W':~blocked&(count>0),'NATURAL_ZERO':~blocked&(count==0),
                          'VOTES_0':votes[ei]==0,'VOTES_1_9':(votes[ei]>=1)&(votes[ei]<10),
                          'VOTES_10_99':(votes[ei]>=10)&(votes[ei]<100),'VOTES_100_PLUS':votes[ei]>=100})
            for label,lo,hi in [('0',0,1),('1_9',1,10),('10_49',10,50),('50_PLUS',50,np.inf)]:
                masks['SUPPORT_'+label]=(count>=lo)&(count<hi)
        for group,mask in masks.items():
            if not mask.any(): continue
            for j,v in enumerate(VARIANTS):
                rows.append({'uid':c['uid'],'cap':c['cap'],'h':c['h'],'activity':c['activity'],'variant':v,'group':group,
                             **quality(truth[mask],scores[mask,j],ids[ei[mask]])})
    user=pd.DataFrame(rows); user.to_parquet(OUT/'user-metrics.parquet',index=False)
    tables=[summarize(user)]
    h10=user[user.cap.eq(10)&user.group.eq('ALL')]
    for group,subset in [('H_POSITIVE',h10[h10.h.gt(0)]),('H_ZERO',h10[h10.h.eq(0)])]:
        part=subset.copy(); part['group']=group; tables.append(summarize(part))
    for act,subset in h10.groupby('activity'):
        part=subset.copy(); part['group']='ACTIVITY_'+str(act); tables.append(summarize(part))
    for actual_h,subset in h10.groupby('h'):
        part=subset.copy(); part['group']='ACTUAL_H_'+str(actual_h); tables.append(summarize(part))
    pd.concat(tables,ignore_index=True).to_csv(OUT/'metrics.csv',index=False)
    comparisons=[]; eligible=[]
    for before,after in config()['formal_contrasts']:
        record={'before':before,'after':after,'cohorts':{}}
        for group,part in [('H_POSITIVE',h10[h10.h.gt(0)]),('H_ZERO',h10[h10.h.eq(0)]),('ALL',h10)]:
            pair=part[part.variant.eq(before)].set_index('uid').join(part[part.variant.eq(after)].set_index('uid'),lsuffix='_before',rsuffix='_after',validate='one_to_one').sort_index()
            record['cohorts'][group]={metric:interval_record(pair,metric,.01 if metric=='ndcg2' and group=='H_POSITIVE' else .05) for metric in ['ndcg2','stars2','low2']}
        primary=record['cohorts']['H_POSITIVE']['ndcg2']
        record['primary_improves']=primary['interval'][0] is not None and primary['interval'][0]>0
        record['point_veto_pass']=all(record['cohorts'][g]['stars2']['mean_delta'] is not None and record['cohorts'][g]['stars2']['mean_delta']>=-.1 and record['cohorts'][g]['low2']['mean_delta']<=.03 for g in ['H_POSITIVE','H_ZERO'])
        if before=='B' and record['primary_improves'] and record['point_veto_pass']: eligible.append(after)
        comparisons.append(record)
    tie={'R':0,'H':1,'RH':2}
    means=h10[h10.h.gt(0)].groupby('variant').ndcg2.mean().to_dict()
    selected=min(eligible,key=lambda v:(-means[v],tie[v])) if eligible else 'B'
    wide=h10[h10.h.gt(0)].pivot(index='uid',columns='variant',values='ndcg2').dropna()
    interaction=(wide.RH-wide.H)-(wide.R-wide.B)
    write_json(OUT/'selection.json',{'selected':selected,'eligible':eligible,'h_positive_ndcg2_means':means,'comparisons':comparisons,
                                   'interaction_descriptive_only':{'users':len(interaction),'ndcg2_difference_of_differences':float(interaction.mean()),'formal_test':False},
                                   'claim':'Development candidate only; one fit each, already used MovieLens labels/current TMDB. Point veto is not a safety proof.'})
    top=pd.read_parquet(OUT/'catalog-top10.parquet'); full=[]
    top['rating']=[lookup.get((int(u),int(m)),np.nan) for u,m in top[['uid','movie_id']].itertuples(index=False,name=None)]
    ix=np.searchsorted(ids,top.movie_id)
    top['one_vote_ten']=(meta.tmdb_vote_count.to_numpy()[ix]==1)&(meta.tmdb_vote_average.to_numpy()[ix]==10)
    for v in VARIANTS:
        for group,mask in [('ALL',np.ones(len(top),bool)),('H_ZERO',top.h.eq(0)),('H_POSITIVE',top.h.gt(0))]:
            for n in [2,4,6,10]:
                a=top[top.variant.eq(v)&mask&top['rank'].le(n)]; freq=a.movie_id.value_counts(normalize=True)
                full.append({'variant':v,'group':group,'n':n,'users':int(a.uid.nunique()),'slots':len(a),'known':int(a.rating.notna().sum()),
                             'unknown':int(a.rating.isna().sum()),'known_only_stars':float(a.rating.mean()) if a.rating.notna().any() else None,
                             'one_vote_ten_slots':int(a.one_vote_ten.sum()),'unique_movies':len(freq),'exposure_hhi':float((freq**2).sum()),
                             'zero_train_slots':int(a.train_count.eq(0).sum())})
    pd.DataFrame(full).to_csv(OUT/'catalog-diagnostics.csv',index=False)
    seal('evaluation-seal.json',['user-metrics.parquet','metrics.csv','selection.json','catalog-diagnostics.csv'],
         evaluation_code=evaluation_fingerprint(),fit_seal=pin(OUT/'fit-seal.json'),catalog_seal=pin(OUT/'catalog-seal.json'),labels=pin(OLD/'labels.parquet'))
    print('EVALUATED',selected,json.dumps(means),flush=True)

if __name__=='__main__':evaluate()
