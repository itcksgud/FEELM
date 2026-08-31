#!/usr/bin/env python3
"""Audit whether MovieLens 32M can support K/horizon/candidate-size decisions.

This script intentionally uses only the Python standard library so that the
descriptive audit can run before the Spark/recommender environment is installed.
It treats MovieLens timestamps as rating-entry timestamps, never as watch times.
Movie release timing is an approximate year parsed from ``movies.csv``; exact
release dates and Korean production metadata require a versioned TMDB cache.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


K_VALUES = (5, 10, 25, 50)
TIME_HORIZON_DAYS = (7, 30, 90, 180, 365)
EVENT_HORIZONS = (5, 10, 20, 50)
CANDIDATE_N_GRID = (50, 100, 200, 500, 1000, 2000)
DATASET_END_ISO = "2023-10-13T02:29:07+00:00"
YEAR_PATTERN = re.compile(r"\((\d{4})\)\s*$")
SECONDS_PER_DAY = 86_400
PROGRESS_INTERVAL = 5_000_000
TIMESTAMP_YEAR_STARTS = tuple(
    int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    for year in range(1970, 2102)
)
TIMESTAMP_YEARS = tuple(range(1970, 2102))


def parse_release_year(title: str) -> int | None:
    match = YEAR_PATTERN.search(title)
    if match is None:
        return None
    year = int(match.group(1))
    return year if 1870 <= year <= 2100 else None


def rating_code(value: str | float) -> int:
    code = int(round(float(value) * 2))
    if code < 1 or code > 10:
        raise ValueError(f"rating outside MovieLens 0.5..5.0 scale: {value}")
    return code


def relative_label(profile_counts: Sequence[int], future_rating_code: int) -> str:
    """Classify a future rating using the past profile's shrunken-free midrank.

    This is deliberately a descriptive proxy. It avoids calling an unrated item
    negative and avoids a universal 4-star threshold, but it is not a product
    satisfaction label.
    """

    total = sum(profile_counts)
    if total <= 0:
        raise ValueError("profile must contain at least one rating")
    lower = sum(profile_counts[: future_rating_code - 1])
    equal = profile_counts[future_rating_code - 1]
    utility = (lower + 0.5 * equal) / total
    if utility >= 0.7:
        return "POSITIVE"
    if utility <= 0.3:
        return "NEGATIVE"
    return "NEUTRAL"


def histogram_quantiles(histogram: Counter[int], quantiles: Sequence[float]) -> dict[str, float | None]:
    total = sum(histogram.values())
    if total == 0:
        return {f"p{int(q * 100):02d}": None for q in quantiles}
    ordered = sorted(histogram.items())
    result: dict[str, float | None] = {}
    for quantile in quantiles:
        target = max(1, math.ceil(total * quantile))
        cumulative = 0
        selected = ordered[-1][0]
        for value, count in ordered:
            cumulative += count
            if cumulative >= target:
                selected = value
                break
        result[f"p{int(quantile * 100):02d}"] = selected
    return result


def percentage(count: int, total: int) -> float | None:
    return round(count / total, 6) if total else None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class HorizonAccumulator:
    users: int = 0
    event_histogram: Counter[int] = field(default_factory=Counter)
    personal_positive_histogram: Counter[int] = field(default_factory=Counter)
    raw_positive_histogram: Counter[int] = field(default_factory=Counter)
    personal_negative_histogram: Counter[int] = field(default_factory=Counter)

    def add(self, events: int, personal_positive: int, raw_positive: int, personal_negative: int) -> None:
        self.users += 1
        self.event_histogram[events] += 1
        self.personal_positive_histogram[personal_positive] += 1
        self.raw_positive_histogram[raw_positive] += 1
        self.personal_negative_histogram[personal_negative] += 1

    def as_dict(self) -> dict[str, Any]:
        def threshold_counts(histogram: Counter[int], values: Sequence[int]) -> dict[str, int]:
            return {
                f"at_least_{value}": sum(count for observed, count in histogram.items() if observed >= value)
                for value in values
            }

        return {
            "fully_observable_users": self.users,
            "future_events": {
                "threshold_users": threshold_counts(self.event_histogram, (1, 5, 10, 20)),
                "quantiles": histogram_quantiles(self.event_histogram, (0.1, 0.25, 0.5, 0.75, 0.9)),
                "mean": round(
                    sum(value * count for value, count in self.event_histogram.items()) / self.users, 4
                ) if self.users else None,
            },
            "personal_relative_positive": {
                "definition": "past-K midrank utility >= 0.7",
                "threshold_users": threshold_counts(self.personal_positive_histogram, (1, 3, 5, 10)),
                "quantiles": histogram_quantiles(
                    self.personal_positive_histogram, (0.1, 0.25, 0.5, 0.75, 0.9)
                ),
            },
            "raw_rating_positive": {
                "definition": "future rating >= 4.0",
                "threshold_users": threshold_counts(self.raw_positive_histogram, (1, 3, 5, 10)),
                "quantiles": histogram_quantiles(
                    self.raw_positive_histogram, (0.1, 0.25, 0.5, 0.75, 0.9)
                ),
            },
            "personal_relative_negative": {
                "definition": "past-K midrank utility <= 0.3",
                "threshold_users": threshold_counts(self.personal_negative_histogram, (1, 3, 5, 10)),
            },
        }


@dataclass
class EventHorizonAccumulator:
    users_with_required_events: int = 0
    duration_days_histogram: Counter[int] = field(default_factory=Counter)
    positive_histogram: Counter[int] = field(default_factory=Counter)
    raw_positive_histogram: Counter[int] = field(default_factory=Counter)

    def add(self, duration_days: int, positives: int, raw_positives: int) -> None:
        self.users_with_required_events += 1
        self.duration_days_histogram[duration_days] += 1
        self.positive_histogram[positives] += 1
        self.raw_positive_histogram[raw_positives] += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "users_with_required_strict_future_events": self.users_with_required_events,
            "time_to_event_count_days": histogram_quantiles(
                self.duration_days_histogram, (0.1, 0.25, 0.5, 0.75, 0.9)
            ),
            "users_with_personal_positive": sum(
                count for value, count in self.positive_histogram.items() if value >= 1
            ),
            "personal_positive_count_quantiles": histogram_quantiles(
                self.positive_histogram, (0.1, 0.25, 0.5, 0.75, 0.9)
            ),
            "users_with_raw_4plus": sum(
                count for value, count in self.raw_positive_histogram.items() if value >= 1
            ),
        }


@dataclass
class KAccumulator:
    users_with_k: int = 0
    boundary_timestamp_collision_users: int = 0
    boundary_same_utc_day_collision_users: int = 0
    time_horizons: dict[int, HorizonAccumulator] = field(
        default_factory=lambda: {days: HorizonAccumulator() for days in TIME_HORIZON_DAYS}
    )
    time_horizons_next_utc_day: dict[int, HorizonAccumulator] = field(
        default_factory=lambda: {days: HorizonAccumulator() for days in TIME_HORIZON_DAYS}
    )
    event_horizons: dict[int, EventHorizonAccumulator] = field(
        default_factory=lambda: {events: EventHorizonAccumulator() for events in EVENT_HORIZONS}
    )
    event_horizons_next_utc_day: dict[int, EventHorizonAccumulator] = field(
        default_factory=lambda: {events: EventHorizonAccumulator() for events in EVENT_HORIZONS}
    )


def load_movies(path: Path) -> tuple[dict[int, int | None], dict[str, Any]]:
    release_years: dict[int, int | None] = {}
    genre_missing = 0
    release_year_histogram: Counter[int] = Counter()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            movie_id = int(row["movieId"])
            year = parse_release_year(row["title"])
            release_years[movie_id] = year
            if year is not None:
                release_year_histogram[year] += 1
            if row["genres"] == "(no genres listed)":
                genre_missing += 1
    summary = {
        "movies": len(release_years),
        "release_year_known": sum(release_year_histogram.values()),
        "release_year_missing": len(release_years) - sum(release_year_histogram.values()),
        "genre_missing": genre_missing,
        "release_year_range": [min(release_year_histogram), max(release_year_histogram)]
        if release_year_histogram else None,
        "release_year_histogram": dict(sorted(release_year_histogram.items())),
    }
    return release_years, summary


def load_link_summary(path: Path) -> dict[str, int]:
    rows = tmdb_present = imdb_present = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows += 1
            tmdb_present += bool(row.get("tmdbId", "").strip())
            imdb_present += bool(row.get("imdbId", "").strip())
    return {"rows": rows, "tmdb_id_present": tmdb_present, "imdb_id_present": imdb_present}


def timestamp_year(timestamp: int) -> int:
    index = bisect.bisect_right(TIMESTAMP_YEAR_STARTS, timestamp) - 1
    if index < 0 or index >= len(TIMESTAMP_YEARS):
        raise ValueError(f"timestamp outside supported year range: {timestamp}")
    return TIMESTAMP_YEARS[index]


def classify_lag_years(lag: int) -> str:
    if lag < 0:
        return "negative_or_title_year_issue"
    if lag == 0:
        return "same_calendar_year"
    if lag == 1:
        return "one_year"
    if lag <= 5:
        return "two_to_five_years"
    if lag <= 10:
        return "six_to_ten_years"
    if lag <= 20:
        return "eleven_to_twenty_years"
    return "over_twenty_years"


def process_user(
    events: list[tuple[int, int, int]],
    dataset_end_timestamp: int,
    k_accumulators: dict[int, KAccumulator],
    user_rating_count_histogram: Counter[int],
    user_span_days_histogram: Counter[int],
    max_same_timestamp_histogram: Counter[int],
    max_same_day_histogram: Counter[int],
) -> None:
    if not events:
        return
    events.sort(key=lambda value: (value[0], value[1]))
    timestamps = [event[0] for event in events]
    user_rating_count_histogram[len(events)] += 1
    user_span_days_histogram[(timestamps[-1] - timestamps[0]) // SECONDS_PER_DAY] += 1
    same_timestamp = Counter(timestamps)
    same_day = Counter(timestamp // SECONDS_PER_DAY for timestamp in timestamps)
    max_same_timestamp_histogram[max(same_timestamp.values())] += 1
    max_same_day_histogram[max(same_day.values())] += 1

    for k, accumulator in k_accumulators.items():
        if len(events) < k:
            continue
        accumulator.users_with_k += 1
        t0 = timestamps[k - 1]
        strict_start = bisect.bisect_right(timestamps, t0)
        if strict_start > k:
            accumulator.boundary_timestamp_collision_users += 1

        profile_counts = [0] * 10
        for _, _, rating in events[:k]:
            profile_counts[rating - 1] += 1

        future = events[strict_start:]
        future_timestamps = timestamps[strict_start:]
        next_utc_day = (t0 // SECONDS_PER_DAY + 1) * SECONDS_PER_DAY
        next_day_start = bisect.bisect_left(future_timestamps, next_utc_day)
        if next_day_start > 0:
            accumulator.boundary_same_utc_day_collision_users += 1
        personal_positive_prefix = [0]
        raw_positive_prefix = [0]
        personal_negative_prefix = [0]
        for _, _, rating in future:
            label = relative_label(profile_counts, rating)
            personal_positive_prefix.append(personal_positive_prefix[-1] + (label == "POSITIVE"))
            personal_negative_prefix.append(personal_negative_prefix[-1] + (label == "NEGATIVE"))
            raw_positive_prefix.append(raw_positive_prefix[-1] + (rating >= 8))

        for days, horizon in accumulator.time_horizons.items():
            if t0 + days * SECONDS_PER_DAY > dataset_end_timestamp:
                continue
            end = bisect.bisect_right(future_timestamps, t0 + days * SECONDS_PER_DAY)
            horizon.add(
                end,
                personal_positive_prefix[end],
                raw_positive_prefix[end],
                personal_negative_prefix[end],
            )
            accumulator.time_horizons_next_utc_day[days].add(
                end - next_day_start,
                personal_positive_prefix[end] - personal_positive_prefix[next_day_start],
                raw_positive_prefix[end] - raw_positive_prefix[next_day_start],
                personal_negative_prefix[end] - personal_negative_prefix[next_day_start],
            )

        for required, event_horizon in accumulator.event_horizons.items():
            if len(future) < required:
                continue
            duration_days = (future_timestamps[required - 1] - t0) // SECONDS_PER_DAY
            event_horizon.add(
                duration_days,
                personal_positive_prefix[required],
                raw_positive_prefix[required],
            )

            if len(future) - next_day_start < required:
                continue
            next_day_event_horizon = accumulator.event_horizons_next_utc_day[required]
            next_day_end = next_day_start + required
            next_day_event_horizon.add(
                (future_timestamps[next_day_end - 1] - t0) // SECONDS_PER_DAY,
                personal_positive_prefix[next_day_end] - personal_positive_prefix[next_day_start],
                raw_positive_prefix[next_day_end] - raw_positive_prefix[next_day_start],
            )


def stream_ratings(
    path: Path,
    release_years: dict[int, int | None],
    dataset_end_timestamp: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    movie_counts: Counter[int] = Counter()
    movie_first_timestamp: dict[int, int] = {}
    rating_lag_histogram: Counter[str] = Counter()
    first_rating_lag_histogram: Counter[str] = Counter()
    user_rating_count_histogram: Counter[int] = Counter()
    user_span_days_histogram: Counter[int] = Counter()
    max_same_timestamp_histogram: Counter[int] = Counter()
    max_same_day_histogram: Counter[int] = Counter()
    k_accumulators = {k: KAccumulator() for k in K_VALUES}
    rating_rows = 0
    min_timestamp: int | None = None
    max_timestamp: int | None = None
    current_user: int | None = None
    events: list[tuple[int, int, int]] = []

    with path.open("r", encoding="utf-8", newline="") as handle:
        header = handle.readline().strip()
        if header != "userId,movieId,rating,timestamp":
            raise RuntimeError(f"unexpected ratings header: {header}")
        for line in handle:
            user_text, movie_text, rating_text, timestamp_text = line.rstrip("\r\n").split(",")
            user_id = int(user_text)
            movie_id = int(movie_text)
            rating = rating_code(rating_text)
            timestamp = int(timestamp_text)
            if current_user is not None and user_id < current_user:
                raise RuntimeError(
                    "ratings.csv must be grouped by ascending userId for the streaming audit: "
                    f"observed {user_id} after {current_user}"
                )
            if current_user is not None and user_id != current_user:
                process_user(
                    events,
                    dataset_end_timestamp,
                    k_accumulators,
                    user_rating_count_histogram,
                    user_span_days_histogram,
                    max_same_timestamp_histogram,
                    max_same_day_histogram,
                )
                events = []
            current_user = user_id
            events.append((timestamp, movie_id, rating))
            rating_rows += 1
            movie_counts[movie_id] += 1
            previous_first = movie_first_timestamp.get(movie_id)
            if previous_first is None or timestamp < previous_first:
                movie_first_timestamp[movie_id] = timestamp
            min_timestamp = timestamp if min_timestamp is None else min(min_timestamp, timestamp)
            max_timestamp = timestamp if max_timestamp is None else max(max_timestamp, timestamp)
            release_year = release_years.get(movie_id)
            if release_year is not None:
                rating_lag_histogram[classify_lag_years(timestamp_year(timestamp) - release_year)] += 1
            if rating_rows % PROGRESS_INTERVAL == 0:
                elapsed = time.perf_counter() - started
                print(
                    f"processed={rating_rows:,} users_completed={sum(user_rating_count_histogram.values()):,} "
                    f"elapsed_seconds={elapsed:.1f}",
                    flush=True,
                )
        process_user(
            events,
            dataset_end_timestamp,
            k_accumulators,
            user_rating_count_histogram,
            user_span_days_histogram,
            max_same_timestamp_histogram,
            max_same_day_histogram,
        )

    for movie_id, first_timestamp in movie_first_timestamp.items():
        release_year = release_years.get(movie_id)
        if release_year is not None:
            first_rating_lag_histogram[
                classify_lag_years(timestamp_year(first_timestamp) - release_year)
            ] += 1

    if max_timestamp is None or min_timestamp is None:
        raise RuntimeError("ratings file is empty")
    if max_timestamp != dataset_end_timestamp:
        raise RuntimeError(
            f"dataset end mismatch: observed={max_timestamp}, expected={dataset_end_timestamp}"
        )

    rating_count_tiers = {
        "zero": sum(1 for movie_id in release_years if movie_counts[movie_id] == 0),
        "one_to_nine": sum(1 for count in movie_counts.values() if 1 <= count <= 9),
        "ten_to_ninety_nine": sum(1 for count in movie_counts.values() if 10 <= count <= 99),
        "one_hundred_to_999": sum(1 for count in movie_counts.values() if 100 <= count <= 999),
        "one_thousand_to_9999": sum(1 for count in movie_counts.values() if 1000 <= count <= 9999),
        "ten_thousand_plus": sum(1 for count in movie_counts.values() if count >= 10000),
    }

    return {
        "rating_rows": rating_rows,
        "users": sum(user_rating_count_histogram.values()),
        "rating_time": {
            "earliest_utc": datetime.fromtimestamp(min_timestamp, tz=timezone.utc).isoformat(),
            "latest_utc": datetime.fromtimestamp(max_timestamp, tz=timezone.utc).isoformat(),
        },
        "rating_release_lag_years": {
            "scope": "all ratings with a release year parsed from MovieLens title",
            "counts": dict(rating_lag_histogram),
            "shares": {
                key: percentage(value, sum(rating_lag_histogram.values()))
                for key, value in rating_lag_histogram.items()
            },
        },
        "first_rating_release_lag_years": {
            "scope": "first observed MovieLens rating per movie; release year is approximate",
            "counts": dict(first_rating_lag_histogram),
            "shares": {
                key: percentage(value, sum(first_rating_lag_histogram.values()))
                for key, value in first_rating_lag_histogram.items()
            },
        },
        "user_activity": {
            "rating_count_quantiles": histogram_quantiles(
                user_rating_count_histogram, (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)
            ),
            "rating_span_days_quantiles": histogram_quantiles(
                user_span_days_histogram, (0.1, 0.25, 0.5, 0.75, 0.9)
            ),
            "max_ratings_same_exact_timestamp_quantiles": histogram_quantiles(
                max_same_timestamp_histogram, (0.5, 0.75, 0.9, 0.99)
            ),
            "max_ratings_same_utc_day_quantiles": histogram_quantiles(
                max_same_day_histogram, (0.5, 0.75, 0.9, 0.99)
            ),
            "users_with_max_same_day_at_least": {
                str(value): sum(count for observed, count in max_same_day_histogram.items() if observed >= value)
                for value in (10, 25, 50, 100)
            },
        },
        "k_feasibility": {
            str(k): {
                "users_with_k": accumulator.users_with_k,
                "boundary_timestamp_collision_users": accumulator.boundary_timestamp_collision_users,
                "boundary_collision_share": percentage(
                    accumulator.boundary_timestamp_collision_users, accumulator.users_with_k
                ),
                "boundary_same_utc_day_collision_users": accumulator.boundary_same_utc_day_collision_users,
                "boundary_same_utc_day_collision_share": percentage(
                    accumulator.boundary_same_utc_day_collision_users, accumulator.users_with_k
                ),
                "time_horizons_days": {
                    str(days): horizon.as_dict()
                    for days, horizon in accumulator.time_horizons.items()
                },
                "time_horizons_excluding_profile_boundary_utc_day": {
                    str(days): horizon.as_dict()
                    for days, horizon in accumulator.time_horizons_next_utc_day.items()
                },
                "event_horizons": {
                    str(events): horizon.as_dict()
                    for events, horizon in accumulator.event_horizons.items()
                },
                "event_horizons_excluding_profile_boundary_utc_day": {
                    str(events): horizon.as_dict()
                    for events, horizon in accumulator.event_horizons_next_utc_day.items()
                },
            }
            for k, accumulator in k_accumulators.items()
        },
        "movie_interactions": {
            "rating_count_tiers": rating_count_tiers,
            "rated_movies": len(movie_counts),
            "first_rating_timestamp_by_movie": movie_first_timestamp,
            "rating_count_by_movie": movie_counts,
        },
        "runtime_seconds": round(time.perf_counter() - started, 3),
    }


def catalog_snapshots(
    release_years: dict[int, int | None],
    first_rating_timestamp: dict[int, int],
) -> dict[str, Any]:
    snapshots: dict[str, Any] = {}
    for year in range(1995, 2024):
        end = int(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        released = [movie_id for movie_id, release_year in release_years.items() if release_year and release_year <= year]
        scorable = sum(1 for movie_id in released if first_rating_timestamp.get(movie_id, end + 1) <= end)
        recent = [
            movie_id for movie_id, release_year in release_years.items()
            if release_year is not None and year - 1 <= release_year <= year
        ]
        recent_scorable = sum(
            1 for movie_id in recent if first_rating_timestamp.get(movie_id, end + 1) <= end
        )
        snapshots[str(year)] = {
            "released_catalog_proxy": len(released),
            "als_scorable_at_least_one_rating": scorable,
            "als_scorable_share": percentage(scorable, len(released)),
            "content_only_or_unrated_proxy": len(released) - scorable,
            "released_in_current_or_previous_year": len(recent),
            "recent_als_scorable": recent_scorable,
            "recent_als_scorable_share": percentage(recent_scorable, len(recent)),
            "candidate_n_share_of_released_pool": {
                str(n): percentage(min(n, len(released)), len(released))
                for n in CANDIDATE_N_GRID
            },
        }
    return snapshots


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def percent_text(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def build_markdown(result: dict[str, Any]) -> str:
    activity = result["ratings"]["user_activity"]
    lag = result["ratings"]["rating_release_lag_years"]
    k_rows = []
    for k, values in result["ratings"]["k_feasibility"].items():
        h90 = values["time_horizons_days"]["90"]
        h90_next_day = values["time_horizons_excluding_profile_boundary_utc_day"]["90"]
        e20 = values["event_horizons"]["20"]
        k_rows.append(
            (
                k,
                f"{values['users_with_k']:,}",
                percent_text(values["boundary_collision_share"]),
                percent_text(values["boundary_same_utc_day_collision_share"]),
                f"{h90['fully_observable_users']:,}",
                f"{h90['future_events']['threshold_users']['at_least_5']:,}",
                f"{h90['personal_relative_positive']['threshold_users']['at_least_1']:,}",
                f"{h90_next_day['future_events']['threshold_users']['at_least_5']:,}",
                f"{h90_next_day['personal_relative_positive']['threshold_users']['at_least_1']:,}",
                f"{e20['users_with_required_strict_future_events']:,}",
                e20["time_to_event_count_days"].get("p50"),
            )
        )
    snapshot_rows = []
    for year in (2000, 2005, 2010, 2015, 2018, 2020, 2021, 2022, 2023):
        values = result["catalog_snapshots"][str(year)]
        snapshot_rows.append(
            (
                year,
                f"{values['released_catalog_proxy']:,}",
                f"{values['als_scorable_at_least_one_rating']:,}",
                percent_text(values["als_scorable_share"]),
                f"{values['released_in_current_or_previous_year']:,}",
                percent_text(values["recent_als_scorable_share"]),
            )
        )
    latest_snapshot = result["catalog_snapshots"]["2023"]
    latest_n_shares = latest_snapshot["candidate_n_share_of_released_pool"]
    return f"""# MovieLens 32M 시간·후보 설계 가능성 감사

> 상태: `COMPLETED_DESCRIPTIVE_AUDIT`
> 생성 시각: {result['generated_at_utc']}
> 주의: MovieLens timestamp는 평가 입력 시각이며 관람 시각이 아니다. MovieLens 제목의 연도는 정확한 개봉일이 아닌 근사치다.

## 결론

이 문서는 K, 평가 기간, 후보 N을 먼저 고정하지 않고 실제 데이터가 허용하는 실험 범위를 정하기 위한 기술통계다.
추천 모델 성능이나 한국 20대 적합성을 승인하지 않는다. Candidate N은 `{', '.join(map(str, CANDIDATE_N_GRID))}`의
`Recall@N` 곡선을 후속 모델 비교에서 측정한 뒤 선택한다.
전체 기간을 본 기술통계이므로 이 결과로 파라미터를 고른 뒤 같은 전체 데이터를 최종 Test로 재사용하면 안 된다.

## 평가 입력 시점의 신뢰성

- 사용자 Rating 수 중앙값: {activity['rating_count_quantiles']['p50']}편
- 사용자별 최대 동일 UTC 일 Rating 수 중앙값/p90/p99: {activity['max_ratings_same_utc_day_quantiles']['p50']} / {activity['max_ratings_same_utc_day_quantiles']['p90']} / {activity['max_ratings_same_utc_day_quantiles']['p99']}
- 하루에 25편 이상 Rating을 입력한 적 있는 사용자: {activity['users_with_max_same_day_at_least']['25']:,}명
- K번째 Rating과 같은 timestamp 또는 같은 UTC 날짜에 뒤 Rating이 더 있는 사용자를 아래 표에 별도 집계했다.

개봉연도 대비 전체 Rating 지연 분포:

{markdown_table(['구간', 'Rating 수', '비율'], [(key, f"{value:,}", percent_text(lag['shares'].get(key))) for key, value in lag['counts'].items()])}

## K와 미래 관측 가능성

`90일`은 결정값이 아니라 비교 열이다. 동일 timestamp만 제외한 수치와 프로필 경계의 같은 UTC 날짜를
전부 제외한 보수적 수치를 함께 표시한다.

{markdown_table(['K', '사용자', '동일시각 충돌', '동일날짜 충돌', '90일 완전관측', '90일 미래≥5', '90일 pos≥1', '다음날부터 미래≥5', '다음날부터 pos≥1', '다음20개 가능', '다음20개 p50일'], k_rows)}

세부 7/30/90/180/365일 및 다음 5/10/20/50개 분포는 JSON 결과에 있다.

## 시점별 카탈로그와 ALS 가능 범위

MovieLens 제목에서 파싱한 개봉연도와 해당 시점까지 최소 1개 Rating이 존재하는지를 비교한 근사치다.

{markdown_table(['연도', '개봉 Pool 근사', 'ALS 가능', 'ALS 가능률', '최근 2년 영화', '최근 ALS 가능률'], snapshot_rows)}

2023년 개봉 Pool 근사에서 후보 500개는 {percent_text(latest_n_shares['500'])}를 차지한다. 이 비율은 Recall이 아니다.

## 아직 결정하지 않는 것

- `K=25`: K별 표본과 모델 성능 곡선, 실제 온보딩 완료 비용을 함께 본 뒤 결정한다.
- `Horizon=90일`: Rating 몰아넣기와 기간별 표본을 본 뒤 시간 기반과 이벤트 기반 중 선택한다.
- 기간 안에 추가 Rating이 0개라는 사실은 이탈·미기록·비선호를 구분하지 못하므로 실패 정답으로 취급하지 않는다.
- `Candidate=500`: Popularity/ALS/Feature/Embedding/Hybrid의 `Recall@N` 곡선으로 선택한다.
- 한국 20대 성능: MovieLens에는 국가·연령이 없으므로 별도 목표 사용자 평가가 필요하다.
- 한국 영화 구간: 전수 TMDB production-country 캐시가 없으므로 이 감사에는 포함하지 않았다.

## 다음 실험 입력

1. 후보 N `{', '.join(map(str, CANDIDATE_N_GRID))}` 전부에서 다중-positive Recall/HitRate를 계산한다.
2. 시간 Horizon과 다음-event Horizon을 모두 보고 Rating timestamp의 몰아넣기 민감도를 공개한다.
3. TMDB 전수 또는 검증된 한국 영화 ID 집합을 만든 뒤 KR 선호 cohort를 Train 이력만으로 정의한다.
4. Popularity → ALS → 구조 Feature → Text Embedding → RRF Hybrid 순서로 비교한다.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--movies", type=Path, required=True)
    parser.add_argument("--links", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--dataset-end-iso", default=DATASET_END_ISO)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for path in (args.ratings, args.movies, args.links):
        if not path.exists():
            raise FileNotFoundError(path)
    dataset_end = datetime.fromisoformat(args.dataset_end_iso)
    if dataset_end.tzinfo is None:
        raise ValueError("dataset-end-iso must include timezone")
    dataset_end_timestamp = int(dataset_end.timestamp())
    release_years, movie_summary = load_movies(args.movies)
    link_summary = load_link_summary(args.links)
    ratings = stream_ratings(args.ratings, release_years, dataset_end_timestamp)
    first_rating_timestamp = ratings["movie_interactions"].pop("first_rating_timestamp_by_movie")
    ratings["movie_interactions"].pop("rating_count_by_movie")
    result = {
        "schema_version": 1,
        "audit_id": "MOVIELENS_TEMPORAL_FEASIBILITY_V1",
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "source": {
            "ratings": {"path": str(args.ratings.resolve()), "bytes": args.ratings.stat().st_size},
            "movies": {"path": str(args.movies.resolve()), "bytes": args.movies.stat().st_size, "sha256": sha256(args.movies)},
            "links": {"path": str(args.links.resolve()), "bytes": args.links.stat().st_size, "sha256": sha256(args.links)},
            "dataset_end_iso": args.dataset_end_iso,
        },
        "protocol": {
            "analysis_role": "FULL_DATASET_DESCRIPTIVE_FEASIBILITY_ONLY_NOT_FINAL_TEST",
            "k_values": list(K_VALUES),
            "time_horizon_days": list(TIME_HORIZON_DAYS),
            "event_horizons": list(EVENT_HORIZONS),
            "candidate_n_grid_for_followup": list(CANDIDATE_N_GRID),
            "strict_future": "timestamp > Kth rating timestamp; equal timestamp rows excluded",
            "conservative_future": "rating UTC day > Kth rating UTC day; profile-boundary day excluded",
            "positive_primary": "past-K midrank utility >= 0.7",
            "positive_sensitivity": "raw rating >= 4.0",
            "unrated_semantics": "UNKNOWN_NOT_NEGATIVE",
            "selection_rule": "Choose K/horizon/N on train-validation only; reserve a later untouched test",
        },
        "movies": movie_summary,
        "links": link_summary,
        "ratings": ratings,
        "catalog_snapshots": catalog_snapshots(release_years, first_rating_timestamp),
        "korean_target_status": {
            "status": "NOT_EVALUATED_NO_FULL_TMDB_PRODUCTION_COUNTRY_CACHE",
            "reason": "MovieLens has no user country/age; links.csv provides TMDB IDs but not production country",
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_markdown.write_text(build_markdown(result), encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "users": ratings["users"],
        "ratings": ratings["rating_rows"],
        "output_json": str(args.output_json),
        "output_markdown": str(args.output_markdown),
        "runtime_seconds": ratings["runtime_seconds"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
