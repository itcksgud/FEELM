from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from discovery_10x10_reference_v1 import (  # noqa: E402
    DiscoveryPolicy,
    DirectionSnapshot,
    GroupGeometry,
    PopularityMovie,
    VersionPin,
    WatchedMovie,
    WatchSnapshot,
    build_current_group_rank_lists,
    collect_discovery_10x10,
    load_policy,
    rerank_discovery_with_gbt,
)


EMPTY_EXCLUSIONS = {
    "watched": set(),
    "rated": set(),
    "dismissed": set(),
    "filtered": set(),
    "exposed": set(),
    "reserved": set(),
}


class Discovery10x10Test(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DiscoveryPolicy()
        self.version = VersionPin("catalog-v1", "geometry-v1", "recipe-hash", 7)
        self.means = np.zeros((1024, 131), dtype=np.float64)
        for group_id in range(1024):
            self.means[group_id, 0] = 1024 - group_id

    def movie(self, movie_id: int, group_id: int, *, q: float | None = 7.0,
              votes: int | None = 100, tmdb_ok: bool | None = None,
              audience: int | None = None, kobis_ok: bool | None = None,
              mapped: bool = True, eligible: bool = True) -> PopularityMovie:
        if tmdb_ok is None:
            tmdb_ok = q is not None and votes is not None and votes >= 21
        if kobis_ok is None:
            kobis_ok = audience is not None and audience >= 10_000
        return PopularityMovie(
            movie_id, group_id, mapped, eligible, q, votes, tmdb_ok, audience, kobis_ok)

    def collect(self, movies, watched=(), direction_value=1.0, exclusions=None,
                version=None, geometry=None):
        version = version or self.version
        current_exclusions = {
            reason: set(values)
            for reason, values in (exclusions or EMPTY_EXCLUSIONS).items()
        }
        current_exclusions["watched"].update(movie.service_movie_id for movie in watched)
        ranks = build_current_group_rank_lists(
            movies, current_exclusions, version, self.policy)
        direction = np.zeros(131, dtype=np.float64)
        direction[0] = direction_value
        geometry = geometry or GroupGeometry(
            version.catalog_version,
            version.geometry_version,
            version.group_recipe_hash,
            self.means,
            True,
            True,
        )
        return collect_discovery_10x10(
            DirectionSnapshot(version, direction),
            geometry,
            ranks,
            WatchSnapshot(version, tuple(watched)),
            self.policy,
        )

    def test_repository_config_is_the_frozen_v1_policy(self) -> None:
        actual = load_policy(ROOT / "docs/recommendation/service-v1-20260913/config.v1.json")
        self.assertEqual(self.policy, actual)

    def test_p11_stops_at_ten_groups_and_ten_per_group(self) -> None:
        movies = [self.movie(group * 1000 + offset + 1, group)
                  for group in range(11) for offset in range(20)]
        result = self.collect(movies)
        self.assertEqual("READY_FOR_MODEL_RERANK", result.status)
        self.assertEqual(tuple(range(10)), result.supplying_group_ids)
        self.assertEqual(100, len(result.candidates))
        self.assertEqual({10}, {sum(row.group_id == group for row in result.candidates)
                                for group in range(10)})
        self.assertNotIn(10, {row.group_id for row in result.candidates})

    def test_p12_partial_group_counts_and_eleventh_does_not_refill(self) -> None:
        movies = [self.movie(offset + 1, 0) for offset in range(3)]
        movies += [self.movie(group * 1000 + offset + 1, group)
                   for group in range(1, 10) for offset in range(10)]
        movies += [self.movie(11000 + offset, 10) for offset in range(100)]
        result = self.collect(movies)
        self.assertEqual(93, len(result.candidates))
        self.assertEqual(tuple(range(10)), result.supplying_group_ids)
        self.assertNotIn(10, {row.group_id for row in result.candidates})

    def test_p13_empty_high_groups_do_not_count(self) -> None:
        movies = [self.movie(group * 1000 + 1, group) for group in range(12)]
        exclusions = {key: set(value) for key, value in EMPTY_EXCLUSIONS.items()}
        exclusions["exposed"] = {1, 1001}
        result = self.collect(movies, exclusions=exclusions)
        self.assertEqual(tuple(range(2, 12)), result.supplying_group_ids)
        self.assertEqual(10, len(result.candidates))

    def test_p14_viewed_fraction_and_count_both_apply(self) -> None:
        movies = [self.movie(group * 1000 + 900 + offset, group)
                  for group in range(3) for offset in range(2)]
        watched = [WatchedMovie(10_000 + index, 0 if index < 2 else 50 + index, True)
                   for index in range(10)]
        result = self.collect(movies, watched=watched)
        self.assertIn(0, result.supplying_group_ids)  # 2/10 passes

        watched_nine = watched[:9]
        result = self.collect(movies, watched=watched_nine)
        self.assertNotIn(0, result.supplying_group_ids)  # 2/9 fails fraction

        watched_hundred = [WatchedMovie(20_000 + index, 0 if index < 3 else 100 + index % 900, True)
                           for index in range(100)]
        result = self.collect(movies, watched=watched_hundred)
        self.assertNotIn(0, result.supplying_group_ids)  # 3/100 fails count

    def test_p18_zero_and_threshold_direction_do_not_invent_groups(self) -> None:
        movies = [self.movie(1, 0)]
        self.assertEqual("NO_CONTENT_DIRECTION", self.collect(movies, direction_value=0.0).status)
        self.assertEqual("NO_CONTENT_DIRECTION", self.collect(movies, direction_value=1e-8).status)
        self.assertEqual("READY_FOR_MODEL_RERANK",
                         self.collect(movies, direction_value=np.nextafter(1e-8, np.inf)).status)
        with self.assertRaisesRegex(ValueError, "finite"):
            self.collect(movies, direction_value=float("nan"))

    def test_g07_unmapped_watched_is_not_in_the_mapped_denominator(self) -> None:
        movies = [self.movie(1, 0)]
        watched = (
            WatchedMovie(100, 0, True),
            WatchedMovie(101, 1, False),
            WatchedMovie(101, 1, False),
        )
        result = self.collect(movies, watched=watched)
        self.assertEqual(1, result.total_mapped_viewed)
        self.assertEqual({0: 1}, result.viewed_count_by_group)

    def test_confirmed_watched_must_share_the_rank_exclusion_snapshot(self) -> None:
        movie = self.movie(1, 0)
        ranks = build_current_group_rank_lists([movie], EMPTY_EXCLUSIONS, self.version)
        direction = np.zeros(131, dtype=np.float64)
        direction[0] = 1.0
        with self.assertRaisesRegex(ValueError, "every confirmed watched"):
            collect_discovery_10x10(
                DirectionSnapshot(self.version, direction),
                GroupGeometry("catalog-v1", "geometry-v1", "recipe-hash",
                              self.means, True, True),
                ranks,
                WatchSnapshot(self.version, (WatchedMovie(1, 0, True),)),
                self.policy,
            )

    def test_source_gates_are_explicit_and_low_raw_values_do_not_rank(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 21"):
            build_current_group_rank_lists(
                [self.movie(1, 0, q=9.0, votes=1, tmdb_ok=True)],
                EMPTY_EXCLUSIONS, self.version)
        with self.assertRaisesRegex(ValueError, "at least 10000"):
            build_current_group_rank_lists(
                [self.movie(1, 0, q=None, votes=None, audience=1, kobis_ok=True)],
                EMPTY_EXCLUSIONS, self.version)
        ranks = build_current_group_rank_lists(
            [self.movie(1, 0, q=9.0, votes=1, tmdb_ok=False,
                        audience=1, kobis_ok=False)],
            EMPTY_EXCLUSIONS, self.version)
        self.assertEqual({}, ranks.by_group)

    def test_current_exclusions_apply_before_rank_cutoff(self) -> None:
        movies = [self.movie(movie_id, 0, q=100 - movie_id, votes=100)
                  for movie_id in range(1, 16)]
        exclusions = {key: set() for key in EMPTY_EXCLUSIONS}
        for reason, movie_id in zip(exclusions, range(1, 7)):
            exclusions[reason].add(movie_id)
        result = self.collect(movies, exclusions=exclusions)
        self.assertEqual(tuple(range(7, 16)), tuple(row.service_movie_id for row in result.candidates))
        self.assertEqual(6, result.excluded_union_count)
        self.assertEqual({reason: 1 for reason in exclusions}, result.excluded_counts_by_reason)

    def test_t_and_k_ranks_are_recounted_and_best_rank_uses_minimum(self) -> None:
        movies = [
            self.movie(1, 0, q=9.0, votes=100, audience=10_000),
            self.movie(2, 0, q=8.0, votes=200, audience=100_000),
            self.movie(3, 0, q=None, votes=None, audience=90_000),
        ]
        ranks = build_current_group_rank_lists(movies, EMPTY_EXCLUSIONS, self.version)
        rows = {row.service_movie_id: row for row in ranks.by_group[0]}
        self.assertEqual((1, 3), (rows[1].tmdb_rank, rows[1].kobis_rank))
        self.assertEqual((2, 1), (rows[2].tmdb_rank, rows[2].kobis_rank))
        self.assertEqual(1, rows[1].best_rank)
        self.assertEqual(1, rows[2].best_rank)
        self.assertEqual((1, 2, 3), tuple(row.service_movie_id for row in ranks.by_group[0]))

    def test_negative_group_similarity_is_kept_and_ties_use_group_id(self) -> None:
        means = np.zeros((1024, 131), dtype=np.float64)
        means[0, 0] = -2.0
        means[1, 0] = -1.0
        means[2, 0] = -1.0
        geometry = GroupGeometry("catalog-v1", "geometry-v1", "recipe-hash", means, True, True)
        result = self.collect([self.movie(1, 0), self.movie(2, 1), self.movie(3, 2)],
                              geometry=geometry)
        self.assertEqual((1, 2, 0), result.supplying_group_ids)

    def test_identical_catalog_rows_are_traced_but_conflicts_fail(self) -> None:
        movie = self.movie(1, 0)
        result = self.collect([movie, movie])
        self.assertEqual(1, result.duplicate_row_count)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.collect([movie, self.movie(1, 1)])

    def test_snapshot_or_serving_state_mismatch_fails_closed(self) -> None:
        movie = self.movie(1, 0)
        ranks = build_current_group_rank_lists([movie], EMPTY_EXCLUSIONS, self.version)
        vector = np.ones(131, dtype=np.float64)
        not_ready = GroupGeometry("catalog-v1", "geometry-v1", "recipe-hash",
                                  self.means, False, False)
        with self.assertRaisesRegex(ValueError, "not ready"):
            collect_discovery_10x10(
                DirectionSnapshot(self.version, vector), not_ready, ranks,
                WatchSnapshot(self.version, ()), self.policy)
        other = VersionPin("catalog-v1", "geometry-v1", "recipe-hash", 8)
        with self.assertRaisesRegex(ValueError, "identical versions"):
            collect_discovery_10x10(
                DirectionSnapshot(other, vector),
                GroupGeometry("catalog-v1", "geometry-v1", "recipe-hash",
                              self.means, True, True),
                ranks,
                WatchSnapshot(self.version, ()),
                self.policy,
            )

    def test_corrupt_rank_order_and_extreme_direction_fail_closed(self) -> None:
        ranks = build_current_group_rank_lists(
            [self.movie(1, 0, q=9.0), self.movie(2, 0, q=8.0)],
            EMPTY_EXCLUSIONS,
            self.version,
        )
        corrupt = type(ranks)(
            ranks.version,
            {0: tuple(reversed(ranks.by_group[0]))},
            ranks.excluded_movie_ids_by_reason,
            ranks.excluded_counts_by_reason,
            ranks.excluded_union_count,
            ranks.duplicate_row_count,
        )
        direction = np.zeros(131, dtype=np.float64)
        direction[0] = 1.0
        geometry = GroupGeometry("catalog-v1", "geometry-v1", "recipe-hash",
                                 self.means, True, True)
        with self.assertRaisesRegex(ValueError, "sorted"):
            collect_discovery_10x10(
                DirectionSnapshot(self.version, direction), geometry, corrupt,
                WatchSnapshot(self.version, ()), self.policy)
        huge = direction.copy()
        huge[0] = 1e308
        result = collect_discovery_10x10(
            DirectionSnapshot(self.version, huge), geometry, ranks,
            WatchSnapshot(self.version, ()), self.policy)
        self.assertEqual("READY_FOR_MODEL_RERANK", result.status)

    def test_corrupt_preserved_exclusion_or_non_contiguous_rank_fails(self) -> None:
        ranks = build_current_group_rank_lists([self.movie(1, 0)], EMPTY_EXCLUSIONS, self.version)
        exclusions = dict(ranks.excluded_movie_ids_by_reason)
        exclusions["dismissed"] = frozenset({1})
        corrupt_exclusion = type(ranks)(
            ranks.version, ranks.by_group, exclusions, ranks.excluded_counts_by_reason,
            ranks.excluded_union_count, ranks.duplicate_row_count)
        direction = np.zeros(131, dtype=np.float64)
        direction[0] = 1.0
        geometry = GroupGeometry("catalog-v1", "geometry-v1", "recipe-hash",
                                 self.means, True, True)
        with self.assertRaisesRegex(ValueError, "preserved exclusion"):
            collect_discovery_10x10(
                DirectionSnapshot(self.version, direction), geometry, corrupt_exclusion,
                WatchSnapshot(self.version, ()), self.policy)

        row = ranks.by_group[0][0]
        gap_row = type(row)(row.service_movie_id, row.group_id, 2, 2, None)
        corrupt_rank = type(ranks)(
            ranks.version, {0: (gap_row,)}, ranks.excluded_movie_ids_by_reason,
            ranks.excluded_counts_by_reason, ranks.excluded_union_count,
            ranks.duplicate_row_count)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            collect_discovery_10x10(
                DirectionSnapshot(self.version, direction), geometry, corrupt_rank,
                WatchSnapshot(self.version, ()), self.policy)

    def test_empty_candidate_set_has_explicit_exhausted_status(self) -> None:
        result = self.collect([self.movie(1, 0, mapped=False)])
        self.assertEqual("DISCOVERY_EXHAUSTED", result.status)
        self.assertEqual((), result.candidates)

    def test_gbt_rerank_preserves_set_uses_raw_score_and_requires_k10(self) -> None:
        result = self.collect([self.movie(1, 0), self.movie(2, 0), self.movie(3, 0)])
        scored = rerank_discovery_with_gbt(result, {1: 5.5, 2: 4.0, 3: 5.5}, 10, True)
        self.assertEqual((1, 3, 2), tuple(row.service_movie_id for row in scored))
        self.assertEqual((5.0, 5.0, 4.0), tuple(row.displayed_rating for row in scored))
        self.assertEqual({1, 2, 3}, {row.service_movie_id for row in scored})
        with self.assertRaisesRegex(ValueError, "K >= 10"):
            rerank_discovery_with_gbt(result, {1: 5.5, 2: 4.0, 3: 5.5}, 9, True)
        with self.assertRaisesRegex(ValueError, "exactly"):
            rerank_discovery_with_gbt(result, {1: 5.5, 2: 4.0}, 10, True)
        with self.assertRaisesRegex(ValueError, "valid GBT history"):
            rerank_discovery_with_gbt(result, {1: 5.5, 2: 4.0, 3: 5.5}, 10, False)

    def test_cross_group_duplicate_in_corrupt_rank_snapshot_fails(self) -> None:
        movies = [self.movie(1, 0), self.movie(2, 1)]
        ranks = build_current_group_rank_lists(movies, EMPTY_EXCLUSIONS, self.version)
        corrupt = type(ranks)(
            ranks.version,
            {0: ranks.by_group[0], 1: (type(ranks.by_group[0][0])(1, 1, 1, 1, None),)},
            ranks.excluded_movie_ids_by_reason,
            ranks.excluded_counts_by_reason,
            ranks.excluded_union_count,
            ranks.duplicate_row_count,
        )
        direction = np.zeros(131, dtype=np.float64)
        direction[0] = 1.0
        with self.assertRaisesRegex(ValueError, "more than one"):
            collect_discovery_10x10(
                DirectionSnapshot(self.version, direction),
                GroupGeometry("catalog-v1", "geometry-v1", "recipe-hash",
                              self.means, True, True),
                corrupt,
                WatchSnapshot(self.version, ()),
                self.policy,
            )


if __name__ == "__main__":
    unittest.main()
