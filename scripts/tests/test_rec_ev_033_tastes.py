"""Synthetic tests only: no research catalogue, user data, or network access."""
from collections import Counter
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import numpy as np
import pandas as pd

SPEC = importlib.util.spec_from_file_location('rec033', Path(__file__).resolve().parents[1] / 'rec_ev_033_tastes.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
CFG = m.read_json(m.FILES['config'])


def response(tid, genres, keywords):
    body = {'id': tid, 'title': f'Synthetic {tid}', 'original_title': f'Fixture {tid}',
            'original_language': 'en', 'genres': genres, 'keywords': {'keywords': keywords}}
    digest = hashlib.sha256(m.canonical_body(body)).hexdigest()
    return {'request': {'kind': 'movie', 'identity': str(tid), 'endpoint': f'/3/movie/{tid}',
            'params': {'append_to_response': 'credits,keywords', 'language': 'ko-KR'}},
            'status': 200, 'body': body, 'body_sha256': digest}


class TasteTests(unittest.TestCase):
    def test_cache_provenance_and_canonical_newline(self):
        value = response(7, [{'id': 28, 'name': 'Action'}], [])
        expected = hashlib.sha256((json.dumps(value['body'], ensure_ascii=False, sort_keys=True,
                    separators=(',', ':')) + '\n').encode()).hexdigest()
        self.assertEqual(m.validate_cache(value, 7, expected), value['body'])
        for change in ('request', 'status', 'body', 'body_sha256'):
            bad = copy.deepcopy(value)
            if change == 'request': bad[change]['params']['language'] = 'en-US'
            elif change == 'status': bad[change] = 404
            elif change == 'body': bad[change]['id'] = 8
            else: bad[change] = '0' * 64
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                m.validate_cache(bad, 7, expected)
        with self.assertRaises(RuntimeError): m.validate_cache(value, 7, '0' * 64)

    def test_genre_assignment_boundaries(self):
        groups, formats = CFG['genre_groups'], CFG['format_only_genres']
        cases = [([], (None, 'NO_GENRE', None)), ([10770], (None, 'FORMAT_ONLY', None)),
                 ([10770, 10402], ('RHYTHM', 'ASSIGNED', 10402)),
                 ([28, 28, 18], ('ADRENALINE', 'ASSIGNED', 28)),
                 ([999999, 28], (None, 'UNMAPPED_GENRE', 999999))]
        for genres, expected in cases:
            self.assertEqual(m.genre_assignment(genres, groups, formats), expected)
        self.assertNotEqual(m.genre_assignment([28, 18], groups, formats)[0],
                            m.genre_assignment([18, 28], groups, formats)[0])
        for values in ([True], [28.5], ['28'], [-1]):
            with self.assertRaises(RuntimeError): m.genre_assignment(values, groups, formats)
        with self.assertRaises(RuntimeError): m.genre_assignment([28], {'X': [28], 'Y': [28]}, [])

    def test_metadata_ids(self):
        self.assertEqual(m.ordered_ids([{'id': 4}, {'id': 2}, {'id': 4}]), [4, 2])
        for value in (True, 1.5, '2', 0, -1):
            with self.assertRaises(RuntimeError): m.ordered_ids([{'id': value}])

    def test_vector_contract_rejects_incompatible_future_inputs(self):
        descriptor = {'dimension': 2, 'model_id': 'synthetic', 'revision': 'one', 'normalization': 'L2'}
        x = np.eye(2, dtype=np.float32)
        self.assertEqual(m.validate_vectors(x, descriptor, descriptor).dtype, np.float64)
        for key, value in [('model_id', 'other'), ('revision', 'two'), ('dimension', 3), ('normalization', 'none')]:
            bad = {**descriptor, key: value}
            with self.assertRaises(RuntimeError): m.validate_vectors(x, bad, descriptor)
        for bad in (np.ones((2, 3)), np.ones(2), np.zeros((2, 2)), [[np.nan, 0]], [[np.inf, 0]]):
            with self.assertRaises(RuntimeError): m.validate_vectors(bad, descriptor, descriptor)

    def test_euclidean_predict_ties_and_order(self):
        x = np.array([[1., 0.], [0., 1.], [-1., 0.], [0., -1.]])
        centers = np.array([[.1, 0.], [.9, .2]])
        labels, distances = m.nearest(x, centers, 1)
        # Cosine would pick center 0 for the first row; Euclidean picks center 1.
        self.assertEqual(labels[0], 1)
        expected = np.array([[sum((float(a) - float(b)) ** 2 for a, b in zip(row, center))
                             for center in centers] for row in x])
        np.testing.assert_array_equal(labels, expected.argmin(axis=1))
        np.testing.assert_array_equal(distances, expected.min(axis=1))
        for batch in (2, 3, 100):
            a, b = m.nearest(x, centers, batch)
            np.testing.assert_array_equal(a, labels); np.testing.assert_array_equal(b, distances)
        order = np.array([3, 0, 2, 1])
        a, b = m.nearest(x[order], centers)
        np.testing.assert_array_equal(a, labels[order]); np.testing.assert_array_equal(b, distances[order])
        self.assertEqual(m.nearest(np.array([[0., 0.]]), np.array([[1., 0.], [-1., 0.]]))[0][0], 0)
        for batch in (0, -1):
            with self.assertRaises(RuntimeError): m.nearest(x, centers, batch)
        with self.assertRaises(RuntimeError): m.nearest(x, [[np.nan, 1]])

    def test_cluster_code_permutation_invariance(self):
        x = np.array([[1., 0.], [.9, 0.], [-1., 0.], [-.9, 0.]])
        ids = np.array([40, 30, 20, 10])
        centers = np.array([[1., 0.], [-1., 0.]])
        first = m.canonical_centers(x, ids, centers)
        second = m.canonical_centers(x, ids, centers[::-1])
        np.testing.assert_array_equal(first[0], second[0])
        np.testing.assert_array_equal(first[2], [20, 40])
        with self.assertRaises(RuntimeError): m.canonical_centers(x, ids, np.vstack([centers, [0., 10.]]))

    def test_ctfidf_independent_formula_and_empty_columns(self):
        counts = np.array([[2, 1, 0], [0, 4, 0], [0, 0, 0]])
        average = (3 + 4 + 0) // 3
        expected = np.array([[2 / 3 * np.log(1 + average / 2), 1 / 3 * np.log(1 + average / 5), 0],
                             [0, np.log(1 + average / 5), 0], [0, 0, 0]])
        np.testing.assert_allclose(m.ctfidf(counts), expected, atol=1e-15, rtol=0)
        for bad in ([[-1]], [[np.nan]]):
            with self.assertRaises(RuntimeError): m.ctfidf(bad)

    def test_stability_ignores_cluster_label_permutation(self):
        first = np.array([0, 0, 1, 1, 2, 2])
        second = np.array([2, 2, 0, 0, 1, 1])
        value = m.stability(first, second, 3)
        self.assertEqual(value['adjusted_rand_index'], 1)
        self.assertEqual(value['aligned_agreement'], 1)
        self.assertFalse(value['primary_model_replaced'])
        self.assertEqual(value['diagnostic_to_primary'], {'2': 0, '0': 1, '1': 2})

    def test_seals_reject_tamper_and_missing_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            run = m.Run.__new__(m.Run); run.root = Path(temp); run.identity = {'fixture': 'one'}; run.guard = lambda: None
            (run.root / 'a').write_text('original')
            run.seal('seal.json', ['a'], dependency='pinned')
            run.verify('seal.json', ['a'], {'dependency': 'pinned'})
            with self.assertRaises(RuntimeError): run.verify('seal.json', ['a', 'b'])
            with self.assertRaises(RuntimeError): run.verify('seal.json', ['a'], {'dependency': 'other'})
            (run.root / 'a').write_text('modified')
            with self.assertRaises(RuntimeError): run.verify('seal.json', ['a'])

    def test_failure_and_partial_run_never_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            run = m.Run.__new__(m.Run); run.root = Path(temp); run.sources = Mock()
            (run.root / 'failure.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'failed execution'): run.run()
            (run.root / 'failure.json').unlink()
            (run.root / 'partial').write_text('preserve')
            with self.assertRaisesRegex(RuntimeError, 'partial execution'): run.run()
            run.sources.assert_not_called()

    def test_synthetic_catalogue_through_description_and_reuse(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(m, 'ROOT', Path(temp)):
            root = Path(temp); output = root / 'output'; output.mkdir(); cache = root / 'cache'; cache.mkdir()
            run = m.Run.__new__(m.Run); run.root = output; run.identity = {'synthetic': 'one'}
            run.cfg = copy.deepcopy(CFG); run.cfg.update(expected_catalog=49, expected_common=48,
                expected_identity_eligible=48, expected_structured_eligible=48, expected_text_eligible=48, inputs={})
            run.guard = lambda: None; run.log = lambda *args, **kwargs: None; run.budget = None
            run.descriptor = {'dimension': 8, 'model_id': 'synthetic', 'model_revision': 'one'}
            run.cache_roots = [cache]
            identity, structured, embeddings = [], [], []
            genres = [values[0] for values in CFG['genre_groups'].values()]
            for pos in range(48):
                mid, tid, group = pos + 1, 1000 + pos, pos // 6
                g = [{'id': genres[group], 'name': f'Genre {group}'}]
                # One common movie stays unassigned in A and in the common denominator.
                if pos == 0: g = [{'id': 10770, 'name': 'TV Movie'}]
                keyword = [{'id': group + 100, 'name': f'Topic {group}'}]
                value = response(tid, g, keyword)
                m.write_json(cache / f'movie-{tid}-ko_KR.json', value)
                identity.append({'movie_id': mid, 'tmdb_id': tid, 'identity_status': 'ML_TMDB_VERIFIED',
                                 'media_type': 'movie', 'response_sha256': value['body_sha256']})
                structured.append({'movie_id': mid, 'genre_ids': [a['id'] for a in g],
                                   'keyword_ids': [group + 100], 'original_language': 'en', 'feature_eligible': True})
                vector = np.eye(8, dtype=np.float32)[group]
                embeddings.append({'movie_id': mid, 'embedding': vector, 'model_id': 'synthetic',
                                   'model_revision': 'one', 'feature_eligible': True})
            identity.append({'movie_id': 49, 'tmdb_id': None, 'identity_status': 'REVIEW', 'media_type': 'movie', 'response_sha256': None})
            run.paths = {}
            for name, rows in [('identity', identity), ('structured', structured), ('embeddings', embeddings)]:
                run.paths[name] = root / f'{name}.parquet'; pd.DataFrame(rows).to_parquet(run.paths[name], index=False)
            run.paths['universe'] = root / 'universe.npz'; np.savez_compressed(run.paths['universe'], item_ids=np.arange(1, 49))
            run.prepare(); run.models(); run.describe(); run.verify_cache_sources()
            summary = m.read_json(output / 'taste-summary.json')
            self.assertEqual(summary['genre_unassigned'], 1); self.assertEqual(summary['semantic_unassigned'], 0)
            self.assertEqual(len(summary['tastes']), 16); self.assertFalse(summary['winner_selected'])
            self.assertEqual(sum(v['movies'] for v in summary['tastes'] if v['policy'] == 'A_GENRE'), 47)
            self.assertEqual(sum(v['movies'] for v in summary['tastes'] if v['policy'] == 'B_KMEANS'), 48)
            with np.load(output / 'centroids.npz', allow_pickle=False) as fitted:
                a, d = m.nearest(run.x, fitted['primary_centers'], 7)
                np.testing.assert_array_equal(a, fitted['primary_labels']); np.testing.assert_array_equal(d, fitted['primary_distances'])
                self.assertEqual(len(fitted['codes']), 8)
            m.write_json(output / 'budget.json', {'synthetic': True})
            run.seal('completion-seal.json', m.OUTPUTS, sources={})
            run.sources = Mock(); run.prepare = Mock(side_effect=AssertionError('reuse transformed metadata'))
            run.models = Mock(side_effect=AssertionError('reuse fitted model')); run.describe = Mock(side_effect=AssertionError('reuse described'))
            run.run(); run.models.assert_not_called(); run.prepare.assert_not_called()
            # Cache provenance is checked even during completed reuse.
            (cache / 'movie-1000-ko_KR.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'cache source drift'): run.run()


if __name__ == '__main__':
    unittest.main()
