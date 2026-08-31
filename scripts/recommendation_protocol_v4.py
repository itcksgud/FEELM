from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Iterable, Sequence


USER_SPLIT_SALT = "feelm-rec-vnext-user-split-v1"
ITEM_SPLIT_SALT = "feelm-cold-item-v2"
DENSITY_SPLIT_SALT = "feelm-density-item-v2"
SLATE_SALT = "feelm-top2-v4"
USER_KEY_SALT = "feelm-ml32m-user-v1"


def canonical_integer(value: int) -> str:
    value = int(value)
    if value < 0:
        raise ValueError("canonical integers must be non-negative")
    return str(value)


def uint64_bucket(salt: str, value: int, modulo: int = 100) -> int:
    payload = f"{salt}|{canonical_integer(value)}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % modulo


def user_bucket(user_id: int) -> int:
    return uint64_bucket(USER_SPLIT_SALT, user_id)


def item_bucket(movie_id: int) -> int:
    return uint64_bucket(ITEM_SPLIT_SALT, movie_id)


def density_bucket(movie_id: int) -> int:
    return uint64_bucket(DENSITY_SPLIT_SALT, movie_id)


def user_key(user_id: int) -> str:
    payload = f"{USER_KEY_SALT}|{canonical_integer(user_id)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def slate_digest(seed: int, user_id: int, movie_id: int) -> bytes:
    payload = (
        f"{SLATE_SALT}|{canonical_integer(seed)}|"
        f"{canonical_integer(user_id)}|{canonical_integer(movie_id)}"
    ).encode("utf-8")
    return hashlib.sha256(payload).digest()


def locked_movie_order(seed: int, user_id: int, movie_ids: Sequence[int]) -> list[int]:
    return sorted(
        (int(movie_id) for movie_id in movie_ids),
        key=lambda movie_id: (slate_digest(seed, user_id, movie_id), movie_id),
    )


def midrank_utilities(ratings: Sequence[float]) -> list[float]:
    values = [float(value) for value in ratings]
    count = len(values)
    if count == 0:
        return []
    frequencies: dict[float, int] = {}
    for value in values:
        frequencies[value] = frequencies.get(value, 0) + 1
    less = 0
    lookup: dict[float, float] = {}
    for value in sorted(frequencies):
        equal = frequencies[value]
        lookup[value] = (1.0 + less + 0.5 * equal) / (count + 2.0)
        less += equal
    return [lookup[value] for value in values]


def wilson_lower(successes: int, total: int, z: float = 1.6448536269514722) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    z2 = z * z
    numerator = p + z2 / (2 * total) - z * math.sqrt(
        p * (1 - p) / total + z2 / (4 * total * total)
    )
    return numerator / (1 + z2 / total)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def quantile_nearest_rank(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(1, math.ceil(probability * len(ordered))) - 1
    return ordered[index]


def cold_fold(movie_id: int) -> int:
    return uint64_bucket("feelm-cold-fold-v2", movie_id, modulo=5)


def custom_squared_rank_alpha(units: Sequence[Sequence[int | None]]) -> tuple[float | None, float | None, float | None]:
    valid_units: list[list[int]] = []
    for unit in units:
        values = [int(value) for value in unit if value is not None]
        if len(values) >= 2:
            valid_units.append(values)
    total_ratings = sum(len(values) for values in valid_units)
    if total_ratings < 2:
        return None, None, None
    observed_numerator = 0.0
    category_counts: dict[int, int] = {}
    for values in valid_units:
        for value in values:
            category_counts[value] = category_counts.get(value, 0) + 1
        pair_distance = 0.0
        for left_index in range(len(values)):
            for right_index in range(left_index + 1, len(values)):
                pair_distance += float((values[left_index] - values[right_index]) ** 2)
        observed_numerator += 2.0 * pair_distance / (len(values) - 1)
    observed = observed_numerator / total_ratings
    expected_numerator = 0.0
    for left, left_count in category_counts.items():
        for right, right_count in category_counts.items():
            expected_numerator += left_count * (right_count - int(left == right)) * float((left - right) ** 2)
    expected = expected_numerator / (total_ratings * (total_ratings - 1))
    if expected == 0.0:
        return observed, expected, None
    return observed, expected, 1.0 - observed / expected


def linear_ndcg_at_5(model_relevance: Sequence[float], union_relevance: Sequence[float]) -> tuple[float, float, float | None]:
    if len(model_relevance) != 5 or len(union_relevance) < 5:
        raise ValueError("model relevance must have five values and union must have at least five")
    dcg = sum(float(relevance) / math.log2(rank + 1) for rank, relevance in enumerate(model_relevance, start=1))
    ideal = sorted((float(value) for value in union_relevance), reverse=True)[:5]
    idcg = sum(relevance / math.log2(rank + 1) for rank, relevance in enumerate(ideal, start=1))
    return dcg, idcg, dcg / idcg if idcg else None


def equal_share_request_weight(user_weight: float, item_weights: Sequence[float]) -> float | None:
    if not item_weights:
        return None
    return float(user_weight) * sum(float(value) for value in item_weights) / len(item_weights)
