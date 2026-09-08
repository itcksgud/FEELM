import sys
from pathlib import Path
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rec_ev_037_descriptions import term_statistics, describe_channel, validate_excerpts
from rec_ev_033_tastes import ctfidf


class DescriptionTests(unittest.TestCase):
    def test_denominators_include_missing_metadata(self):
        within, outside, delta = term_statistics([[2, 0], [1, 1]], [10, 20])
        np.testing.assert_allclose(within, [[.2, 0], [.05, .05]])
        np.testing.assert_allclose(outside, [[.05, .05], [.2, 0]])
        np.testing.assert_allclose(delta, within - outside)
        with self.assertRaises(RuntimeError): term_statistics([[11], [1]], [10, 20])

    def test_channels_are_recomputed_and_ties_stable(self):
        full = np.array([[100, 2, 2], [10, 4, 4]])
        split = ctfidf(full[:, 1:])
        self.assertFalse(np.allclose(split, ctfidf(full)[:, 1:]))
        scores, top, diag = describe_channel(full[:, 1:], ['keyword:9', 'keyword:2'], ['b', 'a'], [110, 20], ['x', 'y'], 15)
        self.assertEqual([r['term_key'] for r in top['x']], ['keyword:2', 'keyword:9'])
        self.assertEqual(diag['top_term_intersection_counts'], [[2, 2], [2, 2]])
        np.testing.assert_array_equal(scores, split)

    def test_zero_channel_no_nan(self):
        scores, top, diag = describe_channel(np.zeros((2, 1)), ['keyword:1'], ['a'], [2, 3], ['x', 'y'], 15)
        self.assertEqual(top, {'x': [], 'y': []})
        self.assertTrue(np.isfinite(scores).all())
        self.assertEqual(diag['ctfidf_cosine'], [[0., 0.], [0., 0.]])

    def test_quote_membership_and_unicode_limits(self):
        cards = [{'card': 'C001', 'overview': '소년은 집으로 돌아온다. Family returns home.'}]
        cfg = {'excerpt_min_count': 1, 'excerpt_max_count': 2, 'excerpt_max_characters': 180}
        good = [{'card': 'C001', 'excerpts': ['소년은 집으로 돌아온다.', 'Family returns home.'], 'theme': '귀향', 'note': ''}]
        self.assertEqual(validate_excerpts(cards, good, cfg), good)
        for bad in [['소년은 집으로...'], ['집으로', '집으로'], [], ['wrong quote']]:
            row = {**good[0], 'excerpts': bad}
            with self.assertRaises(RuntimeError): validate_excerpts(cards, [row], cfg)
        with self.assertRaises(RuntimeError): validate_excerpts(cards, good * 2, cfg)
        with self.assertRaises(RuntimeError): validate_excerpts(cards, good, {**cfg, 'excerpt_max_characters': 3})


if __name__ == '__main__': unittest.main()
