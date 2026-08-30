from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from movielens_time_split_profile import (  # noqa: E402
    build_user_profiles,
    choose_boundaries,
    split_masks,
    validate_splits,
)


class TimeSplitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ratings = pd.DataFrame(
            {
                "user_id": pd.Series([1, 1, 2, 2, 3, 3, 4, 4, 5, 5], dtype="int32"),
                "movie_id": pd.Series(range(1, 11), dtype="int32"),
                "rating": pd.Series(
                    [1.0, 2.0, 4.0, 5.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
                    dtype="float32",
                ),
                "timestamp": pd.Series(
                    [10, 20, 30, 40, 50, 60, 70, 80, 90, 100], dtype="int64"
                ),
            }
        )

    def test_global_split_is_strict_and_preserves_rows(self) -> None:
        train_boundary, validation_boundary = choose_boundaries(
            self.ratings["timestamp"].to_numpy(), 0.6, 0.2
        )
        masks = split_masks(
            self.ratings["timestamp"], train_boundary, validation_boundary
        )
        splits = {name: self.ratings.loc[mask] for name, mask in masks.items()}
        result = validate_splits(splits, len(self.ratings))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(sum(len(frame) for frame in splits.values()), 10)
        self.assertLess(
            splits["train"]["timestamp"].max(),
            splits["validation"]["timestamp"].min(),
        )
        self.assertLess(
            splits["validation"]["timestamp"].max(),
            splits["test"]["timestamp"].min(),
        )

    def test_boundary_timestamp_goes_entirely_to_later_split(self) -> None:
        timestamps = pd.Series([10, 10, 20, 20, 30, 30, 40, 40, 50, 60])
        train_boundary, validation_boundary = choose_boundaries(
            timestamps.to_numpy(), 0.5, 0.2
        )
        masks = split_masks(timestamps, train_boundary, validation_boundary)
        self.assertFalse(bool((timestamps[masks["train"]] == train_boundary).any()))
        self.assertTrue(
            bool((timestamps[masks["validation"]] == train_boundary).all())
            or bool((timestamps[masks["validation"]] == train_boundary).any())
        )
        self.assertFalse(
            bool((timestamps[masks["validation"]] == validation_boundary).any())
        )
        self.assertTrue(bool((timestamps[masks["test"]] == validation_boundary).any()))

    def test_user_profiles_use_given_train_only(self) -> None:
        train = self.ratings.iloc[:6]
        profiles, summary = build_user_profiles(train)
        self.assertEqual(summary["train_users"], 3)
        self.assertEqual(int(profiles["rating_count"].sum()), 6)
        user_one = profiles.loc[profiles["user_id"] == 1].iloc[0]
        self.assertAlmostEqual(float(user_one["rating_mean"]), 1.5)
        self.assertEqual(int(user_one["rating_5.0_count"]), 0)


if __name__ == "__main__":
    unittest.main()
