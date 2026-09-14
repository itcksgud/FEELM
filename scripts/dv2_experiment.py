"""Finite model diagnostics, actual candidate scoring, selection and check panels."""
from __future__ import annotations
import argparse
import gzip
import itertools
import pickle
import time
import numpy as np
import pandas as pd
import sklearn
from threadpoolctl import threadpool_limits
from dv2_common import *
from dv2_predictor import load_assets,ServicePredictor,VARIANTS
from dv2_retrieve import Engine,comparison

REPS=['mean','norm','medoid','multi4']

def dataset(hierarchies=None):
    verify('prepare');verify('cluster')
    start=time.perf_counter();frame=pd.read_parquet(OUT/'prepare/catalog.parquet')
    x=np.load(OUT/'prepare/content.npy');genre=np.load(OUT/'prepare/genre.npy')
    q=np.load(OUT/'prepare/quality.npy');order=np.load(OUT/'prepare/quality-order.npy')
    assets=load_assets(CFG['source_root']);predictor=ServicePredictor(frame,assets)
    hs={}
    names=hierarchies or list(read(OUT/'cluster/report.json')['hierarchies'])
    for name in names:
        with (OUT/'cluster'/(name+'-hierarchy.pkl')).open('rb') as f:hs[name]=pickle.load(f)
    engines={k:Engine(h,x,genre,frame,order,q) for k,h in hs.items()}
    contexts=read(OUT/'prepare/contexts.json');roles=read(OUT/'prepare/roles.json')
    return {'frame':frame,'x':x,'genre':genre,'q':q,'order':order,'predictor':predictor,'hierarchies':hs,
            'engines':engines,'contexts':contexts,'roles':roles,'initialization_seconds':time.perf_counter()-start,'assets':assets}

def pcontext(c):
    return {'cap':c['cap'],'oi':c['history'],'stars':c['stars'],'viewed':c['viewed']}

def predict_rows(predictor,c,candidates,variant):
    # Fixed chunks bound RH feature-memory for expensive full-model references.
    outputs=[];calls=0;alsrows=0;gbtrows=0
    for start in range(0,len(candidates),8192):
        r=predictor.predict(pcontext(c),candidates[start:start+8192],variant)
        outputs.append(r);calls+=1;alsrows+=r['als_rows_computed'];gbtrows+=r['gbt_rows_computed']
    cols=['prediction','rating_clipped','train_count','has_factor','als_available','als_weight','branch']
    result={k:np.concatenate([r[k] for r in outputs]) if outputs else np.array([],dtype=str if k=='branch' else float) for k in cols}
    result.update(model_calls=calls,als_rows=alsrows,gbt_rows=gbtrows,predicted_movies=len(candidates))
    return result

def policy(name,rep='mean',B=100,quota=25,kind='group',**extras):
    p={'hierarchy':name,'rep':rep,'budget':B,'quota':quota,'kind':kind,**extras}
    suffix=':'.join(f'{k}={v}' for k,v in sorted(extras.items()))
    p['id']=f'{name}:{kind}:{rep}:B{B}:Q{quota}'+(':'+suffix if suffix else '')
    return p

def metrics(frame,ranked,score,candidates,c,engine,state):
    eligible=np.zeros(len(frame),bool);eligible[engine.order]=True
    target=np.asarray(c['target'],int);stars=np.asarray(c['ratings'],float);keep=eligible[target]
    truth=dict(zip(target[keep].tolist(),stars[keep].tolist()));positives={i for i,r in truth.items() if r>=4}
    known=[(j,truth[int(i)]) for j,i in enumerate(ranked) if int(i) in truth]
    known_values=[r for j,r in known];seen_top=np.bincount(frame.taste_id.iloc[c['viewed']].to_numpy(int),minlength=8)
    genres=engine.genre>0;seen_genres=genres[c['viewed']].any(axis=0) if c['viewed'] else np.zeros(genres.shape[1],bool)
    seen_keywords={int(k) for a in frame.keyword_ids.iloc[c['viewed']] for k in a}
    seen_collections={int(k) for a in frame.collection_ids.iloc[c['viewed']] for k in a}
    genre_new=[];keyword_overlap=[];collection_repeat=[]
    for i in ranked:
        g=genres[i];genre_new.append(float((g&~seen_genres).sum()/g.sum()) if g.any() else None)
        kw=set(map(int,frame.keyword_ids.iloc[i]));keyword_overlap.append(len(kw&seen_keywords)/len(kw) if kw else None)
        cc=set(map(int,frame.collection_ids.iloc[i]));collection_repeat.append(bool(cc&seen_collections))
    mean=lambda a:float(np.mean([v for v in a if v is not None])) if any(v is not None for v in a) else None
    output={'returned':len(ranked),'observed_slots':len(known),'unknown_slots':len(ranked)-len(known),
            'unknown_share':(len(ranked)-len(known))/len(ranked) if len(ranked) else None,
            'known_mean':mean(known_values),'strong_negative_slots':sum(r<=2 for r in known_values),'eligible_targets':len(truth),'positive_targets':len(positives),
            'future_recall10':len(set(ranked)&positives)/len(positives) if positives else None,
            'future_candidate_recall':len(set(candidates)&positives)/len(positives) if positives else None,
            'fixed8_unseen_slots':int((seen_top[frame.taste_id.iloc[ranked].to_numpy(int)]==0).sum()),
            'genre_new_fraction':mean(genre_new),'keyword_seen_overlap':mean(keyword_overlap),'seen_collection_slots':sum(collection_repeat),
            'top8_histogram':np.bincount(frame.taste_id.iloc[ranked].to_numpy(int),minlength=8).tolist()}
    ideal=np.sort([max(r-3,0) for r in truth.values()])[::-1]
    for k in [2,6,10]:
        gain=np.array([max(truth.get(int(i),0)-3,0) for i in ranked[:k]])
        denom=float(np.sum(ideal[:k]/np.log2(np.arange(min(k,len(ideal)))+2)))
        output['future_ndcg'+str(k)]=float(np.sum(gain/np.log2(np.arange(len(gain))+2))/denom) if positives and denom else None
    return output

def run_one(data,c,p,variant,reference=None,engine_override=None):
    engine=engine_override or data['engines'][p['hierarchy']];predictor=data['predictor'];frame=data['frame']
    wall=time.perf_counter();cpu=time.process_time()
    candidates,info,state=engine.retrieve(c,p)
    retrieval_ms=(time.perf_counter()-wall)*1000
    scores=predict_rows(predictor,c,candidates,variant)
    ordering=rank(scores['prediction'],engine.ids[candidates]) if len(candidates) else np.array([],int)
    take=ordering[:CFG['return_k']];ranked=candidates[take]
    wall_ms=(time.perf_counter()-wall)*1000;cpu_ms=(time.process_time()-cpu)*1000
    assert info['global_fill']==0 and len(np.unique(candidates))==len(candidates)
    if 'full_predictor' not in p['kind']:assert scores['predicted_movies']<=p['budget']
    values=frame.raw_vote_count_number.to_numpy(float)
    result={'uid':c['uid'],'cap':c['cap'],'policy':p['id'],'hierarchy':p['hierarchy'],'rep':p['rep'],'kind':p['kind'],'budget':p['budget'],'quota':p['quota'],
            'predictor':variant,'profile_state':info['profile']['state'],'profile':info['profile'],'latency_ms':wall_ms,'cpu_ms':cpu_ms,'retrieval_ms':retrieval_ms,
            'model_calls':scores['model_calls'],'als_rows':scores['als_rows'],'gbt_rows':scores['gbt_rows'],'candidate_count':len(candidates),
            'ranked':ranked.tolist(),'ranked_prediction':scores['prediction'][take].tolist(),'ranked_train_count':scores['train_count'][take].astype(int).tolist(),
            'ranked_branch':scores['branch'][take].tolist(),'ranked_als_available':scores['als_available'][take].astype(bool).tolist(),
            'ranked_als_weight':scores['als_weight'][take].tolist(),'ranked_tmdb_count':values[ranked].tolist(),
            'ranked_Q':engine.q[ranked].tolist(),'low_tmdb20_slots':int((values[ranked]<=20).sum()),
            'low_ml20_slots':int((scores['train_count'][take]<=20).sum()),'low_tmdb20_top1':int(len(ranked)>0 and values[ranked[0]]<=20),
            'low_ml20_top1':int(len(take)>0 and scores['train_count'][take[0]]<=20),
            'original_als_low20_top1':int(len(take)>0 and scores['als_available'][take[0]] and scores['train_count'][take[0]]<=20),
            'candidate_low_tmdb20':int((values[candidates]<=20).sum()),'candidate_low_ml20':int((scores['train_count']<=20).sum()),
            'ranked_candidate_position':(take+1).tolist(),'ranked_quality_position':[],
            'global_fill':info['global_fill'],'retrieval_state':info['state'],'legal_group_count':len(info['legal_groups']),
            'representative_comparisons':info['representative_comparisons'],'content_comparisons':info['content_comparisons'],
            **metrics(frame,ranked,scores['prediction'][take],candidates,c,engine,state)}
    if len(candidates):
        q_order=np.lexsort((engine.ids[candidates],-values[candidates],-engine.q[candidates]));q_rank=np.empty(len(candidates),int);q_rank[q_order]=np.arange(1,len(candidates)+1)
        result['ranked_quality_position']=q_rank[take].tolist()
        qtop=candidates[q_order[:10]]
        result['Q_order_top10']=qtop.tolist();result['Q_order_low_tmdb20_top1']=int(values[qtop[0]]<=20)
        result['Q_order_low_ml20_top1']=int(predictor.train_count[qtop[0]]<=20)
    else:result.update(Q_order_top10=[],Q_order_low_tmdb20_top1=0,Q_order_low_ml20_top1=0)
    if reference is not None:result.update(comparison(candidates,reference,state['x'],state['p'],p['budget']))
    else:result.update(comparable=False,prefix_overlap=None,content_gap=None,absolute_gap=None,positive_loss=None,candidate_supply=len(candidates)/p['budget'],reference_supply=None)
    detail={'uid':c['uid'],'cap':c['cap'],'policy':p['id'],'predictor':variant,'candidate_indices':candidates.tolist(),'candidate_predictions':scores['prediction'].tolist(),
            'candidate_branches':scores['branch'].tolist(),'candidate_als_weights':scores['als_weight'].tolist(),'retrieval':info}
    return result,detail

def select_predictor():
    parity=read(DOC/'predictor-parity-review.json')
    assert parity['status']=='PASS','actual model prediction parity must pass before model diagnostics'
    for path,expected in parity['files'].items():assert pin(path)==expected,'reviewed prediction parity source drift'
    dest=require_fresh('predictor','decision.json');data=dataset(['v1-fixed16']);frame=data['frame'];predictor=data['predictor']
    eligible=np.zeros(len(frame),bool);eligible[data['order']]=True
    contexts=[c for c in data['contexts'] if c['cap']==10 and c['uid'] in data['roles']['validation']]
    rows=[];requests=[];request_details=[];profile_counts={}
    base=policy('v1-fixed16')
    for c in contexts:
        p,info=profile(data['x'],c);profile_counts[info['state']]=profile_counts.get(info['state'],0)+1
        if info['state']!='VALID':continue
        target=np.asarray(c['target'],int);truth=np.asarray(c['ratings'],float);keep=eligible[target];target=target[keep];truth=truth[keep]
        for variant in VARIANTS:
            r=predict_rows(predictor,c,target,variant)
            for j,i in enumerate(target):
                rows.append({'uid':c['uid'],'index':int(i),'service_movie_id':int(frame.service_movie_id.iloc[i]),'variant':variant,'actual':float(truth[j]),
                             'prediction':float(r['prediction'][j]),'clipped':float(r['rating_clipped'][j]),'squared_error':float((r['rating_clipped'][j]-truth[j])**2),
                             'absolute_error':float(abs(r['rating_clipped'][j]-truth[j])),'train_count':int(r['train_count'][j]),'als_available':bool(r['als_available'][j])})
            a,d=run_one(data,c,base,variant);requests.append(a);request_details.append(d)
    errors=pd.DataFrame(rows);requests_df=pd.DataFrame(requests);summary=[]
    if not rows:
        errors.to_parquet(dest/'observed-errors.parquet',index=False);requests_df.to_parquet(dest/'same-candidate-requests.parquet',index=False)
        write(dest/'candidate-details.json',request_details);write(dest/'summary.json',[])
        write(dest/'decision.json',{'selected':'original','status':'ALTERNATIVE_UNDERDETERMINED_NO_OBSERVED_ROWS','alternatives':[],
                                  'selection_user_profile_states':profile_counts,'initialization_seconds':data['initialization_seconds'],
                                  'model_sources':data['assets'].provenance})
        seal('predictor');return
    for variant in VARIANTS:
        allrows=errors[errors.variant.eq(variant)];low=allrows[allrows.train_count<=20];req=requests_df[requests_df.predictor.eq(variant)]
        summary.append({'variant':variant,'rows':len(allrows),'users':int(allrows.uid.nunique()),'macro_mse':float(allrows.groupby('uid').squared_error.mean().mean()),
                        'micro_mse':float(allrows.squared_error.mean()),'macro_mae':float(allrows.groupby('uid').absolute_error.mean().mean()),
                        'low_rows':len(low),'low_users':int(low.uid.nunique()),'low_macro_mse':float(low.groupby('uid').squared_error.mean().mean()) if len(low) else None,
                        'low_micro_mse':float(low.squared_error.mean()) if len(low) else None,
                        'original_als_low20_top1':int(req.original_als_low20_top1.sum()),'all_low_ml20_top1':int(req.low_ml20_top1.sum()),'low_tmdb20_top1':int(req.low_tmdb20_top1.sum()),
                        'out_of_rating_range':int(((allrows.prediction<.5)|(allrows.prediction>5)).sum())})
    choice=predictor_choice(summary)
    errors.to_parquet(dest/'observed-errors.parquet',index=False);requests_df.to_parquet(dest/'same-candidate-requests.parquet',index=False)
    write(dest/'candidate-details.json',request_details);write(dest/'summary.json',summary)
    write(dest/'decision.json',{**choice,'selection_user_profile_states':profile_counts,
                              'scope':'Repeated-development predictor diagnostics; not discovery satisfaction','initialization_seconds':data['initialization_seconds'],
                              'model_sources':data['assets'].provenance})
    seal('predictor');print('PREDICTOR_SELECTED',choice['selected'],choice['status'],flush=True)

def predictor_choice(summary):
    if not summary:return {'selected':'original','status':'ALTERNATIVE_UNDERDETERMINED_NO_OBSERVED_ROWS','alternatives':[]}
    by={r['variant']:r for r in summary};original=by['original'];decisions=[];chosen='original';status='PREDICTOR_RISK_HOLD'
    sufficient=original['low_rows']>=30 and original['low_users']>=10
    for name in ['shrink100','shrink20','min20']:
        a=by[name];evidence=sufficient and original['original_als_low20_top1']>0
        gates={'global_mse':a['macro_mse']<=1.02*original['macro_mse'],
               'low_mse':bool(sufficient and a['low_macro_mse']<=1.05*original['low_macro_mse']),
               'low_top1_drop':bool(evidence and a['original_als_low20_top1']<=.8*original['original_als_low20_top1'])}
        passed=all(gates.values());decisions.append({'variant':name,'gates':gates,'sufficient_low_error_evidence':sufficient,'sufficient_low_exposure_evidence':evidence,'pass':passed})
        if passed and chosen=='original':chosen=name;status='TECHNICAL_PREDICTOR_ALTERNATIVE'
    if not sufficient or original['original_als_low20_top1']==0:status='ALTERNATIVE_UNDERDETERMINED_KEEP_ORIGINAL'
    return {'selected':chosen,'status':status,'alternatives':decisions}

def policy_summary(rows):
    if not rows:return []
    allrows=pd.DataFrame(rows);valid=allrows[allrows.profile_state.eq('VALID')];summary=[]
    for name,a in valid.groupby('policy',sort=True):
        comp=a[a.comparable];g=CFG['gates']
        returned=a[a.returned>0]
        values={'policy':name,'users':len(a),'comparable_users':len(comp),'full_return_share':float(a.returned.ge(10).mean()),
                'comparable_share':len(comp)/len(a),'mean_candidates':float(a.candidate_count.mean()),'latency_p95_ms':float(a.latency_ms.quantile(.95)),
                'cpu_p95_ms':float(a.cpu_ms.quantile(.95)),'mean_absolute_gap':float(comp.absolute_gap.mean()) if len(comp) else None,
                'mean_signed_gap':float(comp.content_gap.mean()) if len(comp) else None,'mean_prefix_overlap':float(comp.prefix_overlap.mean()) if len(comp) else None,
                'absolute_gap_p95':float(comp.absolute_gap.quantile(.95)) if len(comp) else None,'worst_positive_loss':float(comp.positive_loss.max()) if len(comp) else None,
                'top1_users':len(returned),'tmdb_low20_top1_share':float(returned.low_tmdb20_top1.mean()) if len(returned) else None,
                'ml_low20_top1_share':float(returned.low_ml20_top1.mean()) if len(returned) else None,
                'tmdb_low20_top1_all_valid_share':float(a.low_tmdb20_top1.mean()),'ml_low20_top1_all_valid_share':float(a.low_ml20_top1.mean()),
                'hierarchy':a.hierarchy.iloc[0],'rep':a.rep.iloc[0],'budget':int(a.budget.iloc[0]),'quota':int(a.quota.iloc[0])}
        gates={'full_return':values['full_return_share']>=g['full_return_share'],'comparable':values['comparable_share']>=.95,
               'overlap':bool(len(comp) and values['mean_prefix_overlap']>=g['prefix_overlap']),
               'p95_gap':bool(len(comp) and values['absolute_gap_p95']<=g['gap_p95']),
               'worst_loss':bool(len(comp) and values['worst_positive_loss']<=g['worst_loss']),
               'latency':values['latency_p95_ms']<=g['latency_p95_ms'],'hard_invariants':bool(a.global_fill.eq(0).all() and (a.candidate_count<=a.budget).all())}
        values['gates']=gates;values['feasible']=all(gates.values());summary.append(values)
    return summary

def choose(summary,policies,hierarchies):
    # Intentionally no future rating/ranking metric can enter discovery selection.
    pmap={p['id']:p for p in policies};primary=[a for a in summary if a['policy'] in pmap]
    defined=[a for a in primary if a['mean_absolute_gap'] is not None];feasible=[a for a in defined if a['feasible']]
    rep_order={r:i for i,r in enumerate(REPS)}
    def priority(a):return (a['budget'],hierarchies[a['hierarchy']]['n_groups'],rep_order[a['rep']],a['quota']!=25,a['policy'])
    pareto=[]
    for a in feasible:
        av=np.array([a['mean_candidates'],a['latency_p95_ms'],a['mean_absolute_gap']])
        if not any(np.all(np.array([b['mean_candidates'],b['latency_p95_ms'],b['mean_absolute_gap']])<=av) and np.any(np.array([b['mean_candidates'],b['latency_p95_ms'],b['mean_absolute_gap']])<av) for b in feasible if b is not a):pareto.append(a)
    candidate=min(pareto,key=priority)['policy'] if pareto else None
    per_k={}
    for name in hierarchies:
        pool=[a for a in defined if a['hierarchy']==name];good=[a for a in pool if a['feasible']];pool=good or pool
        if pool:per_k[name]=min(pool,key=lambda a:(a['mean_absolute_gap'],a['budget'],rep_order[a['rep']],a['quota']!=25,a['policy']))['policy']
    best=min(defined,key=lambda a:(a['mean_absolute_gap'],*priority(a)))['policy'] if defined else None
    return {'status':'TECHNICAL_REVIEW_CANDIDATE' if candidate else 'NO_FEASIBLE_POLICY_HOLD','candidate':candidate,'best_approximation':best,
            'per_hierarchy':per_k,'pareto':[a['policy'] for a in sorted(pareto,key=priority)],'policies':pmap,
            'scope':'Engineering criteria only; human discovery satisfaction unmeasured'}

def sweep():
    verify('predictor');dest=require_fresh('sweep','decision.json');data=dataset();variant=read(OUT/'predictor/decision.json')['selected']
    policies=[policy(name,rep,B,Q) for name,rep,B,Q in itertools.product(data['hierarchies'],REPS,CFG['budgets'],CFG['quotas'])]
    controls=[policy('v1-fixed16',space='G'),policy('v1-fixed16',profile_mode='two_branch'),policy('v1-fixed16','qmean')]
    contexts=[c for c in data['contexts'] if c['cap']==10 and c['uid'] in data['roles']['validation']]
    rows=[]
    (dest/'details').mkdir(parents=True,exist_ok=True)
    warm=next((c for c in contexts if profile(data['x'],c)[1]['state']=='VALID'),None)
    if warm is not None:
        for name in data['engines']:run_one(data,warm,policy(name),variant)
    for j,c in enumerate(contexts):
        refs={};details=[]
        for name in data['engines']:
            for B,Q in itertools.product(CFG['budgets'],CFG['quotas']):
                rp=policy(name,B=B,quota=Q,kind='direct_prefix')
                refs[name,B,Q]=data['engines'][name].retrieve(c,rp)[0]
        rotated=policies[j%len(policies):]+policies[:j%len(policies)]+controls
        for p in rotated:
            ref=refs[p['hierarchy'],p['budget'],p['quota']]
            if p.get('space')=='G' or p.get('profile_mode')=='two_branch':
                rp={**p,'kind':'direct_prefix'};ref=data['engines'][p['hierarchy']].retrieve(c,rp)[0]
            a,d=run_one(data,c,p,variant,ref);rows.append(a);details.append(d)
        with gzip.open(dest/'details'/f"{c['uid']}.json.gz",'wt',encoding='utf-8') as f:json.dump(details,f,ensure_ascii=False,allow_nan=False)
        if (j+1)%10==0:print('SWEEP_USER',j+1,'/',len(contexts),flush=True)
    summary=policy_summary(rows);decision=choose(summary,policies,data['hierarchies'])
    pd.DataFrame(rows).to_parquet(dest/'requests.parquet',index=False);write(dest/'summary.json',summary)
    decision.update(predictor=variant,controls=controls,initialization_seconds=data['initialization_seconds'])
    write(dest/'decision.json',decision);seal('sweep');print('DISCOVERY_SELECTED',decision['status'],decision['candidate'],flush=True)

def check():
    verify('predictor');verify('sweep');dest=require_fresh('check','report.json');data=dataset()
    decision=read(OUT/'sweep/decision.json');variant=decision['predictor']
    chosen_ids=set(decision['per_hierarchy'].values())
    chosen_ids.update(i for i in [decision['candidate'],decision['best_approximation']] if i)
    chosen=[decision['policies'][i] for i in sorted(chosen_ids)]
    if not chosen:
        write(dest/'summary.json',[])
        write(dest/'report.json',{'status':'NO_COMPARABLE_POLICY_HOLD','finalist_policies':[],'exemplar':None,'requests':0,'sensitivity_requests':0,
                                 'check_did_not_reselect':True,'scope':'No defined selection-user approximation comparison; no arbitrary check finalist.'})
        seal('check');return
    chosen.extend(decision['controls'])
    exemplar=decision['candidate'] or decision['best_approximation']
    exemplar_policy=decision['policies'][exemplar]
    contexts=[c for c in data['contexts'] if c['cap']==10 and c['uid'] in data['roles']['verification']]
    rows=[];references=[];drift=[]
    observed_errors=[]
    (dest/'details').mkdir(parents=True,exist_ok=True)
    warm=next((c for c in contexts if profile(data['x'],c)[1]['state']=='VALID'),None)
    if warm is not None:
        for p in chosen:run_one(data,warm,p,variant)
    for j,c in enumerate(contexts):
        details=[];completed_controls={}
        if profile(data['x'],c)[1]['state']=='VALID':
            eligible=np.zeros(len(data['frame']),bool);eligible[data['order']]=True
            target=np.asarray(c['target'],int);actual=np.asarray(c['ratings'],float);keep=eligible[target];target=target[keep];actual=actual[keep]
            for model in sorted(set(['original',variant])):
                predictions=predict_rows(data['predictor'],c,target,model)
                for t,i in enumerate(target):
                    movie=data['frame'].iloc[int(i)];pred=float(predictions['rating_clipped'][t]);err=pred-float(actual[t])
                    observed_errors.append({'uid':c['uid'],'service_movie_id':int(movie.service_movie_id),'index':int(i),'variant':model,
                                            'actual':float(actual[t]),'prediction':float(predictions['prediction'][t]),'clipped':pred,
                                            'squared_error':err*err,'absolute_error':abs(err),'train_count':int(predictions['train_count'][t]),
                                            'als_available':bool(predictions['als_available'][t]),'input_count':len(c['history']),
                                            'supported_als_inputs':int(data['predictor'].has_factor[c['history']].sum()),
                                            'language':movie.original_language,'release_year':None if pd.isna(movie.release_year) else int(movie.release_year),
                                            'production_KR':'KR' in movie.production_country_codes})
        rotated=chosen[j%len(chosen):]+chosen[:j%len(chosen)]
        for p in rotated:
            engine=data['engines'][p['hierarchy']]
            extras={k:p[k] for k in ['space','profile_mode'] if k in p}
            rp=policy(p['hierarchy'],B=p['budget'],quota=p['quota'],kind='direct_prefix',**extras)
            ref,ref_info,state=engine.retrieve(c,rp)
            a,d=run_one(data,c,p,variant,ref);rows.append(a);details.append(d)
            # Inspect all legal actual prefixes, independently of the chosen groups.
            for g in state['legal']:
                actual=state['pools'][g];fixed=engine.lists[g][:p['quota']]
                whole=engine.h['representatives']['genre_mean' if p.get('space')=='G' else 'mean'][g]
                actual_mean=state['x'][actual].mean(axis=0)
                fixed_mean=state['x'][fixed].mean(axis=0) if len(fixed) else np.zeros_like(whole)
                drift.append({'uid':c['uid'],'hierarchy':p['hierarchy'],'policy':p['id'],'profile_state':state['profile']['state'],'group_id':int(g),'actual_prefix_count':len(actual),'fixed_prefix_count':len(fixed),
                              'viewed_count':int(state['counts'][g]),'whole_to_actual_distance':float(np.linalg.norm(whole-actual_mean)),
                              'whole_to_fixed_distance':float(np.linalg.norm(whole-fixed_mean)),
                              'actual_mean_similarity':float(actual_mean@state['p']),'whole_mean_similarity':float(whole@state['p'])})
            # References are actual request executions; full predictions are never sliced as serving time.
            if a['profile_state']=='VALID':
                kinds=['direct_prefix','all_content','flat_q','full_predictor','global_flat_q','global_full_predictor']
                control_policies=[policy(p['hierarchy'],B=p['budget'],quota=p['quota'],kind=kind,**extras) for kind in kinds]
                control_policies.extend(policy(p['hierarchy'],B=p['budget'],quota=p['quota'],kind='random',seed=seed,**extras) for seed in CFG['random_seeds'])
                for cp in control_policies:
                    # Exact same global references need only one actual call per user/B; keep their real measured cost.
                    if cp['kind']=='global_full_predictor':cachekey=(cp['kind'],)
                    elif cp['kind']=='full_predictor':cachekey=(cp['kind'],cp['hierarchy'])
                    elif cp['kind'].startswith('global_'):cachekey=(cp['kind'],cp['budget'])
                    else:cachekey=(cp['id'],)
                    if cachekey in completed_controls:
                        original=completed_controls[cachekey]
                        references.append({'uid':c['uid'],'policy':p['id'],'reference_policy':original['policy'],'reference_reused_for_comparison':True})
                        continue
                    rr,rd=run_one(data,c,cp,variant,ref if cp['kind'] not in ['full_predictor','global_full_predictor'] else None)
                    rows.append(rr);details.append(rd);completed_controls[cachekey]=rr
                    references.append({'uid':c['uid'],'policy':p['id'],'reference_policy':rr['policy'],'reference_reused_for_comparison':False})
        with gzip.open(dest/'details'/f"{c['uid']}.json.gz",'wt',encoding='utf-8') as f:json.dump(details,f,ensure_ascii=False,allow_nan=False)
        if (j+1)%10==0:print('CHECK_USER',j+1,'/',len(contexts),flush=True)
    pd.DataFrame(rows).to_parquet(dest/'requests.parquet',index=False)
    pd.DataFrame(observed_errors).to_parquet(dest/'observed-errors.parquet',index=False)
    pd.DataFrame(drift).to_parquet(dest/'prefix-drift.parquet',index=False);write(dest/'reference-links.json',references)
    # Finite sensitivity only; no check-user reselection.
    sensitivities=[];base=eligibility(data['frame'],data['x']);h=data['hierarchies'][exemplar_policy['hierarchy']]
    options=[]
    for kind in ['movie_mean','vote_mean']:
        for m in CFG['quality_m_sensitivity']:
            if kind==CFG['quality_C'] and m==CFG['quality_m']:continue
            q,order,info=quality(data['frame'],base,kind,m)
            options.append((f'Q-{kind}-m{m}',Engine(h,data['x'],data['genre'],data['frame'],order,q),info))
    order=data['order'][data['frame'].raw_vote_count_number.iloc[data['order']].to_numpy()>=CFG['minimum_tmdb_sensitivity']]
    options.append(('TMDB_MIN21',Engine(h,data['x'],data['genre'],data['frame'],order,data['q']),{'min_votes':21}))
    legal=data['order'];ids=data['frame'].service_movie_id.to_numpy();v=data['frame'].raw_vote_count_number.to_numpy(float);r=data['frame'].raw_vote_average_number.to_numpy(float)
    raw_order=legal[np.lexsort((ids[legal],-v[legal],-r[legal]))]
    count_order=legal[np.lexsort((ids[legal],-r[legal],-v[legal]))]
    options.append(('RAW_R_ONLY',Engine(h,data['x'],data['genre'],data['frame'],raw_order,data['q']),{'order':'raw R desc, v desc, service ID asc; Q still reported separately'}))
    options.append(('TMDB_COUNT_ONLY',Engine(h,data['x'],data['genre'],data['frame'],count_order,data['q']),{'order':'v desc, raw R desc, service ID asc; historical diagnostic only'}))
    sensitivity_details=[]
    for c in contexts:
        user_details=[]
        for label,engine,info in options:
            p={**exemplar_policy,'id':exemplar_policy['id']+':'+label}
            rp={**p,'kind':'direct_prefix'};ref=engine.retrieve(c,rp)[0]
            a,d=run_one(data,c,p,variant,ref,engine);a['sensitivity']=label;sensitivities.append(a);user_details.append(d)
        with gzip.open(dest/'details'/f"{c['uid']}-sensitivity.json.gz",'wt',encoding='utf-8') as f:json.dump(user_details,f,ensure_ascii=False,allow_nan=False)
    for cap in CFG['diagnostic_caps']:
        for c in (c for c in data['contexts'] if c['cap']==cap and c['uid'] in data['roles']['verification']):
            p={**exemplar_policy,'id':exemplar_policy['id']+f':CAP{cap}'}
            ref=data['engines'][p['hierarchy']].retrieve(c,{**p,'kind':'direct_prefix'})[0]
            a,d=run_one(data,c,p,variant,ref);a['sensitivity']=f'CAP{cap}';sensitivities.append(a)
            with gzip.open(dest/'details'/f"{c['uid']}-cap{cap}.json.gz",'wt',encoding='utf-8') as f:json.dump([d],f,ensure_ascii=False,allow_nan=False)
    pd.DataFrame(sensitivities).to_parquet(dest/'sensitivity-requests.parquet',index=False)
    write(dest/'sensitivity-configs.json',[{'label':label,**info} for label,engine,info in options])
    group_rows=[a for a in rows if a['kind']=='group'];summary=policy_summary(group_rows)
    write(dest/'summary.json',summary)
    write(dest/'report.json',{'finalist_policies':chosen,'exemplar':exemplar,'requests':len(rows),'sensitivity_requests':len(sensitivities),'users':len(contexts),
                              'observed_prediction_rows':len(observed_errors),'observed_predictors':sorted(set(['original',variant])),
                              'main_valid_users':sum(profile(data['x'],c)[1]['state']=='VALID' for c in contexts),'initialization_seconds':data['initialization_seconds'],
                              'check_did_not_reselect':True,'scope':'Repeated development technical check. Future ML metrics descriptive, UNKNOWN preserved.'})
    seal('check');print('CHECK_COMPLETE',len(rows),len(sensitivities),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['predictor','sweep','check']);args=parser.parse_args()
    reviewed(args.stage,['dv2_experiment.py','dv2_predictor.py','dv2_retrieve.py'])
    with Guard(args.stage),threadpool_limits(limits=CFG['threads']):
        {'predictor':select_predictor,'sweep':sweep,'check':check}[args.stage]()
