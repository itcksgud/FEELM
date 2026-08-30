from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError


PAIR_FIELDS = {
    "prediction_id",
    "predicted_at",
    "rated_at",
    "model_scale_prediction",
    "actual_c1_rating",
    "k",
    "model_version",
    "artifact_set_version",
    "policy_version",
    "split",
}
VALID_K = frozenset({0, 1, 3, 5, 10, 20})
VALID_SPLITS = frozenset({"CALIBRATION", "VALIDATION"})


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _utc_instant(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field_name} must be an ISO 8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ArtifactValidationError(f"{field_name} must be an ISO 8601 UTC string") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ArtifactValidationError(f"{field_name} must use UTC")
    return parsed


@dataclass(frozen=True, slots=True)
class ProductScaleValidationExportResult:
    dataset_version: str
    payload_sha256: str
    calibration_records: int
    validation_records: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS",
            "artifact_kind": "c1-product-star-alignment-pairs-v1",
            "dataset_version": self.dataset_version,
            "payload_sha256": self.payload_sha256,
            "calibration_records": self.calibration_records,
            "validation_records": self.validation_records,
            "contains_user_or_movie_ids": False,
        }


def _parse_pair(value: Any, index: int) -> tuple[dict[str, Any], datetime, datetime]:
    if not isinstance(value, dict) or set(value) != PAIR_FIELDS:
        raise ArtifactValidationError(
            f"pair {index} must contain only the allowlisted product-scale fields"
        )
    try:
        prediction_id = str(uuid.UUID(str(value["prediction_id"])))
    except (ValueError, AttributeError) as error:
        raise ArtifactValidationError(f"pair {index} prediction_id must be a UUID") from error
    predicted_at = _utc_instant(value["predicted_at"], f"pair {index} predicted_at")
    rated_at = _utc_instant(value["rated_at"], f"pair {index} rated_at")
    if rated_at <= predicted_at:
        raise ArtifactValidationError(
            f"pair {index} rating must occur after its prediction snapshot"
        )
    prediction = value["model_scale_prediction"]
    if isinstance(prediction, bool) or not isinstance(prediction, (int, float)):
        raise ArtifactValidationError(f"pair {index} prediction must be numeric")
    prediction = float(prediction)
    if not math.isfinite(prediction) or not 0.5 <= prediction <= 5.0:
        raise ArtifactValidationError(
            f"pair {index} prediction must be finite within model scale 0.5..5"
        )
    actual = value["actual_c1_rating"]
    if isinstance(actual, bool) or not isinstance(actual, int) or actual not in {1, 2, 3, 4, 5}:
        raise ArtifactValidationError(f"pair {index} actual C1 rating must be integer 1..5")
    k = value["k"]
    if isinstance(k, bool) or not isinstance(k, int) or k not in VALID_K:
        raise ArtifactValidationError(f"pair {index} has an unvalidated K")
    split = value["split"]
    if split not in VALID_SPLITS:
        raise ArtifactValidationError(f"pair {index} split must be CALIBRATION or VALIDATION")
    strings: dict[str, str] = {}
    for key in ("model_version", "artifact_set_version", "policy_version"):
        item = value[key]
        if not isinstance(item, str) or not item.strip() or len(item) > 128:
            raise ArtifactValidationError(f"pair {index} {key} must be a nonblank bounded string")
        strings[key] = item
    return (
        {
            "prediction_id": prediction_id,
            "predicted_at": predicted_at.isoformat().replace("+00:00", "Z"),
            "rated_at": rated_at.isoformat().replace("+00:00", "Z"),
            "model_scale_prediction": prediction,
            "actual_c1_rating": actual,
            "k": k,
            "model_version": strings["model_version"],
            "artifact_set_version": strings["artifact_set_version"],
            "policy_version": strings["policy_version"],
            "split": split,
        },
        predicted_at,
        rated_at,
    )


def export_product_scale_validation_pairs(
    *,
    source_path: str | Path,
    payload_path: str | Path,
    metadata_path: str | Path,
    dataset_version: str,
) -> ProductScaleValidationExportResult:
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise ArtifactValidationError("dataset_version must not be blank")
    try:
        source_bytes = Path(source_path).read_bytes()
        source = json.loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactValidationError(f"cannot read product-scale source: {error}") from error
    if not isinstance(source, list) or not source:
        raise ArtifactValidationError("product-scale source must be a non-empty array")
    parsed = [_parse_pair(value, index + 1) for index, value in enumerate(source)]
    records = [item[0] for item in parsed]
    prediction_ids = [item["prediction_id"] for item in records]
    if len(set(prediction_ids)) != len(prediction_ids):
        raise ArtifactValidationError("prediction_id values must be unique across splits")
    calibration = [item for item in parsed if item[0]["split"] == "CALIBRATION"]
    validation = [item for item in parsed if item[0]["split"] == "VALIDATION"]
    if not calibration or not validation:
        raise ArtifactValidationError("both CALIBRATION and VALIDATION records are required")
    if max(item[2] for item in calibration) >= min(item[1] for item in validation):
        raise ArtifactValidationError(
            "CALIBRATION ratings must precede every VALIDATION prediction"
        )
    records.sort(
        key=lambda item: (
            0 if item["split"] == "CALIBRATION" else 1,
            item["predicted_at"],
            item["prediction_id"],
        )
    )
    payload = {
        "schema_version": 1,
        "artifact_kind": "c1-product-star-alignment-pairs-v1",
        "dataset_version": dataset_version,
        "source_prediction_scale": {"minimum": 0.5, "maximum": 5.0},
        "target_product_scale": {
            "minimum": 1,
            "maximum": 5,
            "allowed_values": [1, 2, 3, 4, 5],
        },
        "split_policy": "CALIBRATION_RATINGS_PRECEDE_VALIDATION_PREDICTIONS_V1",
        "contains_user_or_movie_ids": False,
        "records": records,
    }
    payload_bytes = _canonical_json(payload)
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    metadata = {
        "schema_version": 1,
        "artifact_kind": "c1-product-star-alignment-pairs-v1",
        "dataset_version": dataset_version,
        "payload_sha256": payload_sha256,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "calibration_records": len(calibration),
        "validation_records": len(validation),
        "contains_user_or_movie_ids": False,
        "leakage_check": "PASS",
    }
    for path, content in (
        (Path(payload_path), payload_bytes),
        (Path(metadata_path), _canonical_json(metadata)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
    return ProductScaleValidationExportResult(
        dataset_version=dataset_version,
        payload_sha256=payload_sha256,
        calibration_records=len(calibration),
        validation_records=len(validation),
    )
