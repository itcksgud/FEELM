from __future__ import annotations

import unittest

import numpy as np

from recommendation_binary_onboarding_preflight import (
    binary_k_eligibility,
    global_midrank_ecdf,
    sequential_binary_labels,
    stable_user_bucket,
    strict_binary_k_eligibility,
    validate_protocol,
)


class RecommendationBinaryOnboardingPreflightTest(unittest.TestCase):
    def test_user_bucket_is_deterministic_and_bounded(self) -> None:
        first = stable_user_bucket(1234, split_prefix="split-v1|")
        second = stable_user_bucket(1234, split_prefix="split-v1|")
        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 0)
        self.assertLess(first, 100)

    def test_binary_labels_keep_neutral_ratings_unlabeled(self) -> None:
        global_midrank = global_midrank_ecdf([0.5, 1.0, 2.5, 4.0, 5.0])
        labels = sequential_binary_labels(
            [5.0, 2.5, 0.5],
            global_midrank,
            shrinkage=10.0,
            like_min=0.15,
            dislike_max=-0.15,
        )
        by_position = {position: label for position, label, _ in labels}
        self.assertEqual(1, by_position[0])
        self.assertNotIn(1, by_position)
        self.assertEqual(-1, by_position[2])

    def test_k_eligibility_requires_future_window_after_kth_label(self) -> None:
        labels = [(0, 1, 0.2), (3, -1, -0.2), (5, 1, 0.3)]
        eligible = binary_k_eligibility(
            labels, rating_count=16, k=3, future_window=10
        )
        ineligible = binary_k_eligibility(
            labels, rating_count=15, k=3, future_window=10
        )
        self.assertTrue(eligible["eligible"])
        self.assertTrue(eligible["both_classes"])
        self.assertFalse(ineligible["eligible"])

    def test_protocol_rejects_binary_to_rating_conversion(self) -> None:
        protocol = {
            "user_split": {
                "base_train_buckets": [0, 39],
                "router_train_buckets": [40, 49],
                "validation_buckets": [50, 59],
                "test_buckets": [60, 99],
            },
            "candidate": {
                "positive_injection": False,
                "missing_model_artifact_policy": "KEEP_WITH_DECLARED_FALLBACK",
                "provisional_identity_basis": "MOVIELENS_LINKS_TMDB_ID_PRESENT",
            },
            "inputs": {
                "binary_to_numeric_rating_forbidden": False,
                "unrated_as_negative_forbidden": True,
            },
        }
        with self.assertRaisesRegex(ValueError, "binary-to-rating"):
            validate_protocol(protocol)

    def test_strict_eligibility_requires_positive_and_candidate(self) -> None:
        ratings = np.asarray(
            [5.0, 0.5, 1.0, 2.0, 3.0, 3.5, 4.0, 4.5, 5.0, 4.5, 5.0]
        )
        movies = np.arange(1, 12)
        labels = [(0, 1, 0.2)]
        eligible = strict_binary_k_eligibility(
            labels,
            ratings=ratings,
            movie_ids=movies,
            candidate_movie_ids={9},
            k=1,
            future_window=10,
            positive_midrank_min=0.65,
            minimum_positives=3,
        )
        no_candidate = strict_binary_k_eligibility(
            labels,
            ratings=ratings,
            movie_ids=movies,
            candidate_movie_ids=set(),
            k=1,
            future_window=10,
            positive_midrank_min=0.65,
            minimum_positives=3,
        )
        self.assertTrue(eligible["minimum_positives"])
        self.assertTrue(eligible["eligible"])
        self.assertFalse(no_candidate["eligible"])

    def test_global_midrank_is_monotonic(self) -> None:
        values = global_midrank_ecdf(np.repeat(np.arange(0.5, 5.01, 0.5), 2))
        self.assertTrue(np.all(np.diff(values) > 0))


if __name__ == "__main__":
    unittest.main()
