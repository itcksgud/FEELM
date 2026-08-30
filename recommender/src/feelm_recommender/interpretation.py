from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Sequence

import numpy as np

from .service import RecommendationCore


SUPPORTED_K = (0, 1, 3, 5, 10, 20)
K_SELECTION_POLICY_VERSION = "C6_MOST_RECENT_VALIDATED_K_FLOOR_V1"
UTILITY_POLICY_VERSION = "C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2"
EXPERIMENT_VERSION = "c6-recommendation-interpretation-v2"
SERVICE_RATING_STEP = 1.0
LIMITATIONS = (
    "LOCAL_EXPERIMENT_ONLY",
    "NOT_SELF_REPORTED_SATISFACTION",
    "NOT_PRODUCT_DISPLAY_APPROVED",
    "K_BUCKETED_MOST_RECENT",
)


class InterpretationInputError(ValueError):
    """An opaque request-boundary error for IDs unavailable to this artifact set."""


@dataclass(frozen=True, slots=True)
class InterpretationRating:
    movie_id: str
    value: float


@dataclass(frozen=True, slots=True)
class InterpretedItem:
    movie_id: str
    predicted_rating: float
    expected_relative_utility: float | None
    direct_fold_in: bool
    confidence: str


@dataclass(frozen=True, slots=True)
class RatingProfile:
    active_rating_count: int
    mean: float | None
    median: float | None
    confidence: str


@dataclass(frozen=True, slots=True)
class InterpretationResult:
    used_rating_count: int
    rating_profile: RatingProfile
    items: tuple[InterpretedItem, ...]


def select_validated_k(available_rating_count: int) -> int:
    """Floor the caller's most-recent-first input to one validated K bucket."""
    if available_rating_count < 0:
        raise ValueError("available_rating_count must not be negative")
    capped = min(available_rating_count, SUPPORTED_K[-1])
    return max(k for k in SUPPORTED_K if k <= capped)


def confidence_for_k(k: int) -> str:
    if k == 0:
        return "INSUFFICIENT_DATA"
    if k in (1, 3, 5):
        return "LOW"
    if k == 10:
        return "MEDIUM"
    if k == 20:
        return "HIGH"
    raise ValueError(f"unsupported confidence K: {k}")


def personal_ecdf(
    predicted_rating: float,
    rating_values: Sequence[float],
    *,
    rating_step: float = SERVICE_RATING_STEP,
) -> float | None:
    """Return the v2 quantized-midrank ECDF; this is not satisfaction truth."""
    if not rating_values:
        return None
    if not math.isfinite(predicted_rating) or not math.isfinite(rating_step) or rating_step <= 0:
        raise ValueError("predicted rating and rating step must be finite")
    quantized = math.floor(predicted_rating / rating_step + 0.5) * rating_step
    less = sum(value < quantized for value in rating_values)
    equal = sum(value == quantized for value in rating_values)
    value = (1.0 + less + 0.5 * equal) / (len(rating_values) + 2.0)
    return min(1.0, max(0.0, value))


def interpret_recommendations(
    core: RecommendationCore,
    *,
    candidate_movie_ids: Sequence[str],
    ratings_most_recent_first: Sequence[InterpretationRating],
) -> InterpretationResult:
    """Compute the local-only C6 interpretation without changing ranking or state."""
    candidate_rows, candidate_missing = core.item_mapping.resolve_service_ids(
        candidate_movie_ids
    )
    if candidate_missing or len(candidate_rows) != len(candidate_movie_ids):
        raise InterpretationInputError("candidate movie is not mapped")
    candidate_item_ids = [item_id for _, item_id in candidate_rows]
    if any(
        item_id >= len(core.bias_model.item_counts)
        or core.bias_model.item_counts[item_id] <= 0
        for item_id in candidate_item_ids
    ):
        raise InterpretationInputError("candidate movie is not available to the model")

    rating_rows: list[tuple[str, int, float]] = []
    for rating in ratings_most_recent_first:
        resolved, missing = core.item_mapping.resolve_service_ids([rating.movie_id])
        if missing or not resolved:
            raise InterpretationInputError("preference movie is not mapped")
        service_id, item_id = resolved[0]
        rating_rows.append((service_id, item_id, float(rating.value)))

    available_count = len(rating_rows)
    k = select_validated_k(available_count)
    used_rows = rating_rows[:k]
    if any(
        item_id >= len(core.bias_model.item_counts)
        for _, item_id, _ in used_rows
    ):
        raise InterpretationInputError("preference movie is not available to the model")

    estimate = core.estimate_stars(
        target_item_ids=np.asarray(candidate_item_ids, dtype=np.int64),
        onboarding_item_ids=np.asarray(
            [item_id for _, item_id, _ in used_rows], dtype=np.int64
        ),
        onboarding_ratings=np.asarray(
            [value for _, _, value in used_rows], dtype=np.float64
        ),
        k=k,
    )
    all_values = [value for _, _, value in rating_rows]
    confidence = confidence_for_k(k)
    items = tuple(
        InterpretedItem(
            movie_id=service_id,
            predicted_rating=float(predicted),
            expected_relative_utility=personal_ecdf(float(predicted), all_values),
            direct_fold_in=bool(direct),
            confidence=confidence,
        )
        for (service_id, _), predicted, direct in zip(
            candidate_rows, estimate.stars, estimate.direct_fold_in, strict=True
        )
    )
    return InterpretationResult(
        used_rating_count=k,
        rating_profile=RatingProfile(
            active_rating_count=available_count,
            mean=float(np.mean(all_values)) if all_values else None,
            median=float(median(all_values)) if all_values else None,
            confidence=confidence,
        ),
        items=items,
    )
