"""Reviewed content comparison, all-catalog final refit and bounded child search."""
from collections import Counter
import gc
import json
import pickle
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import adjusted_rand_score
from sklearn.preprocessing import normalize
from threadpoolctl import threadpool_limits
from k8_common import *

def fit_centers(x,k,seed):
    m=KMeans(n_clusters=k,n_init=5,max_iter=150,algorithm='lloyd',random_state=seed).fit(x)
    assert len(np.unique(m.labels_))==k,'empty trained cluster'
    return canonical(m.cluster_centers_),{'inertia':float(m.inertia_),'iterations':int(m.n_iter_),'converged_before_cap':bool(m.n_iter_<150)}

def reconstruction(blocks, supports, train, target, labels):
    losses=[];base=[]
    for b,s in zip(blocks,supports):
        good=train&s; mu=b[good].mean(axis=0)
        table=np.stack([b[good&(labels==j)].mean(axis=0) if np.any(good&(labels==j)) else mu for j in range(8)])
        ll=np.mean((b[target]-table[labels[target]])**2,axis=1)
        bb=np.mean((b[target]-mu)**2,axis=1)
        ll[~s[target]]=np.nan;bb[~s[target]]=np.nan
        assert np.nanmean(bb)>0,'zero reference baseline'
        losses.append(ll);base.append(bb)
    return np.stack(losses,axis=1),np.stack(base,axis=1)

def compression_summary(loss,base):
    ratios=np.nanmean(loss,axis=0)/np.nanmean(base,axis=0)
    cc=np.isfinite(loss).all(axis=1)
    per=np.nanmean(loss/np.nanmean(base,axis=0),axis=1)
    valid=per[np.isfinite(per)];cut=np.quantile(valid,.9)
    return {'D8':float(ratios.mean()),'ratios':ratios.tolist(),'support':np.isfinite(loss).sum(axis=0).tolist(),
            'complete_case_count':int(cc.sum()),'complete_case_D8':float(np.mean(np.mean(loss[cc],axis=0)/np.mean(base[cc],axis=0))) if cc.any() else None,
            'worst_decile_relative_loss':float(valid[valid>=cut].mean())}

def paired_content_ci(a,b,base,cfg):
    denom=np.nanmean(base,axis=0)
    d=(a-b)/denom
    rng=np.random.default_rng(cfg['seed']);stats=[]
    for _ in range(cfg['bootstrap_replicates']):
        ix=rng.integers(0,len(d),len(d));stats.append(float(np.nanmean(d[ix],axis=0).mean()))
    assert np.isfinite(stats).all(),'bootstrap lacks supported reference rows'
    return {'delta':float(np.nanmean(d,axis=0).mean()),'ci95':np.quantile(stats,[.025,.975]).tolist()}

def fit_preprocessor(frame,train,cfg):
    genres=sorted({int(g) for a in frame.loc[train,'genre_ids'] for g in a})
    count=Counter(int(k) for a in frame.loc[train,'keyword_ids'] for k in set(a))
    keywords=sorted(k for k,n in count.items() if n>=cfg['keyword_min_df'])
    idf=np.array([np.log((train.sum()+1)/(count[k]+1))+1 for k in keywords])
    k=normalize(csr_ids(frame.loc[train,'keyword_ids'],keywords,idf),copy=False)
    ks=TruncatedSVD(n_components=cfg['keyword_dimensions'],random_state=cfg['seed'],n_iter=5).fit(k)
    tv=TfidfVectorizer(lowercase=True,min_df=5,max_features=cfg['text_max_features'],dtype=np.float64)
    t=tv.fit_transform(frame.loc[train,'overview'].tolist())
    ts=TruncatedSVD(n_components=cfg['text_dimensions'],random_state=cfg['seed'],n_iter=5).fit(t)
    return {'genres':genres,'keywords':keywords,'keyword_idf':idf,'keyword_components':ks.components_,
            'text_vectorizer':tv,'text_components':ts.components_,'keyword_explained_variance':float(ks.explained_variance_ratio_.sum()),
            'text_explained_variance':float(ts.explained_variance_ratio_.sum()),'fit_rows':int(train.sum())}

def train_content(cfg):
    verify_seal('prepare')
    dest=OUT/'train';dest.mkdir(parents=True,exist_ok=True)
    assert not (dest/'hierarchies.pkl').exists(),'preserve trained run'
    frame=pd.read_parquet(OUT/'prepare/catalog.parquet')
    assert len(frame)==cfg['expected_movies'] and frame.service_movie_id.is_unique and np.all(np.diff(frame.service_movie_id)>0)
    bucket=hash_buckets(frame.service_movie_id,'k8-content:');tr=bucket<70;va=(bucket>=70)&(bucket<85);te=bucket>=85
    prep=fit_preprocessor(frame,tr,cfg)
    blocks,genres=transform(frame,prep)
    supports=[np.sum(x*x,axis=1)>1e-12 for x in blocks]
    reference=[genres,blocks[1],blocks[2]]
    # All methods share a fixed basis and common evaluation row masks.
    with open(dest/'preprocessor.pkl','wb') as f:pickle.dump(prep,f,protocol=5)
    for name,b in zip(['genre','keyword','text'],blocks):np.save(dest/(name+'.npy'),b.astype(np.float32))
    np.save(dest/'genre-binary.npy',genres.astype(np.uint8));np.save(dest/'content-split.npy',bucket)
    models={};diagnostics=[];all_labels={}
    for name,weights in cfg['top_weights'].items():
        x=combine(blocks,weights);valid=np.sum(x*x,axis=1)>1e-12
        for seed in cfg['seeds']:
            centers,meta=fit_centers(x[tr&valid],8,seed);labels=nearest(x,centers)[0]
            key=f'{name}-{seed}';models[key]=centers;all_labels[key]=labels
            losses,base=reconstruction(reference,supports,tr,va,labels)
            result={'name':name,'seed':seed,'key':key,**compression_summary(losses,base),**meta}
            diagnostics.append(result);print('CONTENT',json.dumps(result),flush=True)
    baseline=min([r for r in diagnostics if r['name']=='G'],key=lambda r:(r['D8'],r['seed']))
    challenger=min([r for r in diagnostics if r['name']!='G'],key=lambda r:(r['D8'],r['key']))
    bl,bb=reconstruction(reference,supports,tr,te,all_labels[baseline['key']])
    cl,cb=reconstruction(reference,supports,tr,te,all_labels[challenger['key']])
    assert np.allclose(bb,cb,equal_nan=True)
    ci=paired_content_ci(cl,bl,bb,cfg)
    selected=challenger if ci['ci95'][1]<0 else baseline
    stability=[]
    for name in cfg['top_weights']:
        for s in cfg['seeds'][1:]:
            a=all_labels[f'{name}-{cfg["seed"]}'];b=all_labels[f'{name}-{s}']
            contingency=np.bincount(a*8+b,minlength=64).reshape(8,8)
            ii,jj=linear_sum_assignment(-contingency)
            stability.append({'name':name,'seed':s,'ARI':float(adjusted_rand_score(a,b)),
                              'matched_agreement':float(contingency[ii,jj].sum()/len(a))})
    write(dest/'content-selection.json',{'baseline':baseline,'challenger':challenger,'verification_delta':ci,
          'baseline_verification':compression_summary(bl,bb),'challenger_verification':compression_summary(cl,cb),
          'selected':selected,'validation':diagnostics,'seed_stability':stability,
          'split_counts':{'train':int(tr.sum()),'validation':int(va.sum()),'verification':int(te.sum())},
          'preprocessor_variance':{k:prep[k] for k in ['keyword_explained_variance','text_explained_variance']}})
    np.savez_compressed(dest/'content-verification-losses.npz',baseline=bl,challenger=cl,global_loss=bb,service_ids=frame.service_movie_id.to_numpy()[te])
    strata=[]
    testframe=frame.loc[te].reset_index(drop=True)
    for field in ['original_language','genre_present','keyword_present','overview_present','mapping_status']:
        for key,indices in testframe.groupby(field,dropna=False).groups.items():
            ix=np.asarray(indices)
            # Unsupported reference blocks are null here, not fabricated scores.
            stats={}
            for m,loss in [('baseline',bl),('challenger',cl)]:
                stats[m]=[float(np.nanmean(loss[ix,j])/np.nanmean(bb[ix,j])) if np.isfinite(loss[ix,j]).any() and np.nanmean(bb[ix,j])>0 else None for j in range(3)]
            strata.append({'field':field,'value':str(key),'movies':len(ix),**stats})
    write(dest/'content-strata.json',strata)
    topx=combine(blocks,cfg['top_weights'][selected['name']]);valid=np.sum(topx*topx,axis=1)>1e-12
    centers,finalmeta=fit_centers(topx[valid],8,selected['seed']);top,toploss=nearest(topx,centers)
    assert set(top)==set(range(8))
    np.save(dest/'top-labels.npy',top);np.save(dest/'top-centers.npy',centers)
    hierarchies={};search=[]
    for space,weights in cfg['sub_weights'].items():
        x=combine(blocks,weights);v=np.sum(x*x,axis=1)>1e-12
        sizes=[];parent_feasible=[];parent_distinct=[]
        for parent in range(8):
            trainmask=(top==parent)&tr&v;validmask=(top==parent)&va&v
            previous=None;chosen=1;feasible=[]
            distinct=len(np.unique(x[trainmask],axis=0))
            parent_distinct.append(distinct)
            for k in cfg['sub_k']:
                if trainmask.sum()<k or validmask.sum()==0 or distinct<k:
                    search.append({'space':space,'parent':parent,'k':k,'accepted':False,'skip_reason':'insufficient_members_validation_or_distinct','distinct_train_vectors':distinct})
                    continue
                cc,meta=fit_centers(x[trainmask],k,cfg['seed']+parent)
                ll=nearest(x[trainmask],cc)[0];count=np.bincount(ll,minlength=k)
                loss=float(nearest(x[validmask],cc)[1].mean())
                improvement=None if previous is None else (previous-loss)/max(previous,1e-12)
                accepted=bool(count.min()>=cfg['sub_min_members'])
                search.append({'space':space,'parent':parent,'k':k,'validation_distortion':loss,'minimum_train_members':int(count.min()),'relative_improvement':improvement,'accepted':accepted,**meta})
                if accepted:feasible.append((k,loss))
                previous=loss
            if feasible:
                best=min(loss for k,loss in feasible)
                chosen=min(k for k,loss in feasible if loss<=best*(1+cfg['sub_min_relative_improvement'])+1e-12)
            search.append({'space':space,'parent':parent,'chosen_k':chosen,'selection':'smallest feasible within2percent best distortion'})
            sizes.append(chosen)
            parent_feasible.append(feasible)
        final_cache={};size_aliases={}
        for policy in ['fixed2','cap8','cap32','cap128','cap256']:
            desired=[]
            for parent in range(8):
                if policy=='fixed2':k=2 if ((top==parent)&tr&v).sum()>=200 and parent_distinct[parent]>=2 else 1
                else:
                    cap=int(policy[3:]);usable=[(k,loss) for k,loss in parent_feasible[parent] if k<=cap]
                    best=min((loss for k,loss in usable),default=0)
                    k=min((k for k,loss in usable if loss<=best*(1+cfg['sub_min_relative_improvement'])+1e-12),default=1)
                desired.append(k)
            if tuple(desired) in size_aliases:
                search.append({'space':space,'policy':policy,'alias_of':size_aliases[tuple(desired)],'sizes':desired})
                continue
            size_aliases[tuple(desired)]=policy
            child_centers=[]
            for parent in range(8):
                mask=(top==parent)&v
                k=desired[parent]
                if np.unique(x[mask],axis=0).shape[0]<k:k=1
                search.append({'space':space,'parent':parent,'policy':policy,'final_k':k,'stage':'all_catalog_refit'})
                if (parent,k) in final_cache:cc=final_cache[parent,k]
                elif not mask.any():cc=np.zeros((1,x.shape[1]))
                else:cc,_=fit_centers(x[mask],k,cfg['seed']+parent)
                final_cache[parent,k]=cc
                child_centers.append(cc)
            key=f'{space}-{policy}'
            bundle={'version':cfg['version'],'preprocessor':prep,'top_weights':cfg['top_weights'][selected['name']],
                    'top_centers':centers,'sub_weights':weights,'child_centers':child_centers,'hierarchy_key':key,
                    'tie_rule':'float64 elementwise squared distances; argmin lowest frozen ID',
                    'top_names':None,'group_names':None}
            # Reuse precomputed blocks to preserve the same exact model geometry.
            child=np.zeros(len(frame),int);offsets=np.cumsum([0]+[len(c) for c in child_centers])
            for parent,cc in enumerate(child_centers):
                ix=np.flatnonzero(top==parent);child[ix]=nearest(x[ix],cc)[0]
            groups=offsets[top]+child
            np.save(dest/(key+'-groups.npy'),groups)
            hierarchies[key]=bundle
            print('HIERARCHY',key,[len(c) for c in child_centers],flush=True)
    with open(dest/'hierarchies.pkl','wb') as f:pickle.dump(hierarchies,f,protocol=5)
    write(dest/'child-search.json',search);write(dest/'final-top-fit.json',finalmeta)
    seal('train')

if __name__=='__main__':
    cfg=read(DOC/'config.json');reviewed('train')
    with Guard(cfg,'train'),threadpool_limits(limits=cfg['threads']):train_content(cfg)
