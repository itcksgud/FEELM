"""Small synthetic protections for temporal and identity contracts."""

import unittest
import numpy as np
import pandas as pd
from rec047_common import (
    H,
    window_start,
    assigned_k,
    user_levels,
    pair_accuracy,
    role,
    development,
    check_contexts,
    check_prediction,
)
from rec047_features import Relations


def fixture():
    rows = []
    for mid, actor in enumerate([11, 11, 22]):
        rows.append(
            dict(
                movie_id=mid,
                genre_ids=[18],
                keyword_ids=[],
                production_country_codes=[],
                original_language="",
                release_year=2000,
                runtime_minutes=100,
                director_ids=[],
                top5_cast_ids=[actor],
                production_company_ids=[],
                collection_ids=[],
            )
        )
    return pd.DataFrame(rows)


class Contracts(unittest.TestCase):
    def test_nonfinite_prediction_cannot_disappear_as_missing_user(self):
        check_prediction(np.array([1.0, 2.0]), 2, np.array([True, False]))
        for p, n, d in [
            (np.array([np.nan, 1]), 2, None),
            (np.array([1.0]), 2, None),
            (np.array([1.0]), 1, np.array([1])),
        ]:
            with self.assertRaises(RuntimeError):
                check_prediction(p, n, d)

    def test_context_boundaries_and_alignment(self):
        uid = next(
            u for u in range(1, 2000) if development(u) and role(u) == "evaluation"
        )
        c = dict(
            uid=uid,
            k=1,
            origin=1000,
            start=0,
            stop=1,
            oi=[0],
            ei=[1],
            stars=[4.5],
            input_timestamps=[999],
            target_timestamps=[1000],
            pre_catalog=1,
        )
        self.assertEqual(check_contexts([c], "evaluation", np.array([10, 20]), 1000), 1)
        for delta in [
            dict(start=1),
            dict(target_timestamps=[999]),
            dict(input_timestamps=[1000]),
            dict(ei=[0]),
        ]:
            with self.assertRaises(RuntimeError):
                check_contexts([{**c, **delta}], "evaluation", np.array([10, 20]), 1000)

    def test_temporal_grid_extension(self):
        origin = 100 * H
        t = np.asarray([origin - 2 * H, origin - H - 1, origin - H, origin - 1])
        a = window_start(t, origin)
        np.testing.assert_array_equal(
            a, [origin - 2 * H, origin - 2 * H, origin - H, origin - H]
        )
        np.testing.assert_array_equal(a, window_start(t, origin + H))
        with self.assertRaises(RuntimeError):
            window_start([origin], origin)

    def test_k_stays_with_episode(self):
        for n in [0, 1, 4, 5, 10, 30, 100]:
            k = assigned_k(123, 1000, n)
            self.assertLessEqual(k, n)
            self.assertIn(k, [0, 1, 5, 10, 30])

    def test_nested_users(self):
        uids = np.arange(1, 10000)
        lv = user_levels(uids)
        s = [set(np.flatnonzero((lv > 0) & (lv <= p))) for p in [25, 50, 100]]
        self.assertTrue(s[0] < s[1] < s[2])
        self.assertTrue(all(role(u) == "train" for u in s[-1]))

    def test_exact_identity_and_guards(self):
        r = Relations(fixture())
        a = r.transform([0], [4.5], [1, 2]).toarray()
        ci = r.names.index("CAST_linked_fraction")
        self.assertEqual(a[0, ci], 1)
        self.assertEqual(a[1, ci], 0)
        self.assertAlmostEqual(a[0, r.names.index("CAST_weighted_stars")], 0.9, 6)
        for oi, stars, ei in [
            ([-1], [4], [1]),
            ([0], [4], [0]),
            ([0], [4.1], [1]),
            ([0], [4], [1, 1]),
        ]:
            with self.assertRaises(RuntimeError):
                r.transform(oi, stars, ei)

    def test_no_input_is_explicit(self):
        r = Relations(fixture())
        a = r.transform([], [], [1]).toarray()[0]
        self.assertEqual(a[r.names.index("input_mean")], 0)
        self.assertEqual(a[r.names.index("has_input")], 0)

    def test_pairs_use_real_unequal_stars(self):
        self.assertIsNone(pair_accuracy([3, 3], [1, 5]))
        self.assertEqual(pair_accuracy([1, 3, 5], [1, 1, 2]), 5 / 6)


if __name__ == "__main__":
    unittest.main()
