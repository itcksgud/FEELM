from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from recommendation_cold_start_blend import (  # noqa: E402
    alpha_grid,
    ranking_per_user,
    stable_user_selection,
)


class ColdStartBlendTest(unittest.TestCase):
    def test_alpha_grid_includes_both_endpoints(self) -> None:
        np.testing.assert_allclose(alpha_grid(0.2), [0.0, 0.2, 0.4, 0.6, 0.8, 1.0])

    def test_user_split_is_constant_within_user_and_has_both_sides(self) -> None:
        users = np.repeat(np.arange(1, 101), 3)
        selected = stable_user_selection(users, 42)
        frame = pd.DataFrame({"user_id": users, "selected": selected})
        self.assertEqual(int(frame.groupby("user_id")["selected"].nunique().max()), 1)
        self.assertTrue(bool(selected.any()))
        self.assertTrue(bool((~selected).any()))

    def test_ranking_per_user_finds_positive_rank(self) -> None:
        frame = pd.DataFrame(
            {
                "user_id": [1, 1, 1, 2, 2, 2],
                "is_positive": [0, 1, 0, 0, 0, 1],
            }
        )
        result = ranking_per_user(frame, np.array([0.1, 0.9, 0.2, 0.9, 0.8, 0.1]))
        self.assertEqual(result.loc[1, "rank"], 1)
        self.assertEqual(result.loc[2, "rank"], 3)
        self.assertEqual(result.loc[1, "hit_at_10"], 1.0)


if __name__ == "__main__":
    unittest.main()
