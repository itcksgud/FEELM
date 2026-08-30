from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .errors import ArtifactCompatibilityError, ArtifactValidationError


SUPPORTED_ARTIFACT_SCHEMA_VERSIONS = frozenset({1})


class ArtifactKind(StrEnum):
    BIAS = "regularized-bias-v1"
    ALS_ITEM_FACTORS = "spark-explicit-als-item-factors-v1"
    ISOTONIC_BUNDLE = "isotonic-threshold-bundle-v1"
    HEAD_CALIBRATION_BUNDLE = "head-calibration-bundle-v2"
    ITEM_ID_MAPPING = "movielens-service-item-mapping-v1"


class ModelStatus(StrEnum):
    VALIDATED_BASELINE = "VALIDATED_BASELINE"
    VALIDATED_CANDIDATE_NOT_CHAMPION = "VALIDATED_CANDIDATE_NOT_CHAMPION"
    EXPERIMENTAL_NOT_ADOPTED = "EXPERIMENTAL_NOT_ADOPTED"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    schema_version: int
    artifact_kind: ArtifactKind
    model_version: str
    model_status: ModelStatus
    evidence_id: str
    run_id: str
    compatibility_id: str
    id_space: str
    payload_sha256: str
    parameters: dict[str, Any]
    compatibility: dict[str, Any] | None = None
    rating_min: float = 0.5
    rating_max: float = 5.0
    factor_rank: int | None = None

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArtifactMetadata":
        required = {
            "schema_version",
            "artifact_kind",
            "model_version",
            "model_status",
            "evidence_id",
            "run_id",
            "compatibility_id",
            "id_space",
            "payload_sha256",
            "parameters",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise ArtifactValidationError(f"metadata is missing fields: {', '.join(missing)}")
        if not isinstance(value["parameters"], dict):
            raise ArtifactValidationError("metadata parameters must be an object")
        if value.get("compatibility") is not None and not isinstance(
            value["compatibility"], dict
        ):
            raise ArtifactValidationError("metadata compatibility must be an object")
        try:
            metadata = cls(
                schema_version=int(value["schema_version"]),
                artifact_kind=ArtifactKind(value["artifact_kind"]),
                model_version=str(value["model_version"]),
                model_status=ModelStatus(value["model_status"]),
                evidence_id=str(value["evidence_id"]),
                run_id=str(value["run_id"]),
                compatibility_id=str(value["compatibility_id"]),
                id_space=str(value["id_space"]),
                payload_sha256=str(value["payload_sha256"]),
                parameters=dict(value["parameters"]),
                compatibility=(
                    dict(value["compatibility"])
                    if value.get("compatibility") is not None
                    else None
                ),
                rating_min=float(value.get("rating_min", 0.5)),
                rating_max=float(value.get("rating_max", 5.0)),
                factor_rank=(
                    int(value["factor_rank"]) if value.get("factor_rank") is not None else None
                ),
            )
        except (TypeError, ValueError) as error:
            raise ArtifactValidationError(f"invalid metadata value: {error}") from error
        return metadata

    @classmethod
    def load(cls, path: str | Path) -> "ArtifactMetadata":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ArtifactValidationError(f"cannot read metadata {path}: {error}") from error
        if not isinstance(value, dict):
            raise ArtifactValidationError("metadata root must be an object")
        return cls.from_dict(value)

    def validate(self) -> None:
        if not isinstance(self.artifact_kind, ArtifactKind) or not isinstance(
            self.model_status, ModelStatus
        ):
            raise ArtifactValidationError("artifact_kind and model_status must be known values")
        if self.schema_version not in SUPPORTED_ARTIFACT_SCHEMA_VERSIONS:
            raise ArtifactCompatibilityError(
                f"unsupported artifact schema version: {self.schema_version}"
            )
        for field_name in (
            "model_version",
            "evidence_id",
            "run_id",
            "compatibility_id",
            "id_space",
        ):
            if not getattr(self, field_name).strip():
                raise ArtifactValidationError(f"metadata {field_name} must not be blank")
        if len(self.payload_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.payload_sha256.lower()
        ):
            raise ArtifactValidationError("payload_sha256 must be a SHA-256 hex digest")
        if not self.rating_min < self.rating_max:
            raise ArtifactValidationError("rating scale is invalid")
        if self.factor_rank is not None and self.factor_rank <= 0:
            raise ArtifactValidationError("factor_rank must be positive")
        if not isinstance(self.parameters, dict):
            raise ArtifactValidationError("parameters must be an object")
        if self.compatibility is not None and not isinstance(self.compatibility, dict):
            raise ArtifactValidationError("compatibility must be an object")
        if self.artifact_kind == ArtifactKind.BIAS:
            required_parameters = {
                "reg_user", "reg_item", "iterations", "popularity_prior_count"
            }
            if not required_parameters <= self.parameters.keys():
                raise ArtifactValidationError("Bias metadata is missing training parameters")
        if self.artifact_kind == ArtifactKind.ALS_ITEM_FACTORS:
            if self.factor_rank is None or "reg_param" not in self.parameters:
                raise ArtifactValidationError(
                    "ALS item-factor metadata requires factor_rank and reg_param"
                )
        if self.artifact_kind == ArtifactKind.HEAD_CALIBRATION_BUNDLE:
            required = {
                "policy_version",
                "star_head",
                "ranking_head",
                "ranking_alpha",
                "bias_payload_sha256",
                "factor_payload_sha256",
                "mapping_payload_sha256",
            }
            if self.compatibility is None or not required <= self.compatibility.keys():
                raise ArtifactValidationError(
                    "head calibration metadata is missing compatibility bindings"
                )
            for key in (
                "bias_payload_sha256",
                "factor_payload_sha256",
                "mapping_payload_sha256",
            ):
                self._validate_sha256(str(self.compatibility[key]), f"compatibility.{key}")
        if self.artifact_kind == ArtifactKind.ITEM_ID_MAPPING:
            required = {"mapping_version", "source_id_space", "target_id_space"}
            if self.compatibility is None or not required <= self.compatibility.keys():
                raise ArtifactValidationError(
                    "item mapping metadata is missing ID-space compatibility fields"
                )
            if self.compatibility["source_id_space"] != self.id_space:
                raise ArtifactCompatibilityError(
                    "mapping source_id_space must equal metadata id_space"
                )
            for key in required:
                if not str(self.compatibility[key]).strip():
                    raise ArtifactValidationError(f"compatibility.{key} must not be blank")

    @staticmethod
    def _validate_sha256(value: str, field_name: str) -> None:
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value.lower()
        ):
            raise ArtifactValidationError(f"{field_name} must be a SHA-256 hex digest")

    def verify_payload(self, payload_path: str | Path) -> None:
        actual = sha256_file(Path(payload_path))
        if actual.lower() != self.payload_sha256.lower():
            raise ArtifactValidationError(
                f"payload checksum mismatch: expected {self.payload_sha256}, got {actual}"
            )

    def require_kind(self, expected: ArtifactKind) -> None:
        if self.artifact_kind != expected:
            raise ArtifactCompatibilityError(
                f"expected {expected.value}, got {self.artifact_kind.value}"
            )


def require_same_family(*metadata: ArtifactMetadata) -> None:
    if not metadata:
        raise ArtifactCompatibilityError("at least one artifact is required")
    first = metadata[0]
    for other in metadata[1:]:
        if other.compatibility_id != first.compatibility_id:
            raise ArtifactCompatibilityError("artifact compatibility_id values differ")
        if other.id_space != first.id_space:
            raise ArtifactCompatibilityError("artifact ID spaces differ")
        if (other.rating_min, other.rating_max) != (first.rating_min, first.rating_max):
            raise ArtifactCompatibilityError("artifact rating scales differ")
