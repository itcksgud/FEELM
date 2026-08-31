#!/usr/bin/env python3
"""Build a Korean-origin TMDB proxy set and audit its MovieLens coverage.

The TMDB Discover endpoint is partitioned by primary-release year because a
single ``with_origin_country=KR`` query currently exceeds the API's paginated
result window. The resulting set is a reproducible proxy, not a declaration of
national identity and not evidence about Korean users' age or nationality.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


TMDB_BASE_URL = "https://api.themoviedb.org/3"
YEAR_PATTERN = re.compile(r"\((\d{4})\)\s*$")
V3_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
SECONDS_PER_DAY = 86_400
PROGRESS_INTERVAL = 5_000_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--movies", type=Path, required=True)
    parser.add_argument("--links", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--through-date", type=date.fromisoformat, default=date(2023, 10, 13))
    parser.add_argument("--start-year", type=int, default=1870)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def load_token(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("TMDB_READ_ACCESS_TOKEN", "TMDB_API_TOKEN"):
        if values.get(key):
            return values[key]
    raise RuntimeError("TMDB_READ_ACCESS_TOKEN or TMDB_API_TOKEN is missing")


def authenticated_request(url: str, token: str) -> urllib.request.Request:
    headers = {
        "Accept": "application/json",
        "User-Agent": "FEELM-Korean-Origin-Audit/1.0",
    }
    if V3_KEY_PATTERN.fullmatch(token):
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}api_key={urllib.parse.quote(token)}"
    else:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def request_json(url: str, token: str, retries: int = 6) -> dict[str, Any]:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(authenticated_request(url, token), timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise RuntimeError(f"TMDB authentication rejected with HTTP {error.code}") from error
            if error.code == 429 or 500 <= error.code < 600:
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else min(2**attempt, 10)
                time.sleep(delay)
                continue
            raise RuntimeError(f"TMDB request failed with HTTP {error.code}") from error
        except (TimeoutError, urllib.error.URLError) as error:
            if attempt + 1 == retries:
                raise RuntimeError("TMDB network retries exhausted") from error
            time.sleep(min(2**attempt, 10))
    raise RuntimeError("TMDB request retries exhausted")


def discover_url(year: int, through_date: date, page: int) -> str:
    upper = min(through_date, date(year, 12, 31))
    query = urllib.parse.urlencode(
        {
            "include_adult": "false",
            "include_video": "false",
            "language": "ko-KR",
            "page": page,
            "primary_release_date.gte": f"{year:04d}-01-01",
            "primary_release_date.lte": upper.isoformat(),
            "sort_by": "primary_release_date.asc",
            "with_origin_country": "KR",
        }
    )
    return f"{TMDB_BASE_URL}/discover/movie?{query}"


def normalize_discover_movie(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "tmdb_id": int(payload["id"]),
        "title": payload.get("title"),
        "original_title": payload.get("original_title"),
        "original_language": payload.get("original_language"),
        "release_date": payload.get("release_date"),
        "vote_count": int(payload.get("vote_count") or 0),
        "popularity": payload.get("popularity") or 0,
    }


def fetch_discover_set(
    token: str, start_year: int, through_date: date, workers: int
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    first_pages: list[tuple[int, dict[str, Any]]] = []
    page_tasks: list[tuple[int, int]] = []
    year_summaries: dict[str, dict[str, int]] = {}
    for year in range(start_year, through_date.year + 1):
        payload = request_json(discover_url(year, through_date, 1), token)
        total_pages = int(payload.get("total_pages") or 0)
        total_results = int(payload.get("total_results") or 0)
        if total_pages > 500:
            raise RuntimeError(f"TMDB year {year} exceeds 500 pages; partition by month")
        first_pages.append((year, payload))
        page_tasks.extend((year, page) for page in range(2, total_pages + 1))
        year_summaries[str(year)] = {
            "reported_results": total_results,
            "reported_pages": total_pages,
        }
        if year % 20 == 0:
            print(f"discover_first_pages_through_year={year}", flush=True)

    movies: dict[int, dict[str, Any]] = {}
    raw_rows = 0

    def add_payload(payload: dict[str, Any]) -> None:
        nonlocal raw_rows
        for item in payload.get("results") or []:
            raw_rows += 1
            normalized = normalize_discover_movie(item)
            movies[normalized["tmdb_id"]] = normalized

    for _, payload in first_pages:
        add_payload(payload)

    def fetch_page(task: tuple[int, int]) -> dict[str, Any]:
        year, page = task
        return request_json(discover_url(year, through_date, page), token)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for index, payload in enumerate(executor.map(fetch_page, page_tasks), start=1):
            add_payload(payload)
            if index % 100 == 0:
                print(
                    f"discover_additional_pages={index:,}/{len(page_tasks):,} "
                    f"unique_movies={len(movies):,}",
                    flush=True,
                )

    return movies, {
        "endpoint": "/discover/movie",
        "filter": "with_origin_country=KR",
        "date_field": "primary_release_date",
        "date_range": [f"{start_year:04d}-01-01", through_date.isoformat()],
        "reported_results_sum": sum(v["reported_results"] for v in year_summaries.values()),
        "raw_result_rows": raw_rows,
        "unique_tmdb_movies": len(movies),
        "duplicate_rows": raw_rows - len(movies),
        "year_summaries": year_summaries,
        "runtime_seconds": round(time.perf_counter() - started, 3),
    }


def parse_release_year(title: str) -> int | None:
    match = YEAR_PATTERN.search(title)
    return int(match.group(1)) if match else None


def load_movielens(
    movies_path: Path, links_path: Path
) -> tuple[dict[int, dict[str, Any]], dict[int, list[int]], dict[str, int]]:
    movies: dict[int, dict[str, Any]] = {}
    with movies_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            movie_id = int(row["movieId"])
            movies[movie_id] = {
                "movie_id": movie_id,
                "title": row["title"],
                "release_year": parse_release_year(row["title"]),
                "genres": row["genres"],
                "tmdb_id": None,
            }

    tmdb_to_movie_ids: dict[int, list[int]] = {}
    mapped = 0
    with links_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            movie_id = int(row["movieId"])
            tmdb_text = row.get("tmdbId", "").strip()
            if not tmdb_text:
                continue
            tmdb_id = int(tmdb_text)
            movies[movie_id]["tmdb_id"] = tmdb_id
            tmdb_to_movie_ids.setdefault(tmdb_id, []).append(movie_id)
            mapped += 1
    return movies, tmdb_to_movie_ids, {
        "movies": len(movies),
        "tmdb_mapped_movies": mapped,
        "tmdb_unmapped_movies": len(movies) - mapped,
    }


def histogram_quantiles(histogram: Counter[int], quantiles: Sequence[float]) -> dict[str, int | None]:
    total = sum(histogram.values())
    if not total:
        return {f"p{int(q * 100):02d}": None for q in quantiles}
    ordered = sorted(histogram.items())
    result: dict[str, int | None] = {}
    for quantile in quantiles:
        target = max(1, math.ceil(total * quantile))
        cumulative = 0
        for value, count in ordered:
            cumulative += count
            if cumulative >= target:
                result[f"p{int(quantile * 100):02d}"] = value
                break
    return result


def interaction_tier(count: int) -> str:
    if count == 0:
        return "zero"
    if count < 10:
        return "one_to_nine"
    if count < 100:
        return "ten_to_ninety_nine"
    if count < 1_000:
        return "one_hundred_to_999"
    if count < 10_000:
        return "one_thousand_to_9999"
    return "ten_thousand_plus"


def scan_ratings(path: Path, korean_movie_ids: set[int]) -> tuple[dict[str, Any], Counter[int]]:
    started = time.perf_counter()
    rating_rows = korean_rating_rows = 0
    users = users_with_korean = 0
    current_user: int | None = None
    user_total = user_korean = 0
    korean_movie_counts: Counter[int] = Counter()
    korean_count_histogram: Counter[int] = Counter()
    korean_rating_years: Counter[int] = Counter()
    rating_values: Counter[str] = Counter()
    user_thresholds = Counter()
    share_thresholds = Counter()

    def finish_user() -> None:
        nonlocal users, users_with_korean, user_total, user_korean
        if current_user is None:
            return
        users += 1
        if user_korean:
            users_with_korean += 1
            korean_count_histogram[user_korean] += 1
        for threshold in (1, 3, 5, 10, 20, 25):
            user_thresholds[str(threshold)] += user_korean >= threshold
        for percent in (10, 20, 50, 80):
            share_thresholds[str(percent)] += user_korean * 100 >= user_total * percent
        user_total = user_korean = 0

    with path.open("r", encoding="utf-8", newline="") as handle:
        header = handle.readline().strip()
        if header != "userId,movieId,rating,timestamp":
            raise RuntimeError(f"unexpected ratings header: {header}")
        for line in handle:
            user_text, movie_text, rating_text, timestamp_text = line.rstrip("\r\n").split(",")
            user_id = int(user_text)
            if current_user is not None and user_id < current_user:
                raise RuntimeError("ratings.csv must be grouped by ascending userId")
            if current_user is not None and user_id != current_user:
                finish_user()
            current_user = user_id
            movie_id = int(movie_text)
            rating_rows += 1
            user_total += 1
            if movie_id in korean_movie_ids:
                korean_rating_rows += 1
                user_korean += 1
                korean_movie_counts[movie_id] += 1
                rating_values[rating_text] += 1
                korean_rating_years[
                    datetime.fromtimestamp(int(timestamp_text), tz=timezone.utc).year
                ] += 1
            if rating_rows % PROGRESS_INTERVAL == 0:
                print(
                    f"ratings_processed={rating_rows:,} korean_ratings={korean_rating_rows:,} "
                    f"elapsed_seconds={time.perf_counter() - started:.1f}",
                    flush=True,
                )
        finish_user()

    return {
        "rating_rows": rating_rows,
        "korean_origin_rating_rows": korean_rating_rows,
        "korean_origin_rating_share": round(korean_rating_rows / rating_rows, 6),
        "users": users,
        "users_with_korean_origin_rating": users_with_korean,
        "users_with_korean_origin_rating_share": round(users_with_korean / users, 6),
        "users_with_at_least_korean_ratings": dict(user_thresholds),
        "users_with_korean_rating_share_at_least_percent": dict(share_thresholds),
        "korean_rating_count_quantiles_among_users_with_any": histogram_quantiles(
            korean_count_histogram, (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)
        ),
        "korean_rating_value_counts": dict(sorted(rating_values.items(), key=lambda item: float(item[0]))),
        "korean_rating_entry_year_counts": dict(sorted(korean_rating_years.items())),
        "runtime_seconds": round(time.perf_counter() - started, 3),
    }, korean_movie_counts


def percent(count: int, total: int) -> float | None:
    return round(count / total, 6) if total else None


def percent_text(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def build_markdown(result: dict[str, Any]) -> str:
    discover = result["tmdb_discover"]
    ml = result["movielens"]
    ratings = result["ratings"]
    thresholds = ratings["users_with_at_least_korean_ratings"]
    tier_rows = [
        (tier, f"{count:,}", percent(count, ml["matched_movie_count"]))
        for tier, count in ml["matched_interaction_tiers"].items()
    ]
    return f"""# TMDB 한국-origin · MovieLens 교차 감사

> 상태: `COMPLETED_PROXY_AUDIT`
> 생성 시각: {result['generated_at_utc']}
> 기준: TMDB Discover `with_origin_country=KR`, primary release date {discover['date_range'][0]}~{discover['date_range'][1]}

## 결론

이 감사는 한국 영화에 대한 MovieLens 표본이 학습·검증에 어느 정도 존재하는지 확인한다.
`with_origin_country=KR`는 TMDB의 검색 필터로 만든 proxy이며 작품의 국적을 법적·문화적으로 확정하지 않는다.
MovieLens에는 사용자 국가와 나이가 없으므로 아래 숫자로 **한국 20대 사용자 성능**을 주장할 수 없다.

- TMDB 한국-origin proxy: {discover['unique_tmdb_movies']:,}편
- MovieLens와 TMDB ID가 교차되는 영화: {ml['matched_movie_count']:,}편 ({percent_text(ml['matched_movie_share'])})
- 해당 영화 Rating: {ratings['korean_origin_rating_rows']:,}개 ({percent_text(ratings['korean_origin_rating_share'])})
- 해당 영화를 1편 이상 평가한 사용자: {ratings['users_with_korean_origin_rating']:,}명 ({percent_text(ratings['users_with_korean_origin_rating_share'])})
- 한국-origin Rating 5개 이상 사용자: {thresholds['5']:,}명
- 한국-origin Rating 10개 이상 사용자: {thresholds['10']:,}명
- 한국-origin Rating 25개 이상 사용자: {thresholds['25']:,}명

## 영화별 상호작용 밀도

{markdown_table(['Rating 구간', '영화 수', '교차 영화 내 비율'], [(tier, count, percent_text(share)) for tier, count, share in tier_rows])}

## 해석과 사용 제한

1. 전체 모델 학습에는 한국-origin 영화를 포함하되, 작은 집단을 전체 Train/Validation/Test에 중복 사용하지 않는다.
2. 한국 영화 slice는 사용자 기준으로 먼저 분리한 뒤 Train 이력만으로 cohort를 정의한다.
3. 한국-origin Rating 1개만 있는 사용자를 한국 영화 선호 사용자로 부르지 않는다. 5/10/25개 기준을 민감도 분석한다.
4. 한국 20대 검증은 별도의 실제 사용자 평가 또는 서비스 이벤트가 생기기 전까지 `NOT_EVALUATED`다.
5. 이 결과는 2026년 TMDB 상태를 2023-10-13까지의 primary release date로 조회한 회고적 proxy다.
"""


def main() -> int:
    args = parse_args()
    for path in (args.ratings, args.movies, args.links, args.env_file):
        if not path.exists():
            raise FileNotFoundError(path)
    if args.workers < 1 or args.workers > 16:
        raise ValueError("workers must be between 1 and 16")
    if args.start_year > args.through_date.year:
        raise ValueError("start-year must not exceed through-date year")

    token = load_token(args.env_file)
    discovered, discover_summary = fetch_discover_set(
        token, args.start_year, args.through_date, args.workers
    )
    movies, tmdb_to_movie_ids, movielens_summary = load_movielens(args.movies, args.links)
    matched_movie_ids = {
        movie_id
        for tmdb_id in discovered
        for movie_id in tmdb_to_movie_ids.get(tmdb_id, [])
    }
    ratings_summary, korean_movie_counts = scan_ratings(args.ratings, matched_movie_ids)

    interaction_tiers = Counter(
        interaction_tier(korean_movie_counts[movie_id]) for movie_id in matched_movie_ids
    )
    matched_items = []
    for movie_id in sorted(matched_movie_ids):
        item = movies[movie_id]
        tmdb = discovered[int(item["tmdb_id"])]
        matched_items.append(
            {
                "movie_id": movie_id,
                "tmdb_id": item["tmdb_id"],
                "movielens_title": item["title"],
                "movielens_release_year": item["release_year"],
                "tmdb_title": tmdb["title"],
                "tmdb_original_title": tmdb["original_title"],
                "tmdb_original_language": tmdb["original_language"],
                "tmdb_release_date": tmdb["release_date"],
                "rating_count": korean_movie_counts[movie_id],
                "interaction_tier": interaction_tier(korean_movie_counts[movie_id]),
            }
        )

    result = {
        "schema_version": 1,
        "audit_id": "TMDB_KOREAN_ORIGIN_MOVIELENS_V1",
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "protocol": {
            "target": "KOREAN_ORIGIN_MOVIE_PROXY_NOT_KOREAN_USER_DEMOGRAPHIC",
            "tmdb_filter": "with_origin_country=KR",
            "through_date": args.through_date.isoformat(),
            "movielens_user_country_age": "UNAVAILABLE",
            "cohort_rule_for_future_models": "define from train history only; evaluate by user-disjoint slice",
        },
        "tmdb_discover": {
            **discover_summary,
            "tmdb_ids": sorted(discovered),
        },
        "movielens": {
            **movielens_summary,
            "matched_movie_count": len(matched_movie_ids),
            "matched_movie_share": percent(len(matched_movie_ids), movielens_summary["movies"]),
            "matched_interaction_tiers": {
                tier: interaction_tiers[tier]
                for tier in (
                    "zero",
                    "one_to_nine",
                    "ten_to_ninety_nine",
                    "one_hundred_to_999",
                    "one_thousand_to_9999",
                    "ten_thousand_plus",
                )
            },
            "matched_items": matched_items,
        },
        "ratings": ratings_summary,
        "korean_20s_target_status": {
            "status": "NOT_EVALUATED_NO_MOVIELENS_DEMOGRAPHICS_OR_TARGET_USER_LABELS",
            "required_evidence": "target-user study or real FEELM events with consented aggregate cohort reporting",
        },
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_markdown.write_text(build_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "PASS",
                "tmdb_korean_origin_proxy": len(discovered),
                "movielens_matched_movies": len(matched_movie_ids),
                "korean_origin_ratings": ratings_summary["korean_origin_rating_rows"],
                "output_json": str(args.output_json),
                "output_markdown": str(args.output_markdown),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
