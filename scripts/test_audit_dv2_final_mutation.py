import unittest
import numpy as np
import pandas as pd
from audit_dv2_final_mutation import near, profile, reconstruct, overlap, equal, quality


class AuditCounterexamples(unittest.TestCase):
    def test_lower_id_exact_geometric_tie(self):
        np.testing.assert_array_equal(near(np.array([[0., 0.], [2., 0.]]), np.array([[-1., 0.], [1., 0.]])), [0, 1])

    def test_underseen_uses_all_distinct_viewed_denominator(self):
        x = np.array([[1., 0.]] * 8 + [[0., 1.]] * 4)
        groups = np.array([0, 1, 1, 1, 1, 1, 0, 0, 2, 2, 2, 2])
        frame = pd.DataFrame({'service_movie_id': np.arange(1, 13)})
        c = dict(history=[0], stars=[5.], original_stars=[5.], viewed=[0, 1, 2, 3, 4, 5], cap=10, raw_pre_count=6)
        bundle = dict(policy=dict(kind='group', rep='mean', quota=1, budget=2), child_centers=[np.zeros((3, 2))], representatives={'mean': np.eye(3, 2)})
        _, pools, snap, _ = reconstruct(c, frame, x, groups, np.arange(12), bundle)
        self.assertEqual(snap['underseen_groups'], [0, 2])
        self.assertEqual(pools[0].tolist(), [6, 7])
        self.assertEqual(snap['candidate_ids'], [7, 9])
        wrong = dict(snap); wrong['candidate_ids'] = [7, 8]
        with self.assertRaises(AssertionError): equal(snap, wrong)

    def test_no_history_and_negative_profile(self):
        x = np.eye(2)
        c = dict(history=[], stars=[], original_stars=[], cap=10, raw_pre_count=0)
        self.assertFalse(profile(x, c)[1])
        c.update(history=[0], stars=[1.], original_stars=[1.], raw_pre_count=1)
        p, valid = profile(x, c)
        self.assertTrue(valid); np.testing.assert_array_equal(p, [-1., 0.])

    def test_order_changes_distinguished_from_set_retention(self):
        v = overlap([1, 2], [2, 1])
        self.assertEqual(v['retained_fraction'], 1.)
        self.assertFalse(v['ordered_equal'])
        self.assertEqual(v['added'], [])

    def test_quality_ties_and_tombstone(self):
        frame = pd.DataFrame(dict(service_movie_id=[3, 2, 1], raw_vote_count_number=[10, 10, 10], raw_vote_average_number=[8, 8, 8], quality_state=['VALID'] * 3, release_date=['2020-01-01'] * 3, status=['Released'] * 3, raw_adult_state=['FALSE'] * 3, raw_video_state=['FALSE'] * 3))
        b = dict(quality=dict(C=6., m=300), candidate_date='2026-09-09')
        q, ix = quality(frame, b, np.ones((3, 1)), np.array([True, False, True]))
        np.testing.assert_array_equal(ix, [2, 0])
        np.testing.assert_allclose(q, [(80 + 1800) / 310] * 3)


if __name__ == '__main__': unittest.main()
