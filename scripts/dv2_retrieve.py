"""E_u first, frozen representative order, quality prefixes, no global fill."""
from __future__ import annotations
import math
import numpy as np
from scipy.special import logsumexp
from dv2_common import CFG,profile,rank,array_hash

def unseen_prefix(order,seen,quota):
    a=np.asarray(order,int)
    return a[~seen[a]][:quota]

def representative_scores(reps,p,kind,legal=None):
    total=len(reps[kind]);legal=np.arange(total) if legal is None else np.asarray(legal,int)
    scores=np.full(total,-1e30)
    if kind=='multi4':
        count=reps['multi_count'];comparisons=0
        for g in legal:
            n=int(count[g])
            if n:
                values=reps[kind][g,:n]@p
                scores[g]=CFG['multi_temperature']*(logsumexp(values/CFG['multi_temperature'])-np.log(n));comparisons+=n
        return scores,comparisons
    scores[legal]=reps[kind][legal]@p
    return scores,len(legal)

def gather(group_order,pools,budget):
    candidates=[];source=[];within=[];group_ranks=[];visited=[]
    for r,g in enumerate(group_order):
        a=pools[int(g)];visited.append(int(g))
        take=a[:budget-len(candidates)]
        candidates.extend(map(int,take));source.extend([int(g)]*len(take));within.extend(range(1,len(take)+1));group_ranks.extend([r+1]*len(take))
        if len(candidates)>=budget:break
    return np.asarray(candidates,int),{'candidate_groups':source,'candidate_group_rank':group_ranks,'candidate_unseen_q_rank':within,'visited_groups':visited}

class Engine:
    def __init__(self,hierarchy,x,genre,frame,order,q):
        self.h=hierarchy;self.x=x;self.genre=genre;self.ids=frame.service_movie_id.to_numpy();self.q=q
        self.groups=hierarchy['groups'];self.n=hierarchy['n_groups'];self.order=np.asarray(order,int)
        self.space_hashes=hierarchy.get('space_hashes') or {'GKT':array_hash(x),'G':array_hash(genre)}
        # Stable partition preserves the exact frozen Q/v/service-ID order.
        parts=np.argsort(self.groups[self.order],kind='stable');grouped=self.order[parts]
        bounds=np.searchsorted(self.groups[grouped],np.arange(self.n+1))
        self.lists=[grouped[bounds[g]:bounds[g+1]] for g in range(self.n)]

    def state(self,c,quota,space='GKT',profile_mode='signed',global_reference=False):
        x=self.genre if space=='G' else self.x
        assert space in ['G','GKT'],'explicit content space required'
        p,info=profile(x,c,profile_mode)
        info['space']=space;info['space_hash']=self.space_hashes[space]
        viewed=np.asarray(c['viewed'],int)
        assert len(np.unique(viewed))==len(viewed),'viewed IDs must be distinct'
        seen=np.zeros(len(self.ids),bool);seen[viewed]=True
        if global_reference:
            return {'p':p,'profile':info,'seen':seen,'counts':np.zeros(self.n,int),'under':np.zeros(self.n,bool),
                    'pools':{},'legal':np.array([],int),'x':x,'E_u_applicable':False}
        counts=np.bincount(self.groups[viewed],minlength=self.n)
        under=(counts<=CFG['underseen_count'])&(counts/max(1,len(c['viewed']))<=CFG['underseen_share'])
        pools={g:unseen_prefix(self.lists[g],seen,quota) for g in np.flatnonzero(under)}
        pools={g:a for g,a in pools.items() if len(a)}
        legal=np.asarray(sorted(pools),int)
        return {'p':p,'profile':info,'seen':seen,'counts':counts,'under':under,'pools':pools,'legal':legal,'x':x,'E_u_applicable':True}

    def retrieve(self,c,policy):
        B=policy['budget'];quota=policy['quota'];rep=policy.get('rep','mean');kind=policy.get('kind','group')
        if policy.get('space','GKT')=='G':
            assert rep in ['mean','genre_mean'],'genre control only defines genre mean'
            rep='genre_mean'
        state=self.state(c,quota,policy.get('space','GKT'),policy.get('profile_mode','signed'),global_reference=kind.startswith('global_'))
        p=state['p'];legal=state['legal'];pools=state['pools'];seen=state['seen']
        info={'global_fill':0,'legal_groups':legal.tolist(),'underseen_groups':np.flatnonzero(state['under']).tolist(),
              'E_u_applicable':state['E_u_applicable'],
              'viewed_counts':state['counts'].tolist(),'profile':state['profile'],'representative_comparisons':0,'content_comparisons':0,
              'initial_probes':math.ceil(B/quota),'candidate_groups':[],'candidate_group_rank':[],'candidate_unseen_q_rank':[],'visited_groups':[]}
        if state['profile']['state']!='VALID':
            info['state']='NO_PERSONAL_VECTOR';return np.array([],int),info,state
        if not len(legal) and kind not in ['global_flat_q','global_full_predictor']:
            info['state']='NO_ELIGIBLE_DISCOVERY_GROUP';return np.array([],int),info,state
        if kind in ['group','direct_prefix','random']:
            if kind=='direct_prefix':
                values=[state['x'][pools[g]]@p for g in legal]
                scores=np.array([float(np.mean(v)) for v in values]);info['content_comparisons']=sum(len(a) for a in pools.values())
                info['direct_top5_scores']=[float(np.mean(np.sort(v)[-5:])) for v in values]
                info['direct_mean_scores']=scores.tolist()
                order=legal[rank(scores,legal)]
            elif kind=='random':
                rng=np.random.default_rng(np.random.SeedSequence([policy['seed'],int(c['uid']),self.n,quota]))
                order=rng.permutation(legal)
            else:
                assert self.h['representatives'][rep].shape[-1]==len(p),'representative and user spaces must match'
                scores,comparisons=representative_scores(self.h['representatives'],p,rep,legal)
                info['representative_comparisons']=comparisons;order=legal[rank(scores[legal],legal)]
            candidates,extra=gather(order,pools,B);info.update(extra);info['group_order']=order.tolist()
        else:
            legalmask=np.zeros(self.n,bool);legalmask[legal]=True
            available=self.order[~seen[self.order]]
            if not kind.startswith('global_'):available=available[legalmask[self.groups[available]]]
            if kind=='all_content':
                values=state['x'][available]@p;info['content_comparisons']=len(available)
                candidates=available[rank(values,self.ids[available])[:B]]
            elif kind in ['flat_q','global_flat_q']:candidates=available[:B]
            elif kind in ['full_predictor','global_full_predictor']:candidates=available
            else:raise ValueError(kind)
            info['candidate_groups']=self.groups[candidates].tolist()
        assert len(np.unique(candidates))==len(candidates) and not seen[candidates].any()
        if 'full_predictor' not in kind:assert len(candidates)<=B
        if not kind.startswith('global_'):assert set(self.groups[candidates])<=set(legal)
        info['candidate_count']=len(candidates)
        info['state']='READY' if len(candidates)>=B else 'ELIGIBLE_PREFIXES_EXHAUSTED'
        info['unfilled_budget']=max(0,B-len(candidates))
        return candidates,info,state

def comparison(candidates,reference,x,p,budget):
    candidates=np.asarray(candidates,int);reference=np.asarray(reference,int)
    result={'candidate_supply':len(candidates)/budget,'reference_supply':len(reference)/budget,'comparable':bool(len(candidates) and len(reference))}
    if not result['comparable']:return {**result,'prefix_overlap':None,'content_gap':None,'absolute_gap':None,'positive_loss':None}
    gap=float(np.mean(x[reference]@p)-np.mean(x[candidates]@p))
    return {**result,'prefix_overlap':len(np.intersect1d(candidates,reference))/len(reference),'content_gap':gap,'absolute_gap':abs(gap),'positive_loss':max(0.,gap)}
