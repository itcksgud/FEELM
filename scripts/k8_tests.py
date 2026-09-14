"""Meaningful synthetic edge/candidate/metric tests; no source data needed."""
import unittest
import numpy as np
from k8_common import nearest,underseen,signed_profile
from k8_common import transform
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from k8_recommend import candidates,metrics,Predictor

class Tests(unittest.TestCase):
    def test_heterogeneous_new_records(self):
        tv=TfidfVectorizer().fit(['red blue','blue green'])
        prep={'genres':[1],'keywords':[2],'keyword_idf':np.ones(1),'keyword_components':np.ones((1,1)),
              'text_vectorizer':tv,'text_components':np.ones((1,3))}
        a=pd.DataFrame([{'genre_ids':[1],'keyword_ids':[2],'overview':'red'},{'service_movie_id':2}])
        blocks,_=transform(a,prep)
        self.assertTrue(all(np.all(b[1]==0) for b in blocks))
    def test_exact_tie(self):
        self.assertEqual(nearest(np.array([[0.,0.]]),np.array([[1.,0.],[-1.,0.]]))[0][0],0)
    def test_order_batch_delete_append(self):
        x=np.random.default_rng(2).normal(size=(81,13));c=x[:8]
        l=nearest(x,c)[0]
        np.testing.assert_array_equal(l,nearest(x,c,batch=1)[0])
        np.testing.assert_array_equal(l[::-1],nearest(x[::-1],c,batch=7)[0])
        np.testing.assert_array_equal(l[:30],nearest(x[:30],c)[0])
        np.testing.assert_array_equal(l,nearest(np.vstack([x,x*2]),c)[0][:81])
    def test_negative_and_zero_history(self):
        x=np.eye(2)
        self.assertTrue(np.all(signed_profile(x,[],[],3.5)==0))
        p=signed_profile(x,[0,1],[.5,5],3.5)
        self.assertLess(p[0],0);self.assertGreater(p[1],0)
        under,count=underseen(np.array([0,1,1]),[1,2],2)
        self.assertTrue(under[0]);self.assertFalse(under[1]);self.assertEqual(count[1],2)
    def test_candidate_no_repeat_and_empty(self):
        order=np.arange(8);seen=np.array([True,False,False,False,False,False,False,False])
        pol={'kind':'group','budget':5,'probes':2,'quota':2}
        result,extra=candidates(pol,order,[np.array([0,1,2,3]),np.array([4,5,6,7])],np.eye(2),np.ones(2),np.array([True,True]),seen,order)
        self.assertEqual(len(result),5);self.assertEqual(len(set(result)),5);self.assertNotIn(0,result);self.assertEqual(extra['fill'],1)
        result,_=candidates(pol,order,[order],np.ones((1,2)),np.ones(2),np.array([False]),np.ones(8,bool),order)
        self.assertEqual(len(result),0)
    def test_known_gain_and_unknown(self):
        m=metrics(np.array([0,2]),np.array([0,2]),np.array([0,1]),np.array([4,5]),np.zeros(3,int),np.array([True]),np.eye(3),[])
        self.assertAlmostEqual(m['ndcg2'],1/(2+1/np.log2(3)))
        self.assertEqual(m['recall10'],.5);self.assertEqual(m['candidate_recall'],.5);self.assertEqual(m['unknown_share'],.5)
        self.assertEqual(m['underseen_share'],.2)
    def test_predictor_no_history_and_rating_bounds(self):
        cfg={'bayes_mass':20,'profile_prior_mass':5,'ridge_alpha':5,'als_reg':.1}
        p=Predictor(np.eye(3),np.zeros((3,2)),np.zeros(3,bool),np.array([2.,0,1]),np.array([8.,0,2]),cfg)
        u=p.user([],[]);np.testing.assert_allclose(p.predict(u,np.arange(3)),p.bayes)
        u=p.user([0],[.5]);pred=p.predict(u,np.arange(3));self.assertTrue(((pred>=.5)&(pred<=5)).all())

if __name__=='__main__':unittest.main()
