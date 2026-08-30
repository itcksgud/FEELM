from __future__ import annotations

import unittest

from feelm_recommender.interpretation import (
    InterpretationRating,
    confidence_for_k,
    interpret_recommendations,
    personal_ecdf,
    select_validated_k,
)
from test_service_policy import build_core


MOVIE_A = "00000000-0000-0000-0000-000000000001"
MOVIE_B = "00000000-0000-0000-0000-000000000002"


class RecommendationInterpretationTest(unittest.TestCase):
    def test_k_floor_and_confidence_cover_every_validated_bucket(self) -> None:
        expected = {
            0: (0, "INSUFFICIENT_DATA"),
            1: (1, "LOW"),
            2: (1, "LOW"),
            3: (3, "LOW"),
            4: (3, "LOW"),
            5: (5, "LOW"),
            9: (5, "LOW"),
            10: (10, "MEDIUM"),
            19: (10, "MEDIUM"),
            20: (20, "HIGH"),
            200: (20, "HIGH"),
        }
        for available, (k, confidence) in expected.items():
            with self.subTest(available=available):
                self.assertEqual(select_validated_k(available), k)
                self.assertEqual(confidence_for_k(k), confidence)

    def test_personal_ecdf_quantizes_to_service_scale_and_uses_midrank_ties(self) -> None:
        self.assertIsNone(personal_ecdf(3.0, []))
        self.assertAlmostEqual(personal_ecdf(3.0, [1, 2, 4, 5]), 3 / 6)
        self.assertAlmostEqual(personal_ecdf(5.0, [1, 2, 4, 5]), 4.5 / 6)
        self.assertEqual(
            personal_ecdf(3.99, [1, 2, 4, 4, 5]),
            personal_ecdf(4.0, [1, 2, 4, 4, 5]),
        )
        self.assertAlmostEqual(
            personal_ecdf(3.76, [1, 2, 3.5, 4, 5], rating_step=0.5),
            (1 + 3 + 0.5) / 7,
        )

    def test_interpretation_preserves_candidate_and_recent_rating_order(self) -> None:
        result = interpret_recommendations(
            build_core(),
            candidate_movie_ids=[MOVIE_B, MOVIE_A],
            ratings_most_recent_first=[
                InterpretationRating(MOVIE_B, 1.0),
                InterpretationRating(MOVIE_A, 5.0),
            ],
        )
        self.assertEqual(result.used_rating_count, 1)
        self.assertEqual(result.rating_profile.active_rating_count, 2)
        self.assertEqual(result.rating_profile.mean, 3.0)
        self.assertEqual(result.rating_profile.median, 3.0)
        self.assertEqual(result.rating_profile.confidence, "LOW")
        self.assertEqual([item.movie_id for item in result.items], [MOVIE_B, MOVIE_A])
        self.assertTrue(all(0.0 <= item.expected_relative_utility <= 1.0 for item in result.items))
        reversed_recent_order = interpret_recommendations(
            build_core(),
            candidate_movie_ids=[MOVIE_B, MOVIE_A],
            ratings_most_recent_first=[
                InterpretationRating(MOVIE_A, 5.0),
                InterpretationRating(MOVIE_B, 1.0),
            ],
        )
        self.assertNotEqual(
            [item.predicted_rating for item in result.items],
            [item.predicted_rating for item in reversed_recent_order.items],
        )

    def test_k0_returns_calibrated_predictions_without_satisfaction_proxy(self) -> None:
        result = interpret_recommendations(
            build_core(),
            candidate_movie_ids=[MOVIE_A],
            ratings_most_recent_first=[],
        )
        self.assertEqual(result.used_rating_count, 0)
        self.assertEqual(result.rating_profile.confidence, "INSUFFICIENT_DATA")
        self.assertIsNone(result.rating_profile.mean)
        self.assertIsNone(result.rating_profile.median)
        self.assertIsNone(result.items[0].expected_relative_utility)
        self.assertGreaterEqual(result.items[0].predicted_rating, 0.5)
        self.assertLessEqual(result.items[0].predicted_rating, 5.0)


if __name__ == "__main__":
    unittest.main()
