"""Small metadata adapter for the fixed RH-230 representation.

Movie IDs are caller-supplied integer identities in one consistent namespace;
they are never model features. The frame need only contain the supplied history
and candidates, including IDs absent from the old research catalog. Exact token
identity overlap is rebuilt locally, without fitting a vocabulary or a prior.

This is a feature-path demonstration, not a service catalog or history selector.
The caller must select legal, strictly past inputs and eligible unseen candidates.
It does not modify or regenerate the sealed RH training/score files.
"""
from __future__ import annotations

import operator
import numpy as np
import pandas as pd
from cold_item_features import Features as OriginalFeatures
from text339_relations import CappedRelations

PRIOR_MEAN = 6.173088067675869
PRIOR_MASS = 48.0
PRIOR_TRAINING_MOVIES = 44920
FEATURE_COUNT = 230
LIST_COLUMNS = (
    "genre_ids", "keyword_ids", "production_country_codes", "director_ids",
    "top5_cast_ids", "production_company_ids", "collection_ids",
)
NUMBER_COLUMNS = (
    "release_year", "runtime_minutes", "tmdb_vote_average", "tmdb_vote_count",
    "tmdb_popularity",
)


def _missing(value):
    return value is None or value is pd.NA or (
        isinstance(value, (float, np.floating)) and np.isnan(value)
    )


def _identities(values):
    result = []
    for value in values:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError("movie identities must be integers, not booleans")
        try:
            result.append(operator.index(value))
        except TypeError as error:
            raise ValueError("movie identities must be integers") from error
    if len(set(result)) != len(result):
        raise ValueError("movie identities must be unique")
    return result


def _metadata(frame):
    if "movie_id" not in frame or frame.empty:
        raise ValueError("nonempty metadata with movie_id is required")
    result = pd.DataFrame({"movie_id": _identities(frame.movie_id)})
    for name in LIST_COLUMNS:
        values = frame[name].tolist() if name in frame else [None] * len(frame)
        cells = []
        for value in values:
            if _missing(value):
                cells.append([])
            elif isinstance(value, (list, tuple, np.ndarray)):
                cells.append(list(value))
            else:
                raise ValueError(name + " must contain lists or missing values")
        result[name] = cells
    languages = frame.original_language.tolist() if "original_language" in frame else [None] * len(frame)
    result["original_language"] = ["" if _missing(v) else v for v in languages]
    if not all(isinstance(v, str) for v in result.original_language):
        raise ValueError("original_language must be a string or missing")
    for name in NUMBER_COLUMNS:
        values = frame[name].tolist() if name in frame else [None] * len(frame)
        result[name] = [np.nan if _missing(v) else float(v) for v in values]
    if np.isinf(result[list(NUMBER_COLUMNS)].to_numpy()).any():
        raise ValueError("numeric metadata must be finite or missing")
    return result


class MetadataAdapter:
    def __init__(self, metadata):
        frame = _metadata(metadata)
        self._lookup = {movie: i for i, movie in enumerate(frame.movie_id)}
        self._features = OriginalFeatures(frame)
        self._features.base = CappedRelations(frame)
        valid = self._features.valid[:, 0]
        votes = frame.tmdb_vote_count.to_numpy(float)
        # Keep the old training-W prior. New candidates never estimate it.
        values = self._features.crowd
        values[valid, 0] = (
            votes[valid] * values[valid, 0] + PRIOR_MASS * PRIOR_MEAN / 10
        ) / (votes[valid] + PRIOR_MASS)
        self.names = tuple(self._features.names[168:398])
        if len(self.names) != FEATURE_COUNT:
            raise RuntimeError("sealed RH feature contract changed")

    def transform(self, history_movie_ids, history_stars, candidate_movie_ids):
        history = _identities(history_movie_ids)
        candidates = _identities(candidate_movie_ids)
        stars = np.asarray(history_stars, dtype=float)
        if stars.ndim != 1 or len(history) != len(stars) or len(history) > 30:
            raise ValueError("one half-star rating per history movie, at most 30")
        if not np.isin(stars, np.arange(1, 11) / 2).all():
            raise ValueError("history ratings must use 0.5 steps in [0.5, 5]")
        if set(history).intersection(candidates):
            raise ValueError("history and candidates must be disjoint")
        if any(movie not in self._lookup for movie in history + candidates):
            raise ValueError("every requested movie needs a metadata row")
        if not candidates:
            return np.empty((0, FEATURE_COUNT), dtype=np.float32)
        oi = np.array([self._lookup[movie] for movie in history], dtype=int)
        ei = np.array([self._lookup[movie] for movie in candidates], dtype=int)
        return self._features.transform(oi, stars, ei)[:, 168:398]
