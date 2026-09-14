"""Independent read-only reconstruction of frozen discovery v2 stage results.

No imports from the experiment's decision/retrieval implementation, no trained
model calls, and no result reads without --execute. Output is JSON on stdout.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import itertools
import json
from pathlib import Path
import pickle
import time

import numpy as np
import pandas as pd
import sklearn  # Load numerical runtimes before applying threadpool limits.
from threadpoolctl import threadpool_limits

SOURCE = Path('C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-v2-20260913')
CONTRACT = 'docs/recommendation/experiments/fixed-k8-discovery-v2/'
PINS = {
    CONTRACT+'config.json': '20e624a77ac73c2334eb1730f9df8cee3bfbc305d592b07000b0dd178b509103',
    CONTRACT+'EXECUTION.md': '4f7e4aebf28701d01d77e33457e4b2df9a541e46eef8e610fe7f124ae5c1d93f',
    'scripts/dv2_common.py': '6a45cc381aca7053a9e098c59a859c87a09825c866d1430cbc9e31e81394ba7a',
    'scripts/dv2_cluster.py': 'ef1965108a39c28e04480d9fe99fd3f223026655c59d554f4b5bbf55844ac150',
    'scripts/dv2_retrieve.py': '5601266ffb37ecad46aa92f21c861c537c46002569b11eeb3de0b34b4a8e3465',
    'scripts/dv2_experiment.py': '20ce383c1d94b85008e0f8b1f0417fb99bfc6a57a0e0a2bc878f173c4fabffac',
    'scripts/dv2_predictor.py': 'db3c1c92278fdfc889e8e951c1aa53495c631772dcf2f07bc6644d545ecc4086',
}
HIERARCHIES = ['v1-fixed16'] + ['GKT-K'+str(k) for k in [2,8,32,64,128,256]]
REPS = ['mean','norm','medoid','multi4']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def pin(path):
    path = Path(path); digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2**20), b''):
            digest.update(block)
    return {'sha256':digest.hexdigest(), 'bytes':path.stat().st_size}


def exact(actual, expected, label=''):
    if isinstance(expected, (list,tuple,np.ndarray)):
        assert np.array_equal(np.asarray(actual),np.asarray(expected)), label
    else:
        assert actual == expected, (label,actual,expected)


def number(actual, expected, label=''):
    if expected is None:
        assert actual is None or pd.isna(actual), (label,actual)
    else:
        assert np.isfinite(actual) and np.isfinite(expected), (label,actual,expected)
        assert np.isclose(actual,expected,rtol=1e-10,atol=1e-12), (label,actual,expected)


def descending(values, ids):
    values=np.asarray(values,float); assert np.isfinite(values).all()
    return np.lexsort((np.asarray(ids),-values))


def make_policy(hierarchy, rep='mean', budget=100, quota=25, kind='group', **extras):
    p=dict(hierarchy=hierarchy,rep=rep,budget=budget,quota=quota,kind=kind,**extras)
    suffix=':'.join(str(k)+'='+str(v) for k,v in sorted(extras.items()))
    p['id']=f'{hierarchy}:{kind}:{rep}:B{budget}:Q{quota}'+(':'+suffix if suffix else '')
    return p


def parse_policy(name):
    h,kind,rep,b,q,*extras=name.split(':')
    assert h in HIERARCHIES and b.startswith('B') and q.startswith('Q')
    p=dict(hierarchy=h,kind=kind,rep=rep,budget=int(b[1:]),quota=int(q[1:]),id=name)
    for token in extras:
        if '=' in token:
            key,value=token.split('=',1); assert key in ['seed','space','profile_mode']
            p[key]=int(value) if key=='seed' else value
        else:
            assert '_diagnostic' not in p; p['_diagnostic']=token
    return p


def independent_profile(x, c, mode='signed'):
    history=np.asarray(c['history'],int); stars=np.asarray(c['stars'],float)
    original=np.asarray(c['original_stars'],float)
    assert len(history)==len(stars) and len(history)<=len(original)<=c['cap']
    assert len(set(history))==len(history) and np.isin(stars,np.arange(.5,5.1,.5)).all()
    supported=(x[history]*x[history]).sum(axis=1)>1e-12
    if c['raw_pre_count']==0: state='ACTUAL_NO_HISTORY'
    elif c['cap']==0: state='HIDDEN_CAP0'
    elif not len(original): state='NO_SELECTED_INPUT'
    elif not len(history): state='UNMAPPED_INPUT'
    elif not supported.any(): state='UNSUPPORTED_INPUT'
    else: state='VALID'
    anchor=3+.5*((original.sum()+5*3.5)/(len(original)+5)-3)
    weights=(stars-anchor)[supported]; vectors=x[history[supported]]
    abs_weight=float(np.abs(weights).sum()); squared=float((weights*weights).sum())
    effective=abs_weight**2/squared if squared else 0.
    if mode=='signed':
        raw=(vectors*weights[:,None]).sum(axis=0)/(abs_weight+5)
    else:
        assert mode=='two_branch'
        pos=weights>0; neg=weights<0
        positive=(vectors[pos]*weights[pos,None]).sum(axis=0)/weights[pos].sum() if pos.any() else np.zeros(x.shape[1])
        negative=(vectors[neg]*(-weights[neg,None])).sum(axis=0)/(-weights[neg]).sum() if neg.any() else np.zeros(x.shape[1])
        raw=positive-negative
    length=float(np.linalg.norm(raw))
    if state=='VALID' and length<=1e-8: state='CANCELLED_OR_TINY'
    direction=raw/length if state=='VALID' else np.zeros(x.shape[1])
    return direction,dict(state=state,original_inputs=len(original),mapped_inputs=len(history),
        supported_inputs=int(supported.sum()),anchor=float(anchor),raw_norm=length,effective_n=effective,
        confidence=effective/(effective+5),positive_inputs=int((weights>0).sum()),negative_inputs=int((weights<0).sum()),mode=mode)


def visit(order, pools, budget):
    candidates=[]; groups=[]; group_ranks=[]; within=[]; visited=[]
    for rank,g in enumerate(order,1):
        g=int(g); visited.append(g); rows=pools[g][:budget-len(candidates)]
        candidates.extend(map(int,rows)); groups.extend([g]*len(rows))
        group_ranks.extend([rank]*len(rows)); within.extend(range(1,len(rows)+1))
        if len(candidates)>=budget: break
    return np.asarray(candidates,int),dict(candidate_groups=groups,candidate_group_rank=group_ranks,
        candidate_unseen_q_rank=within,visited_groups=visited)


def score_representatives(reps,p,rep,legal):
    if rep=='multi4':
        values=[]; comparisons=0
        for group in legal:
            count=int(reps['multi_count'][group]); comparisons+=count
            if count:
                scaled=(reps['multi4'][group,:count]@p)/.1
                high=np.max(scaled)
                values.append(.1*(high+np.log(np.exp(scaled-high).sum())-np.log(count)))
            else: values.append(-1e30)
        return np.asarray(values),comparisons
    return reps[rep][legal]@p,len(legal)


def approximation(candidate, reference, x, p, budget):
    comp=bool(len(candidate) and len(reference))
    answer=dict(comparable=comp,candidate_supply=len(candidate)/budget,reference_supply=len(reference)/budget)
    if not comp:
        return {**answer,'prefix_overlap':None,'content_gap':None,'absolute_gap':None,'positive_loss':None}
    gap=float(np.mean(x[reference]@p)-np.mean(x[candidate]@p))
    return {**answer,'prefix_overlap':len(set(candidate)&set(reference))/len(reference),
        'content_gap':gap,'absolute_gap':abs(gap),'positive_loss':max(0.,gap)}


def summarize(rows):
    result=[]; valid=rows[rows.profile_state.eq('VALID')]
    for name in sorted(set(valid.policy)):
        a=valid[valid.policy.eq(name)]; comp=a[a.comparable]; returned=a[a.returned.gt(0)]
        mean=lambda col:float(comp[col].mean()) if len(comp) else None
        s=dict(policy=name,users=len(a),comparable_users=len(comp),full_return_share=float((a.returned>=10).mean()),
            comparable_share=len(comp)/len(a),mean_candidates=float(a.candidate_count.mean()),
            latency_p95_ms=float(np.quantile(a.latency_ms,.95)),cpu_p95_ms=float(np.quantile(a.cpu_ms,.95)),
            mean_absolute_gap=mean('absolute_gap'),mean_signed_gap=mean('content_gap'),mean_prefix_overlap=mean('prefix_overlap'),
            absolute_gap_p95=float(np.quantile(comp.absolute_gap,.95)) if len(comp) else None,
            worst_positive_loss=float(comp.positive_loss.max()) if len(comp) else None,
            top1_users=len(returned),tmdb_low20_top1_share=float(returned.low_tmdb20_top1.mean()) if len(returned) else None,
            ml_low20_top1_share=float(returned.low_ml20_top1.mean()) if len(returned) else None,
            tmdb_low20_top1_all_valid_share=float(a.low_tmdb20_top1.mean()),ml_low20_top1_all_valid_share=float(a.low_ml20_top1.mean()),
            hierarchy=a.hierarchy.iloc[0],rep=a.rep.iloc[0],budget=int(a.budget.iloc[0]),quota=int(a.quota.iloc[0]))
        gates=dict(full_return=s['full_return_share']>=.95,comparable=s['comparable_share']>=.95,
            overlap=bool(len(comp) and s['mean_prefix_overlap']>=.60),p95_gap=bool(len(comp) and s['absolute_gap_p95']<=.10),
            worst_loss=bool(len(comp) and s['worst_positive_loss']<=.25),latency=s['latency_p95_ms']<=250,
            hard_invariants=bool((a.global_fill==0).all() and (a.candidate_count<=a.budget).all()))
        s.update(gates=gates,feasible=all(gates.values()));result.append(s)
    return result


def choose_independently(summary, policies, group_counts):
    ids={p['id'] for p in policies}; defined=[s for s in summary if s['policy'] in ids and s['mean_absolute_gap'] is not None]
    feasible=[s for s in defined if s['feasible']]
    def priority(s): return (s['budget'],group_counts[s['hierarchy']],REPS.index(s['rep']),s['quota']!=25,s['policy'])
    def cost(s): return (s['mean_candidates'],s['latency_p95_ms'],s['mean_absolute_gap'])
    pareto=[]
    for a in feasible:
        dominated=False
        for b in feasible:
            if a is b: continue
            pairs=list(zip(cost(b),cost(a)))
            if all(x<=y for x,y in pairs) and any(x<y for x,y in pairs): dominated=True;break
        if not dominated: pareto.append(a)
    candidate=min(pareto,key=priority)['policy'] if pareto else None
    per_hierarchy={}
    for hierarchy in group_counts:
        pool=[s for s in defined if s['hierarchy']==hierarchy]
        pool=[s for s in pool if s['feasible']] or pool
        if pool:
            per_hierarchy[hierarchy]=min(pool,key=lambda s:(s['mean_absolute_gap'],s['budget'],REPS.index(s['rep']),s['quota']!=25,s['policy']))['policy']
    return dict(status='TECHNICAL_REVIEW_CANDIDATE' if candidate else 'NO_FEASIBLE_POLICY_HOLD',candidate=candidate,
        best_approximation=min(defined,key=lambda s:(s['mean_absolute_gap'],*priority(s)))['policy'] if defined else None,
        per_hierarchy=per_hierarchy,pareto=[s['policy'] for s in sorted(pareto,key=priority)])


class Audit:
    def __init__(self, source, stage):
        self.source=Path(source);self.stage=stage
        for relative,digest in PINS.items(): exact(pin(self.source/relative)['sha256'],digest,relative)
        self.config=read(self.source/(CONTRACT+'config.json'))
        self.base=self.source/'outputs/fixed-k8-discovery-v2'/self.config['version']
        self.stages=['prepare','cluster','predictor','sweep']+(['check'] if stage=='check' else [])
        self.seals={name:self.verify(name) for name in self.stages}
        columns=['service_movie_id','taste_id','raw_vote_average_number','raw_vote_count_number','quality_state',
            'release_date','status','raw_adult_state','raw_video_state','genre_ids','keyword_ids','collection_ids',
            'mapping_status','movielens_movie_id']
        self.frame=pd.read_parquet(self.base/'prepare/catalog.parquet',columns=columns)
        self.ids=self.frame.service_movie_id.to_numpy();exact(len(self.ids),237817);assert len(set(self.ids))==len(self.ids)
        self.x=np.load(self.base/'prepare/content.npy',mmap_mode='r');self.genre=np.load(self.base/'prepare/genre.npy',mmap_mode='r')
        self.contexts={(c['uid'],c['cap']):c for c in read(self.base/'prepare/contexts.json')}
        self.roles=read(self.base/'prepare/roles.json');assert not set(self.roles['validation'])&set(self.roles['verification'])
        self.hs={}
        for name in HIERARCHIES:
            with (self.base/'cluster'/(name+'-hierarchy.pkl')).open('rb') as stream:self.hs[name]=pickle.load(stream)
        self.group_counts={name:h['n_groups'] for name,h in self.hs.items()}
        self.primary=[make_policy(h,r,b,q) for h,r,b,q in itertools.product(HIERARCHIES,REPS,[50,100,200],[10,25])]
        self.controls=[make_policy('v1-fixed16',space='G'),make_policy('v1-fixed16',profile_mode='two_branch'),make_policy('v1-fixed16','qmean')]
        self.quality_cache={};self.partition_cache={};self.state_cache={};self.retrieval_cache={}
        self.descriptor_cache={};self.fixed_mean_cache={};self.drift_verified=0
        self.r=self.frame.raw_vote_average_number.to_numpy(float);self.v=self.frame.raw_vote_count_number.to_numpy(float)
        date=pd.to_datetime(self.frame.release_date,format='%Y-%m-%d',errors='coerce')
        self.base_eligible=(date.notna()&(date<=pd.Timestamp('2026-09-09'))&self.frame.status.eq('Released')&
            self.frame.raw_adult_state.eq('FALSE')&self.frame.raw_video_state.eq('FALSE')).to_numpy() & ((self.x*self.x).sum(axis=1)>1e-12)
        self.valid_quality=self.frame.quality_state.eq('VALID').to_numpy()
        valid=self.valid_quality
        assert np.isfinite(self.r[valid]).all() and ((self.r[valid]>0)&(self.r[valid]<=10)).all()
        assert np.isfinite(self.v[valid]).all() and ((self.v[valid]>0)&(self.v[valid]==np.floor(self.v[valid]))).all()
        q,order=self.quality('main');saved_q=np.load(self.base/'prepare/quality.npy')
        np.testing.assert_allclose(q,saved_q,rtol=1e-13,atol=1e-13,equal_nan=True)
        exact(order,np.load(self.base/'prepare/quality-order.npy'),'main quality order')
        qi=read(self.base/'prepare/quality-config.json');number(qi['C'],float(self.r[self.base_eligible&valid].mean()),'C');exact(qi['m'],300)
        reference=Path(self.config['reference'])
        support=pd.read_parquet(reference/'text339/catalog.parquet',columns=['movie_id','train_count'])
        factors=set(pd.read_parquet(reference/'combination340/ALS/item-factors',columns=['id']).id)
        counts=dict(zip(support.movie_id,support.train_count));self.train=np.zeros(len(self.ids),int);self.factors=np.zeros(len(self.ids),bool)
        for i,row in enumerate(self.frame.itertuples()):
            if row.mapping_status=='MATCHED':
                mid=int(row.movielens_movie_id);self.train[i]=counts.get(mid,0);self.factors[i]=mid in factors
        self.predictor=read(self.base/'predictor/decision.json')['selected']
        self.total_requests=0;self.total_candidates=0;self.nonempty=0;self.profile_counts={}

    def verify(self,stage):
        seal=read(self.base/(stage+'-seal.json'))
        expected={k.replace('\\','/'):v for k,v in seal['files'].items()}
        actual={p.relative_to(self.base).as_posix():pin(p) for p in (self.base/stage).rglob('*') if p.is_file()}
        exact(actual,expected,stage+' complete seal');return pin(self.base/(stage+'-seal.json'))

    def quality(self,tag):
        if tag in self.quality_cache:return self.quality_cache[tag]
        kind='movie_mean';m=300
        if tag.startswith('Q-'):
            kind,m=tag[2:].rsplit('-m',1);m=int(m);assert kind in ['movie_mean','vote_mean'] and m in [100,300,1000]
        else:assert tag in ['main','RAW_R_ONLY','TMDB_COUNT_ONLY','TMDB_MIN21']
        use=self.base_eligible&self.valid_quality
        c=float(self.r[use].mean()) if kind=='movie_mean' else float(np.sum(self.v[use]*self.r[use])/np.sum(self.v[use]))
        q=np.full(len(self.ids),np.nan);valid=self.valid_quality
        q[valid]=(self.v[valid]*self.r[valid]+m*c)/(self.v[valid]+m)
        legal=np.flatnonzero(use)
        if tag=='TMDB_MIN21':legal=legal[self.v[legal]>=21]
        if tag=='RAW_R_ONLY':sort=np.lexsort((self.ids[legal],-self.v[legal],-self.r[legal]))
        elif tag=='TMDB_COUNT_ONLY':sort=np.lexsort((self.ids[legal],-self.r[legal],-self.v[legal]))
        else:sort=np.lexsort((self.ids[legal],-self.v[legal],-q[legal]))
        self.quality_cache[tag]=(q,legal[sort]);return self.quality_cache[tag]

    def state(self,c,p):
        tag=p.get('_diagnostic','main');tag='main' if tag.startswith('CAP') else tag
        global_ref=p['kind'].startswith('global_');space=p.get('space','GKT');mode=p.get('profile_mode','signed')
        key=(c['uid'],c['cap'],p['hierarchy'],p['quota'],space,mode,tag,global_ref)
        if key in self.state_cache:return self.state_cache[key]
        h=self.hs[p['hierarchy']];x=self.genre if space=='G' else self.x;direction,info=independent_profile(x,c,mode)
        viewed=np.asarray(c['viewed'],int);assert len(viewed)==len(set(viewed))
        seen=np.zeros(len(self.ids),bool);seen[viewed]=True;q,quality_order=self.quality(tag)
        counts=np.zeros(h['n_groups'],int);under=np.zeros(h['n_groups'],bool);pools={}
        if not global_ref:
            counts=np.bincount(h['groups'][viewed],minlength=h['n_groups'])
            under=(counts<=2)&(counts/max(1,len(viewed))<=.2)
            pk=(p['hierarchy'],tag)
            if pk not in self.partition_cache:
                ordered=quality_order[np.argsort(h['groups'][quality_order],kind='stable')]
                bounds=np.searchsorted(h['groups'][ordered],np.arange(h['n_groups']+1))
                self.partition_cache[pk]=[ordered[bounds[g]:bounds[g+1]] for g in range(h['n_groups'])]
            for g in np.flatnonzero(under):
                rows=self.partition_cache[pk][g];rows=rows[~seen[rows]][:p['quota']]
                if len(rows):pools[int(g)]=rows
        state=dict(p=direction,profile=info,x=x,h=h,seen=seen,counts=counts,under=under,pools=pools,
            legal=np.asarray(sorted(pools),int),q=q,quality_order=quality_order,global_reference=global_ref)
        self.state_cache[key]=state;return state

    def retrieve(self,c,p):
        key=(c['uid'],c['cap'],p['id'])
        if key in self.retrieval_cache:return self.retrieval_cache[key]
        s=self.state(c,p);legal=s['legal'];kind=p['kind'];budget=p['budget']
        extra=dict(representative_comparisons=0,content_comparisons=0,candidate_groups=[],candidate_group_rank=[],
            candidate_unseen_q_rank=[],visited_groups=[])
        if s['profile']['state']!='VALID':
            result=(np.array([],int),s,{**extra,'state':'NO_PERSONAL_VECTOR'})
        elif not len(legal) and not s['global_reference']:
            result=(np.array([],int),s,{**extra,'state':'NO_ELIGIBLE_DISCOVERY_GROUP'})
        else:
            if kind in ['group','direct_prefix','random']:
                if kind=='group':
                    rep='genre_mean' if p.get('space')=='G' else p['rep']
                    scores,extra['representative_comparisons']=score_representatives(s['h']['representatives'],s['p'],rep,legal)
                    order=legal[descending(scores,legal)]
                elif kind=='direct_prefix':
                    dots=[s['x'][s['pools'][g]]@s['p'] for g in legal]
                    values=np.asarray([v.mean() for v in dots]);order=legal[descending(values,legal)]
                    extra['content_comparisons']=sum(len(v) for v in dots)
                    extra['direct_mean_scores']=values
                    extra['direct_top5_scores']=[np.sort(v)[-5:].mean() for v in dots]
                else:
                    rng=np.random.default_rng(np.random.SeedSequence([p['seed'],int(c['uid']),s['h']['n_groups'],p['quota']]))
                    order=rng.permutation(legal)
                candidate,provenance=visit(order,s['pools'],budget);extra.update(provenance,group_order=order.tolist())
            else:
                available=s['quality_order'][~s['seen'][s['quality_order']]]
                if not s['global_reference']:available=available[np.isin(s['h']['groups'][available],legal)]
                if kind=='all_content':
                    values=s['x'][available]@s['p'];extra['content_comparisons']=len(available)
                    candidate=available[descending(values,self.ids[available])[:budget]]
                elif kind in ['flat_q','global_flat_q']:candidate=available[:budget]
                else:assert kind in ['full_predictor','global_full_predictor'];candidate=available
                extra['candidate_groups']=s['h']['groups'][candidate].tolist()
            extra['state']='READY' if len(candidate)>=budget else 'ELIGIBLE_PREFIXES_EXHAUSTED'
            result=(candidate,s,extra)
        self.retrieval_cache[key]=result;return result

    def request(self,row,detail):
        p=parse_policy(row['policy']);c=self.contexts[int(row['uid']),int(row['cap'])]
        for key in ['uid','cap','policy','predictor']:exact(detail[key],row[key],key)
        exact(row['predictor'],self.predictor,'frozen predictor')
        for key in ['hierarchy','rep','kind','budget','quota']:exact(row[key],p[key],key)
        candidate,s,extra=self.retrieve(c,p);got=np.asarray(detail['candidate_indices'],int)
        exact(got,candidate,'actual candidate order '+row['policy'])
        exact(len(got),row['candidate_count'],'candidate count')
        assert len(set(got))==len(got) and not s['seen'][got].any()
        assert set(got)<=set(s['quality_order'])
        if not p['kind'].startswith('global_'):assert set(s['h']['groups'][got])<=set(s['legal'])
        if 'full_predictor' not in p['kind']:assert len(got)<=p['budget']
        info=detail['retrieval'];exact(info['global_fill'],0);exact(row['global_fill'],0)
        exact(info['E_u_applicable'],not s['global_reference'])
        exact(info['legal_groups'],s['legal'],'legal E_u')
        exact(info['underseen_groups'],np.flatnonzero(s['under']),'underseen')
        exact(info['viewed_counts'],s['counts'],'viewed group counts')
        exact(info['state'],extra['state']);exact(row['retrieval_state'],extra['state'])
        exact(row['profile_state'],s['profile']['state'],'profile state')
        exact(row['legal_group_count'],len(s['legal']))
        exact(info['initial_probes'],(p['budget']+p['quota']-1)//p['quota'])
        for key,value in s['profile'].items():
            for saved in [info['profile'],row['profile']]:
                if isinstance(value,str):exact(saved[key],value,'profile '+key)
                else:number(saved[key],value,'profile '+key)
        for key,value in extra.items():
            if key=='state':continue
            if key in ['direct_mean_scores','direct_top5_scores']:
                np.testing.assert_allclose(info[key],value,rtol=1e-10,atol=1e-12)
            else:exact(info[key],value,'retrieval '+key)
        for key in ['representative_comparisons','content_comparisons']:exact(row[key],extra[key],key)
        predictions=np.asarray(detail['candidate_predictions'],float)
        assert predictions.shape==got.shape and np.isfinite(predictions).all()
        order=descending(predictions,self.ids[got])[:10];ranked=got[order]
        exact(row['ranked'],ranked,'final affine rank');exact(row['ranked_prediction'],predictions[order])
        exact(row['returned'],len(ranked));exact(row['ranked_candidate_position'],order+1)
        available=self.factors[got]&bool(self.factors[c['history']].any())
        weight=available.astype(float)
        if self.predictor.startswith('shrink'):
            weight*=self.train[got]/(self.train[got]+int(self.predictor[6:]))
        elif self.predictor=='min20':weight*=self.train[got]>=20
        else:exact(self.predictor,'original')
        branches=np.where(weight==1,'ALS',np.where(weight>0,'ALS_GBT_SHRINK','GBT'))
        exact(detail['candidate_als_weights'],weight);exact(detail['candidate_branches'],branches)
        exact(row['model_calls'],(len(got)+8191)//8192)
        exact(row['als_rows'],int((weight>0).sum()));exact(row['gbt_rows'],int((weight<1).sum()))
        for key,value in dict(ranked_train_count=self.train[ranked],ranked_branch=branches[order],
            ranked_als_available=available[order],ranked_als_weight=weight[order],ranked_tmdb_count=self.v[ranked],ranked_Q=s['q'][ranked]).items():
            exact(row[key],value,key)
        counts=dict(low_tmdb20_slots=int((self.v[ranked]<=20).sum()),low_ml20_slots=int((self.train[ranked]<=20).sum()),
            candidate_low_tmdb20=int((self.v[got]<=20).sum()),candidate_low_ml20=int((self.train[got]<=20).sum()),
            low_tmdb20_top1=int(bool(len(ranked)) and self.v[ranked[0]]<=20),
            low_ml20_top1=int(bool(len(ranked)) and self.train[ranked[0]]<=20),
            original_als_low20_top1=int(bool(len(ranked)) and available[order[0]] and self.train[ranked[0]]<=20))
        for key,value in counts.items():exact(row[key],value,key)
        if len(got):
            qo=np.lexsort((self.ids[got],-self.v[got],-s['q'][got])); qr=np.empty(len(got),int);qr[qo]=np.arange(1,len(got)+1)
            exact(row['ranked_quality_position'],qr[order]);exact(row['Q_order_top10'],got[qo[:10]])
            exact(row['Q_order_low_tmdb20_top1'],int(self.v[got[qo[0]]]<=20))
            exact(row['Q_order_low_ml20_top1'],int(self.train[got[qo[0]]]<=20))
        else:
            exact(row['ranked_quality_position'],[]);exact(row['Q_order_top10'],[])
        if row['reference_supply'] is None or pd.isna(row['reference_supply']):
            assert 'full_predictor' in p['kind'] or self.stage=='predictor'
            expected=dict(comparable=False,prefix_overlap=None,content_gap=None,absolute_gap=None,positive_loss=None,
                candidate_supply=len(got)/p['budget'],reference_supply=None)
        else:
            direct={**p,'kind':'direct_prefix','id':'AUDIT_DIRECT:'+p['id']}
            reference,reference_state,_=self.retrieve(c,direct)
            expected=approximation(got,reference,s['x'],s['p'],p['budget'])
        for key,value in expected.items():
            if isinstance(value,bool):exact(row[key],value,key)
            else:number(row[key],value,key)
        assert np.isfinite([row['latency_ms'],row['cpu_ms'],row['retrieval_ms']]).all()
        assert row['latency_ms']>=row['retrieval_ms']>=0 and row['cpu_ms']>=0
        self.descriptive_metrics(row,c,got,ranked,s)
        self.total_requests+=1;self.total_candidates+=len(got);self.nonempty+=bool(len(got))

    def descriptive_metrics(self,row,c,candidate,ranked,s):
        eligible=set(s['quality_order']);truth={i:r for i,r in zip(c['target'],c['ratings']) if i in eligible}
        positives={i for i,r in truth.items() if r>=4};known=[truth[int(i)] for i in ranked if int(i) in truth]
        values=dict(observed_slots=len(known),unknown_slots=len(ranked)-len(known),
            unknown_share=(len(ranked)-len(known))/len(ranked) if len(ranked) else None,
            known_mean=float(np.mean(known)) if known else None,strong_negative_slots=sum(r<=2 for r in known),
            eligible_targets=len(truth),positive_targets=len(positives),
            future_recall10=len(set(ranked)&positives)/len(positives) if positives else None,
            future_candidate_recall=len(set(candidate)&positives)/len(positives) if positives else None)
        top_seen=np.bincount(self.frame.taste_id.iloc[c['viewed']].to_numpy(int),minlength=8)
        top_ranked=self.frame.taste_id.iloc[ranked].to_numpy(int)
        values['fixed8_unseen_slots']=int((top_seen[top_ranked]==0).sum())
        exact(row['top8_histogram'],np.bincount(top_ranked,minlength=8))
        ck=(c['uid'],c['cap'])
        if ck not in self.descriptor_cache:
            seen_genres=(self.genre[c['viewed']]>0).any(axis=0) if c['viewed'] else np.zeros(self.genre.shape[1],bool)
            seen_keywords={int(k) for ar in self.frame.keyword_ids.iloc[c['viewed']] for k in ar}
            seen_collections={int(k) for ar in self.frame.collection_ids.iloc[c['viewed']] for k in ar}
            self.descriptor_cache[ck]=(seen_genres,seen_keywords,seen_collections)
        seen_genres,seen_keywords,seen_collections=self.descriptor_cache[ck]
        new_genres=[];keyword_overlap=[];collection_repeat=[]
        for i in ranked:
            genre=self.genre[i]>0
            if genre.any():new_genres.append(float((genre&~seen_genres).sum()/genre.sum()))
            kw=set(map(int,self.frame.keyword_ids.iloc[int(i)]))
            if kw:keyword_overlap.append(len(kw&seen_keywords)/len(kw))
            cc=set(map(int,self.frame.collection_ids.iloc[int(i)]))
            collection_repeat.append(bool(cc&seen_collections))
        values['genre_new_fraction']=float(np.mean(new_genres)) if new_genres else None
        values['keyword_seen_overlap']=float(np.mean(keyword_overlap)) if keyword_overlap else None
        values['seen_collection_slots']=sum(collection_repeat)
        ideal=np.array(sorted([max(r-3,0) for r in truth.values()],reverse=True))
        for k in [2,6,10]:
            gains=np.array([max(truth.get(int(i),0)-3,0) for i in ranked[:k]])
            idcg=float((ideal[:k]/np.log2(np.arange(min(k,len(ideal)))+2)).sum())
            values['future_ndcg'+str(k)]=float((gains/np.log2(np.arange(len(gains))+2)).sum()/idcg) if positives and idcg else None
        for key,value in values.items():number(row[key],value,key)

    def compare_summary(self,rows,saved):
        actual=summarize(rows);exact([v['policy'] for v in actual],[v['policy'] for v in saved],'summary inventory')
        for new,old in zip(actual,saved):
            exact(set(new),set(old),'summary keys')
            for key,value in new.items():
                if isinstance(value,(dict,str,bool)):exact(old[key],value,key)
                else:number(old[key],value,key)
        return actual

    def selection(self,summary):
        decision=read(self.base/'sweep/decision.json')
        actual=choose_independently(summary,self.primary,self.group_counts)
        for key,value in actual.items():exact(decision[key],value,'selection '+key)
        exact(decision['policies'],{p['id']:p for p in self.primary},'primary grid')
        exact(decision['controls'],self.controls,'fixed controls')
        exact(decision['predictor'],self.predictor)
        return actual

    def inspect_detail_file(self,path,rows):
        with gzip.open(path,'rt',encoding='utf-8') as stream:details=json.load(stream)
        mapping={(int(d['uid']),int(d['cap']),d['policy']):d for d in details}
        exact(len(mapping),len(details),'detail uniqueness')
        row_keys={(int(r.uid),int(r.cap),r.policy) for r in rows.itertuples()}
        exact(set(mapping),row_keys,'detail/result inventory '+path.name)
        for row in rows.to_dict('records'):
            self.request(row,mapping[int(row['uid']),int(row['cap']),row['policy']])

    def run(self):
        start=time.perf_counter();stage=self.base/self.stage;rows=pd.read_parquet(stage/'requests.parquet')
        assert not rows.duplicated(['uid','cap','policy']).any()
        users=self.roles['validation' if self.stage=='sweep' else 'verification']
        exact(set(rows.uid),set(users));exact(set(rows.cap),{10})
        if self.stage=='sweep':
            expected={p['id'] for p in self.primary+self.controls}
            exact(len(rows),len(users)*len(expected))
            for uid,group in rows.groupby('uid'):exact(set(group.policy),expected,'full per-user policy grid')
        else:
            selected=read(self.base/'sweep/decision.json');report=read(stage/'report.json')
            expected=set(selected['per_hierarchy'].values())|{p for p in [selected['candidate'],selected['best_approximation']] if p}
            finalists=[selected['policies'][p] for p in sorted(expected)]+self.controls
            exact(report['finalist_policies'],finalists,'check frozen finalists')
            exact(report['exemplar'],selected['candidate'] or selected['best_approximation'])
            exact(report['check_did_not_reselect'],True)
            for uid,group in rows.groupby('uid'):
                exact(set(group[group.kind.eq('group')].policy),{p['id'] for p in finalists},'check primary coverage')
            drift=pd.read_parquet(stage/'prefix-drift.parquet')
            assert not drift.duplicated(['uid','policy','group_id']).any()
            self.drift_by_user={int(uid):g for uid,g in drift.groupby('uid')}
        for offset,uid in enumerate(users,1):
            self.state_cache.clear();self.retrieval_cache.clear()
            current=rows[rows.uid.eq(uid)];self.inspect_detail_file(stage/'details'/f'{uid}.json.gz',current)
            if self.stage=='check':self.check_drift(current,self.drift_by_user.get(int(uid),pd.DataFrame()))
            if offset%10==0:print(json.dumps({'progress':offset,'users':len(users),'requests_verified':self.total_requests}),flush=True)
        group_rows=rows[rows.kind.eq('group')]
        summary=self.compare_summary(group_rows,read(stage/'summary.json'))
        if self.stage=='sweep':decision=self.selection(summary)
        else:
            decision=None;self.check_links(rows);self.check_sensitivities(stage,users)
            exact(self.drift_verified,len(drift),'complete prefix-drift rows')
        for name,sealed in self.seals.items():exact(self.verify(name),sealed,name+' stable seal after audit')
        for relative,digest in PINS.items():exact(pin(self.source/relative)['sha256'],digest,relative+' stable source')
        return dict(status='PASS',scope='Every saved request candidate/order/E_u/prefix/rank/accounting; aggregates and frozen finite decisions',
            stage=self.stage,script=pin(Path(__file__)),source_seals=self.seals,request_count=self.total_requests,
            candidate_rows=self.total_candidates,nonempty_requests=self.nonempty,users=len(users),prefix_drift_rows=self.drift_verified,summary=summary,decision=decision,
            wall_seconds=time.perf_counter()-start,trained_model_calls=0,
            limits=['Predictions and timing values are saved measurements; affine ordering/accounting are independently checked.',
                    'Prepared frozen content and original support identities rely additionally on prior independent input/source audits.',
                    'No new criteria, policy selection, training, native model calls, human-value claims or production approval.'])

    def check_links(self,rows):
        links=read(self.base/'check/reference-links.json')
        finalists=read(self.base/'check/report.json')['finalist_policies'];expected=[]
        contexts=[c for c in self.contexts.values() if c['cap']==10 and c['uid'] in self.roles['verification']]
        for j,c in enumerate(contexts):
            completed={};expected_rows={p['id'] for p in finalists}
            rotated=finalists[j%len(finalists):]+finalists[:j%len(finalists)]
            for p in rotated:
                x=self.genre if p.get('space')=='G' else self.x
                if independent_profile(x,c,p.get('profile_mode','signed'))[1]['state']!='VALID':continue
                extras={key:p[key] for key in ['space','profile_mode'] if key in p}
                refs=[make_policy(p['hierarchy'],budget=p['budget'],quota=p['quota'],kind=kind,**extras)
                    for kind in ['direct_prefix','all_content','flat_q','full_predictor','global_flat_q','global_full_predictor']]
                refs.extend(make_policy(p['hierarchy'],budget=p['budget'],quota=p['quota'],kind='random',seed=seed,**extras) for seed in [913,914,915])
                for cp in refs:
                    if cp['kind']=='global_full_predictor':key=(cp['kind'],)
                    elif cp['kind']=='full_predictor':key=(cp['kind'],cp['hierarchy'])
                    elif cp['kind']=='global_flat_q':key=(cp['kind'],cp['budget'])
                    else:key=(cp['id'],)
                    reused=key in completed
                    if not reused:completed[key]=cp['id'];expected_rows.add(cp['id'])
                    expected.append(dict(uid=c['uid'],policy=p['id'],reference_policy=completed[key],reference_reused_for_comparison=reused))
            exact(set(rows.loc[rows.uid.eq(c['uid']),'policy']),expected_rows,'exact executed/reference-cache inventory')
        assert links==expected,'reference links must match exact user rotation, constraints and first execution/reuse cache semantics'

    def check_drift(self,requests,saved):
        expected_keys=set()
        for row in requests[requests.kind.eq('group')].itertuples():
            p=parse_policy(row.policy);c=self.contexts[int(row.uid),int(row.cap)];s=self.state(c,p)
            group_saved=saved[saved.policy.eq(row.policy)].set_index('group_id') if len(saved) else pd.DataFrame()
            exact(set(group_saved.index),set(s['legal']),'drift legal-group inventory')
            for g in s['legal']:
                expected_keys.add((row.policy,int(g)));got=group_saved.loc[g]
                actual=s['pools'][g];fixed=self.partition_cache[p['hierarchy'],'main'][g][:p['quota']]
                space=p.get('space','GKT');cachekey=(p['hierarchy'],space,p['quota'],int(g))
                if cachekey not in self.fixed_mean_cache:
                    self.fixed_mean_cache[cachekey]=s['x'][fixed].mean(axis=0) if len(fixed) else np.zeros(s['x'].shape[1])
                actual_mean=s['x'][actual].mean(axis=0);fixed_mean=self.fixed_mean_cache[cachekey]
                whole=s['h']['representatives']['genre_mean' if space=='G' else 'mean'][g]
                exact(got.hierarchy,p['hierarchy']);exact(got.profile_state,s['profile']['state'])
                expected=dict(actual_prefix_count=len(actual),fixed_prefix_count=len(fixed),viewed_count=int(s['counts'][g]),
                    whole_to_actual_distance=float(np.linalg.norm(whole-actual_mean)),
                    whole_to_fixed_distance=float(np.linalg.norm(whole-fixed_mean)),
                    actual_mean_similarity=float(actual_mean@s['p']),whole_mean_similarity=float(whole@s['p']))
                for key,value in expected.items():number(got[key],value,'prefix-drift '+key)
                self.drift_verified+=1
        saved_keys=set(zip(saved.policy,saved.group_id)) if len(saved) else set()
        exact(saved_keys,expected_keys,'all primary/control drift inventory')

    def check_sensitivities(self,stage,users):
        rows=pd.read_parquet(stage/'sensitivity-requests.parquet')
        assert not rows.duplicated(['uid','cap','policy']).any()
        quality_tags=['Q-'+kind+'-m'+str(m) for kind,m in itertools.product(['movie_mean','vote_mean'],[100,300,1000]) if (kind,m)!=('movie_mean',300)]
        tags=quality_tags+['TMDB_MIN21','RAW_R_ONLY','TMDB_COUNT_ONLY']
        exact(set(rows.sensitivity),set(tags+['CAP0','CAP30']))
        exact(len(rows),len(users)*(len(tags)+2))
        for uid in users:
            self.state_cache.clear();self.retrieval_cache.clear()
            current=rows[rows.uid.eq(uid)];main=current[current.cap.eq(10)]
            exact(set(main.sensitivity),set(tags))
            self.inspect_detail_file(stage/'details'/f'{uid}-sensitivity.json.gz',main)
            for cap in [0,30]:
                self.inspect_detail_file(stage/'details'/f'{uid}-cap{cap}.json.gz',current[current.cap.eq(cap)])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['sweep','check'],required=True)
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--execute',action='store_true',help='Explicitly allow reading completed stage results; never writes source outputs.')
    args=parser.parse_args()
    if not args.execute:parser.error('--execute is required to open stage results')
    with threadpool_limits(limits=2):result=Audit(args.source,args.stage).run()
    print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)


if __name__=='__main__':main()
