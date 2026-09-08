import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rec_ev_032_als_only as mod
import numpy as np
import pandas as pd


class ALSOnlyTests(unittest.TestCase):
    def setUp(self):
        self.ids = np.array([10, 20, 30, 40])
        self.bayes = np.array([1., 4., 3., 2.])

    def calc(self, factors, has, movies=(10,), indices=(9,), n=1):
        return mod.als_order(self.ids, self.bayes, np.asarray(factors, dtype=float).reshape(4, -1),
            np.asarray(has, dtype=bool), np.asarray(movies), np.asarray(indices), n, .1)

    def test_scalar_fold_in_and_raw_half_star(self):
        order, values, diag = self.calc([2, 1, -1, 3], [1, 1, 1, 1], indices=(0,))
        expected_p = 1. / 4.1
        np.testing.assert_allclose(values[1:], np.array([1, -1, 3]) * expected_p)
        self.assertEqual(order.tolist(), [3, 1, 2])
        self.assertTrue(diag['als_active'])

    def test_negative_zero_and_above_five_scores_kept(self):
        order, values, _ = self.calc([1, -1, 0, 4], [1, 1, 1, 1])
        self.assertEqual(order.tolist(), [3, 2, 1])
        self.assertLess(values[1], 0)
        self.assertEqual(values[2], 0)
        self.assertGreater(values[3], 5)

    def test_ties_use_movie_id(self):
        order, _, _ = self.calc([1, 2, 2, 2], [1, 1, 1, 1])
        self.assertEqual(order.tolist(), [1, 2, 3])

    def test_factorless_input_does_not_enter_regularizer(self):
        order, values, diag = self.calc([2, 0, 1, 3], [1, 0, 1, 1], movies=(10, 20), indices=(9, 0), n=2)
        self.assertEqual(diag['n_factor'], 1)
        np.testing.assert_allclose(values[2:], [10 / 4.1, 30 / 4.1])
        self.assertEqual(order.tolist(), [3, 2])

    def test_factorless_movie_not_zero_prediction(self):
        order, values, _ = self.calc([1, 0, -1, -2], [1, 0, 1, 1])
        self.assertEqual(order.tolist(), [2, 3])
        self.assertEqual(values[1], -np.inf)

    def test_n0_matches_p0(self):
        order, _, diag = self.calc([1, 2, 3, 4], [1, 1, 1, 1], n=0)
        self.assertEqual(order.tolist(), [1, 2, 3, 0])
        self.assertEqual(diag['fallback'], 'P0_NO_INPUT')

    def test_no_factor_fallback(self):
        order, _, diag = self.calc([0, 1, 1, 1], [0, 1, 1, 1])
        self.assertEqual(order.tolist(), [1, 2, 3])
        self.assertEqual(diag['fallback'], 'P0_NO_FACTOR')

    def test_zero_profile_fallback(self):
        _, _, diag = self.calc([0, 1, 1, 1], [1, 1, 1, 1])
        self.assertEqual(diag['fallback'], 'P0_ZERO_PROFILE')

    def test_global_shortage_stops(self):
        with self.assertRaisesRegex(RuntimeError, 'global ALS supply'):
            self.calc([1, 1, 0, 0], [1, 1, 0, 0])

    def test_projection_shortage_does_not_top_up(self):
        selected, ranks = mod.prior.projected_order(np.array([1, 2]), np.array([0, 3, 2]), 4)
        self.assertEqual(selected.tolist(), [2])
        self.assertEqual(ranks.tolist(), [0, 0, 2])
        with self.assertRaisesRegex(RuntimeError, 'supply failure'):
            mod.prior.check_supply(np.array([[2, 1]]))

    def test_exact_paired_tie_and_harm_boundary(self):
        hist = np.array([2] * 10)
        diff = mod.prior.observed_difference([1., 5.], [2., 4.], hist)
        self.assertEqual(diff[0, 0], 0.)
        numerator, denominator = mod.prior.metric_parts([1., 5.], hist)
        self.assertEqual((numerator / denominator)[2], 1.)
        self.assertEqual(mod.prior.direction('MEAN_Q', 0., 0.), 'UNDECIDED')

    def test_unsealed_score_cannot_open_labels(self):
        run = mod.Run.__new__(mod.Run)
        run.validate_score = lambda: mod.base.require(False, 'not sealed')
        with patch.object(mod.pd, 'read_parquet') as reader:
            with self.assertRaisesRegex(RuntimeError, 'not sealed'):
                run.evaluate()
            reader.assert_not_called()

    def test_completion_requires_all_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod.base.write_json(root/'completion-seal.json', {'status':'COMPLETE','fingerprint':{},'source_completion':{},'outputs':{}})
            with self.assertRaisesRegex(RuntimeError, 'completion drift'):
                mod.validate_completion(root, {}, {})

    def test_completion_rejects_changed_dependency_or_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in mod.OUTPUTS:
                (root/name).write_text('fixture')
            seal={'status':'COMPLETE','fingerprint':{},'source_completion':{'sha256':'one'},
                  'outputs':{n:mod.base.pin(root/n) for n in mod.OUTPUTS}}
            mod.base.write_json(root/'completion-seal.json', seal)
            mod.validate_completion(root, {}, {'sha256':'one'})
            with self.assertRaisesRegex(RuntimeError, 'completion drift'):
                mod.validate_completion(root, {}, {'sha256':'two'})
            (root/'rankings.npz').write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'output drift'):
                mod.validate_completion(root, {}, {'sha256':'one'})

    def test_synthetic_score_then_evaluate_and_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = mod.Run.__new__(mod.Run)
            run.root, run.identity = root, {'synthetic':'fixture'}
            run.cfg = {'ns':[0,1,2,3], 'policies':['P0','M0_RAW_ALS_STRUCTURED_RRF60','A0_RAW_ALS_ONLY'],
                'bootstrap_repeats':5000,'bootstrap_seed':20260907,'alpha':.05,'family_size':18,'amendment':'SYNTHETIC'}
            run.oldcfg={'als':{'rank':1,'reg':.1}}
            run.guard=lambda:None
            run.sources=lambda:None
            run.ids=np.arange(10,90,10)
            run.bayes=np.array([0.,0.,0.,4.,5.,3.,2.,1.])
            run.keys=np.array(['fixture_a','fixture_b'])
            run.profiles=pd.DataFrame({'user_key':run.keys,'profile_movie_ids':[[10,20,30]]*2,'profile_rating_indices':[[9,8,7],[1,2,3]]})
            run.targets=[np.array([40,50,60])]*2
            run.target_ids=np.concatenate(run.targets)
            run.offsets=np.array([0,3,6])
            run.reference=np.zeros((2,4,2,2),dtype=np.int64)
            run.reference[:,:,0]=[50,40]
            run.reference[:,:,1]=[60,40]
            run.reference[:,0,1]=[50,40]
            run.paths={}
            for name in ('conditional_completion-seal.json','conditional_targets.parquet','membership'):
                path=root/('source_'+name)
                path.write_text('synthetic source')
                run.paths[name]=path
            run.paths['factors']=root/'source_factors.npz'
            fy=np.array([[1.],[2.],[3.],[3.],[2.],[1.],[0.],[-1.]])
            np.savez_compressed(run.paths['factors'],item_ids=run.ids,factors=fy)
            diag=[]
            for row in run.profiles.itertuples(index=False):
                for n in run.cfg['ns']:
                    diag.append({'user_key':row.user_key,'n':n,'n_factor':n,'als_active':n>0})
            run.paths['original_diagnostics']=root/'source_diagnostics.parquet'
            pd.DataFrame(diag).to_parquet(run.paths['original_diagnostics'],index=False)
            mod.base.write_json(root/'request-seal.json', {'status':'SEALED_BEFORE_SCORING','fingerprint':run.identity,
                'source_completion':mod.base.pin(run.paths['conditional_completion-seal.json']),
                'source_targets':mod.base.pin(run.paths['conditional_targets.parquet']),
                'source_membership':mod.base.pin(run.paths['membership'])})
            run.score()
            run.validate_score()
            hists=np.array([[2]*10,[1,2,3,4,5,6,7,8,9,10]])
            lookup={'fixture_a':{40:5.,50:3.,60:1.},'fixture_b':{40:2.,50:4.,60:5.}}
            labels=[]
            for u,key in enumerate(run.keys):
                for movie,raw in lookup[key].items():
                    labels.append({'user_key':key,'movie_id':movie,'rating_raw':raw,'q':mod.base.q_from_hist(mod.base.rating_index(raw),hists[u])})
            run.paths['conditional_evaluation-labels.parquet']=root/'source_labels.parquet'
            pd.DataFrame(labels).to_parquet(run.paths['conditional_evaluation-labels.parquet'],index=False)
            run.paths['conditional_histograms.npz']=root/'source_histograms.npz'
            np.savez_compressed(run.paths['conditional_histograms.npz'],user_keys=run.keys,histograms=hists)
            refs=[]
            for u,key in enumerate(run.keys):
                for k,n in enumerate(run.cfg['ns']):
                    for p,policy in enumerate(run.cfg['policies'][:2]):
                        movies=run.reference[u,k,p].tolist()
                        raw=[lookup[key][m] for m in movies]
                        qs=[mod.base.q_from_hist(mod.base.rating_index(r),hists[u]) for r in raw]
                        num,den=mod.prior.metric_parts(raw,hists[u])
                        refs.append({'user_key':key,'n':n,'policy':policy,'movie_ids':movies,'raw_ratings':raw,'q':qs,
                            **dict(zip(mod.prior.METRICS,(num/den).tolist()))})
            run.paths['conditional_user-metrics.parquet']=root/'source_metrics.parquet'
            pd.DataFrame(refs).to_parquet(run.paths['conditional_user-metrics.parquet'],index=False)
            run.evaluate()
            result=mod.base.read_json(root/'metrics.json')
            self.assertEqual(len(result['paired_differences']),18)
            self.assertEqual(len(result['metrics']),12)
            self.assertTrue(result['reference_per_user_metrics_match'])
            self.assertEqual(result['training_runs'],0)
            for row in result['paired_differences']:
                if row['n']>0 and row['reference']=='P0':
                    self.assertEqual(row['difference_ALS_minus_reference'],0.)


if __name__ == '__main__':
    unittest.main()
