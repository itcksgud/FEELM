from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from catalog_recommendation_capability_audit import (  # noqa: E402
    UNKNOWN_NOT_AUDITED,
    build_row,
    era_band,
    group_catalog,
    popularity_band,
    popularity_segment,
    summarize,
    valid_genres,
)


def movie(
    movie_id: int,
    *,
    tmdb_id: int | None,
    rating_count: int,
    genres: str = "Drama",
    title: str | None = None,
    year: int | None = 2020,
) -> dict[str, object]:
    return {
        "movie_id": movie_id,
        "title": title or f"Movie {movie_id} ({year})",
        "release_year": year,
        "genres": genres,
        "tmdb_id": tmdb_id,
        "rating_count": rating_count,
    }


class CatalogRecommendationCapabilityAuditTest(unittest.TestCase):
    def test_popularity_boundaries_do_not_treat_unlinked_as_zero(self) -> None:
        self.assertEqual(popularity_band(0, False), "NO_MOVIELENS_LINK")
        self.assertEqual(popularity_band(0, True), "ZERO")
        self.assertEqual(popularity_band(9, True), "R1_9")
        self.assertEqual(popularity_band(10, True), "R10_99")
        self.assertEqual(popularity_band(1_000, True), "R1000_9999")
        self.assertEqual(popularity_band(10_000, True), "R10000_PLUS")
        self.assertEqual(popularity_segment(999, True), "TAIL_R1_999")
        self.assertEqual(popularity_segment(1_000, True), "POPULAR_R1000_PLUS")

    def test_era_boundary_is_explicit(self) -> None:
        self.assertEqual(era_band(None, 2015), "UNKNOWN_YEAR")
        self.assertEqual(era_band(2014, 2015), "OLD_PRE_2015")
        self.assertEqual(era_band(2015, 2015), "NEW_2015_PLUS")

    def test_genres_remove_missing_marker_and_deduplicate(self) -> None:
        self.assertEqual(
            valid_genres(["Drama|Comedy", "Drama|(no genres listed)"]),
            ["Comedy", "Drama"],
        )

    def test_catalog_grain_collapses_duplicate_tmdb_links_and_adds_korean_only(self) -> None:
        movies = {
            1: movie(1, tmdb_id=100, rating_count=7),
            2: movie(2, tmdb_id=100, rating_count=3),
            3: movie(3, tmdb_id=None, rating_count=0),
        }
        groups = group_catalog(movies, {100, 200})
        self.assertEqual(set(groups), {"tmdb:100", "tmdb:200", "ml:3"})
        self.assertEqual(len(groups["tmdb:100"]["movielens_items"]), 2)
        self.assertEqual(groups["tmdb:200"]["movielens_items"], [])

    def test_uncollected_tmdb_fields_remain_unknown_not_false(self) -> None:
        movies = {1: movie(1, tmdb_id=100, rating_count=10)}
        group = group_catalog(movies, {100})["tmdb:100"]
        row = build_row(group, {100}, {}, {}, None, 2015)
        self.assertEqual(row["overview_ko_presence"], UNKNOWN_NOT_AUDITED)
        self.assertEqual(row["keywords_presence"], UNKNOWN_NOT_AUDITED)
        self.assertEqual(row["embedding_input_status"], "UNVERIFIED_TEXT_METADATA_NOT_COLLECTED")
        self.assertEqual(row["content_only_status"], "LIMITED_GENRE_ONLY")
        self.assertEqual(row["capability_zone"], "ALS_ELIGIBLE_GENRE_ONLY")

    def test_korean_only_item_requires_fallback_until_metadata_is_collected(self) -> None:
        groups = group_catalog({}, {200})
        row = build_row(groups["tmdb:200"], {200}, {}, {}, None, 2015)
        self.assertEqual(row["origin_group"], "KOREAN_PROXY")
        self.assertEqual(row["popularity_band"], "NO_MOVIELENS_LINK")
        self.assertEqual(row["als_factor_eligibility"], "INELIGIBLE_NO_MOVIELENS_LINK")
        self.assertEqual(row["content_only_status"], "UNVERIFIED_METADATA_NOT_COLLECTED")
        self.assertEqual(row["capability_zone"], "FALLBACK_REQUIRED_CONTENT_UNRESOLVED")

    def test_market_cache_empty_is_observed_not_unknown(self) -> None:
        movies = {1: movie(1, tmdb_id=100, rating_count=1)}
        group = group_catalog(movies, set())["tmdb:100"]
        market = {
            100: {
                "status": "OK",
                "production_countries": ["US"],
                "has_current_korean_provider": False,
                "has_current_korean_flatrate": False,
                "korean_provider_names": [],
            }
        }
        row = build_row(group, set(), {}, market, None, 2015)
        self.assertEqual(row["origin_group"], "FOREIGN_OBSERVED")
        self.assertEqual(row["current_kr_provider_status"], "OBSERVED_EMPTY")
        self.assertEqual(row["current_kr_flatrate_status"], "OBSERVED_EMPTY")

    def test_summary_preserves_catalog_and_rating_totals(self) -> None:
        movies = {
            1: movie(1, tmdb_id=100, rating_count=7),
            2: movie(2, tmdb_id=100, rating_count=3),
            3: movie(3, tmdb_id=None, rating_count=0, genres="(no genres listed)"),
        }
        groups = group_catalog(movies, {100, 200})
        rows = [
            build_row(groups[key], {100, 200}, {}, {}, None, 2015)
            for key in sorted(groups)
        ]
        result = summarize(rows, movies, {100, 200}, {}, None, 2015)
        self.assertEqual(result["totals"]["catalog_rows"], 3)
        self.assertEqual(result["totals"]["rating_rows_represented"], 10)
        self.assertEqual(result["totals"]["duplicate_movielens_links_collapsed"], 1)
        self.assertEqual(result["totals"]["korean_proxy_only_rows"], 1)


if __name__ == "__main__":
    unittest.main()
