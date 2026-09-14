import unittest
import copy
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
import audit_dv2_retrieval_results as a


class IndependentAuditTests(unittest.TestCase):
    def fixture(self):
        audit=a.Audit.__new__(a.Audit)
        audit.ids=np.arange(10,19)
        audit.x=np.array([[1.,0.]]*4+[[.8,.6]]*2+[[.6,.8]]*2+[[0.,1.]])
        audit.genre=audit.x
        groups=np.array([0,0,0,0,1,1,2,2,3])
        reps=np.stack([audit.x[groups==g].mean(axis=0) for g in range(4)])
        audit.hs={'v1-fixed16':{'groups':groups,'n_groups':4,'representatives':{'mean':reps}}}
        audit.state_cache={};audit.retrieval_cache={};audit.partition_cache={}
        audit.quality=lambda tag:(np.arange(9,dtype=float),np.arange(9))
        context=dict(uid=1,cap=10,history=[0],stars=[5.],original_stars=[5.],viewed=[0,1,2,4,6],raw_pre_count=5)
        return audit,context

    def test_e_first_actual_unseen_prefix_and_next_group(self):
        audit,c=self.fixture();p=a.make_policy('v1-fixed16',budget=5,quota=1)
        candidate,s,info=audit.retrieve(c,p)
        np.testing.assert_array_equal(candidate,[5,7,8])
        np.testing.assert_array_equal(s['legal'],[1,2,3])
        self.assertEqual(info['state'],'ELIGIBLE_PREFIXES_EXHAUSTED')
        self.assertEqual(info['candidate_unseen_q_rank'],[1,1,1])

    def test_original_unmapped_star_changes_anchor_before_cap_mapping(self):
        x=np.array([[1.,0.]])
        c=dict(uid=1,cap=5,history=[0],stars=[.5],original_stars=[5.,.5],viewed=[0],raw_pre_count=2)
        _,info=a.independent_profile(x,c)
        self.assertAlmostEqual(info['anchor'],3+.5*((5+.5+17.5)/7-3))
        c.update(cap=1,history=[],stars=[],original_stars=[5.])
        self.assertEqual(a.independent_profile(x,c)[1]['state'],'UNMAPPED_INPUT')

    def test_short_and_empty_reference_denominators(self):
        x=np.eye(2)
        self.assertEqual(a.approximation([0],[0],x,np.ones(2),10)['prefix_overlap'],1)
        self.assertIsNone(a.approximation([],[],x,np.ones(2),10)['content_gap'])

    def test_all_prefixes_make_representative_order_irrelevant_to_set(self):
        pools={0:np.array([1,2]),1:np.array([3])}
        one,_=a.visit([0,1],pools,200);two,_=a.visit([1,0],pools,200)
        self.assertEqual(set(one),set(two));self.assertFalse(np.array_equal(one,two))

    def test_multi_equal_count_normalization(self):
        reps={'multi4':np.array([[[1.,0.],[1.,0.]],[[1.,0.],[0.,0.]]]),'multi_count':np.array([2,1])}
        score,count=a.score_representatives(reps,np.array([1.,0.]),'multi4',np.array([0,1]))
        np.testing.assert_allclose(score,[1.,1.]);self.assertEqual(count,3)

    def test_decision_has_no_future_metric_input(self):
        ps=[a.make_policy('v1-fixed16',budget=50),a.make_policy('GKT-K128',budget=100)]
        rows=[]
        for p,gap in zip(ps,[.04,.01]):
            rows.append({**p,'policy':p['id'],'feasible':True,'mean_absolute_gap':gap,'mean_candidates':p['budget'],'latency_p95_ms':10.})
        first=a.choose_independently(rows,ps,{'v1-fixed16':16,'GKT-K128':1024})
        rows[0]['future_ndcg10']=-100;rows[1]['future_ndcg10']=100
        self.assertEqual(first,a.choose_independently(rows,ps,{'v1-fixed16':16,'GKT-K128':1024}))
        self.assertEqual(first['candidate'],ps[0]['id'])

    def test_reference_links_reject_same_kind_wrong_constraint_or_reuse(self):
        audit,c=self.fixture();audit.base=Path('unused');audit.roles={'verification':[1,2]}
        audit.contexts={(1,10):c,(2,10):{**c,'uid':2}}
        finalists=[a.make_policy('v1-fixed16',budget=50),a.make_policy('GKT-K128',budget=100)]
        links=[];rows=[]
        for uid,ordered in [(1,finalists),(2,list(reversed(finalists)))]:
            first_global=None;actual_ids={p['id'] for p in finalists}
            for p in ordered:
                kinds=['direct_prefix','all_content','flat_q','full_predictor','global_flat_q','global_full_predictor']
                refs=[a.make_policy(p['hierarchy'],budget=p['budget'],quota=p['quota'],kind=k) for k in kinds]
                refs.extend(a.make_policy(p['hierarchy'],budget=p['budget'],quota=p['quota'],kind='random',seed=s) for s in [913,914,915])
                for ref in refs:
                    reused=ref['kind']=='global_full_predictor' and first_global is not None
                    if ref['kind']=='global_full_predictor' and first_global is None:first_global=ref['id']
                    ref_id=first_global if reused else ref['id']
                    actual_ids.add(ref_id)
                    links.append(dict(uid=uid,policy=p['id'],reference_policy=ref_id,reference_reused_for_comparison=reused))
            rows.extend(dict(uid=uid,policy=p) for p in actual_ids)
        data=pd.DataFrame(rows)
        def verify(records):
            def mocked(path):return {'finalist_policies':finalists} if path.name=='report.json' else records
            with patch.object(a,'read',mocked):audit.check_links(data)
        verify(links)
        changed=copy.deepcopy(links);changed[0]['reference_reused_for_comparison']=True
        with self.assertRaises(AssertionError):verify(changed)
        changed=copy.deepcopy(links);changed[0]['reference_policy']=a.make_policy('GKT-K128',budget=100,kind='direct_prefix')['id']
        with self.assertRaises(AssertionError):verify(changed)

    def test_prefix_drift_recomputed_and_wrong_count_rejected(self):
        audit,c=self.fixture();audit.contexts={(1,10):c};audit.fixed_mean_cache={};audit.drift_verified=0
        p=a.make_policy('v1-fixed16',budget=5,quota=1);audit.state(c,p)
        requests=pd.DataFrame([{**p,'policy':p['id'],'uid':1,'cap':10}])
        rows=[dict(uid=1,policy=p['id'],hierarchy='v1-fixed16',profile_state='VALID',group_id=g,
            actual_prefix_count=1,fixed_prefix_count=1,viewed_count=1 if g in [1,2] else 0,
            whole_to_actual_distance=0.,whole_to_fixed_distance=0.,actual_mean_similarity=score,whole_mean_similarity=score)
            for g,score in [(1,.8),(2,.6),(3,0.)]]
        audit.check_drift(requests,pd.DataFrame(rows));self.assertEqual(audit.drift_verified,3)
        rows[0]['actual_prefix_count']=2
        with self.assertRaises(AssertionError):audit.check_drift(requests,pd.DataFrame(rows))


if __name__=='__main__':unittest.main()
