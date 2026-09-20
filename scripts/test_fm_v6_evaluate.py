import unittest

import numpy as np
import pandas as pd

from fm_v6_evaluate import contributions, paired


class FmV6EvaluateTest(unittest.TestCase):
    def frame(self):
        rows = []
        for uid in (1, 2):
            for rank in range(12):
                observed = rank < 10
                rows.append({
                    "episode_id": f"e{uid}", "uid": uid, "target_movie_id": 100,
                    "prediction_at": 1000, "candidate_movie_id": 100 + rank, "candidate_rank": rank,
                    "n": 5, "n_bucket": "5-9", "supported": True,
                    "candidate_genres": "Drama" if rank % 2 else "Comedy",
                    "label_state": ("POSITIVE_OBSERVED" if rank < 5 else
                                    "NEGATIVE_OBSERVED" if observed else "UNKNOWN_SAMPLED"),
                    "observed_rating": float(5 - rank % 5) if observed else np.nan,
                    "good": float(12 - rank), "bad": float(rank),
                })
        return pd.DataFrame(rows)

    def test_ten_observed_judgments_make_ndcg_at_10_defined(self):
        result = contributions(self.frame(), "good", "good")
        self.assertTrue(result.ndcg_at_10.notna().all())
        self.assertTrue((result.observed_judgments == 10).all())

    def test_user_bootstrap_is_deterministic(self):
        frame = self.frame()
        good, bad = contributions(frame, "good", "good"), contributions(frame, "bad", "bad")
        self.assertEqual(paired(good, bad, "ndcg_at_2", 339, 100),
                         paired(good, bad, "ndcg_at_2", 339, 100))

    def test_unknown_top_items_have_zero_low_rating_exposure_with_fixed_denominator(self):
        frame = self.frame()
        frame["unknown_first"] = np.where(frame.label_state == "UNKNOWN_SAMPLED", 100.0, 0.0)
        result = contributions(frame, "unknown_first", "unknown_first")
        self.assertTrue((result.low_exposure_at_2 == 0.0).all())

    def test_zero_idcg_is_zero_and_user_is_retained(self):
        frame = self.frame()
        frame.loc[frame.observed_rating.notna(), "observed_rating"] = 0.5
        result = contributions(frame, "good", "good")
        self.assertEqual(len(result), 2)
        self.assertTrue((result.ndcg_at_2 == 0.0).all())
        self.assertTrue(result.zero_idcg_at_2.all())

    def test_paired_user_set_mismatch_fails_closed(self):
        frame = self.frame()
        good, bad = contributions(frame, "good", "good"), contributions(frame, "bad", "bad")
        with self.assertRaisesRegex(RuntimeError, "paired user sets differ"):
            paired(good, bad[bad.uid == 1], "ndcg_at_2", 339, 10)


if __name__ == "__main__":
    unittest.main()
