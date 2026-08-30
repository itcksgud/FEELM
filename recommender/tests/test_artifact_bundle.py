from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from feelm_recommender import OfflineInferencePipeline, RecommendationCore


MOVIE_A = "00000000-0000-0000-0000-000000000001"
MOVIE_B = "00000000-0000-0000-0000-000000000002"


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_metadata(path: Path, **values) -> None:
    path.write_text(json.dumps(values), encoding="utf-8")


class ServingArtifactBundleTest(unittest.TestCase):
    def test_full_bundle_loads_with_checksums_mapping_and_head_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            bias_payload = directory / "bias.npz"
            np.savez_compressed(
                bias_payload,
                global_mean=np.array(3.0),
                user_counts=np.array([2]),
                user_sums=np.array([6.0]),
                movie_counts=np.array([0, 2, 1]),
                movie_sums=np.array([0.0, 8.0, 2.0]),
                user_bias=np.array([0.0]),
                movie_bias=np.array([0.0, 0.5, -0.5]),
            )
            factor_payload = directory / "factors.npz"
            np.savez_compressed(
                factor_payload,
                item_ids=np.array([1, 2]),
                item_factors=np.array([[1.0, 0.0], [0.0, 1.0]]),
            )
            mapping_payload = directory / "mapping.json"
            mapping_payload.write_text(json.dumps({
                "schema_version": 1,
                "mapping_version": "test-mapping-v1",
                "source_id_space": "movielens-int-v1",
                "target_id_space": "feelm-movie-uuid-v1",
                "records": [
                    {"movielens_item_id": 1, "service_movie_id": MOVIE_A},
                    {"movielens_item_id": 2, "service_movie_id": MOVIE_B},
                ],
            }), encoding="utf-8")
            calibration_payload = directory / "calibration.json"
            calibration_payload.write_text(json.dumps({
                "schema_version": 2,
                "policy_version": "cold-start-dual-head-blend-v1",
                "heads": {
                    "star_blend": {
                        "mode": "ISOTONIC_BY_K",
                        "calibrators": {
                            str(k): {
                                "x_thresholds": [0.5, 5.0],
                                "y_thresholds": [0.5, 5.0],
                            }
                            for k in [0, 1, 3, 5, 10, 20]
                        },
                    },
                    "ranking": {"mode": "NONE_POPULARITY_RAW", "alpha": 0.0},
                },
            }), encoding="utf-8")

            family = "test-serving-family-v1"
            common = {
                "schema_version": 1,
                "model_status": "VALIDATED_CANDIDATE_NOT_CHAMPION",
                "evidence_id": "REC-EV-003B",
                "run_id": "EXP-TEST",
                "compatibility_id": family,
                "id_space": "movielens-int-v1",
                "rating_min": 0.5,
                "rating_max": 5.0,
            }
            bias_metadata = directory / "bias.metadata.json"
            write_metadata(
                bias_metadata,
                **common,
                artifact_kind="regularized-bias-v1",
                model_version="bias-test",
                payload_sha256=checksum(bias_payload),
                parameters={
                    "reg_user": 10.0,
                    "reg_item": 25.0,
                    "iterations": 10,
                    "popularity_prior_count": 50.0,
                },
            )
            factor_metadata = directory / "factor.metadata.json"
            write_metadata(
                factor_metadata,
                **common,
                artifact_kind="spark-explicit-als-item-factors-v1",
                model_version="factor-test",
                payload_sha256=checksum(factor_payload),
                factor_rank=2,
                parameters={"reg_param": 0.1, "max_iter": 10, "seed": 42},
            )
            mapping_metadata = directory / "mapping.metadata.json"
            write_metadata(
                mapping_metadata,
                **common,
                artifact_kind="movielens-service-item-mapping-v1",
                model_version="mapping-test",
                payload_sha256=checksum(mapping_payload),
                parameters={"mapping_format": "json-v1"},
                compatibility={
                    "mapping_version": "test-mapping-v1",
                    "source_id_space": "movielens-int-v1",
                    "target_id_space": "feelm-movie-uuid-v1",
                },
            )
            calibration_metadata = directory / "calibration.metadata.json"
            write_metadata(
                calibration_metadata,
                **common,
                artifact_kind="head-calibration-bundle-v2",
                model_version="calibration-test",
                payload_sha256=checksum(calibration_payload),
                parameters={"calibration": "validation-forward"},
                compatibility={
                    "policy_version": "cold-start-dual-head-blend-v1",
                    "star_head": "ISOTONIC_BY_K",
                    "ranking_head": "NONE_POPULARITY_RAW",
                    "ranking_alpha": 0.0,
                    "bias_payload_sha256": checksum(bias_payload),
                    "factor_payload_sha256": checksum(factor_payload),
                    "mapping_payload_sha256": checksum(mapping_payload),
                },
            )

            core = RecommendationCore.from_artifacts(
                bias_payload=bias_payload,
                bias_metadata_path=bias_metadata,
                factor_payload=factor_payload,
                factor_metadata_path=factor_metadata,
                calibrator_payload=calibration_payload,
                calibrator_metadata_path=calibration_metadata,
                mapping_payload=mapping_payload,
                mapping_metadata_path=mapping_metadata,
                enable_candidate=True,
            )
            result = OfflineInferencePipeline(core).run(
                candidate_movie_ids=[MOVIE_B, MOVIE_A]
            )
            self.assertEqual(result.mapping_version, "test-mapping-v1")
            self.assertEqual(result.ranking_alpha, 0.0)
            self.assertEqual(len(result.ranked_movies), 2)


if __name__ == "__main__":
    unittest.main()
