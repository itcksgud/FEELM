#!/usr/bin/env python3
"""Audit whether KMRD-5M can represent FEELM's target users.

This is a descriptive feasibility audit, not a recommendation-quality result.
KMRD users are anonymous Naver Movie raters; they are not verified FEELM users,
Korean residents, or people in their twenties.
The dataset may be used for pipeline diagnostics, but its preselected high-activity
users do not represent FEELM's expected new-community cold start.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ORIGINAL_YEAR = re.compile(r"(?:^|,\s*)(\d{4})\s*$")
MOVIELENS_YEAR = re.compile(r"\((\d{4})\)\s*$")
ALT_TITLE = re.compile(r"\(([^()]*)\)")
TITLE_SPLIT = re.compile(r"\s+,\s+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kmrd-root", type=Path, required=True)
    parser.add_argument("--movielens-movies", type=Path, required=True)
    parser.add_argument("--movielens-links", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def zip_dict_reader(archive: zipfile.ZipFile, member: str, delimiter: str = ","):
    binary = archive.open(member)
    text = io.TextIOWrapper(binary, encoding="utf-8", newline="")
    try:
        yield from csv.DictReader(text, delimiter=delimiter)
    finally:
        text.close()


def parse_original_year(title_eng: str) -> int | None:
    match = ORIGINAL_YEAR.search(title_eng.strip())
    return int(match.group(1)) if match else None


def parse_optional_year(value: str) -> int | None:
    match = re.search(r"\b(18|19|20)\d{2}\b", value.strip())
    return int(match.group(0)) if match else None


def english_title_candidates(title_eng: str) -> list[str]:
    text = title_eng.strip()
    match = ORIGINAL_YEAR.search(text)
    if match:
        text = text[: match.start()].rstrip(" ,")
    candidates = [part.strip() for part in TITLE_SPLIT.split(text) if part.strip()]
    if text and len(candidates) <= 1:
        candidates.append(text)
    return list(dict.fromkeys(candidates))


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"\b(the|a|an)\b", " ", text)
    return "".join(char for char in text if char.isalnum())


def movielens_title_candidates(title: str) -> tuple[list[str], int | None]:
    match = MOVIELENS_YEAR.search(title)
    year = int(match.group(1)) if match else None
    base = title[: match.start()].strip() if match else title.strip()
    candidates = [base]
    candidates.extend(match.group(1).strip() for match in ALT_TITLE.finditer(base))
    without_parentheses = ALT_TITLE.sub(" ", base).strip()
    if without_parentheses:
        candidates.append(without_parentheses)
    return list(dict.fromkeys(value for value in candidates if value)), year


def quantiles(values: Iterable[float], points: tuple[float, ...]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {f"p{int(point * 100):02d}": None for point in points}
    result: dict[str, float | int | None] = {}
    for point in points:
        index = max(0, math.ceil(point * len(ordered)) - 1)
        value = ordered[index]
        result[f"p{int(point * 100):02d}"] = round(value, 6) if isinstance(value, float) else value
    return result


def gini(values: Iterable[int]) -> float:
    ordered = sorted(value for value in values if value >= 0)
    total = sum(ordered)
    if not ordered or total == 0:
        return 0.0
    weighted = sum((index + 1) * value for index, value in enumerate(ordered))
    return round((2 * weighted) / (len(ordered) * total) - (len(ordered) + 1) / len(ordered), 6)


def concentration(values: Iterable[int], shares: tuple[float, ...] = (0.01, 0.05, 0.10)) -> dict[str, float]:
    ordered = sorted(values, reverse=True)
    total = sum(ordered)
    result: dict[str, float] = {}
    for share in shares:
        count = max(1, math.ceil(len(ordered) * share)) if ordered else 0
        result[f"top_{int(share * 100)}pct_items_interaction_share"] = (
            round(sum(ordered[:count]) / total, 6) if total else 0.0
        )
    return result


def load_metadata(meta_zip: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    movies: dict[int, dict[str, Any]] = {}
    with zipfile.ZipFile(meta_zip) as archive:
        for row in zip_dict_reader(archive, "movies.txt", delimiter="\t"):
            movie_id = int(row["movie"])
            release_text = (row.get("year") or "").strip()
            movies[movie_id] = {
                "movie_id": movie_id,
                "title_ko": (row.get("title") or "").strip(),
                "title_eng": (row.get("title_eng") or "").strip(),
                "original_year": parse_original_year(row.get("title_eng") or ""),
                "korean_release_year": parse_optional_year(release_text),
                "grade": (row.get("grade") or "").strip(),
                "countries": set(),
                "genres": set(),
                "cast_count": 0,
                "leading_cast_count": 0,
            }

        unknown_references: Counter[str] = Counter()
        for row in zip_dict_reader(archive, "countries.csv"):
            movie_id = int(row["movie"])
            if movie_id in movies:
                movies[movie_id]["countries"].add(row["country"].strip())
            else:
                unknown_references["countries"] += 1
        for row in zip_dict_reader(archive, "genres.csv"):
            movie_id = int(row["movie"])
            if movie_id in movies:
                movies[movie_id]["genres"].add(row["genre"].strip())
            else:
                unknown_references["genres"] += 1
        casting_rows = 0
        for row in zip_dict_reader(archive, "castings.csv"):
            casting_rows += 1
            movie_id = int(row["movie"])
            if movie_id in movies:
                movies[movie_id]["cast_count"] += 1
                if str(row.get("leading") or "").strip() == "1":
                    movies[movie_id]["leading_cast_count"] += 1
            else:
                unknown_references["castings"] += 1

    country_counts = Counter(country for movie in movies.values() for country in movie["countries"])
    genre_counts = Counter(genre for movie in movies.values() for genre in movie["genres"])
    return movies, {
        "movie_rows": len(movies),
        "casting_rows": casting_rows,
        "country_values": len(country_counts),
        "genre_values": len(genre_counts),
        "top_countries": country_counts.most_common(15),
        "top_genres": genre_counts.most_common(30),
        "unknown_movie_references": dict(unknown_references),
    }


def metadata_coverage(movies: dict[int, dict[str, Any]], item_ids: set[int]) -> dict[str, Any]:
    selected = [movies[item_id] for item_id in item_ids if item_id in movies]
    total = len(item_ids)

    def covered(predicate) -> int:
        return sum(bool(predicate(movie)) for movie in selected)

    fields = {
        "metadata_row": len(selected),
        "korean_title": covered(lambda item: item["title_ko"]),
        "english_title": covered(lambda item: item["title_eng"]),
        "original_year_parsed": covered(lambda item: item["original_year"] is not None),
        "korean_release_year": covered(lambda item: item["korean_release_year"] is not None),
        "country": covered(lambda item: item["countries"]),
        "genre": covered(lambda item: item["genres"]),
        "cast": covered(lambda item: item["cast_count"] > 0),
        "leading_cast": covered(lambda item: item["leading_cast_count"] > 0),
        "grade": covered(lambda item: item["grade"]),
    }
    return {
        "items": total,
        "counts": fields,
        "shares": {key: round(value / total, 6) if total else 0.0 for key, value in fields.items()},
        "not_in_metadata": total - len(selected),
        "unsupported_fields": ["overview", "keywords", "director", "tmdb_id", "provider"],
    }


def finalize_pair(
    state: dict[str, Any],
    movies: dict[int, dict[str, Any]],
    item_unique: Counter[int],
    user_unique: Counter[int],
    user_korean_unique: Counter[int],
    dedup_rating_distribution: Counter[int],
    dedup_year_distribution: Counter[int],
    latest_event: dict[int, tuple[int, int, bool]],
    latest_time_count: Counter[int],
    duplicate_stats: Counter[str],
) -> None:
    if state.get("user") is None:
        return
    user = int(state["user"])
    movie = int(state["movie"])
    count = int(state["count"])
    latest_time = int(state["latest_time"])
    latest_rating = int(state["latest_rating"])
    is_korean = "한국" in movies.get(movie, {}).get("countries", set())

    item_unique[movie] += 1
    user_unique[user] += 1
    if is_korean:
        user_korean_unique[user] += 1
    dedup_rating_distribution[latest_rating] += 1
    dedup_year_distribution[datetime.fromtimestamp(latest_time, tz=timezone.utc).year] += 1

    previous = latest_event.get(user)
    if previous is None or latest_time > previous[0]:
        latest_event[user] = (latest_time, movie, is_korean)
        latest_time_count[user] = 1
    elif latest_time == previous[0]:
        latest_time_count[user] += 1
        if movie > previous[1]:
            latest_event[user] = (latest_time, movie, is_korean)

    if count > 1:
        duplicate_stats["duplicate_user_item_pairs"] += 1
        duplicate_stats["duplicate_extra_rows"] += count - 1
        if len(state["ratings"]) > 1:
            duplicate_stats["conflicting_rating_pairs"] += 1
        if len(state["times"]) > 1:
            duplicate_stats["multiple_timestamp_pairs"] += 1


def audit_ratings(rates_zip: Path, movies: dict[int, dict[str, Any]]) -> dict[str, Any]:
    raw_item: Counter[int] = Counter()
    raw_user: Counter[int] = Counter()
    item_unique: Counter[int] = Counter()
    user_unique: Counter[int] = Counter()
    user_korean_unique: Counter[int] = Counter()
    raw_rating_distribution: Counter[int] = Counter()
    dedup_rating_distribution: Counter[int] = Counter()
    raw_year_distribution: Counter[int] = Counter()
    dedup_year_distribution: Counter[int] = Counter()
    latest_event: dict[int, tuple[int, int, bool]] = {}
    latest_time_count: Counter[int] = Counter()
    duplicate_stats: Counter[str] = Counter()
    unknown_metadata_rows = 0
    raw_korean_rows = 0
    min_timestamp: int | None = None
    max_timestamp: int | None = None
    row_count = 0
    previous_order: tuple[int, int] | None = None
    state: dict[str, Any] = {"user": None}

    with zipfile.ZipFile(rates_zip) as archive:
        member = archive.namelist()[0]
        for row in zip_dict_reader(archive, member):
            user = int(row["user"])
            movie = int(row["movie"])
            rating = int(row["rate"])
            timestamp = int(row["time"])
            order = (user, movie)
            if previous_order is not None and order < previous_order:
                raise RuntimeError("KMRD ratings are not ordered by (user, movie); adjacent dedupe is unsafe")
            previous_order = order

            if state.get("user") != user or state.get("movie") != movie:
                finalize_pair(
                    state,
                    movies,
                    item_unique,
                    user_unique,
                    user_korean_unique,
                    dedup_rating_distribution,
                    dedup_year_distribution,
                    latest_event,
                    latest_time_count,
                    duplicate_stats,
                )
                state = {
                    "user": user,
                    "movie": movie,
                    "count": 0,
                    "latest_time": timestamp,
                    "latest_rating": rating,
                    "ratings": set(),
                    "times": set(),
                }

            state["count"] += 1
            state["ratings"].add(rating)
            state["times"].add(timestamp)
            if timestamp >= state["latest_time"]:
                state["latest_time"] = timestamp
                state["latest_rating"] = rating

            row_count += 1
            raw_item[movie] += 1
            raw_user[user] += 1
            raw_rating_distribution[rating] += 1
            raw_year_distribution[datetime.fromtimestamp(timestamp, tz=timezone.utc).year] += 1
            min_timestamp = timestamp if min_timestamp is None else min(min_timestamp, timestamp)
            max_timestamp = timestamp if max_timestamp is None else max(max_timestamp, timestamp)
            if movie not in movies:
                unknown_metadata_rows += 1
            elif "한국" in movies[movie]["countries"]:
                raw_korean_rows += 1

    finalize_pair(
        state,
        movies,
        item_unique,
        user_unique,
        user_korean_unique,
        dedup_rating_distribution,
        dedup_year_distribution,
        latest_event,
        latest_time_count,
        duplicate_stats,
    )

    users = set(raw_user)
    rated_items = set(raw_item)
    korean_rated_items = {item for item in rated_items if "한국" in movies.get(item, {}).get("countries", set())}
    unique_pairs = sum(item_unique.values())
    korean_unique_pairs = sum(user_korean_unique.values())
    per_user_korean_share = [
        user_korean_unique[user] / user_unique[user] for user in users if user_unique[user]
    ]

    onboarding = {}
    for k in (0, 1, 3, 5, 10, 20):
        eligible = sum(user_unique[user] >= k + 1 for user in users)
        latest_korean = sum(
            user_unique[user] >= k + 1 and latest_event[user][2] for user in users
        )
        latest_korean_strict = sum(
            user_unique[user] >= k + 1
            and latest_event[user][2]
            and latest_time_count[user] == 1
            for user in users
        )
        onboarding[f"K{k}"] = {
            "users_with_at_least_k_plus_one_unique_items": eligible,
            "share": round(eligible / len(users), 6),
            "latest_item_korean_target_users": latest_korean,
            "strict_unique_latest_time_korean_target_users": latest_korean_strict,
        }

    top_items = []
    for movie_id, count in item_unique.most_common(20):
        metadata = movies.get(movie_id, {})
        top_items.append(
            {
                "movie_id": movie_id,
                "title_ko": metadata.get("title_ko"),
                "original_year": metadata.get("original_year"),
                "countries": sorted(metadata.get("countries", [])),
                "unique_user_item_pairs": count,
            }
        )

    top_korean_items = []
    for movie_id in sorted(korean_rated_items, key=lambda item: (-item_unique[item], item))[:20]:
        metadata = movies[movie_id]
        top_korean_items.append(
            {
                "movie_id": movie_id,
                "title_ko": metadata["title_ko"],
                "original_year": metadata["original_year"],
                "unique_user_item_pairs": item_unique[movie_id],
            }
        )

    item_bands = Counter()
    korean_item_bands = Counter()
    for movie_id, count in item_unique.items():
        if count < 10:
            band = "U1_9"
        elif count < 100:
            band = "U10_99"
        elif count < 1_000:
            band = "U100_999"
        elif count < 10_000:
            band = "U1000_9999"
        else:
            band = "U10000_PLUS"
        item_bands[band] += 1
        if movie_id in korean_rated_items:
            korean_item_bands[band] += 1

    tied_latest_users = sum(latest_time_count[user] > 1 for user in users)
    latest_timestamp = max_timestamp or 0
    earliest_timestamp = min_timestamp or 0
    return {
        "raw": {
            "rating_rows": row_count,
            "users": len(users),
            "rated_items": len(rated_items),
            "unknown_metadata_rows": unknown_metadata_rows,
            "korean_rating_rows": raw_korean_rows,
            "korean_rating_share": round(raw_korean_rows / row_count, 6),
        },
        "deduplicated_latest": {
            "user_item_pairs": unique_pairs,
            "removed_duplicate_rows": row_count - unique_pairs,
            "rated_items": len(item_unique),
            "korean_rated_items": len(korean_rated_items),
            "korean_user_item_pairs": korean_unique_pairs,
            "korean_pair_share": round(korean_unique_pairs / unique_pairs, 6),
        },
        "duplicates": {
            **dict(duplicate_stats),
            "duplicate_row_share": round((row_count - unique_pairs) / row_count, 6),
            "dedupe_rule": "latest timestamp; final row wins on timestamp tie",
        },
        "timestamps": {
            "earliest_utc": datetime.fromtimestamp(earliest_timestamp, tz=timezone.utc).isoformat(),
            "latest_utc": datetime.fromtimestamp(latest_timestamp, tz=timezone.utc).isoformat(),
            "raw_rows_by_year": dict(sorted(raw_year_distribution.items())),
            "deduplicated_pairs_by_latest_year": dict(sorted(dedup_year_distribution.items())),
            "users_with_tied_latest_timestamp": tied_latest_users,
            "users_with_tied_latest_timestamp_share": round(tied_latest_users / len(users), 6),
        },
        "rating_distribution": {
            "raw": dict(sorted(raw_rating_distribution.items())),
            "deduplicated_latest": dict(sorted(dedup_rating_distribution.items())),
            "deduplicated_mean": round(
                sum(rating * count for rating, count in dedup_rating_distribution.items()) / unique_pairs,
                6,
            ),
            "deduplicated_rate_8_to_10_share": round(
                sum(dedup_rating_distribution[rating] for rating in (8, 9, 10)) / unique_pairs,
                6,
            ),
            "deduplicated_rate_1_to_4_share": round(
                sum(dedup_rating_distribution[rating] for rating in (1, 2, 3, 4)) / unique_pairs,
                6,
            ),
        },
        "users": {
            "raw_rows_per_user_quantiles": quantiles(raw_user.values(), (0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1)),
            "unique_items_per_user_quantiles": quantiles(user_unique.values(), (0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1)),
            "users_with_korean_item": sum(user_korean_unique[user] > 0 for user in users),
            "users_with_korean_item_share": round(
                sum(user_korean_unique[user] > 0 for user in users) / len(users), 6
            ),
            "korean_item_share_per_user_mean": round(sum(per_user_korean_share) / len(per_user_korean_share), 6),
            "korean_item_share_per_user_quantiles": quantiles(
                per_user_korean_share, (0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1)
            ),
        },
        "items": {
            "all_item_unique_user_bands": dict(sorted(item_bands.items())),
            "korean_item_unique_user_bands": dict(sorted(korean_item_bands.items())),
            "all_item_gini": gini(item_unique.values()),
            "korean_item_gini": gini(item_unique[item] for item in korean_rated_items),
            "all_concentration": concentration(item_unique.values()),
            "korean_concentration": concentration(item_unique[item] for item in korean_rated_items),
            "top_items": top_items,
            "top_korean_items": top_korean_items,
        },
        "onboarding_simulation_capacity": onboarding,
        "internal": {
            "rated_item_ids": rated_items,
            "korean_rated_item_ids": korean_rated_items,
            "item_unique_counts": item_unique,
        },
    }


def load_movielens_index(movies_path: Path, links_path: Path) -> dict[tuple[str, int], set[int]]:
    tmdb_by_movie: dict[int, int] = {}
    with links_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            value = (row.get("tmdbId") or "").strip()
            if value:
                tmdb_by_movie[int(row["movieId"])] = int(value)

    index: dict[tuple[str, int], set[int]] = defaultdict(set)
    with movies_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            movie_id = int(row["movieId"])
            tmdb_id = tmdb_by_movie.get(movie_id)
            if tmdb_id is None:
                continue
            candidates, year = movielens_title_candidates(row["title"])
            if year is None:
                continue
            for candidate in candidates:
                normalized = normalize_title(candidate)
                if normalized:
                    index[(normalized, year)].add(tmdb_id)
    return index


def bridge_to_tmdb(
    movies: dict[int, dict[str, Any]],
    rated_item_ids: set[int],
    korean_rated_item_ids: set[int],
    item_counts: Counter[int],
    movielens_index: dict[tuple[str, int], set[int]],
) -> dict[str, Any]:
    status: Counter[str] = Counter()
    status_korean: Counter[str] = Counter()
    pair_coverage: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    for movie_id in sorted(rated_item_ids):
        metadata = movies.get(movie_id)
        tmdb_ids: set[int] = set()
        if metadata and metadata["original_year"] is not None:
            for candidate in english_title_candidates(metadata["title_eng"]):
                key = (normalize_title(candidate), int(metadata["original_year"]))
                tmdb_ids.update(movielens_index.get(key, set()))
        if len(tmdb_ids) == 1:
            label = "UNIQUE_EXACT_TITLE_YEAR"
            if len(samples) < 20:
                samples.append(
                    {
                        "kmrd_movie_id": movie_id,
                        "title_ko": metadata["title_ko"] if metadata else None,
                        "title_eng": metadata["title_eng"] if metadata else None,
                        "tmdb_id": next(iter(tmdb_ids)),
                    }
                )
        elif len(tmdb_ids) > 1:
            label = "AMBIGUOUS_EXACT_TITLE_YEAR"
        else:
            label = "NO_EXACT_BRIDGE"
        status[label] += 1
        pair_coverage[label] += item_counts[movie_id]
        if movie_id in korean_rated_item_ids:
            status_korean[label] += 1

    total_items = len(rated_item_ids)
    total_pairs = sum(item_counts.values())
    return {
        "method": "conservative exact normalized English title + original year through MovieLens links.csv",
        "warning": "This is a lower-bound bridge feasibility estimate, not a completed TMDB identity map.",
        "rated_item_status": dict(sorted(status.items())),
        "rated_item_status_share": {
            key: round(value / total_items, 6) for key, value in sorted(status.items())
        },
        "korean_rated_item_status": dict(sorted(status_korean.items())),
        "korean_rated_item_status_share": {
            key: round(value / len(korean_rated_item_ids), 6)
            for key, value in sorted(status_korean.items())
        },
        "deduplicated_pair_status": dict(sorted(pair_coverage.items())),
        "deduplicated_pair_status_share": {
            key: round(value / total_pairs, 6) for key, value in sorted(pair_coverage.items())
        },
        "unique_match_samples": samples,
    }


def pct(value: int, total: int) -> str:
    return f"{100 * value / total:.2f}%" if total else "0.00%"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def render_markdown(result: dict[str, Any]) -> str:
    ratings = result["ratings"]
    raw = ratings["raw"]
    dedup = ratings["deduplicated_latest"]
    users = ratings["users"]
    duplicates = ratings["duplicates"]
    bridge = result["tmdb_bridge_feasibility"]
    metadata = result["metadata_coverage"]
    timestamps = ratings["timestamps"]
    item_bands = ratings["items"]["all_item_unique_user_bands"]
    kr_bands = ratings["items"]["korean_item_unique_user_bands"]
    band_names = sorted(set(item_bands) | set(kr_bands))
    band_rows = [[band, f"{item_bands.get(band, 0):,}", f"{kr_bands.get(band, 0):,}"] for band in band_names]
    rating_rows = [
        [score, f"{count:,}", pct(count, dedup["user_item_pairs"])]
        for score, count in ratings["rating_distribution"]["deduplicated_latest"].items()
    ]
    onboarding_rows = [
        [
            name,
            f"{value['users_with_at_least_k_plus_one_unique_items']:,}",
            f"{value['share'] * 100:.2f}%",
            f"{value['latest_item_korean_target_users']:,}",
            f"{value['strict_unique_latest_time_korean_target_users']:,}",
        ]
        for name, value in ratings["onboarding_simulation_capacity"].items()
    ]
    meta_rows = [
        [field, f"{count:,}", f"{metadata['rated_items']['shares'][field] * 100:.2f}%"]
        for field, count in metadata["rated_items"]["counts"].items()
    ]
    bridge_rows = [
        [
            status,
            f"{count:,}",
            f"{bridge['rated_item_status_share'][status] * 100:.2f}%",
            f"{bridge['korean_rated_item_status'].get(status, 0):,}",
            f"{bridge['korean_rated_item_status_share'].get(status, 0) * 100:.2f}%",
        ]
        for status, count in bridge["rated_item_status"].items()
    ]
    top_korean_rows = [
        [item["movie_id"], item["title_ko"], item["original_year"], f"{item['unique_user_item_pairs']:,}"]
        for item in ratings["items"]["top_korean_items"][:10]
    ]

    return f"""# KMRD-5M FEELM 적용 가능성 감사

> 상태: `COMPLETED_TARGET_PROXY_REJECTED`
> 원본 commit: `{result['source']['commit']}` ({result['source']['commit_date']})
> 목적: FEELM 사용자가 0명인 출시 전 상황에서 KMRD가 한국 시장 대리 평가 데이터로 쓸 수 있는지 판정한다.

## 결론

KMRD는 MovieLens보다 한국 제작 영화에 대한 상호작용을 많이 포함하지만, **FEELM 목표 사용자를
대리하는 출시 전 평가 데이터로는 기각한다.** 원본 {raw['rating_rows']:,}행을 최신 Rating 기준으로
중복 제거하면 {dedup['user_item_pairs']:,}개 user-item pair이고, 이 중 한국 제작 영화 pair는
{dedup['korean_user_item_pairs']:,}개({dedup['korean_pair_share'] * 100:.2f}%)다. 사용자
{raw['users']:,}명 중 한국 영화를 한 편이라도 평가한 사용자는 {users['users_with_korean_item']:,}명
({users['users_with_korean_item_share'] * 100:.2f}%)이다. 그러나 이 규모는 대표성을 뜻하지 않는다.

가장 큰 문제는 원본 README가 KMRD-2M과 KMRD-5M 사용자를 **20회 이상 평가한 사용자로 미리
선별했다**고 명시한다는 점이다. FEELM은 10편도 입력하지 않는 신규 사용자가 대부분일 것으로
예상하므로, 이 표본에서 과거 이력을 가려 만든 K0·K1·K3·K5는 실제 new-community cold start를
재현하지 않는다. 이미 활동적인 사용자를 인위적으로 차갑게 만든 실험일 뿐이다.

그 밖에도 다음 Gate를 통과하지 못한다.

- 저장소 README는 4,941,301 Rating·48,840 평가 영화를 적었지만, 현재 commit의 ZIP 원본은
  {raw['rating_rows']:,} Rating·{raw['rated_items']:,} 평가 영화다. 문서와 배포 파일 snapshot이 다르다.
- README는 Rating 범위를 1~10으로 설명하지만 원본에는 0점 행도
  {ratings['rating_distribution']['raw'].get('0', ratings['rating_distribution']['raw'].get(0, 0)):,}개 있다.
- 마지막 Rating 시점은 `{timestamps['latest_utc']}`로 현재 취향과 시장을 반영하지 못한다.
- 연령·성별·국적 필드가 없어 한국 20대 대표성을 검증할 수 없다.
- 중복 행 {duplicates['duplicate_extra_rows']:,}개가 있고 충돌 Rating pair도
  {duplicates['conflicting_rating_pairs']:,}개다.
- 저장소에 명시적 LICENSE 파일이 없어 제품 사용·재배포 권한을 별도로 확인해야 한다.
- 장르·국가·배우는 있지만 줄거리·키워드·감독·TMDB ID·OTT provider는 없다.

판정은 **파이프라인·알고리즘 연습용 제한적 사용은 가능, 출시 전 모델 선별·FEELM 사용자 성능
증명·production 사용은 NO**다.

## 원본 규모와 중복

| 항목 | 값 |
| --- | ---: |
| 원본 Rating 행 | {raw['rating_rows']:,} |
| README 기재 Rating 행 | {result['source']['readme_published_statistics']['rating_rows']:,} |
| 사용자 | {raw['users']:,} |
| 평가된 영화 | {raw['rated_items']:,} |
| README 기재 평가 영화 | {result['source']['readme_published_statistics']['rated_items']:,} |
| metadata 영화 행 | {result['metadata_coverage']['source']['movie_rows']:,} |
| 중복 제거 user-item pair | {dedup['user_item_pairs']:,} |
| 중복 제거 행 | {duplicates['duplicate_extra_rows']:,} |
| 중복 pair | {duplicates['duplicate_user_item_pairs']:,} |
| 서로 다른 점수가 기록된 중복 pair | {duplicates['conflicting_rating_pairs']:,} |
| 한국 제작 평가 영화 | {dedup['korean_rated_items']:,} |
| 한국 제작 user-item pair | {dedup['korean_user_item_pairs']:,} ({dedup['korean_pair_share'] * 100:.2f}%) |

중복은 `(user, movie)`별 가장 최신 timestamp를 사용했다. timestamp가 같으면 파일의 마지막 행을
사용한다. 원본을 그대로 ALS에 넣으면 동일 사용자의 동일 영화가 여러 번 학습될 수 있으므로 반드시
dedupe가 필요하다. README의 `num item=191,238`도 metadata 영화 행 180,982개와 다른 개념이므로
카탈로그 영화 수로 그대로 인용하면 안 된다.

## 사용자별 한국 영화 신호

| 지표 | 값 |
| --- | --- |
| 사용자별 고유 평가 수 | `{json.dumps(users['unique_items_per_user_quantiles'], ensure_ascii=False)}` |
| 사용자별 한국 영화 비중 평균 | {users['korean_item_share_per_user_mean'] * 100:.2f}% |
| 사용자별 한국 영화 비중 분위수 | `{json.dumps(users['korean_item_share_per_user_quantiles'], ensure_ascii=False)}` |
| 한국 영화 평가 경험 사용자 | {users['users_with_korean_item']:,} ({users['users_with_korean_item_share'] * 100:.2f}%) |

전체 한국 영화 pair 비율과 사용자 동일가중 비율을 함께 봐야 한다. 전자는 활동량이 큰 사용자의 영향을
강하게 받는다.

## 영화별 상호작용 희소성

{table(['고유 사용자 수', '전체 영화', '한국 영화'], band_rows)}

전체 item Gini는 {ratings['items']['all_item_gini']:.4f}, 한국 item Gini는
{ratings['items']['korean_item_gini']:.4f}다. KMRD도 인기작 집중과 롱테일 문제를 갖고 있으므로
전체 평균 NDCG만으로 모델을 고르면 안 된다.

### 한국 영화 Top 10

{table(['KMRD ID', '제목', '원작 연도', '고유 사용자'], top_korean_rows)}

## Rating 분포

{table(['점수', '중복 제거 pair', '비율'], rating_rows)}

중복 제거 평균은 {ratings['rating_distribution']['deduplicated_mean']:.3f}/10이고,
8~10점은 {ratings['rating_distribution']['deduplicated_rate_8_to_10_share'] * 100:.2f}%,
1~4점은 {ratings['rating_distribution']['deduplicated_rate_1_to_4_share'] * 100:.2f}%다.
implicit ALS로 바꿀 경우 모든 Rating을 양성으로 취급하면 낮은 평점의 의미가 사라지므로 threshold와
confidence 변환을 별도 실험해야 한다.

## K0·K1·K3·K5 수치와 해석 한계

{table(['조건', '평가 가능 사용자', '전체 비율', '마지막 item이 한국 영화', '마지막 timestamp 고유 한국 target'], onboarding_rows)}

이 표는 모델 성능이나 실제 cold-start 대표성이 아니라 **기존 고활동 사용자 이력을 가리는 계산이
가능한지**만 보여준다. 모든 사용자를 20회 이상 평가한 사람으로 선별한 뒤 이력을 가린 것이므로,
FEELM 신규 사용자의 이탈·무응답·낮은 관여도를 재현하지 못한다. 같은 timestamp에 여러 영화를
평가한 사용자는 엄격한 시간 순서도 복원할 수 없다.

## 메타데이터 범위

{table(['필드', '평가 영화 보유 수', 'coverage'], meta_rows)}

지원하지 않는 필드: `{', '.join(metadata['rated_items']['unsupported_fields'])}`.
따라서 KMRD 단독으로 TMDB embedding/content 모델을 만들 수 없고, TMDB identity 연결이 선행돼야 한다.

## TMDB 연결 하한선

{table(['상태', '전체 평가 영화', '비율', '한국 평가 영화', '한국 비율'], bridge_rows)}

영문 제목과 원작 연도를 MovieLens `links.csv`를 통해 정확 일치시킨 보수적 하한선이다. 일치하지 않는
영화가 TMDB에 없다는 뜻이 아니다. 본 적용 전에는 TMDB search/detail로 별도 identity resolution과
수동 표본 검증이 필요하다.

## FEELM에서 허용되는 사용법

1. CSV 적재·중복 제거·시간 분할·ALS 학습 코드의 재현성 검증에 사용한다.
2. Popularity·콘텐츠·ALS 구현의 일반적인 동작과 slice 보고 형식을 연습한다.
3. 결과 명칭은 `KMRD_BENCHMARK_DIAGNOSTIC`으로 제한한다.
4. KMRD에서 이긴 모델을 FEELM의 proxy champion 또는 한국 사용자 추천 우승 모델로 부르지 않는다.
5. 라이선스·원천 서비스 약관 확인 전에는 데이터나 파생물을 production에 배포하지 않는다.

## 현재 결정

| 질문 | 판정 |
| --- | --- |
| 출시 전 FEELM 목표 사용자 proxy로 실험 가능한가 | `NO_SELECTION_AND_TARGET_MISMATCH` |
| 한국 제작 영화 상호작용을 더 많이 포함하는가 | `YES_DESCRIPTIVE_ONLY` |
| 한국 20대 정답 데이터인가 | `NO` |
| 현재 취향을 반영하는가 | `NO_STALE` |
| 그대로 production 학습 데이터로 승인 가능한가 | `NO_LICENSE_AND_DOMAIN_GATE` |
| KMRD 분석만으로 ALS 기본 뼈대를 확정할 수 있는가 | `NO_TARGET_PROXY_REJECTED` |
"""


def main() -> None:
    args = parse_args()
    meta_zip = args.kmrd_root / "kmr_dataset" / "datafile" / "kmrd" / "meta.zip"
    rates_zip = args.kmrd_root / "kmr_dataset" / "datafile" / "kmrd" / "rates-5m.zip"
    movies, metadata_summary = load_metadata(meta_zip)
    ratings = audit_ratings(rates_zip, movies)
    internal = ratings.pop("internal")
    all_catalog_ids = set(movies)
    korean_catalog_ids = {item for item, value in movies.items() if "한국" in value["countries"]}
    rated_ids = set(internal["rated_item_ids"])
    korean_rated_ids = set(internal["korean_rated_item_ids"])
    metadata = {
        "source": metadata_summary,
        "all_catalog": metadata_coverage(movies, all_catalog_ids),
        "korean_catalog": metadata_coverage(movies, korean_catalog_ids),
        "rated_items": metadata_coverage(movies, rated_ids),
        "korean_rated_items": metadata_coverage(movies, korean_rated_ids),
    }
    movielens_index = load_movielens_index(args.movielens_movies, args.movielens_links)
    bridge = bridge_to_tmdb(
        movies,
        rated_ids,
        korean_rated_ids,
        internal["item_unique_counts"],
        movielens_index,
    )

    published_statistics = {
        "rating_rows": 4_941_301,
        "users": 86_457,
        "rated_items": 48_840,
    }
    actual_statistics = {
        "rating_rows": ratings["raw"]["rating_rows"],
        "users": ratings["raw"]["users"],
        "rated_items": ratings["raw"]["rated_items"],
    }

    result = {
        "schema_version": 1,
        "audit_id": "REC_DATA_008_KMRD_FEASIBILITY_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "repository": "https://github.com/lovit/kmrd",
            "commit": args.source_commit,
            "commit_date": "2020-01-29T15:54:57+09:00",
            "license_file_observed": False,
            "readme_published_statistics": published_statistics,
            "archive_actual_statistics": actual_statistics,
            "readme_archive_consistent": published_statistics == actual_statistics,
            "readme_user_selection": "KMRD-2M and KMRD-5M users rated at least 20 times",
            "readme_archive_differences": {
                key: actual_statistics[key] - published_statistics[key]
                for key in published_statistics
            },
            "meta_zip": {"path": str(meta_zip), "sha256": sha256_file(meta_zip)},
            "rates_zip": {"path": str(rates_zip), "sha256": sha256_file(rates_zip)},
        },
        "claim_boundary": {
            "allowed": "pipeline, deduplication, split, and algorithm diagnostic benchmark only",
            "forbidden": [
                "FEELM target-user proxy champion claim",
                "FEELM user satisfaction claim",
                "Korean twenties representativeness claim",
                "current Korean market claim",
                "production or redistribution approval",
            ],
        },
        "ratings": ratings,
        "metadata_coverage": metadata,
        "tmdb_bridge_feasibility": bridge,
        "decision": {
            "target_user_proxy": "NO_SELECTION_AND_TARGET_MISMATCH",
            "research_proxy": "NO_TARGET_PROXY_DIAGNOSTIC_ONLY",
            "production_training_data": "NO_LICENSE_AND_DOMAIN_GATE",
            "als_backbone": "NO_TARGET_PROXY_REJECTED",
            "recommended_next_experiment": "EXPLICIT_PREFERENCE_CONTENT_BASELINE_AND_LOGGING_CONTRACT",
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(render_markdown(result) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "COMPLETED_TARGET_PROXY_REJECTED",
                "rating_rows": ratings["raw"]["rating_rows"],
                "unique_pairs": ratings["deduplicated_latest"]["user_item_pairs"],
                "korean_pair_share": ratings["deduplicated_latest"]["korean_pair_share"],
                "latest_utc": ratings["timestamps"]["latest_utc"],
                "output_json": str(args.output_json),
                "output_markdown": str(args.output_markdown),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
