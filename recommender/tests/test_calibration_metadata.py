from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from feelm_recommender import (
    ArtifactCompatibilityError, ArtifactKind, ArtifactMetadata, ArtifactValidationError,
    IsotonicCalibrator, load_calibrator_bundle, load_head_calibration_bundle,
    require_same_family
)
from helpers import metadata


class CalibrationAndMetadataTest(unittest.TestCase):
    def test_isotonic_application_clips_and_preserves_missing(self) -> None:
        calibrator = IsotonicCalibrator(
            np.array([1.0, 3.0, 5.0]), np.array([1.5, 3.5, 4.5])
        )
        result = calibrator.apply([-100.0, 2.0, 100.0, np.nan])
        np.testing.assert_allclose(result[:3], [1.5, 2.5, 4.5])
        self.assertTrue(np.isnan(result[3]))

    def test_isotonic_rejects_non_monotonic_thresholds(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            IsotonicCalibrator(np.array([1.0, 1.0]), np.array([1.0, 2.0]))
        with self.assertRaises(ArtifactValidationError):
            IsotonicCalibrator(np.array([1.0, 2.0]), np.array([2.0, 1.0]))

    def test_calibrator_bundle_loads_legacy_k_mapping_with_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "calibrators.json"
            payload.write_text(
                json.dumps({"0": {"x_thresholds": [0.5, 5], "y_thresholds": [0.5, 5]}}),
                encoding="utf-8",
            )
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            loaded = load_calibrator_bundle(
                payload,
                metadata(
                    ArtifactKind.ISOTONIC_BUNDLE,
                    checksum=digest,
                    evidence_id="REC-EV-003B",
                ),
            )
            self.assertEqual(set(loaded), {0})

    def test_metadata_rejects_unknown_schema_and_family_mismatch(self) -> None:
        raw = {
            "schema_version": 99,
            "artifact_kind": ArtifactKind.BIAS.value,
            "model_version": "x",
            "model_status": "VALIDATED_BASELINE",
            "evidence_id": "REC-EV-002",
            "run_id": "EXP-X",
            "compatibility_id": "x",
            "id_space": "movielens-int-v1",
            "payload_sha256": "0" * 64,
            "parameters": {
                "reg_user": 10, "reg_item": 25, "iterations": 10,
                "popularity_prior_count": 50
            },
        }
        with self.assertRaisesRegex(ArtifactCompatibilityError, "schema"):
            ArtifactMetadata.from_dict(raw)
        with self.assertRaisesRegex(ArtifactCompatibilityError, "compatibility_id"):
            require_same_family(
                metadata(ArtifactKind.BIAS, compatibility_id="a"),
                metadata(ArtifactKind.BIAS, compatibility_id="b"),
            )

    def test_head_bundle_explicitly_separates_star_and_raw_popularity_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "head-calibrators.json"
            payload.write_text(
                json.dumps({
                    "schema_version": 2,
                    "policy_version": "cold-start-dual-head-blend-v1",
                    "heads": {
                        "star_blend": {
                            "mode": "ISOTONIC_BY_K",
                            "calibrators": {
                                "0": {"x_thresholds": [0.5, 5.0], "y_thresholds": [0.5, 5.0]}
                            },
                        },
                        "ranking": {"mode": "NONE_POPULARITY_RAW", "alpha": 0.0},
                    },
                }),
                encoding="utf-8",
            )
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            bundle = load_head_calibration_bundle(
                payload,
                metadata(
                    ArtifactKind.HEAD_CALIBRATION_BUNDLE,
                    checksum=digest,
                    evidence_id="REC-EV-003B",
                ),
            )
            self.assertEqual(set(bundle.star_blend), {0})
            self.assertEqual(bundle.ranking_mode, "NONE_POPULARITY_RAW")
            self.assertEqual(bundle.ranking_alpha, 0.0)

    def test_head_bundle_rejects_a_ranking_calibrator_or_nonzero_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "invalid-head-calibrators.json"
            payload.write_text(
                json.dumps({
                    "schema_version": 2,
                    "policy_version": "cold-start-dual-head-blend-v1",
                    "heads": {
                        "star_blend": {
                            "mode": "ISOTONIC_BY_K",
                            "calibrators": {
                                "0": {"x_thresholds": [0.5, 5.0], "y_thresholds": [0.5, 5.0]}
                            },
                        },
                        "ranking": {"mode": "ISOTONIC", "alpha": 0.1},
                    },
                }),
                encoding="utf-8",
            )
            digest = hashlib.sha256(payload.read_bytes()).hexdigest()
            with self.assertRaises(ArtifactCompatibilityError):
                load_head_calibration_bundle(
                    payload,
                    metadata(
                        ArtifactKind.HEAD_CALIBRATION_BUNDLE,
                        checksum=digest,
                        evidence_id="REC-EV-003B",
                    ),
                )


if __name__ == "__main__":
    unittest.main()
