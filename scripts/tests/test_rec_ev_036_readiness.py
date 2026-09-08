import hashlib
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rec_ev_036_readiness import geometry, select_samples, aligned, match_text, validate_english
from build_rec_ev_019b_features import merge_text, build_embedding_input


class ReadinessTests(unittest.TestCase):
    def test_geometry_ties_and_batching(self):
        x = np.array([[0., 0.], [2., 0.], [1., 1.]])
        c = np.array([[0., 0.], [0., 0.], [2., 0.]])
        a = geometry(x, c, 1); b = geometry(x, c, 10)
        for u, v in zip(a, b): np.testing.assert_array_equal(u, v)
        np.testing.assert_array_equal(a[1], [0, 2, 0])
        np.testing.assert_array_equal(a[2], [1, 0, 1])
        np.testing.assert_array_equal(a[3], [0, 1, 0])
        with self.assertRaises(RuntimeError): geometry([[np.nan]], [[1], [2]])

    def test_selection_ties_and_roles(self):
        cfg = {'nearest_count': 2, 'hash_count': 2, 'boundary_count': 2, 'hash_prefix': 'fixture|'}
        ids = np.array([9, 2, 7, 3])
        s = select_samples(ids, np.zeros(4), np.array([1, 1, 2, 3]), np.array([.3, .2, .1, .1]), cfg, ['X'])['X']
        self.assertEqual(s['nearest'], [2, 9]); self.assertEqual(s['boundary'], [3, 7])
        expected = sorted(ids, key=lambda i: (hashlib.sha256(f'fixture|{i}'.encode()).digest(), i))[:2]
        self.assertEqual(s['hash'], expected)

    def test_id_alignment_fail_closed(self):
        f = pd.DataFrame({'movie_id': [2, 1], 'v': [20, 10]})
        self.assertEqual(aligned(f, [1, 2]).v.tolist(), [10, 20])
        with self.assertRaises(RuntimeError): aligned(f, [1, 3])
        with self.assertRaises(RuntimeError): aligned(pd.concat([f, f]), [1, 2])

    def test_text_hash_not_just_id(self):
        primary = {'title': '제목', 'overview': '', 'genres': [{'name': '드라마'}]}
        english = {'title': 'Other', 'overview': 'One small story.'}
        descriptor = {'input_template': '{display_title}|{overview_fallback}|{genre_names}|{director_names}|{top5_cast_names}|{keyword_names}', 'input_prefix': 'passage: '}
        payload = build_embedding_input(descriptor['input_template'], descriptor['input_prefix'], merge_text(primary, english))
        expected = hashlib.sha256(payload.encode()).hexdigest()
        result, source = match_text(primary, [(None, None), ('cached', english)], expected, descriptor)
        self.assertEqual(source, 'cached'); self.assertEqual(result['display_title'], '제목')
        with self.assertRaises(RuntimeError): match_text(primary, [(None, None)], expected, descriptor)
        with self.assertRaises(RuntimeError): validate_english({'status': 200, 'body': {'id': 3}}, 3)


if __name__ == '__main__': unittest.main()
