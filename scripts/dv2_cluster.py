"""Frozen top8, finite child experiments including requested K128/256."""
import hashlib
import pickle
import time
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from threadpoolctl import threadpool_limits
from dv2_common import *

def fit(x,k,seed):
    distinct=len(np.unique(x,axis=0));actual=min(k,distinct,len(x))
    if actual==0:return np.zeros((1,x.shape[1])),{'requested_k':k,'actual_k':1,'supported':0,'distinct':0,'iterations':0,'empty_ids':1,'seconds':0.}
    start=time.perf_counter()
    model=KMeans(n_clusters=actual,n_init=CFG['kmeans_n_init'],max_iter=CFG['kmeans_max_iter'],algorithm='lloyd',random_state=seed).fit(x)
    centers=canonical(model.cluster_centers_)
    return centers,{'requested_k':k,'actual_k':actual,'supported':len(x),'distinct':distinct,'iterations':int(model.n_iter_),
                    'empty_ids':actual-len(np.unique(model.labels_)),'inertia':float(model.inertia_),'seconds':time.perf_counter()-start}

def build_representatives(x,groups,ids,n_groups,q):
    dimension=x.shape[1];means=np.zeros((n_groups,dimension));medoids=means.copy();qmeans=means.copy()
    multi=np.zeros((n_groups,CFG['multi_representatives'],dimension));multi_count=np.zeros(n_groups,int)
    supported=np.sum(x*x,axis=1)>1e-12;diagnostics=[];medoid_ids=np.full(n_groups,-1,int)
    hash_order=np.array([int.from_bytes(hashlib.sha256(f'dv2-rep:{int(i)}'.encode()).digest()[:8],'big') for i in ids],dtype=np.uint64)
    for g in range(n_groups):
        allix=np.flatnonzero(groups==g);ix=allix[supported[allix]]
        if not len(ix):
            diagnostics.append({'group_id':g,'members':len(allix),'supported':0,'rep_count':0,'medoid_service_id':None,'fit_members':0,'mode_masses':[],'spread':None});continue
        means[g]=x[ix].mean(axis=0)
        distance=np.sum((x[ix]-means[g])**2,axis=1);j=ix[np.lexsort((ids[ix],distance))[0]]
        medoids[g]=x[j];medoid_ids[g]=ids[j]
        good=ix[np.isfinite(q[ix])]
        if len(good):qmeans[g]=np.average(x[good],axis=0,weights=q[good])
        sub=ix[np.argsort(hash_order[ix],kind='stable')[:CFG['representative_fit_max_rows']]]
        centers,meta=fit(x[sub],CFG['multi_representatives'],CFG['seed']+g)
        r=len(centers);multi[g,:r]=centers;multi_count[g]=r
        labels=nearest(x[ix],centers)[0];masses=np.bincount(labels,minlength=r)
        sample_masses=np.bincount(nearest(x[sub],centers)[0],minlength=r)
        distances=np.sum((centers[:,None,:]-centers[None,:,:])**2,axis=2)
        diagnostics.append({'group_id':g,'members':len(allix),'supported':len(ix),'rep_count':r,'medoid_service_id':int(ids[j]),
                            'fit_members':len(sub),'fit_sample_id_hash':array_hash(ids[sub]),'fit_mode_masses':sample_masses.tolist(),
                            'mode_masses':masses.tolist(),'spread':float(distances.mean()),'mean_norm':float(np.linalg.norm(means[g]))})
    return {'mean':means,'norm':norm(means),'medoid':medoids,'qmean':qmeans,'multi4':multi,'multi_count':multi_count,'medoid_ids':medoid_ids},diagnostics

def cluster():
    verify('prepare');dest=require_fresh('cluster','report.json')
    frame=pd.read_parquet(OUT/'prepare/catalog.parquet');ids=frame.service_movie_id.to_numpy()
    x=np.load(OUT/'prepare/content.npy');genre=np.load(OUT/'prepare/genre.npy');top=np.load(OUT/'prepare/top.npy');q=np.load(OUT/'prepare/quality.npy')
    eligible=np.zeros(len(x),bool);eligible[np.load(OUT/'prepare/quality-order.npy')]=True
    supported=np.sum(x*x,axis=1)>1e-12;oldbundle=frozen_bundle();oldassign=pd.read_parquet(OLDOUT/'final/assignments.parquet')
    assert pin(OLDOUT/'final/assignments.parquet')['sha256']=='095c157daa77ffdd6d09fd4f33341b51bed81890f57095492112b5ef75a54708'
    assert np.array_equal(ids,oldassign.service_movie_id) and np.array_equal(top,oldassign.taste_id)
    hierarchies={};fitrows=[];group_rows=[];rep_rows=[]
    space_hashes={'GKT':array_hash(x),'G':array_hash(genre)}
    definitions=[('v1-fixed16',None)]+[(f'GKT-K{k}',k) for k in CFG['child_k']]
    for name,k in definitions:
        print('HIERARCHY_START',name,flush=True)
        centers=[];labels=np.zeros(len(x),int)
        if k is None:
            centers=oldbundle['child_centers'];groups=oldassign.group_id.to_numpy(int);weights=oldbundle['sub_weights']
            child=oldassign.child_id.to_numpy(int)
        else:
            weights=CFG['content_weights']
            for parent in range(8):
                mask=(top==parent)&supported
                cc,meta=fit(x[mask],k,CFG['seed']+parent);centers.append(cc)
                fitrows.append({'hierarchy':name,'parent':parent,**meta});print('FIT',name,parent,meta,flush=True)
            offsets=np.cumsum([0]+[len(c) for c in centers]);child=np.zeros(len(x),int)
            for parent,cc in enumerate(centers):
                ix=np.flatnonzero(top==parent);child[ix]=nearest(x[ix],cc)[0]
            groups=offsets[top]+child
        n_groups=sum(len(c) for c in centers)
        reps,diag=build_representatives(x,groups,ids,n_groups,q)
        rep_rows.extend([{'hierarchy':name,**d} for d in diag])
        offsets=np.cumsum([0]+[len(c) for c in centers])
        for g in range(n_groups):
            ix=np.flatnonzero(groups==g);good=ix[supported[ix]];active=ix[eligible[ix]]
            parent=int(np.searchsorted(offsets[1:],g,side='right'))
            group_rows.append({'hierarchy':name,'group_id':g,'taste_id':parent,'child_id':int(g-offsets[parent]),'members':len(ix),'supported':len(good),
                               'eligible':len(active),'tmdb_low20':int((frame.raw_vote_count_number.iloc[active]<=20).sum()),
                               'ko':int(frame.original_language.iloc[active].eq('ko').sum()),'KR':int(frame.production_country_codes.iloc[active].map(lambda a:'KR' in a).sum()),
                               'distinct_supported_vectors':len(np.unique(x[good],axis=0)) if len(good) else 0})
        if name=='v1-fixed16':
            genre_supported=np.sum(genre*genre,axis=1)>1e-12
            reps['genre_support_counts']=np.bincount(groups[genre_supported],minlength=n_groups)
            reps['genre_mean']=np.stack([genre[(groups==g)&genre_supported].mean(axis=0) if np.any((groups==g)&genre_supported) else np.zeros(genre.shape[1]) for g in range(n_groups)])
        h={'name':name,'requested_per_taste':k,'centers':centers,'sub_weights':weights,'groups':groups,'children':child,
           'representatives':reps,'n_groups':n_groups,'offsets':offsets,'geometry_version':CFG['version'],'space_hashes':space_hashes}
        hierarchies[name]=h
        np.savez_compressed(dest/(name+'-assignments.npz'),service_movie_id=ids,taste_id=top,child_id=child,group_id=groups)
        with (dest/(name+'-hierarchy.pkl')).open('wb') as f:pickle.dump(h,f,protocol=5)
        print('HIERARCHY_DONE',name,n_groups,flush=True)
    pd.DataFrame(group_rows).to_parquet(dest/'groups.parquet',index=False)
    write(dest/'representative-diagnostics.json',rep_rows);write(dest/'fits.json',fitrows)
    write(dest/'report.json',{'hierarchies':{name:{'n_groups':h['n_groups'],'groups_hash':array_hash(h['groups']),
                            'centers_hash':[array_hash(c) for c in h['centers']],'mean_hash':array_hash(h['representatives']['mean'])} for name,h in hierarchies.items()},
                            'small_group_rule':'No statistical minimum100; all feasible K trained and retained','top_assignment_hash':array_hash(top),
                            'fit_count':len(fitrows),'fit_seconds':sum(r['seconds'] for r in fitrows)})
    seal('cluster')

if __name__=='__main__':
    reviewed('cluster',['dv2_cluster.py'])
    with Guard('cluster'),threadpool_limits(limits=CFG['threads']):cluster()
