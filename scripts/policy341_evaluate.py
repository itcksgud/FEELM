"""Fallback-inclusive set quality and a separate full-catalog UNKNOWN audit."""
import json
import numpy as np
import pandas as pd
from policy341_run import ROOT,DOC,OUT,OLD,verify,combination
from rec046_common import require,pin,write_json
from text339_evaluate import paired_interval

def evaluation_fingerprint(): return {'policy341_evaluate.py':pin(__file__)}

def statistics(d):
    d=np.asarray(d,float); d=d[np.isfinite(d)]
    return {'users':len(d),'mean_delta':float(d.mean()) if len(d) else None,'interval':paired_interval(d,.05/7,341),'alpha':.05/7}

def compare(user,after_policy,metric,before_model,before_policy='P0',positive=True):
    a=user[(user.h.gt(0) if positive else user.h.eq(0))&user.j.ge(3)]
    before=a[a.model.eq(before_model)&a.policy.eq(before_policy)].set_index('uid')[metric]
    after=a[a.model.eq('SELECTED')&a.policy.eq(after_policy)].set_index('uid')[metric]
    pair=pd.concat([before.rename('before'),after.rename('after')],axis=1).sort_index()
    require(pair.notna().all().all(),'complete common policy comparison')
    return (pair.after-pair.before).to_numpy()

def evaluate():
    prediction=verify()
    review=json.loads((DOC/'evaluation-code-review.json').read_text())
    require(review['status']=='PASS' and review['fingerprint']==evaluation_fingerprint(),'independent policy evaluator review')
    review=json.loads((DOC/'prediction-review.json').read_text())
    require(review['status']=='PASS' and review['prediction_seal']==pin(OUT/'prediction-seal.json'),'independent policy output review')
    files=['user-metrics.parquet','metrics.csv','decision.json','catalog-diagnostics.csv','evaluation-seal.json']
    for name in files: require(not (OUT/name).exists(),'preserve policy evaluation '+name)
    source_record=json.loads((combination.foundation.OUT/'evaluation-seal.json').read_text())
    require(pin(OLD/'labels.parquet')==source_record['labels'],'approved label identity before decoding')
    labels=pd.read_parquet(OLD/'labels.parquet'); lookup=labels.set_index(['uid','movie_id']).rating.to_dict()
    cats=pd.read_parquet(OLD/'catalog.parquet'); ids=cats.movie_id.to_numpy()
    contexts={c['uid']:c for c in json.loads((OLD/'contexts.json').read_text()) if c['cap']==10}
    rankings=pd.read_parquet(OUT/'rankings.parquet'); blocks=pd.read_parquet(OUT/'blocks.parquet')
    rankings['rating']=[lookup.get((int(u),int(m)),np.nan) for u,m in rankings[['uid','movie_id']].itertuples(index=False,name=None)]
    observed=rankings[rankings.pool.eq('OBSERVED')]
    require(observed.rating.notna().all(),'all observed candidate recommendations have actual labels')
    rows=[]
    for (uid,model,policy),a in observed.groupby(['uid','model','policy'],sort=True):
        a=a.sort_values('rank'); c=contexts[uid]; truth=np.array([lookup[(uid,int(ids[i]))] for i in c['ei']])
        value={'uid':uid,'h':c['h'],'j':len(truth),'model':model,'policy':policy}
        first_block=blocks[blocks.pool.eq('OBSERVED')&blocks.uid.eq(uid)&blocks.model.eq(model)&blocks.policy.eq(policy)&blocks.block.eq(0)]
        value['first_discovery']=bool(first_block.discovered.iloc[0]) if len(first_block) else False
        value['first_fallback']=bool(first_block.fallback.iloc[0]) if len(first_block) else False
        if value['first_discovery']:
            first_slot=a[a['rank'].eq(3)].iloc[0]
            value['first_slot_discovery_stars']=float(first_slot.rating)
            value['first_slot_discovery_low']=float(first_slot.rating<=2)
            value['first_slot_discovery_novelty']=float(first_slot.novelty)
        for n in [3,6,9]:
            if len(truth)<n: continue
            top=a.head(n); rating=top.rating.to_numpy(); gain=(rating-.5)/4.5
            ideal=np.sort((truth-.5)/4.5)[::-1][:n]@(1/np.log2(np.arange(n)+2))
            value['stars'+str(n)]=float(rating.mean()); value['low'+str(n)]=float((rating<=2).mean())
            value['good'+str(n)]=float((rating>=4).mean())
            value['ndcg'+str(n)]=float(gain@(1/np.log2(np.arange(n)+2))/ideal) if ideal>0 else np.nan
            value['novelty'+str(n)]=float(top.novelty.mean()) if top.novelty.notna().all() else np.nan
        fixed_taste=a[((a['rank']-1)%3)<2]
        for n in [2,4,6]:
            if len(fixed_taste)>=n:
                t=fixed_taste.head(n).rating.to_numpy(); value['taste_stars'+str(n)]=float(t.mean()); value['taste_low'+str(n)]=float((t<=2).mean())
        discoveries=a[a.role.eq('DISCOVERY')]
        value['actual_discoveries']=len(discoveries)
        for n in [1,2,3]:
            if len(discoveries)>=n:
                t=discoveries.head(n); value['discovery_stars'+str(n)]=float(t.rating.mean())
                value['discovery_low'+str(n)]=float(t.rating.le(2).mean()); value['discovery_novelty'+str(n)]=float(t.novelty.mean())
        rows.append(value)
    user=pd.DataFrame(rows); user.to_parquet(OUT/'user-metrics.parquet',index=False)
    require(user[user.model.eq('B')&user.policy.eq('P0')&user.h.gt(0)&user.j.ge(3)].uid.nunique()==158,'fixed 158-user policy cohort')
    ignore={'uid','h','j','model','policy'}; metric_names=[name for name in user.columns if name not in ignore]; summaries=[]
    for (model,policy),a in user.groupby(['model','policy']):
        for group,b in [('ALL',a),('H_POSITIVE',a[a.h.gt(0)]),('H_ZERO',a[a.h.eq(0)])]:
            for name in metric_names:
                # Discovery/fallback rates are defined on users with a complete first set.
                v=(b[b.j.ge(3)] if name in ['first_discovery','first_fallback'] else b)[name].dropna()
                summaries.append({'model':model,'policy':policy,'group':group,'metric':name,'users_total':len(b),'users_valid':len(v),
                                  'mean':float(v.mean()) if len(v) else None,'status':'DESCRIPTIVE_SMALL_N' if len(v)<30 else 'DESCRIPTIVE'})
    pd.DataFrame(summaries).to_csv(OUT/'metrics.csv',index=False)
    first=pd.read_parquet(combination.foundation.OUT/'user-metrics.parquet')
    next_stage=pd.read_parquet(combination.OUT/'user-metrics.parquet'); choice=prediction['selected']
    f=first[first.cap.eq(10)&first.h.gt(0)&first.group.eq('ALL')&first.variant.eq('B')].set_index('uid').ndcg2
    s=next_stage[next_stage.cap.eq(10)&next_stage.h.gt(0)&next_stage.group.eq('ALL')&next_stage.variant.eq(choice)].set_index('uid').ndcg2
    pair=pd.concat([f.rename('before'),s.rename('after')],axis=1).sort_index().dropna(); require(len(pair)==176,'fixed 176-user model check')
    model_check=statistics((pair.after-pair.before).to_numpy()); policies=[]; eligible=[]
    for policy in ['P2_010','P2_020']:
        novelty=statistics(compare(user,policy,'novelty3','SELECTED'))
        stars=statistics(compare(user,policy,'stars3','B'))
        low=statistics(compare(user,policy,'low3','B'))
        require(novelty['users']==stars['users']==low['users']==158,'same policy bootstrap population')
        h0_stars=compare(user,policy,'stars3','B',positive=False); h0_low=compare(user,policy,'low3','B',positive=False)
        require(len(h0_stars)==len(h0_low)==84,'fixed no-history guard cohort')
        h0pass=h0_stars.mean()>=-.1 and h0_low.mean()<=.03
        passed=novelty['interval'][0] is not None and novelty['interval'][0]>0 and stars['interval'][0]>=-.1 and low['interval'][1]<=.03 and h0pass
        if passed: eligible.append(policy)
        policies.append({'policy':policy,'novelty_vs_own_P0':novelty,'stars_vs_B_P0':stars,'low_vs_B_P0':low,
                         'h0_point_guard':{'stars_delta':float(h0_stars.mean()),'low_delta':float(h0_low.mean()),'pass':bool(h0pass)},
                         'own_P0_stars_delta_descriptive':float(compare(user,policy,'stars3','SELECTED').mean()),
                         'own_P0_low_delta_descriptive':float(compare(user,policy,'low3','SELECTED').mean()),'eligible':bool(passed)})
    selected=eligible[0] if eligible else 'P0'
    write_json(OUT/'decision.json',{'selected_model':choice,'selected_policy':selected,'eligible_policies':eligible,'model_check':model_check,'policies':policies,
                                   'claim':'Development policy recommendation only; UNKNOWN and current Korean service usefulness are not validated.'})
    full=[]; catalog=rankings[rankings.pool.eq('CATALOG')]
    for (model,policy),a in catalog.groupby(['model','policy']):
        for group,total,b in [('ALL',270,a),('H_POSITIVE',186,a[a.h.gt(0)]),('H_ZERO',84,a[a.h.eq(0)])]:
            for n in [3,6,9]:
                t=b[b['rank'].le(n)]; freq=t.movie_id.value_counts(normalize=True)
                first_blocks=blocks[blocks.pool.eq('CATALOG')&blocks.model.eq(model)&blocks.policy.eq(policy)&blocks.block.lt(n//3)]
                if group=='H_POSITIVE': first_blocks=first_blocks[first_blocks.h.gt(0)]
                if group=='H_ZERO': first_blocks=first_blocks[first_blocks.h.eq(0)]
                full.append({'model':model,'policy':policy,'group':group,'n':n,'users':total,'slots':len(t),'missing_slots':total*n-len(t),
                             'known':int(t.rating.notna().sum()),'unknown':int(t.rating.isna().sum()),'known_only_stars':float(t.rating.mean()) if t.rating.notna().any() else None,
                             'discovery_slots':int(t.role.eq('DISCOVERY').sum()),'fallback_blocks':int(first_blocks.fallback.sum()),'total_blocks':len(first_blocks),
                             'unique_movies':len(freq),'exposure_hhi':float((freq**2).sum()),'zero_train_slots':int(t.train_count.eq(0).sum()),
                             'novelty_mean':float(t.novelty.mean()) if t.novelty.notna().any() else None,'novelty_slots':int(t.novelty.notna().sum())})
    pd.DataFrame(full).to_csv(OUT/'catalog-diagnostics.csv',index=False)
    write_json(OUT/'evaluation-seal.json',{'evaluation_code':evaluation_fingerprint(),'prediction_seal':pin(OUT/'prediction-seal.json'),'labels':source_record['labels'],
                                         'files':{name:pin(OUT/name) for name in files if name!='evaluation-seal.json'}})
    print('POLICY_EVALUATED',choice,selected,flush=True)

if __name__=='__main__':evaluate()
