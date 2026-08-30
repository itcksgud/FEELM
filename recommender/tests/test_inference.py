from __future__ import annotations

import json
import unittest

from feelm_recommender import (
    CandidateNotEnabledError,
    OfflineInferencePipeline,
)
from test_service_policy import build_core


MOVIE_A = "00000000-0000-0000-0000-000000000001"
MOVIE_B = "00000000-0000-0000-0000-000000000002"
MOVIE_MISSING = "00000000-0000-0000-0000-000000000099"


class OfflineInferencePipelineTest(unittest.TestCase):
    def test_pipeline_is_deterministic_across_candidate_input_order(self) -> None:
        pipeline = OfflineInferencePipeline(build_core())
        first = pipeline.run(
            candidate_movie_ids=[MOVIE_B, MOVIE_A],
            onboarding=[(MOVIE_A, 4.0)] * 10,
            k=10,
            enable_candidate_stars=True,
        )
        second = pipeline.run(
            candidate_movie_ids=[MOVIE_A, MOVIE_B],
            onboarding=[(MOVIE_A, 4.0)] * 10,
            k=10,
            enable_candidate_stars=True,
        )
        # Duplicate onboarding IDs are quarantined rather than silently counted as K ratings.
        self.assertEqual(first.star_estimates, ())
        self.assertEqual(first, second)
        self.assertEqual(first.ranking_alpha, 0.0)
        self.assertEqual(first.ranking_policy, "BAYESIAN_POPULARITY_ONLY")
        self.assertEqual(
            json.dumps(first.to_dict(), sort_keys=True, separators=(",", ":")),
            json.dumps(second.to_dict(), sort_keys=True, separators=(",", ":")),
        )

    def test_valid_k_star_candidate_is_opt_in_and_uses_service_uuid_boundary(self) -> None:
        pipeline = OfflineInferencePipeline(build_core())
        ranked_only = pipeline.run(candidate_movie_ids=[MOVIE_B, MOVIE_A])
        self.assertFalse(ranked_only.star_candidate_enabled)
        self.assertEqual(ranked_only.star_estimates, ())
        self.assertTrue(all("-" in item.service_movie_id for item in ranked_only.ranked_movies))

        star_result = pipeline.run(
            candidate_movie_ids=[MOVIE_A, MOVIE_B],
            onboarding=[(MOVIE_A, 4.0)],
            k=1,
            enable_candidate_stars=True,
        )
        self.assertEqual(len(star_result.star_estimates), 2)
        self.assertTrue(star_result.star_candidate_enabled)
        self.assertEqual(star_result.request_quarantine, ())

    def test_missing_mapping_is_quarantined_without_becoming_a_model_item_id(self) -> None:
        result = OfflineInferencePipeline(build_core()).run(
            candidate_movie_ids=[MOVIE_A, MOVIE_MISSING]
        )
        self.assertEqual([item.service_movie_id for item in result.ranked_movies], [MOVIE_A])
        self.assertEqual(len(result.request_quarantine), 1)
        self.assertEqual(result.request_quarantine[0].reason, "SERVICE_ID_NOT_MAPPED")

    def test_pipeline_cannot_enable_stars_when_core_candidate_was_not_enabled(self) -> None:
        pipeline = OfflineInferencePipeline(build_core(enabled=False))
        with self.assertRaises(CandidateNotEnabledError):
            pipeline.run(
                candidate_movie_ids=[MOVIE_A],
                onboarding=[(MOVIE_A, 4.0)],
                k=1,
                enable_candidate_stars=True,
            )

    def test_popularity_tie_breaks_by_service_uuid_not_movielens_id(self) -> None:
        core = build_core()
        core.bias_model.item_counts[1:3] = 10
        core.bias_model.item_sums[1:3] = 40.0
        result = OfflineInferencePipeline(core).run(candidate_movie_ids=[MOVIE_B, MOVIE_A])
        self.assertEqual(
            [item.service_movie_id for item in result.ranked_movies],
            [MOVIE_A, MOVIE_B],
        )


if __name__ == "__main__":
    unittest.main()
