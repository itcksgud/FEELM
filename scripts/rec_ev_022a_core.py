"""Deterministic primitives for the REC-EV-022A Stage-1 experiment."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import sparse


RATING_VALUES = np.arange(0.5, 5.01, 0.5, dtype=np.float64)
OLD_SPLIT_PREFIX = "feelm-rec-vnext-user-split-v1|"
USER_KEY_PREFIX = "rec-ev-022a-user-key-v1|"
USER_ROLE_PREFIX = "rec-ev-022a-user-role-v1|"


def canonical_decimal(value: int) -> str:
    integer = int(value)
    if integer < 0:
        raise ValueError("identifier must be non-negative")
    return str(integer)


def old_user_bucket(user_id: int) -> int:
    payload = f"{OLD_SPLIT_PREFIX}{canonical_decimal(user_id)}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False) % 100


def user_key(user_id: int) -> str:
    payload = f"{USER_KEY_PREFIX}{canonical_decimal(user_id)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def user_role_bucket(user_id: int) -> int:
    payload = f"{USER_ROLE_PREFIX}{canonical_decimal(user_id)}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big", signed=False) % 10_000


def user_role(user_id: int) -> str:
    bucket = user_role_bucket(user_id)
    if bucket < 6_000:
        return "TRAIN_USERS"
    if bucket < 8_000:
        return "STAGE1_SELECTION"
    if bucket < 9_200:
        return "STAGE2_DEVELOPMENT"
    return "FINAL_RESERVE"


def order_key(order_salt: str, anonymous_user_key: str, movie_id: int) -> tuple[bytes, int]:
    if len(anonymous_user_key) != 64 or anonymous_user_key.lower() != anonymous_user_key:
        raise ValueError("user key must be a lowercase SHA-256 hex digest")
    payload = f"{order_salt}|{anonymous_user_key}|{canonical_decimal(movie_id)}".encode("utf-8")
    return hashlib.sha256(payload).digest(), int(movie_id)


def rating_indices(ratings: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(ratings) if not isinstance(ratings, np.ndarray) else ratings, dtype=np.float64)
    indices = np.rint((values - 0.5) * 2.0).astype(np.int8)
    if bool(((indices < 0) | (indices >= len(RATING_VALUES))).any()):
        raise ValueError("rating outside MovieLens half-star grid")
    if not np.allclose(RATING_VALUES[indices], values):
        raise ValueError("rating outside MovieLens half-star grid")
    return indices


def user_equal_prior(user_rating_histograms: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    hist = np.asarray(user_rating_histograms, dtype=np.float64)
    if hist.ndim != 2 or hist.shape[1] != len(RATING_VALUES):
        raise ValueError("expected users by ten rating bins")
    totals = hist.sum(axis=1)
    valid = totals > 0
    if not bool(valid.any()):
        raise ValueError("training rating prior cannot be empty")
    normalized = hist[valid] / totals[valid, None]
    pi0 = normalized.mean(axis=0)
    pi0 /= pi0.sum()
    g0_mid = np.cumsum(pi0) - 0.5 * pi0
    return pi0, g0_mid


def full_history_mid_percentiles(ratings: Sequence[float]) -> np.ndarray:
    indices = rating_indices(np.asarray(ratings, dtype=np.float64))
    counts = np.bincount(indices, minlength=len(RATING_VALUES)).astype(np.float64)
    below = np.cumsum(counts) - counts
    return (below[indices] + 0.5 * counts[indices]) / counts.sum()


def smoothed_profile_percentiles(
    ratings: Sequence[float], g0_mid: np.ndarray, *, tau: float = 5.0
) -> np.ndarray:
    indices = rating_indices(np.asarray(ratings, dtype=np.float64))
    prior = np.asarray(g0_mid, dtype=np.float64)
    if prior.shape != (len(RATING_VALUES),) or tau <= 0:
        raise ValueError("invalid prior or smoothing")
    counts = np.bincount(indices, minlength=len(RATING_VALUES)).astype(np.float64)
    below = np.cumsum(counts) - counts
    return (below[indices] + 0.5 * counts[indices] + float(tau) * prior[indices]) / (len(indices) + float(tau))


def encoding_weights(
    encoding: str,
    ratings: Sequence[float],
    g0_mid: np.ndarray,
    *,
    tau: float = 5.0,
) -> np.ndarray:
    values = np.asarray(ratings, dtype=np.float64)
    if not len(values):
        return np.empty(0, dtype=np.float64)
    if encoding in {"BINARY_SIGN", "PERCENTILE_MAGNITUDE"}:
        centered = 2.0 * smoothed_profile_percentiles(values, g0_mid, tau=tau) - 1.0
        if encoding == "BINARY_SIGN":
            return np.sign(centered)
        return centered
    if encoding == "ORDINAL_RANK":
        q = full_history_mid_percentiles(values)
        return 2.0 * q - 1.0
    raise ValueError(f"unknown encoding: {encoding}")


def _list_values(value: Any) -> list[int]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    return [int(item) for item in value]


def _group_matrix(token_rows: list[list[str]]) -> sparse.csr_matrix:
    vocabulary = {token: index for index, token in enumerate(sorted({token for row in token_rows for token in row}))}
    rows: list[int] = []
    columns: list[int] = []
    for row_index, tokens in enumerate(token_rows):
        for token in sorted(set(tokens)):
            rows.append(row_index)
            columns.append(vocabulary[token])
    matrix = sparse.coo_matrix(
        (np.ones(len(rows), dtype=np.float32), (rows, columns)),
        shape=(len(token_rows), len(vocabulary)),
    ).tocsr()
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    inverse = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
    return sparse.diags(inverse.astype(np.float32)) @ matrix


def build_structured_full(structured: pd.DataFrame, item_ids: Sequence[int]) -> sparse.csr_matrix:
    ids = np.asarray(item_ids, dtype=np.int64)
    indexed = structured.set_index("movie_id", verify_integrity=True).reindex(ids)
    available = indexed["feature_eligible"].astype("boolean").fillna(False).to_numpy(dtype=bool)
    genre_rows: list[list[str]] = []
    context_rows: list[list[str]] = []
    people_rows: list[list[str]] = []
    keyword_rows: list[list[str]] = []
    for row, ok in zip(indexed.itertuples(index=False), available, strict=True):
        if not ok:
            genre_rows.append([])
            context_rows.append([])
            people_rows.append([])
            keyword_rows.append([])
            continue
        decade = int(row.release_year) // 10 * 10 if pd.notna(row.release_year) else None
        runtime_bucket = int(row.runtime_minutes) // 30 if pd.notna(row.runtime_minutes) else None
        genre_rows.append([f"genre:{value}" for value in _list_values(row.genre_ids)])
        context_rows.append(
            ([f"language:{row.original_language}"] if pd.notna(row.original_language) else [])
            + ([f"decade:{decade}"] if decade is not None else [])
            + ([f"runtime30:{runtime_bucket}"] if runtime_bucket is not None else [])
        )
        people_rows.append(
            [f"director:{value}" for value in _list_values(row.director_ids)]
            + [f"cast:{value}" for value in _list_values(row.top5_cast_ids)]
        )
        keyword_rows.append([f"keyword:{value}" for value in _list_values(row.keyword_ids)])
    groups = [_group_matrix(rows) for rows in (genre_rows, context_rows, people_rows, keyword_rows)]
    matrix = sparse.hstack([group * 0.25 for group in groups], format="csr", dtype=np.float32)
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    inverse = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
    return (sparse.diags(inverse.astype(np.float32)) @ matrix).tocsr()


def structured_pair_similarity(
    item_features: sparse.csr_matrix,
    profile_positions: Sequence[int],
    target_positions: Sequence[int],
) -> np.ndarray:
    profile = np.asarray(profile_positions, dtype=np.int64)
    targets = np.asarray(target_positions, dtype=np.int64)
    similarities = (item_features[targets] @ item_features[profile].T).toarray().astype(np.float64)
    np.maximum(similarities, 0.0, out=similarities)
    return similarities


def itemknn_pair_similarity(
    train_z: sparse.csc_matrix,
    train_observed: sparse.csc_matrix,
    column_norms: np.ndarray,
    profile_positions: Sequence[int],
    target_positions: Sequence[int],
    *,
    shrinkage: float = 50.0,
    minimum_support: int = 2,
) -> np.ndarray:
    if shrinkage < 0 or minimum_support < 1:
        raise ValueError("invalid ItemKNN parameters")
    profile = np.asarray(profile_positions, dtype=np.int64)
    targets = np.asarray(target_positions, dtype=np.int64)
    dots = (train_z[:, targets].T @ train_z[:, profile]).toarray().astype(np.float64)
    support = (train_observed[:, targets].T @ train_observed[:, profile]).toarray().astype(np.float64)
    norms = np.asarray(column_norms, dtype=np.float64)
    denominators = norms[targets, None] * norms[profile][None, :]
    cosine = np.divide(dots, denominators, out=np.zeros_like(dots), where=denominators > 0)
    similarity = cosine * np.divide(support, support + float(shrinkage), out=np.zeros_like(support), where=support > 0)
    similarity[(support < int(minimum_support)) | (cosine <= 0)] = 0.0
    np.maximum(similarity, 0.0, out=similarity)
    return similarity


def score_judged_targets(similarities: np.ndarray, weights: Sequence[float]) -> tuple[np.ndarray, bool]:
    matrix = np.asarray(similarities, dtype=np.float64)
    vector = np.asarray(weights, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(vector):
        raise ValueError("target/profile similarity shape mismatch")
    denominator = float(np.abs(vector).sum())
    if not math.isfinite(denominator) or denominator == 0:
        return np.zeros(matrix.shape[0], dtype=np.float64), True
    return (matrix @ vector) / denominator, False


def deterministic_rank(
    movie_ids: Sequence[int],
    personalized_scores: Sequence[float],
    b0_scores: Sequence[float],
    *,
    fallback: bool,
) -> np.ndarray:
    movies = np.asarray(movie_ids, dtype=np.int64)
    personal = np.asarray(personalized_scores, dtype=np.float64)
    b0 = np.asarray(b0_scores, dtype=np.float64)
    if movies.shape != personal.shape or movies.shape != b0.shape:
        raise ValueError("ranking arrays must align")
    if fallback:
        return np.lexsort((movies, -b0)).astype(np.int64)
    return np.lexsort((movies, -b0, -personal)).astype(np.int64)


def pair1_metrics(ranked_q_eval: Sequence[float]) -> tuple[float, float]:
    values = np.asarray(ranked_q_eval, dtype=np.float64)
    if len(values) < 2 or not np.isfinite(values[:2]).all():
        raise ValueError("two finite judged labels are required")
    pair = values[:2]
    return float(pair.mean()), float(1.0 - pair.min())


def pairwise_concordance(scores: Sequence[float], q_eval: Sequence[float]) -> float:
    prediction = np.asarray(scores, dtype=np.float64)
    truth = np.asarray(q_eval, dtype=np.float64)
    if prediction.shape != truth.shape:
        raise ValueError("prediction and truth must align")
    truth_delta = truth[:, None] - truth[None, :]
    score_delta = prediction[:, None] - prediction[None, :]
    comparable = np.triu(truth_delta != 0, k=1)
    count = int(comparable.sum())
    if not count:
        return 0.5
    concordant = ((np.sign(score_delta) == np.sign(truth_delta)) & comparable).sum()
    tied = ((score_delta == 0) & comparable).sum()
    return float((concordant + 0.5 * tied) / count)


def simultaneous_max_t(
    user_contrasts: np.ndarray,
    *,
    repeats: int = 10_000,
    seed: int = 20_260_924,
) -> Mapping[str, np.ndarray | float]:
    values = np.asarray(user_contrasts, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] < 2 or values.shape[1] < 1 or not np.isfinite(values).all():
        raise ValueError("finite users by contrasts matrix required")
    n_users = values.shape[0]
    theta = values.mean(axis=0)
    se = values.std(axis=0, ddof=1) / math.sqrt(n_users)
    active = se > 0
    maxima = np.zeros(int(repeats), dtype=np.float64)
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    for repeat in range(int(repeats)):
        indices = rng.integers(0, n_users, size=n_users, endpoint=False, dtype=np.int64)
        if bool(active.any()):
            deviation = (values[indices].mean(axis=0)[active] - theta[active]) / se[active]
            maxima[repeat] = float(np.max(np.abs(deviation)))
    critical = float(np.quantile(maxima, 0.95, method="linear"))
    half_width = critical * se
    return {
        "mean": theta,
        "se": se,
        "critical": critical,
        "low": theta - half_width,
        "high": theta + half_width,
        "half_width": half_width,
    }
