"""Local research serving adapter: append/update/tombstone without refitting.

Historical ratings, frozen classifier and trained item factors are loaded once.
The adapter is not a deployed service/API contract. History records are supplied
latest-first; all records exclude viewed items, only first cap enter prediction.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from k8_common import assign,combine,transform,norm,signed_profile,underseen,rank
from k8_recommend import Predictor,candidates

class CatalogRuntime:
    def __init__(self,bundle,frame,interaction,cfg,selection):
        self.bundle=bundle;self.cfg=cfg;self.selection=selection
        self.frame=frame.copy().reset_index(drop=True)
        assert self.frame.service_movie_id.is_unique
        self.lookup={int(s):i for i,s in enumerate(self.frame.service_movie_id)}
        self.count=np.array(interaction['count'],copy=True);self.total=np.array(interaction['total'],copy=True)
        self.factors=np.array(interaction['factors'],copy=True);self.has=np.array(interaction['has_factor'],copy=True)
        assert len(self.count)==len(self.frame)==len(self.factors)
        self.active=np.ones(len(self.frame),bool)
        blocks,_=transform(self.frame,bundle['preprocessor'])
        self.x=combine(blocks,[.5,.35,.15]);self.group_x=combine(blocks,bundle['sub_weights'])
        labels=assign(self.frame,bundle);self.top=labels['taste_id'];self.group=labels['group_id'];self.support=labels['content_supported']
        self.weak=~labels['top_supported']|(~labels['keyword_supported']&~labels['overview_supported'])
        self.reps=norm(np.concatenate(bundle['child_centers']))
        self.refresh()
    def refresh(self):
        # Count/factor arrays retain tombstones, so catalog removal cannot alter priors.
        self.predictor=Predictor(self.x,self.factors,self.has,self.count,self.total,self.cfg)
        self.quality={'count':self.count,'mean':np.divide(self.total,self.count,out=np.full(len(self.count),self.predictor.mean),where=self.count>0),'bayes':self.predictor.bayes}
        self.index_cache={}
    def remove(self,service_ids):
        service_ids=list(service_ids)
        if any(isinstance(s,(bool,np.bool_)) or not isinstance(s,(int,np.integer)) or s<=0 for s in service_ids):
            raise ValueError('positive integer service_movie_id required')
        for s in service_ids:
            if int(s) in self.lookup:self.active[self.lookup[int(s)]]=False
        self.index_cache.clear()
    def upsert(self,records):
        if not records:return []
        new=pd.DataFrame(records)
        if 'service_movie_id' not in new or not new.service_movie_id.is_unique:raise ValueError('unique service_movie_id required')
        for s in new.service_movie_id:
            if isinstance(s,(bool,np.bool_)) or not isinstance(s,(int,np.integer)) or s<=0:raise ValueError('positive integer service_movie_id required')
        blocks,_=transform(new,self.bundle['preprocessor']);xx=combine(blocks,[.5,.35,.15]);gx=combine(blocks,self.bundle['sub_weights']);ll=assign(new,self.bundle)
        output=[]
        for j,row in enumerate(new.to_dict(orient='records')):
            s=int(row['service_movie_id']);i=self.lookup.get(s)
            if i is None:
                i=len(self.frame);self.lookup[s]=i
                self.frame=pd.concat([self.frame,pd.DataFrame([row])],ignore_index=True)
                self.x=np.vstack([self.x,xx[j]]);self.group_x=np.vstack([self.group_x,gx[j]])
                self.count=np.append(self.count,0);self.total=np.append(self.total,0)
                self.factors=np.vstack([self.factors,np.zeros(self.factors.shape[1])]);self.has=np.append(self.has,False)
                self.active=np.append(self.active,True);self.top=np.append(self.top,ll['taste_id'][j]);self.group=np.append(self.group,ll['group_id'][j])
                self.support=np.append(self.support,ll['content_supported'][j]);self.weak=np.append(self.weak,False)
            else:
                # Upsert is a complete replacement of the supplied normalized record.
                for c in self.frame.columns:self.frame.at[i,c]=row.get(c,None)
                for c,v in row.items():
                    if c not in self.frame:self.frame[c]=None
                    self.frame.at[i,c]=v
                self.x[i]=xx[j];self.group_x[i]=gx[j];self.active[i]=True
                self.top[i]=ll['taste_id'][j];self.group[i]=ll['group_id'][j];self.support[i]=ll['content_supported'][j]
            self.weak[i]=not ll['top_supported'][j] or not (ll['keyword_supported'][j] or ll['overview_supported'][j])
            output.append({'service_movie_id':s,'taste_id':int(self.top[i]),'group_id':int(self.group[i]),'weak_evidence':bool(self.weak[i])})
        self.refresh();return output
    def recommend(self,history,mode='group',cap=10,as_of='2026-09-09'):
        if mode not in ['group','flat']:raise ValueError('mode must be group or flat')
        if cap not in [0,1,5,10,30]:raise ValueError('supported history cap required')
        for r in history:
            s=r['service_movie_id']
            if isinstance(s,(bool,np.bool_)) or not isinstance(s,(int,np.integer)) or s<=0:raise ValueError('positive integer service_movie_id required')
        hs=[int(r['service_movie_id']) for r in history]
        stars=np.asarray([r['rating'] for r in history],float)
        if len(set(hs))!=len(hs) or not np.isfinite(stars).all() or ((stars<.5)|(stars>5)).any() or not np.allclose(stars*2,np.rint(stars*2),rtol=0,atol=1e-12):
            raise ValueError('unique rated movies and half-star ratings in[.5,5] required')
        known=[j for j,s in enumerate(hs) if s in self.lookup]
        viewed=np.asarray([self.lookup[hs[j]] for j in known],int)
        scoring=[j for j in known if j<cap]
        h=np.asarray([self.lookup[hs[j]] for j in scoring],int);ratings=stars[scoring]
        seen=np.zeros(len(self.frame),bool);seen[viewed]=True
        date=pd.to_datetime(self.frame.get('release_date',pd.Series('',index=self.frame.index)),utc=True,errors='coerce')
        status=self.frame.get('status',pd.Series('',index=self.frame.index)).fillna('')
        adult=self.frame.get('adult',pd.Series(False,index=self.frame.index)).fillna(False).astype(bool)
        video=self.frame.get('video',pd.Series(False,index=self.frame.index)).fillna(False).astype(bool)
        eligible=self.active&self.support&date.notna().to_numpy()&(date<=pd.Timestamp(as_of,tz='UTC')).to_numpy()&status.eq('Released').to_numpy()&~adult.to_numpy()&~video.to_numpy()
        p=self.selection['group' if mode=='group' else 'baseline']
        ix=np.flatnonzero(eligible);ids=self.frame.service_movie_id.to_numpy(dtype=np.int64)
        cache_key=(as_of,p['quality'])
        if cache_key not in self.index_cache:
            order=ix[rank(self.quality[p['quality']][ix],ids[ix])]
            self.index_cache[cache_key]=(order,[order[self.group[order]==g] for g in range(len(self.reps))])
        order,group_lists=self.index_cache[cache_key]
        under,_=underseen(self.group,viewed,len(self.reps),self.cfg['underseen_max_count'],self.cfg['underseen_max_share'])
        profile=signed_profile(self.group_x,h,ratings,self.predictor.mean,self.cfg['profile_prior_mass'])
        cand,extra=candidates(p,order,group_lists,self.reps,profile,under,seen,ids)
        params=self.predictor.user(h,ratings);scores=self.predictor.predict(params,cand);take=rank(scores,ids[cand])[:10]
        result=[]
        for q in take:
            i=int(cand[q]);result.append({'service_movie_id':int(ids[i]),'predicted_rating':float(scores[q]),'taste_id':int(self.top[i]),'group_id':int(self.group[i]),
                'weak_evidence':bool(self.weak[i]),'underseen':bool(under[self.group[i]]),'prediction_path':'ALS' if self.has[i] and params[2] is not None else 'content_fallback'})
        return {'model_version':self.bundle['version'],'items':result,'candidate_count':len(cand),**extra}
