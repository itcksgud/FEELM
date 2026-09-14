"""Nondegenerate and degenerate controls; synthetic only, no source reads."""
from copy import deepcopy
import unittest
import numpy as np
import audit_dv2_profile_controls as a


def context(stars,uid=101):
    return {'uid':uid,'cap':10,'history':list(range(len(stars))),'stars':list(stars),
            'original_stars':list(stars),'viewed':list(range(len(stars))),'raw_pre_count':len(stars)}


class ProfileControlTests(unittest.TestCase):
    def test_nondegenerate_2d_swap_shuffle_and_reverse_expected_top1(self):
        x=np.eye(2);c=context([5.,.5]);donor=context([.5,5.],102)
        reps=np.vstack([x,-x,np.array([[1.,1.],[-1.,-1.]])/np.sqrt(2)])
        legal=np.arange(6);base,info=a.profile(x,c);other,oi=a.profile(x,donor)
        shuffled,perm=a.shuffled_context(c,[1,0]);shift,si=a.profile(x,shuffled)
        ranks=[a.ranked_groups(reps,d,legal,s['state']) for d,s in [(base,info),(other,oi),(shift,si),(-base,info)]]
        self.assertEqual([int(r[0]) for r in ranks],[3,2,2,1])
        self.assertTrue(all(a.comparison(ranks[0],r,legal,'VALID')['top1_changed'] for r in ranks[1:]))
        self.assertTrue(all(not np.array_equal(ranks[0],r) for r in ranks[1:]))

    def test_nondegenerate_3d_swap_shuffle_and_reverse_expected_top1(self):
        x=np.eye(3);reps=np.vstack([x,-x]);legal=np.arange(6)
        c=context([5.,3.5,.5]);donor=context([.5,5.,3.5],102)
        base,info=a.profile(x,c);other,oi=a.profile(x,donor)
        shuffled,_=a.shuffled_context(c,[2,0,1]);shift,si=a.profile(x,shuffled)
        ranks=[a.ranked_groups(reps,d,legal,s['state']) for d,s in [(base,info),(other,oi),(shift,si),(-base,info)]]
        self.assertEqual([int(r[0]) for r in ranks],[5,3,3,2])
        self.assertTrue(all(a.comparison(ranks[0],r,legal,'VALID')['top5_overlap']<1 for r in ranks[1:]))

    def test_uniform_ratings_shuffle_is_invariant_without_forced_change(self):
        c=context([4.,4.,4.]);x=np.eye(3);shuffled,_=a.shuffled_context(c,[2,0,1])
        before,bi=a.profile(x,c);after,ai=a.profile(x,shuffled)
        np.testing.assert_array_equal(before,after);self.assertEqual(bi,ai)
        reps=np.vstack([x,-x]);legal=np.arange(6)
        one=a.ranked_groups(reps,before,legal);two=a.ranked_groups(reps,after,legal)
        self.assertFalse(a.comparison(one,two,legal,'VALID')['top1_changed'])

    def test_symmetric_identical_representatives_keep_ties_under_reverse(self):
        reps=np.tile([1.,0.],(8,1));legal=np.array([7,4,2]);direction=np.array([.8,.6])
        one=a.ranked_groups(reps,direction,legal);two=a.ranked_groups(reps,-direction,legal)
        np.testing.assert_array_equal(one,[2,4,7]);np.testing.assert_array_equal(two,one)
        self.assertFalse(a.comparison(one,two,legal,'VALID')['top5_order_changed'])

    def test_cancelled_direction_and_empty_e_are_not_forced_rank_changes(self):
        c=context([3.5,3.5]);direction,info=a.profile(np.array([[1.,0.],[-1.,0.]]),c)
        self.assertEqual(info['state'],'CANCELLED_OR_TINY');np.testing.assert_array_equal(direction,[0.,0.])
        ranks=a.ranked_groups(np.eye(2),direction,[0,1],info['state']);self.assertEqual(len(ranks),0)
        self.assertEqual(a.comparison(np.array([0,1]),ranks,[0,1],info['state'])['comparison_state'],'NO_VALID_VARIANT_VECTOR')
        for d in [np.array([1.,0.]),np.array([-1.,0.])]:
            out=a.ranked_groups(np.eye(2),d,[]);self.assertEqual(len(out),0)
            check=a.comparison([],out,[],'VALID');self.assertEqual(check['comparison_state'],'EMPTY_RECIPIENT_EU');self.assertIsNone(check['top1_changed'])

    def test_shuffle_preserves_original_anchor_unmapped_ratings_and_input(self):
        c=context([5.,.5]);c['original_stars']=[5.,.5,4.];copy=deepcopy(c)
        changed,perm=a.shuffled_context(c,[1,0]);x=np.eye(2)
        _,before=a.profile(x,c);_,after=a.profile(x,changed)
        self.assertEqual(c,copy);self.assertEqual(changed['stars'],[.5,5.]);self.assertEqual(changed['original_stars'],[5.,.5,4.])
        self.assertEqual(before['anchor'],after['anchor'])
        self.assertAlmostEqual(before['anchor'],3+.5*((9.5+17.5)/8-3))
        self.assertEqual(changed['history'],c['history']);self.assertEqual(changed['viewed'],c['viewed'])

    def test_default_seed_is_deterministic_without_rejecting_identity(self):
        c=context([5.,3.5,.5]);first,p=a.shuffled_context(c);second,q=a.shuffled_context(c)
        self.assertEqual(first,second);np.testing.assert_array_equal(p,q)
        self.assertEqual(sorted(first['stars']),sorted(c['stars']))
        one=context([4.]);same,p=a.shuffled_context(one);self.assertEqual(same,one);np.testing.assert_array_equal(p,[0])

    def test_swap_uses_recipient_e_and_actual_unseen_prefix(self):
        groups=np.array([0,0,0,0,1,2,3,4]);quality=np.arange(8)
        recipient=context([5.,.5]);recipient['viewed']=[0,1,2,3]
        donor=context([.5,5.],102);donor['viewed']=[4,5]
        legal,pools,counts=a.recipient_pools(groups,quality,recipient,25,5)
        donor_legal,_,_=a.recipient_pools(groups,quality,donor,25,5)
        np.testing.assert_array_equal(legal,[1,2,3,4]);self.assertNotEqual(legal.tolist(),donor_legal.tolist())
        self.assertEqual(pools,{1:[4],2:[5],3:[6],4:[7]});self.assertEqual(counts[0],4)
        reps=np.array([[100.,100.],[1.,0.],[0.,1.],[-1.,0.],[0.,-1.]])
        x=np.eye(2);base,bi=a.profile(x,recipient);other,oi=a.profile(x,donor)
        for direction,info in [(base,bi),(other,oi),(-base,bi)]:
            ranked=a.ranked_groups(reps,direction,legal,info['state']);self.assertTrue(set(ranked)<=set(legal));self.assertNotIn(0,ranked)
        np.testing.assert_array_equal(legal,[1,2,3,4])


if __name__=='__main__':unittest.main()
