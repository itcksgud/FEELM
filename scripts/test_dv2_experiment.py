import unittest
import numpy as np
from dv2_experiment import choose,predictor_choice,policy,predict_rows,run_one,policy_summary
from dv2_retrieve import Engine
import pandas as pd

class SelectionTests(unittest.TestCase):
    def example(self):
        ps=[policy('a',B=50),policy('b',B=100)]
        summary=[{'policy':p['id'],'hierarchy':p['hierarchy'],'rep':p['rep'],'budget':p['budget'],'quota':p['quota'],'mean_absolute_gap':.01,'mean_candidates':p['budget'],'latency_p95_ms':10.,'feasible':True} for p in ps]
        return ps,summary,{'a':{'n_groups':16},'b':{'n_groups':2048}}
    def test_no_future_metric_in_discovery_selection(self):
        ps,s,hs=self.example();a=choose(s,ps,hs)
        s[0]['future_ndcg10']=-100;s[1]['future_ndcg10']=100
        self.assertEqual(a,choose(s,ps,hs));self.assertEqual(a['candidate'],ps[0]['id'])
    def test_no_feasible_is_hold_but_large_k_kept(self):
        ps,s,hs=self.example()
        for row in s:row['feasible']=False
        a=choose(s,ps,hs);self.assertIsNone(a['candidate']);self.assertEqual(set(a['per_hierarchy']),{'a','b'})
        self.assertEqual(a['status'],'NO_FEASIBLE_POLICY_HOLD')
    def test_per_k_uses_absolute_not_signed_gap(self):
        ps=[policy('a','mean'),policy('a','norm')]
        s=[{'policy':p['id'],'hierarchy':'a','rep':p['rep'],'budget':100,'quota':25,'mean_absolute_gap':gap,'mean_candidates':100,'latency_p95_ms':10,'feasible':True} for p,gap in zip(ps,[.2,.01])]
        s[0]['mean_signed_gap']=-.2;s[1]['mean_signed_gap']=.01
        self.assertEqual(choose(s,ps,{'a':{'n_groups':16}})['per_hierarchy']['a'],ps[1]['id'])
    def predictor_rows(self):
        return [{'variant':v,'macro_mse':1.,'low_rows':40,'low_users':12,'low_macro_mse':1.,'original_als_low20_top1':10 if v=='original' else 5} for v in ['original','shrink20','shrink100','min20']]
    def test_predictor_strongest_qualifying_shrink(self):
        s=self.predictor_rows();self.assertEqual(predictor_choice(s)['selected'],'shrink100')
        s[2]['macro_mse']=1.03;self.assertEqual(predictor_choice(s)['selected'],'shrink20')
    def test_low_evidence_never_silent_pass(self):
        s=self.predictor_rows();s[0]['low_users']=9
        a=predictor_choice(s);self.assertEqual(a['selected'],'original');self.assertIn('UNDERDETERMINED',a['status'])
        s=self.predictor_rows();s[0]['original_als_low20_top1']=0
        self.assertEqual(predictor_choice(s)['selected'],'original')
    def test_support_exposure_cannot_pass_without_reduction(self):
        s=self.predictor_rows()
        for row in s:row['original_als_low20_top1']=10
        self.assertEqual(predictor_choice(s)['selected'],'original')
    def test_actual_scoring_chunk_count(self):
        class Spy:
            def __init__(self):self.calls=[]
            def predict(self,c,a,variant):
                self.calls.append(list(a));n=len(a)
                return {**{key:np.ones(n) for key in ['prediction','rating_clipped','train_count','has_factor','als_available','als_weight']},'branch':np.array(['ALS']*n),'als_rows_computed':n,'gbt_rows_computed':0}
        spy=Spy();c={'cap':10,'history':[],'stars':[],'viewed':[]};indices=np.arange(8193)
        r=predict_rows(spy,c,indices,'original');self.assertEqual(len(spy.calls),2)
        self.assertEqual(r['predicted_movies'],8193);self.assertEqual(r['als_rows'],8193)
        self.assertEqual(spy.calls[1],[8192]);self.assertEqual(len(r['prediction']),8193)
    def test_empty_summary_and_predictor_evidence(self):
        self.assertEqual(policy_summary([]),[])
        self.assertEqual(predictor_choice([])['selected'],'original')
    def test_integration_same_candidates_unclipped_rank_and_support(self):
        x=np.array([[1.,0.],[1.,0.],[1.,0.],[1.,0.],[.7,.7],[.7,.7]])
        groups=np.array([0,0,0,0,1,1]);reps=np.stack([x[groups==g].mean(axis=0) for g in range(2)])
        frame=pd.DataFrame({'service_movie_id':np.arange(10,16),'taste_id':[0,0,0,0,0,0],
                            'raw_vote_count_number':[100,100,100,100,3,1000],
                            'keyword_ids':[[] for _ in range(6)],'collection_ids':[[] for _ in range(6)]})
        h={'groups':groups,'n_groups':2,'representatives':{'mean':reps}}
        engine=Engine(h,x,x,frame,np.arange(6),np.arange(6)+6.)
        c={'uid':1,'cap':10,'history':[0],'stars':[5.],'original_stars':[5.],'viewed':[0,1,2],
           'raw_pre_count':3,'target':[4],'ratings':[4.]}
        class Spy:
            train_count=np.array([100,100,100,100,8,100])
            def predict(self,c,a,variant):
                pred=np.array([6. if i==4 else 7. for i in a]);n=len(a)
                return {'prediction':pred,'rating_clipped':np.clip(pred,.5,5),'train_count':self.train_count[a],
                        'has_factor':np.ones(n,bool),'als_available':np.ones(n,bool),'als_weight':np.ones(n),
                        'branch':np.array(['ALS']*n),'als_rows_computed':n,'gbt_rows_computed':0}
        data={'engines':{'a':engine},'predictor':Spy(),'frame':frame}
        a,d=run_one(data,c,policy('a',B=2,quota=2),'original',np.array([4,5]))
        self.assertEqual(d['candidate_indices'],[4,5]);self.assertEqual(a['ranked'],[5,4])
        self.assertEqual(a['ranked_prediction'],[7.,6.]);self.assertEqual(a['low_ml20_slots'],1)
        self.assertEqual(a['low_ml20_top1'],0);self.assertEqual(a['observed_slots'],1)
        self.assertEqual(a['global_fill'],0);self.assertEqual(a['prefix_overlap'],1.)

if __name__=='__main__':unittest.main()
