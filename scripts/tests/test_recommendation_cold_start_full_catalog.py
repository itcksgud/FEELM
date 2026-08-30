from __future__ import annotations
import unittest
import numpy as np
from recommendation_cold_start_full_catalog import blend_scores, rank_of_positive

class ColdFullCatalogTest(unittest.TestCase):
    def test_blend_endpoints_do_not_create_nan_from_excluded_seen(self):
        pop=np.asarray([1.0,-np.inf]); fold=np.asarray([2.0,-np.inf])
        np.testing.assert_array_equal(blend_scores(pop,fold,0.0),pop)
        np.testing.assert_array_equal(blend_scores(pop,fold,1.0),fold)
        self.assertTrue(np.isneginf(blend_scores(pop,fold,0.2)[1]))
    def test_positive_is_ranked_naturally_not_injected(self):
        universe=np.asarray([1,2,3]); scores=np.asarray([3.0,2.0,1.0])
        self.assertEqual(3,rank_of_positive(universe,scores,3))
        self.assertIsNone(rank_of_positive(universe,scores,4))
if __name__=="__main__": unittest.main()
