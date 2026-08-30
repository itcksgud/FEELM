from __future__ import annotations

from feelm_recommender import ArtifactKind, ArtifactMetadata, ModelStatus


def metadata(
    kind: ArtifactKind,
    *,
    compatibility_id: str = "rec-ev-003-serving-family-v1",
    evidence_id: str = "REC-EV-003",
    factor_rank: int | None = None,
    checksum: str = "0" * 64,
    compatibility: dict | None = None,
) -> ArtifactMetadata:
    if kind == ArtifactKind.BIAS:
        parameters = {
            "reg_user": 10.0, "reg_item": 25.0, "iterations": 10,
            "popularity_prior_count": 50.0
        }
    elif kind == ArtifactKind.ALS_ITEM_FACTORS:
        parameters = {"reg_param": 0.1, "max_iter": 10, "seed": 42}
    elif kind in {ArtifactKind.ISOTONIC_BUNDLE, ArtifactKind.HEAD_CALIBRATION_BUNDLE}:
        parameters = {"calibration": "validation-forward"}
    else:
        parameters = {"mapping_format": "json-v1"}
    if kind == ArtifactKind.HEAD_CALIBRATION_BUNDLE and compatibility is None:
        compatibility = {
            "policy_version": "cold-start-dual-head-blend-v1",
            "star_head": "ISOTONIC_BY_K",
            "ranking_head": "NONE_POPULARITY_RAW",
            "ranking_alpha": 0.0,
            "bias_payload_sha256": "0" * 64,
            "factor_payload_sha256": "0" * 64,
            "mapping_payload_sha256": "0" * 64,
        }
    if kind == ArtifactKind.ITEM_ID_MAPPING and compatibility is None:
        compatibility = {
            "mapping_version": "test-mapping-v1",
            "source_id_space": "movielens-int-v1",
            "target_id_space": "feelm-movie-uuid-v1",
        }
    return ArtifactMetadata(
        schema_version=1,
        artifact_kind=kind,
        model_version=f"test-{kind.value}",
        model_status=ModelStatus.VALIDATED_CANDIDATE_NOT_CHAMPION,
        evidence_id=evidence_id,
        run_id="EXP-TEST",
        compatibility_id=compatibility_id,
        id_space="movielens-int-v1",
        payload_sha256=checksum,
        parameters=parameters,
        compatibility=compatibility,
        factor_rank=factor_rank,
    )
