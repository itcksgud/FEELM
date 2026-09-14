import copy
import unittest
import numpy as np
import pandas as pd
from dv2_common import CFG,frozen_bundle,array_hash
from dv2_runtime import FrozenCatalog,classify,runtime_versions,RULE_KEYS,integer_ids
from test_dv2_predictor import fixture
from dv2_freeze import rule_digest

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        frame,assets,_=fixture();frame['overview']='';frame['release_date']='2020-01-01';frame['status']='Released'
        frame['raw_adult_state']='FALSE';frame['raw_video_state']='FALSE';frame['quality_state']='VALID'
        frame['raw_vote_average_number']=frame.tmdb_vote_average;frame['raw_vote_count_number']=frame.tmdb_vote_count
        source=frozen_bundle();b=copy.copy(source)
        b.update(runtime_versions=runtime_versions(),content_weights=CFG['content_weights'],runtime_rules={k:CFG[k] for k in RULE_KEYS},
                 candidate_date=CFG['candidate_date'],quality={'C':6.3,'m':300},predictor='original',research_decision='SYNTHETIC_ONLY',
                 policy={'id':'synthetic','kind':'group','rep':'mean','budget':10,'quota':5})
        a=classify(frame,b);n=sum(len(c) for c in b['child_centers']);x=a['content']
        reps=np.stack([x[a['group_id']==g].mean(axis=0) if np.any(a['group_id']==g) else np.zeros(x.shape[1]) for g in range(n)])
        b['representatives']={'mean':reps};b['source_space_hashes']={'GKT':array_hash(x),'G':array_hash(a['genre'])}
        self.frame=frame;self.bundle=b;self.model=FrozenCatalog(frame,b,assets)
    def test_append_delete_criteria_and_C_fixed(self):
        old_groups=self.model.assigned['group_id'].copy();old_q=self.model.engine.q.copy();old_rep=self.bundle['representatives']['mean'].copy()
        add=self.frame.iloc[[0]].copy();add['service_movie_id']=1001;add['mapping_status']='UNMATCHED';add['movielens_movie_id']=np.nan
        add['raw_vote_average_number']=10.;add['raw_vote_count_number']=100000
        self.model.append(add)
        np.testing.assert_array_equal(self.model.assigned['group_id'][:-1],old_groups)
        np.testing.assert_array_equal(self.model.engine.q[:-1],old_q)
        np.testing.assert_array_equal(self.bundle['representatives']['mean'],old_rep)
        self.model.remove([10]);self.assertNotIn(0,self.model.engine.order)
        self.assertEqual(self.model.lookup[10],0);self.assertTrue(self.model.predictor.has_factor[0])
    def test_failed_mutations_atomic(self):
        active=self.model.active.copy()
        with self.assertRaises(ValueError):self.model.remove([10,999999])
        np.testing.assert_array_equal(active,self.model.active)
        add=self.frame.iloc[[0]].copy()
        with self.assertRaises(ValueError):self.model.append(add)
        self.assertEqual(len(self.model.frame),len(self.frame))
        add['service_movie_id']=1001;add['mapping_status']='UNMATCHED';add['movielens_movie_id']=np.nan;add['raw_vote_count_number']=-1
        with self.assertRaises(ValueError):self.model.append(add)
        self.assertEqual(len(self.model.frame),len(self.frame))
    def test_no_vector_states_and_cap_before_mapping(self):
        self.assertEqual(self.model.recommend([],[],10)['profile']['state'],'ACTUAL_NO_HISTORY')
        self.assertEqual(self.model.recommend([{'service_movie_id':10,'stars':5.}],[10],0)['profile']['state'],'HIDDEN_CAP0')
        ratings=[{'service_movie_id':9999,'stars':.5},{'service_movie_id':10,'stars':5.}]
        result=self.model.recommend(ratings,[9999,10],1)
        self.assertEqual(result['profile']['state'],'UNMAPPED_INPUT')
        self.assertEqual(result['profile']['original_inputs'],1)
    def test_invalid_identity_rating_budget_rejected(self):
        for value in [True,1.2,0,-1,'10']:
            with self.assertRaises(ValueError):integer_ids([value])
        with self.assertRaises(ValueError):self.model.recommend([{'service_movie_id':10,'stars':True}],[10])
        with self.assertRaises(ValueError):self.model.recommend([{'service_movie_id':10,'stars':4.2}],[10])
        with self.assertRaises(ValueError):self.model.recommend([{'service_movie_id':10,'stars':4.}],[10],budget=11)
        with self.assertRaises(ValueError):self.model.recommend([{'service_movie_id':10,'stars':4.}],[])
    def test_runtime_mismatch_rejected(self):
        bad={**self.bundle,'runtime_versions':{**self.bundle['runtime_versions'],'numpy':'other'}}
        with self.assertRaises(ValueError):classify(self.frame,bad)
        bad={**self.bundle,'runtime_rules':{**self.bundle['runtime_rules'],'underseen_count':99}}
        with self.assertRaises(ValueError):FrozenCatalog(self.frame,bad,self.model.assets)
    def test_short_supply_does_not_global_fill(self):
        result=self.model.recommend([{'service_movie_id':10,'stars':5.}],[10,12,14,16,18],10)
        self.assertLessEqual(result['predicted_movies'],10);self.assertEqual(result['global_fill'],0)
        self.assertTrue(all(r['service_movie_id'] not in [10,12,14,16,18] for r in result['movies']))
    def test_material_digest_covers_complete_policy_and_runtime(self):
        baseline=rule_digest(self.bundle)
        changes={'policy':{**self.bundle['policy'],'budget':9},'predictor':'shrink100','candidate_date':'2026-01-01',
                 'runtime_versions':{**self.bundle['runtime_versions'],'numpy':'other'},'tie_rule':'changed',
                 'source_space_hashes':{'G':'other','GKT':'other'},'review_status':{'check_feasible':False}}
        for key,value in changes.items():
            with self.subTest(key=key):self.assertNotEqual(baseline,rule_digest({**self.bundle,key:value}))

if __name__=='__main__':unittest.main()
