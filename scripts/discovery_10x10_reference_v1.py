"""Fail-closed reference implementation of FEELM discovery 10 x 10.

The fixed 8 x 128 GKT geometry and the 10 x 10 candidate policy are separate:
the former assigns each supported movie to one of 1,024 groups, while the
latter inspects at most ten supplying groups and takes at most ten movies from
each.  This module deliberately stops before storage, Kafka, Redis, or Spring
session assembly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence
import json
import math

import numpy as np


EXCLUSION_REASONS = (
    "watched",
    "rated",
    "dismissed",
    "filtered",
    "exposed",
    "reserved",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _positive_int(value: object, field: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool) and value > 0,
             f"{field} must be a positive integer")
    return int(value)


def _nonempty(value: object, field: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{field} must be non-empty")
    return value


@dataclass(frozen=True)
class DiscoveryPolicy:
    parent_taste_count: int = 8
    children_per_taste: int = 128
    maximum_supplying_groups: int = 10
    maximum_candidates_per_group: int = 10
    maximum_candidates: int = 100
    maximum_viewed_per_group: int = 2
    maximum_viewed_fraction: float = 0.20
    direction_norm_threshold: float = 1e-8

    @property
    def total_groups(self) -> int:
        return self.parent_taste_count * self.children_per_taste

    def validate(self) -> None:
        _require(self.parent_taste_count == 8, "v1 requires exactly 8 parent tastes")
        _require(self.children_per_taste == 128, "v1 requires exactly 128 children per taste")
        _require(self.maximum_supplying_groups == 10, "v1 requires at most 10 supplying groups")
        _require(self.maximum_candidates_per_group == 10,
                 "v1 requires at most 10 candidates per group")
        _require(self.maximum_candidates == 100, "v1 requires at most 100 discovery candidates")
        _require(self.maximum_candidates ==
                 self.maximum_supplying_groups * self.maximum_candidates_per_group,
                 "maximumCandidates must equal supplyingGroups times candidatesPerGroup")
        _require(self.maximum_viewed_per_group == 2, "v1 maximumViewedPerGroup must be 2")
        _require(math.isclose(self.maximum_viewed_fraction, 0.20, rel_tol=0.0, abs_tol=0.0),
                 "v1 maximumViewedFraction must be 0.20")
        _require(math.isclose(self.direction_norm_threshold, 1e-8,
                              rel_tol=0.0, abs_tol=0.0),
                 "v1 direction threshold must be 1e-8")


def load_policy(path: Path) -> DiscoveryPolicy:
    payload = json.loads(path.read_text(encoding="utf-8"))
    discovery = payload.get("discovery")
    _require(isinstance(discovery, dict), "config.discovery is required")
    _require(discovery.get("groupRepresentative") == "FROZEN_MEAN_NOT_UNIT_RENORMALIZED",
             "group representatives must be frozen, unnormalized means")
    _require(discovery.get("countPartiallySupplyingGroup") is True,
             "partial supplying groups must count")
    _require(discovery.get("skipEmptyGroupWithoutCounting") is True,
             "empty groups must not count")
    _require(discovery.get("refillFromEleventhSupplyingGroup") is False,
             "the eleventh supplying group must not refill a partial result")
    _require(discovery.get("fillFromIneligibleContentCandidates") is False,
             "ineligible content candidates must never fill discovery")
    _require(discovery.get("groupSimilarityMinimum") is None,
             "v1 must not discard groups because similarity is negative")
    _require(discovery.get("requireTopAndSubSupported") is True,
             "v1 discovery requires both top and sub support")
    policy = DiscoveryPolicy(
        parent_taste_count=discovery.get("parentTasteCount"),
        children_per_taste=discovery.get("childrenPerTaste"),
        maximum_supplying_groups=discovery.get("maximumSupplyingGroups"),
        maximum_candidates_per_group=discovery.get("maximumCandidatesPerGroup"),
        maximum_candidates=discovery.get("maximumCandidates"),
        maximum_viewed_per_group=discovery.get("maximumViewedPerGroup"),
        maximum_viewed_fraction=discovery.get("maximumViewedFraction"),
    )
    policy.validate()
    return policy


@dataclass(frozen=True)
class VersionPin:
    catalog_version: str
    geometry_version: str
    group_recipe_hash: str
    input_version: int

    def validate(self) -> None:
        _nonempty(self.catalog_version, "catalogVersion")
        _nonempty(self.geometry_version, "geometryVersion")
        _nonempty(self.group_recipe_hash, "groupRecipeHash")
        _require(isinstance(self.input_version, int) and not isinstance(self.input_version, bool)
                 and self.input_version >= 0, "inputVersion must be a non-negative integer")

    def geometry_key(self) -> tuple[str, str, str]:
        return self.catalog_version, self.geometry_version, self.group_recipe_hash


@dataclass(frozen=True)
class PopularityMovie:
    service_movie_id: int
    group_id: int
    group_mapped: bool
    catalog_eligible: bool
    tmdb_q: float | None = None
    tmdb_vote_count: int | None = None
    tmdb_rank_eligible: bool = False
    kobis_audience: int | None = None
    kobis_rank_eligible: bool = False


@dataclass(frozen=True)
class RankedMovie:
    service_movie_id: int
    group_id: int
    best_rank: int
    tmdb_rank: int | None
    kobis_rank: int | None


@dataclass(frozen=True)
class CurrentGroupRanks:
    version: VersionPin
    by_group: Mapping[int, tuple[RankedMovie, ...]]
    excluded_movie_ids_by_reason: Mapping[str, frozenset[int]]
    excluded_counts_by_reason: Mapping[str, int]
    excluded_union_count: int
    duplicate_row_count: int


@dataclass(frozen=True)
class WatchedMovie:
    service_movie_id: int
    group_id: int
    group_mapped: bool


@dataclass(frozen=True)
class WatchSnapshot:
    version: VersionPin
    movies: tuple[WatchedMovie, ...]


@dataclass(frozen=True)
class DirectionSnapshot:
    version: VersionPin
    raw_direction: Sequence[float]


@dataclass(frozen=True)
class GroupGeometry:
    catalog_version: str
    geometry_version: str
    group_recipe_hash: str
    means: np.ndarray
    ready_for_serving: bool
    service_id_mapping_applied: bool


@dataclass(frozen=True)
class CollectedCandidate:
    service_movie_id: int
    group_id: int
    group_similarity: float
    group_order: int
    within_group_order: int
    best_rank: int


@dataclass(frozen=True)
class DiscoveryCollection:
    status: str
    version: VersionPin
    total_mapped_viewed: int
    viewed_count_by_group: Mapping[int, int]
    ordered_eligible_group_ids: tuple[int, ...]
    supplying_group_ids: tuple[int, ...]
    candidates: tuple[CollectedCandidate, ...]
    excluded_counts_by_reason: Mapping[str, int]
    excluded_union_count: int
    duplicate_row_count: int


@dataclass(frozen=True)
class ScoredCandidate:
    service_movie_id: int
    score: float
    displayed_rating: float


def _validate_group_id(group_id: object, policy: DiscoveryPolicy) -> int:
    _require(isinstance(group_id, int) and not isinstance(group_id, bool),
             "groupId must be an integer")
    _require(0 <= group_id < policy.total_groups,
             f"groupId must be in [0,{policy.total_groups - 1}]")
    return int(group_id)


def _normalize_exclusions(exclusions: Mapping[str, Iterable[int]]) -> dict[str, set[int]]:
    _require(set(exclusions) == set(EXCLUSION_REASONS),
             "all and only v1 current exclusion reasons are required")
    normalized: dict[str, set[int]] = {}
    for reason in EXCLUSION_REASONS:
        values: set[int] = set()
        for movie_id in exclusions[reason]:
            values.add(_positive_int(movie_id, f"{reason} movie id"))
        normalized[reason] = values
    return normalized


def _validate_movie(movie: PopularityMovie, policy: DiscoveryPolicy) -> None:
    _positive_int(movie.service_movie_id, "serviceMovieId")
    _validate_group_id(movie.group_id, policy)
    _require(isinstance(movie.group_mapped, bool), "groupMapped must be boolean")
    _require(isinstance(movie.catalog_eligible, bool), "catalogEligible must be boolean")
    _require(isinstance(movie.tmdb_rank_eligible, bool), "tmdbRankEligible must be boolean")
    _require(isinstance(movie.kobis_rank_eligible, bool), "kobisRankEligible must be boolean")
    has_q = movie.tmdb_q is not None
    has_count = movie.tmdb_vote_count is not None
    _require(has_q == has_count, "TMDB Q and vote count must be present together")
    if has_q:
        _require(math.isfinite(float(movie.tmdb_q)), "TMDB Q must be finite")
        _require(isinstance(movie.tmdb_vote_count, int)
                 and not isinstance(movie.tmdb_vote_count, bool)
                 and movie.tmdb_vote_count >= 0, "TMDB vote count must be non-negative")
    if movie.tmdb_rank_eligible:
        _require(has_q and int(movie.tmdb_vote_count) >= 21,
                 "TMDB rank eligibility requires a valid Q and at least 21 votes")
    if movie.kobis_audience is not None:
        _require(isinstance(movie.kobis_audience, int)
                 and not isinstance(movie.kobis_audience, bool)
                 and movie.kobis_audience >= 0, "KOBIS audience must be non-negative")
    if movie.kobis_rank_eligible:
        _require(movie.kobis_audience is not None and movie.kobis_audience >= 10_000,
                 "KOBIS rank eligibility requires verified audience of at least 10000")


def build_current_group_rank_lists(
    movies: Iterable[PopularityMovie],
    exclusions: Mapping[str, Iterable[int]],
    version: VersionPin,
    policy: DiscoveryPolicy = DiscoveryPolicy(),
) -> CurrentGroupRanks:
    """Apply current exclusions, recount T/K ranks, and build full group lists."""
    policy.validate()
    version.validate()
    excluded = _normalize_exclusions(exclusions)
    excluded_union = set().union(*excluded.values())

    canonical: dict[int, PopularityMovie] = {}
    duplicate_rows = 0
    for movie in movies:
        _validate_movie(movie, policy)
        previous = canonical.get(movie.service_movie_id)
        if previous is None:
            canonical[movie.service_movie_id] = movie
        elif previous == movie:
            duplicate_rows += 1
        else:
            raise ValueError("one service movie has conflicting catalog or group rows")

    base = {
        movie_id: movie
        for movie_id, movie in canonical.items()
        if movie.catalog_eligible
        and movie.group_mapped
        and (movie.tmdb_rank_eligible or movie.kobis_rank_eligible)
    }
    excluded_counts = {
        reason: len(set(base).intersection(movie_ids))
        for reason, movie_ids in excluded.items()
    }
    current = [movie for movie_id, movie in base.items() if movie_id not in excluded_union]

    grouped: dict[int, list[PopularityMovie]] = {}
    for movie in current:
        grouped.setdefault(movie.group_id, []).append(movie)

    output: dict[int, tuple[RankedMovie, ...]] = {}
    for group_id, rows in grouped.items():
        tmdb_order = sorted(
            (row for row in rows if row.tmdb_rank_eligible),
            key=lambda row: (-float(row.tmdb_q), -int(row.tmdb_vote_count), row.service_movie_id),
        )
        kobis_order = sorted(
            (row for row in rows if row.kobis_rank_eligible),
            key=lambda row: (-int(row.kobis_audience), row.service_movie_id),
        )
        tmdb_rank = {row.service_movie_id: rank for rank, row in enumerate(tmdb_order, 1)}
        kobis_rank = {row.service_movie_id: rank for rank, row in enumerate(kobis_order, 1)}
        ids = set(tmdb_rank).union(kobis_rank)
        ranked = [
            RankedMovie(
                service_movie_id=movie_id,
                group_id=group_id,
                best_rank=min(tmdb_rank.get(movie_id, math.inf),
                              kobis_rank.get(movie_id, math.inf)),
                tmdb_rank=tmdb_rank.get(movie_id),
                kobis_rank=kobis_rank.get(movie_id),
            )
            for movie_id in ids
        ]
        ranked.sort(key=lambda row: (row.best_rank, row.service_movie_id))
        output[group_id] = tuple(ranked)

    _require(all(row.best_rank >= 1 for rows in output.values() for row in rows),
             "all best ranks must be positive")
    return CurrentGroupRanks(
        version=version,
        by_group=output,
        excluded_movie_ids_by_reason={
            reason: frozenset(values) for reason, values in excluded.items()
        },
        excluded_counts_by_reason=excluded_counts,
        excluded_union_count=len(set(base).intersection(excluded_union)),
        duplicate_row_count=duplicate_rows,
    )


def _mapped_watched_counts(
    snapshot: WatchSnapshot,
    policy: DiscoveryPolicy,
) -> tuple[int, dict[int, int]]:
    canonical: dict[int, WatchedMovie] = {}
    for movie in snapshot.movies:
        _positive_int(movie.service_movie_id, "watched serviceMovieId")
        _validate_group_id(movie.group_id, policy)
        _require(isinstance(movie.group_mapped, bool), "watched groupMapped must be boolean")
        previous = canonical.get(movie.service_movie_id)
        if previous is None:
            canonical[movie.service_movie_id] = movie
        elif previous != movie:
            raise ValueError("one watched movie has conflicting group rows")
    counts: dict[int, int] = {}
    total = 0
    for movie in canonical.values():
        if not movie.group_mapped:
            continue
        counts[movie.group_id] = counts.get(movie.group_id, 0) + 1
        total += 1
    return total, counts


def collect_discovery_10x10(
    direction: DirectionSnapshot,
    geometry: GroupGeometry,
    ranks: CurrentGroupRanks,
    watched: WatchSnapshot,
    policy: DiscoveryPolicy = DiscoveryPolicy(),
) -> DiscoveryCollection:
    """Collect candidates only; do not invoke or blend a prediction model."""
    policy.validate()
    direction.version.validate()
    ranks.version.validate()
    watched.version.validate()
    _require(direction.version == ranks.version == watched.version,
             "direction, ranks, and watched snapshots must have identical versions")
    expected_geometry = direction.version.geometry_key()
    actual_geometry = (
        _nonempty(geometry.catalog_version, "geometry catalogVersion"),
        _nonempty(geometry.geometry_version, "geometryVersion"),
        _nonempty(geometry.group_recipe_hash, "groupRecipeHash"),
    )
    _require(actual_geometry == expected_geometry, "geometry and input snapshot versions differ")
    _require(geometry.ready_for_serving is True, "group bundle is not ready for serving")
    _require(geometry.service_id_mapping_applied is True,
             "verified current service ID mapping is required")

    means = np.asarray(geometry.means)
    _require(means.dtype == np.float64, "group means must remain float64")
    _require(means.shape == (policy.total_groups, 131), "group means must have shape 1024 x 131")
    _require(bool(np.isfinite(means).all()), "group means must be finite")
    vector = np.asarray(direction.raw_direction, dtype=np.float64)
    _require(vector.shape == (131,), "raw direction must have 131 dimensions")
    _require(bool(np.isfinite(vector).all()), "raw direction must be finite")

    total_watched, viewed_counts = _mapped_watched_counts(watched, policy)
    watched_ids = {movie.service_movie_id for movie in watched.movies}
    _require(set(ranks.excluded_movie_ids_by_reason) == set(EXCLUSION_REASONS),
             "rank snapshot must preserve exactly the six v1 exclusion reasons")
    preserved_exclusions: dict[str, set[int]] = {}
    for reason in EXCLUSION_REASONS:
        preserved_exclusions[reason] = {
            _positive_int(movie_id, f"preserved {reason} movie id")
            for movie_id in ranks.excluded_movie_ids_by_reason[reason]
        }
    excluded_watched = ranks.excluded_movie_ids_by_reason.get("watched")
    _require(excluded_watched is not None and watched_ids.issubset(excluded_watched),
             "every confirmed watched movie must be in the watched exclusion snapshot")
    preserved_exclusion_union = set().union(*preserved_exclusions.values())

    all_ranked_ids: set[int] = set()
    for group_key, rows in ranks.by_group.items():
        _validate_group_id(group_key, policy)
        previous_sort_key: tuple[int, int] | None = None
        tmdb_ranks: list[int] = []
        kobis_ranks: list[int] = []
        for row in rows:
            _positive_int(row.service_movie_id, "ranked serviceMovieId")
            _require(row.group_id == group_key, "rank list contains a movie from another group")
            _require(isinstance(row.best_rank, int) and row.best_rank > 0,
                     "bestRank must be a positive integer")
            source_ranks = [rank for rank in (row.tmdb_rank, row.kobis_rank) if rank is not None]
            _require(bool(source_ranks), "ranked movie must have at least one source rank")
            _require(all(isinstance(rank, int) and not isinstance(rank, bool) and rank > 0
                         for rank in source_ranks),
                     "source ranks must be positive integers")
            _require(row.best_rank == min(source_ranks), "bestRank must be the minimum source rank")
            sort_key = row.best_rank, row.service_movie_id
            _require(previous_sort_key is None or previous_sort_key <= sort_key,
                     "rank list must be sorted by bestRank then serviceMovieId")
            previous_sort_key = sort_key
            _require(row.service_movie_id not in all_ranked_ids,
                     "one movie appears in more than one group rank list")
            _require(row.service_movie_id not in preserved_exclusion_union,
                     "rank list contains a movie from the preserved exclusion snapshot")
            all_ranked_ids.add(row.service_movie_id)
            if row.tmdb_rank is not None:
                tmdb_ranks.append(row.tmdb_rank)
            if row.kobis_rank is not None:
                kobis_ranks.append(row.kobis_rank)
        _require(sorted(tmdb_ranks) == list(range(1, len(tmdb_ranks) + 1)),
                 "TMDB ranks must be unique and contiguous from 1 within each group")
        _require(sorted(kobis_ranks) == list(range(1, len(kobis_ranks) + 1)),
                 "KOBIS ranks must be unique and contiguous from 1 within each group")

    scale = float(np.max(np.abs(vector)))
    if scale == 0.0:
        norm = 0.0
    else:
        norm = scale * float(np.linalg.norm(vector / scale))
    _require(math.isfinite(norm), "raw direction norm must be finite")
    if norm <= policy.direction_norm_threshold:
        return DiscoveryCollection(
            status="NO_CONTENT_DIRECTION",
            version=direction.version,
            total_mapped_viewed=total_watched,
            viewed_count_by_group=viewed_counts,
            ordered_eligible_group_ids=(),
            supplying_group_ids=(),
            candidates=(),
            excluded_counts_by_reason=ranks.excluded_counts_by_reason,
            excluded_union_count=ranks.excluded_union_count,
            duplicate_row_count=ranks.duplicate_row_count,
        )

    scores = np.sum(means * (vector / norm)[None, :], axis=1, dtype=np.float64)
    _require(bool(np.isfinite(scores).all()), "group similarities must be finite")
    ordered_groups = sorted(range(policy.total_groups), key=lambda group_id: (-scores[group_id], group_id))

    eligible_groups: list[int] = []
    supplying_groups: list[int] = []
    selected: list[CollectedCandidate] = []
    selected_ids: set[int] = set()
    for group_id in ordered_groups:
        viewed_in_group = viewed_counts.get(group_id, 0)
        if viewed_in_group > policy.maximum_viewed_per_group:
            continue
        if viewed_in_group / max(1, total_watched) > policy.maximum_viewed_fraction:
            continue
        available = ranks.by_group.get(group_id, ())
        if not available:
            continue
        eligible_groups.append(group_id)
        chosen = available[:policy.maximum_candidates_per_group]
        if not chosen:
            continue
        supplying_groups.append(group_id)
        for within_group_order, row in enumerate(chosen, 1):
            _require(row.group_id == group_id, "rank list contains a movie from another group")
            _require(row.service_movie_id not in selected_ids,
                     "one movie appears in more than one supplying group")
            selected_ids.add(row.service_movie_id)
            selected.append(CollectedCandidate(
                service_movie_id=row.service_movie_id,
                group_id=group_id,
                group_similarity=float(scores[group_id]),
                group_order=len(eligible_groups),
                within_group_order=within_group_order,
                best_rank=row.best_rank,
            ))
        if len(supplying_groups) == policy.maximum_supplying_groups:
            break

    _require(len(selected) <= policy.maximum_candidates, "discovery candidate cap exceeded")
    status = "READY_FOR_MODEL_RERANK" if selected else "DISCOVERY_EXHAUSTED"
    return DiscoveryCollection(
        status=status,
        version=direction.version,
        total_mapped_viewed=total_watched,
        viewed_count_by_group=viewed_counts,
        ordered_eligible_group_ids=tuple(eligible_groups),
        supplying_group_ids=tuple(supplying_groups),
        candidates=tuple(selected),
        excluded_counts_by_reason=ranks.excluded_counts_by_reason,
        excluded_union_count=ranks.excluded_union_count,
        duplicate_row_count=ranks.duplicate_row_count,
    )


def rerank_discovery_with_gbt(
    collection: DiscoveryCollection,
    calibrated_scores: Mapping[int, float],
    actual_rating_count: int,
    gbt_history_valid: bool,
) -> tuple[ScoredCandidate, ...]:
    """Rerank the exact collected set for the K>=10 GBT path."""
    _require(collection.status == "READY_FOR_MODEL_RERANK",
             "only a ready discovery collection can be reranked")
    _require(isinstance(actual_rating_count, int) and not isinstance(actual_rating_count, bool)
             and actual_rating_count >= 10, "GBT discovery rerank requires K >= 10")
    _require(gbt_history_valid is True, "GBT discovery rerank requires valid GBT history")
    _require(len(collection.candidates) ==
             len({row.service_movie_id for row in collection.candidates}),
             "collected discovery candidate IDs must be unique")
    candidate_ids = {row.service_movie_id for row in collection.candidates}
    _require(len(calibrated_scores) == len(candidate_ids) and set(calibrated_scores) == candidate_ids,
             "GBT scores must cover exactly the collected candidate set")
    values: list[ScoredCandidate] = []
    for movie_id, score in calibrated_scores.items():
        _positive_int(movie_id, "scored serviceMovieId")
        numeric = float(score)
        _require(math.isfinite(numeric), "GBT scores must be finite")
        values.append(ScoredCandidate(
            service_movie_id=movie_id,
            score=numeric,
            displayed_rating=min(5.0, max(0.5, numeric)),
        ))
    values.sort(key=lambda row: (-row.score, row.service_movie_id))
    _require({row.service_movie_id for row in values} == candidate_ids,
             "reranking changed the candidate set")
    return tuple(values)
