"""Post-freeze acceptance of catalog additions/deletions and serving edge paths."""
import hashlib
import pickle
import numpy as np
import pandas as pd
from k8_common import OUT,DOC,read,write,pin,Guard
from k8_assign import load_bundle
from serve_fixed_k8 import CatalogRuntime
from threadpoolctl import threadpool_limits

def main():
    cfg=read(DOC/'config.json')
    bundle=load_bundle(OUT/'final');frame=pd.read_parquet(OUT/'prepare/catalog.parquet')
    state=np.load(OUT/'recommend/interaction-model.npz')
    selection=read(OUT/'recommend/selection.json')
    modelhash=hashlib.sha256(pickle.dumps(bundle,protocol=5)).hexdigest()
    rt=CatalogRuntime(bundle,frame,state,cfg,selection)
    labels=rt.top.copy();groups=rt.group.copy();prior=rt.predictor.mean
    nohistory=rt.recommend([])
    assert nohistory['candidate_count']<=selection['group']['budget']
    assert len({r['service_movie_id'] for r in nohistory['items']})==len(nohistory['items'])
    fresh=int(frame.service_movie_id.max())+1
    # Empty content is classifiable with evidence flag, but not recommendation-eligible.
    empty=rt.upsert([{'service_movie_id':fresh,'status':'Released','release_date':'2020-01-01','genre_ids':None,'keyword_ids':None,'overview':None}])
    assert empty[0]['weak_evidence'] and not rt.support[-1]
    example=frame.loc[frame.genre_present&frame.keyword_present].iloc[0].to_dict();example['service_movie_id']=fresh+1
    example['status']='Released';example['release_date']='2020-01-01';example['adult']=False;example['video']=False
    added=rt.upsert([example])
    assert rt.has[-1]==False and rt.count[-1]==0
    np.testing.assert_array_equal(rt.top[:len(frame)],labels);np.testing.assert_array_equal(rt.group[:len(frame)],groups)
    assert rt.predictor.mean==prior
    # Unknown newest record consumes its input slot, matching capped-then-map evaluation.
    unknown={'service_movie_id':fresh+999,'rating':5.}
    older={'service_movie_id':int(frame.service_movie_id.iloc[0]),'rating':.5}
    assert rt.recommend([unknown,older],cap=1)['items']==rt.recommend([unknown,older],cap=0)['items']
    for invalid in [{'service_movie_id':1.9,'rating':3.},{'service_movie_id':True,'rating':3.},{'service_movie_id':1,'rating':3.00001}]:
        try:rt.recommend([invalid])
        except ValueError:pass
        else:raise AssertionError('invalid history accepted')
    rt.upsert([frame.iloc[0].to_dict()])
    np.testing.assert_array_equal(rt.top[:len(frame)],labels);np.testing.assert_array_equal(rt.group[:len(frame)],groups)
    deleted=[r['service_movie_id'] for r in nohistory['items']]
    rt.remove(deleted)
    after=rt.recommend([])
    assert not set(deleted)&{r['service_movie_id'] for r in after['items']}
    assert rt.predictor.mean==prior
    np.testing.assert_array_equal(rt.top[:len(frame)],labels);np.testing.assert_array_equal(rt.group[:len(frame)],groups)
    hist=[{'service_movie_id':int(frame.service_movie_id.iloc[0]),'rating':.5},{'service_movie_id':fresh+1,'rating':5.}]
    for mode in ['group','flat']:
        for cap in [0,1,5,10,30]:
            response=rt.recommend(hist,mode=mode,cap=cap)
            assert not {r['service_movie_id'] for r in hist}&{r['service_movie_id'] for r in response['items']}
            assert response['candidate_count']<=selection['group' if mode=='group' else 'baseline']['budget']
            assert all(.5<=r['predicted_rating']<=5 for r in response['items'])
    # All remaining active catalog is marked seen, retaining true empty response.
    allseen=[{'service_movie_id':int(s),'rating':3.} for s in rt.frame.service_movie_id]
    assert rt.recommend(allseen)['items']==[]
    rt.remove(rt.frame.service_movie_id.tolist());assert rt.recommend([])['items']==[]
    assert hashlib.sha256(pickle.dumps(bundle,protocol=5)).hexdigest()==modelhash
    write(OUT/'serving-checks.json',{'status':'PASS','all_original_assignments_preserved':len(frame),'new_empty':empty,'new_content':added,
          'checks':['empty_history','new_missing_content','new_content_zero_factor','same_input_upsert','catalog_delete','negative_rating_seen','all_caps','all_seen','empty_active_catalog','no_model_mutation','frozen_prior'],
          'code':{p:pin(__import__('pathlib').Path(__file__).with_name(p)) for p in ['serve_fixed_k8.py','check_fixed_k8_serving.py']}})

if __name__=='__main__':
    cfg=read(DOC/'config.json')
    with Guard(cfg,'serving'),threadpool_limits(limits=cfg['threads']):main()
