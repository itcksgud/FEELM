"""Shared, fitted public-evidence transform for strict-past GBT/FM experiments.

Calibration uses only catalogue metadata, never held-out rating labels.  A
normal/normal empirical-Bayes approximation makes one 10/10 vote uncertain;
it does not turn KOBIS audience into a star rating or create negative labels.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy import sparse

from train_evidence_rank_models_v1 import numeric_features


@dataclass(frozen=True)
class PublicCalibration:
    prior_mean: float
    between_variance: float
    rating_noise_variance: float
    prior_votes: float
    reference_vote_quantile: float
    reference_count: int
    one_vote_count: int
    kobis_log_p95: float

    @classmethod
    def from_catalog(cls, catalog: pd.DataFrame) -> "PublicCalibration":
        votes = pd.to_numeric(catalog.tmdb_vote_count, errors="coerce").to_numpy(np.float64)
        average = pd.to_numeric(catalog.tmdb_vote_average, errors="coerce").to_numpy(np.float64)
        valid = np.isfinite(votes) & np.isfinite(average) & (votes > 0) & (average >= 0) & (average <= 10)
        if valid.sum() < 1000:
            raise ValueError("TMDB catalogue too small to fit public calibration")
        reference_vote_quantile = float(np.quantile(votes[valid], .99))
        reference = valid & (votes >= reference_vote_quantile)
        one = valid & (votes == 1)
        if reference.sum() < 100 or one.sum() < 100:
            raise ValueError("calibration reference/one-vote cohorts missing")
        prior_mean = float(average[reference].mean())
        between = float(average[reference].var(ddof=1))
        one_variance = float(average[one].var(ddof=1))
        noise = one_variance - between
        if not (np.isfinite(noise) and noise > 0 and between > 0):
            raise ValueError("unidentifiable positive TMDB dispersion")
        kobis = pd.to_numeric(catalog.kobis_audience_cumulative, errors="coerce").to_numpy(np.float64)
        verified_status = catalog.kobis_link_status.fillna("").astype(str).str.startswith("VERIFIED").to_numpy()
        verified = verified_status & np.isfinite(kobis) & (kobis > 0)
        if verified.sum() < 100:
            raise ValueError("verified KOBIS reference missing")
        return cls(prior_mean, between, noise, noise / between,
                   reference_vote_quantile, int(reference.sum()), int(one.sum()),
                   float(np.quantile(np.log1p(kobis[verified]), .95)))

    @classmethod
    def from_dict(cls, state: dict) -> "PublicCalibration":
        return cls(**state)

    def to_dict(self) -> dict:
        return asdict(self)


def public_columns(rows: pd.DataFrame, calibration: PublicCalibration) -> dict[str, np.ndarray]:
    average = pd.to_numeric(rows.tmdb_vote_average, errors="coerce").to_numpy(np.float64)
    votes = pd.to_numeric(rows.tmdb_vote_count, errors="coerce").to_numpy(np.float64)
    valid = np.isfinite(average) & (average >= 0) & (average <= 10) & np.isfinite(votes) & (votes > 0)
    safe_votes = np.where(valid, np.maximum(votes, 0), 0)
    safe_average = np.where(valid, average, calibration.prior_mean)
    m = calibration.prior_votes
    posterior = (safe_votes * safe_average + m * calibration.prior_mean) / (safe_votes + m)
    variance = calibration.between_variance * calibration.rating_noise_variance / (
        calibration.rating_noise_variance + safe_votes * calibration.between_variance)
    uncertainty = np.sqrt(variance)
    reliability = safe_votes / (safe_votes + m)
    audience = pd.to_numeric(rows.kobis_audience_cumulative, errors="coerce").to_numpy(np.float64)
    verified = rows.kobis_verified.fillna(False).to_numpy(bool)
    kobis_known = verified & np.isfinite(audience) & (audience > 0)
    kobis_log = np.where(kobis_known, np.log1p(np.maximum(audience, 0)), 0)
    kobis_strength = np.clip(kobis_log / calibration.kobis_log_p95, 0, 1)
    result = {
        "tmdb_eb_mean_0_1": (posterior / 10).astype(np.float32),
        "tmdb_eb_lcb_0_1": ((posterior - uncertainty) / 10).astype(np.float32),
        "tmdb_eb_uncertainty_0_1": (uncertainty / 10).astype(np.float32),
        "tmdb_reliability": reliability.astype(np.float32),
        "kobis_strength": kobis_strength.astype(np.float32),
        "either_public_strength": np.maximum(reliability, kobis_strength).astype(np.float32),
    }
    if not all(np.isfinite(value).all() for value in result.values()):
        raise ValueError("nonfinite public evidence feature")
    return result


def user_vote_context(selector: sparse.csr_matrix, movie_votes: np.ndarray) -> dict[str, np.ndarray]:
    """Strict-past positive/negative utility-weighted volume and coverage."""
    if selector.shape[1] != len(movie_votes):
        raise ValueError("selector movie axis mismatch")
    known = (np.isfinite(movie_votes) & (movie_votes > 0)).astype(np.float32)
    log_votes = np.log1p(np.maximum(np.nan_to_num(movie_votes, nan=0), 0)).astype(np.float32)
    positive = selector.maximum(0).tocsr()
    negative = (-selector).maximum(0).tocsr()
    positive_weight = np.asarray(positive.sum(axis=1)).reshape(-1)
    negative_weight = np.asarray(negative.sum(axis=1)).reshape(-1)
    pos = np.asarray(positive @ log_votes).reshape(-1) / np.maximum(positive_weight, 1e-8)
    neg = np.asarray(negative @ log_votes).reshape(-1) / np.maximum(negative_weight, 1e-8)
    return {
        "positive_mean": pos.astype(np.float32),
        "negative_mean": neg.astype(np.float32),
        "positive_present": (positive_weight > 0).astype(np.float32),
        "negative_present": (negative_weight > 0).astype(np.float32),
        "positive_known_share": (np.asarray(positive @ known).reshape(-1) /
                                 np.maximum(positive_weight, 1e-8)).astype(np.float32),
        "negative_known_share": (np.asarray(negative @ known).reshape(-1) /
                                 np.maximum(negative_weight, 1e-8)).astype(np.float32),
    }


def numeric_features_v2(rows: pd.DataFrame, calibration: PublicCalibration,
                        *, vote_context: dict[str, np.ndarray] | None = None,
                        history_style: bool = True) -> tuple[np.ndarray, list[str]]:
    original, names, _ = numeric_features(rows)
    # Raw 1-vote 10/10 must not survive as a separate high-weight FM linear arm.
    keep = [index for index, name in enumerate(names) if name != "tmdb_average_0_1"]
    columns = {names[index]: original[:, index] for index in keep}
    columns.update(public_columns(rows, calibration))
    k = len(rows)
    required = ("positive_mean", "negative_mean", "positive_present", "negative_present",
                "positive_known_share", "negative_known_share")
    if vote_context is None or any(name not in vote_context or len(vote_context[name]) != k for name in required):
        raise ValueError("complete history vote context required")
    candidate_log_votes = columns["tmdb_votes_log1p"]
    pos = np.asarray(vote_context["positive_mean"], np.float32)
    neg = np.asarray(vote_context["negative_mean"], np.float32)
    columns["history_positive_log_votes"] = pos
    columns["history_negative_log_votes"] = neg
    for name in required[2:]:
        columns[f"history_{name}"] = np.asarray(vote_context[name], np.float32)
    columns["candidate_x_positive_log_votes"] = candidate_log_votes * pos / 14
    columns["candidate_x_negative_log_votes"] = candidate_log_votes * neg / 14
    columns["candidate_positive_vote_distance"] = (
        np.abs(candidate_log_votes - pos) * columns["history_positive_present"])
    collection = np.log1p(rows.collection_strong_pos_count.fillna(0).to_numpy(np.float32))
    director = np.log1p(rows.director_strong_pos_count.fillna(0).to_numpy(np.float32))
    cast = np.log1p(rows.cast_strong_pos_count.fillna(0).to_numpy(np.float32))
    columns["series_strong_evidence"] = collection
    columns["director_strong_evidence"] = director
    columns["cast_only_strong_evidence"] = np.where((collection + director) == 0, cast, 0)
    low = 1 - columns["either_public_strength"]
    columns["series_x_public_uncertainty"] = collection * low
    columns["cast_only_x_public_uncertainty"] = columns["cast_only_strong_evidence"] * low
    if history_style:
        for name in ("history_rating_mean", "history_rating_std",
                     "history_positive_share", "history_negative_share"):
            columns[name] = rows[name].to_numpy(np.float32)
    names = list(columns)
    matrix = np.column_stack(list(columns.values())).astype(np.float32)
    if not np.isfinite(matrix).all():
        raise ValueError("nonfinite v2 numeric feature")
    return matrix, names
