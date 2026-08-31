#!/usr/bin/env python3
"""Build a catalog-level recommendation capability inventory for FEELM.

The audit is deliberately fail-closed. A TMDB field that was not collected is
reported as ``UNKNOWN_NOT_AUDITED`` rather than ``false``. Likewise, MovieLens
ratings establish ALS factor *eligibility*, not proof that a trained factor
artifact currently contains the item.

Audit grain:

* one row per unique TMDB id linked from MovieLens;
* one row per MovieLens item without a TMDB id;
* one row per Korean-origin TMDB proxy item not linked from MovieLens.

Duplicate MovieLens ids pointing at the same TMDB id are retained in the
``movielens_ids`` field and their rating counts are summed for catalog-level
stratification.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


YEAR_PATTERN = re.compile(r"\((\d{4})\)\s*$")
UNKNOWN_NOT_AUDITED = "UNKNOWN_NOT_AUDITED"
NOT_APPLICABLE_NO_TMDB = "NOT_APPLICABLE_NO_TMDB"


CSV_FIELDS = (
    "catalog_key",
    "universe_source",
    "tmdb_id",
    "primary_movielens_id",
    "movielens_ids",
    "movielens_item_count",
    "title",
    "release_year",
    "era_band",
    "origin_group",
    "origin_segment",
    "origin_evidence",
    "rating_count",
    "popularity_band",
    "popularity_segment",
    "movielens_link_status",
    "als_factor_eligibility",
    "als_factor_artifact_status",
    "movielens_genres",
    "genres_presence",
    "overview_ko_presence",
    "overview_en_presence",
    "keywords_presence",
    "director_presence",
    "cast_presence",
    "embedding_input_status",
    "content_only_status",
    "current_kr_provider_status",
    "current_kr_flatrate_status",
    "current_kr_provider_names",
    "tmdb_market_metadata_status",
    "capability_zone",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--movies", type=Path, required=True)
    parser.add_argument("--links", type=Path, required=True)
    parser.add_argument("--movie-stats", type=Path, required=True)
    parser.add_argument("--korean-origin-json", type=Path, required=True)
    parser.add_argument("--market-cache-dir", type=Path, required=True)
    parser.add_argument("--factor-item-ids", type=Path)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--new-year-cutoff", type=int, default=2015)
    return parser.parse_args()


def parse_release_year(title: str) -> int | None:
    match = YEAR_PATTERN.search(title)
    return int(match.group(1)) if match else None


def popularity_band(rating_count: int, has_movielens_link: bool) -> str:
    if not has_movielens_link:
        return "NO_MOVIELENS_LINK"
    if rating_count == 0:
        return "ZERO"
    if rating_count < 10:
        return "R1_9"
    if rating_count < 100:
        return "R10_99"
    if rating_count < 1_000:
        return "R100_999"
    if rating_count < 10_000:
        return "R1000_9999"
    return "R10000_PLUS"


def popularity_segment(rating_count: int, has_movielens_link: bool) -> str:
    if not has_movielens_link:
        return "NO_MOVIELENS_LINK"
    if rating_count == 0:
        return "ZERO"
    if rating_count < 1_000:
        return "TAIL_R1_999"
    return "POPULAR_R1000_PLUS"


def era_band(release_year: int | None, cutoff: int) -> str:
    if release_year is None:
        return "UNKNOWN_YEAR"
    return f"NEW_{cutoff}_PLUS" if release_year >= cutoff else f"OLD_PRE_{cutoff}"


def valid_genres(values: Iterable[str]) -> list[str]:
    return sorted(
        {
            value.strip()
            for item in values
            for value in item.split("|")
            if value.strip() and value.strip() != "(no genres listed)"
        }
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_manifest(cache_dir: Path) -> dict[str, Any]:
    files = sorted(cache_dir.glob("*.json"), key=lambda item: item.name)
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {"json_files": len(files), "combined_sha256": digest.hexdigest()}


def load_movielens_movies(path: Path) -> dict[int, dict[str, Any]]:
    movies: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            movie_id = int(row["movieId"])
            movies[movie_id] = {
                "movie_id": movie_id,
                "title": row["title"],
                "release_year": parse_release_year(row["title"]),
                "genres": row["genres"],
                "tmdb_id": None,
                "rating_count": 0,
            }
    return movies


def add_links(movies: dict[int, dict[str, Any]], path: Path) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            movie_id = int(row["movieId"])
            if movie_id not in movies:
                raise ValueError(f"links.csv contains unknown movieId={movie_id}")
            value = (row.get("tmdbId") or "").strip()
            movies[movie_id]["tmdb_id"] = int(value) if value else None


def add_rating_counts(movies: dict[int, dict[str, Any]], path: Path) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            movie_id = int(row["movieId"])
            if movie_id not in movies:
                raise ValueError(f"movie stats contains unknown movieId={movie_id}")
            movies[movie_id]["rating_count"] = int(row["rating_count"])


def load_korean_origin(path: Path) -> tuple[set[int], dict[int, dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = {int(value) for value in payload["tmdb_discover"]["tmdb_ids"]}
    matched = {
        int(item["tmdb_id"]): item
        for item in payload.get("movielens", {}).get("matched_items", [])
    }
    protocol = {
        "audit_id": payload.get("audit_id"),
        "generated_at_utc": payload.get("generated_at_utc"),
        "filter": payload.get("tmdb_discover", {}).get("filter"),
        "date_range": payload.get("tmdb_discover", {}).get("date_range"),
        "unique_tmdb_movies": payload.get("tmdb_discover", {}).get("unique_tmdb_movies"),
    }
    return ids, matched, protocol


def load_market_cache(cache_dir: Path) -> tuple[dict[int, dict[str, Any]], Counter[str]]:
    records: dict[int, dict[str, Any]] = {}
    statuses: Counter[str] = Counter()
    for path in sorted(cache_dir.glob("*.json"), key=lambda item: item.name):
        payload = json.loads(path.read_text(encoding="utf-8"))
        tmdb_id = int(payload.get("tmdb_id") or path.stem)
        status = str(payload.get("status") or "UNKNOWN").upper()
        statuses[status] += 1
        records[tmdb_id] = payload
    return records, statuses


def load_factor_item_ids(path: Path | None) -> set[int] | None:
    if path is None:
        return None
    values: set[int] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        first = handle.readline()
        handle.seek(0)
        if "," in first or any(char.isalpha() for char in first):
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return values
            key = next(
                (name for name in ("movieId", "movie_id", "id") if name in reader.fieldnames),
                None,
            )
            if key is None:
                raise ValueError("factor item csv needs movieId, movie_id, or id column")
            for row in reader:
                if row.get(key):
                    values.add(int(row[key]))
        else:
            for line in handle:
                if line.strip():
                    values.add(int(line.strip()))
    return values


def group_catalog(
    movies: dict[int, dict[str, Any]], korean_ids: set[int]
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for movie in movies.values():
        tmdb_id = movie["tmdb_id"]
        key = f"tmdb:{tmdb_id}" if tmdb_id is not None else f"ml:{movie['movie_id']}"
        group = groups.setdefault(
            key,
            {
                "catalog_key": key,
                "tmdb_id": tmdb_id,
                "movielens_items": [],
            },
        )
        group["movielens_items"].append(movie)

    linked_korean_ids = {
        int(group["tmdb_id"])
        for group in groups.values()
        if group["tmdb_id"] is not None and int(group["tmdb_id"]) in korean_ids
    }
    for tmdb_id in sorted(korean_ids - linked_korean_ids):
        key = f"tmdb:{tmdb_id}"
        groups[key] = {
            "catalog_key": key,
            "tmdb_id": tmdb_id,
            "movielens_items": [],
        }
    return groups


def _presence_for_tmdb(tmdb_id: int | None) -> str:
    return UNKNOWN_NOT_AUDITED if tmdb_id is not None else NOT_APPLICABLE_NO_TMDB


def _factor_artifact_status(
    movie_ids: list[int], eligible_ids: list[int], factor_ids: set[int] | None
) -> str:
    if not eligible_ids:
        return "NOT_APPLICABLE"
    if factor_ids is None:
        return "NOT_AUDITED_NO_FACTOR_ARTIFACT"
    observed = sum(movie_id in factor_ids for movie_id in eligible_ids)
    if observed == len(eligible_ids):
        return "OBSERVED_ALL_ELIGIBLE_ITEMS"
    if observed:
        return "OBSERVED_PARTIAL_ELIGIBLE_ITEMS"
    return "NOT_OBSERVED_IN_PROVIDED_ARTIFACT"


def build_row(
    group: dict[str, Any],
    korean_ids: set[int],
    matched_korean: dict[int, dict[str, Any]],
    market: dict[int, dict[str, Any]],
    factor_ids: set[int] | None,
    new_year_cutoff: int,
) -> dict[str, Any]:
    items = sorted(group["movielens_items"], key=lambda item: item["movie_id"])
    tmdb_id = group["tmdb_id"]
    movie_ids = [int(item["movie_id"]) for item in items]
    eligible_ids = [int(item["movie_id"]) for item in items if item["rating_count"] > 0]
    rating_count = sum(int(item["rating_count"]) for item in items)
    genres = valid_genres(item["genres"] for item in items)
    market_item = market.get(int(tmdb_id)) if tmdb_id is not None else None
    market_ok = bool(market_item and str(market_item.get("status", "")).upper() == "OK")

    if items:
        primary = items[0]
        title = primary["title"]
        release_year = primary["release_year"]
    else:
        primary = None
        matched = matched_korean.get(int(tmdb_id)) if tmdb_id is not None else None
        title = (matched or {}).get("tmdb_title")
        date_text = (matched or {}).get("tmdb_release_date") or ""
        release_year = int(date_text[:4]) if len(date_text) >= 4 and date_text[:4].isdigit() else None
        if not title and market_ok:
            title = market_item.get("title_ko_response") or market_item.get("original_title")

    if tmdb_id is not None and int(tmdb_id) in korean_ids:
        origin_group = "KOREAN_PROXY"
        origin_segment = "KOREAN"
        origin_evidence = "TMDB_DISCOVER_ORIGIN_COUNTRY_KR"
    elif market_ok and "KR" in (market_item.get("production_countries") or []):
        origin_group = "KOREAN_OBSERVED"
        origin_segment = "KOREAN"
        origin_evidence = "TMDB_PRODUCTION_COUNTRY_KR"
    elif market_ok and market_item.get("production_countries"):
        origin_group = "FOREIGN_OBSERVED"
        origin_segment = "FOREIGN"
        origin_evidence = "TMDB_PRODUCTION_COUNTRIES_NON_KR"
    else:
        origin_group = "UNKNOWN_ORIGIN"
        origin_segment = "UNKNOWN"
        origin_evidence = "UNOBSERVED"

    if items and origin_group.startswith("KOREAN"):
        universe_source = "MOVIELENS_AND_KOREAN_PROXY"
    elif items:
        universe_source = "MOVIELENS"
    else:
        universe_source = "KOREAN_PROXY_ONLY"

    has_link = bool(items)
    if eligible_ids:
        als_eligibility = "ELIGIBLE_HAS_RATINGS"
    elif has_link:
        als_eligibility = "INELIGIBLE_ZERO_RATINGS"
    else:
        als_eligibility = "INELIGIBLE_NO_MOVIELENS_LINK"

    if genres:
        genres_presence = "OBSERVED_MOVIELENS"
        content_only_status = "LIMITED_GENRE_ONLY"
    elif tmdb_id is not None:
        genres_presence = UNKNOWN_NOT_AUDITED
        content_only_status = "UNVERIFIED_METADATA_NOT_COLLECTED"
    else:
        genres_presence = "NOT_OBSERVED"
        content_only_status = "NOT_SUPPORTED_BY_CURRENT_EVIDENCE"

    if eligible_ids and genres:
        capability_zone = "ALS_ELIGIBLE_GENRE_ONLY"
    elif eligible_ids and tmdb_id is not None:
        capability_zone = "ALS_ELIGIBLE_CONTENT_UNRESOLVED"
    elif eligible_ids:
        capability_zone = "ALS_ELIGIBLE_NO_CONTENT"
    elif genres:
        capability_zone = "CONTENT_GENRE_ONLY_NO_ALS"
    elif tmdb_id is not None:
        capability_zone = "FALLBACK_REQUIRED_CONTENT_UNRESOLVED"
    else:
        capability_zone = "FALLBACK_REQUIRED_NO_CONTENT"

    if market_item is None:
        provider_status = UNKNOWN_NOT_AUDITED
        flatrate_status = UNKNOWN_NOT_AUDITED
        provider_names = ""
        market_status = "NOT_AUDITED"
    elif not market_ok:
        provider_status = "UNKNOWN_FETCH_FAILED"
        flatrate_status = "UNKNOWN_FETCH_FAILED"
        provider_names = ""
        market_status = f"FETCH_{str(market_item.get('status') or 'UNKNOWN').upper()}"
    else:
        provider_status = "AVAILABLE" if market_item.get("has_current_korean_provider") else "OBSERVED_EMPTY"
        flatrate_status = "AVAILABLE" if market_item.get("has_current_korean_flatrate") else "OBSERVED_EMPTY"
        provider_names = "|".join(sorted(market_item.get("korean_provider_names") or []))
        market_status = "OBSERVED"

    return {
        "catalog_key": group["catalog_key"],
        "universe_source": universe_source,
        "tmdb_id": tmdb_id,
        "primary_movielens_id": primary["movie_id"] if primary else None,
        "movielens_ids": "|".join(str(value) for value in movie_ids),
        "movielens_item_count": len(movie_ids),
        "title": title,
        "release_year": release_year,
        "era_band": era_band(release_year, new_year_cutoff),
        "origin_group": origin_group,
        "origin_segment": origin_segment,
        "origin_evidence": origin_evidence,
        "rating_count": rating_count,
        "popularity_band": popularity_band(rating_count, has_link),
        "popularity_segment": popularity_segment(rating_count, has_link),
        "movielens_link_status": "LINKED" if has_link else "NOT_LINKED",
        "als_factor_eligibility": als_eligibility,
        "als_factor_artifact_status": _factor_artifact_status(movie_ids, eligible_ids, factor_ids),
        "movielens_genres": "|".join(genres),
        "genres_presence": genres_presence,
        "overview_ko_presence": _presence_for_tmdb(tmdb_id),
        "overview_en_presence": _presence_for_tmdb(tmdb_id),
        "keywords_presence": _presence_for_tmdb(tmdb_id),
        "director_presence": _presence_for_tmdb(tmdb_id),
        "cast_presence": _presence_for_tmdb(tmdb_id),
        "embedding_input_status": (
            "UNVERIFIED_TEXT_METADATA_NOT_COLLECTED"
            if tmdb_id is not None
            else "NOT_SUPPORTED_NO_TEXT_SOURCE"
        ),
        "content_only_status": content_only_status,
        "current_kr_provider_status": provider_status,
        "current_kr_flatrate_status": flatrate_status,
        "current_kr_provider_names": provider_names,
        "tmdb_market_metadata_status": market_status,
        "capability_zone": capability_zone,
    }


def count_by(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(str(row[field]) for row in rows)
    return dict(sorted(counts.items()))


def summarize(
    rows: list[dict[str, Any]],
    movies: dict[int, dict[str, Any]],
    korean_ids: set[int],
    market_statuses: Counter[str],
    factor_ids: set[int] | None,
    new_year_cutoff: int,
) -> dict[str, Any]:
    total_ratings = sum(int(row["rating_count"]) for row in rows)
    linked_rows = [row for row in rows if row["movielens_link_status"] == "LINKED"]
    eligible_rows = [row for row in rows if row["als_factor_eligibility"] == "ELIGIBLE_HAS_RATINGS"]
    korean_rows = [row for row in rows if str(row["origin_group"]).startswith("KOREAN")]
    korean_only_rows = [row for row in rows if row["universe_source"] == "KOREAN_PROXY_ONLY"]
    cross_tab: Counter[tuple[str, str, str, str]] = Counter()
    cross_ratings: Counter[tuple[str, str, str, str]] = Counter()
    origin_popularity: Counter[tuple[str, str]] = Counter()
    origin_era: Counter[tuple[str, str]] = Counter()
    origin_capability: Counter[tuple[str, str]] = Counter()
    for row in rows:
        key = (
            str(row["origin_group"]),
            str(row["popularity_band"]),
            str(row["era_band"]),
            str(row["capability_zone"]),
        )
        cross_tab[key] += 1
        cross_ratings[key] += int(row["rating_count"])
        origin_popularity[(str(row["origin_segment"]), str(row["popularity_segment"]))] += 1
        origin_era[(str(row["origin_segment"]), str(row["era_band"]))] += 1
        origin_capability[(str(row["origin_segment"]), str(row["capability_zone"]))] += 1

    strata = [
        {
            "origin_group": key[0],
            "popularity_band": key[1],
            "era_band": key[2],
            "capability_zone": key[3],
            "catalog_rows": count,
            "rating_count": cross_ratings[key],
        }
        for key, count in sorted(cross_tab.items())
    ]

    observed_rich_content = sum(
        row["content_only_status"] == "SUPPORTED_RICH_CONTENT" for row in rows
    )
    return {
        "totals": {
            "catalog_rows": len(rows),
            "movielens_source_items": len(movies),
            "unique_tmdb_ids": len({row["tmdb_id"] for row in rows if row["tmdb_id"] is not None}),
            "movielens_linked_catalog_rows": len(linked_rows),
            "movielens_unmapped_catalog_rows": sum(row["tmdb_id"] is None for row in linked_rows),
            "duplicate_movielens_links_collapsed": len(movies) - len(linked_rows),
            "korean_origin_proxy_ids": len(korean_ids),
            "korean_catalog_rows": len(korean_rows),
            "korean_proxy_only_rows": len(korean_only_rows),
            "als_eligible_catalog_rows": len(eligible_rows),
            "rating_rows_represented": total_ratings,
            "rich_content_supported_rows": observed_rich_content,
        },
        "new_old_definition": {
            "cutoff_year": new_year_cutoff,
            "old": f"release_year < {new_year_cutoff}",
            "new": f"release_year >= {new_year_cutoff}",
            "warning": "분석용 고정 구간이며 제품 정책이나 최신성 선호를 뜻하지 않는다.",
        },
        "counts": {
            "universe_source": count_by(rows, "universe_source"),
            "origin_group": count_by(rows, "origin_group"),
            "origin_segment": count_by(rows, "origin_segment"),
            "popularity_band": count_by(rows, "popularity_band"),
            "popularity_segment": count_by(rows, "popularity_segment"),
            "era_band": count_by(rows, "era_band"),
            "als_factor_eligibility": count_by(rows, "als_factor_eligibility"),
            "als_factor_artifact_status": count_by(rows, "als_factor_artifact_status"),
            "content_only_status": count_by(rows, "content_only_status"),
            "embedding_input_status": count_by(rows, "embedding_input_status"),
            "current_kr_provider_status": count_by(rows, "current_kr_provider_status"),
            "capability_zone": count_by(rows, "capability_zone"),
            "market_cache_fetch_status": dict(sorted(market_statuses.items())),
        },
        "field_observation": {
            field: count_by(rows, field)
            for field in (
                "genres_presence",
                "overview_ko_presence",
                "overview_en_presence",
                "keywords_presence",
                "director_presence",
                "cast_presence",
            )
        },
        "strata": strata,
        "decision_strata": {
            "origin_by_popularity_segment": [
                {"origin_segment": key[0], "popularity_segment": key[1], "catalog_rows": count}
                for key, count in sorted(origin_popularity.items())
            ],
            "origin_by_era": [
                {"origin_segment": key[0], "era_band": key[1], "catalog_rows": count}
                for key, count in sorted(origin_era.items())
            ],
            "origin_by_capability_zone": [
                {"origin_segment": key[0], "capability_zone": key[1], "catalog_rows": count}
                for key, count in sorted(origin_capability.items())
            ],
        },
        "decision": {
            "status": "COMPLETED_FAIL_CLOSED_METADATA_GAP",
            "als_backbone_for_entire_catalog": "NOT_SUPPORTED_BY_CURRENT_INVENTORY",
            "reason": (
                "ALS eligibility exists only for MovieLens-linked items with ratings; "
                "Korean-origin-only items have no collaborative signal."
            ),
            "content_only_for_korean_gap": (
                "NOT_YET_VERIFIABLE because overview, keyword, director, and cast "
                "presence were not collected for the audit universe."
            ),
            "factor_artifact_verified": factor_ids is not None,
            "quality_claim": "NO_RECOMMENDATION_QUALITY_CLAIM",
        },
    }


def pct(value: int, total: int) -> str:
    return f"{(100 * value / total):.2f}%" if total else "0.00%"


def markdown_table(headers: list[str], values: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in values)
    return "\n".join(lines)


def render_markdown(summary: dict[str, Any], csv_path: Path) -> str:
    totals = summary["totals"]
    zones = summary["counts"]["capability_zone"]
    origins = summary["counts"]["origin_group"]
    popularity = summary["counts"]["popularity_band"]
    fields = summary["field_observation"]
    decision_strata = summary["decision_strata"]

    zone_rows = [
        [name, f"{count:,}", pct(count, totals["catalog_rows"])]
        for name, count in zones.items()
    ]
    origin_rows = [
        [name, f"{count:,}", pct(count, totals["catalog_rows"])]
        for name, count in origins.items()
    ]
    popularity_rows = [
        [name, f"{count:,}", pct(count, totals["catalog_rows"])]
        for name, count in popularity.items()
    ]
    field_rows = []
    for field, counts in fields.items():
        observed = sum(value for key, value in counts.items() if key.startswith("OBSERVED"))
        unknown = sum(value for key, value in counts.items() if "UNKNOWN" in key)
        field_rows.append([field, f"{observed:,}", f"{unknown:,}", json.dumps(counts, ensure_ascii=False)])

    def matrix(
        records: list[dict[str, Any]], row_field: str, column_field: str, columns: list[str]
    ) -> list[list[Any]]:
        lookup = {
            (str(item[row_field]), str(item[column_field])): int(item["catalog_rows"])
            for item in records
        }
        row_names = sorted({str(item[row_field]) for item in records})
        return [
            [row_name, *(f"{lookup.get((row_name, column), 0):,}" for column in columns)]
            for row_name in row_names
        ]

    popularity_columns = ["POPULAR_R1000_PLUS", "TAIL_R1_999", "ZERO", "NO_MOVIELENS_LINK"]
    era_columns = [f"OLD_PRE_{summary['new_old_definition']['cutoff_year']}", f"NEW_{summary['new_old_definition']['cutoff_year']}_PLUS", "UNKNOWN_YEAR"]
    origin_popularity_rows = matrix(
        decision_strata["origin_by_popularity_segment"],
        "origin_segment",
        "popularity_segment",
        popularity_columns,
    )
    origin_era_rows = matrix(
        decision_strata["origin_by_era"],
        "origin_segment",
        "era_band",
        era_columns,
    )

    return f"""# FEELM 카탈로그 추천 가능성 전수 감사

> 상태: `COMPLETED_FAIL_CLOSED_METADATA_GAP`
> 단위: MovieLens의 고유 TMDB 연결 + TMDB 미연결 MovieLens item + MovieLens에 없는 한국-origin proxy
> 주의: 추천 품질 실험이 아니라, 현재 확보한 데이터로 어떤 추천 신호를 만들 수 있는지 확인한 inventory다.

## 결론

현재 자료만으로는 **ALS를 FEELM 전체 카탈로그의 기본 뼈대로 확정할 수 없다.**
ALS 학습 자격이 있는 카탈로그 행은 {totals['als_eligible_catalog_rows']:,}개지만,
MovieLens에 연결되지 않은 한국-origin proxy가 {totals['korean_proxy_only_rows']:,}개다.
이 항목에는 협업 신호가 없다.

그렇다고 한국 영화 공백을 콘텐츠 유사도로 해결할 수 있다고도 아직 말할 수 없다.
전수 범위에서 한국어·영어 줄거리, 키워드, 감독, 배우를 수집한 artifact가 없어서
rich-content 가능 판정 행은 {totals['rich_content_supported_rows']:,}개다. 이는 메타데이터가 실제로
없다는 뜻이 아니라 **현재 감사 입력에서 존재 여부를 관측하지 않았다는 뜻**이다.

따라서 이번 조치 A의 결과는 `ALS vs 콘텐츠` 승자 선택이 아니라 다음 구현 Gate다.

1. ALS는 `MovieLens-linked + rating_count > 0` 구간에서만 후보 신호로 허용한다.
2. 한국-origin-only 구간은 TMDB 상세 메타데이터 전수 수집 전까지 content-only 가능으로 간주하지 않는다.
3. 줄거리·키워드·감독·배우 수집 후 이 표를 다시 실행해 `content-only 가능`과 `fallback 필요`를 재분류한다.
4. 추천 품질은 별도 평가 데이터 없이는 주장하지 않는다.

## 감사 모집단

| 항목 | 값 |
| --- | ---: |
| 카탈로그 고유 행 | {totals['catalog_rows']:,} |
| 원본 MovieLens item | {totals['movielens_source_items']:,} |
| 고유 TMDB ID | {totals['unique_tmdb_ids']:,} |
| MovieLens 연결 카탈로그 행 | {totals['movielens_linked_catalog_rows']:,} |
| MovieLens 미연결 한국-origin proxy | {totals['korean_proxy_only_rows']:,} |
| ALS 학습 자격 행 | {totals['als_eligible_catalog_rows']:,} |
| 합산 MovieLens Rating | {totals['rating_rows_represented']:,} |

`MovieLens item` 여러 개가 같은 TMDB ID를 가리키는 경우 한 카탈로그 행으로 합쳤고,
원래 ID는 CSV의 `movielens_ids`에 보존했다. 한국-origin 모집단은 TMDB
`with_origin_country=KR`, 1870-01-01~2023-10-13 proxy이므로 현재 전체 한국 영화 목록과 동일하지 않다.

## 현재 추천 가능 구간

{markdown_table(['구간', '영화', '비율'], zone_rows)}

`GENRE_ONLY`는 MovieLens 장르만 관측했다는 뜻이다. 줄거리 embedding이나 감독·배우·키워드 기반
유사도를 구현할 수 있다는 증거가 아니다. ALS factor도 rating 수로 자격만 판정했으며, 실제 factor
artifact를 입력하지 않았다면 `NOT_AUDITED_NO_FACTOR_ARTIFACT`로 유지한다.

## 원산지 구간

{markdown_table(['원산지 판정', '영화', '비율'], origin_rows)}

`UNKNOWN_ORIGIN`을 외국 영화로 바꾸지 않았다. TMDB 생산국을 실제로 읽지 않은 항목은 미관측이다.

## MovieLens 상호작용 구간

{markdown_table(['Rating 구간', '영화', '비율'], popularity_rows)}

`POPULAR`은 MovieLens Rating 1,000개 이상, `TAIL`은 1~999개로 고정했다. 이는 인지도의
정답이 아니라 ALS 신호량을 보기 위한 분석 구간이다.

## 원산지 × Popular/Tail

{markdown_table(['원산지', *popularity_columns], origin_popularity_rows)}

## 원산지 × New/Old

{markdown_table(['원산지', *era_columns], origin_era_rows)}

New/Old 경계는 {summary['new_old_definition']['cutoff_year']}년이다. 한국-origin-only 항목의
상세 release year를 현재 artifact가 보존하지 않아 `UNKNOWN_YEAR`가 크게 나타난다. 따라서 이
구간 역시 영화가 오래됐다는 뜻이 아니라 데이터가 아직 없다는 뜻일 수 있다.

## 콘텐츠 필드 관측 상태

{markdown_table(['필드', '관측 있음', '미관측', '상태 전체'], field_rows)}

TMDB ID가 있다는 사실과 TMDB 상세 필드가 확보됐다는 사실을 구분했다. 미수집 값을 `false`로
채우지 않았기 때문에, 이 표는 콘텐츠 추천 성능을 과장하지 않는다.

## 산출물

- 전수 행 CSV: `{csv_path.as_posix()}`
- 요약 JSON: 이 보고서와 함께 생성된 `catalog-recommendation-capability-v1.json`
- 재실행 코드: `scripts/catalog_recommendation_capability_audit.py`

CSV는 생성물 디렉터리(`outputs/`)에 두며 Git 추적 대상이 아니다. 요약 JSON과 이 보고서만
근거 문서로 유지한다.
"""


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    movies = load_movielens_movies(args.movies)
    add_links(movies, args.links)
    add_rating_counts(movies, args.movie_stats)
    korean_ids, matched_korean, korean_protocol = load_korean_origin(args.korean_origin_json)
    market, market_statuses = load_market_cache(args.market_cache_dir)
    factor_ids = load_factor_item_ids(args.factor_item_ids)

    groups = group_catalog(movies, korean_ids)
    rows = [
        build_row(
            groups[key],
            korean_ids,
            matched_korean,
            market,
            factor_ids,
            args.new_year_cutoff,
        )
        for key in sorted(groups)
    ]
    summary = summarize(
        rows,
        movies,
        korean_ids,
        market_statuses,
        factor_ids,
        args.new_year_cutoff,
    )

    expected_ratings = sum(int(movie["rating_count"]) for movie in movies.values())
    if summary["totals"]["rating_rows_represented"] != expected_ratings:
        raise RuntimeError("catalog grouping changed the total MovieLens rating count")
    if summary["totals"]["korean_origin_proxy_ids"] != len(korean_ids):
        raise RuntimeError("Korean-origin proxy coverage invariant failed")

    write_csv(args.output_csv, rows)
    result = {
        "schema_version": 1,
        "audit_id": "REC_DATA_007_CATALOG_RECOMMENDATION_CAPABILITY_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": {
            "audit_grain": (
                "unique TMDB-linked MovieLens catalog row, TMDB-unmapped MovieLens item, "
                "or MovieLens-unlinked Korean-origin TMDB proxy"
            ),
            "unknown_policy": "UNOBSERVED_OR_UNCOLLECTED_IS_NEVER_COERCED_TO_FALSE",
            "rating_count_aggregation": "SUM_ACROSS_DUPLICATE_MOVIELENS_IDS_PER_TMDB_ID",
            "factor_policy": "RATING_COUNT_ESTABLISHES_ELIGIBILITY_NOT_FACTOR_EXISTENCE",
            "content_policy": "MOVIELENS_GENRE_ONLY_IS_NOT_RICH_CONTENT_OR_EMBEDDING_EVIDENCE",
            "korean_origin": korean_protocol,
        },
        "sources": {
            "movies": {"path": str(args.movies), "sha256": sha256_file(args.movies)},
            "links": {"path": str(args.links), "sha256": sha256_file(args.links)},
            "movie_stats": {"path": str(args.movie_stats), "sha256": sha256_file(args.movie_stats)},
            "korean_origin_json": {
                "path": str(args.korean_origin_json),
                "sha256": sha256_file(args.korean_origin_json),
            },
            "market_cache": {
                "path": str(args.market_cache_dir),
                **cache_manifest(args.market_cache_dir),
            },
            "factor_item_ids": (
                {"path": str(args.factor_item_ids), "sha256": sha256_file(args.factor_item_ids)}
                if args.factor_item_ids
                else None
            ),
        },
        "artifacts": {"full_catalog_csv": str(args.output_csv)},
        **summary,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(
        render_markdown(result, args.output_csv) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": result["decision"]["status"],
                "catalog_rows": result["totals"]["catalog_rows"],
                "korean_proxy_only_rows": result["totals"]["korean_proxy_only_rows"],
                "als_eligible_rows": result["totals"]["als_eligible_catalog_rows"],
                "output_csv": str(args.output_csv),
                "output_json": str(args.output_json),
                "output_markdown": str(args.output_markdown),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
