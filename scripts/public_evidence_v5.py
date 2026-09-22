"""Market-conditioned public evidence for the model-specific v5 rankers.

TMDB vote count is deliberately a reliability signal, never a preference axis.
TMDB rating quality and KOBIS cumulative audience are normalized inside the
candidate's Korean/non-Korean market so a Korean title with modest TMDB reach is
not treated as equivalent to an obscure foreign title.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


SOURCE_NAMES = ("tmdb_quality_market", "kobis_audience_market")
TMDB_PRIOR_MASS = 250.0
TMDB_CONFIDENCE_FLOOR = 500.0


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _tokens(value: object) -> set[str]:
    if value is None or value is pd.NA:
        return set()
    if isinstance(value, float) and np.isnan(value):
        return set()
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return {str(item) for item in value if item is not None and str(item)}
    return {str(value)} if str(value) else set()


def market_is_korean(frame: pd.DataFrame) -> np.ndarray:
    production = (
        frame["production_country_codes"].tolist()
        if "production_country_codes" in frame
        else [None] * len(frame)
    )
    origin = (
        frame["origin_country_codes"].tolist()
        if "origin_country_codes" in frame
        else [None] * len(frame)
    )
    return np.asarray(
        ["KR" in (_tokens(left) | _tokens(right)) for left, right in zip(production, origin, strict=True)],
        dtype=bool,
    )


def percentile_against(reference: np.ndarray, values: np.ndarray) -> np.ndarray:
    reference = np.sort(np.asarray(reference, dtype=float))
    values = np.asarray(values, dtype=float)
    require(bool(len(reference)), "nonempty market percentile reference required")
    left = np.searchsorted(reference, values, side="left")
    right = np.searchsorted(reference, values, side="right")
    return ((left + right) / 2.0 + 0.5) / len(reference)


def _posterior_lower_bound(average: np.ndarray, votes: np.ndarray, prior_mean: float) -> np.ndarray:
    posterior = (average * votes + prior_mean * TMDB_PRIOR_MASS) / (votes + TMDB_PRIOR_MASS)
    probability = np.clip(posterior / 10.0, 1e-4, 1.0 - 1e-4)
    uncertainty = np.sqrt(probability * (1.0 - probability) / (votes + TMDB_PRIOR_MASS))
    return probability - 1.96 * uncertainty


def source_axes_v5(
    metadata: pd.DataFrame, full_catalog: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, object]]:
    """Return preference values, presence, reliability, market and combined gate.

    Preference values contain only market-relative TMDB rating quality and
    market-relative KOBIS audience. Reliability contains TMDB vote support and
    KOBIS audience strength. The latter may scale a relation but is never emitted
    as an independent preference magnitude.
    """
    required = {
        "tmdb_id",
        "tmdb_vote_average",
        "tmdb_vote_count",
        "kobis_audience_cumulative",
        "kobis_link_status",
        "kobis_statistics_as_of",
    }
    require(required.issubset(full_catalog.columns), "full catalog lacks v5 evidence columns")
    audience_full = pd.to_numeric(full_catalog["kobis_audience_cumulative"], errors="coerce").to_numpy(float)
    kobis_rows = np.isfinite(audience_full) & (audience_full > 0)
    require(
        full_catalog.loc[kobis_rows, "kobis_link_status"].astype(str).str.startswith("VERIFIED").all(),
        "KOBIS features require a verified crosswalk",
    )
    require(
        full_catalog.loc[kobis_rows, "kobis_statistics_as_of"].notna().all(),
        "KOBIS features require statistics with an explicit as-of date",
    )

    market_full = market_is_korean(full_catalog)
    market = market_is_korean(metadata)
    full_average = pd.to_numeric(full_catalog["tmdb_vote_average"], errors="coerce").to_numpy(float)
    full_votes = pd.to_numeric(full_catalog["tmdb_vote_count"], errors="coerce").to_numpy(float)
    full_tmdb = (
        np.isfinite(full_average)
        & np.isfinite(full_votes)
        & (full_average > 0)
        & (full_average <= 10)
        & (full_votes > 0)
    )
    require(bool(full_tmdb.any()), "valid full-catalog TMDB reference required")

    average = pd.to_numeric(metadata["tmdb_vote_average"], errors="coerce").to_numpy(float)
    votes = pd.to_numeric(metadata["tmdb_vote_count"], errors="coerce").to_numpy(float)
    tmdb_present = (
        np.isfinite(average)
        & np.isfinite(votes)
        & (average > 0)
        & (average <= 10)
        & (votes > 0)
    )

    lookup = (
        full_catalog.loc[kobis_rows, ["tmdb_id", "kobis_audience_cumulative"]]
        .drop_duplicates("tmdb_id")
        .set_index("tmdb_id")["kobis_audience_cumulative"]
    )
    audience = pd.to_numeric(metadata["tmdb_id"].map(lookup), errors="coerce").to_numpy(float)
    kobis_present = np.isfinite(audience) & (audience > 0)

    values = np.zeros((len(metadata), len(SOURCE_NAMES)), dtype=np.float32)
    confidence = np.zeros_like(values)
    market_info: dict[str, object] = {}
    for is_kr, label in ((False, "NON_KR"), (True, "KR")):
        ref_tmdb = full_tmdb & (market_full == is_kr)
        target_tmdb = tmdb_present & (market == is_kr)
        require(bool(ref_tmdb.any()), f"TMDB reference missing for {label}")
        clipped_weight = np.minimum(full_votes[ref_tmdb], 1000.0)
        prior_mean = float(np.average(full_average[ref_tmdb], weights=clipped_weight))
        reference_quality = _posterior_lower_bound(
            full_average[ref_tmdb], full_votes[ref_tmdb], prior_mean
        )
        values[target_tmdb, 0] = percentile_against(
            reference_quality,
            _posterior_lower_bound(average[target_tmdb], votes[target_tmdb], prior_mean),
        )
        tmdb_tau = max(float(np.quantile(full_votes[ref_tmdb], 0.90)), TMDB_CONFIDENCE_FLOOR)
        confidence[target_tmdb, 0] = votes[target_tmdb] / (votes[target_tmdb] + tmdb_tau)

        ref_kobis = kobis_rows & (market_full == is_kr)
        target_kobis = kobis_present & (market == is_kr)
        require(bool(ref_kobis.any()), f"KOBIS reference missing for {label}")
        reference_audience = audience_full[ref_kobis]
        values[target_kobis, 1] = percentile_against(
            np.log1p(reference_audience), np.log1p(audience[target_kobis])
        )
        kobis_tau = float(np.median(reference_audience))
        confidence[target_kobis, 1] = audience[target_kobis] / (audience[target_kobis] + kobis_tau)
        market_info[label] = {
            "tmdb_reference_movies": int(ref_tmdb.sum()),
            "tmdb_prior_mean": prior_mean,
            "tmdb_confidence_tau": tmdb_tau,
            "kobis_reference_movies": int(ref_kobis.sum()),
            "kobis_confidence_tau": kobis_tau,
        }

    present = np.column_stack([tmdb_present, kobis_present])
    combined = 1.0 - np.prod(1.0 - confidence, axis=1)
    require(np.isfinite(values).all() and np.isfinite(confidence).all(), "finite v5 evidence required")
    require(((values >= 0) & (values <= 1)).all(), "normalized v5 values required")
    require(((confidence >= 0) & (confidence <= 1)).all(), "normalized v5 confidence required")
    return values, present, confidence, market, combined.astype(np.float32), {
        "movies": len(metadata),
        "sources": list(SOURCE_NAMES),
        "tmdb_present": int(tmdb_present.sum()),
        "kobis_present": int(kobis_present.sum()),
        "korean_market_movies": int(market.sum()),
        "full_catalog_korean_market_share": float(market_full.mean()),
        "tmdb_prior_mass": TMDB_PRIOR_MASS,
        "tmdb_vote_count_role": "reliability_only_not_preference",
        "combined_confidence": "1-(1-tmdb_confidence)*(1-kobis_confidence)",
        "market_references": market_info,
    }
