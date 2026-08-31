from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from recommendation_user_case_study import (
    CASE_SELECTION_SALT,
    load_movie_metadata,
    movie_year,
    policy_change,
    stable_case_user,
)


class RecommendationUserCaseStudyTest(unittest.TestCase):
    def test_case_selection_is_order_independent_and_deduplicated(self) -> None:
        left = stable_case_user(np.asarray([9, 3, 7, 3]))
        right = stable_case_user(np.asarray([7, 9, 3]))
        self.assertEqual(left, right)
        self.assertTrue(CASE_SELECTION_SALT.startswith("REC-EV-016"))

    def test_case_selection_rejects_empty_cohort(self) -> None:
        with self.assertRaises(RuntimeError):
            stable_case_user(np.asarray([], dtype=np.int64))

    def test_movie_year_only_reads_terminal_year(self) -> None:
        self.assertEqual(1999, movie_year("Example (Director's Cut) (1999)"))
        self.assertIsNone(movie_year("Example without year"))

    def test_policy_change_preserves_rank_order(self) -> None:
        metadata = pd.DataFrame(
            {"title": ["One", "Two", "Three", "Four"], "year": [None] * 4, "genres": [[] for _ in range(4)]},
            index=pd.Index([1, 2, 3, 4], name="movie_id"),
        )
        change = policy_change(np.asarray([1, 2, 3]), np.asarray([2, 4, 1]), metadata)
        self.assertEqual(2, change["overlap_at_10"])
        self.assertEqual(["Four"], change["entered"])
        self.assertEqual(["Three"], change["exited"])


if __name__ == "__main__":
    unittest.main()
