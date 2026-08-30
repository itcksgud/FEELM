#!/usr/bin/env python3
"""Audit real TMDB metadata coverage for a deterministic MovieLens 32M sample.

The script never prints or persists the TMDB token. It reads MovieLens files
directly from the official zip, counts movie ratings, builds representative and
stress-test cohorts, calls TMDB, and writes normalized audit artifacts.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import io
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TMDB_BASE_URL = "https://api.themoviedb.org/3"
APPENDED_ENDPOINTS = (
    "credits",
    "keywords",
    "external_ids",
    "translations",
    "images",
    "watch/providers",
    "release_dates",
)
YEAR_PATTERN = re.compile(r"\((\d{4})\)\s*$")


@dataclass(frozen=True)
class Movie:
    movie_id: int
    title: str
    genres: str
    release_year: int | None
    tmdb_id: int | None
    imdb_id: str | None
    rating_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--random-size", type=int, default=400)
    parser.add_argument("--top-size", type=int, default=100)
    parser.add_argument("--per-stratum", type=int, default=8)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def zip_member(zip_file: zipfile.ZipFile, basename: str) -> str:
    matches = [name for name in zip_file.namelist() if name.endswith("/" + basename)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {basename} in archive; found {matches}")
    return matches[0]


def csv_rows(zip_file: zipfile.ZipFile, member: str) -> Iterable[dict[str, str]]:
    binary = zip_file.open(member)
    text = io.TextIOWrapper(binary, encoding="utf-8", newline="")
    try:
        yield from csv.DictReader(text)
    finally:
        text.close()


def read_movielens(archive: Path) -> tuple[list[Movie], dict[str, Any]]:
    print("Reading MovieLens 32M metadata and counting ratings...", flush=True)
    with zipfile.ZipFile(archive) as zip_file:
        movies_member = zip_member(zip_file, "movies.csv")
        links_member = zip_member(zip_file, "links.csv")
        ratings_member = zip_member(zip_file, "ratings.csv")

        movie_meta: dict[int, tuple[str, str, int | None]] = {}
        for row in csv_rows(zip_file, movies_member):
            movie_id = int(row["movieId"])
            title = row["title"]
            year_match = YEAR_PATTERN.search(title)
            year = int(year_match.group(1)) if year_match else None
            movie_meta[movie_id] = (title, row["genres"], year)

        links: dict[int, tuple[int | None, str | None]] = {}
        for row in csv_rows(zip_file, links_member):
            tmdb_text = row.get("tmdbId", "").strip()
            imdb_text = row.get("imdbId", "").strip()
            links[int(row["movieId"])] = (
                int(tmdb_text) if tmdb_text else None,
                imdb_text or None,
            )

        counts: Counter[int] = Counter()
        rating_rows = 0
        for row in csv_rows(zip_file, ratings_member):
            counts[int(row["movieId"])] += 1
            rating_rows += 1
            if rating_rows % 5_000_000 == 0:
                print(f"  counted {rating_rows:,} ratings", flush=True)

    movies = []
    for movie_id, (title, genres, year) in movie_meta.items():
        tmdb_id, imdb_id = links.get(movie_id, (None, None))
        movies.append(
            Movie(
                movie_id=movie_id,
                title=title,
                genres=genres,
                release_year=year,
                tmdb_id=tmdb_id,
                imdb_id=imdb_id,
                rating_count=counts.get(movie_id, 0),
            )
        )

    stats = {
        "movie_count": len(movies),
        "rating_rows": rating_rows,
        "tmdb_mapped": sum(movie.tmdb_id is not None for movie in movies),
        "tmdb_unmapped": sum(movie.tmdb_id is None for movie in movies),
        "imdb_mapped": sum(movie.imdb_id is not None for movie in movies),
        "movies_with_zero_ratings": sum(movie.rating_count == 0 for movie in movies),
    }
    return movies, stats


def popularity_bucket(count: int) -> str:
    if count == 0:
        return "p0_0"
    if count < 10:
        return "p1_1-9"
    if count < 100:
        return "p2_10-99"
    if count < 1_000:
        return "p3_100-999"
    if count < 10_000:
        return "p4_1000-9999"
    return "p5_10000+"


def era_bucket(year: int | None) -> str:
    if year is None:
        return "e_unknown"
    if year < 1960:
        return "e_pre1960"
    if year < 1980:
        return "e_1960s_1970s"
    if year < 1990:
        return "e_1980s"
    if year < 2000:
        return "e_1990s"
    if year < 2010:
        return "e_2000s"
    if year < 2020:
        return "e_2010s"
    return "e_2020s"


def build_sample(
    movies: list[Movie], random_size: int, top_size: int, per_stratum: int, seed: int
) -> tuple[list[Movie], dict[int, set[str]]]:
    rng = random.Random(seed)
    mapped = [movie for movie in movies if movie.tmdb_id is not None]
    cohorts: dict[int, set[str]] = defaultdict(set)

    random_sample = rng.sample(mapped, min(random_size, len(mapped)))
    for movie in random_sample:
        cohorts[movie.movie_id].add("representative_random")

    top_sample = sorted(mapped, key=lambda movie: (-movie.rating_count, movie.movie_id))[:top_size]
    for movie in top_sample:
        cohorts[movie.movie_id].add("top_rated_count")

    strata: dict[tuple[str, str], list[Movie]] = defaultdict(list)
    for movie in mapped:
        strata[(popularity_bucket(movie.rating_count), era_bucket(movie.release_year))].append(movie)
    for key in sorted(strata):
        candidates = strata[key]
        picked = rng.sample(candidates, min(per_stratum, len(candidates)))
        for movie in picked:
            cohorts[movie.movie_id].add("stratified_stress")

    movie_by_id = {movie.movie_id: movie for movie in mapped}
    sample = [movie_by_id[movie_id] for movie_id in sorted(cohorts)]
    return sample, cohorts


def request_json(url: str, token: str, retries: int = 5) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "FEELM-TMDB-Coverage-Audit/1.0",
        },
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise RuntimeError("TMDB token was rejected (401)") from error
            if error.code == 404:
                raise LookupError("TMDB movie not found") from error
            if error.code == 429 or 500 <= error.code < 600:
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else min(2**attempt, 10)
                time.sleep(delay)
                continue
            body = error.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"TMDB HTTP {error.code}: {body}") from error
        except (TimeoutError, urllib.error.URLError) as error:
            if attempt + 1 == retries:
                raise RuntimeError(f"TMDB network error: {error}") from error
            time.sleep(min(2**attempt, 10))
    raise RuntimeError("TMDB request retries exhausted")


def movie_url(tmdb_id: int) -> str:
    query = urllib.parse.urlencode(
        {
            "language": "ko-KR",
            "append_to_response": ",".join(APPENDED_ENDPOINTS),
            "include_image_language": "ko,en,null",
        }
    )
    return f"{TMDB_BASE_URL}/movie/{tmdb_id}?{query}"


def translations_by_language(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_language: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for translation in payload.get("translations", {}).get("translations", []):
        language = translation.get("iso_639_1")
        if language:
            by_language[language].append(translation.get("data") or {})
    return by_language


def has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def any_translation_text(
    translations: dict[str, list[dict[str, Any]]], language: str, field: str
) -> bool:
    return any(has_text(item.get(field)) for item in translations.get(language, []))


def normalize(movie: Movie, cohorts: set[str], payload: dict[str, Any]) -> dict[str, Any]:
    translations = translations_by_language(payload)
    credits = payload.get("credits") or {}
    crew = credits.get("crew") or []
    cast = credits.get("cast") or []
    keywords_payload = payload.get("keywords") or {}
    keywords = keywords_payload.get("keywords") or keywords_payload.get("results") or []
    external_ids = payload.get("external_ids") or {}
    images = payload.get("images") or {}
    posters = images.get("posters") or []
    providers = (payload.get("watch/providers") or {}).get("results") or {}
    kr_providers = providers.get("KR") or {}
    release_results = (payload.get("release_dates") or {}).get("results") or []
    kr_release = next(
        (item for item in release_results if item.get("iso_3166_1") == "KR"), None
    )
    kr_releases = (kr_release or {}).get("release_dates") or []

    streaming_provider_names = sorted(
        {
            provider.get("provider_name")
            for provider in (kr_providers.get("flatrate") or [])
            if provider.get("provider_name")
        }
    )
    transactional_provider_names = sorted(
        {
            provider.get("provider_name")
            for kind in ("rent", "buy")
            for provider in (kr_providers.get(kind) or [])
            if provider.get("provider_name")
        }
    )
    provider_names = sorted(
        {
            provider.get("provider_name")
            for kind in ("flatrate", "free", "ads", "rent", "buy")
            for provider in (kr_providers.get(kind) or [])
            if provider.get("provider_name")
        }
    )
    directors = sorted(
        {person.get("name") for person in crew if person.get("job") == "Director" and person.get("name")}
    )

    ko_title = any_translation_text(translations, "ko", "title")
    ko_overview = any_translation_text(translations, "ko", "overview")
    en_overview = any_translation_text(translations, "en", "overview")
    any_overview = has_text(payload.get("overview")) or any(
        any(has_text(item.get("overview")) for item in items)
        for items in translations.values()
    )

    return {
        "movie_id": movie.movie_id,
        "tmdb_id": movie.tmdb_id,
        "movielens_title": movie.title,
        "tmdb_title": payload.get("title"),
        "original_title": payload.get("original_title"),
        "release_year": movie.release_year,
        "rating_count": movie.rating_count,
        "popularity_bucket": popularity_bucket(movie.rating_count),
        "era_bucket": era_bucket(movie.release_year),
        "cohorts": "|".join(sorted(cohorts)),
        "display_title": has_text(payload.get("title")) or has_text(payload.get("original_title")),
        "ko_title": ko_title,
        "ko_overview": ko_overview,
        "en_overview": en_overview,
        "any_overview": any_overview,
        "poster": has_text(payload.get("poster_path")),
        "ko_poster": any(item.get("iso_639_1") == "ko" for item in posters),
        "backdrop": has_text(payload.get("backdrop_path")),
        "runtime": isinstance(payload.get("runtime"), int) and payload.get("runtime", 0) > 0,
        "genres": bool(payload.get("genres")),
        "production_countries": bool(payload.get("production_countries")),
        "director": bool(directors),
        "directors": "|".join(directors),
        "cast_3": len(cast) >= 3,
        "cast_count": len(cast),
        "keywords": bool(keywords),
        "keyword_count": len(keywords),
        "imdb_id": has_text(external_ids.get("imdb_id")),
        "wikidata_id": has_text(external_ids.get("wikidata_id")),
        "kr_any_offer": bool(provider_names),
        "kr_streaming": bool(streaming_provider_names),
        "kr_provider_names": "|".join(provider_names),
        "kr_streaming_provider_names": "|".join(streaming_provider_names),
        "kr_transactional_provider_names": "|".join(transactional_provider_names),
        "kr_release": bool(kr_releases),
        "kr_certification": any(has_text(item.get("certification")) for item in kr_releases),
        "vote_count": payload.get("vote_count") or 0,
        "vote_average": payload.get("vote_average") or 0,
        "original_language": payload.get("original_language"),
        "status": payload.get("status"),
    }


COVERAGE_FIELDS = (
    "display_title",
    "ko_title",
    "ko_overview",
    "en_overview",
    "any_overview",
    "poster",
    "ko_poster",
    "backdrop",
    "runtime",
    "genres",
    "production_countries",
    "director",
    "cast_3",
    "keywords",
    "imdb_id",
    "wikidata_id",
    "kr_any_offer",
    "kr_streaming",
    "kr_release",
    "kr_certification",
)


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    result: dict[str, Any] = {"sample_size": total}
    for field in COVERAGE_FIELDS:
        count = sum(bool(row[field]) for row in rows)
        result[field] = {
            "count": count,
            "rate": round(count / total, 4) if total else None,
        }
    return result


def group_coverage(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    return {name: coverage(items) for name, items in sorted(grouped.items())}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
    if not token:
        print("TMDB_READ_ACCESS_TOKEN is missing", file=sys.stderr)
        return 2
    if not args.archive.exists():
        print(f"Archive not found: {args.archive}", file=sys.stderr)
        return 2

    # Fail before the expensive MovieLens pass if authentication is invalid.
    request_json(f"{TMDB_BASE_URL}/movie/550?language=ko-KR", token)
    print("TMDB authentication validated (token not displayed).", flush=True)

    movies, movielens_stats = read_movielens(args.archive)
    sample, cohorts = build_sample(
        movies,
        random_size=args.random_size,
        top_size=args.top_size,
        per_stratum=args.per_stratum,
        seed=args.seed,
    )
    if args.limit > 0:
        sample = sample[: args.limit]

    print(
        f"Querying {len(sample):,} distinct TMDB movies with {args.workers} workers...",
        flush=True,
    )
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    def fetch(movie: Movie) -> tuple[Movie, dict[str, Any] | None, str | None]:
        try:
            return movie, request_json(movie_url(int(movie.tmdb_id)), token), None
        except Exception as error:  # errors are persisted without credentials
            return movie, None, str(error)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(fetch, movie) for movie in sample]
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            movie, payload, error = future.result()
            if error:
                errors.append(
                    {
                        "movie_id": movie.movie_id,
                        "tmdb_id": movie.tmdb_id,
                        "title": movie.title,
                        "error": error,
                    }
                )
            else:
                rows.append(normalize(movie, cohorts[movie.movie_id], payload or {}))
            if index % 100 == 0 or index == len(sample):
                print(f"  completed {index:,}/{len(sample):,}", flush=True)

    rows.sort(key=lambda row: int(row["movie_id"]))
    errors.sort(key=lambda row: int(row["movie_id"]))
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "movie_field_audit.csv", rows)
    (args.output / "errors.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    cohort_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for cohort in str(row["cohorts"]).split("|"):
            cohort_rows[cohort].append(row)

    summary = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_archive": str(args.archive),
        "seed": args.seed,
        "sample_requested": len(sample),
        "sample_succeeded": len(rows),
        "sample_failed": len(errors),
        "movielens": movielens_stats,
        "coverage_all_audit_movies": coverage(rows),
        "coverage_by_cohort": {
            name: coverage(items) for name, items in sorted(cohort_rows.items())
        },
        "coverage_by_popularity": group_coverage(rows, "popularity_bucket"),
        "coverage_by_era": group_coverage(rows, "era_bucket"),
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    weakest = sorted(
        COVERAGE_FIELDS,
        key=lambda field: summary["coverage_all_audit_movies"][field]["rate"],
    )
    print("Weakest fields:", flush=True)
    for field in weakest[:10]:
        item = summary["coverage_all_audit_movies"][field]
        print(f"  {field}: {item['count']}/{len(rows)} ({item['rate']:.1%})", flush=True)
    print(f"Artifacts written to {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
