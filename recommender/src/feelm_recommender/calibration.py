from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from .errors import ArtifactCompatibilityError, ArtifactValidationError
from .metadata import ArtifactKind, ArtifactMetadata


@dataclass(frozen=True, slots=True)
class IsotonicCalibrator:
    x_thresholds: npt.NDArray[np.float64]
    y_thresholds: npt.NDArray[np.float64]
    rating_min: float = 0.5
    rating_max: float = 5.0

    def __post_init__(self) -> None:
        if self.x_thresholds.ndim != 1 or self.y_thresholds.ndim != 1:
            raise ArtifactValidationError("isotonic thresholds must be one-dimensional")
        if len(self.x_thresholds) < 2 or len(self.x_thresholds) != len(self.y_thresholds):
            raise ArtifactValidationError("isotonic thresholds must be aligned with at least two points")
        if not np.isfinite(self.x_thresholds).all() or not np.isfinite(self.y_thresholds).all():
            raise ArtifactValidationError("isotonic thresholds must be finite")
        if not bool((np.diff(self.x_thresholds) > 0).all()):
            raise ArtifactValidationError("isotonic x thresholds must be strictly increasing")
        if not bool((np.diff(self.y_thresholds) >= 0).all()):
            raise ArtifactValidationError("isotonic y thresholds must be non-decreasing")
        if bool((self.y_thresholds < self.rating_min).any()) or bool(
            (self.y_thresholds > self.rating_max).any()
        ):
            raise ArtifactValidationError("isotonic outputs are outside the rating scale")

    @classmethod
    def from_dict(
        cls, value: dict[str, Any], *, rating_min: float = 0.5, rating_max: float = 5.0
    ) -> "IsotonicCalibrator":
        try:
            return cls(
                np.asarray(value["x_thresholds"], dtype=np.float64),
                np.asarray(value["y_thresholds"], dtype=np.float64),
                rating_min,
                rating_max,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ArtifactValidationError(f"invalid isotonic threshold object: {error}") from error

    @classmethod
    def fit(
        cls, raw: npt.ArrayLike, actual: npt.ArrayLike, *, rating_min: float = 0.5,
        rating_max: float = 5.0
    ) -> "IsotonicCalibrator":
        try:
            from sklearn.isotonic import IsotonicRegression
        except ImportError as error:
            raise RuntimeError("install the 'training' extra to fit isotonic calibration") from error
        raw_array, actual_array = np.asarray(raw, dtype=np.float64), np.asarray(actual, dtype=np.float64)
        finite = np.isfinite(raw_array) & np.isfinite(actual_array)
        if int(finite.sum()) < 2:
            raise ValueError("at least two finite calibration rows are required")
        model = IsotonicRegression(
            y_min=rating_min, y_max=rating_max, increasing=True, out_of_bounds="clip"
        ).fit(raw_array[finite], actual_array[finite])
        return cls(
            np.asarray(model.X_thresholds_, dtype=np.float64),
            np.asarray(model.y_thresholds_, dtype=np.float64),
            rating_min,
            rating_max,
        )

    def apply(self, raw: npt.ArrayLike):
        values = np.asarray(raw, dtype=np.float64)
        result = np.full(values.shape, np.nan, dtype=np.float64)
        finite = np.isfinite(values)
        result[finite] = np.interp(
            values[finite], self.x_thresholds, self.y_thresholds,
            left=self.y_thresholds[0], right=self.y_thresholds[-1]
        )
        return np.clip(result, self.rating_min, self.rating_max)


def load_calibrator_bundle(
    payload_path: str | Path, metadata: ArtifactMetadata
) -> dict[int, IsotonicCalibrator]:
    metadata.require_kind(ArtifactKind.ISOTONIC_BUNDLE)
    metadata.verify_payload(payload_path)
    try:
        root = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactValidationError(f"cannot load calibrator payload: {error}") from error
    if not isinstance(root, dict):
        raise ArtifactValidationError("calibrator bundle must be an object")
    raw_calibrators = root.get("calibrators", root)
    if not isinstance(raw_calibrators, dict):
        raise ArtifactValidationError("calibrators must be an object keyed by K")
    result: dict[int, IsotonicCalibrator] = {}
    for raw_k, value in raw_calibrators.items():
        try:
            k = int(raw_k)
        except (TypeError, ValueError) as error:
            raise ArtifactValidationError(f"calibrator K is not an integer: {raw_k}") from error
        if k < 0 or not isinstance(value, dict):
            raise ArtifactValidationError(f"invalid calibrator for K={raw_k}")
        result[k] = IsotonicCalibrator.from_dict(
            value, rating_min=metadata.rating_min, rating_max=metadata.rating_max
        )
    if not result:
        raise ArtifactValidationError("calibrator bundle is empty")
    return result


@dataclass(frozen=True, slots=True)
class HeadCalibrationBundle:
    """Calibration is explicit per serving head; ranking remains uncalibrated."""

    policy_version: str
    star_blend: dict[int, IsotonicCalibrator]
    ranking_mode: str
    ranking_alpha: float

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ArtifactValidationError("calibration policy_version must not be blank")
        if not self.star_blend:
            raise ArtifactValidationError("star_blend calibrators must not be empty")
        if self.ranking_mode != "NONE_POPULARITY_RAW" or self.ranking_alpha != 0.0:
            raise ArtifactCompatibilityError(
                "validated ranking head requires raw Popularity with alpha 0"
            )


def load_head_calibration_bundle(
    payload_path: str | Path, metadata: ArtifactMetadata
) -> HeadCalibrationBundle:
    metadata.require_kind(ArtifactKind.HEAD_CALIBRATION_BUNDLE)
    metadata.verify_payload(payload_path)
    try:
        root = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactValidationError(f"cannot load head calibration payload: {error}") from error
    if not isinstance(root, dict) or root.get("schema_version") != 2:
        raise ArtifactCompatibilityError("head calibration payload schema_version must be 2")
    heads = root.get("heads")
    if not isinstance(heads, dict) or set(heads) != {"star_blend", "ranking"}:
        raise ArtifactValidationError(
            "calibration payload must explicitly contain star_blend and ranking heads"
        )
    star = heads["star_blend"]
    ranking = heads["ranking"]
    if not isinstance(star, dict) or star.get("mode") != "ISOTONIC_BY_K":
        raise ArtifactCompatibilityError("star_blend head must use ISOTONIC_BY_K")
    if not isinstance(ranking, dict):
        raise ArtifactValidationError("ranking calibration head must be an object")
    raw_calibrators = star.get("calibrators")
    if not isinstance(raw_calibrators, dict):
        raise ArtifactValidationError("star_blend calibrators must be keyed by K")
    calibrators: dict[int, IsotonicCalibrator] = {}
    for raw_k, value in raw_calibrators.items():
        try:
            k = int(raw_k)
        except (TypeError, ValueError) as error:
            raise ArtifactValidationError(f"calibrator K is not an integer: {raw_k}") from error
        if k < 0 or not isinstance(value, dict):
            raise ArtifactValidationError(f"invalid star_blend calibrator for K={raw_k}")
        calibrators[k] = IsotonicCalibrator.from_dict(
            value,
            rating_min=metadata.rating_min,
            rating_max=metadata.rating_max,
        )
    policy_version = str(root.get("policy_version", ""))
    bundle = HeadCalibrationBundle(
        policy_version=policy_version,
        star_blend=calibrators,
        ranking_mode=str(ranking.get("mode", "")),
        ranking_alpha=float(ranking.get("alpha", float("nan"))),
    )
    compatibility = metadata.compatibility or {}
    expected = {
        "policy_version": bundle.policy_version,
        "star_head": "ISOTONIC_BY_K",
        "ranking_head": bundle.ranking_mode,
        "ranking_alpha": bundle.ranking_alpha,
    }
    for key, value in expected.items():
        if compatibility.get(key) != value:
            raise ArtifactCompatibilityError(
                f"calibration metadata {key} does not match payload"
            )
    return bundle
