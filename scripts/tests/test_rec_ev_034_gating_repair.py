"""Synthetic tests for one eligibility alternative; model scoring is forbidden."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import numpy as np
import pandas as pd

SCRIPTS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SCRIPTS))
SPEC=importlib.util.spec_from_file_location('repair',SCRIPTS/'rec_ev_034_gating_repair.py')
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)


class GatingRepairTests(unittest.TestCase):
    def features(self,n):
        return m.base.inverted_features([{'genres':[10],'directors':[],'keywords':[]} for _ in range(n)])

    def test_positive_one_negative_many_and_superset(self):
        tokens,inv=self.features(34);labels=np.array([0]*32+[1,1]);observed=np.arange(30)
        weights=np.array([.9]+[-.1]*29)
        old_t,old_d,old_a,info=m.base.eligibility(labels,observed,weights,tokens,inv)
        t,d,a,new=m.support_masks(labels,observed,weights,tokens,inv)
        self.assertFalse(old_t.any());self.assertEqual(info['positive_tastes'],0)
        self.assertEqual(new['support_tastes'],1);self.assertEqual(a.tolist(),[0]);self.assertTrue(t[30]);self.assertTrue(d[32])
        self.assertFalse((old_t&~t).any());self.assertFalse((old_d&~d).any())
        self.assertEqual(m.no_support_reason(0,1,1),'MEAN_CANCELLATION')

    def test_zero_mean_no_positive_and_unassigned_partition(self):
        tokens,inv=self.features(5);labels=np.array([0,0,-1,1,0]);observed=np.array([0,1,2])
        for weights,reason in [(np.array([1.,-1.,0.]),'MEAN_CANCELLATION'),
                               (np.array([-1.,0.,0.]),'NO_POSITIVE_OBSERVATIONS'),
                               (np.array([-1.,0.,1.]),'ONLY_UNASSIGNED_POSITIVE')]:
            _,_,_,info=m.support_masks(labels,observed,weights,tokens,inv)
            self.assertEqual(m.no_support_reason(0,info['positive_observations'],info['assigned_positive_observations']),reason)
        self.assertEqual(m.no_support_reason(1,1,1),'ORIGINAL_SUPPORTED')

    def test_same_namespace_and_experienced_flavor_not_discovery(self):
        rows=[{'genres':[28],'directors':[],'keywords':[]},{'genres':[],'directors':[],'keywords':[28]},
              {'genres':[28],'directors':[],'keywords':[]},{'genres':[28],'directors':[],'keywords':[]}]
        tokens,inv=m.base.inverted_features(rows)
        t,d,_,_=m.support_masks(np.array([0,1,0,-1]),np.array([0]),np.array([1.]),tokens,inv)
        self.assertFalse(d.any());self.assertTrue(t[2]);self.assertFalse(t[3])

    def test_expansion_can_displace_D_at_fixed100(self):
        # The numerical limits are reduced to make the same fixed-budget effect visible.
        raw=np.arange(6);excluded=np.zeros(6,bool)
        old_t=np.array([True,True,False,False,False,False]);old_d=np.array([False,False,False,True,False,False])
        new_t=np.array([True,True,True,False,False,False]);new_d=old_d.copy()
        old=m.base.stage_orders(raw,old_t,old_d,limit500=6,limit100=3)[0]
        new=m.base.stage_orders(raw,new_t,new_d,limit500=6,limit100=3)[0]
        self.assertEqual(m.base.select_slots(old['TOPN100'],old_t,old_d,excluded)[0],'T2_D1')
        self.assertEqual(m.base.select_slots(new['TOPN100'],new_t,new_d,excluded)[0],'NO_DISCOVERY_T3')
        self.assertEqual(m.base.select_slots(new['CANDIDATE500'],new_t,new_d,excluded)[0],'T2_D1')

    def test_new_slot_provenance_is_separate_from_quality(self):
        tokens,inv=self.features(5);ids=np.arange(1,6)
        count,needed,facts=m.describe_slots(ids,[2,3,4],['TASTE','TASTE','DISCOVERY'],np.zeros(5,bool),np.array([],int),np.array([0]),tokens)
        self.assertEqual(count,2);self.assertTrue(needed);self.assertEqual(facts,[{'anchor_movie_id':1,'field':'genres','id':10}])

    def test_two_phases_reuse_and_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            run=m.Run.__new__(m.Run);run.root=Path(temp);run.identity={'fixture':'one'};run.cfg={'inputs':{}}
            run.guard=lambda:None;run.budget=Mock();run.budget.elapsed.return_value=0
            run.ids=np.arange(1,641);labels=np.array([0]*320+[1]*320);run.labels={'A_GENRE':labels,'B_KMEANS':labels.copy()}
            run.tokens,run.inverted=self.features(640);observed=np.arange(30);excluded=np.zeros(640,bool);excluded[observed]=True
            run.requests=[('synthetic-a',observed,np.array([.9]+[-.1]*29),excluded),
                          ('synthetic-b',observed,np.full(30,-.5),excluded.copy())]
            run.raw=np.tile(np.arange(500),(2,1));refs=[];refslots=[]
            for u,(key,o,w,e) in enumerate(run.requests):
                for name,label in run.labels.items():
                    t,d,a,info=m.base.eligibility(label,o,w,run.tokens,run.inverted)
                    rec={'user_key':key,'policy':name,**info,'catalogue_T':int(t.sum()),'catalogue_D':int(d.sum())}
                    stages,_,_=m.base.stage_orders(run.raw[u],t,d)
                    for stage in ('CANDIDATE500','TOPN100'):
                        status,pos,kinds,tc,dc=m.base.select_slots(stages[stage],t,d,e)
                        suffix='500' if stage=='CANDIDATE500' else '100'
                        rec.update({f'status{suffix}':status,f'T{suffix}':tc,f'D{suffix}':dc})
                        refslots.append({'user_key':key,'policy':name,'stage':stage,'status':status,'movie_ids':run.ids[pos],
                                         'types':kinds,'D_connection_facts_json':'[]'})
                    refs.append(rec)
            run.reference=pd.DataFrame(refs).set_index(['user_key','policy'])
            run.reference_slots=pd.DataFrame(refslots).set_index(['user_key','policy','stage'])
            run.seal('role-seal.json',(),sources={})
            with patch.object(m.base,'fold_scores',side_effect=AssertionError('ALS scoring forbidden')):
                run.baseline();self.assertTrue((run.root/'baseline-seal.json').exists());run.compare()
            s=m.read_json(run.root/'summary.json');self.assertFalse(s['quality_evaluated']);self.assertEqual(s['new_ALS_scores'],0)
            for p in s['by_policy']:
                self.assertEqual(p['original_no_support_reasons'],{'MEAN_CANCELLATION':1,'NO_POSITIVE_OBSERVATIONS':1})
                self.assertEqual(p['alternative_status_counts'],{'NO_DISCOVERY_T3':1,'INSUFFICIENT_TASTE':1})
            m.write_json(run.root/'budget.json',{'fixture':True});run.seal('completion-seal.json',m.OUTPUTS,sources={})
            run.sources=Mock();run.load=Mock(side_effect=AssertionError('reuse loaded'));run.baseline=Mock();run.compare=Mock()
            run.run();run.load.assert_not_called();run.baseline.assert_not_called();run.compare.assert_not_called()
            (run.root/'baseline-check.json').write_text('{}')
            with self.assertRaises(RuntimeError):run.run()

    def test_missing_baseline_and_partial_failure_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            run=m.Run.__new__(m.Run);run.root=Path(temp);run.identity={};run.cfg={'inputs':{}};run.guard=lambda:None
            with self.assertRaises(FileNotFoundError):run.compare()
            (run.root/'partial').write_text('preserve')
            with self.assertRaisesRegex(RuntimeError,'partial execution'):run.run()
            (run.root/'failure.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'failed execution'):run.run()


if __name__=='__main__':unittest.main()
