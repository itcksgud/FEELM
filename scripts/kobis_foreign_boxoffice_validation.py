#!/usr/bin/env python3
"""Validate the Korean-market foreign-film proxy against official KOBIS charts.

The public KOBIS statistics pages are queried with their CSRF/session flow.
No OpenAPI key is required. Matching is deliberately conservative: normalized
Korean release titles must match TMDB's Korean title, and ambiguous matches are
not auto-selected unless release-year proximity resolves them uniquely.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import http.cookiejar
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


KOBIS_BASE = "https://www.kobis.or.kr"
YEARLY_PATH = "/kobis/business/stat/boxs/findYearlyBoxOfficeList.do"
FORMER_PATH = "/kobis/business/stat/boxs/findFormerBoxOfficeList.do"
ROW_PATTERN = re.compile(r'<tr id="tr_\d+"[^>]*>(.*?)</tr>', re.DOTALL)
MOVIE_PATTERN = re.compile(
    r"mstView\('movie','([^']+)'\).*?title=\"([^\"]+)\"",
    re.DOTALL,
)
TAG_PATTERN = re.compile(r"<[^>]+>")
YEAR_PATTERN = re.compile(r"\((\d{4})\)\s*$")
NON_WORD_PATTERN = re.compile(r"[^0-9a-z가-힣]+")
RATING_PROGRESS_INTERVAL = 5_000_000
KOBIS_SET_NAMES = (
    "KOBIS_ANY",
    "KOBIS_100K",
    "KOBIS_1M",
    "KOBIS_5M",
    "LEGACY_PRE_2004",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--awareness-json", type=Path, required=True)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2004)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--through-date", type=date.fromisoformat, default=date(2023, 10, 13))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def text_content(fragment: str) -> str:
    return html.unescape(TAG_PATTERN.sub("", fragment)).strip()


def cell(block: str, cell_id: str) -> str:
    match = re.search(
        rf'<td id="{re.escape(cell_id)}"[^>]*>(.*?)</td>',
        block,
        re.DOTALL,
    )
    return text_content(match.group(1)) if match else ""


def parse_number(value: str) -> int:
    cleaned = value.replace(",", "").strip()
    return int(cleaned) if cleaned else 0


def parse_rows(content: str, source: str, query_year: int | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in ROW_PATTERN.findall(content):
        movie_match = MOVIE_PATTERN.search(block)
        if movie_match is None:
            continue
        open_date = cell(block, "td_openDt")
        rows.append(
            {
                "kobis_movie_code": movie_match.group(1),
                "title_ko": html.unescape(movie_match.group(2)).strip(),
                "open_date": open_date or None,
                "rank": parse_number(cell(block, "td_rank")),
                "sales": parse_number(cell(block, "td_salesAcc")),
                "audience": parse_number(cell(block, "td_audiAcc")),
                "screens": parse_number(cell(block, "td_scrnCnt")),
                "show_count": parse_number(cell(block, "td_showCnt")),
                "source": source,
                "query_year": query_year,
            }
        )
    return rows


def fetch_statistics(path: str, fields: dict[str, str], retries: int = 4) -> str:
    url = KOBIS_BASE + path
    for attempt in range(retries):
        try:
            cookie_jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
            get_request = urllib.request.Request(
                url,
                headers={"User-Agent": "FEELM-KOBIS-Validation/1.0"},
            )
            with opener.open(get_request, timeout=60) as response:
                first = response.read().decode("utf-8")
            csrf_match = re.search(r'name="CSRFToken" value="([^"]+)"', first)
            if csrf_match is None:
                raise RuntimeError("KOBIS CSRF token missing")
            body = {
                "CSRFToken": csrf_match.group(1),
                "loadEnd": "0",
                "searchType": "search",
                "sMultiMovieYn": "",
                "sRepNationCd": "F",
                "sWideAreaCd": "",
                **fields,
            }
            post_request = urllib.request.Request(
                url,
                data=urllib.parse.urlencode(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "FEELM-KOBIS-Validation/1.0",
                },
                method="POST",
            )
            with opener.open(post_request, timeout=90) as response:
                return response.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError, RuntimeError) as error:
            if attempt + 1 == retries:
                raise RuntimeError(f"KOBIS request failed for {path}") from error
            time.sleep(min(2**attempt, 8))
    raise RuntimeError("KOBIS retries exhausted")


def fetch_year(year: int) -> list[dict[str, Any]]:
    content = fetch_statistics(YEARLY_PATH, {"sSearchYearFrom": str(year)})
    return parse_rows(content, "YEARLY_FOREIGN_TOP", year)


def fetch_former() -> list[dict[str, Any]]:
    content = fetch_statistics(FORMER_PATH, {})
    return parse_rows(content, "FORMER_FOREIGN_TOP", None)


def merge_kobis_rows(rows: list[dict[str, Any]], through_date: date) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        open_date = row.get("open_date")
        if open_date:
            try:
                parsed_date = date.fromisoformat(open_date)
            except ValueError:
                parsed_date = None
            if parsed_date and parsed_date > through_date:
                continue
        code = str(row["kobis_movie_code"])
        current = merged.get(code)
        if current is None:
            current = {
                "kobis_movie_code": code,
                "title_ko": row["title_ko"],
                "open_date": open_date,
                "audience": int(row["audience"]),
                "sales": int(row["sales"]),
                "screens": int(row["screens"]),
                "show_count": int(row["show_count"]),
                "all_time_rank": None,
                "yearly_ranks": {},
                "sources": [],
            }
            merged[code] = current
        current["audience"] = max(int(current["audience"]), int(row["audience"]))
        current["sales"] = max(int(current["sales"]), int(row["sales"]))
        current["screens"] = max(int(current["screens"]), int(row["screens"]))
        current["show_count"] = max(int(current["show_count"]), int(row["show_count"]))
        if row["source"] == "FORMER_FOREIGN_TOP":
            previous = current["all_time_rank"]
            current["all_time_rank"] = min(previous, row["rank"]) if previous else row["rank"]
        elif row.get("query_year") is not None:
            current["yearly_ranks"][str(row["query_year"])] = row["rank"]
        if row["source"] not in current["sources"]:
            current["sources"].append(row["source"])
    return sorted(merged.values(), key=lambda row: (-int(row["audience"]), str(row["title_ko"])))


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return NON_WORD_PATTERN.sub("", normalized)


def movielens_title_without_year(value: str) -> str:
    return YEAR_PATTERN.sub("", value).strip()


def item_titles(item: dict[str, Any]) -> set[str]:
    values = {
        str(item.get("title_ko_response") or ""),
        str(item.get("original_title") or ""),
        movielens_title_without_year(str(item.get("movielens_title") or "")),
    }
    return {normalize_title(value) for value in values if normalize_title(value)}


def release_year(item: dict[str, Any]) -> int | None:
    value = item.get("movielens_release_year")
    return int(value) if value is not None else None


def match_kobis(
    kobis_rows: list[dict[str, Any]], head_items: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for item in head_items:
        if item.get("status") != "OK":
            continue
        for title in item_titles(item):
            index.setdefault(title, []).append(item)

    matches: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    used_movie_ids: set[int] = set()
    for row in kobis_rows:
        candidates = index.get(normalize_title(str(row["title_ko"])), [])
        chosen: dict[str, Any] | None = None
        method: str | None = None
        if len(candidates) == 1:
            chosen = candidates[0]
            method = "EXACT_NORMALIZED_KOREAN_TITLE_UNIQUE"
        elif len(candidates) > 1 and row.get("open_date"):
            open_year = int(str(row["open_date"])[:4])
            distances = [
                (abs((release_year(candidate) or open_year) - open_year), candidate)
                for candidate in candidates
            ]
            minimum = min(distance for distance, _ in distances)
            nearest = [candidate for distance, candidate in distances if distance == minimum]
            if len(nearest) == 1 and minimum <= 5:
                chosen = nearest[0]
                method = "EXACT_NORMALIZED_TITLE_RELEASE_YEAR_TIEBREAK"
        if chosen is None:
            unmatched.append({**row, "candidate_count": len(candidates)})
            continue
        movie_id = int(chosen["movie_id"])
        if movie_id in used_movie_ids:
            unmatched.append({**row, "candidate_count": len(candidates), "reason": "DUPLICATE_MOVIELENS_MATCH"})
            continue
        used_movie_ids.add(movie_id)
        matches.append(
            {
                **row,
                "movie_id": movie_id,
                "tmdb_id": int(chosen["tmdb_id"]),
                "movielens_title": chosen["movielens_title"],
                "tmdb_korean_title": chosen.get("title_ko_response"),
                "rating_count": int(chosen["rating_count"]),
                "match_method": method,
                "foreign_broad": bool(
                    chosen.get("is_foreign")
                    and chosen.get("rating_count", 0) >= 100
                    and chosen.get("has_korean_title_translation")
                ),
                "foreign_moderate": bool(
                    chosen.get("is_foreign")
                    and chosen.get("rating_count", 0) >= 100
                    and chosen.get("has_korean_title_translation")
                    and (chosen.get("has_korean_release") or chosen.get("has_current_korean_provider"))
                ),
                "foreign_strict": bool(
                    chosen.get("is_foreign")
                    and chosen.get("rating_count", 0) >= 1000
                    and chosen.get("has_korean_title_translation")
                    and (
                        chosen.get("has_korean_theatrical_release")
                        or chosen.get("has_current_korean_provider")
                    )
                ),
            }
        )
    return matches, unmatched


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def kobis_audience_comparable(row: dict[str, Any], start_year: int) -> bool:
    open_date = row.get("open_date")
    return bool(open_date and int(str(open_date)[:4]) >= start_year)


def proxy_validation(matches: list[dict[str, Any]], start_year: int = 2004) -> dict[str, Any]:
    result: dict[str, Any] = {}
    comparable = [row for row in matches if kobis_audience_comparable(row, start_year)]
    cohorts = {
        "ANY_MATCHED_KOBIS": matches,
        "COMPARABLE_2004_PLUS_AUDIENCE_100K_PLUS": [
            row for row in comparable if row["audience"] >= 100_000
        ],
        "COMPARABLE_2004_PLUS_AUDIENCE_1M_PLUS": [
            row for row in comparable if row["audience"] >= 1_000_000
        ],
        "COMPARABLE_2004_PLUS_AUDIENCE_5M_PLUS": [
            row for row in comparable if row["audience"] >= 5_000_000
        ],
    }
    for cohort_name, rows in cohorts.items():
        total_audience = sum(int(row["audience"]) for row in rows)
        values: dict[str, Any] = {
            "movies": len(rows),
            "audience_sum": total_audience,
        }
        for proxy in ("foreign_broad", "foreign_moderate", "foreign_strict"):
            selected = [row for row in rows if row[proxy]]
            selected_audience = sum(int(row["audience"]) for row in selected)
            values[proxy] = {
                "movies": len(selected),
                "movie_recall": ratio(len(selected), len(rows)),
                "audience_sum": selected_audience,
                "audience_weighted_recall": ratio(selected_audience, total_audience),
            }
        result[cohort_name] = values
    return result


def legacy_pre_2004_items(head_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a search/survey candidate set for classics outside KOBIS coverage.

    This is not an awareness label. It only identifies pre-2004 foreign films
    with substantial MovieLens evidence and at least one Korean-market metadata
    signal, so KOBIS absence is not incorrectly treated as negative evidence.
    """
    selected = []
    for item in head_items:
        year = release_year(item)
        if not (
            item.get("status") == "OK"
            and item.get("is_foreign")
            and year is not None
            and year < 2004
            and int(item.get("rating_count", 0)) >= 1_000
            and item.get("has_korean_title_translation")
            and (item.get("has_korean_release") or item.get("has_current_korean_provider"))
        ):
            continue
        selected.append(
            {
                "movie_id": int(item["movie_id"]),
                "tmdb_id": int(item["tmdb_id"]),
                "movielens_title": item["movielens_title"],
                "movielens_release_year": year,
                "tmdb_korean_title": item.get("title_ko_response"),
                "rating_count": int(item["rating_count"]),
                "has_korean_release": bool(item.get("has_korean_release")),
                "has_korean_theatrical_release": bool(item.get("has_korean_theatrical_release")),
                "has_current_korean_provider": bool(item.get("has_current_korean_provider")),
                "status": "CANDIDATE_REQUIRES_SEARCH_OR_SURVEY_VALIDATION",
            }
        )
    return sorted(selected, key=lambda row: (-row["rating_count"], row["movielens_title"]))


def scan_user_coverage(
    path: Path,
    matches: list[dict[str, Any]],
    legacy_items: list[dict[str, Any]],
    kobis_start_year: int = 2004,
) -> dict[str, Any]:
    comparable = [row for row in matches if kobis_audience_comparable(row, kobis_start_year)]
    sets = {
        "KOBIS_ANY": {int(row["movie_id"]) for row in matches},
        "KOBIS_100K": {int(row["movie_id"]) for row in comparable if row["audience"] >= 100_000},
        "KOBIS_1M": {int(row["movie_id"]) for row in comparable if row["audience"] >= 1_000_000},
        "KOBIS_5M": {int(row["movie_id"]) for row in comparable if row["audience"] >= 5_000_000},
        "LEGACY_PRE_2004": {int(row["movie_id"]) for row in legacy_items},
    }
    membership: dict[int, list[str]] = {}
    for name, movie_ids in sets.items():
        for movie_id in movie_ids:
            membership.setdefault(movie_id, []).append(name)
    users = rows = 0
    rating_rows = Counter()
    user_thresholds = {name: Counter() for name in KOBIS_SET_NAMES}
    current_user: int | None = None
    current_counts = {name: 0 for name in KOBIS_SET_NAMES}
    started = time.perf_counter()

    def finish_user() -> None:
        nonlocal users
        if current_user is None:
            return
        users += 1
        for name in KOBIS_SET_NAMES:
            for threshold in (1, 5, 10, 25):
                user_thresholds[name][str(threshold)] += current_counts[name] >= threshold
            current_counts[name] = 0

    with path.open("r", encoding="utf-8", newline="") as handle:
        handle.readline()
        for line in handle:
            user_text, movie_text, _, _ = line.rstrip("\r\n").split(",")
            user_id = int(user_text)
            if current_user is not None and user_id != current_user:
                finish_user()
            current_user = user_id
            movie_id = int(movie_text)
            rows += 1
            for name in membership.get(movie_id, ()):
                current_counts[name] += 1
                rating_rows[name] += 1
            if rows % RATING_PROGRESS_INTERVAL == 0:
                print(
                    f"kobis_coverage_rows={rows:,} elapsed_seconds={time.perf_counter() - started:.1f}",
                    flush=True,
                )
        finish_user()
    return {
        name: {
            "movies": len(sets[name]),
            "rating_rows": rating_rows[name],
            "rating_share": ratio(rating_rows[name], rows),
            "users_with_at_least_ratings": dict(user_thresholds[name]),
            "user_share_with_any": ratio(user_thresholds[name]["1"], users),
        }
        for name in KOBIS_SET_NAMES
    }


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
    validation = result["proxy_validation"]
    recall_rows = []
    for cohort_name, cohort in validation.items():
        recall_rows.append(
            (
                cohort_name,
                f"{cohort['movies']:,}",
                percent_text(cohort["foreign_broad"]["movie_recall"]),
                percent_text(cohort["foreign_moderate"]["movie_recall"]),
                percent_text(cohort["foreign_strict"]["movie_recall"]),
                percent_text(cohort["foreign_moderate"]["audience_weighted_recall"]),
                percent_text(cohort["foreign_strict"]["audience_weighted_recall"]),
            )
        )
    coverage_rows = []
    for name, values in result["movielens_coverage"].items():
        users = values["users_with_at_least_ratings"]
        coverage_rows.append(
            (
                name,
                f"{values['movies']:,}",
                f"{values['rating_rows']:,}",
                percent_text(values["rating_share"]),
                f"{users['1']:,}",
                f"{users['5']:,}",
                f"{users['10']:,}",
                f"{users['25']:,}",
            )
        )
    source = result["kobis_source"]
    legacy = result["legacy_pre_2004"]
    titanic = legacy["titanic_1997_diagnostic"]
    legacy_example_rows = [
        (
            row["movielens_title"],
            row["tmdb_korean_title"],
            f"{row['rating_count']:,}",
            "Y" if row["has_korean_theatrical_release"] else "N",
            "Y" if row["has_current_korean_provider"] else "N",
        )
        for row in legacy["items"][:15]
    ]
    return f"""# KOBIS 외국 영화 흥행 교차 검증

> 상태: `COMPLETED_OFFICIAL_BOXOFFICE_VALIDATION`
> 생성 시각: {result['generated_at_utc']}
> 공식 자료: KOBIS 연도별 외국 영화 상위 {source['start_year']}~{source['end_year']} + 역대 외국 영화 상위

## 수집·매칭

- KOBIS 원시 행: {source['raw_rows']:,}개
- 영화코드 중복 제거·기준일 필터 후: {source['unique_movies']:,}편
- MovieLens head/TMDB 한국어 제목에 보수적 매칭: {result['matching']['matched_movies']:,}편
- 미매칭 또는 모호: {result['matching']['unmatched_movies']:,}편
- 매칭률: {percent_text(result['matching']['match_rate'])}

KOBIS 한국 개봉명을 정규화한 뒤 TMDB 한국어 제목과 정확히 맞는 경우만 자동 연결했다. 제목 충돌은
개봉연도 근접성이 하나로 결정되는 경우만 허용했다.

## KOBIS만으로 규모를 판정할 수 없는 2004년 이전 고전

KOBIS 목록 부재 또는 일부 재개봉 기록은 **미인지 판정에 사용하지 않는다**. 2004년 이전 외국 영화 중 MovieLens 평점이
1,000개 이상이고 한국어 제목과 국내 개봉/provider 신호가 있는 {legacy['candidate_movies']:,}편을
`LEGACY_PRE_2004` 후보군으로 분리했다. 이 집합은 국내 검색량 또는 사용자 설문으로 검증해야 하며,
아직 실제 인지도로 확정된 집합이 아니다.

- `Titanic (1997)`의 KOBIS 수집 목록 포함: {"Y" if titanic['present_in_kobis_rows'] else "N"} (2012·2023 재개봉 연도별 목록)
- KOBIS 표시 관객 수 / 원개봉 관객 비교 가능: {titanic['kobis_reported_audience']:,} / {"Y" if titanic['kobis_audience_comparable'] else "N"}
- 레거시 후보 포함: {"Y" if titanic['present_in_legacy_candidates'] else "N"}
- MovieLens 평점 수: {titanic['rating_count']:,}
- TMDB 국내 극장 개봉 / 현재 국내 제공처: {"Y" if titanic['has_korean_theatrical_release'] else "N"} / {"Y" if titanic['has_current_korean_provider'] else "N"}

{markdown_table(['레거시 예시', '한국어 제목', 'ML 평점', '국내 극장', '현재 provider'], legacy_example_rows)}

## 기존 proxy가 실제 국내 흥행작을 얼마나 포함하는가

`영화 recall`은 KOBIS 매칭 영화 수 기준, `관객 가중 recall`은 KOBIS 관객수 합 기준이다.

{markdown_table(['KOBIS cohort', '영화', 'BROAD 영화 recall', 'MODERATE 영화 recall', 'STRICT 영화 recall', 'MODERATE 관객가중', 'STRICT 관객가중'], recall_rows)}

## MovieLens 안의 KOBIS 검증 집합

{markdown_table(['집합', '영화', 'Rating', 'Rating 비율', '사용자≥1', '사용자≥5', '사용자≥10', '사용자≥25'], coverage_rows)}

## 판정 경계

- KOBIS는 실제 한국 극장 흥행 근거이므로 TMDB 한국어 제목/provider보다 강한 검증 신호다.
- KOBIS는 흥행·재개봉의 **양성 근거**로만 쓴다. 목록에 없다는 사실은 음성 근거가 아니다.
- 10만·100만·500만 관객 cohort에는 원개봉일이 2004년 이후인 영화만 넣어 관객 수 비교 범위를 맞춘다.
- 원개봉일이 2004년 이전인 영화의 KOBIS 재개봉 관객 수는 원개봉 당시 인지도의 크기로 해석하지 않는다.
- 2004년 이전 작품, 극장 미개봉 OTT 작품, 연도별 상위 50 밖 영화는 이 자료만으로 검증되지 않는다.
- 미매칭은 미인지가 아니라 제목 차이 또는 MovieLens head 밖일 수 있다.
- 실제 개인 인지도와 관람 여부는 한국 사용자 설문으로 별도 확인한다.
"""


def main() -> int:
    args = parse_args()
    if not args.awareness_json.exists() or not args.ratings.exists():
        raise FileNotFoundError("awareness JSON or ratings file missing")
    if args.start_year > args.end_year:
        raise ValueError("start-year must not exceed end-year")
    if args.workers < 1 or args.workers > 8:
        raise ValueError("workers must be between 1 and 8")

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_year = {
            executor.submit(fetch_year, year): year
            for year in range(args.start_year, args.end_year + 1)
        }
        former_future = executor.submit(fetch_former)
        for future in concurrent.futures.as_completed(future_to_year):
            year = future_to_year[future]
            year_rows = future.result()
            rows.extend(year_rows)
            print(f"kobis_year={year} rows={len(year_rows)}", flush=True)
        former_rows = former_future.result()
        rows.extend(former_rows)
        print(f"kobis_former_rows={len(former_rows)}", flush=True)

    merged = merge_kobis_rows(rows, args.through_date)
    awareness = json.loads(args.awareness_json.read_text(encoding="utf-8"))
    head_items = awareness["head_items"]
    matches, unmatched = match_kobis(merged, head_items)
    legacy_items = legacy_pre_2004_items(head_items)
    validation = proxy_validation(matches, args.start_year)
    movielens_coverage = scan_user_coverage(
        args.ratings,
        matches,
        legacy_items,
        args.start_year,
    )
    titanic_candidates = [
        row for row in legacy_items if row["movielens_title"] == "Titanic (1997)"
    ]
    titanic_source = titanic_candidates[0] if titanic_candidates else {}
    titanic_kobis_rows = [
        row
        for row in merged
        if normalize_title(str(row["title_ko"])) == normalize_title("타이타닉")
    ]
    titanic_in_kobis = bool(titanic_kobis_rows)
    titanic_kobis = titanic_kobis_rows[0] if titanic_kobis_rows else {}

    result = {
        "schema_version": 1,
        "audit_id": "KOBIS_FOREIGN_BOXOFFICE_PROXY_VALIDATION_V1",
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "claim_boundary": "OFFICIAL_KOREAN_THEATRICAL_EVIDENCE_NOT_PERSON_LEVEL_AWARENESS",
        "kobis_source": {
            "yearly_url": KOBIS_BASE + YEARLY_PATH,
            "former_url": KOBIS_BASE + FORMER_PATH,
            "filter": "sRepNationCd=F",
            "start_year": args.start_year,
            "end_year": args.end_year,
            "through_date": args.through_date.isoformat(),
            "raw_rows": len(rows),
            "unique_movies": len(merged),
            "pre_2004_open_date_movies": sum(
                bool(row.get("open_date"))
                and int(str(row["open_date"])[:4]) < args.start_year
                for row in merged
            ),
            "audience_threshold_scope": f"original open_date >= {args.start_year}",
            "runtime_seconds": round(time.perf_counter() - started, 3),
        },
        "matching": {
            "awareness_head_items": len(head_items),
            "matched_movies": len(matches),
            "unmatched_movies": len(unmatched),
            "match_rate": ratio(len(matches), len(merged)),
            "method": "normalized exact title; unique match or unique release-year tiebreak <=5 years",
        },
        "proxy_validation": validation,
        "legacy_pre_2004": {
            "definition": (
                "foreign, release_year<2004, MovieLens ratings>=1000, Korean title, "
                "and Korean release or current provider; candidate only"
            ),
            "claim_boundary": "SEARCH_OR_SURVEY_CANDIDATE_NOT_CONFIRMED_AWARENESS",
            "candidate_movies": len(legacy_items),
            "titanic_1997_diagnostic": {
                "present_in_kobis_rows": titanic_in_kobis,
                "kobis_reported_audience": int(titanic_kobis.get("audience", 0)),
                "kobis_yearly_ranks": titanic_kobis.get("yearly_ranks", {}),
                "kobis_audience_comparable": (
                    kobis_audience_comparable(titanic_kobis, args.start_year)
                    if titanic_kobis
                    else False
                ),
                "present_in_legacy_candidates": bool(titanic_candidates),
                "rating_count": int(titanic_source.get("rating_count", 0)),
                "has_korean_theatrical_release": bool(
                    titanic_source.get("has_korean_theatrical_release")
                ),
                "has_current_korean_provider": bool(
                    titanic_source.get("has_current_korean_provider")
                ),
            },
            "items": legacy_items,
        },
        "movielens_coverage": movielens_coverage,
        "matches": matches,
        "unmatched": unmatched,
        "kobis_movies": merged,
        "decision": {
            "strong_evidence": "KOBIS_1M",
            "supporting_evidence": "KOBIS_100K",
            "kobis_absence_policy": "NEVER_TREAT_AS_LOW_AWARENESS",
            "pre_2004_policy": "VALIDATE_LEGACY_PRE_2004_WITH_SEARCH_OR_SURVEY",
            "proxy_default_under_test": "FOREIGN_MODERATE",
            "actual_person_awareness_status": "NOT_EVALUATED_REQUIRES_SURVEY",
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
                "kobis_movies": len(merged),
                "matched": len(matches),
                "moderate_1m_movie_recall": validation[
                    "COMPARABLE_2004_PLUS_AUDIENCE_1M_PLUS"
                ]["foreign_moderate"]["movie_recall"],
                "strict_1m_movie_recall": validation[
                    "COMPARABLE_2004_PLUS_AUDIENCE_1M_PLUS"
                ]["foreign_strict"]["movie_recall"],
                "output_json": str(args.output_json),
                "output_markdown": str(args.output_markdown),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
