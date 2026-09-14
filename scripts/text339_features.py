"""Train-only text axes and per-user text responses, without learned user IDs."""
from __future__ import annotations
import numpy as np
from cold_item_features import Features as Structured
from text339_relations import CappedRelations
from text339_common import require

def fit_projection(vectors, mask, dimension=32):
    x=np.asarray(vectors[mask],np.float64)
    require(len(x)>dimension and np.isfinite(x).all(),'sufficient legal PCA movies')
    mean=x.mean(axis=0);x-=mean
    eigenvalues,eigenvectors=np.linalg.eigh(x.T@x/(len(x)-1))
    order=np.argsort(eigenvalues)[::-1][:dimension]
    basis=eigenvectors[:,order];scale=np.sqrt(np.maximum(eigenvalues[order],1e-12))
    for j in range(dimension):
        if basis[np.argmax(abs(basis[:,j])),j]<0:basis[:,j]*=-1
    return {'mean':mean,'basis':basis,'scale':scale}

def project(vectors,pca):
    present=np.linalg.norm(vectors,axis=1)>0
    x=np.clip((vectors-pca['mean'])@pca['basis']/pca['scale'],-5,5)/5
    x[~present]=0
    return x.astype(np.float32)

TEXT_STATS=['candidate_present','input_fraction','positive_cosine_mean','positive_cosine_max','mass_per_input',
            'effective_n','mass_reliability','shrunk_rating','shrunk_relative','rating_covariance',
            'no_evidence','input_count']

def text_features(vectors,coordinates,oi,stars,ei,strength=5.):
    oi,ei,stars=np.asarray(oi,int),np.asarray(ei,int),np.asarray(stars,float)
    k=len(oi);n=len(ei);cp=np.linalg.norm(vectors[ei],axis=1)>0
    usable=np.linalg.norm(vectors[oi],axis=1)>0
    oi=oi[usable];r=stars[usable];m=len(oi);d=coordinates.shape[1]
    mean=stars.mean() if k else 0
    if m:
        sim=np.clip(vectors[ei]@vectors[oi].T,0,1)
        mass=sim.sum(1);effective=mass**2/np.maximum((sim**2).sum(1),1e-12)
        profile=coordinates[oi].mean(0)
        response=((r-mean)[:,None]*coordinates[oi]).sum(0)/(m+strength)/5
        cov=((sim-sim.mean(1)[:,None])@(r-r.mean()))/(m+strength)/5
        extra=np.column_stack([cp,np.full(n,m/max(k,1)),sim.mean(1),sim.max(1),mass/max(k,1),
              effective/30,mass/(mass+strength),np.where(mass>0,(sim@r+strength*mean)/(mass+strength)/5,0),
              sim@(r-mean)/(mass+strength)/5,cov,mass==0,np.full(n,m/30)])
    else:
        profile=response=np.zeros(d);extra=np.zeros((n,len(TEXT_STATS)));extra[:,0]=cp;extra[:,10]=1
    x=np.column_stack([coordinates[ei],np.tile(profile,(n,1)),np.tile(response,(n,1)),extra]).astype(np.float32)
    x[~cp]=0
    require(x.shape==(n,3*d+len(TEXT_STATS)) and np.isfinite(x).all(),'finite text features')
    return x

class Features:
    def __init__(self,metadata,embeddings,projections):
        self.structured=Structured(metadata)
        self.structured.base=CappedRelations(metadata)
        self.embeddings=embeddings;self.coordinates={k:project(v,projections['overview' if k=='overview' else 'wiki']) for k,v in embeddings.items()}
        self.names=[self.structured.names[i] for i in self.structured.indices['CROWD_RESPONSE']]
        self.indices={'T0':list(range(len(self.names)))}
        for key in ['overview','T2','T3']:
            start=len(self.names)
            self.names += [f'{key}_{kind}_{j}' for kind in ['candidate_pc','input_pc','response_pc'] for j in range(32)]
            self.names += [key+'_'+s for s in TEXT_STATS]
            block=list(range(start,len(self.names)))
            if key=='overview':self.indices['T1']=self.indices['T0']+block
            else:self.indices[key]=self.indices['T1']+block
        require(len(self.names)==554 and len(self.indices['T2'])==446 and len(self.indices['T3'])==446,'feature projection sizes')

    def transform(self,oi,stars,ei):
        base=self.structured.transform(oi,stars,ei)[:,self.structured.indices['CROWD_RESPONSE']]
        return np.column_stack([base,*[text_features(self.embeddings[k],self.coordinates[k],oi,stars,ei) for k in ['overview','T2','T3']]])
