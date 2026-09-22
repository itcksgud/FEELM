"""Market-safe GBT relations and confidence-gated sparse FM v5 features."""
from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable

import numpy as np
import pandas as pd
from scipy import sparse

from public_evidence_v5 import SOURCE_NAMES


PUBLIC_RELATION_NAMES = (
    "candidate_present",
    "same_market_history_present",
    "same_market_history_fraction",
    "source_history_fraction",
    "source_history_reliability",
    "rating_covariance_weighted",
    "candidate_delta_gated",
    "signed_affinity_gated",
    "positive_proximity_gated",
    "negative_proximity_gated",
    "positive_support",
    "negative_support",
)
GBT_EVIDENCE_NAMES = tuple(
    f"{source}_{name}" for source in SOURCE_NAMES for name in PUBLIC_RELATION_NAMES
)
CATEGORY_FIELDS = (
    ("production_country", "production_country_codes"),
    ("origin_country", "origin_country_codes"),
    ("original_language", "original_language"),
)
CATEGORY_RELATION_NAMES = (
    "candidate_present",
    "history_present",
    "history_fraction",
    "linked_fraction",
    "linked_count_log",
    "linked_reliability",
    "linked_rating_mean",
    "linked_rating_lift",
    "positive_link_fraction",
    "negative_link_fraction",
)
GBT_CATEGORY_NAMES = tuple(
    f"{namespace}_{name}"
    for namespace, _ in CATEGORY_FIELDS
    for name in CATEGORY_RELATION_NAMES
)
MARKET_RELATION_NAMES = (
    "same_market_present",
    "same_market_fraction",
    "same_market_count_log",
    "same_market_reliability",
    "same_market_rating_lift",
    "same_market_positive_fraction",
    "same_market_negative_fraction",
    "same_market_preference_balance",
    "catalog_adjusted_market_log_odds",
)
GBT_CONFIDENCE_NAMES = (
    "candidate_combined_confidence",
    "candidate_any_strong_source",
)
PUBLIC_BINS = 8
MIN_CANDIDATE_GATE = 0.05
FM_FIXED_MARKET_COEFFICIENT = 0.05
FM_LINEAR_HISTORY_NAMES = (
    "history_present",
    "history_count_log",
    "rating_mean",
    "rating_std",
    "positive_fraction",
    "negative_fraction",
    *MARKET_RELATION_NAMES,
)
FACTOR_FIELD_BUCKETS = (
    ("genre", "genre_ids", 64),
    ("production_country", "production_country_codes", 64),
    ("origin_country", "origin_country_codes", 32),
    ("original_language", "original_language", 32),
    ("spoken_language", "spoken_language_codes", 64),
    ("release_decade", "release_year", 32),
    ("director", "director_ids", 2048),
    ("cast", "top5_cast_ids", 4096),
    ("keyword", "keyword_ids", 4096),
    ("company", "production_company_ids", 2048),
    ("collection", "collection_ids", 1024),
)
FACTOR_CONTENT_WIDTH = sum(item[2] for item in FACTOR_FIELD_BUCKETS)
FM_FACTOR_WIDTH = FACTOR_CONTENT_WIDTH + len(SOURCE_NAMES) * PUBLIC_BINS


def _aligned_history(history_indices, history_stars, history_mask, candidate_indices):
    oi = np.asarray(history_indices, dtype=np.int64)
    stars = np.asarray(history_stars, dtype=np.float32)
    mask = np.asarray(history_mask, dtype=bool)
    ei = np.asarray(candidate_indices, dtype=np.int64)
    if oi.ndim != 2 or stars.shape != oi.shape or mask.shape != oi.shape:
        raise ValueError("history arrays must be aligned")
    if len(ei) != len(oi):
        raise ValueError("one candidate per history row is required")
    return oi, stars, mask, ei


def transform_public_relations(
    values,
    present,
    confidence,
    market_is_kr,
    history_indices,
    history_stars,
    history_mask,
    candidate_indices,
):
    """Build market-matched preference relations; support only gates trust."""
    values = np.asarray(values, dtype=np.float32)
    present = np.asarray(present, dtype=bool)
    confidence = np.asarray(confidence, dtype=np.float32)
    market = np.asarray(market_is_kr, dtype=bool)
    oi, stars, mask, ei = _aligned_history(
        history_indices, history_stars, history_mask, candidate_indices
    )
    if values.shape != present.shape or values.shape != confidence.shape:
        raise ValueError("aligned v5 value/presence/confidence axes required")
    if values.ndim != 2 or values.shape[1] != len(SOURCE_NAMES) or len(market) != len(values):
        raise ValueError("two v5 preference sources and one market axis required")
    if len(oi) and (oi.min() < 0 or oi.max() >= len(values) or ei.min() < 0 or ei.max() >= len(values)):
        raise ValueError("movie indices are outside the v5 evidence axis")
    k = mask.sum(axis=1)
    same_market = (market[oi] == market[ei, None]) & mask
    same_market_count = same_market.sum(axis=1)
    blocks = []
    for source in range(len(SOURCE_NAMES)):
        candidate_value = values[ei, source]
        candidate_present = present[ei, source]
        candidate_confidence = confidence[ei, source]
        history_values = values[oi, source]
        usable = present[oi, source] & same_market
        weights = confidence[oi, source] * usable
        count = usable.sum(axis=1)
        weight_sum = weights.sum(axis=1)
        denominator = np.maximum(weight_sum, 1e-6)
        mean = (history_values * weights).sum(axis=1) / denominator
        rating_mean = (stars / 5.0 * weights).sum(axis=1) / denominator
        covariance = (
            (history_values - mean[:, None])
            * (stars / 5.0 - rating_mean[:, None])
            * weights
        ).sum(axis=1) / (weight_sum + 5.0)
        delta = candidate_value - mean
        positive = usable & (stars >= 4.0)
        negative = usable & (stars <= 2.0)
        positive_weights = confidence[oi, source] * positive
        negative_weights = confidence[oi, source] * negative
        positive_sum = positive_weights.sum(axis=1)
        negative_sum = negative_weights.sum(axis=1)
        positive_mean = (history_values * positive_weights).sum(axis=1) / np.maximum(positive_sum, 1e-6)
        negative_mean = (history_values * negative_weights).sum(axis=1) / np.maximum(negative_sum, 1e-6)
        available = candidate_present & (count > 0)
        gate = candidate_confidence * available
        positive_proximity = np.where(
            candidate_present & (positive_sum > 0),
            1.0 - np.abs(candidate_value - positive_mean),
            0.0,
        )
        negative_proximity = np.where(
            candidate_present & (negative_sum > 0),
            1.0 - np.abs(candidate_value - negative_mean),
            0.0,
        )
        blocks.append(
            np.column_stack(
                [
                    candidate_present,
                    same_market_count > 0,
                    same_market_count / np.maximum(k, 1),
                    count / np.maximum(same_market_count, 1),
                    weight_sum / (weight_sum + 5.0),
                    covariance,
                    delta * gate,
                    delta * covariance * gate,
                    positive_proximity * candidate_confidence,
                    negative_proximity * candidate_confidence,
                    positive.sum(axis=1) / np.maximum(k, 1),
                    negative.sum(axis=1) / np.maximum(k, 1),
                ]
            )
        )
    result = np.column_stack(blocks).astype(np.float32)
    if result.shape != (len(ei), len(GBT_EVIDENCE_NAMES)) or not np.isfinite(result).all():
        raise ValueError("finite v5 public relation matrix required")
    return result


def _tokens(value: object, namespace: str) -> Iterable[str]:
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return ()
    if namespace == "release_decade":
        return (str(int(value) // 10 * 10),)
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return (str(item) for item in value if item is not None and str(item))
    text = str(value)
    return (text,) if text else ()


def _primary(value: object) -> str:
    tokens = sorted(set(_tokens(value, "category")))
    if "KR" in tokens:
        return "KR"
    return tokens[0] if tokens else ""


def category_codes(metadata: pd.DataFrame) -> np.ndarray:
    columns = []
    for _, column in CATEGORY_FIELDS:
        raw = metadata[column].tolist() if column in metadata else [None] * len(metadata)
        values = [_primary(value) for value in raw]
        codes, _ = pd.factorize(np.asarray(values, dtype=object), sort=True)
        codes = codes.astype(np.int32)
        codes[np.asarray(values) == ""] = -1
        columns.append(codes)
    return np.column_stack(columns)


def transform_category_relations(
    codes, history_indices, history_stars, history_mask, candidate_indices
):
    codes = np.asarray(codes, dtype=np.int32)
    oi, stars, mask, ei = _aligned_history(
        history_indices, history_stars, history_mask, candidate_indices
    )
    if codes.ndim != 2 or codes.shape[1] != len(CATEGORY_FIELDS):
        raise ValueError("three split category code axes required")
    k = mask.sum(axis=1)
    overall_mean = (stars * mask).sum(axis=1) / np.maximum(k, 1)
    positive_total = ((stars >= 4.0) & mask).sum(axis=1)
    negative_total = ((stars <= 2.0) & mask).sum(axis=1)
    blocks = []
    for field in range(codes.shape[1]):
        candidate = codes[ei, field]
        history = codes[oi, field]
        usable = (history >= 0) & mask
        usable_count = usable.sum(axis=1)
        linked = usable & (history == candidate[:, None]) & (candidate[:, None] >= 0)
        linked_count = linked.sum(axis=1)
        linked_mean = (stars * linked).sum(axis=1) / np.maximum(linked_count, 1)
        lift = (linked_mean - overall_mean) * linked_count / (linked_count + 5.0)
        blocks.append(
            np.column_stack(
                [
                    candidate >= 0,
                    usable_count > 0,
                    usable_count / np.maximum(k, 1),
                    linked_count / np.maximum(usable_count, 1),
                    np.log1p(linked_count) / math.log(31.0),
                    linked_count / (linked_count + 5.0),
                    np.where(linked_count > 0, linked_mean / 5.0, 0.0),
                    np.where(linked_count > 0, lift / 5.0, 0.0),
                    ((stars >= 4.0) & linked).sum(axis=1) / np.maximum(positive_total, 1),
                    ((stars <= 2.0) & linked).sum(axis=1) / np.maximum(negative_total, 1),
                ]
            )
        )
    result = np.column_stack(blocks).astype(np.float32)
    if result.shape != (len(ei), len(GBT_CATEGORY_NAMES)) or not np.isfinite(result).all():
        raise ValueError("finite split category relations required")
    return result


def transform_market_relations(
    market_is_kr,
    history_indices,
    history_stars,
    history_mask,
    candidate_indices,
    market_prior_kr,
):
    market = np.asarray(market_is_kr, dtype=bool)
    oi, stars, mask, ei = _aligned_history(
        history_indices, history_stars, history_mask, candidate_indices
    )
    count = mask.sum(axis=1)
    same = (market[oi] == market[ei, None]) & mask
    same_count = same.sum(axis=1)
    overall_mean = (stars * mask).sum(axis=1) / np.maximum(count, 1)
    same_mean = (stars * same).sum(axis=1) / np.maximum(same_count, 1)
    lift = (same_mean - overall_mean) * same_count / (same_count + 5.0)
    positive = (stars >= 4.0) & mask
    negative = (stars <= 2.0) & mask
    positive_fraction = (positive & same).sum(axis=1) / np.maximum(positive.sum(axis=1), 1)
    negative_fraction = (negative & same).sum(axis=1) / np.maximum(negative.sum(axis=1), 1)
    prior = np.where(market[ei], float(market_prior_kr), 1.0 - float(market_prior_kr))
    if not 0.0 < float(market_prior_kr) < 1.0:
        raise ValueError("nondegenerate Korean market prior required")
    posterior_share = (same_count + 5.0 * prior) / (count + 5.0)
    epsilon = 1e-6
    log_odds_lift = (
        np.log(np.clip(posterior_share, epsilon, 1.0 - epsilon) / np.clip(1.0 - posterior_share, epsilon, 1.0))
        - np.log(np.clip(prior, epsilon, 1.0 - epsilon) / np.clip(1.0 - prior, epsilon, 1.0))
    )
    result = np.column_stack(
        [
            same_count > 0,
            same_count / np.maximum(count, 1),
            np.log1p(same_count) / math.log(31.0),
            same_count / (same_count + 5.0),
            np.where(same_count > 0, lift / 5.0, 0.0),
            positive_fraction,
            negative_fraction,
            positive_fraction - negative_fraction,
            np.clip(log_odds_lift / 3.0, -1.0, 1.0),
        ]
    ).astype(np.float32)
    if result.shape != (len(ei), len(MARKET_RELATION_NAMES)) or not np.isfinite(result).all():
        raise ValueError("finite candidate-to-history market relations required")
    return result


def transform_candidate_confidence(combined_confidence, candidate_indices):
    combined = np.asarray(combined_confidence, dtype=np.float32)
    ei = np.asarray(candidate_indices, dtype=np.int64)
    result = np.column_stack([combined[ei], combined[ei] >= 0.5]).astype(np.float32)
    if result.shape != (len(ei), len(GBT_CONFIDENCE_NAMES)) or not np.isfinite(result).all():
        raise ValueError("finite candidate confidence features required")
    return result


def _hash(namespace: str, token: str, buckets: int) -> tuple[int, float]:
    digest = hashlib.blake2b(f"{namespace}\0{token}".encode("utf-8"), digest_size=8).digest()
    raw = int.from_bytes(digest, "little")
    return raw % buckets, 1.0 if (raw >> 63) == 0 else -1.0


def build_sparse_content(metadata, fields=FACTOR_FIELD_BUCKETS) -> sparse.csr_matrix:
    offsets, width = {}, 0
    for namespace, _, buckets in fields:
        offsets[namespace] = width
        width += buckets
    rows, columns, data = [], [], []
    frame_columns = {
        column: metadata[column].tolist() if column in metadata.columns else [None] * len(metadata)
        for _, column, _ in fields
    }
    for row in range(len(metadata)):
        accumulated: dict[int, float] = {}
        for namespace, column, buckets in fields:
            for token in _tokens(frame_columns[column][row], namespace):
                index, sign = _hash(namespace, token, buckets)
                absolute = offsets[namespace] + index
                accumulated[absolute] = accumulated.get(absolute, 0.0) + sign
        norm = math.sqrt(sum(value * value for value in accumulated.values()))
        if norm:
            for column, value in accumulated.items():
                if value:
                    rows.append(row)
                    columns.append(column)
                    data.append(value / norm)
    result = sparse.csr_matrix(
        (np.asarray(data, np.float32), (rows, columns)),
        shape=(len(metadata), width),
        dtype=np.float32,
    )
    if result.shape != (len(metadata), width) or not np.isfinite(result.data).all():
        raise ValueError("finite sparse v5 content matrix required")
    return result


def public_bin_matrix(values, present, confidence) -> sparse.csr_matrix:
    values = np.asarray(values, dtype=np.float32)
    present = np.asarray(present, dtype=bool)
    confidence = np.asarray(confidence, dtype=np.float32)
    if values.shape != present.shape or values.shape != confidence.shape or values.shape[1] != len(SOURCE_NAMES):
        raise ValueError("aligned v5 public axes required")
    rows, columns, data = [], [], []
    for source in range(len(SOURCE_NAMES)):
        source_rows = np.flatnonzero(present[:, source])
        bucket = np.minimum(
            (np.clip(values[source_rows, source], 0.0, 1.0) * PUBLIC_BINS).astype(int),
            PUBLIC_BINS - 1,
        )
        rows.extend(source_rows.tolist())
        columns.extend((source * PUBLIC_BINS + bucket).tolist())
        data.extend(confidence[source_rows, source].tolist())
    return sparse.csr_matrix(
        (np.asarray(data, np.float32), (rows, columns)),
        shape=(len(values), len(SOURCE_NAMES) * PUBLIC_BINS),
        dtype=np.float32,
    )


def _history_projection(static: sparse.csr_matrix, indices: np.ndarray, weights: np.ndarray):
    total = weights.sum(axis=1)
    normalized = weights / np.maximum(total, 1.0)[:, None]
    row_index = np.repeat(np.arange(len(indices)), indices.shape[1])
    column_index = indices.reshape(-1)
    data = normalized.reshape(-1)
    keep = data != 0
    selector = sparse.csr_matrix(
        (data[keep], (row_index[keep], column_index[keep])),
        shape=(len(indices), static.shape[0]),
        dtype=np.float32,
    )
    return (selector @ static).tocsr()


def build_fm_fields(
    factor_static,
    market_is_kr,
    combined_confidence,
    market_prior_kr,
    history_indices,
    history_stars,
    history_mask,
    candidate_indices,
):
    """Return a zero-width main effect and confidence-gated cross fields."""
    oi, stars, mask, ei = _aligned_history(
        history_indices, history_stars, history_mask, candidate_indices
    )
    market = np.asarray(market_is_kr, dtype=bool)
    combined = np.asarray(combined_confidence, dtype=np.float32)
    if factor_static.shape[1] != FM_FACTOR_WIDTH or len(market) != factor_static.shape[0]:
        raise ValueError("v5 FM sparse feature axes differ")
    candidate_linear = sparse.csr_matrix((len(ei), 0), dtype=np.float32)
    gate = MIN_CANDIDATE_GATE + (1.0 - MIN_CANDIDATE_GATE) * combined[ei]
    candidate = factor_static[ei].multiply(gate[:, None]).tocsr()
    positive_weight = np.clip((stars - 3.0) / 2.0, 0.0, 1.0) * mask
    negative_weight = np.clip((3.0 - stars) / 2.0, 0.0, 1.0) * mask
    positive = _history_projection(factor_static, oi, positive_weight)
    negative = _history_projection(factor_static, oi, negative_weight)
    count = mask.sum(axis=1)
    mean = (stars * mask).sum(axis=1) / np.maximum(count, 1)
    variance = (((stars - mean[:, None]) ** 2) * mask).sum(axis=1) / np.maximum(count, 1)
    base = np.column_stack(
        [
            count > 0,
            np.log1p(count) / math.log(31.0),
            np.where(count > 0, mean / 5.0, 0.0),
            np.where(count > 0, np.sqrt(variance) / 2.5, 0.0),
            ((stars >= 4.0) & mask).sum(axis=1) / np.maximum(count, 1),
            ((stars <= 2.0) & mask).sum(axis=1) / np.maximum(count, 1),
        ]
    ).astype(np.float32)
    market_relations = transform_market_relations(
        market, oi, stars, mask, ei, market_prior_kr
    )
    # A one-film history must not express the same market certainty as K=30.
    # This reliability gate is FM-specific; GBT receives the raw relation and
    # learns its own count-dependent splits.
    market_relations[:, -1] *= count / (count + 10.0)
    history_linear = np.column_stack([base, market_relations]).astype(np.float32)
    outputs = (candidate_linear, history_linear, candidate, positive, negative)
    if history_linear.shape[1] != len(FM_LINEAR_HISTORY_NAMES) or not np.isfinite(history_linear).all():
        raise ValueError("finite v5 FM linear relations required")
    if any(not np.isfinite(value.data).all() for value in (candidate, positive, negative)):
        raise ValueError("finite v5 FM sparse fields required")
    return outputs


def build_fm_fields_bounded_market(
    factor_static,
    market_is_kr,
    combined_confidence,
    market_prior_kr,
    history_indices,
    history_stars,
    history_mask,
    candidate_indices,
):
    """Build the v5r5 FM fields plus a bounded, non-trainable market arm.

    The base v5 model learned the first fourteen history relations without a
    catalog-adjusted market term.  The extra term is kept outside the trainable
    vector so optimization cannot undo the K-based reliability gate.
    """
    fields = build_fm_fields(
        factor_static,
        market_is_kr,
        combined_confidence,
        market_prior_kr,
        history_indices,
        history_stars,
        history_mask,
        candidate_indices,
    )
    candidate_linear, history_linear, candidate, positive, negative = fields
    fixed_adjustment = FM_FIXED_MARKET_COEFFICIENT * history_linear[:, -1]
    bounded_fields = (
        candidate_linear,
        history_linear[:, :-1],
        candidate,
        positive,
        negative,
    )
    if bounded_fields[1].shape[1] != 14 or np.max(np.abs(fixed_adjustment), initial=0.0) > FM_FIXED_MARKET_COEFFICIENT + 1e-7:
        raise ValueError("bounded FM market arm contract differs")
    return bounded_fields, fixed_adjustment.astype(np.float32)
