import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from strict_evidence_v12 import DirectIndex, metadata, public_references
from audit_strict_multiple_links_policy import contextual_evidence


class StrictEvidenceV12Test(unittest.TestCase):
    def fixture(self):
        return pd.DataFrame({"movie_id": [1, 2, 3, 4, 5, 6], "tmdb_id": [101, 102, 103, 104, 105, 106],
            "collection_ids": [[10], [10], [11], [], [12], [10]],
            "director_ids": [[20], [20], [21], [20], [22], [20]],
            "top5_cast_ids": [[30], [30], [30, 31], [32], [31], [30, 31, 32]],
            "origin_country_codes": [["US"]] * 6, "genre_ids": [[28], [28], [35], [28], [28], [28]]})

    def test_fast_index_matches_reviewed_r3e_semantics(self):
        frame = self.fixture()
        movies = metadata(frame)
        history = frame.iloc[:5].copy()
        history["score"] = [5, 4, 5, 2, 3]
        index = DirectIndex(movies)
        for row in history.itertuples():
            index.add(row.movie_id, row.score)
        values, _ = index.query(movies[6])
        expected = contextual_evidence(frame.iloc[5], history)
        for side, offset in (("positive", 0), ("negative", 7)):
            e = expected[side]
            np.testing.assert_array_equal(values[offset:offset + 7],
                [e["series"], e["director"], e["cast_sources"], e["cast_people"], e["cast_units"], e["units"], e["high_specificity"]])

    def test_current_target_is_not_added_before_prediction(self):
        frame = self.fixture()
        movies = metadata(frame)
        index = DirectIndex(movies)
        self.assertEqual(index.query(movies[2])[0][5], 0)
        index.add(1, 5)
        self.assertEqual(index.query(movies[2])[0][5], 1)
        with self.assertRaises(ValueError):
            index.add(1, 1)

    def test_same_actor_franchise_and_different_genre_do_not_inflate(self):
        frame = self.fixture()
        frame.at[5, "collection_ids"] = []
        frame.at[5, "director_ids"] = []
        movies = metadata(frame)
        index = DirectIndex(movies)
        for mid in (1, 2, 3):
            index.add(mid, 5)
        value, _ = index.query(movies[6])
        self.assertEqual(value[5], 1)
        self.assertEqual(value[6], 0)

    def test_candidate_excluded_references_ignore_adult(self):
        n = 41
        frame = pd.DataFrame({"tmdb_id": np.arange(n), "release_date": ["2020-01-01"] * n,
            "status": ["Released"] * n, "adult": [False] * 40 + [True],
            "tmdb_vote_count": np.arange(n) + 100, "tmdb_vote_average": [7.] * n,
            "tmdb_public_score": np.arange(n) / n, "kobis_audience_cumulative": np.arange(n) + 1000,
            "kobis_link_status": ["VERIFIED_TEST"] * n, "kobis_value_valid": [True] * n,
            "origin_country_codes": [["US"]] * n, "genre_ids": [[35]] * n})
        frame.loc[40, ["tmdb_vote_count", "tmdb_public_score"]] = [1000000, 100]
        refs = public_references(frame, 139.)
        self.assertEqual(refs.loc[39, "tmdb_reference"], np.median(np.arange(129, 139)))
        self.assertEqual(refs.loc[39, "kobis_reference"], np.quantile(np.arange(1000, 1039), .9))
        self.assertFalse(refs.loc[40, "eligible_movie"])


if __name__ == "__main__":
    unittest.main()
