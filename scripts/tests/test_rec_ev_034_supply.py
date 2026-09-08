"""Synthetic supply tests; no persisted research profiles or labels are opened."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('supply', SCRIPTS / 'rec_ev_034_supply.py')
m = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)
PRIOR = [(i + .5) / 10 for i in range(10)]


class SupplyTests(unittest.TestCase):
    def test_fold_in_matches_independent_linear_system(self):
        y = np.array([[1., 0.], [0., 1.], [1., 1.], [0., 0.]])
        has = np.array([True, True, True, False]); observed = np.array([0, 1, 3]); ratings = np.array([5., 2., .5])
        values, status, n = m.fold_scores(y, has, observed, ratings)
        np.testing.assert_allclose(values, y @ (ratings[:2] / 1.2), atol=1e-14, rtol=0)
        self.assertEqual(status, 'ACTIVE'); self.assertEqual(n, 2)
        self.assertEqual(m.fold_scores(y, has, np.array([3]), np.array([.5]))[1], 'NO_FACTOR')
        with patch.object(m.np.linalg, 'solve', side_effect=np.linalg.LinAlgError):
            self.assertEqual(m.fold_scores(y, has, observed, ratings)[1], 'SOLVE_FAILED')
        with patch.object(m.np.linalg, 'solve', return_value=np.zeros(2)):
            self.assertEqual(m.fold_scores(y, has, observed, ratings)[1], 'ZERO_VECTOR')
        with patch.object(m.np.linalg, 'solve', return_value=np.array([np.nan, 0])):
            self.assertEqual(m.fold_scores(y, has, observed, ratings)[1], 'NONFINITE_VECTOR')

    def test_sparse_masks_match_state_module(self):
        items = [{'id': 1, 'taste': 'F0', 'genres': [28], 'directors': [], 'keywords': []},
                 {'id': 2, 'taste': 'F0', 'genres': [], 'directors': [7], 'keywords': []},
                 {'id': 3, 'taste': 'F0', 'genres': [], 'directors': [], 'keywords': []},
                 {'id': 4, 'taste': 'F1', 'genres': [], 'directors': [], 'keywords': [28]},
                 {'id': 5, 'taste': 'F1', 'genres': [28], 'directors': [], 'keywords': []},
                 {'id': 6, 'taste': None, 'genres': [28], 'directors': [], 'keywords': []}]
        states = [{'id': 1, 'watch': 'RATED', 'rating': 5.}, {'id': 2, 'watch': 'RATED', 'rating': 4.}]
        tokens, inv = m.inverted_features(items); labels = np.array([0, 0, 0, 1, 1, -1]); observed = np.array([0, 1])
        weights = np.asarray(m.policy.relative_weights([5., 4.], PRIOR))
        t, d, anchors, info = m.eligibility(labels, observed, weights, tokens, inv)
        p, expected_t, expected_d, _ = m.policy.pools(items, states, PRIOR, {x['id']: 1. for x in items})
        excluded = np.zeros(6, bool); excluded[observed] = True
        self.assertEqual((np.flatnonzero(t & ~excluded) + 1).tolist(), expected_t)
        self.assertEqual((np.flatnonzero(d & ~excluded) + 1).tolist(), expected_d)
        self.assertEqual(set(anchors + 1), p['anchors']); self.assertEqual(info['unassigned_inputs'], 0)
        self.assertFalse(d[3]); self.assertTrue(d[4]); self.assertFalse(t[5] or d[5])

    def test_negative_taste_positive_movie_not_an_anchor(self):
        rows = [{'genres': [], 'directors': [9], 'keywords': []} for _ in range(4)]
        tokens, inv = m.inverted_features(rows)
        t, d, anchors, info = m.eligibility(np.array([0, 0, 1, -1]), np.array([0, 1, 3]), np.array([.5, -1., 1.]), tokens, inv)
        self.assertFalse(t.any()); self.assertFalse(d.any()); self.assertEqual(len(anchors), 0)
        self.assertEqual(info['unassigned_inputs'], 1)

    def test_raw_cutoff_precedes_type_and_observed_exclusion(self):
        raw = np.arange(7); taste = np.array([True, False, True, True, False, False, True])
        discovery = np.array([False, False, False, False, True, True, False])
        stages, first, typed = m.stage_orders(raw, taste, discovery, limit500=5, limit100=3)
        np.testing.assert_array_equal(first, [0, 1, 2, 3, 4]); np.testing.assert_array_equal(typed, [0, 2, 3, 4])
        np.testing.assert_array_equal(stages['TOPN100'], [0, 2, 3])
        excluded = np.array([True, False, False, False, False, False, False])
        self.assertEqual(m.select_slots(stages['TOPN100'], taste, discovery, excluded)[0], 'INSUFFICIENT_TASTE_FALLBACK')
        self.assertEqual(m.select_slots(stages['CANDIDATE500'], taste, discovery, excluded)[0], 'T2_D1')
        self.assertIn(0, first)  # Input exclusion must not be moved ahead of raw500.

    def test_supply_shortage_not_relabelled(self):
        excluded = np.zeros(5, bool); order = np.arange(5)
        t = np.array([True, False, False, False, False]); d = ~t
        status, selected, types, _, _ = m.select_slots(order, t, d, excluded)
        self.assertEqual(status, 'INSUFFICIENT_TASTE'); self.assertEqual(selected, []); self.assertEqual(types, [])
        t[:3] = True; d[:] = False
        self.assertEqual(m.select_slots(order, t, d, excluded)[0], 'NO_DISCOVERY_T3')

    def test_synthetic_score_outputs_hash_and_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run = m.Run.__new__(m.Run); run.root = root; run.identity = {'fixture': 'one'}
            run.cfg = {'inputs': {}}; run.guard = lambda: None; run.budget = Mock(); run.budget.elapsed.return_value = 0
            run.ids = np.arange(1, 641); run.prior = np.asarray(PRIOR)
            rng = np.random.default_rng(19); run.y = rng.normal(size=(640, 4)); run.has = np.ones(640, bool); run.has[-1] = False; run.y[-1] = 0
            run.profiles = pd.DataFrame([{'user_key': 'synthetic-a', 'profile_movie_ids': np.arange(1, 31), 'profile_rating_indices': np.full(30, 9)},
                                        {'user_key': 'synthetic-b', 'profile_movie_ids': np.arange(1, 31), 'profile_rating_indices': np.tile([0, 9], 15)}])
            labels = np.zeros(640, dtype=int); labels[320:] = 1
            run.labels = {'A_GENRE': labels, 'B_KMEANS': labels.copy()}
            run.tokens, run.inverted = m.inverted_features([{'genres': [10], 'directors': [], 'keywords': []} for _ in run.ids])
            digest = hashlib.sha256()
            for row in run.profiles.itertuples(index=False):
                ratings = (row.profile_rating_indices + 1) / 2
                y = run.y[:30]; vector = np.linalg.solve(y.T @ y + 3 * np.eye(4), y.T @ ratings)
                scores = run.y @ vector; scores[~run.has] = -np.inf; scores[:30] = -np.inf; digest.update(scores.astype('<f8').tobytes())
            run.paths = {'als_score_seal': root / 'reference.json'}
            m.write_json(run.paths['als_score_seal'], {'full_score_sha256': [None, None, None, digest.hexdigest()]})
            run.seal('role-seal.json', (), sources={})
            run.score()
            summary = m.read_json(root / 'summary.json')
            self.assertTrue(summary['existing_ALS_n30_exact_score_hash_match']); self.assertFalse(summary['quality_evaluated'])
            self.assertEqual(sum(summary['paired_T2_D1'].values()), 2)
            frame = pd.read_parquet(root / 'user-supply.parquet')
            self.assertEqual(len(frame), 4); self.assertTrue((frame.raw500_size == 500).all())
            slots = pd.read_parquet(root / 'slots.parquet'); self.assertEqual(len(slots), 12)
            for ids in slots.movie_ids: self.assertFalse(set(ids) & set(range(1, 31)))
            m.write_json(root / 'budget.json', {'fixture': True}); run.seal('completion-seal.json', m.OUTPUTS, sources={})
            run.sources = Mock(); run.load = Mock(side_effect=AssertionError('reuse read profiles')); run.score = Mock(side_effect=AssertionError('reuse rescored'))
            run.run(); run.load.assert_not_called(); run.score.assert_not_called()
            (root / 'summary.json').write_text('{}')
            with self.assertRaises(RuntimeError): run.run()

    def test_partial_and_failure_never_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            run = m.Run.__new__(m.Run); run.root = Path(temp); run.sources = Mock()
            (run.root / 'partial').write_text('preserve')
            with self.assertRaisesRegex(RuntimeError, 'partial execution'): run.run()
            (run.root / 'failure.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'failed execution'): run.run()
            run.sources.assert_not_called()


if __name__ == '__main__':
    unittest.main()
