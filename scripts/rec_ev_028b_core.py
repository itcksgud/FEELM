"""Pure inference helpers for the REC-EV-028B post-label analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from scipy import sparse


USER_METRICS = ("HARM20", "TOP2_MEAN_Q", "TOP2_MIN_Q")
ITEM_METRICS = ("ITEM_MACRO_UTILITY_CONTRIBUTION", "ITEM_MACRO_LOW_SLOT_CONTRIBUTION")
ESTIMANDS = ("R0", "R1", "R2", "R3", "R4", "KR", "RECENT", "RANDOM_POOLED")
CONTRASTS = (
    ("FULL_PERSONALIZED", "RANDOM_EXPECTATION"),
    ("BIAS_ONLY", "RANDOM_EXPECTATION"),
    ("PROFILE_SHUFFLE", "RANDOM_EXPECTATION"),
    ("STRUCTURED_DIRECT", "RANDOM_EXPECTATION"),
    ("FULL_PERSONALIZED", "BIAS_ONLY"),
    ("FULL_PERSONALIZED", "PROFILE_SHUFFLE"),
    ("FULL_PERSONALIZED", "STRUCTURED_DIRECT"),
)


def benefit(model: np.ndarray, comparator: np.ndarray, metric: str) -> np.ndarray:
    """Orient every contrast so positive means that the model is better."""
    left = np.asarray(model, dtype=np.float64)
    right = np.asarray(comparator, dtype=np.float64)
    if left.shape != right.shape:
        raise ValueError("aligned model and comparator arrays required")
    if metric in {"HARM20", "ITEM_MACRO_LOW_SLOT_CONTRIBUTION"}:
        return right - left
    return left - right


def max_t_intervals(estimates: np.ndarray, points: np.ndarray, confidence: float) -> dict[str, np.ndarray | float]:
    """Studentized simultaneous intervals using the contract's higher quantile."""
    boot = np.asarray(estimates, dtype=np.float64)
    point = np.asarray(points, dtype=np.float64)
    if boot.ndim != 2 or point.shape != (boot.shape[1],) or not np.isfinite(boot).all() or not np.isfinite(point).all():
        raise ValueError("finite repeats-by-contrasts estimates and aligned points required")
    if boot.shape[0] < 2 or not 0.0 < float(confidence) < 1.0:
        raise ValueError("at least two repeats and a proper confidence level required")
    se = boot.std(axis=0, ddof=1)
    nonzero = se > 0.0
    maxima = np.zeros(boot.shape[0], dtype=np.float64)
    if bool(nonzero.any()):
        maxima = np.max(np.abs((boot[:, nonzero] - point[nonzero]) / se[nonzero]), axis=1)
    critical = float(np.quantile(maxima, float(confidence), method="higher"))
    half = critical * se
    low, high = point - half, point + half
    low[~nonzero], high[~nonzero] = point[~nonzero], point[~nonzero]
    return {"se": se, "low": low, "high": high, "critical": critical}


def user_multiplier_max_t(
    *,
    union_users: Sequence[str],
    matrices: Mapping[str, tuple[Sequence[str], np.ndarray]],
    repeats: int,
    seed: int,
    confidence: float,
    batch_size: int = 100,
) -> dict[str, np.ndarray | float]:
    """Shared-user exponential multiplier bootstrap for ordered estimand matrices."""
    users = list(map(str, union_users))
    if users != sorted(set(users)):
        raise ValueError("union_users must be unique and sorted")
    positions = {key: index for index, key in enumerate(users)}
    ordered = list(matrices)
    if not ordered:
        raise ValueError("at least one estimand matrix required")
    columns = None
    prepared: list[tuple[np.ndarray, np.ndarray]] = []
    points: list[np.ndarray] = []
    for name in ordered:
        keys, raw = matrices[name]
        local = list(map(str, keys))
        values = np.asarray(raw, dtype=np.float64)
        if local != sorted(set(local)) or values.ndim != 2 or values.shape[0] != len(local) or not np.isfinite(values).all():
            raise ValueError(f"invalid user matrix: {name}")
        if columns is None:
            columns = values.shape[1]
        if values.shape[1] != columns:
            raise ValueError("estimand column count drift")
        indices = np.asarray([positions[key] for key in local], dtype=np.int64)
        prepared.append((indices, values))
        points.append(values.mean(axis=0))
    assert columns is not None
    point = np.concatenate(points)
    boot = np.empty((int(repeats), len(ordered) * columns), dtype=np.float64)
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    for start in range(0, int(repeats), int(batch_size)):
        stop = min(int(repeats), start + int(batch_size))
        weights = rng.exponential(1.0, size=(stop - start, len(users)))
        cursor = 0
        for indices, values in prepared:
            local = weights[:, indices]
            estimates = (local @ values) / local.sum(axis=1, keepdims=True)
            boot[start:stop, cursor : cursor + columns] = estimates
            cursor += columns
    intervals = max_t_intervals(boot, point, confidence)
    return {"point": point, "estimates": boot, **intervals}


@dataclass(frozen=True)
class ItemEstimand:
    user_keys: tuple[str, ...]
    movie_ids: tuple[int, ...]
    denominator: sparse.csr_matrix
    contribution_matrices: tuple[sparse.csr_matrix, ...]


def item_multiway_multiplier_max_t(
    *,
    union_users: Sequence[str],
    union_movies: Sequence[int],
    estimands: Mapping[str, ItemEstimand],
    repeats: int,
    seed: int,
    confidence: float,
    linear_transform: np.ndarray | None = None,
    batch_size: int = 50,
) -> dict[str, np.ndarray | float]:
    """Shared user×movie multiplier bootstrap for equal-movie estimands.

    Each local matrix already contains occurrence values divided by the fixed
    occurrence count of its movie.  The denominator matrix contains 1/N_i at
    every observed user/movie occurrence.
    """
    users = list(map(str, union_users))
    movies = list(map(int, union_movies))
    if users != sorted(set(users)) or movies != sorted(set(movies)):
        raise ValueError("union users and movies must be unique and sorted")
    user_position = {key: index for index, key in enumerate(users)}
    movie_position = {movie: index for index, movie in enumerate(movies)}
    ordered = list(estimands)
    if not ordered:
        raise ValueError("at least one item estimand required")
    columns = None
    prepared: list[tuple[np.ndarray, np.ndarray, ItemEstimand]] = []
    points: list[np.ndarray] = []
    for name in ordered:
        value = estimands[name]
        local_users = list(value.user_keys)
        local_movies = list(map(int, value.movie_ids))
        if local_users != sorted(set(local_users)) or local_movies != sorted(set(local_movies)):
            raise ValueError(f"local item keys must be sorted and unique: {name}")
        shape = (len(local_users), len(local_movies))
        if value.denominator.shape != shape or not value.contribution_matrices:
            raise ValueError(f"invalid denominator shape: {name}")
        if columns is None:
            columns = len(value.contribution_matrices)
        if len(value.contribution_matrices) != columns or any(matrix.shape != shape for matrix in value.contribution_matrices):
            raise ValueError(f"item contribution shape drift: {name}")
        if value.denominator.nnz <= 0 or not np.isfinite(value.denominator.data).all():
            raise ValueError(f"invalid denominator values: {name}")
        if any(not np.isfinite(matrix.data).all() for matrix in value.contribution_matrices):
            raise ValueError(f"nonfinite item contribution: {name}")
        user_indices = np.asarray([user_position[key] for key in local_users], dtype=np.int64)
        movie_indices = np.asarray([movie_position[movie] for movie in local_movies], dtype=np.int64)
        prepared.append((user_indices, movie_indices, value))
        denominator = float(value.denominator.sum())
        points.append(np.asarray([float(matrix.sum()) / denominator for matrix in value.contribution_matrices]))
    assert columns is not None
    transform = np.eye(columns, dtype=np.float64) if linear_transform is None else np.asarray(linear_transform, dtype=np.float64)
    if transform.ndim != 2 or transform.shape[0] != columns or not np.isfinite(transform).all():
        raise ValueError("linear transform must map base columns to finite output columns")
    output_columns = transform.shape[1]
    point = np.concatenate([value @ transform for value in points])
    boot = np.empty((int(repeats), len(ordered) * output_columns), dtype=np.float64)
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    combined = len(users) + len(movies)
    for start in range(0, int(repeats), int(batch_size)):
        stop = min(int(repeats), start + int(batch_size))
        # Row-major generation makes every repeat draw all user weights followed
        # by all movie weights, matching the frozen contract.
        weights = rng.exponential(1.0, size=(stop - start, combined))
        all_user_weights, all_movie_weights = weights[:, : len(users)], weights[:, len(users) :]
        cursor = 0
        for user_indices, movie_indices, value in prepared:
            u = all_user_weights[:, user_indices]
            v = all_movie_weights[:, movie_indices]
            denominator_by_movie = value.denominator.T.dot(u.T).T
            denominator = np.sum(denominator_by_movie * v, axis=1)
            if bool((denominator <= 0.0).any()) or not np.isfinite(denominator).all():
                raise RuntimeError("nonpositive item multiplier denominator")
            base = np.empty((stop - start, columns), dtype=np.float64)
            for base_column, matrix in enumerate(value.contribution_matrices):
                numerator_by_movie = matrix.T.dot(u.T).T
                numerator = np.sum(numerator_by_movie * v, axis=1)
                base[:, base_column] = numerator / denominator
            boot[start:stop, cursor : cursor + output_columns] = base @ transform
            cursor += output_columns
    intervals = max_t_intervals(boot, point, confidence)
    return {"point": point, "estimates": boot, **intervals}


def classify_signal(
    *,
    active_rates: Mapping[str, float],
    user_bounds: Mapping[tuple[str, str, str], tuple[float, float]],
    item_bounds: Mapping[tuple[str, str, str], tuple[float, float]],
    gate: float = 0.95,
) -> str:
    """Apply the frozen REC-EV-028 truth-table priority for one estimand."""
    required = ("FULL_PERSONALIZED", "BIAS_ONLY", "PROFILE_SHUFFLE")
    active_ok = all(float(active_rates[name]) >= float(gate) for name in required)

    def user_pass(model: str, comparator: str) -> bool:
        harm = user_bounds[(model, comparator, "HARM20")][0] >= 0.0
        mean = user_bounds[(model, comparator, "TOP2_MEAN_Q")][0] > 0.0
        minimum = user_bounds[(model, comparator, "TOP2_MIN_Q")][0] > 0.0
        return harm and mean and minimum

    def item_pass(model: str, comparator: str) -> bool:
        utility = item_bounds[(model, comparator, "ITEM_MACRO_UTILITY_CONTRIBUTION")][0] > 0.0
        low = item_bounds[(model, comparator, "ITEM_MACRO_LOW_SLOT_CONTRIBUTION")][0] >= 0.0
        return utility and low

    attributed = (
        active_ok
        and user_pass("FULL_PERSONALIZED", "RANDOM_EXPECTATION")
        and user_pass("FULL_PERSONALIZED", "BIAS_ONLY")
        and user_pass("FULL_PERSONALIZED", "PROFILE_SHUFFLE")
        and item_pass("FULL_PERSONALIZED", "BIAS_ONLY")
        and item_pass("FULL_PERSONALIZED", "PROFILE_SHUFFLE")
    )
    if attributed:
        return "ATTRIBUTED_PERSONALIZED_CONTENT_SIGNAL"

    random_systems = ("FULL_PERSONALIZED", "BIAS_ONLY", "PROFILE_SHUFFLE", "STRUCTURED_DIRECT")
    if any(float(active_rates[name]) >= float(gate) and user_pass(name, "RANDOM_EXPECTATION") for name in random_systems):
        return "CONTENT_SIGNAL_WITHOUT_ATTRIBUTED_PERSONALIZATION"

    def random_fail(model: str) -> bool:
        if float(active_rates[model]) < float(gate):
            return True
        harm_high = user_bounds[(model, "RANDOM_EXPECTATION", "HARM20")][1]
        mean_high = user_bounds[(model, "RANDOM_EXPECTATION", "TOP2_MEAN_Q")][1]
        min_high = user_bounds[(model, "RANDOM_EXPECTATION", "TOP2_MIN_Q")][1]
        return harm_high < 0.0 or mean_high <= 0.0 or min_high <= 0.0

    if all(random_fail(name) for name in random_systems):
        return "NO_CONTENT_SIGNAL"
    return "INCONCLUSIVE"
