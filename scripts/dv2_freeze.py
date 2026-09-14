"""Freeze the technical review exemplar and audit mutable catalog behavior."""
from collections import Counter
import copy
import hashlib
import pickle
import numpy as np
import pandas as pd
import sklearn
from threadpoolctl import threadpool_limits
from dv2_common import *
from dv2_runtime import FrozenCatalog,classify,fixed_quality,RULE_KEYS,integer_ids,runtime_versions
from dv2_retrieve import representative_scores

def rule_digest(bundle):
    prep=bundle['preprocessor'];vec=prep['text_vectorizer']
    value={'top':array_hash(bundle['top_centers']),'children':[array_hash(c) for c in bundle['child_centers']],
           'representatives':{k:array_hash(v) for k,v in bundle['representatives'].items()},
           'preprocessor_arrays':{k:array_hash(prep[k]) for k in ['keyword_idf','keyword_components','text_components']},
           'genres':list(prep['genres']),'keywords':list(prep['keywords']),
           'text_vocabulary':sorted((k,int(v)) for k,v in vec.vocabulary_.items()),'text_idf':array_hash(vec.idf_),
           'top_weights':bundle['top_weights'],'sub_weights':bundle['sub_weights'],'content_weights':bundle['content_weights'],
           'names':bundle['top_names'],'group_names':bundle.get('group_names'),
           'text_parameters':{k:str(v) for k,v in vec.get_params(deep=False).items()},'runtime_rules':bundle['runtime_rules'],'quality':bundle['quality'],
           'policy':bundle['policy'],'predictor':bundle['predictor'],'candidate_date':bundle['candidate_date'],'runtime_versions':bundle['runtime_versions'],
           'tie_rule':bundle.get('tie_rule'),'source_space_hashes':bundle['source_space_hashes'],'review_status':bundle.get('review_status')}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True).encode()).hexdigest()

def check_equal(reference,result,indices=None):
    for k in ['taste_id','child_id','group_id','top_supported','content_supported']:
        expected=reference[k] if indices is None else reference[k][indices]
        assert np.array_equal(expected,result[k]),k+' frozen invariant failed'

def freeze():
    verify('prepare');verify('cluster');verify('predictor');verify('sweep');verify('check')
    dest=require_fresh('final','manifest.json');selection=read(OUT/'sweep/decision.json');check=read(OUT/'check/report.json')
    exemplar=check['exemplar']
    if exemplar is None:
        write(dest/'manifest.json',{'status':'NO_POLICY_SELECTED','top8_source':pin(OLDOUT/'final/bundle.pkl'),
                                   'reason':'No comparable retrieval policy; all hierarchy artifacts preserved in cluster','human_value':'UNMEASURED'})
        seal('final');return
    p=selection['policies'][exemplar]
    check_summary=read(OUT/'check/summary.json');exemplar_check=next((s for s in check_summary if s['policy']==exemplar),None)
    predictor_decision=read(OUT/'predictor/decision.json')
    review_status={'exemplar_kind':'selection_candidate' if exemplar==selection['candidate'] else 'best_approximation_tradeoff',
                   'selection_status':selection['status'],'check_gates':exemplar_check['gates'] if exemplar_check else None,
                   'check_feasible':bool(exemplar_check and exemplar_check['feasible']),'predictor_status':predictor_decision['status'],
                   'human_discovery_value':'UNMEASURED'}
    with (OUT/'cluster'/(p['hierarchy']+'-hierarchy.pkl')).open('rb') as f:h=pickle.load(f)
    source=frozen_bundle();frame=pd.read_parquet(OUT/'prepare/catalog.parquet');ids=frame.service_movie_id.to_numpy()
    bundle={'version':CFG['version'],'preprocessor':source['preprocessor'],'top_centers':source['top_centers'],'top_weights':source['top_weights'],
            'top_names':source['top_names'],'child_centers':h['centers'],'sub_weights':h['sub_weights'],'content_weights':CFG['content_weights'],
           'representatives':h['representatives'],'source_space_hashes':h['space_hashes'],'quality':read(OUT/'prepare/quality-config.json'),
            'runtime_versions':runtime_versions(),
            'runtime_rules':{k:CFG[k] for k in RULE_KEYS},'candidate_date':CFG['candidate_date'],'policy':p,
            'predictor':selection['predictor'],'research_decision':selection['status'],'human_value':'UNMEASURED',
            'review_status':review_status,
            'top_source_sha256':pin(OLDOUT/'final/bundle.pkl')['sha256'],'tie_rule':'float64 fixed-axis squared distance; lowest frozen ID exact tie'}
    with (dest/'bundle.pkl').open('wb') as f:pickle.dump(bundle,f,protocol=5)
    diskpin=pin(dest/'bundle.pkl');material=rule_digest(bundle)
    baseline=classify(frame,bundle)
    assert np.array_equal(baseline['taste_id'],frame.taste_id)
    assert np.array_equal(baseline['group_id'],h['groups']) and np.array_equal(baseline['child_id'],h['children'])
    assert pin(OLDOUT/'prepare-seal.json')['sha256']=='963a5bd3a4598c3ccf8fa0dd059e89761951d36755c0c0efb5b1f1105b9d6844'
    assert pin(OLDOUT/'prepare/names.json')==read(OLDOUT/'prepare-seal.json')['files']['prepare/names.json']
    names=read(OLDOUT/'prepare/names.json');group_names={};descriptors=[]
    for g in range(h['n_groups']):
        ix=np.flatnonzero(h['groups']==g);genre=Counter(int(k) for a in frame.genre_ids.iloc[ix] for k in a)
        keywords=Counter(int(k) for a in frame.keyword_ids.iloc[ix] for k in a)
        gs=[names['genres'].get(str(k),str(k)) for k,v in genre.most_common(3)]
        ks=[names['keywords'].get(str(k),str(k)) for k,v in keywords.most_common(5)]
        label=' / '.join(gs) if gs else 'Insufficient genre evidence'
        group_names[str(g)]=label
        descriptors.append({'group_id':g,'taste_id':int(baseline['taste_id'][ix[0]]) if len(ix) else None,'members':len(ix),
                            'content_supported':int(baseline['content_supported'][ix].sum()),'top_supported':int(baseline['top_supported'][ix].sum()),
                            'display_label':label,'genres':gs,'keywords':ks,'label_is_empirical_summary_not_semantic_truth':True})
    bundle['group_names']=group_names
    material=rule_digest(bundle)
    # Names are finalized once; all validation and manifest refer to this final disk version.
    with (dest/'bundle.pkl').open('wb') as f:pickle.dump(bundle,f,protocol=5)
    diskpin=pin(dest/'bundle.pkl')
    assignments=frame[['service_movie_id','tmdb_id','movielens_movie_id','mapping_status','title','original_language','quality_state','raw_vote_average_number','raw_vote_count_number']].copy()
    for k in ['taste_id','child_id','group_id','top_supported','content_supported']:assignments[k]=baseline[k]
    assignments['taste_name']=[bundle['top_names'][str(t)] for t in baseline['taste_id']]
    assignments['group_name']=[group_names[str(g)] for g in baseline['group_id']]
    assignments['Q']=np.load(OUT/'prepare/quality.npy');assignments['version']=CFG['version']
    assignments.to_parquet(dest/'assignments.parquet',index=False);assignments.to_csv(dest/'assignments.csv.gz',index=False,compression='gzip')
    write(dest/'descriptors.json',descriptors)
    # Whole-catalog row permutation, differing batches and a fresh disk load.
    reverse=np.arange(len(frame)-1,-1,-1);check_equal(baseline,classify(frame.iloc[reverse],bundle),reverse)
    with (dest/'bundle.pkl').open('rb') as f:reloaded=pickle.load(f)
    check_equal(baseline,classify(frame,reloaded))
    for start in range(0,len(frame),4093):
        ix=np.arange(start,min(start+4093,len(frame)));check_equal(baseline,classify(frame.iloc[ix],reloaded),ix)
    rng=np.random.default_rng(CFG['seed']);sample=rng.permutation(len(frame))[:1024]
    for start in range(0,len(sample),17):
        ix=sample[start:start+17];check_equal(baseline,classify(frame.iloc[ix],reloaded),ix)
    # Clone, null and OOV append fixtures with fresh IDs are not real catalog claims.
    added=frame.iloc[:3].copy().reset_index(drop=True);new_start=int(ids.max())+1000
    added['service_movie_id']=[new_start,new_start+1,new_start+2]
    added['tmdb_id']=[int(frame.tmdb_id.max())+1000+j for j in range(3)]
    added['mapping_status']='SERVICE_ONLY_CONTENT_BASED';added['movielens_movie_id']=np.nan
    for row in [1,2]:
        for field in ['genre_ids','keyword_ids','director_ids','top5_cast_ids','production_company_ids','collection_ids','production_country_codes','origin_country_codes']:
            added.at[row,field]=[]
        added.at[row,'overview']=''
    added.at[1,'genre_ids']=None;added.at[1,'keyword_ids']=None;added.at[1,'overview']=None
    added.at[2,'genre_ids']=[999999991];added.at[2,'keyword_ids']=[999999993];added.at[2,'overview']='zzzz_v2_oov_9837621_only'
    combined=pd.concat([frame,added],ignore_index=True);combined_result=classify(combined,bundle)
    check_equal(baseline,{k:v[:len(frame)] for k,v in combined_result.items()})
    assert not combined_result['content_supported'][-2:].any(),'null and OOV fixtures must have no trained content support'
    model=FrozenCatalog(frame,bundle);contexts=read(OUT/'prepare/contexts.json');roles=read(OUT/'prepare/roles.json')
    candidates=[c for c in contexts if c['cap']==10 and c['uid'] in roles['verification'] and len(c['history'])==c['original_input_count'] and profile(baseline['content'],c)[1]['state']=='VALID']
    saved=pd.read_parquet(OUT/'check/requests.parquet');acceptance=[]
    served=saved[saved.policy.eq(exemplar)&saved.candidate_count.gt(0)&saved.returned.gt(0)]
    candidates=[c for c in candidates if c['uid'] in set(served.uid)]
    sufficient_serving_evidence=len(candidates)>=3
    for c in candidates[:3]:
        ratings=[{'service_movie_id':int(ids[i]),'stars':float(r)} for i,r in zip(c['history'],c['stars'])]
        result=model.recommend(ratings,ids[c['viewed']].tolist(),10)
        expected=saved[saved.uid.eq(c['uid'])&saved.policy.eq(exemplar)].iloc[0]
        assert [v['service_movie_id'] for v in result['movies']]==ids[np.asarray(expected.ranked,int)].tolist()
        assert result['predicted_movies']==int(expected.candidate_count)>0 and result['predicted_movies']<=p['budget']
        assert result['als_rows']+result['gbt_rows']>0
        np.testing.assert_allclose([v['ranking_score'] for v in result['movies']],np.asarray(expected.ranked_prediction),rtol=1e-12,atol=1e-12)
        acceptance.append({'uid':c['uid'],'returned':len(result['movies']),'predicted_movies':result['predicted_movies'],'state':result['state']})
    before_rules=rule_digest(bundle);model.append(added)
    assert np.array_equal(model.assigned['group_id'][:len(frame)],baseline['group_id']) and rule_digest(bundle)==before_rules
    np.testing.assert_array_equal(model.engine.q[:len(frame)],np.load(OUT/'prepare/quality.npy'))
    remove_ids=[int(i) for i in h['representatives']['medoid_ids'] if int(i)>0][:8]
    if candidates:remove_ids.append(int(ids[candidates[0]['history'][0]]))
    remove_ids=sorted(set(remove_ids));before_active=model.active.copy()
    try:model.remove(remove_ids+[new_start+100000])
    except ValueError:pass
    else:raise AssertionError('invalid removal batch accepted')
    np.testing.assert_array_equal(model.active,before_active)
    model.remove(remove_ids);assert rule_digest(bundle)==before_rules
    assert not set(model.frame.service_movie_id.iloc[model.engine.order])&set(remove_ids)
    keep=~frame.service_movie_id.isin(remove_ids).to_numpy();check_equal(baseline,classify(frame.loc[keep],bundle),np.flatnonzero(keep))
    if candidates:
        c=candidates[0];ratings=[{'service_movie_id':int(ids[i]),'stars':float(r)} for i,r in zip(c['history'],c['stars'])]
        after=model.recommend(ratings,ids[c['viewed']].tolist(),10)
        assert not set(v['service_movie_id'] for v in after['movies'])&set(remove_ids)
        assert after['global_fill']==0 and after['predicted_movies']<=p['budget']
        assert model.recommend(ratings,ids[c['viewed']].tolist(),0)['profile']['state']=='HIDDEN_CAP0'
    assert model.recommend([],[],10)['profile']['state']=='ACTUAL_NO_HISTORY'
    assert pin(dest/'bundle.pkl')==diskpin and rule_digest(bundle)==material
    # Actual personal rank perturbations: distribution only, no forced-change assertion.
    perturbations=[]
    for j,c in enumerate(candidates):
        state=model.engine.state(c,p['quota']);legal=state['legal']
        if not len(legal):continue
        donor=candidates[(j+1)%len(candidates)];other,_=profile(model.assigned['content'],donor)
        base_score,_=representative_scores(bundle['representatives'],state['p'],p['rep'],legal)
        donor_score,_=representative_scores(bundle['representatives'],other,p['rep'],legal)
        reverse_score,_=representative_scores(bundle['representatives'],-state['p'],p['rep'],legal)
        order=legal[rank(base_score[legal],legal)[:5]];swap=legal[rank(donor_score[legal],legal)[:5]];neg=legal[rank(reverse_score[legal],legal)[:5]]
        perturbations.append({'uid':c['uid'],'donor_uid':donor['uid'],'groups':order.tolist(),'swapped_groups':swap.tolist(),'sign_reversed_groups':neg.tolist(),
                              'swap_top1_changed':bool(order[0]!=swap[0]),'reverse_top1_changed':bool(order[0]!=neg[0])})
    write(dest/'profile-perturbations.json',perturbations)
    write(dest/'invariance.json',{'status':'PASS' if sufficient_serving_evidence else 'INSUFFICIENT_SERVING_PARITY_EVIDENCE','all_catalog':len(frame),'top8_unchanged':True,'reverse':len(frame),'reload':len(frame),
                                 'batch4093':len(frame),'batch17_sample':len(sample),'append_existing':len(frame),'null_oov_new':2,
                                 'delete_existing_reclassified':int(keep.sum()),'removed_medoid_or_history_ids':remove_ids,
                                 'representatives_and_preprocessor_material_unchanged':True,'frozen_C_m_unchanged':True,'disk_bundle_unchanged':True,
                                 'pickle_cache_note':'Material rules/arrays and disk hashes checked; sklearn process cache bytes are not a cross-process invariant',
                                 'serving_eligible_check_users':len(candidates),'serving_parity_sufficient':sufficient_serving_evidence,
                                 'actual_serving_request_parity':acceptance})
    write(dest/'manifest.json',{'version':CFG['version'],'bundle':diskpin,'assignments':pin(dest/'assignments.parquet'),'rules_digest':material,
                               'runtime_versions':runtime_versions(),'pandas_version':pd.__version__,
                               'policy':p,'research_decision':selection['status'],'predictor_status':read(OUT/'predictor/decision.json')['status'],
                               'review_status':review_status,'serving_parity_sufficient':sufficient_serving_evidence,
                               'human_discovery_value':'UNMEASURED','top_source_bundle':pin(OLDOUT/'final/bundle.pkl'),
                               'label_source':pin(OLDOUT/'prepare/names.json'),'label_source_seal':pin(OLDOUT/'prepare-seal.json'),
                               'code':{name:pin(ROOT/'scripts'/name) for name in ['dv2_common.py','dv2_runtime.py','dv2_predictor.py','dv2_retrieve.py','dv2_freeze.py']},
                               'source_seals':{stage:pin(OUT/(stage+'-seal.json')) for stage in ['prepare','cluster','predictor','sweep','check']}})
    seal('final');print('FROZEN_FINAL',p['hierarchy'],exemplar,flush=True)

if __name__=='__main__':
    reviewed('final',['dv2_runtime.py','dv2_freeze.py','dv2_predictor.py','dv2_retrieve.py'])
    with Guard('final'),threadpool_limits(limits=CFG['threads']):freeze()
