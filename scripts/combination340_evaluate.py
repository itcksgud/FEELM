"""Separate cold content ability from warm ALS contribution on fixed candidates."""
import json
import numpy as np
import pandas as pd
from combination340_common import *
from combination340_catalog import verify_catalog
from text339_evaluate import quality,paired_interval,summarize

CONTENTS=['S','NO_RESPONSE','GBT','BLEND_0.25','BLEND_0.5','BLEND_0.75','BLEND_1.0']

def evaluation_fingerprint(): return {'combination340_evaluate.py':pin(__file__)}

def delta(pair,name,alpha=.05):
    d=(pair[name+'_after']-pair[name+'_before']).dropna().to_numpy()
    return {'users':len(d),'mean_delta':float(d.mean()) if len(d) else None,'interval':paired_interval(d,alpha,340),'alpha':alpha}

def joined(user,after,group,positive=True):
    a=user[user.cap.eq(10)&user.group.eq(group)&(user.h.gt(0) if positive else user.h.eq(0))]
    return a[a.variant.eq('S')].set_index('uid').join(a[a.variant.eq(after)].set_index('uid'),lsuffix='_before',rsuffix='_after',validate='one_to_one').sort_index()

def evaluate():
    reviewed(); verify('fit-seal.json'); verify_catalog()
    audit=json.loads((DOC/'evaluation-code-review.json').read_text())
    require(audit['status']=='PASS' and audit['fingerprint']==evaluation_fingerprint(),'independent extension evaluation review')
    audit=json.loads((DOC/'prediction-review.json').read_text())
    require(audit['status']=='PASS' and audit['fit_seal']==pin(OUT/'fit-seal.json') and audit['catalog_seal']==pin(OUT/'catalog-seal.json'),'independent actual extension predictions')
    for name in ['evaluation-seal.json','user-metrics.parquet','metrics.csv','selection.json','reference-agreement.csv','catalog-diagnostics.csv']:
        require(not (OUT/name).exists(),'preserve extension evaluation '+name)
    contexts=json.loads((OLD/'contexts.json').read_text()); cats=pd.read_parquet(OLD/'catalog.parquet'); ids=cats.movie_id.to_numpy()
    label_pin=json.loads((foundation.OUT/'evaluation-seal.json').read_text())['labels']
    require(pin(OLD/'labels.parquet')==label_pin,'same already opened development labels')
    labels=pd.read_parquet(OLD/'labels.parquet'); lookup=labels.set_index(['uid','movie_id']).rating.to_dict()
    saved=np.load(OUT/'predictions.npz'); names=saved['names'].tolist(); values=saved['predictions']
    require(values.shape==(93230,len(names)) and set(CONTENTS)<=set(names),'prediction axes')
    actual=saved['actual_direct']; reference=saved['reference_direct']; rows=[]; agreement=[]
    for c in contexts:
        ei=np.asarray(c['ei'],int); sl=slice(c['start'],c['stop']); truth=np.array([lookup[(c['uid'],int(ids[i]))] for i in ei])
        blocked=cats.blocked.to_numpy()[ei]; count=cats.train_count.to_numpy()[ei]
        masks={'ALL':np.ones(len(ei),bool)}
        if c['cap']==10:
            masks.update({'C':blocked,'W':~blocked&(count>0),'NATURAL_ZERO':~blocked&(count==0),
                          'W_DIRECT':~blocked&(count>0)&actual[sl],
                          'REF_DIRECT_C':blocked&reference[sl],'REF_DIRECT_W':~blocked&(count>0)&reference[sl]})
            for name,lo,hi in [('0',0,1),('1_9',1,10),('10_49',10,50),('50_PLUS',50,np.inf)]:
                masks['SUPPORT_'+name]=(count>=lo)&(count<hi)
        for group,mask in masks.items():
            if not mask.any(): continue
            for j,name in enumerate(names):
                if name=='ACTUAL_ALS' and group!='W_DIRECT': continue
                if name=='REFERENCE_ALS' and group not in ['REF_DIRECT_C','REF_DIRECT_W']: continue
                p=values[sl,j][mask]
                require(np.isfinite(p).all(),'complete common candidate scoring')
                rows.append({'uid':c['uid'],'cap':c['cap'],'h':c['h'],'activity':c['activity'],'variant':name,'group':group,
                             **quality(truth[mask],p,ids[ei[mask]])})
            if group in ['REF_DIRECT_C','REF_DIRECT_W'] and mask.sum()>=2:
                ref=values[sl,names.index('REFERENCE_ALS')][mask]; axis=ids[ei[mask]]; reference_order=np.lexsort((axis,-ref))
                for name in ['S','NO_RESPONSE','GBT']:
                    p=values[sl,names.index(name)][mask]; order=np.lexsort((axis,-p))
                    for n in [2,10]:
                        if len(order)>=n:
                            agreement.append({'uid':c['uid'],'group':group,'variant':name,'n':n,'j':len(order),'overlap':len(set(order[:n])&set(reference_order[:n]))/n})
        for name in [v for v in names if v.startswith('BLEND_')]:
            require(np.array_equal(values[sl,names.index(name)][~actual[sl]],values[sl,names.index('S')][~actual[sl]]),'unsupported rows identical')
    user=pd.DataFrame(rows); user.to_parquet(OUT/'user-metrics.parquet',index=False)
    tables=[summarize(user)]; h10=user[user.cap.eq(10)&user.group.eq('ALL')]
    for name,a in [('H_POSITIVE',h10[h10.h.gt(0)]),('H_ZERO',h10[h10.h.eq(0)])]:
        a=a.copy(); a['group']=name; tables.append(summarize(a))
    for dim in ['h','activity']:
        for value,a in h10.groupby(dim):
            a=a.copy(); a['group']=dim+'='+str(value); tables.append(summarize(a))
    pd.concat(tables,ignore_index=True).to_csv(OUT/'metrics.csv',index=False)
    pd.DataFrame(agreement).to_csv(OUT/'reference-agreement.csv',index=False)
    comparisons=[]; eligible=[]
    for after in CONTENTS[1:]:
        group='C' if after in ['NO_RESPONSE','GBT'] else 'W_DIRECT'
        pair=joined(user,after,group); primary=delta(pair,'ndcg2',.05/6)
        guards={}; passed=True
        for g in (['ALL','C'] if group=='C' else ['ALL']):
            for positive in [True,False]:
                p=joined(user,after,g,positive); key=g+('_H_POSITIVE' if positive else '_H_ZERO')
                guard={name:delta(p,name) for name in ['stars2','low2']}; guards[key]=guard
                passed &= guard['stars2']['mean_delta'] is not None and guard['stars2']['mean_delta']>=-.1 and guard['low2']['mean_delta']<=.03
        improves=primary['interval'][0] is not None and primary['interval'][0]>0
        if improves and passed: eligible.append(after)
        comparisons.append({'before':'S','after':after,'primary_group':group,'primary':primary,'point_guards':guards,'eligible':bool(improves and passed)})
    means=h10[h10.h.gt(0)].groupby('variant').ndcg2.mean().to_dict()
    selected=min(['S']+eligible,key=lambda v:(-means[v],CONTENTS.index(v)))
    write_json(OUT/'selection.json',{'selected':selected,'foundation_selected':lock()['selected'],'eligible':eligible,'all_h_positive_ndcg2':means,
                                   'comparisons':comparisons,'claim':'Exploratory development selection; intervals do not adjust for preceding stage-one selection or training randomness.'})
    meta=pd.read_parquet(ROOT/foundation.config()['metadata']); require(np.array_equal(meta.movie_id,ids),'metadata axis')
    top=pd.read_parquet(OUT/'catalog-top10.parquet'); ix=np.searchsorted(ids,top.movie_id)
    top['rating']=[lookup.get((int(u),int(m)),np.nan) for u,m in top[['uid','movie_id']].itertuples(index=False,name=None)]
    top['one_vote_ten']=(meta.tmdb_vote_count.to_numpy()[ix]==1)&(meta.tmdb_vote_average.to_numpy()[ix]==10)
    full=[]
    for name in ['B']+names:
        for group,total,mask in [('ALL',270,np.ones(len(top),bool)),('H_ZERO',84,top.h.eq(0)),('H_POSITIVE',186,top.h.gt(0))]:
            for n in [2,4,6,10]:
                a=top[top.variant.eq(name)&mask&top['rank'].le(n)]; freq=a.movie_id.value_counts(normalize=True)
                full.append({'variant':name,'group':group,'n':n,'users':total,'returned_users':int(a.uid.nunique()),'slots':len(a),'missing_slots':total*n-len(a),
                             'known':int(a.rating.notna().sum()),'unknown':int(a.rating.isna().sum()),'one_vote_ten_slots':int(a.one_vote_ten.sum()),
                             'unique_movies':len(freq),'exposure_hhi':float((freq**2).sum()),'zero_train_slots':int(a.train_count.eq(0).sum())})
    pd.DataFrame(full).to_csv(OUT/'catalog-diagnostics.csv',index=False)
    seal('evaluation-seal.json',['user-metrics.parquet','metrics.csv','selection.json','reference-agreement.csv','catalog-diagnostics.csv'],
         evaluation_code=evaluation_fingerprint(),fit_seal=pin(OUT/'fit-seal.json'),catalog_seal=pin(OUT/'catalog-seal.json'),labels=label_pin)
    print('EVALUATED',selected,json.dumps(means),flush=True)

if __name__=='__main__':evaluate()
