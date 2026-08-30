#!/usr/bin/env python3
"""Create a reproducible local profile of the MovieLens 32M archive."""

from __future__ import annotations

import argparse
import json
import statistics
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tmdb_coverage_audit import csv_rows, zip_member


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    index = min(len(values) - 1, int((len(values) - 1) * fraction))
    return values[index]


def rating_bucket(count: int) -> str:
    if count == 0:
        return "0"
    if count < 10:
        return "1-9"
    if count < 100:
        return "10-99"
    if count < 1_000:
        return "100-999"
    if count < 10_000:
        return "1000-9999"
    return "10000+"


def utc_timestamp(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def main() -> int:
    args = parse_args()
    movie_ids: set[int] = set()
    genre_counts: Counter[str] = Counter()
    tmdb_mapped = 0
    imdb_mapped = 0
    rating_counts: Counter[int] = Counter()
    rating_values: Counter[str] = Counter()
    rating_users: set[int] = set()
    rating_rows = 0
    earliest_rating: int | None = None
    latest_rating: int | None = None
    tag_users: Counter[int] = Counter()
    tagged_movies: Counter[int] = Counter()
    normalized_tags: Counter[str] = Counter()
    blank_tags = 0
    tag_rows = 0

    with zipfile.ZipFile(args.archive) as archive:
        members = archive.namelist()
        for row in csv_rows(archive, zip_member(archive, "movies.csv")):
            movie_ids.add(int(row["movieId"]))
            genre_counts.update(row["genres"].split("|"))

        for row in csv_rows(archive, zip_member(archive, "links.csv")):
            tmdb_mapped += bool(row.get("tmdbId", "").strip())
            imdb_mapped += bool(row.get("imdbId", "").strip())

        for row in csv_rows(archive, zip_member(archive, "ratings.csv")):
            movie_id = int(row["movieId"])
            timestamp = int(row["timestamp"])
            rating_rows += 1
            rating_users.add(int(row["userId"]))
            rating_counts[movie_id] += 1
            rating_values[row["rating"]] += 1
            earliest_rating = timestamp if earliest_rating is None else min(earliest_rating, timestamp)
            latest_rating = timestamp if latest_rating is None else max(latest_rating, timestamp)

        for row in csv_rows(archive, zip_member(archive, "tags.csv")):
            movie_id = int(row["movieId"])
            user_id = int(row["userId"])
            tag = row["tag"].strip().casefold()
            tag_rows += 1
            tag_users[user_id] += 1
            tagged_movies[movie_id] += 1
            if tag:
                normalized_tags[tag] += 1
            else:
                blank_tags += 1

    movie_rating_values = sorted(rating_counts.get(movie_id, 0) for movie_id in movie_ids)
    tag_movie_values = sorted(tagged_movies.values())
    tag_user_values = sorted(tag_users.values())
    tag_user_values_desc = sorted(tag_user_values, reverse=True)
    top_1_count = max(1, len(tag_user_values_desc) // 100)
    top_10_count = max(1, len(tag_user_values_desc) // 10)

    profile = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_archive": str(args.archive),
        "archive_members": members,
        "tag_genome_in_archive": any(
            name.endswith("/genome-scores.csv") or name.endswith("/genome-tags.csv")
            for name in members
        ),
        "movies": {
            "count": len(movie_ids),
            "tmdb_mapped": tmdb_mapped,
            "tmdb_unmapped": len(movie_ids) - tmdb_mapped,
            "imdb_mapped": imdb_mapped,
            "no_genres_listed": genre_counts["(no genres listed)"],
            "genre_counts": dict(genre_counts.most_common()),
        },
        "ratings": {
            "rows": rating_rows,
            "users": len(rating_users),
            "earliest_utc": utc_timestamp(earliest_rating or 0),
            "latest_utc": utc_timestamp(latest_rating or 0),
            "value_counts": dict(sorted(rating_values.items(), key=lambda item: float(item[0]))),
            "movies_by_rating_count": dict(
                Counter(rating_bucket(value) for value in movie_rating_values)
            ),
            "ratings_per_movie": {
                "median": statistics.median(movie_rating_values),
                "p90": percentile(movie_rating_values, 0.90),
                "p99": percentile(movie_rating_values, 0.99),
                "max": max(movie_rating_values),
            },
        },
        "free_text_tags": {
            "rows": tag_rows,
            "users": len(tag_users),
            "tagging_user_rate": round(len(tag_users) / len(rating_users), 4),
            "tagged_movies": len(tagged_movies),
            "tagged_movie_rate": round(len(tagged_movies) / len(movie_ids), 4),
            "distinct_normalized_tags": len(normalized_tags),
            "blank_tags": blank_tags,
            "top_tags": normalized_tags.most_common(25),
            "tags_per_tagged_movie": {
                "median": statistics.median(tag_movie_values),
                "p90": percentile(tag_movie_values, 0.90),
                "p99": percentile(tag_movie_values, 0.99),
                "max": max(tag_movie_values),
            },
            "tags_per_tagging_user": {
                "median": statistics.median(tag_user_values),
                "p90": percentile(tag_user_values, 0.90),
                "p99": percentile(tag_user_values, 0.99),
                "max": max(tag_user_values),
            },
            "top_1pct_user_share": round(
                sum(tag_user_values_desc[:top_1_count]) / tag_rows, 4
            ),
            "top_10pct_user_share": round(
                sum(tag_user_values_desc[:top_10_count]) / tag_rows, 4
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"MovieLens profile written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
