import unittest
from types import SimpleNamespace
from dv2_prepare import field,quality_state,normalize_raw
from dv2_common import profile
import numpy as np

class RawEvidenceTests(unittest.TestCase):
    def test_missing_null_zero_not_collapsed(self):
        self.assertEqual(field({},'r'),('MISSING',None))
        self.assertEqual(field({'r':None},'r'),('NULL',None))
        self.assertEqual(field({'r':0},'r'),('ZERO',0.))
    def test_types_ranges(self):
        for v,state in [(True,'NON_NUMERIC'),('4','NON_NUMERIC'),(float('inf'),'NON_FINITE'),(-1,'OUT_OF_RANGE'),(11,'OUT_OF_RANGE')]:
            self.assertEqual(field({'r':v},'r',max_value=10)[0],state)
        self.assertEqual(field({'v':1.5},'v',integer=True)[0],'NON_INTEGER')
    def test_quality_contradictions(self):
        self.assertEqual(quality_state('ZERO',0,'POSITIVE',3),'AMBIGUOUS_ZERO_AVERAGE')
        self.assertEqual(quality_state('POSITIVE',9,'ZERO',0),'CONTRADICTORY_ZERO_COUNT')
        self.assertEqual(quality_state('ZERO',0,'ZERO',0),'ZERO_VOTES')
        self.assertEqual(quality_state('POSITIVE',9,'POSITIVE',3),'VALID')
    def test_rh_fields_separate_and_cast_order(self):
        d={'details':{'id':3,'vote_average':0,'vote_count':0,'runtime':0,'origin_country':['US'],'production_countries':[{'iso_3166_1':'KR'}]},'credits':{'cast':[{'id':2,'order':2},{'id':1,'order':1},{'id':1,'order':0}],'crew':[{'id':10,'job':'Director'}]}}
        row=normalize_raw(d,SimpleNamespace(name='3.json',mtime=1))
        self.assertIsNone(row['tmdb_vote_average']);self.assertEqual(row['tmdb_vote_count'],0)
        self.assertIsNone(row['runtime_minutes']);self.assertEqual(row['top5_cast_ids'],[1,2])
        self.assertEqual(row['production_country_codes'],['KR']);self.assertEqual(row['origin_country_codes'],['US'])
        self.assertEqual(row['raw_timestamp_fields'],'{}')
    def test_unmapped_rating_kept_in_anchor(self):
        _,info=profile(np.array([[1.,0.]]),{'history':[0],'stars':[5.],'original_stars':[5.,.5],'raw_pre_count':2,'cap':10})
        self.assertAlmostEqual(info['anchor'],3.142857142857143)
        self.assertEqual(info['original_inputs'],2)

if __name__=='__main__':unittest.main()
