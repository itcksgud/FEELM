#!/usr/bin/env python3
"""Estimate MovieLens coverage of foreign films recognizable in Korea.

TMDB does not publish a Korea-specific awareness score. This audit therefore
reports nested proxies that combine MovieLens interaction head thresholds with
Korean localization and Korean release/provider evidence. The proxy must not be
renamed to actual Korean awareness without target-user or Korean market data.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from tmdb_korean_origin_audit import authenticated_request, load_token, parse_release_year


TMDB_BASE_URL = "https://api.themoviedb.org/3"
APPENDED_ENDPOINTS = "translations,release_dates,watch/providers"
PROGRESS_INTERVAL = 500
RATING_PROGRESS_INTERVAL = 5_000_000
PROXY_NAMES = (
    "KOREAN_ORIGIN",
    "FOREIGN_BROAD",
    "FOREIGN_MODERATE",
    "FOREIGN_STRICT",
    "EXPANDED_BROAD",
    "EXPANDED_MODERATE",
    "EXPANDED_STRICT",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--movies", type=Path, required=True)
    parser.add_argument("--links", type=Path, required=True)
    parser.add_argument("--korean-origin-json", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--head-min-ratings", type=int, default=100)
    parser.add_argument("--strict-min-ratings", type=int, default=1000)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_movies_and_links(
    movies_path: Path, links_path: Path
) -> tuple[dict[int, dict[str, Any]], dict[int, int], dict[int, int]]:
    movies: dict[int, dict[str, Any]] = {}
    with movies_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            movie_id = int(row["movieId"])
            movies[movie_id] = {
                "movie_id": movie_id,
                "title": row["title"],
                "release_year": parse_release_year(row["title"]),
                "genres": row["genres"],
            }

    movie_to_tmdb: dict[int, int] = {}
    tmdb_to_movie: dict[int, int] = {}
    with links_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            tmdb_text = row.get("tmdbId", "").strip()
            if not tmdb_text:
                continue
            movie_id = int(row["movieId"])
            tmdb_id = int(tmdb_text)
            movie_to_tmdb[movie_id] = tmdb_id
            tmdb_to_movie.setdefault(tmdb_id, movie_id)
    return movies, movie_to_tmdb, tmdb_to_movie


def scan_movie_rating_counts(path: Path) -> tuple[Counter[int], int]:
    started = time.perf_counter()
    counts: Counter[int] = Counter()
    rows = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        header = handle.readline().strip()
        if header != "userId,movieId,rating,timestamp":
            raise RuntimeError(f"unexpected ratings header: {header}")
        for line in handle:
            _, movie_text, _, _ = line.rstrip("\r\n").split(",")
            counts[int(movie_text)] += 1
            rows += 1
            if rows % RATING_PROGRESS_INTERVAL == 0:
                print(
                    f"count_scan_rows={rows:,} elapsed_seconds={time.perf_counter() - started:.1f}",
                    flush=True,
                )
    return counts, rows


def movie_url(tmdb_id: int) -> str:
    query = urllib.parse.urlencode(
        {
            "append_to_response": APPENDED_ENDPOINTS,
            "language": "ko-KR",
        }
    )
    return f"{TMDB_BASE_URL}/movie/{tmdb_id}?{query}"


def request_json(url: str, token: str, retries: int = 6) -> dict[str, Any]:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(authenticated_request(url, token), timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise RuntimeError(f"TMDB authentication rejected with HTTP {error.code}") from error
            if error.code == 404:
                raise LookupError("TMDB movie not found") from error
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


def translation_has_title(payload: dict[str, Any], language: str) -> bool:
    translations = (payload.get("translations") or {}).get("translations") or []
    return any(
        translation.get("iso_639_1") == language
        and bool(((translation.get("data") or {}).get("title") or "").strip())
        for translation in translations
    )


def normalize_tmdb(payload: dict[str, Any]) -> dict[str, Any]:
    countries = sorted(
        {
            country.get("iso_3166_1")
            for country in payload.get("production_countries") or []
            if country.get("iso_3166_1")
        }
    )
    release_results = (payload.get("release_dates") or {}).get("results") or []
    kr_release = next(
        (row for row in release_results if row.get("iso_3166_1") == "KR"),
        None,
    )
    kr_release_dates = (kr_release or {}).get("release_dates") or []
    release_types = sorted(
        {
            int(row["type"])
            for row in kr_release_dates
            if isinstance(row.get("type"), int)
        }
    )
    provider_results = (payload.get("watch/providers") or {}).get("results") or {}
    kr_providers = provider_results.get("KR") or {}
    provider_kinds = ["flatrate", "free", "ads", "rent", "buy"]
    provider_names = sorted(
        {
            provider.get("provider_name")
            for kind in provider_kinds
            for provider in kr_providers.get(kind) or []
            if provider.get("provider_name")
        }
    )
    return {
        "status": "OK",
        "tmdb_id": int(payload["id"]),
        "title_ko_response": payload.get("title"),
        "original_title": payload.get("original_title"),
        "original_language": payload.get("original_language"),
        "production_countries": countries,
        "has_korean_title_translation": translation_has_title(payload, "ko"),
        "has_korean_release": bool(kr_release_dates),
        "has_korean_theatrical_release": any(value in (2, 3) for value in release_types),
        "korean_release_types": release_types,
        "has_current_korean_provider": bool(provider_names),
        "has_current_korean_flatrate": bool(kr_providers.get("flatrate") or []),
        "korean_provider_names": provider_names,
        "tmdb_vote_count": int(payload.get("vote_count") or 0),
        "tmdb_popularity": payload.get("popularity") or 0,
    }


def cache_path(cache_dir: Path, tmdb_id: int) -> Path:
    return cache_dir / f"{tmdb_id}.json"


def fetch_or_load(cache_dir: Path, tmdb_id: int, token: str) -> dict[str, Any]:
    path = cache_path(cache_dir, tmdb_id)
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("status") != "ERROR":
            return cached
    try:
        result = normalize_tmdb(request_json(movie_url(tmdb_id), token))
    except LookupError:
        result = {"status": "NOT_FOUND", "tmdb_id": tmdb_id}
    except Exception as error:
        result = {"status": "ERROR", "tmdb_id": tmdb_id, "error": str(error)}
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return result


def classify_foreign_proxy(
    row: dict[str, Any], head_min_ratings: int, strict_min_ratings: int
) -> set[str]:
    if row.get("status") != "OK" or not row.get("is_foreign"):
        return set()
    rating_count = int(row["rating_count"])
    korean_title = bool(row.get("has_korean_title_translation"))
    market_evidence = bool(row.get("has_korean_release") or row.get("has_current_korean_provider"))
    strict_market_evidence = bool(
        row.get("has_korean_theatrical_release") or row.get("has_current_korean_provider")
    )
    names: set[str] = set()
    if rating_count >= head_min_ratings and korean_title:
        names.add("FOREIGN_BROAD")
    if rating_count >= head_min_ratings and korean_title and market_evidence:
        names.add("FOREIGN_MODERATE")
    if rating_count >= strict_min_ratings and korean_title and strict_market_evidence:
        names.add("FOREIGN_STRICT")
    return names


def build_proxy_sets(
    metadata_rows: list[dict[str, Any]],
    korean_origin_movie_ids: set[int],
    head_min_ratings: int,
    strict_min_ratings: int,
) -> dict[str, set[int]]:
    sets = {name: set() for name in PROXY_NAMES}
    sets["KOREAN_ORIGIN"] = set(korean_origin_movie_ids)
    for row in metadata_rows:
        if row.get("is_korean_origin"):
            sets["KOREAN_ORIGIN"].add(int(row["movie_id"]))
        for name in classify_foreign_proxy(row, head_min_ratings, strict_min_ratings):
            sets[name].add(int(row["movie_id"]))
    for suffix in ("BROAD", "MODERATE", "STRICT"):
        sets[f"EXPANDED_{suffix}"] = sets["KOREAN_ORIGIN"] | sets[f"FOREIGN_{suffix}"]
    return sets


def scan_user_coverage(
    path: Path, proxy_sets: dict[str, set[int]]
) -> tuple[dict[str, dict[str, Any]], int]:
    movie_membership: dict[int, list[str]] = {}
    for name, movie_ids in proxy_sets.items():
        for movie_id in movie_ids:
            movie_membership.setdefault(movie_id, []).append(name)

    users = 0
    rows = 0
    current_user: int | None = None
    current_counts = {name: 0 for name in PROXY_NAMES}
    users_at_least = {name: Counter() for name in PROXY_NAMES}
    rating_rows = Counter()
    started = time.perf_counter()

    def finish_user() -> None:
        nonlocal users
        if current_user is None:
            return
        users += 1
        for name in PROXY_NAMES:
            count = current_counts[name]
            for threshold in (1, 5, 10, 25):
                users_at_least[name][str(threshold)] += count >= threshold
            current_counts[name] = 0

    with path.open("r", encoding="utf-8", newline="") as handle:
        handle.readline()
        for line in handle:
            user_text, movie_text, _, _ = line.rstrip("\r\n").split(",")
            user_id = int(user_text)
            if current_user is not None and user_id < current_user:
                raise RuntimeError("ratings.csv must be grouped by ascending userId")
            if current_user is not None and user_id != current_user:
                finish_user()
            current_user = user_id
            movie_id = int(movie_text)
            rows += 1
            for name in movie_membership.get(movie_id, ()):
                current_counts[name] += 1
                rating_rows[name] += 1
            if rows % RATING_PROGRESS_INTERVAL == 0:
                print(
                    f"coverage_scan_rows={rows:,} elapsed_seconds={time.perf_counter() - started:.1f}",
                    flush=True,
                )
        finish_user()

    summary = {
        name: {
            "movies": len(proxy_sets[name]),
            "rating_rows": rating_rows[name],
            "rating_share": round(rating_rows[name] / rows, 6),
            "users_with_at_least_ratings": dict(users_at_least[name]),
            "user_share_with_any": round(users_at_least[name]["1"] / users, 6),
        }
        for name in PROXY_NAMES
    }
    return summary, users


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
    coverage = result["coverage"]
    rows = []
    for name in PROXY_NAMES:
        value = coverage[name]
        users_at_least = value["users_with_at_least_ratings"]
        rows.append(
            (
                name,
                f"{value['movies']:,}",
                f"{value['rating_rows']:,}",
                percent_text(value["rating_share"]),
                f"{users_at_least['1']:,}",
                f"{users_at_least['5']:,}",
                f"{users_at_least['10']:,}",
                f"{users_at_least['25']:,}",
            )
        )
    fetch = result["tmdb_fetch"]
    return f"""# 한국 시장 인지 가능 외국 영화 proxy 감사

> 상태: `COMPLETED_PROXY_AUDIT_NOT_ACTUAL_AWARENESS`
> 생성 시각: {result['generated_at_utc']}
> TMDB head 조회: 성공 {fetch['status_counts'].get('OK', 0):,}, 404 {fetch['status_counts'].get('NOT_FOUND', 0):,}, 오류 {fetch['status_counts'].get('ERROR', 0):,}

## 결론

한국 영화만 세는 `0.284%`는 한국 사용자가 소비할 수 있는 전체 취향 공간을 뜻하지 않는다.
MovieLens head 영화와 한국어 현지화·한국 개봉·현재 한국 제공 신호를 합치면 훨씬 넓은 proxy를 만들 수 있다.
다만 TMDB에는 한국 내 실제 인지도 점수가 없으므로 아래 결과는 실제 한국 20대 인지율이 아니다.

## proxy 정의

- `KOREAN_ORIGIN`: 기존 TMDB `with_origin_country=KR` 교차 집합
- `FOREIGN_BROAD`: 외국 영화 + MovieLens Rating {result['protocol']['head_min_ratings']}개 이상 + 한국어 제목
- `FOREIGN_MODERATE`: BROAD + 한국 개봉 기록 또는 현재 한국 provider
- `FOREIGN_STRICT`: 외국 영화 + MovieLens Rating {result['protocol']['strict_min_ratings']}개 이상 + 한국어 제목 + 한국 극장 개봉 또는 현재 한국 provider
- `EXPANDED_*`: KOREAN_ORIGIN과 해당 FOREIGN 집합의 합집합

{markdown_table(['집합', '영화', 'Rating', '전체 Rating 비율', '사용자≥1', '사용자≥5', '사용자≥10', '사용자≥25'], rows)}

## 해석 제한

1. MovieLens Rating 수는 전 세계 표본의 인기도이며 한국 인기도가 아니다.
2. 한국어 번역은 인지도의 필요 신호일 수 있지만 충분조건이 아니다.
3. provider는 현재 스냅샷이며 과거 한국 흥행을 대신하지 않는다.
4. 실제 한국 인지도 검증에는 KOBIS 흥행·국내 검색/관심도·한국 20대 사용자 조사가 추가로 필요하다.
5. 모델 평가는 `EXPANDED_MODERATE`를 기본 slice 후보로, BROAD/STRICT를 민감도 구간으로 사용한다.
"""


def main() -> int:
    args = parse_args()
    for path in (
        args.ratings,
        args.movies,
        args.links,
        args.korean_origin_json,
        args.env_file,
    ):
        if not path.exists():
            raise FileNotFoundError(path)
    if args.workers < 1 or args.workers > 16:
        raise ValueError("workers must be between 1 and 16")
    if args.head_min_ratings < 1 or args.strict_min_ratings < args.head_min_ratings:
        raise ValueError("rating thresholds are invalid")

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    movies, movie_to_tmdb, _ = load_movies_and_links(args.movies, args.links)
    rating_counts, rating_rows = scan_movie_rating_counts(args.ratings)
    korean_origin = json.loads(args.korean_origin_json.read_text(encoding="utf-8"))
    korean_origin_tmdb_ids = set(korean_origin["tmdb_discover"]["tmdb_ids"])
    korean_origin_movie_ids = {
        int(item["movie_id"]) for item in korean_origin["movielens"]["matched_items"]
    }

    head_movie_ids = sorted(
        movie_id
        for movie_id, count in rating_counts.items()
        if count >= args.head_min_ratings and movie_id in movie_to_tmdb
    )
    token = load_token(args.env_file)
    started = time.perf_counter()

    def fetch(movie_id: int) -> dict[str, Any]:
        tmdb_id = movie_to_tmdb[movie_id]
        row = fetch_or_load(args.cache_dir, tmdb_id, token)
        countries = set(row.get("production_countries") or [])
        is_korean_origin = tmdb_id in korean_origin_tmdb_ids or "KR" in countries
        return {
            **row,
            "movie_id": movie_id,
            "movielens_title": movies[movie_id]["title"],
            "movielens_release_year": movies[movie_id]["release_year"],
            "rating_count": rating_counts[movie_id],
            "is_korean_origin": is_korean_origin,
            "is_foreign": bool(countries) and not is_korean_origin,
            "production_country_known": bool(countries),
        }

    metadata_rows: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(fetch, movie_id): movie_id for movie_id in head_movie_ids}
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            metadata_rows.append(future.result())
            if index % PROGRESS_INTERVAL == 0:
                cache_hits = sum(cache_path(args.cache_dir, movie_to_tmdb[movie_id]).exists() for movie_id in head_movie_ids)
                print(
                    f"tmdb_head_processed={index:,}/{len(head_movie_ids):,} cache_files={cache_hits:,} "
                    f"elapsed_seconds={time.perf_counter() - started:.1f}",
                    flush=True,
                )
    metadata_rows.sort(key=lambda row: int(row["movie_id"]))

    proxy_sets = build_proxy_sets(
        metadata_rows,
        korean_origin_movie_ids,
        args.head_min_ratings,
        args.strict_min_ratings,
    )
    coverage, users = scan_user_coverage(args.ratings, proxy_sets)
    status_counts = Counter(row["status"] for row in metadata_rows)
    country_unknown = sum(
        row.get("status") == "OK" and not row.get("production_country_known")
        for row in metadata_rows
    )
    result = {
        "schema_version": 1,
        "audit_id": "TMDB_KOREAN_MARKET_AWARENESS_PROXY_V1",
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "protocol": {
            "claim_boundary": "PROXY_NOT_ACTUAL_KOREAN_AWARENESS_OR_KOREAN_20S_PERFORMANCE",
            "head_min_ratings": args.head_min_ratings,
            "strict_min_ratings": args.strict_min_ratings,
            "foreign_definition": "production countries known, KR absent, and not in KR-origin discover set",
            "broad": "foreign AND MovieLens ratings>=head threshold AND Korean title translation",
            "moderate": "broad AND (KR release record OR current KR provider)",
            "strict": "foreign AND MovieLens ratings>=strict threshold AND Korean title AND (KR theatrical release OR current KR provider)",
            "provider_snapshot": "CURRENT_AT_AUDIT_TIME",
        },
        "source": {
            "ratings": {"path": str(args.ratings.resolve()), "bytes": args.ratings.stat().st_size},
            "movies": {"path": str(args.movies.resolve()), "sha256": sha256(args.movies)},
            "links": {"path": str(args.links.resolve()), "sha256": sha256(args.links)},
            "korean_origin_json": {
                "path": str(args.korean_origin_json.resolve()),
                "sha256": sha256(args.korean_origin_json),
            },
            "tmdb_endpoint": "/movie/{id}?append_to_response=translations,release_dates,watch/providers",
        },
        "dataset": {
            "movies": len(movies),
            "users": users,
            "rating_rows": rating_rows,
            "head_movies_requested": len(head_movie_ids),
        },
        "tmdb_fetch": {
            "status_counts": dict(status_counts),
            "success_country_unknown": country_unknown,
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "cache_dir": str(args.cache_dir.resolve()),
        },
        "coverage": coverage,
        "head_items": metadata_rows,
        "decision": {
            "default_slice_candidate": "EXPANDED_MODERATE",
            "sensitivity_slices": ["EXPANDED_BROAD", "EXPANDED_STRICT"],
            "actual_korean_awareness_status": "NOT_EVALUATED",
            "korean_20s_status": "NOT_EVALUATED",
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
                "head_movies": len(head_movie_ids),
                "tmdb_status": dict(status_counts),
                "expanded_moderate_rating_share": coverage["EXPANDED_MODERATE"]["rating_share"],
                "output_json": str(args.output_json),
                "output_markdown": str(args.output_markdown),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
