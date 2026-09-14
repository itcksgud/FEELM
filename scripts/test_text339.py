"""Boundary and independent expected-value checks for Jira339."""
from __future__ import annotations
import unittest
import numpy as np
import pandas as pd
from text339_text import clean_t3,select_page,chunk_ids,validate_cache
from text339_features import fit_projection,project,text_features
from text339_evaluate import quality,paired_interval,catalog_quality

class TextTests(unittest.TestCase):
    def test_quote_body_not_citation(self):
        self.assertEqual(clean_t3('{{Quote|A man returns home.|author=Reviewer}}')[0],'A man returns home.')
        self.assertEqual(clean_t3('{{인용|title=Review|author=Someone|url=https://example.org}}')[0],'')
        self.assertEqual(clean_t3('<ref>{{Quote|A review}}</ref>The hero leaves.')[0],'The hero leaves.')
    def test_nested_links_and_spacing(self):
        self.assertEqual(clean_t3('{{인용 상자|quote=[[집|고향]]{{nbsp}}으로 돌아간다.}}')[0],'고향 으로 돌아간다.')
    def test_ambiguous_prose_rejected(self):
        for raw in ['{{Quote|A|text=B}}','{{Quote|text=A|text=B}}','{{Quote|A|1=B}}','{{Quote|{{unreviewed|A}}}}','{{Quote|A']:
            with self.assertRaises(ValueError):clean_t3(raw)
    def test_cleaning_does_not_change_source_language(self):
        rows={'ko':{'plot_wikitext':'{{Quote|국문 줄거리}}','plot_text':''},'en':{'plot_wikitext':'English story','plot_text':'English story'}}
        self.assertIs(select_page(rows),rows['ko'])
    def test_complete_chunk_coverage_and_mass(self):
        for size in [0,1,479,480,481,912,913,10000]:
            chunks=chunk_ids(list(range(size)));seen=set(v for ids,_ in chunks for v in ids)
            self.assertEqual(seen,set(range(size)));self.assertEqual(sum(w for _,w in chunks),size)
            self.assertTrue(all(len(ids)<=480 and w>0 for ids,w in chunks))

class FeatureTests(unittest.TestCase):
    def test_capped_history_adapter_all_lengths(self):
        from rec047_features import Relations
        from text339_relations import CappedRelations
        from text339_features import Features
        meta=pd.DataFrame([{'genre_ids':[28,18],'keyword_ids':[i%3],'production_country_codes':['KR'],'original_language':'ko',
                            'release_year':2000+i%10,'runtime_minutes':90,'director_ids':[i%4],'top5_cast_ids':[i%5],
                            'production_company_ids':[1],'collection_ids':[],'tmdb_vote_average':7.,'tmdb_vote_count':100,'tmdb_popularity':10.} for i in range(40)])
        old,new=Relations(meta),CappedRelations(meta)
        rng=np.random.default_rng(9);v=rng.normal(size=(40,768)).astype('float32');v/=np.linalg.norm(v,axis=1)[:,None]
        pc={'mean':np.zeros(768),'basis':np.eye(768)[:,:32],'scale':np.ones(32)}
        feature=Features(meta,{k:v for k in ['overview','T2','T3']},{'overview':pc,'wiki':pc})
        for k in range(31):
            oi=np.arange(k);stars=(np.arange(k)%10+1)/2;ei=np.arange(30,40)
            x=feature.transform(oi,stars,ei);self.assertEqual(x.shape,(10,554));self.assertTrue(np.isfinite(x).all())
            if k in [0,1,5,10,30]:np.testing.assert_array_equal(old.transform(oi,stars,ei).toarray(),new.transform(oi,stars,ei).toarray())
    def test_projection_ignores_excluded_movies(self):
        rng=np.random.default_rng(339);x=rng.normal(size=(80,12));mask=np.arange(80)<60
        a=fit_projection(x,mask,4);x[~mask]*=10000;b=fit_projection(x,mask,4)
        for key in a:np.testing.assert_array_equal(a[key],b[key])
        np.testing.assert_array_equal(project(np.zeros((1,12)),a),np.zeros((1,4)))
    def test_missing_text_is_zero_and_order_invariant(self):
        rng=np.random.default_rng(1);v=rng.normal(size=(20,768)).astype('float32');v/=np.linalg.norm(v,axis=1)[:,None];v[4]=0
        pc=rng.normal(size=(20,32)).astype('float32');pc[4]=0
        a=text_features(v,pc,[1,2],[.5,5],[3,4]);b=text_features(v,pc,[2,1],[5,.5],[3,4])
        np.testing.assert_allclose(a,b,atol=1e-7);np.testing.assert_array_equal(a[1],np.zeros(108))
    def test_zero_input_and_constant_ratings(self):
        v=np.zeros((3,768),np.float32);v[0,0]=v[1,1]=1;pc=np.zeros((3,32),np.float32)
        self.assertTrue(np.isfinite(text_features(v,pc,[],[],[0,1,2])).all())
        a=text_features(v,pc,[0,1],[4,4],[0]);self.assertEqual(float(a[0,104]),0) # shrunk relative

class MetricTests(unittest.TestCase):
    def test_hand_calculated_ranking(self):
        m=quality([.5,5,4,2],[4,3,4,2],[10,20,30,40])
        for key,value in {'ndcg2':.3291846272125626,'stars2':2.25,'hit2':1,'precision2':.5,'recall2':.5,'low2':.5,'any_low2':1,'mse':4.0625,'mae':1.375,'pa':2.5/6}.items():self.assertAlmostEqual(m[key],value)
        self.assertNotIn('ndcg6',m)
    def test_metric_specific_missing(self):
        m=quality([.5,.5],[1,2],[10,20]);self.assertTrue(np.isnan(m['ndcg2']));self.assertTrue(np.isnan(m['recall2']))
        self.assertEqual(m['stars2'],.5);self.assertEqual(m['low2'],1);self.assertEqual(m['precision2'],0)
        with self.assertRaises(RuntimeError):quality([.5,5],[1,np.nan],[10,20])
    def test_bootstrap_constants_and_small_n(self):
        np.testing.assert_allclose(paired_interval(np.full(30,.1),.05/3),[.1,.1])
        np.testing.assert_array_equal(paired_interval(np.zeros(30),.05/3),[0,0])
        self.assertEqual(paired_interval(np.ones(29)),[None,None])
    def test_unknown_and_shortage_are_different(self):
        m=catalog_quality([.5,4,np.nan,np.nan],4)
        self.assertEqual(m['known_fraction'],.5);self.assertEqual(m['like_lower_bound'],.25);self.assertEqual(m['like_upper_bound'],.75)
        empty=catalog_quality([],4);self.assertEqual(empty['missing_slots'],4);self.assertEqual(empty['unknown'],0);self.assertTrue(np.isnan(empty['like_lower_bound']))
        short=catalog_quality([4,np.nan],4);self.assertEqual(short['missing_slots'],2);self.assertEqual(short['known_fraction'],.5);self.assertEqual(short['like_upper_bound'],1)

if __name__=='__main__':unittest.main()
