from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from .errors import ArtifactValidationError
from .metadata import ArtifactKind, ArtifactMetadata


def _integer_ids(values: npt.ArrayLike, name: str) -> npt.NDArray[np.int64]:
    array = np.asarray(values)
    if array.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional integer array")
    if array.size == 0:
        return array.astype(np.int64)
    if not np.issubdtype(array.dtype, np.integer):
        raise ValueError(f"{name} must be a one-dimensional integer array")
    result = array.astype(np.int64, copy=False)
    if bool((result < 0).any()):
        raise ValueError(f"{name} must be non-negative")
    return result


def _ratings(values: npt.ArrayLike) -> npt.NDArray[np.float64]:
    result = np.asarray(values, dtype=np.float64)
    if result.ndim != 1 or not np.isfinite(result).all():
        raise ValueError("ratings must be a one-dimensional finite array")
    return result


def safe_take(values: npt.NDArray, ids: npt.NDArray[np.int64], default: float = 0.0):
    result = np.full(ids.shape, default, dtype=np.float64)
    valid = ids < len(values)
    result[valid] = values[ids[valid]]
    return result


@dataclass(frozen=True, slots=True)
class BiasModel:
    global_mean: float
    user_counts: npt.NDArray[np.int64]
    user_sums: npt.NDArray[np.float64]
    item_counts: npt.NDArray[np.int64]
    item_sums: npt.NDArray[np.float64]
    user_bias: npt.NDArray[np.float64]
    item_bias: npt.NDArray[np.float64]
    rating_min: float = 0.5
    rating_max: float = 5.0

    def __post_init__(self) -> None:
        if not self.rating_min < self.rating_max or not np.isfinite(self.global_mean):
            raise ArtifactValidationError("invalid Bias rating scale or global mean")
        pairs = (
            (self.user_counts, self.user_sums, self.user_bias, "user"),
            (self.item_counts, self.item_sums, self.item_bias, "item"),
        )
        for counts, sums, bias, label in pairs:
            if not (counts.ndim == sums.ndim == bias.ndim == 1):
                raise ArtifactValidationError(f"{label} Bias arrays must be one-dimensional")
            if not (len(counts) == len(sums) == len(bias)):
                raise ArtifactValidationError(f"{label} Bias array lengths differ")
            if bool((counts < 0).any()) or not np.isfinite(sums).all() or not np.isfinite(bias).all():
                raise ArtifactValidationError(f"{label} Bias arrays contain invalid values")

    @classmethod
    def fit(
        cls,
        user_ids: npt.ArrayLike,
        item_ids: npt.ArrayLike,
        ratings: npt.ArrayLike,
        *,
        reg_user: float = 10.0,
        reg_item: float = 25.0,
        iterations: int = 10,
        rating_min: float = 0.5,
        rating_max: float = 5.0,
    ) -> "BiasModel":
        users = _integer_ids(user_ids, "user_ids")
        items = _integer_ids(item_ids, "item_ids")
        stars = _ratings(ratings)
        if len(stars) == 0 or not (len(users) == len(items) == len(stars)):
            raise ValueError("non-empty aligned user, item and rating arrays are required")
        if reg_user < 0 or reg_item < 0 or iterations < 1:
            raise ValueError("regularization must be non-negative and iterations positive")
        if bool((stars < rating_min).any()) or bool((stars > rating_max).any()):
            raise ValueError("ratings are outside the configured scale")

        user_size, item_size = int(users.max()) + 1, int(items.max()) + 1
        global_mean = float(stars.mean(dtype=np.float64))
        user_counts = np.bincount(users, minlength=user_size).astype(np.int64, copy=False)
        user_sums = np.bincount(users, weights=stars, minlength=user_size)
        item_counts = np.bincount(items, minlength=item_size).astype(np.int64, copy=False)
        item_sums = np.bincount(items, weights=stars, minlength=item_size)
        user_bias = np.zeros(user_size, dtype=np.float64)
        item_bias = np.zeros(item_size, dtype=np.float64)
        for _ in range(iterations):
            residual = np.bincount(
                items, weights=stars - global_mean - user_bias[users], minlength=item_size
            )
            item_bias = np.divide(
                residual,
                item_counts + reg_item,
                out=np.zeros(item_size, dtype=np.float64),
                where=(item_counts + reg_item) > 0,
            )
            residual = np.bincount(
                users, weights=stars - global_mean - item_bias[items], minlength=user_size
            )
            user_bias = np.divide(
                residual,
                user_counts + reg_user,
                out=np.zeros(user_size, dtype=np.float64),
                where=(user_counts + reg_user) > 0,
            )
        return cls(
            global_mean, user_counts, user_sums, item_counts, item_sums,
            user_bias, item_bias, rating_min, rating_max
        )

    @classmethod
    def load_npz(
        cls, payload_path: str | Path, metadata: ArtifactMetadata
    ) -> "BiasModel":
        metadata.require_kind(ArtifactKind.BIAS)
        metadata.verify_payload(payload_path)
        try:
            with np.load(payload_path, allow_pickle=False) as payload:
                required = {
                    "global_mean", "user_counts", "user_sums", "movie_counts",
                    "movie_sums", "user_bias", "movie_bias"
                }
                missing = sorted(required - set(payload.files))
                if missing:
                    raise ArtifactValidationError(
                        f"Bias payload is missing arrays: {', '.join(missing)}"
                    )
                return cls(
                    global_mean=float(payload["global_mean"]),
                    user_counts=np.asarray(payload["user_counts"], dtype=np.int64),
                    user_sums=np.asarray(payload["user_sums"], dtype=np.float64),
                    item_counts=np.asarray(payload["movie_counts"], dtype=np.int64),
                    item_sums=np.asarray(payload["movie_sums"], dtype=np.float64),
                    user_bias=np.asarray(payload["user_bias"], dtype=np.float64),
                    item_bias=np.asarray(payload["movie_bias"], dtype=np.float64),
                    rating_min=metadata.rating_min,
                    rating_max=metadata.rating_max,
                )
        except (OSError, ValueError) as error:
            if isinstance(error, ArtifactValidationError):
                raise
            raise ArtifactValidationError(f"cannot load Bias payload: {error}") from error

    def predict(self, user_ids: npt.ArrayLike, item_ids: npt.ArrayLike):
        users = _integer_ids(user_ids, "user_ids")
        items = _integer_ids(item_ids, "item_ids")
        if len(users) != len(items):
            raise ValueError("user_ids and item_ids must be aligned")
        return np.clip(
            self.global_mean + safe_take(self.user_bias, users) + safe_take(self.item_bias, items),
            self.rating_min,
            self.rating_max,
        )

    def onboarding_user_bias(
        self, item_ids: npt.ArrayLike, ratings: npt.ArrayLike, reg_user: float = 10.0
    ) -> float:
        items, stars = _integer_ids(item_ids, "item_ids"), _ratings(ratings)
        if len(items) != len(stars) or len(stars) == 0:
            raise ValueError("non-empty aligned onboarding items and ratings are required")
        if bool((stars < self.rating_min).any()) or bool((stars > self.rating_max).any()):
            raise ValueError("onboarding ratings are outside the configured scale")
        if reg_user < 0:
            raise ValueError("reg_user must be non-negative")
        residual = stars - self.global_mean - safe_take(self.item_bias, items)
        return float(residual.sum() / (len(residual) + reg_user))

    def predict_for_onboarding_user(
        self, target_item_ids: npt.ArrayLike, onboarding_item_ids: npt.ArrayLike,
        onboarding_ratings: npt.ArrayLike, reg_user: float = 10.0
    ):
        targets = _integer_ids(target_item_ids, "target_item_ids")
        user_bias = self.onboarding_user_bias(
            onboarding_item_ids, onboarding_ratings, reg_user=reg_user
        )
        return np.clip(
            self.global_mean + user_bias + safe_take(self.item_bias, targets),
            self.rating_min,
            self.rating_max,
        )

    def popularity(self, item_ids: npt.ArrayLike, prior_count: float = 50.0):
        items = _integer_ids(item_ids, "item_ids")
        if prior_count < 0:
            raise ValueError("prior_count must be non-negative")
        counts = safe_take(self.item_counts, items)
        sums = safe_take(self.item_sums, items)
        denominator = counts + prior_count
        return np.clip(
            np.divide(
                sums + prior_count * self.global_mean,
                denominator,
                out=np.full(items.shape, self.global_mean, dtype=np.float64),
                where=denominator > 0,
            ),
            self.rating_min,
            self.rating_max,
        )
