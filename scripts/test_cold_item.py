import unittest
from unittest.mock import patch
from copy import deepcopy
import numpy as np
import pandas as pd
from cold_item_features import Features
from cold_item_common import movie_partition, concordance, check_contexts

def movies(n=30):
    return pd.DataFrame([dict(movie_id=i+1,tmdb_id=i+101,feature_eligible=True,genre_ids=[28],keyword_ids=[i%3],
        production_country_codes=['US'],original_language='en',release_year=2000,runtime_minutes=100,
        director_ids=[7],top5_cast_ids=[11],production_company_ids=[3],collection_ids=[],
        tmdb_vote_average=2+6*i/max(n-1,1),tmdb_vote_count=10**(i%5),tmdb_popularity=float(i)) for i in range(n)])

class Contract(unittest.TestCase):
    def test_base_parity_and_projection(self):
        f=Features(movies()); x=f.transform([0,1,2,3,4],[1,2,3,4,5],[6,7])
        np.testing.assert_array_equal(x[:,:168],f.base.transform([0,1,2,3,4],[1,2,3,4,5],[6,7]).toarray())
        self.assertEqual([len(f.indices[k]) for k in f.indices],[168,200,206,230])

    def test_shrink_is_toward_input_mean(self):
        a=movies();a.at[1,'director_ids']=[8]
        f=Features(a);x=f.transform([0,1,2,3,4],[5,1,1,1,1],[6])
        b=f.base_names.index('DIRECTOR_weighted_relative_stars')
        self.assertLess(abs(x[0,168+b]),abs(x[0,b]))
        n=f.base_names.index('DIRECTOR_weighted_stars')
        self.assertLess(abs(x[0,168+n]-1.8/5),abs(x[0,n]-1.8/5))

    def test_no_links_and_one_input(self):
        a=movies();a.at[6,'director_ids']=[99]
        f=Features(a);x=f.transform([0],[5],[6])
        b=f.base_names.index('DIRECTOR_weighted_stars')
        self.assertEqual(x[0,168+b],0)
        for block in ['GENRE','DIRECTOR','CAST']:
            b=f.base_names.index(block+'_weighted_relative_stars')
            self.assertEqual(x[0,168+b],0)
        self.assertEqual(x[0,f.names.index('crowd_rating_rating_covariance')],0)

    def test_crowd_direction_and_missing(self):
        a=movies();f=Features(a)
        pos=f.transform([0,1,2,3,4],[1,2,3,4,5],[6])
        neg=f.transform([0,1,2,3,4],[5,4,3,2,1],[6])
        col=f.names.index('crowd_rating_response_cross')
        self.assertGreater(pos[0,col],0);self.assertLess(neg[0,col],0)
        a['tmdb_vote_average']=np.nan;a['tmdb_vote_count']=0
        f=Features(a);x=f.transform([0],[5],[6])
        self.assertEqual(x[0,f.names.index('crowd_rating_present')],0)
        self.assertEqual(x[0,f.names.index('crowd_rating_candidate_delta')],0)
        self.assertTrue(np.isfinite(x).all())

    def test_constant_crowd_no_claimed_covariance(self):
        a=movies();a['tmdb_vote_average']=7
        f=Features(a);x=f.transform([0,1,2,3,4],[1,2,3,4,5],[6])
        self.assertAlmostEqual(x[0,f.names.index('crowd_rating_rating_covariance')],0)

    def test_partition_invariant_to_row_order(self):
        a=movies();counts=np.full(len(a),20)
        p=movie_partition(a,counts)
        order=np.arange(len(a))[::-1]
        q=movie_partition(a.iloc[order].reset_index(drop=True),counts[order])
        np.testing.assert_array_equal(p,q[order])
        self.assertEqual((p=='E').sum(),3);self.assertEqual((p=='V').sum(),3)

    def test_pair_ties(self):
        score,n=concordance([1,2,2],[3,3,4]);self.assertEqual(n,2);self.assertEqual(score,.75)
        self.assertEqual(concordance([2,2],[1,3]),(None,0))

    def test_context_rejects_time_leak_and_duplicate(self):
        cat={'movie_ids':np.array([1,2]),'partition':np.array(['W','E']),'reference_counts':np.array([1,1])}
        c={'uid':1,'k':1,'start':0,'stop':1,'oi':[0],'ei':[1],'stars':[3.5],
           'input_timestamps':[199],'target_timestamps':[201],'pre_warm':1}
        with patch('cold_item_common.config',return_value={'origin':200}),patch('cold_item_common.role',return_value='evaluation'):
            self.assertEqual(check_contexts([c],cat),1)
            bad=deepcopy(c);bad['input_timestamps']=[200]
            with self.assertRaises(RuntimeError):check_contexts([bad],cat)
            with self.assertRaises(RuntimeError):check_contexts([c,c],cat)
            bad=deepcopy(c);bad['ei']=[0]
            with self.assertRaises(RuntimeError):check_contexts([bad],cat)

if __name__=='__main__':unittest.main()
