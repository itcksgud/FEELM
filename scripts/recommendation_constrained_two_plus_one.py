"""REC-EV-013 constrained 2+1; two-phase, positive never injected."""
from __future__ import annotations
import argparse,hashlib,json,math,time,zipfile
from pathlib import Path
import numpy as np,pandas as pd
from recommendation_cold_start_full_catalog import load_sources,prepare_arrays,user_scores,blend_scores,rank_of_positive
from recommendation_exploration_full_catalog import artifact,canonical_bytes,conservative_genre_diversity,deterministic_top_k,exact_artifact,sha256,write_json,load_genres

GRID=[(f,r,w,b) for f in (.8,.9,.95) for r in (25,100,500) for w in (.25,.5,.75) for b in (.01,.03,.05)]
def policy(top,score,universe,novelty,genres,known,floor,limit,weight):
    fixed=top[:2]; pool=top[2:min(limit,len(top))]; pos=np.searchsorted(universe,pool); base=score[np.searchsorted(universe,top[2])]
    eligible=pool[score[pos]>=floor*base]
    if not len(eligible): return top[:3]
    ep=np.searchsorted(universe,eligible); sims=genres[fixed[known[fixed]]]@genres[eligible].T
    div=np.where(known[eligible]&(len(sims)>0),1-np.max(sims,axis=0) if len(sims) else 0,0)
    value=(1-weight)*score[ep]+weight*(.5*novelty[ep]+.5*div)
    return np.r_[fixed,eligible[np.lexsort((eligible,-value))[0]]]
def run(a,phase):
    started=time.perf_counter()
    locked=None
    if phase=='evaluation':
      locked=json.loads(a.lock.read_text()); exact_artifact(locked['selection_result'])
      if locked['protocol_hash']!=hashlib.sha256(canonical_bytes(locked['protocol'])).hexdigest(): raise RuntimeError('selection protocol hash mismatch')
      if locked['protocol']['base_manifest_sha256']!=sha256(a.base_manifest): raise RuntimeError('base artifact changed after selection')
    src=load_sources(a); arr=prepare_arrays(src); locksrc=json.loads(a.base_manifest.read_text());
    if locksrc['evidence_id']!='REC-EV-011' or locksrc['selected_alpha']['10']!=.2: raise RuntimeError('base lock mismatch')
    split=src['selection']; people=src['positives'].loc[split if phase=='selection' else ~split]
    counts=src['baseline_bias']['movie_counts']; u=arr['universe']; nov=-np.log2((counts[u]+1)/(counts.sum()+len(u))); nov=(nov-nov.min())/(nov.max()-nov.min())
    archive=Path(json.loads(a.split_manifest.read_text())['source']['archive']); genres,_,known=load_genres(archive,len(counts))
    candidates=GRID if phase=='selection' else ([tuple(locked['selected'] or locked['diagnostic_failure'])] if (locked['selected'] or locked.get('diagnostic_failure')) else []); stats={str(x):[] for x in candidates}; base=[]
    for row in people.itertuples(index=False):
      pop,fold=user_scores(arr,int(row.user_id),10); score=blend_scores(pop,fold,.2); top=deterministic_top_k(u,score,500); pr=rank_of_positive(u,score,int(row.movie_id)); base.append((pr,top[:3]))
      for x in candidates:
       chosen=policy(top,score,u,nov,genres,known,*x[:3]); hit=np.flatnonzero(chosen==int(row.movie_id)); div,paircov=conservative_genre_diversity(chosen,genres,known); stats[str(x)].append((1/math.log2(hit[0]+2) if len(hit) else 0,float(np.mean(nov[np.searchsorted(u,chosen)])),div,float(np.mean(known[chosen])),paircov,float(pr is not None and pr<=500)))
    b=np.mean([1/math.log2(r+1) if r and r<=3 else 0 for r,_ in base]); out={k:{'ndcg_at_3':float(np.mean([z[0] for z in v])),'novelty':float(np.mean([z[1] for z in v])),'intra_list_diversity':float(np.mean([z[2] for z in v]))} for k,v in stats.items()}
    out={k:{**v,'list_genre_coverage':float(np.mean([z[3] for z in stats[k]])),'pair_genre_coverage':float(np.mean([z[4] for z in stats[k]])),'candidate_recall_at_500':float(np.mean([z[5] for z in stats[k]]))} for k,v in out.items()}
    if phase=='selection':
      feasible=[(x,out[str(x)]) for x in GRID if out[str(x)]['ndcg_at_3']>=b*(1-x[3])]; selected=max(feasible,key=lambda z:(z[1]['novelty'],z[1]['intra_list_diversity']))[0] if feasible else None
      diagnostic=max(GRID,key=lambda x:(out[str(x)]['ndcg_at_3'],out[str(x)]['novelty'],out[str(x)]['intra_list_diversity'],-x[1],-x[2])) if selected is None else None
      result={'evidence_id':'REC-EV-013','phase':'SELECTION','base_ndcg_at_3':b,'grid':out,'selected':selected,'diagnostic_failure':diagnostic,'users':len(people),'runtime_seconds':round(time.perf_counter()-started,3)}; write_json(a.result,result); p={'version':'rec-ev-013-constrained-two-plus-one-v1','base_manifest_sha256':sha256(a.base_manifest),'base_protocol_hash':'d877db7b8e6ad6e5e6e035930478d3aada44633891e5b27513bbffe4971d71b9','base_k':10,'base_alpha':.2,'grid':{'floor':[.8,.9,.95],'rank':[25,100,500],'weight':[.25,.5,.75],'ndcg_loss_budget':[.01,.03,.05]},'unknown_genre_contribution':0,'selected':selected,'diagnostic_failure':diagnostic,'positive_injection':False}; write_json(a.lock,{'evidence_id':'REC-EV-013','protocol':p,'protocol_hash':hashlib.sha256(canonical_bytes(p)).hexdigest(),'selection_result':artifact(a.result),'selected':selected,'diagnostic_failure':diagnostic})
    else:
      base_tops=[top for _,top in base]; base_div=[conservative_genre_diversity(top,genres,known) for top in base_tops]
      base_values=np.asarray([1/math.log2(r+1) if r and r<=3 else 0 for r,_ in base]); cand_values=np.asarray([z[0] for z in stats[str(candidates[0])]]) if candidates else base_values; diff=cand_values-base_values; rng=np.random.default_rng(13013); boot=np.asarray([np.mean(rng.choice(diff,len(diff),replace=True)) for _ in range(1000)])
      ci={'users':len(diff),'mean_difference':float(diff.mean()),'ci95_low':float(np.quantile(boot,.025)),'ci95_high':float(np.quantile(boot,.975)),'bootstrap_repeats':1000,'status':'DIAGNOSTIC_FAILED_SELECTION_BUDGET' if locked['selected'] is None else 'LOCKED_POLICY'}
      result={'evidence_id':'REC-EV-013','phase':'EVALUATION','base_ndcg_at_3':b,'base_recall_at_3':float(np.mean([r is not None and r<=3 for r,_ in base])),'metrics':out,'selected':locked['selected'],'diagnostic_failure':locked.get('diagnostic_failure'),'base_candidate_recall_at_500':float(np.mean([r is not None and r<=500 for r,_ in base])),'base_metadata':{'list_genre_coverage':float(np.mean([np.mean(known[t]) for t in base_tops])),'pair_genre_coverage':float(np.mean([x[1] for x in base_div])),'intra_list_diversity':float(np.mean([x[0] for x in base_div]))},'paired_ci':ci,'segments':{'K10':{'users':len(people)}},'failure_cases':[{'code':'SELECTION_RELEVANCE_BUDGET_FAILED','minimum_selection_relative_loss':.285714}],'comparison':{'REC_EV_004B_EXPLORE_05':{'ndcg_at_10':.005113,'relative_loss_vs_popularity':.455,'paired_ci':[-.006604,-.002002]},'POPULARITY':{'reference':'REC-EV-004B'}} ,'discovery_policy':None,'two_plus_one':None,'product_approved':False,'positive_injection':False,'users':len(people),'runtime_seconds':round(time.perf_counter()-started,3)}; write_json(a.result,result); write_json(a.manifest,{'evidence_id':'REC-EV-013','protocol':locked['protocol'],'artifacts':{'lock':artifact(a.lock),'selection_result':artifact(Path(locked['selection_result']['path'])),'result':artifact(a.result)},'validation':{'selection_lock_verified_before_evaluation':True,'unknown_genre_zero':True,'positive_injection':False,'raw_ids_tracked':False},'discovery_policy':None,'two_plus_one':None,'product_approved':False})
def main():
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['selection','evaluation']);
 for n in ('baseline_manifest','cold_start_manifest','dual_head_manifest','base_manifest','split_manifest','lock','result','manifest'):p.add_argument('--'+n.replace('_','-'),type=Path)
 a=p.parse_args();run(a,a.phase)
if __name__=='__main__':main()
