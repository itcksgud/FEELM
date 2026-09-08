import copy
import itertools
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feelm_genre_set_rule import GenreSetRule, POLICY


def fixture():
    groups = {'ADRENALINE':[28,12], 'HEART':[18,10749], 'JOY':[35,10751],
              'LEGACY':[36,10752,37], 'REAL':[99], 'RHYTHM':[10402],
              'SHADOW':[80,27,9648,53], 'WONDER':[16,14,878]}
    return {'package_id':'REC040_GENRE_SET_RULE', 'policy':POLICY,
            'rule_codes':sorted(groups), 'rule_genre_groups':groups}


class SetRuleTests(unittest.TestCase):
    def setUp(self): self.model = GenreSetRule(fixture())
    def test_every_permutation_and_duplicate_has_same_explanation(self):
        values=[80,27,18,999999]
        expected=self.model.predict(values)
        for p in itertools.permutations(values):
            self.assertEqual(expected,self.model.predict(list(p)+list(p)))
    def test_later_genres_can_outvote_first(self):
        row=self.model.predict([18,80,53])
        self.assertEqual(row['semantic_code'],'SHADOW')
        self.assertEqual(row['matched_counts'][6],2)
        self.assertEqual(row['tie_count'],1)
    def test_exact_tie_and_fixed_code_order(self):
        row=self.model.predict([10402,80,53])
        self.assertEqual(row['top_native_labels'],[5,6])
        self.assertEqual(row['semantic_code'],'RHYTHM')
        self.assertEqual(row['score_margin_squared'],0)
    def test_three_shadow_genres_beat_one_music(self):
        self.assertEqual(self.model.predict([10402,80,53,27])['semantic_code'],'SHADOW')
    def test_singletons_and_group_size_correction(self):
        for code,genres in fixture()['rule_genre_groups'].items():
            for g in genres:
                self.assertEqual(self.model.predict([g])['semantic_code'],code)
        row=self.model.predict([99,18])
        self.assertEqual(row['semantic_code'],'REAL')
        self.assertGreater(row['score_margin_squared'],0)
    def test_empty_format_unknown_and_partial(self):
        for values,status in [([], 'NO_GENRE'),([10770],'FORMAT_ONLY'),
                              ([999999],'NO_KNOWN_CONTENT_GENRE'),
                              ([10770,999999],'NO_KNOWN_CONTENT_GENRE')]:
            row=self.model.predict(values)
            self.assertEqual(row['status'],status)
            self.assertIsNone(row['group_code'])
            self.assertEqual(row['top_native_labels'],[])
        row=self.model.predict([999999,28])
        self.assertEqual(row['status'],'ASSIGNED_PARTIAL_VOCABULARY')
        self.assertEqual(row['semantic_code'],'ADRENALINE')
        self.assertEqual(row['unknown_genre_ids'],[999999])
    def test_tv_is_not_content_evidence(self):
        a=self.model.predict([28]); b=self.model.predict([10770,28])
        self.assertEqual(a,b)
        self.assertTrue(self.model.predict([10770])['format_only'])
    def test_invalid_inputs_rejected(self):
        for v in [None,'28',[True],[1.0],[0],[-2],[[28]],{'id':28}]:
            with self.subTest(v=v),self.assertRaises(ValueError): self.model.predict(v)
    def test_invalid_rule_packages_rejected(self):
        changes=[('policy','other'),('rule_codes',['x']*8)]
        for field,value in changes:
            p=fixture();p[field]=value
            with self.assertRaises(ValueError): GenreSetRule(p)
        for value in [[],[28,28],[10770],[35]]:
            p=copy.deepcopy(fixture());p['rule_genre_groups']['ADRENALINE']=value
            with self.assertRaises(ValueError): GenreSetRule(p)


if __name__ == '__main__': unittest.main()
