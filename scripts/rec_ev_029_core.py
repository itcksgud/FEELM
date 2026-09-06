"""Pure deterministic primitives for REC-EV-029 direct profile scoring."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Sequence

import numpy as np

from rec_ev_027_core import profile_weights, rrf_scores, weighted_similarity_scores


ENCODINGS = ("PERCENTILE_MAGNITUDE", "BINARY_SIGN")
EVEN_K = tuple(range(2, 31, 2))
FAMILIES = ("STRUCTURED_DIRECT", "E5_DIRECT", "STRUCTURED_E5_RRF")


@dataclass(frozen=True)
class Candidate:
    family: str
    encoding: str
    k: int

    @property
    def candidate_id(self) -> str:
        return f"{self.family}|{self.encoding}|K{self.k:02d}"


def candidate_grid() -> tuple[Candidate, ...]:
    return tuple(
        Candidate(family=family, encoding=encoding, k=k)
        for family in FAMILIES
        for encoding in ENCODINGS
        for k in EVEN_K
    )


def ranked_order(
    movie_ids: Sequence[int],
    scores: Sequence[float],
    *,
    cohort: str,
    outer: str,
    system: str,
    encoding: str,
    k: int,
    user_key: str,
) -> tuple[np.ndarray, bool]:
    movies = np.asarray(movie_ids, dtype=np.int64)
    values = np.asarray(scores, dtype=np.float64)
    if movies.ndim != 1 or values.shape != movies.shape or len(movies) < 2:
        raise ValueError("aligned target vectors with at least two movies required")
    active = bool(np.isfinite(values).all() and np.unique(values).size >= 2)
    if not active:
        return np.empty(0, dtype=np.int16), False
    order = sorted(
        range(len(movies)),
        key=lambda position: (
            -float(values[position]),
            hashlib.sha256(
                (
                    "rec-ev-029-score-tie-v1|"
                    f"{cohort}|{outer}|{system}|{encoding}|{int(k)}|"
                    f"{user_key}|{int(movies[position])}"
                ).encode("utf-8")
            ).digest(),
            int(movies[position]),
        ),
    )
    return np.asarray(order, dtype=np.int16), True


def score_profile_grid(
    structured_similarity: np.ndarray,
    e5_similarity: np.ndarray,
    profile_rating_indices: Sequence[int],
    g0_mid: Sequence[float],
    target_movie_ids: Sequence[int],
    *,
    cohort: str,
    outer: str,
    side: str,
    user_key: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return candidate-by-two target indices and candidate activity.

    The candidate order is exactly ``candidate_grid()``. ``side`` must be OWN
    or SHUFFLE and controls only the tie salt/system identity; caller supplies
    the corresponding profile similarities and ratings.
    """
    if side not in {"OWN", "SHUFFLE"}:
        raise ValueError("side must be OWN or SHUFFLE")
    structured = np.asarray(structured_similarity, dtype=np.float64)
    e5 = np.asarray(e5_similarity, dtype=np.float64)
    ratings = np.asarray(profile_rating_indices, dtype=np.int8)
    movies = np.asarray(target_movie_ids, dtype=np.int64)
    if (
        structured.shape != e5.shape
        or structured.ndim != 2
        or structured.shape[0] != len(movies)
        or structured.shape[1] != 30
        or ratings.shape != (30,)
        or not np.isfinite(structured).all()
        or not np.isfinite(e5).all()
    ):
        raise ValueError("finite target-by-30 similarities and profile ratings required")
    grid = candidate_grid()
    top2 = np.full((len(grid), 2), -1, dtype=np.int8)
    active = np.zeros(len(grid), dtype=bool)
    component_orders: dict[tuple[str, int], tuple[np.ndarray, bool, np.ndarray, bool]] = {}
    for encoding in ENCODINGS:
        for k in EVEN_K:
            weights = profile_weights(ratings[:k], g0_mid, encoding)
            structured_scores, structured_active = weighted_similarity_scores(structured[:, :k], weights)
            e5_scores, e5_active = weighted_similarity_scores(e5[:, :k], weights)
            structured_order, structured_active = ranked_order(
                    movies,
                    structured_scores,
                    cohort=cohort,
                    outer=outer,
                    system=f"STRUCTURED_DIRECT_{side}",
                    encoding=encoding,
                    k=k,
                    user_key=user_key,
            ) if structured_active else (np.empty(0, dtype=np.int16), False)
            e5_order, e5_active = ranked_order(
                    movies,
                    e5_scores,
                    cohort=cohort,
                    outer=outer,
                    system=f"E5_DIRECT_{side}",
                    encoding=encoding,
                    k=k,
                    user_key=user_key,
            ) if e5_active else (np.empty(0, dtype=np.int16), False)
            component_orders[(encoding, k)] = (
                structured_order,
                structured_active,
                e5_order,
                e5_active,
            )
    for cursor, candidate in enumerate(grid):
        structured_order, structured_active, e5_order, e5_active = component_orders[
            (candidate.encoding, candidate.k)
        ]
        if candidate.family == "STRUCTURED_DIRECT":
            order, is_active = structured_order, structured_active
        elif candidate.family == "E5_DIRECT":
            order, is_active = e5_order, e5_active
        else:
            if structured_active and e5_active:
                fused = rrf_scores((structured_order, e5_order), len(movies), c=10.0)
                order, is_active = ranked_order(
                    movies,
                    fused,
                    cohort=cohort,
                    outer=outer,
                    system=f"STRUCTURED_E5_RRF_{side}",
                    encoding=candidate.encoding,
                    k=candidate.k,
                    user_key=user_key,
                )
            else:
                order, is_active = np.empty(0, dtype=np.int16), False
        if is_active:
            top2[cursor] = order[:2].astype(np.int8)
            active[cursor] = True
    return top2, active
