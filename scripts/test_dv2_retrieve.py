import unittest
import numpy as np
import pandas as pd
from dv2_retrieve import Engine,comparison,gather,representative_scores
from dv2_common import profile

class RetrievalTests(unittest.TestCase):
    def fixture(self):
        groups=np.array([0,0,0,0,1,1,2,2,3])
        x=np.array([[1.,0.]]*4+[[.8,.6]]*2+[[.6,.8]]*2+[[0.,1.]])
        reps=np.stack([x[groups==g].mean(axis=0) for g in range(4)])
        h={'groups':groups,'n_groups':4,'representatives':{'mean':reps}}
        frame=pd.DataFrame({'service_movie_id':np.arange(10,19)})
        engine=Engine(h,x,x,frame,np.arange(9),np.arange(9))
        c={'uid':1,'cap':10,'history':[0],'stars':[5.],'original_stars':[5.],'viewed':[0,1,2,4,6],'raw_pre_count':5}
        return engine,c
    def test_e_first_and_next_group_no_fill(self):
        e,c=self.fixture();a,info,s=e.retrieve(c,{'budget':5,'quota':1,'rep':'mean'})
        self.assertEqual(a.tolist(),[5,7,8]);self.assertEqual(info['global_fill'],0)
        self.assertNotIn(0,info['legal_groups']);self.assertNotIn(3,a)
        self.assertEqual(info['state'],'ELIGIBLE_PREFIXES_EXHAUSTED')
    def test_seen_prefix_skip_and_exact_budget(self):
        e,c=self.fixture();a,info,s=e.retrieve(c,{'budget':2,'quota':2,'rep':'mean'})
        self.assertEqual(a.tolist(),[5,7]);self.assertEqual(len(a),2)
    def test_no_vector_not_personalized(self):
        e,c=self.fixture();c.update(cap=0,history=[],stars=[],original_stars=[])
        a,info,s=e.retrieve(c,{'budget':2,'quota':2})
        self.assertEqual(len(a),0);self.assertEqual(info['profile']['state'],'HIDDEN_CAP0')
        self.assertTrue(s['seen'][0])
    def test_all_same_high_ratings_can_be_valid(self):
        e,c=self.fixture();p,d=profile(e.x,c)
        self.assertEqual(d['state'],'VALID');self.assertGreater(p[0],0)
    def test_profile_states(self):
        x=np.array([[0.,0.],[1.,0.],[-1.,0.]])
        base={'uid':1,'cap':10,'history':[],'stars':[],'original_stars':[],'viewed':[],'raw_pre_count':0}
        self.assertEqual(profile(x,base)[1]['state'],'ACTUAL_NO_HISTORY')
        base.update(raw_pre_count=1,original_stars=[4.]);self.assertEqual(profile(x,base)[1]['state'],'UNMAPPED_INPUT')
        base.update(history=[0],stars=[4.]);self.assertEqual(profile(x,base)[1]['state'],'UNSUPPORTED_INPUT')
        base.update(history=[1,2],stars=[4.,4.],original_stars=[4.,4.]);self.assertEqual(profile(x,base)[1]['state'],'CANCELLED_OR_TINY')
    def test_short_pair_not_penalized_as_disagreement(self):
        x=np.eye(2);r=comparison([0],[0],x,np.array([1.,0.]),10)
        self.assertEqual(r['prefix_overlap'],1);self.assertEqual(r['candidate_supply'],.1)
        r=comparison([],[],x,np.ones(2),10);self.assertIsNone(r['content_gap'])
    def test_multi_count_normalization(self):
        reps={'multi4':np.array([[[1.,0.],[1.,0.]],[[1.,0.],[0.,0.]]]),'multi_count':np.array([2,1])}
        a,n=representative_scores(reps,np.array([1.,0.]),'multi4')
        np.testing.assert_allclose(a,[1.,1.]);self.assertEqual(n,3)
    def test_random_reproducible_and_no_global_fill(self):
        e,c=self.fixture();p={'budget':10,'quota':2,'kind':'random','seed':913}
        a,_,_=e.retrieve(c,p);b,_,_=e.retrieve(c,p)
        np.testing.assert_array_equal(a,b);self.assertEqual(set(a),{5,7,8})
    def test_eligibility_exhaustion(self):
        e,c=self.fixture();c['viewed']=list(range(9))
        a,d,_=e.retrieve(c,{'budget':10,'quota':2});self.assertEqual(len(a),0)
        self.assertEqual(d['state'],'NO_ELIGIBLE_DISCOVERY_GROUP')
    def test_genre_and_gkt_have_different_dimensions(self):
        e,c=self.fixture();e.x=np.column_stack([e.x,np.zeros(len(e.x))])
        e.h['representatives']['mean']=np.column_stack([e.h['representatives']['mean'],np.zeros(e.n)])
        e.h['representatives']['genre_mean']=np.stack([e.genre[e.groups==g].mean(axis=0) for g in range(e.n)])
        a,info,state=e.retrieve(c,{'budget':2,'quota':2,'space':'G','rep':'mean'})
        self.assertEqual(a.tolist(),[5,7]);self.assertEqual(len(state['p']),2)
        a,info,state=e.retrieve(c,{'budget':2,'quota':2,'space':'GKT','rep':'mean'})
        self.assertEqual(len(state['p']),3)
    def test_direct_prefix_order_reversal_and_ties(self):
        e,c=self.fixture()
        e.h['representatives']['mean'][1]=[0.,1.]
        a,_,_=e.retrieve(c,{'budget':2,'quota':1,'kind':'group'})
        b,info,s=e.retrieve(c,{'budget':2,'quota':1,'kind':'direct_prefix'})
        self.assertEqual(a.tolist(),[7,5]);self.assertEqual(b.tolist(),[5,7])
        self.assertEqual(info['content_comparisons'],3)
        e.x[7]=e.x[5]
        b,info,_=e.retrieve(c,{'budget':2,'quota':1,'kind':'direct_prefix'})
        self.assertEqual(b.tolist(),[5,7])
    def test_duplicate_viewed_rejected(self):
        e,c=self.fixture();c['viewed'].append(4)
        with self.assertRaises(AssertionError):e.state(c,2)
    def test_global_reference_does_not_build_group_prefixes(self):
        e,c=self.fixture();e.lists=None
        a,info,state=e.retrieve(c,{'budget':2,'quota':2,'kind':'global_flat_q'})
        self.assertEqual(a.tolist(),[3,5]);self.assertFalse(info['E_u_applicable']);self.assertEqual(state['pools'],{})

if __name__=='__main__':unittest.main()
