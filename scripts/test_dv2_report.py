"""Reporting regressions: canonical reused references and undefined evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
import dv2_report as report


class ReportingTests(unittest.TestCase):
    def request(self,uid,policy,kind='group',hierarchy='v1-fixed16',budget=50):
        return dict(uid=uid,policy=policy,kind=kind,hierarchy=hierarchy,budget=budget,profile_state='VALID',
          ranked=[1,2],ranked_prediction=[4.,3.],returned=2,candidate_count=50,latency_ms=5.,fixed8_unseen_slots=1,seen_collection_slots=0)

    def test_cached_global_reference_uses_one_canonical_group(self):
        requests=[];links=[]
        for uid,hierarchy in [(1,'v1-fixed16'),(2,'GKT-K128'),(3,'GKT-K256')]:
            name=hierarchy+':global_full_predictor:mean:B50:Q25'
            requests += [self.request(uid,'chosen'),self.request(uid,name,'global_full_predictor',hierarchy)]
            links.append(dict(uid=uid,policy='chosen',reference_policy=name,reference_reused_for_comparison=uid>1))
        rows=report.paired(pd.DataFrame(requests),links)
        self.assertEqual({r['reference'] for r in rows},{'global_full_predictor'})
        self.assertEqual(len({r['source_reference_policy'] for r in rows}),3)
        self.assertEqual([r['reference_actual_predicted_movies'] for r in rows],[50]*3)

    def test_country_absence_is_unknown(self):
        frame=pd.DataFrame([dict(original_language='en',release_year=2000,production_country_codes=country) for country in [[],['US'],['KR']]])
        a=pd.DataFrame([dict(uid=1,policy='chosen',profile_state='VALID',ranked=[0,1,2],ranked_tmdb_count=[1,20,100],ranked_train_count=[0,10,500],returned=3)])
        rows=report.strata(a,frame)
        countries={r['stratum']:r for r in rows if r['dimension']=='production_KR'}
        self.assertEqual(set(countries),{'unknown','known_non_KR','KR'})
        self.assertEqual(countries['unknown']['top1'],1)
        self.assertEqual(countries['known_non_KR']['top1'],0)

    def test_undefined_plot_values_remain_unplotted(self):
        row=dict(policy='chosen',hierarchy='v1-fixed16',budget=50,quota=25,mean_prefix_overlap=None,absolute_gap_p95=None,full_return_share=0.)
        sensitivity=[dict(sensitivity='MAIN_m300',tmdb_low20_top1_conditional=None,ml_low20_top1_conditional=None)]
        def fake_read(path):return [row] if Path(path).name=='summary.json' else {'C':6.3}
        with tempfile.TemporaryDirectory() as directory,patch.object(report,'read',side_effect=fake_read):
            report.plots(Path(directory),{'per_hierarchy':{'v1-fixed16':'chosen'}},sensitivity)
            self.assertEqual(len(list(Path(directory).glob('*.png'))),3)

    def test_no_comparable_policy_writes_explicit_empty_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dest=root/'report';dest.mkdir()
            with patch.object(report,'OUT',root),patch.object(report,'verify'),patch.object(report,'require_fresh',return_value=dest),patch.object(report,'seal'),patch.object(report,'pin',return_value={'sha256':'synthetic','bytes':0}),patch.object(report,'read',return_value={'status':'NO_COMPARABLE_POLICY_HOLD'}):
                report.main()
            self.assertEqual(json.loads((dest/'manifest.json').read_text())['status'],'NO_COMPARABLE_DATA')
            self.assertEqual(json.loads((dest/'request-panels.json').read_text()),[])


if __name__=='__main__':unittest.main()
