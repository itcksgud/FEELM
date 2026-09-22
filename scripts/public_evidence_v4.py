"""Unsupervised, source-preserving country/genre support for the v4 probe.

Counts are ranked within their own source.  KOBIS admissions are never
fabricated as TMDB votes or as a movie/user rating.  Zero support denotes
absent *public evidence*, not a negative preference label.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from public_evidence_v2 import PublicCalibration
from public_evidence_v3 import numeric_features_v3


FEATURES = (
    "tmdb_country_genre_support",
    "kobis_country_genre_support",
    "either_country_genre_support",
    "kobis_minus_tmdb_support",
)


def _tokens(value) -> list[str]:
    if value is None or isinstance(value, float) and np.isnan(value):
        return []
    if isinstance(value, (list, tuple, np.ndarray)):
        return [str(item) for item in value if item is not None and str(item)]
    return [str(value)] if str(value) else []


def _source_percentile(base: pd.DataFrame, observed: np.ndarray,
                       values: np.ndarray) -> tuple[np.ndarray, dict]:
    result = np.zeros(len(base), np.float32)
    active = base.loc[observed, ["tmdb_id", "country", "decade", "genres"]].copy()
    active["value"] = values[observed]
    if active.empty:
        raise ValueError("no measured films for source normalization")
    active["global_rank"] = active.value.rank(method="average", pct=True)
    by_cohort = active.groupby(["country", "decade"], dropna=False).value
    active["cohort_rank"] = by_cohort.rank(method="average", pct=True)
    active["cohort_n"] = by_cohort.transform("size")
    weight = active.cohort_n / (active.cohort_n + 100.0)
    active["cohort_support"] = (weight * active.cohort_rank +
                                (1 - weight) * active.global_rank)
    joint = active[["tmdb_id", "country", "genres", "value", "cohort_support"]].explode("genres")
    joint = joint[joint.genres.notna()].copy()
    if len(joint):
        by_joint = joint.groupby(["country", "genres"], dropna=False).value
        joint_rank = by_joint.rank(method="average", pct=True)
        joint_n = by_joint.transform("size")
        joint_weight = joint_n / (joint_n + 50.0)
        joint["support"] = (joint_weight * joint_rank +
                            (1 - joint_weight) * joint.cohort_support)
        by_film = joint.groupby("tmdb_id").support.mean()
        active["support"] = active.tmdb_id.map(by_film).fillna(active.cohort_support)
    else:
        active["support"] = active.cohort_support
    result[np.flatnonzero(observed)] = active.support.to_numpy(np.float32)
    if not (np.isfinite(result).all() and ((result >= 0) & (result <= 1)).all()):
        raise ValueError("invalid source percentile")
    return result, {"observed_films": int(observed.sum()),
                    "country_decade_groups": int(active.groupby(["country", "decade"]).ngroups),
                    "country_genre_groups": int(joint.groupby(["country", "genres"]).ngroups)}


def build_cohort_support(catalog: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict]:
    """Return sorted TMDB ids and four country/genre-relative evidence values."""
    required = {"tmdb_id", "origin_country_codes", "production_country_codes",
                "genre_ids", "release_year", "tmdb_vote_average", "tmdb_vote_count",
                "kobis_link_status", "kobis_audience_cumulative"}
    if not required.issubset(catalog):
        raise ValueError(f"catalog missing {sorted(required - set(catalog))}")
    base = catalog.sort_values("tmdb_id", kind="stable").reset_index(drop=True)
    ids = base.tmdb_id.to_numpy(np.int64)
    if base.tmdb_id.isna().any() or pd.Index(ids).duplicated().any():
        raise ValueError("tmdb_id axis must be unique and non-null")
    country = []
    for origin, production in zip(base.origin_country_codes, base.production_country_codes, strict=True):
        candidates = _tokens(origin) or _tokens(production)
        country.append(candidates[0] if candidates else "UNKNOWN")
    year = pd.to_numeric(base.release_year, errors="coerce").to_numpy(np.float64)
    decade = np.where(np.isfinite(year) & (year >= 1880) & (year <= 2100),
                      np.floor(np.nan_to_num(year, nan=0) / 10) * 10, -1).astype(np.int32)
    frame = pd.DataFrame({"tmdb_id": ids, "country": country, "decade": decade,
                          "genres": [_tokens(value) for value in base.genre_ids]})
    votes = pd.to_numeric(base.tmdb_vote_count, errors="coerce").to_numpy(np.float64)
    average = pd.to_numeric(base.tmdb_vote_average, errors="coerce").to_numpy(np.float64)
    observed_tmdb = np.isfinite(votes) & (votes > 0) & np.isfinite(average) & (average > 0) & (average <= 10)
    audience = pd.to_numeric(base.kobis_audience_cumulative, errors="coerce").to_numpy(np.float64)
    verified = base.kobis_link_status.fillna("").astype(str).str.startswith("VERIFIED").to_numpy()
    observed_kobis = verified & np.isfinite(audience) & (audience > 0)
    tmdb, tmdb_stats = _source_percentile(frame, observed_tmdb, votes)
    kobis, kobis_stats = _source_percentile(frame, observed_kobis, audience)
    matrix = np.column_stack((tmdb, kobis, np.maximum(tmdb, kobis), kobis - tmdb)).astype(np.float32)
    return ids, matrix, {"tmdb": tmdb_stats, "kobis": kobis_stats,
                         "country_rule": "first origin country, fallback first production country",
                         "genre_rule": "mean across all candidate genres",
                         "cohort_rule": "country x release decade with n/(n+100), then country x genre with n/(n+50)"}


def numeric_features_v4(rows: pd.DataFrame, calibration: PublicCalibration,
                        *, vote_context: dict[str, np.ndarray], tmdb_axis: np.ndarray,
                        cohort_support: np.ndarray) -> tuple[np.ndarray, list[str]]:
    original, names = numeric_features_v3(rows, calibration, vote_context=vote_context)
    ids = rows.tmdb_id.to_numpy(np.int64)
    positions = np.searchsorted(tmdb_axis, ids)
    if ((positions >= len(tmdb_axis)).any() or
            not np.array_equal(tmdb_axis[positions], ids)):
        raise ValueError("target TMDB id absent from source-normalization axis")
    if cohort_support.shape != (len(tmdb_axis), len(FEATURES)):
        raise ValueError("wrong source-normalization matrix shape")
    matrix = np.column_stack((original, cohort_support[positions])).astype(np.float32)
    if not np.isfinite(matrix).all():
        raise ValueError("nonfinite v4 features")
    return matrix, names + list(FEATURES)
