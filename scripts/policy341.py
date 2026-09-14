"""Pure deterministic taste/discovery policies over the complete candidate set."""
import numpy as np
from rec046_common import require

POLICIES={'P0':(0.,None),'P1_010':(.1,None),'P1_020':(.2,None),'P2_010':(.1,.3),'P2_020':(.2,.3)}
NOVELTY_ATOL=1e-12

def genre_vectors(metadata):
    genres=sorted({int(g) for row in metadata.genre_ids for g in row})
    require(len(genres)==19,'fixed original TMDB multi-genre axis')
    vectors=np.array([[float(g in row) for g in genres] for row in metadata.genre_ids])
    norm=np.linalg.norm(vectors,axis=1)
    vectors/=np.maximum(norm,1)[:,None]
    return vectors,norm>0,genres

def familiarity(vectors,present,oi,ei):
    oi=np.asarray(oi,int); ei=np.asarray(ei,int)
    usable=oi[present[oi]]
    if not len(usable): return np.full(len(ei),np.nan),False
    profile=vectors[usable].sum(0); length=np.linalg.norm(profile)
    if length==0: return np.full(len(ei),np.nan),False
    scores=np.clip(vectors[ei]@(profile/length),0,1)
    scores[~present[ei]]=np.nan
    return scores,True

def recommend(scores,ids,similarity,has_input,policy,blocks=3):
    scores=np.asarray(scores,float); ids=np.asarray(ids,int); similarity=np.asarray(similarity,float)
    require(policy in POLICIES and len(scores)==len(ids)==len(similarity) and len(np.unique(ids))==len(ids),'unique policy candidate axis')
    require(np.isfinite(scores).all(),'finite fixed model scores')
    order=np.lexsort((ids,-scores)); remaining=np.ones(len(ids),bool); returned=[]; roles=[]; block_info=[]
    d,limit=POLICIES[policy]; novelty=1-similarity
    for block in range(blocks):
        ranked=order[remaining[order]]
        if len(ranked)<3:
            returned.extend(ranked.tolist()); roles.extend(['TASTE']*len(ranked)); break
        first,second,third=map(int,ranked[:3]); chosen=third; discovered=False
        if policy!='P0' and has_input and np.isfinite(novelty[third]):
            legal=remaining.copy(); legal[[first,second]]=False
            legal &= np.isfinite(similarity) & (similarity>=.25) & (novelty-novelty[third]>=d-NOVELTY_ATOL)
            if limit is not None: legal &= scores>=scores[third]-limit
            options=order[legal[order]]
            if len(options): chosen=int(options[0]); discovered=True
        returned.extend([first,second,chosen]); roles.extend(['TASTE','TASTE','DISCOVERY' if discovered else 'TASTE'])
        remaining[[first,second,chosen]]=False
        block_info.append({'block':block,'baseline_third':third,'chosen_third':chosen,'discovered':discovered,
                           'fallback':policy!='P0' and not discovered})
    require(len(returned)==len(set(returned)),'unique returned movies')
    return np.asarray(returned,int),roles,block_info
