"""v3 correction: no measured TMDB rating is *not* the TMDB prior mean.

This variant retains v2's training/serving feature order so any difference is
caused by the predeclared missing-value and shrink-strength changes.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from public_evidence_v2 import PublicCalibration, numeric_features_v2


def source_shift_calibration(catalog: pd.DataFrame, train_rows: pd.DataFrame) -> tuple[PublicCalibration, dict]:
    base = PublicCalibration.from_catalog(catalog)
    counts = pd.to_numeric(train_rows.tmdb_vote_count, errors="coerce").to_numpy(np.float64)
    valid = np.isfinite(counts) & (counts > 0)
    if valid.sum() < 1000:
        raise ValueError("insufficient observed training vote counts")
    train_median = float(np.median(counts[valid]))
    effective = float(np.sqrt(base.prior_votes * train_median))
    if not (np.isfinite(effective) and effective >= base.prior_votes):
        raise ValueError("invalid source-shift shrink strength")
    return replace(base, prior_votes=effective), {
        "method": "geometric_mean(EB dispersion pseudo-votes, train-only median TMDB votes)",
        "base_prior_votes": base.prior_votes,
        "train_median_vote_count": train_median,
        "train_nonmissing_vote_rows": int(valid.sum()),
        "effective_prior_votes": effective,
    }


def numeric_features_v3(rows: pd.DataFrame, calibration: PublicCalibration,
                        *, vote_context: dict[str, np.ndarray]) -> tuple[np.ndarray, list[str]]:
    matrix, names = numeric_features_v2(rows, calibration, vote_context=vote_context)
    average = pd.to_numeric(rows.tmdb_vote_average, errors="coerce").to_numpy(np.float64)
    votes = pd.to_numeric(rows.tmdb_vote_count, errors="coerce").to_numpy(np.float64)
    measured = np.isfinite(average) & (average > 0) & (average <= 10) & np.isfinite(votes) & (votes > 0)
    for name in ("tmdb_eb_mean_0_1", "tmdb_eb_lcb_0_1"):
        matrix[~measured, names.index(name)] = 0
    return matrix, names
