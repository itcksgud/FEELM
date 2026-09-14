"""Local research runtime: frozen criteria, append/tombstone, strict discovery."""
from __future__ import annotations
import operator
import pickle
import platform
import numpy as np
import pandas as pd
import scipy
import sklearn
from dv2_common import transform,combine,nearest,array_hash,rank,CFG
from dv2_retrieve import Engine
from dv2_predictor import ServicePredictor,load_assets

RULE_KEYS=['underseen_count','underseen_share','profile_prior','profile_prior_mean','profile_epsilon','multi_temperature','return_k']

def runtime_versions():
    return {'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,'sklearn':sklearn.__version__}

def integer_ids(values):
    result=[]
    for v in values:
        if isinstance(v,(bool,np.bool_)):raise ValueError('service IDs must be positive integers, excluding bool')
        try:i=operator.index(v)
        except TypeError as e:raise ValueError('service IDs must be integers') from e
        if i<=0:raise ValueError('service IDs must be positive')
        result.append(i)
    if len(set(result))!=len(result):raise ValueError('duplicate service IDs')
    return result

def classify(frame,bundle):
    if bundle['runtime_versions']!=runtime_versions():raise ValueError('frozen numerical runtime version mismatch')
    if 'service_movie_id' in frame:integer_ids(frame.service_movie_id)
    blocks,_=transform(frame,bundle['preprocessor'])
    top=nearest(combine(blocks,bundle['top_weights']),bundle['top_centers'])[0]
    sx=combine(blocks,bundle['sub_weights']);child=np.zeros(len(frame),int)
    for parent,centers in enumerate(bundle['child_centers']):
        ix=np.flatnonzero(top==parent);child[ix]=nearest(sx[ix],centers)[0]
    offsets=np.cumsum([0]+[len(c) for c in bundle['child_centers']]);group=offsets[top]+child
    x=combine(blocks,bundle['content_weights'])
    return {'taste_id':top,'child_id':child,'group_id':group,'content':x,'genre':blocks[0],
            'top_supported':np.sum(combine(blocks,bundle['top_weights'])**2,axis=1)>1e-12,'content_supported':np.sum(x*x,axis=1)>1e-12}

def fixed_quality(frame,bundle,x,active):
    qinfo=bundle['quality'];r=frame.raw_vote_average_number.to_numpy(float);v=frame.raw_vote_count_number.to_numpy(float)
    valid=frame.quality_state.eq('VALID').to_numpy();q=np.full(len(frame),np.nan)
    if not (np.isfinite(r[valid]).all() and np.isfinite(v[valid]).all() and np.all((r[valid]>0)&(r[valid]<=10)) and np.all(v[valid]>0) and np.all(v[valid]==np.floor(v[valid]))):
        raise ValueError('VALID quality state requires valid original R/v evidence')
    q[valid]=(v[valid]*r[valid]+qinfo['m']*qinfo['C'])/(v[valid]+qinfo['m'])
    date=pd.to_datetime(frame.release_date,format='%Y-%m-%d',errors='coerce')
    eligible=(date.notna()&(date<=pd.Timestamp(bundle['candidate_date']))&frame.status.eq('Released')&frame.raw_adult_state.eq('FALSE')&frame.raw_video_state.eq('FALSE')).to_numpy()
    legal=np.flatnonzero(eligible&valid&active&(np.sum(x*x,axis=1)>1e-12));ids=frame.service_movie_id.to_numpy()
    order=legal[np.lexsort((ids[legal],-v[legal],-q[legal]))]
    return q,order

class FrozenCatalog:
    """Tombstones retain historical vectors/factors. No group is refit on mutation.

    Inputs are prepared normalized metadata rows, using dv2_prepare's raw contract.
    Ratings are supplied newest-first; caller owns authentic timestamps/viewing.
    This is a local research interface, not a deployed endpoint.
    """
    def __init__(self,frame,bundle,assets=None):
        self.bundle=bundle;self.frame=frame.copy().reset_index(drop=True)
        integer_ids(self.frame.service_movie_id)
        self._rules()
        self.active=np.ones(len(frame),bool);self.assets=assets or load_assets(CFG['source_root'])
        self.assigned=classify(self.frame,self.bundle);self._index()

    def _index(self):
        self._rules()
        self.lookup={int(i):j for j,i in enumerate(self.frame.service_movie_id)}
        q,order=fixed_quality(self.frame,self.bundle,self.assigned['content'],self.active)
        h={'groups':self.assigned['group_id'],'n_groups':sum(len(c) for c in self.bundle['child_centers']),
           'representatives':self.bundle['representatives'],'space_hashes':self.bundle['source_space_hashes']}
        self.engine=Engine(h,self.assigned['content'],self.assigned['genre'],self.frame,order,q)
        self.predictor=ServicePredictor(self.frame,self.assets)

    def _rules(self):
        if self.bundle['runtime_rules']!={k:CFG[k] for k in RULE_KEYS}:raise ValueError('runtime rule configuration differs from frozen bundle')

    def append(self,frame):
        self._rules()
        new=frame.copy().reset_index(drop=True);ids=integer_ids(new.service_movie_id)
        if not len(new):return
        if set(ids)&set(self.lookup):raise ValueError('append requires fresh service IDs')
        assigned=classify(new,self.bundle)
        # Construct/validate candidate metadata index first, so failures are atomic.
        combined=pd.concat([self.frame,new],ignore_index=True)
        predictor=ServicePredictor(combined,self.assets)
        fixed_quality(new,self.bundle,assigned['content'],np.ones(len(new),bool))
        self.frame=combined;self.active=np.concatenate([self.active,np.ones(len(new),bool)])
        self.assigned={k:np.concatenate([self.assigned[k],v],axis=0) for k,v in assigned.items()}
        self._index()

    def remove(self,service_ids):
        self._rules()
        ids=integer_ids(service_ids)
        if not set(ids)<=set(self.lookup):raise ValueError('unknown removal ID; batch is atomic')
        indices=[self.lookup[i] for i in ids];self.active[indices]=False
        # Metadata remains for past viewing/rating input; only current candidate order changes.
        q,order=fixed_quality(self.frame,self.bundle,self.assigned['content'],self.active)
        h=self.engine.h;self.engine=Engine(h,self.assigned['content'],self.assigned['genre'],self.frame,order,q)

    def recommend(self,ratings,viewed_service_ids,cap=10,budget=None):
        self._rules()
        if isinstance(cap,bool) or not isinstance(cap,int) or cap not in [0,1,5,10,30]:raise ValueError('supported cap required')
        ids=integer_ids([r['service_movie_id'] for r in ratings])
        if any(isinstance(r['stars'],(bool,np.bool_)) or not isinstance(r['stars'],(int,float,np.integer,np.floating)) for r in ratings):raise ValueError('ratings must be numeric half-stars, excluding bool')
        stars=np.asarray([r['stars'] for r in ratings],float)
        if not np.isin(stars,np.arange(1,11)/2).all():raise ValueError('exact half-stars required')
        viewed=integer_ids(viewed_service_ids)
        if not set(ids)<=set(viewed):raise ValueError('rated movies must be included in viewed history')
        ids=ids[:cap];stars=stars[:cap]
        supported=[j for j,i in enumerate(ids) if i in self.lookup]
        h=[self.lookup[ids[j]] for j in supported];r=stars[supported].tolist()
        c={'uid':0,'cap':cap,'history':h,'stars':r,'original_stars':stars.tolist(),'original_input_count':len(ids),
           'viewed':[self.lookup[i] for i in viewed if i in self.lookup],'raw_pre_count':len(viewed)}
        p=dict(self.bundle['policy'])
        if budget is not None:
            if isinstance(budget,bool) or not isinstance(budget,int) or budget<=0 or budget>p['budget']:raise ValueError('positive integer budget cannot exceed the frozen policy budget')
            p['budget']=budget
        candidates,info,_=self.engine.retrieve(c,p)
        if not len(candidates):return {'state':info['state'],'profile':info['profile'],'movies':[],'predicted_movies':0,'global_fill':0,'review_status':self.bundle.get('review_status')}
        result=self.predictor.predict({'cap':cap,'oi':h,'stars':r,'viewed':c['viewed']},candidates,self.bundle['predictor'])
        order=rank(result['prediction'],self.frame.service_movie_id.to_numpy()[candidates])[:self.bundle['runtime_rules']['return_k']]
        movies=[]
        for j in order:
            ix=int(candidates[j]);movies.append({'service_movie_id':int(self.frame.service_movie_id.iloc[ix]),'taste_id':int(self.assigned['taste_id'][ix]),
              'group_id':int(self.assigned['group_id'][ix]),'predicted_rating':float(result['rating_clipped'][j]),'ranking_score':float(result['prediction'][j]),
              'Q':float(self.engine.q[ix]),'tmdb_vote_count':int(self.frame.raw_vote_count_number.iloc[ix]),'ml_train_count':int(result['train_count'][j]),'model_branch':str(result['branch'][j])})
        return {'state':info['state'],'profile':info['profile'],'movies':movies,'predicted_movies':len(candidates),'global_fill':0,
                'als_rows':result['als_rows_computed'],'gbt_rows':result['gbt_rows_computed'],'research_decision':self.bundle['research_decision'],
                'review_status':self.bundle.get('review_status')}
