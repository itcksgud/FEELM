from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from build_rec_ev_019a_cohorts import (
    _bucket_lookup,
    _read_filtered_parquet,
    _write_base_train,
    build_candidate_core,
    materialize_role_cohorts,
    user_key,
)
from recommendation_binary_onboarding_preflight import global_midrank_ecdf


def protocol() -> dict:
    return {
        "inputs": {
            "binary_k_primary": [0, 5, 10],
            "binary_shrinkage_lambda": 10.0,
            "binary_relative_like_min": 0.15,
            "binary_relative_dislike_max": -0.15,
        },
        "relevance": {
            "future_window_ratings": 10,
            "minimum_future_positives": 3,
            "positive_midrank_utility_min": 0.65,
            "negative_midrank_utility_max": 0.35,
        },
    }


class BuildRecEv019ACohortsTest(unittest.TestCase):
    def test_user_key_is_stable_lowercase_hex_without_raw_id(self) -> None:
        first = user_key(1234)
        self.assertEqual(first, user_key(1234))
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotIn("1234", first)

    def test_candidate_core_uses_only_cutoff_safe_base_rows_and_valid_links(self) -> None:
        base = pd.DataFrame(
            {
                "movie_id": [1, 1, 2, 3],
                "timestamp": [10, 20, 15, 5],
                "rating": [4.0, 5.0, 3.0, 2.0],
            }
        )
        links = pd.DataFrame({"movie_id": [1, 3], "tmdb_id": pd.Series([101, 303], dtype="Int64")})
        result = build_candidate_core(base, links)
        self.assertEqual([1, 3], result["movie_id"].tolist())
        self.assertEqual([2, 1], result["base_train_interaction_count"].tolist())
        self.assertEqual([10, 5], result["first_base_train_timestamp"].tolist())
        self.assertEqual({"LINK_PRESENT"}, set(result["identity_status"]))

    def test_role_cohort_emits_strict_windows_and_nested_prefixes(self) -> None:
        ratings = [5.0, 0.5] * 10 + [5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5]
        frame = pd.DataFrame(
            {
                "user_id": [7] * len(ratings),
                "movie_id": list(range(1, len(ratings) + 1)),
                "rating": ratings,
                "timestamp": list(range(100, 100 + len(ratings))),
                "user_bucket": [50] * len(ratings),
            }
        )
        global_midrank = global_midrank_ecdf(np.repeat(np.arange(0.5, 5.01, 0.5), 10))
        prefixes, windows, summary = materialize_role_cohorts(
            frame,
            role="VALIDATION",
            global_midrank=global_midrank,
            candidate_movie_ids=set(frame["movie_id"]),
            protocol=protocol(),
        )
        prefix_frame = pd.DataFrame(prefixes)
        window_frame = pd.DataFrame(windows)
        self.assertEqual({0, 5, 10}, set(window_frame["k"]))
        self.assertEqual(10, len(window_frame[window_frame["k"] == 0]))
        self.assertEqual(5, len(prefix_frame[prefix_frame["k"] == 5]))
        self.assertEqual(10, len(prefix_frame[prefix_frame["k"] == 10]))
        k5 = prefix_frame[prefix_frame["k"] == 5].sort_values("input_rank")
        k10 = prefix_frame[prefix_frame["k"] == 10].sort_values("input_rank").head(5)
        self.assertEqual(k5["movie_id"].tolist(), k10["movie_id"].tolist())
        self.assertEqual(1, summary["eligibility"]["10"]["strict_eligible_users"])
        self.assertTrue((window_frame.groupby("k")["is_positive"].sum() >= 3).all())

    def test_missing_candidate_positive_drops_user_without_positive_injection(self) -> None:
        ratings = [5.0, 0.5] * 12
        frame = pd.DataFrame(
            {
                "user_id": [8] * len(ratings),
                "movie_id": list(range(1, len(ratings) + 1)),
                "rating": ratings,
                "timestamp": list(range(len(ratings))),
                "user_bucket": [60] * len(ratings),
            }
        )
        global_midrank = global_midrank_ecdf(np.repeat(np.arange(0.5, 5.01, 0.5), 10))
        prefixes, windows, summary = materialize_role_cohorts(
            frame,
            role="LOCKED_TEST",
            global_midrank=global_midrank,
            candidate_movie_ids=set(),
            protocol=protocol(),
        )
        self.assertEqual([], prefixes)
        self.assertEqual([], windows)
        self.assertGreater(summary["eligibility"]["5"]["no_positive_in_provisional_candidate"], 0)

    def test_parquet_role_filter_uses_locked_hash_bucket(self) -> None:
        prefix = "feelm-rec-vnext-user-split-v1|"
        lookup = _bucket_lookup(prefix, maximum_user_id=50)
        expected_users = [user_id for user_id in range(1, 51) if 40 <= int(lookup[user_id]) <= 49]
        frame = pd.DataFrame(
            {
                "user_id": list(range(1, 51)),
                "movie_id": list(range(101, 151)),
                "rating": [4.0] * 50,
                "timestamp": list(range(50)),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ratings.parquet"
            frame.to_parquet(path, index=False)
            actual = _read_filtered_parquet(path, lookup, 40, 49, batch_size=7)
        self.assertEqual(expected_users, actual["user_id"].tolist())
        self.assertTrue(actual["user_bucket"].between(40, 49).all())

    def test_base_train_writer_round_trips_string_keys_and_sort_order(self) -> None:
        frame = pd.DataFrame(
            {
                "user_id": [2, 1, 2, 1],
                "movie_id": [5, 7, 4, 6],
                "rating": [4.0, 3.0, 5.0, 2.0],
                "timestamp": [20, 10, 10, 10],
                "user_bucket": pd.Series([1, 2, 1, 2], dtype="uint8"),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "base.parquet"
            _write_base_train(path, frame)
            table = pq.read_table(path)
        self.assertEqual("string", str(table.schema.field("user_key").type))
        actual = table.to_pandas()
        expected = actual.sort_values(["user_key", "timestamp", "movie_id"], ignore_index=True)
        pd.testing.assert_frame_equal(actual.reset_index(drop=True), expected)


if __name__ == "__main__":
    unittest.main()
