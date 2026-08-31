from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from recommendation_protocol_v4 import (
    locked_movie_order,
    midrank_utilities,
    slate_digest,
    user_bucket,
    user_key,
    wilson_lower,
)


class RecommendationTop2V4PreflightTest(unittest.TestCase):
    def test_locked_hash_order_is_deterministic_and_nested(self) -> None:
        movie_ids = [1, 2, 3, 4, 5, 6]
        order = locked_movie_order(17, 42, movie_ids)
        self.assertEqual(order, locked_movie_order(17, 42, list(reversed(movie_ids))))
        self.assertEqual(order[:3], locked_movie_order(17, 42, movie_ids)[:3])
        self.assertEqual(32, len(slate_digest(17, 42, 1)))

    def test_midrank_utility_uses_full_user_distribution(self) -> None:
        actual = midrank_utilities([1.0, 3.0, 5.0])
        self.assertAlmostEqual(0.3, actual[0])
        self.assertAlmostEqual(0.5, actual[1])
        self.assertAlmostEqual(0.7, actual[2])

    def test_ties_share_the_same_utility(self) -> None:
        actual = midrank_utilities([1.0, 1.0, 5.0, 5.0])
        self.assertEqual(actual[0], actual[1])
        self.assertEqual(actual[2], actual[3])

    def test_user_identifiers_are_stable_but_not_raw(self) -> None:
        self.assertEqual(user_bucket(123), user_bucket(123))
        self.assertEqual(64, len(user_key(123)))
        self.assertNotIn("123", user_key(123))

    def test_wilson_lower_is_conservative(self) -> None:
        self.assertGreater(wilson_lower(80, 100), 0.70)
        self.assertLess(wilson_lower(80, 100), 0.80)
        self.assertEqual(0.0, wilson_lower(0, 0))


if __name__ == "__main__":
    unittest.main()
