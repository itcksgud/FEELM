import unittest

import numpy as np
import pandas as pd

from fm_v5_evaluate import _paired_user, _ranking_contributions


class FmV5EvaluateTest(unittest.TestCase):
    def frame(self):
        rows = []
        for episode, uid in (("e1", 1), ("e2", 2)):
            for rank, (movie, state, rating, target) in enumerate((
                (10, "POSITIVE_OBSERVED", 5.0, True),
                (11, "NEGATIVE_OBSERVED", 1.0, False),
                (12, "UNKNOWN_SAMPLED", np.nan, False),
            )):
                rows.append({"episode_id": episode, "uid": uid, "target_movie_id": 10,
                             "candidate_movie_id": movie, "prediction_at": 100, "n": 7,
                             "n_bucket": "5-9", "total_history_count": 7,
                             "supported_history_count": 7, "is_full_history": True,
                             "candidate_rank": rank, "is_target": target, "label_state": state,
                             "observed_rating": rating})
        return pd.DataFrame(rows)

    def test_unknown_slot_is_not_removed_or_negative(self):
        frame = self.frame()
        # Unknown is ranked first; it remains an empty-gain slot instead of being removed.
        score = np.tile(np.asarray([2.0, 1.0, 3.0]), 2)
        result = _ranking_contributions(frame, score, "x")
        self.assertTrue((result["unknown_fraction_at_2"] == 0.5).all())
        self.assertTrue(result["ndcg_at_2"].notna().all())
        self.assertTrue((result["target_hit_at_2"] == 1.0).all())

    def test_paired_bootstrap_is_deterministic(self):
        frame = self.frame()
        good = _ranking_contributions(frame, np.tile(np.asarray([3.0, 1.0, 2.0]), 2), "good")
        bad = _ranking_contributions(frame, np.tile(np.asarray([1.0, 3.0, 2.0]), 2), "bad")
        first = _paired_user(good, bad, "ndcg_at_2", 339, 100)
        second = _paired_user(good, bad, "ndcg_at_2", 339, 100)
        self.assertEqual(first, second)
        self.assertGreater(first["delta"], 0.0)


if __name__ == "__main__":
    unittest.main()
