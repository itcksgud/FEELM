import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from feelm_discovery_policies import select_policy,quality_bounds,opportunity


def select(scores, roles, policy='T3_GUARD', excluded=()):
    t=np.array([r=='T' for r in roles]);d=np.array([r=='D' for r in roles])
    e=np.zeros(len(roles),dtype=bool);e[list(excluded)]=True
    return select_policy(np.arange(len(roles)),scores,t,d,e,policy)


class DiscoveryTests(unittest.TestCase):
    def test_connected_and_guard_only_change_third_slot(self):
        a=select([5,4,3,2],['T','T','T','D'],'CONNECTED')
        b=select([5,4,3,2],['T','T','T','D'])
        self.assertEqual(a['selected'],[0,1,3]);self.assertEqual(b['selected'],[0,1,2])
        self.assertEqual(a['taste2'],b['taste2']);self.assertEqual(b['status'],'D_SCORE_GUARD_T3')
    def test_equal_score_allows_discovery(self):
        r=select([5,4,3,3],['T','T','T','D'])
        self.assertEqual(r['status'],'T2_D1');self.assertEqual(r['guard_D_count'],1)
    def test_reference_uses_pool_after_exclusion(self):
        r=select([5,4,3,2,1],['T','T','T','D','T'],excluded=[2])
        self.assertEqual(r['reference_position'],4);self.assertEqual(r['selected'],[0,1,3])
    def test_no_reference_abstains_as_incomplete(self):
        a=select([5,4,3],['T','T','D'],'CONNECTED')
        b=select([5,4,3],['T','T','D'])
        self.assertTrue(a['valid_three']);self.assertFalse(b['valid_three'])
        self.assertEqual(b['selected'],[0,1]);self.assertEqual(b['status'],'NO_T3_REFERENCE')
    def test_taste_shortage_has_priority(self):
        r=select([5,4,3],['D','T','D'])
        self.assertEqual(r['status'],'INSUFFICIENT_TASTE');self.assertEqual(r['selected'],[1])
    def test_no_discovery_fallback_and_no_fallback(self):
        self.assertEqual(select([3,2,1],['T']*3)['status'],'NO_DISCOVERY_T3')
        self.assertEqual(select([3,2],['T']*2)['status'],'NO_DISCOVERY_NO_T3')
    def test_candidate_outside_pool_cannot_be_promoted(self):
        t=np.array([True,True,False,True]);d=~t;e=np.zeros(4,dtype=bool)
        r=select_policy([0,1,2],[5,4,3],t,d,e,'T3_GUARD')
        self.assertIsNone(r['reference_position']);self.assertNotIn(3,r['selected'])
    def test_invalid_order_scores_and_overlap_rejected(self):
        for args in [([0,0],[3,2],[True],[False],[False]),
                     ([0,1],[2,3],[True,True],[False,False],[False,False]),
                     ([0],[1],[True],[True],[False])]:
            with self.assertRaises(ValueError):select_policy(*args,'CONNECTED')
    def test_unknown_is_bounded_but_absent_is_unavailable(self):
        r=quality_bounds([.8,None],2)
        self.assertEqual((r['mean_low'],r['mean_high']),(.4,.9))
        self.assertEqual((r['min_low'],r['min_high']),(0,.8))
        self.assertEqual((r['harm_low'],r['harm_high']),(0,1))
        self.assertFalse(quality_bounds([],1)['available'])
        self.assertEqual(quality_bounds([None],1)['harm_high'],1)
    def test_known_harm_and_boundary_are_exact(self):
        r=quality_bounds([.2,None],2)
        self.assertEqual((r['harm_low'],r['harm_high']),(1,1))
        self.assertEqual(quality_bounds([.9,.8],2)['min_low'],.8)
    def test_opportunity_unknown_is_not_no_good_candidate(self):
        self.assertEqual(opportunity([],10)['high_existence'],'HIGH_EXISTENCE_UNKNOWN')
        self.assertEqual(opportunity([],0)['high_existence'],'NO_ELIGIBLE')
        self.assertEqual(opportunity([.8],10)['high_existence'],'OBSERVED_HIGH_EXISTS')
        self.assertEqual(opportunity([.2],1)['high_existence'],'FULLY_LABELED_NO_HIGH')
        self.assertEqual(opportunity([.3],1)['known_nonlow'],1)
    def test_loss_reason_stops_at_reported_candidate_stage(self):
        from rec_ev_041_discovery import absence_reason
        counts={s:{'D_before':1,'D_after':1} for s in ['CATALOG','ALS_SUPPORTED','RAW500','TOP100']}
        counts['TOP100']={'D_before':0,'D_after':0}
        self.assertEqual(absence_reason(counts,'after','RAW500'),'D_AVAILABLE')
        self.assertEqual(absence_reason(counts,'after','TOP100'),'LOST_AT_100')
        counts['RAW500']['D_after']=0
        self.assertEqual(absence_reason(counts,'after','RAW500'),'LOST_AT_500')


if __name__=='__main__':unittest.main()
