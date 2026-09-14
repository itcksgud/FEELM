"""Pure numerical building blocks for the hybrid345 comparison."""
from __future__ import annotations

import hashlib

import numpy as np


def rating_indices(stars):
    values = np.asarray(stars, dtype=np.float64)
    indices = np.rint(values * 2 - 1).astype(np.int64)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError("ratings must be a finite vector")
    if not np.allclose(values, (indices + 1) / 2, rtol=0, atol=1e-12):
        raise ValueError("ratings must use the 0.5 grid")
    if ((indices < 0) | (indices >= 10)).any():
        raise ValueError("ratings must be between 0.5 and 5.0")
    return indices


def percentile_weights(stars, prior, prior_mass=5.0):
    """REC032 percentile-magnitude weights, retaining exact half-star inputs."""
    indices = rating_indices(stars)
    prior = np.asarray(prior, dtype=np.float64)
    if prior.shape != (10,) or not np.isfinite(prior).all():
        raise ValueError("prior must have ten finite mid-ranks")
    hist = np.bincount(indices, minlength=10)
    below = np.cumsum(hist) - hist
    q = (below[indices] + 0.5 * hist[indices] + prior_mass * prior[indices]) / (
        len(indices) + prior_mass
    )
    return 2 * q - 1


def dense_profile_scores(vectors, history_indices, stars, candidate_indices, prior):
    history_indices = np.asarray(history_indices, dtype=np.int64)
    candidate_indices = np.asarray(candidate_indices, dtype=np.int64)
    if len(history_indices) == 0:
        return np.full(len(candidate_indices), np.nan), False
    weights = percentile_weights(stars, prior)
    denominator = float(np.abs(weights).sum())
    if denominator <= 1e-12:
        return np.full(len(candidate_indices), np.nan), False
    profile = np.asarray(vectors[history_indices], dtype=np.float64).T @ (weights / denominator)
    if not np.isfinite(profile).all() or np.linalg.norm(profile) <= 1e-12:
        return np.full(len(candidate_indices), np.nan), False
    scores = np.asarray(vectors[candidate_indices], dtype=np.float64) @ profile
    if not np.isfinite(scores).all():
        raise ValueError("nonfinite dense content score")
    return scores, True


def sparse_profile_scores(matrix, history_indices, stars, candidate_indices, prior):
    history_indices = np.asarray(history_indices, dtype=np.int64)
    candidate_indices = np.asarray(candidate_indices, dtype=np.int64)
    if len(history_indices) == 0:
        return np.full(len(candidate_indices), np.nan), False
    weights = percentile_weights(stars, prior)
    denominator = float(np.abs(weights).sum())
    if denominator <= 1e-12:
        return np.full(len(candidate_indices), np.nan), False
    profile = np.asarray(matrix[history_indices].T @ (weights / denominator)).ravel()
    if not np.isfinite(profile).all() or np.linalg.norm(profile) <= 1e-12:
        return np.full(len(candidate_indices), np.nan), False
    scores = np.asarray(matrix[candidate_indices] @ profile).ravel().astype(np.float64)
    if not np.isfinite(scores).all():
        raise ValueError("nonfinite sparse content score")
    return scores, True


def fold_in_scores(history_factors, stars, candidate_factors, reg=0.1):
    history = np.asarray(history_factors, dtype=np.float64)
    candidates = np.asarray(candidate_factors, dtype=np.float64)
    stars = np.asarray(stars, dtype=np.float64)
    if history.ndim != 2 or candidates.ndim != 2 or history.shape[1] != candidates.shape[1]:
        raise ValueError("factor dimensions do not match")
    if len(history) == 0:
        return np.full(len(candidates), np.nan), False
    rating_indices(stars)
    if stars.shape != (len(history),) or not np.isfinite(history).all() or not np.isfinite(candidates).all():
        raise ValueError("invalid fold-in inputs")
    user = np.linalg.solve(
        history.T @ history + reg * len(history) * np.eye(history.shape[1]),
        history.T @ stars,
    )
    scores = candidates @ user
    if not np.isfinite(scores).all():
        raise ValueError("nonfinite fold-in score")
    return scores, True


def stratified_mapper_split(movie_ids, support, salt, train_fraction=0.8):
    movie_ids = np.asarray(movie_ids, dtype=np.int64)
    support = np.asarray(support, dtype=np.int64)
    if movie_ids.shape != support.shape or not (0 < train_fraction < 1):
        raise ValueError("invalid mapper split inputs")
    strata = np.where(support < 10, 0, np.where(support < 50, 1, 2))
    train = np.zeros(len(movie_ids), dtype=bool)
    for stratum in range(3):
        positions = np.flatnonzero(strata == stratum)
        if len(positions) < 2:
            raise ValueError("mapper stratum too small")
        keys = [hashlib.sha256(f"{salt}{int(movie_ids[p])}".encode()).digest() for p in positions]
        order = np.asarray(sorted(range(len(positions)), key=lambda j: (keys[j], int(movie_ids[positions[j]]))))
        count = int(np.floor(train_fraction * len(positions)))
        count = min(max(count, 1), len(positions) - 1)
        train[positions[order[:count]]] = True
    return train, ~train, strata


def ridge_sufficient_statistics(x, y):
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.ndim != 2 or y.ndim != 2 or len(x) != len(y) or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("invalid ridge inputs")
    x_mean, y_mean = x.mean(axis=0), y.mean(axis=0)
    xc, yc = x - x_mean, y - y_mean
    return x_mean, y_mean, xc.T @ xc, xc.T @ yc


def ridge_from_statistics(x_mean, y_mean, xtx, xty, alpha):
    if not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("alpha must be positive")
    coefficient = np.linalg.solve(xtx + alpha * np.eye(xtx.shape[0]), xty)
    intercept = y_mean - x_mean @ coefficient
    if not np.isfinite(coefficient).all() or not np.isfinite(intercept).all():
        raise ValueError("nonfinite ridge fit")
    return coefficient, intercept


def factor_reconstruction(y, prediction):
    y = np.asarray(y, dtype=np.float64)
    prediction = np.asarray(prediction, dtype=np.float64)
    if y.shape != prediction.shape or y.ndim != 2:
        raise ValueError("factor reconstruction shapes differ")
    yn = np.linalg.norm(y, axis=1)
    pn = np.linalg.norm(prediction, axis=1)
    valid = (yn > 1e-12) & (pn > 1e-12) & np.isfinite(yn) & np.isfinite(pn)
    cosine = np.full(len(y), np.nan)
    cosine[valid] = np.sum(y[valid] * prediction[valid], axis=1) / (yn[valid] * pn[valid])
    return {
        "items": int(len(y)),
        "cosine_items": int(valid.sum()),
        "mean_cosine": float(np.nanmean(cosine)) if valid.any() else None,
        "rmse": float(np.sqrt(np.mean((prediction - y) ** 2))),
        "mean_target_norm": float(yn.mean()),
        "mean_prediction_norm": float(pn.mean()),
    }


def fit_nonnegative_affine(raw, truth):
    raw = np.asarray(raw, dtype=np.float64)
    truth = np.asarray(truth, dtype=np.float64)
    valid = np.isfinite(raw) & np.isfinite(truth)
    if valid.sum() < 2:
        raise ValueError("too few calibration rows")
    x, y = raw[valid], truth[valid]
    variance = float(np.mean((x - x.mean()) ** 2))
    covariance = float(np.mean((x - x.mean()) * (y - y.mean())))
    slope = max(covariance / variance, 0.0) if variance > 1e-15 else 0.0
    intercept = float(y.mean() - slope * x.mean())
    return intercept, float(slope), int(valid.sum()), variance, covariance


def apply_affine(raw, intercept, slope):
    return intercept + slope * np.asarray(raw, dtype=np.float64)


def stable_order(scores, movie_ids):
    scores = np.asarray(scores, dtype=np.float64)
    movie_ids = np.asarray(movie_ids, dtype=np.int64)
    if scores.shape != movie_ids.shape or not np.isfinite(scores).all():
        raise ValueError("invalid ranking inputs")
    return np.lexsort((movie_ids, -scores))
