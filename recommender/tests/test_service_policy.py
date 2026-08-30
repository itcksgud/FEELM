from __future__ import annotations

import unittest

import numpy as np

from feelm_recommender import (
    ArtifactCompatibilityError, ArtifactKind, BiasModel, CandidateNotEnabledError,
    HeadCalibrationBundle, IsotonicCalibrator, ItemFactorModel, ItemIdMapping,
    REC_EV_003B_POLICY, RecommendationCore
)
from helpers import metadata


def build_core(*, enabled: bool = True, family: str = "family") -> RecommendationCore:
    bias = BiasModel.fit(
        [0, 0, 1, 1, 1], [1, 2, 1, 1, 2], [5, 1, 5, 4, 1], iterations=3
    )
    factors = ItemFactorModel(
        np.array([1, 2]), np.array([[1.0, 0.0], [0.0, 1.0]]), reg_param=0.1
    )
    calibrators = {
        k: IsotonicCalibrator(np.array([0.5, 5.0]), np.array([0.5, 5.0]))
        for k in REC_EV_003B_POLICY.star_alpha_by_k
    }
    calibration_bundle = HeadCalibrationBundle(
        REC_EV_003B_POLICY.version, calibrators, "NONE_POPULARITY_RAW", 0.0
    )
    item_mapping = ItemIdMapping(
        "test-mapping-v1", "movielens-int-v1", "feelm-movie-uuid-v1",
        {
            "00000000-0000-0000-0000-000000000001": 1,
            "00000000-0000-0000-0000-000000000002": 2,
        },
        {
            1: "00000000-0000-0000-0000-000000000001",
            2: "00000000-0000-0000-0000-000000000002",
        },
        (),
    )
    return RecommendationCore(
        bias_model=bias,
        item_factors=factors,
        calibration_bundle=calibration_bundle,
        item_mapping=item_mapping,
        bias_metadata=metadata(ArtifactKind.BIAS, compatibility_id=family),
        factor_metadata=metadata(
            ArtifactKind.ALS_ITEM_FACTORS, compatibility_id=family, factor_rank=2
        ),
        calibrator_metadata=metadata(
            ArtifactKind.HEAD_CALIBRATION_BUNDLE,
            compatibility_id=family,
            evidence_id="REC-EV-003B",
        ),
        mapping_metadata=metadata(
            ArtifactKind.ITEM_ID_MAPPING, compatibility_id=family
        ),
        enable_candidate=enabled,
    )


class RecommendationCoreTest(unittest.TestCase):
    def test_candidate_requires_explicit_enable(self) -> None:
        core = build_core(enabled=False)
        self.assertEqual(core.rank([1]).ranking_policy, "BAYESIAN_POPULARITY_ONLY")
        with self.assertRaises(CandidateNotEnabledError):
            core.estimate_stars(
                target_item_ids=[1], onboarding_item_ids=[], onboarding_ratings=[], k=0
            )

    def test_star_head_blends_only_known_target_factors(self) -> None:
        result = build_core().estimate_stars(
            target_item_ids=[1, 99], onboarding_item_ids=[1] * 10,
            onboarding_ratings=[4.0] * 10, k=10
        )
        self.assertEqual(result.star_alpha, 0.3)
        self.assertEqual(result.direct_fold_in.tolist(), [True, False])
        self.assertEqual(result.known_factor_ratings, 10)
        self.assertTrue(np.isfinite(result.stars).all())
        self.assertTrue(result.calibrated)

    def test_star_head_rejects_unvalidated_k_and_wrong_input_count(self) -> None:
        core = build_core()
        with self.assertRaisesRegex(ValueError, "not validated"):
            core.estimate_stars(
                target_item_ids=[1], onboarding_item_ids=[1, 2],
                onboarding_ratings=[4, 3], k=2
            )
        with self.assertRaisesRegex(ValueError, "exactly 5"):
            core.estimate_stars(
                target_item_ids=[1], onboarding_item_ids=[1], onboarding_ratings=[4], k=5
            )

    def test_k0_uses_calibrated_bias_fallback_with_empty_onboarding(self) -> None:
        result = build_core().estimate_stars(
            target_item_ids=[1, 99], onboarding_item_ids=[], onboarding_ratings=[], k=0
        )
        self.assertEqual(result.star_alpha, 0.0)
        self.assertEqual(result.provided_ratings, 0)
        self.assertEqual(result.direct_fold_in.tolist(), [False, False])
        self.assertTrue(np.isfinite(result.stars).all())

    def test_star_head_rejects_rating_outside_scale(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside"):
            build_core().estimate_stars(
                target_item_ids=[1], onboarding_item_ids=[1], onboarding_ratings=[6], k=1
            )

    def test_ranking_head_is_always_popularity_and_stable(self) -> None:
        core = build_core()
        result = core.rank([2, 1, 99])
        reordered = core.rank([99, 1, 2])
        self.assertEqual(result.ranking_policy, "BAYESIAN_POPULARITY_ONLY")
        self.assertEqual(result.fold_in_alpha, 0.0)
        self.assertEqual(sorted(result.ranked_item_ids.tolist()), [1, 2, 99])
        self.assertEqual(result.ranked_item_ids.tolist(), reordered.ranked_item_ids.tolist())

    def test_calibrators_from_wrong_evidence_are_rejected(self) -> None:
        bias = BiasModel.fit([0, 1], [1, 1], [4, 3])
        factors = ItemFactorModel(np.array([1]), np.array([[1.0]]), 0.1)
        calibrators = {
            k: IsotonicCalibrator(np.array([0.5, 5.0]), np.array([0.5, 5.0]))
            for k in REC_EV_003B_POLICY.star_alpha_by_k
        }
        with self.assertRaisesRegex(ArtifactCompatibilityError, "dual-head evidence"):
            RecommendationCore(
                bias_model=bias,
                item_factors=factors,
                calibration_bundle=HeadCalibrationBundle(
                    REC_EV_003B_POLICY.version, calibrators, "NONE_POPULARITY_RAW", 0.0
                ),
                item_mapping=ItemIdMapping(
                    "test-mapping-v1", "movielens-int-v1", "feelm-movie-uuid-v1",
                    {"00000000-0000-0000-0000-000000000001": 1},
                    {1: "00000000-0000-0000-0000-000000000001"}, (),
                ),
                bias_metadata=metadata(ArtifactKind.BIAS),
                factor_metadata=metadata(ArtifactKind.ALS_ITEM_FACTORS, factor_rank=1),
                calibrator_metadata=metadata(ArtifactKind.HEAD_CALIBRATION_BUNDLE),
                mapping_metadata=metadata(ArtifactKind.ITEM_ID_MAPPING),
                enable_candidate=True,
            )

    def test_calibration_checksum_bindings_are_required(self) -> None:
        core = build_core()
        bad_calibration_metadata = metadata(
            ArtifactKind.HEAD_CALIBRATION_BUNDLE,
            compatibility_id="family",
            evidence_id="REC-EV-003B",
            compatibility={
                **(core.calibrator_metadata.compatibility or {}),
                "mapping_payload_sha256": "1" * 64,
            },
        )
        with self.assertRaisesRegex(ArtifactCompatibilityError, "mapping_payload_sha256"):
            RecommendationCore(
                bias_model=core.bias_model,
                item_factors=core.item_factors,
                calibration_bundle=core.calibration_bundle,
                item_mapping=core.item_mapping,
                bias_metadata=core.bias_metadata,
                factor_metadata=core.factor_metadata,
                calibrator_metadata=bad_calibration_metadata,
                mapping_metadata=core.mapping_metadata,
                enable_candidate=True,
            )


if __name__ == "__main__":
    unittest.main()
