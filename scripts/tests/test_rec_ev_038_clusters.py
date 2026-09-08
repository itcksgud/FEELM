"""Small invariant tests; no real catalogue access or fits."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rec_ev_038_clusters import (canonical_labels, display_labels, hash_order, make_features,
                               nmf_contribution, validate_descriptions, validate_judgments)


class ClusterContracts(unittest.TestCase):
    def test_features_count_dedup_and_missing(self):
        metadata = pd.DataFrame({'genre_ids': [[1, 1], [1, 2], [2]], 'keyword_ids': [[4, 4], [], [99]]})
        terms = pd.DataFrame({'term_key': ['genre:1', 'genre:2', 'keyword:4'], 'movie_document_frequency': [2, 2, 1]})
        presence, features, idf, masks, raw = make_features(metadata, terms)
        np.testing.assert_array_equal(presence.toarray(), [[1, 0, 1], [1, 1, 0], [0, 1, 0]])
        np.testing.assert_array_equal(raw, [1, 0, 1])
        self.assertEqual(features['TAG'].getnnz(axis=1).tolist(), [1, 0, 0])
        both = features['BOTH'].toarray()
        np.testing.assert_allclose(np.linalg.norm(both, axis=1), 1)
        self.assertAlmostEqual(float(np.sum(both[0, :2] ** 2)), .5)
        np.testing.assert_allclose(both[1, :2], features['GENRE'].toarray()[1])
        np.testing.assert_allclose(idf, np.log(4 / np.array([3, 3, 2])) + 1)

    def test_wrong_df_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'frequency'):
            make_features(pd.DataFrame({'genre_ids': [[1]], 'keyword_ids': [[]]}),
                          pd.DataFrame({'term_key': ['genre:1'], 'movie_document_frequency': [2]}))

    def test_nmf_scale_invariance_and_reconstruction(self):
        w = np.array([[1., 3., 2.], [0., 2., 1.]])
        h = np.array([[2., 0., 0.], [0., 1., 3.], [0., 0., 0.]])
        u, contribution, labels = nmf_contribution(w, h)
        scale = np.array([100., .001, 7.])
        u2, c2, labels2 = nmf_contribution(w / scale, h * scale[:, None])
        np.testing.assert_allclose(contribution @ u, w @ h)
        np.testing.assert_allclose(c2 @ u2, w @ h)
        np.testing.assert_allclose(c2, contribution)
        np.testing.assert_array_equal(labels, labels2)

    def test_nmf_zero_and_tie(self):
        _, _, labels = nmf_contribution(np.array([[0., 0.], [1., 1.]]), np.eye(2))
        np.testing.assert_array_equal(labels, [-1, 0])
        with self.assertRaisesRegex(RuntimeError, 'invalid'):
            nmf_contribution(np.array([[-1., 2.]]), np.eye(2))

    def test_canonical_permutation_does_not_change_partition(self):
        ids = np.array([10, 20, 30, 40, 50])
        first, order = canonical_labels(np.array([2, 1, 2, 0, -1]), ids, 3)
        other, _ = canonical_labels(np.array([0, 2, 0, 1, -1]), ids, 3)
        np.testing.assert_array_equal(first, other)
        np.testing.assert_array_equal(first, [0, 1, 0, 2, -1])

    def test_fallback_does_not_create_native_evidence(self):
        native = np.array([-1, 2, 2, 1, -1])
        result = display_labels(native, 4)
        np.testing.assert_array_equal(result, [2, 2, 2, 1, 2])
        np.testing.assert_array_equal(native, [-1, 2, 2, 1, -1])
        self.assertEqual(len(set(native[native >= 0])), 2)

    def test_hash_order_independent_of_input_order(self):
        a = np.arange(20); b = a[::-1]
        np.testing.assert_array_equal(a[hash_order(a, 'test|')], b[hash_order(b, 'test|')])

    def test_description_exact_source_and_unique_packets(self):
        packets = [{'packet': 'P1', 'films': [{'card': f'C{i}', 'overview': '아들이 집으로 돌아온다.'} for i in range(4)]}]
        rows = [{'packet': 'P1', 'description': '가족과 귀향', 'coherence': 'COHERENT',
                 'films': [{'card': f'C{i}', 'support': 'FIT', 'excerpt': '집으로 돌아온다.'} for i in range(4)], 'note': ''}]
        self.assertEqual(validate_descriptions(packets, rows), rows)
        rows[0]['coherence'] = 'INSUFFICIENT'
        with self.assertRaisesRegex(RuntimeError, 'support mismatch'): validate_descriptions(packets, rows)
        rows[0]['coherence'] = 'COHERENT'
        rows[0]['films'][0]['support'] = 'CONFLICT'
        with self.assertRaisesRegex(RuntimeError, 'support mismatch'): validate_descriptions(packets, rows)
        rows[0]['films'][0]['support'] = 'FIT'
        rows[0]['films'][0]['excerpt'] = '부모에게 복수한다.'
        with self.assertRaisesRegex(RuntimeError, 'excerpt'): validate_descriptions(packets, rows)

    def test_judgments_keep_ambiguity_distinct_from_insufficient(self):
        packets = [{'card': 'C1', 'methods': [{'method': 'M1', 'groups': [{'group': 'G1'}, {'group': 'G2'}]}]}]
        rows = [{'card': 'C1', 'method': 'M1', 'sufficient': True, 'acceptable': ['G1', 'G2'], 'reason': '두 설명에 모두 부합'}]
        self.assertEqual(validate_judgments(packets, rows), rows)
        rows[0]['sufficient'] = False
        with self.assertRaisesRegex(RuntimeError, 'insufficient'): validate_judgments(packets, rows)
        rows[0]['acceptable'] = []
        self.assertEqual(validate_judgments(packets, rows), rows)


if __name__ == '__main__': unittest.main()
