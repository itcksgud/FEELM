from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from recommendation_exploration_full_catalog import (
    canonical_bytes,
    conservative_genre_diversity,
    deterministic_top_k,
    select_warm_positives,
)


class FullCatalogProtocolTest(unittest.TestCase):
    def test_top_k_is_score_descending_then_movie_id_ascending(self) -> None:
        movies = np.asarray([40, 10, 30, 20, 50])
        scores = np.asarray([0.5, 0.9, 0.5, 0.9, -np.inf])
        self.assertEqual([10, 20, 30], deterministic_top_k(movies, scores, 3).tolist())

    def test_seen_exclusion_is_respected(self) -> None:
        movies = np.asarray([1, 2, 3, 4])
        scores = np.asarray([0.9, -np.inf, 0.8, -np.inf])
        self.assertEqual([1, 3], deterministic_top_k(movies, scores, 4).tolist())

    def test_positive_is_not_injected_by_top_k(self) -> None:
        movies = np.asarray([1, 2, 3, 4])
        scores = np.asarray([0.4, 0.3, 0.2, 0.1])
        positive = 4
        self.assertNotIn(positive, deterministic_top_k(movies, scores, 2).tolist())

    def test_unknown_genre_pair_is_zero_diversity_and_uncovered(self) -> None:
        genres = np.asarray([[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]])
        available = np.asarray([False, True, False])
        diversity, coverage = conservative_genre_diversity(np.asarray([1, 2]), genres, available)
        self.assertEqual(0.0, diversity)
        self.assertEqual(0.0, coverage)

    def test_known_genre_pair_reports_coverage_and_distance(self) -> None:
        genres = np.asarray([[1.0, 0.0], [0.0, 1.0]])
        available = np.asarray([True, True])
        diversity, coverage = conservative_genre_diversity(np.asarray([0, 1]), genres, available)
        self.assertEqual(1.0, diversity)
        self.assertEqual(1.0, coverage)

    def test_warm_positive_uses_latest_natural_row(self) -> None:
        heldout = pd.DataFrame({
            "user_id": [1, 1], "movie_id": [1, 2], "rating": [5.0, 5.0], "timestamp": [10, 20]
        })
        user_counts = np.asarray([0, 2])
        movie_counts = np.asarray([0, 1, 1])
        profiles = np.zeros((2, 10), dtype=np.int32)
        profiles[1, 6] = 2  # rating 3.5 gives both held-out 5.0 values high relative utility
        totals = profiles.sum(axis=1)
        selected = select_warm_positives(heldout, user_counts, movie_counts, profiles, totals)
        self.assertEqual(2, int(selected.iloc[0]["movie_id"]))

    def test_lock_hash_changes_on_protocol_mutation(self) -> None:
        protocol = {"positive_injection": False, "top_candidates": 500}
        locked = hashlib.sha256(canonical_bytes(protocol)).hexdigest()
        protocol["top_candidates"] = 499
        self.assertNotEqual(locked, hashlib.sha256(canonical_bytes(protocol)).hexdigest())

    def test_canonical_output_is_byte_identical(self) -> None:
        left = canonical_bytes({"b": 2, "a": [1, 3]})
        right = canonical_bytes(json.loads(left))
        self.assertEqual(left, right)


if __name__ == "__main__":
    unittest.main()
