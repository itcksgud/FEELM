import copy
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feelm_genre_candidates import GenreCandidates


def fixture():
    ids = [10402,10749,10751,10752,10770,12,14,16,18,27,28,35,36,37,53,80,878,9648,99]
    groups = {'A':[28,12],'B':[16,14,878],'C':[35,10751],'D':[18,10749],
              'E':[80,27,9648,53],'F':[99],'G':[36,10752,37],'H':[10402]}
    centers = np.zeros((8,19)); centers[0,10] = 1; centers[1,11] = 1
    return {'package_id':'REC039_FROM_REC038','genre_ids':ids,'idf':[1.]*19,
            'raw_centers':centers.tolist(),'raw_cluster_order':[1,0,2,3,4,5,6,7],
            'rule_codes':list(groups),'rule_genre_groups':groups}


class CandidateTests(unittest.TestCase):
    def setUp(self): self.model = GenreCandidates(fixture())
    def test_rule_order_is_preserved(self):
        self.assertEqual(self.model.predict([28,35],'RULE_GENRE')['native_label'],0)
        self.assertEqual(self.model.predict([35,28],'RULE_GENRE')['native_label'],2)
    def test_km_order_and_duplicates(self):
        a=self.model.predict([28,35],'KM_GENRE')
        self.assertEqual(a,self.model.predict([35,28,35],'KM_GENRE'))
    def test_raw_tie_before_canonical_map(self):
        # Equal distances to raw0/raw1. Raw0 wins, then maps to native1.
        self.assertEqual(self.model.predict([28,35],'KM_GENRE')['native_label'],1)
    def test_empty_and_unknown(self):
        for m in ['RULE_GENRE','KM_GENRE']:
            self.assertIsNone(self.model.predict([],m)['group_code'])
            self.assertFalse(self.model.predict([999999],m)['supported'])
    def test_unknown_first_not_skipped_in_rule(self):
        self.assertEqual(self.model.predict([999999,28],'RULE_GENRE')['status'],'UNMAPPED_GENRE')
        row=self.model.predict([999999,28],'KM_GENRE')
        self.assertEqual(row['status'],'ASSIGNED_PARTIAL_VOCABULARY')
        self.assertEqual(row['unknown_genre_ids'],[999999])
    def test_format_only_is_visible(self):
        a=self.model.predict([10770],'RULE_GENRE'); b=self.model.predict([10770],'KM_GENRE')
        self.assertFalse(a['supported']); self.assertTrue(b['supported'])
        self.assertTrue(a['format_only'] and b['format_only'])
        self.assertEqual(self.model.predict([10770,28],'RULE_GENRE')['native_label'],0)
    def test_invalid_input(self):
        for v in [None,'28',[True],[1.0],[0],[-2],[[28]],{'id':28}]:
            with self.subTest(v=v),self.assertRaises(ValueError): self.model.predict(v,'KM_GENRE')
    def test_invalid_package(self):
        for field,value in [('idf',[0]*19),('raw_centers',[[float('nan')]*19]*8),
                            ('raw_cluster_order',[0]*8),('genre_ids',[28]*19)]:
            p=copy.deepcopy(fixture());p[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError): GenreCandidates(p)
    def test_unknown_method(self):
        with self.assertRaises(ValueError): self.model.predict([28],'NMF')


if __name__ == '__main__': unittest.main()
