"""Conservatively link full KOBIS annual observations with fail-closed output.

No network calls or model training. Original annual rows/values stay unchanged.
Only directly observed KOBIS codes define movie entities. Metadata similarities
produce proposals, never verified cross-source IDs. Last-observed cumulative
figures retain their period-end date and are never called current lifetime data.

Version 3 adds explicit input contracts and atomic publication. Every output is
written to a sibling temporary directory, validated, and renamed only after all
checks pass. Python optimization cannot disable any contract check.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import unicodedata
from typing import Callable
from uuid import uuid4

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/recommendation-evidence/kobis-expanded-20260913"
CAT = ROOT / ".codex-tmp/fixed-k8-discovery-v2-20260913/outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2/prepare/catalog.parquet"
ML = ROOT / "outputs/recommendation-evidence/presentation-data-20260913/movie-rating-aggregates.parquet"
DEFAULT_CATALOG_SHA256 = "8bda180eb6530188a6ebf08da599d4d68124247a878ca0911f0d3f38512415cd"
DEFAULT_CATALOG_ROWS = 237_817
DEFAULT_MOVIELENS_ROWS = 87_585

# Explicit mapping only. Unknown or historical labels are reported, not guessed.
NATION_CODES = {
    "한국": "KR", "미국": "US", "일본": "JP", "중국": "CN", "홍콩": "HK", "대만": "TW",
    "영국": "GB", "프랑스": "FR", "독일": "DE", "이탈리아": "IT", "스페인": "ES",
    "캐나다": "CA", "호주": "AU", "뉴질랜드": "NZ", "러시아": "RU", "인도": "IN",
    "태국": "TH", "싱가포르": "SG", "말레이시아": "MY", "인도네시아": "ID", "필리핀": "PH",
    "베트남": "VN", "몽골": "MN", "캄보디아": "KH", "미얀마": "MM", "라오스": "LA",
    "네팔": "NP", "방글라데시": "BD", "파키스탄": "PK", "스리랑카": "LK", "부탄": "BT",
    "벨기에": "BE", "네덜란드": "NL", "스위스": "CH", "오스트리아": "AT", "아일랜드": "IE",
    "스웨덴": "SE", "덴마크": "DK", "노르웨이": "NO", "핀란드": "FI", "아이슬란드": "IS",
    "폴란드": "PL", "체코": "CZ", "슬로바키아": "SK", "헝가리": "HU", "루마니아": "RO",
    "불가리아": "BG", "그리스": "GR", "포르투갈": "PT", "룩셈부르크": "LU", "몰타": "MT",
    "에스토니아": "EE", "라트비아": "LV", "리투아니아": "LT", "우크라이나": "UA",
    "벨라루스": "BY", "몰도바": "MD", "크로아티아": "HR", "슬로베니아": "SI",
    "세르비아": "RS", "몬테네그로": "ME", "보스니아 헤르체고비나": "BA", "알바니아": "AL",
    "북마케도니아": "MK", "마케도니아": "MK", "키프로스": "CY", "조지아": "GE",
    "아르메니아": "AM", "아제르바이잔": "AZ", "카자흐스탄": "KZ", "우즈베키스탄": "UZ",
    "키르기스스탄": "KG", "타지키스탄": "TJ", "터키": "TR", "튀르키예": "TR",
    "이란": "IR", "이스라엘": "IL", "팔레스타인": "PS", "레바논": "LB", "요르단": "JO",
    "시리아": "SY", "이라크": "IQ", "아프가니스탄": "AF", "사우디아라비아": "SA",
    "아랍에미리트연합국": "AE", "아랍에미리트": "AE", "카타르": "QA", "쿠웨이트": "KW",
    "바레인": "BH", "이집트": "EG", "모로코": "MA", "튀니지": "TN", "알제리": "DZ",
    "남아프리카공화국": "ZA", "케냐": "KE", "나이지리아": "NG", "에티오피아": "ET",
    "세네갈": "SN", "말리": "ML", "부르키나파소": "BF", "수단": "SD", "르완다": "RW",
    "브라질": "BR", "멕시코": "MX", "아르헨티나": "AR", "칠레": "CL", "페루": "PE",
    "콜롬비아": "CO", "베네수엘라": "VE", "우루과이": "UY", "파라과이": "PY",
    "볼리비아": "BO", "에콰도르": "EC", "쿠바": "CU", "도미니카공화국": "DO",
    "코스타리카": "CR", "파나마": "PA", "과테말라": "GT", "푸에르토리코": "PR",
    "자메이카": "JM", "북한": "KP", "구소련": "SU",
}
COUNTRY_RULE_VERSION = "explicit-korean-country-labels-v1-no-unknown-inference"

REQUIRED_OBSERVATION_COLUMNS = [
    "source_row_id", "screening_year", "period_end", "is_partial_year", "kobis_movie_code",
    "title", "open_date", "representative_nation", "cumulative_admissions_at_period_end",
    "cumulative_sales_krw_at_period_end", "has_negative_adjustment",
]
CATALOG_COLUMNS = [
    "service_movie_id", "tmdb_id", "title", "original_title", "release_date",
    "production_country_codes", "movielens_movie_id", "mapping_status",
    "raw_vote_count_number", "raw_vote_average_number", "quality_state",
]
MOVIELENS_COLUMNS = ["movieId", "title", "ml_year", "ml_count", "ml_mean_5"]
NULL_CROSS_SOURCE_COLUMNS = [
    "tmdb_id", "service_movie_id", "tmdb_title", "tmdb_original_title",
    "tmdb_release_date", "tmdb_production_country_codes", "tmdb_vote_count",
    "tmdb_vote_average_10", "tmdb_quality_state", "movielens_movie_id",
    "ml_mapping_status", "ml_title", "ml_year", "ml_count", "ml_mean_5",
]


class ContractError(RuntimeError):
    """Raised when an input, semantic, or publication contract is violated."""


class ExecutionNotAuthorized(ContractError):
    """Raised before any write when --execute was not supplied."""


@dataclass(frozen=True)
class RunConfig:
    execute: bool
    input_path: Path
    catalog_path: Path
    movielens_aggregates_path: Path
    output_path: Path
    expect_input_sha256: str | None = None
    expect_input_rows: int | None = None
    expect_catalog_sha256: str | None = DEFAULT_CATALOG_SHA256
    expect_catalog_rows: int | None = DEFAULT_CATALOG_ROWS
    expect_movielens_aggregates_sha256: str | None = None
    expect_movielens_aggregates_rows: int | None = DEFAULT_MOVIELENS_ROWS


def require(condition: object, message: str, error_type: type[Exception] = ContractError) -> None:
    if not bool(condition):
        raise error_type(message)


def require_frame_equal(left: pd.DataFrame, right: pd.DataFrame, message: str) -> None:
    try:
        pd.testing.assert_frame_equal(left, right)
    except AssertionError as exc:
        raise ContractError(message) from exc


def repo_logical_path(path: Path) -> str:
    """Return a stable logical path without leaking a machine-absolute path."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
        return f"_external/{digest}/{resolved.name}"


def pin(path: Path, *, logical_path: str | None = None) -> dict[str, object]:
    require(path.is_file(), f"Input file does not exist: {path}", FileNotFoundError)
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return {
        "path": logical_path or repo_logical_path(path),
        "bytes": path.stat().st_size,
        "sha256": digest,
    }


def dump(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def norm(value: object) -> str:
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip().casefold()


def title_key(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", norm(value))


def full_date(value: object) -> date | None:
    normalized = norm(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def identity_key(row: dict[str, object]) -> tuple[str, str, str] | None:
    opening = full_date(row["open_date"])
    title = norm(row["title"])
    nation = norm(row["representative_nation"])
    return (title, opening.isoformat(), nation) if title and opening and nation else None


def resolve_identity(observations: pd.DataFrame) -> pd.DataFrame:
    """Keep direct codes; exact metadata seed similarities remain proposals."""
    frame = observations.copy(deep=True)
    require(frame.source_row_id.is_unique, "source_row_id must be unique")
    supports: defaultdict[tuple[str, str, str], defaultdict[str, list[object]]] = defaultdict(
        lambda: defaultdict(list)
    )
    original_codes: list[str | None] = []
    records = frame.to_dict("records")
    for row in records:
        code = norm(row["kobis_movie_code"]).upper()
        if code:
            require(
                re.fullmatch(r"[0-9A-Z]{8}", code) is not None,
                f"Invalid source code {code}",
            )
            key = identity_key(row)
            if key is not None:
                supports[key][code].append(row["source_row_id"])
        original_codes.append(code or None)

    resolved: list[str | None] = []
    proposed: list[str | None] = []
    methods: list[str] = []
    evidence: list[str] = []
    evidence_by_code: list[str] = []
    for row, direct in zip(records, original_codes):
        if direct:
            resolved.append(direct)
            proposed.append(None)
            methods.append("DIRECT_SOURCE_CODE")
            evidence.append(json.dumps([row["source_row_id"]], ensure_ascii=False))
            evidence_by_code.append(
                json.dumps({direct: [row["source_row_id"]]}, ensure_ascii=False)
            )
            continue
        key = identity_key(row)
        candidates = supports.get(key, {}) if key is not None else {}
        normalized_evidence = {
            str(code): sorted(source_ids) for code, source_ids in sorted(candidates.items())
        }
        if len(candidates) == 1:
            code = next(iter(candidates))
            resolved.append(None)
            proposed.append(code)
            methods.append("PROPOSED_EXACT_TITLE_FULL_DATE_NATION_UNIQUE_DIRECT_CODE")
            evidence.append(json.dumps(sorted(candidates[code]), ensure_ascii=False))
        else:
            resolved.append(None)
            proposed.append(None)
            methods.append(
                "UNRESOLVED_MISSING_IDENTITY_FIELD"
                if key is None
                else (
                    "UNRESOLVED_MULTIPLE_DIRECT_CODES"
                    if len(candidates) > 1
                    else "UNRESOLVED_NO_DIRECT_CODE"
                )
            )
            evidence.append(
                json.dumps(
                    sorted({sid for ids in candidates.values() for sid in ids}),
                    ensure_ascii=False,
                )
            )
        evidence_by_code.append(json.dumps(normalized_evidence, ensure_ascii=False))

    frame["resolved_kobis_movie_code"] = resolved
    frame["proposed_kobis_movie_code"] = proposed
    frame["identity_method"] = methods
    frame["identity_support_source_row_ids"] = evidence
    frame["kobis_identity_candidate_evidence_by_code"] = evidence_by_code
    signed = [
        column
        for column in [
            "annual_sales_krw", "annual_admissions",
            "cumulative_sales_krw_at_period_end", "cumulative_admissions_at_period_end",
        ]
        if column in frame
    ]
    frame["has_any_negative_collected_value"] = frame[signed].lt(0).any(axis=1)
    require(
        frame.resolved_kobis_movie_code.notna().equals(
            frame.identity_method.eq("DIRECT_SOURCE_CODE")
        ),
        "Only directly observed KOBIS codes may become resolved identities",
    )
    require_frame_equal(
        frame[observations.columns], observations, "Identity linking changed source observations"
    )
    return frame


def rollup_movies(observations: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    confirmed = observations[observations.identity_method.eq("DIRECT_SOURCE_CODE")]
    require(
        not confirmed.empty,
        "At least one directly observed KOBIS code is required for movie-level output",
    )
    require(
        confirmed.resolved_kobis_movie_code.notna().all(),
        "Every direct-code observation must retain its KOBIS code",
    )
    for code, group in confirmed.groupby("resolved_kobis_movie_code", sort=True):
        periods = sorted(set(group.period_end))
        latest_end = periods[-1]
        latest = group[group.period_end.eq(latest_end)]
        values = set(
            zip(
                latest.cumulative_admissions_at_period_end,
                latest.cumulative_sales_krw_at_period_end,
            )
        )
        consistent = len(values) == 1
        admissions, sales = next(iter(values)) if consistent else (None, None)
        metadata_row = latest.sort_values("source_row_id", kind="stable").iloc[0]
        dates = [full_date(value) for value in group.open_date]
        rows.append(
            {
                "kobis_movie_code": code,
                "observations": len(group),
                "direct_code_observations": int(
                    group.identity_method.eq("DIRECT_SOURCE_CODE").sum()
                ),
                "first_observed_period_end": periods[0],
                "last_observed_period_end": latest_end,
                "last_observed_source_row_ids": json.dumps(
                    sorted(latest.source_row_id), ensure_ascii=False
                ),
                "last_observed_cumulative_status": (
                    "OBSERVED" if consistent else "CONFLICT_AT_LATEST_PERIOD_END"
                ),
                "last_observed_cumulative_admissions": int(admissions) if consistent else None,
                "last_observed_cumulative_sales_krw": int(sales) if consistent else None,
                "last_observed_is_partial_year": bool(latest.is_partial_year.any()),
                "negative_adjustment_observed": bool(
                    group.has_any_negative_collected_value.any()
                ),
                "last_cumulative_has_negative_adjustment": bool(
                    consistent and (admissions < 0 or sales < 0)
                ),
                "pre_2004_open_date_observed": any(
                    day is not None and day.year < 2004 for day in dates
                ),
                "original_lifetime_complete": None,
                "cumulative_scope": "LAST_OBSERVED_QUERY_END_NOT_CURRENT_ASOF_OR_FULL_LIFETIME",
                "display_title_from_last_observation": metadata_row.title,
                "display_open_date_from_last_observation": metadata_row.open_date,
                "representative_nation_from_last_observation": metadata_row.representative_nation,
                "observed_titles": json.dumps(sorted(set(group.title)), ensure_ascii=False),
                "observed_open_dates": json.dumps(
                    sorted(
                        {
                            str(value)
                            for value in group.open_date
                            if full_date(value) is not None
                        }
                    ),
                    ensure_ascii=False,
                ),
                "observed_representative_nations": json.dumps(
                    sorted(set(group.representative_nation)), ensure_ascii=False
                ),
            }
        )
    movies = pd.DataFrame(rows)
    require(
        len(movies) == confirmed.resolved_kobis_movie_code.nunique(),
        "Movie rollup count differs from directly observed unique KOBIS codes",
    )
    require(movies.kobis_movie_code.is_unique, "Movie rollup KOBIS codes must be unique")
    for column in ["last_observed_cumulative_admissions", "last_observed_cumulative_sales_krw"]:
        movies[column] = pd.array(movies[column], dtype="Int64")
    return movies


def map_tmdb(
    observations: pd.DataFrame,
    movies: pd.DataFrame,
    catalog: pd.DataFrame,
    ml: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    index: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    by_id: dict[int, dict[str, object]] = {}
    for item in catalog.to_dict("records"):
        by_id[int(item["tmdb_id"])] = item
        keys = {title_key(item["title"]), title_key(item["original_title"])} - {""}
        for key in keys:
            index[key].append(item)
    candidate_cache: dict[tuple[str, str, str], frozenset[int]] = {}

    def candidates_for(row: dict[str, object]) -> frozenset[int]:
        key = (
            title_key(row["title"]),
            norm(row["open_date"]),
            norm(row["representative_nation"]),
        )
        if key not in candidate_cache:
            country = NATION_CODES.get(key[2])
            opening = full_date(row["open_date"])
            found: set[int] = set()
            if country is not None and opening is not None:
                for item in index.get(key[0], []):
                    release = full_date(item["release_date"])
                    if (
                        release is not None
                        and abs(release.year - opening.year) <= 1
                        and country in item["production_country_codes"]
                    ):
                        found.add(int(item["tmdb_id"]))
            candidate_cache[key] = frozenset(found)
        return candidate_cache[key]

    groups = {
        str(code): group
        for code, group in observations[
            observations.resolved_kobis_movie_code.notna()
        ].groupby("resolved_kobis_movie_code")
    }
    records: list[dict[str, object]] = []
    for code in movies.kobis_movie_code:
        group = groups[str(code)]
        candidates: set[int] = set()
        evidence: defaultdict[int, set[object]] = defaultdict(set)
        unknown_nations: set[str] = set()
        missing_date = 0
        for row in group[
            ["source_row_id", "title", "open_date", "representative_nation"]
        ].to_dict("records"):
            country = NATION_CODES.get(norm(row["representative_nation"]))
            opening = full_date(row["open_date"])
            if country is None:
                unknown_nations.add(norm(row["representative_nation"]))
            if opening is None:
                missing_date += 1
            if country is None or opening is None:
                continue
            for tmdb_id in candidates_for(row):
                candidates.add(tmdb_id)
                evidence[tmdb_id].add(row["source_row_id"])
        proposed = next(iter(candidates)) if len(candidates) == 1 else None
        records.append(
            {
                "kobis_movie_code": code,
                "tmdb_match_status": (
                    "PROPOSED_UNIQUE"
                    if proposed is not None
                    else (
                        "AMBIGUOUS_TMDB_CANDIDATES"
                        if candidates
                        else "NO_VERIFIED_TMDB_CANDIDATE"
                    )
                ),
                "tmdb_candidate_ids": json.dumps(sorted(candidates)),
                "proposed_tmdb_id": proposed,
                "tmdb_match_evidence_rows": (
                    json.dumps(sorted(evidence[proposed]), ensure_ascii=False)
                    if proposed is not None
                    else "[]"
                ),
                "tmdb_candidate_evidence_by_id": json.dumps(
                    {
                        str(candidate): sorted(source_ids)
                        for candidate, source_ids in sorted(evidence.items())
                    },
                    ensure_ascii=False,
                ),
                "unmapped_representative_nations": json.dumps(
                    sorted(unknown_nations), ensure_ascii=False
                ),
                "observations_missing_valid_date": missing_date,
                "tmdb_match_method": "EXACT_NORMALIZED_TITLE_KNOWN_REPRESENTATIVE_COUNTRY_YEAR_WITHIN_ONE_ALL_OBSERVATIONS_UNIQUE",
            }
        )
    links = pd.DataFrame(records)
    duplicated = links.proposed_tmdb_id.notna() & links.proposed_tmdb_id.duplicated(keep=False)
    links.loc[
        duplicated, "tmdb_match_status"
    ] = "MULTIPLE_KOBIS_CODES_ONE_TMDB_REQUIRES_REVIEW"

    # Metadata similarity never becomes a confirmed cross-source identity.
    links["tmdb_id"] = pd.array([None] * len(links), dtype="Int64")
    links["proposed_tmdb_id"] = pd.array(links["proposed_tmdb_id"], dtype="Int64")
    require(links.tmdb_id.isna().all(), "Confirmed TMDB IDs must remain null")
    require(
        not links.tmdb_match_status.eq("MATCHED").any(),
        "Metadata candidates must never be marked MATCHED",
    )

    movies = movies.merge(links, on="kobis_movie_code", how="left", validate="one_to_one")
    payload: list[dict[str, object]] = []
    for row in movies[["kobis_movie_code", "tmdb_id"]].to_dict("records"):
        item = by_id.get(int(row["tmdb_id"])) if pd.notna(row["tmdb_id"]) else None
        payload.append(
            {
                "kobis_movie_code": row["kobis_movie_code"],
                "service_movie_id": int(item["service_movie_id"]) if item is not None else None,
                "tmdb_title": item["title"] if item is not None else None,
                "tmdb_original_title": item["original_title"] if item is not None else None,
                "tmdb_release_date": item["release_date"] if item is not None else None,
                "tmdb_production_country_codes": (
                    "|".join(item["production_country_codes"]) if item is not None else None
                ),
                "tmdb_vote_count": item["raw_vote_count_number"] if item is not None else None,
                "tmdb_vote_average_10": item["raw_vote_average_number"] if item is not None else None,
                "tmdb_quality_state": item["quality_state"] if item is not None else None,
                "movielens_movie_id": (
                    int(item["movielens_movie_id"])
                    if item is not None and item["mapping_status"] == "MATCHED"
                    else None
                ),
                "ml_mapping_status": item["mapping_status"] if item is not None else None,
            }
        )
    movies = movies.merge(pd.DataFrame(payload), on="kobis_movie_code", validate="one_to_one")
    for column in ["service_movie_id", "movielens_movie_id"]:
        movies[column] = pd.array(movies[column], dtype="Int64")
    movies = movies.merge(
        ml.rename(columns={"movieId": "movielens_movie_id", "title": "ml_title"}),
        on="movielens_movie_id",
        how="left",
        validate="many_to_one",
    )
    require(
        movies[NULL_CROSS_SOURCE_COLUMNS].isna().all().all(),
        "Proposal-only output must leave TMDB, service, and MovieLens values null",
    )

    status = movies.set_index("kobis_movie_code").tmdb_match_status
    ids = movies.set_index("kobis_movie_code").tmdb_id
    observations = observations.copy()
    observations["tmdb_match_status"] = observations.resolved_kobis_movie_code.map(status).fillna(
        "KOBIS_IDENTITY_UNRESOLVED"
    )
    observations["resolved_tmdb_id"] = pd.array(
        observations.resolved_kobis_movie_code.map(ids), dtype="Int64"
    )

    row_candidates: list[str] = []
    row_proposals: list[int | None] = []
    row_statuses: list[str] = []
    row_evidence: list[str] = []
    for row in observations.to_dict("records"):
        if row["resolved_kobis_movie_code"] is not None:
            row_candidates.append("[]")
            row_proposals.append(None)
            row_statuses.append("DIRECT_KOBIS_CODE_USE_MOVIE_LEVEL_PROPOSAL")
            row_evidence.append("{}")
            continue
        possible = candidates_for(row)
        row_candidates.append(json.dumps(sorted(possible)))
        row_proposals.append(next(iter(possible)) if len(possible) == 1 else None)
        row_statuses.append(
            "ROW_TMDB_UNIQUE_PROPOSAL_CODE_UNRESOLVED"
            if len(possible) == 1
            else ("ROW_TMDB_AMBIGUOUS" if possible else "ROW_TMDB_NO_VERIFIED_CANDIDATE")
        )
        row_evidence.append(
            json.dumps(
                {str(candidate): [row["source_row_id"]] for candidate in sorted(possible)},
                ensure_ascii=False,
            )
        )
    observations["row_tmdb_candidate_ids"] = row_candidates
    observations["row_tmdb_proposal_id"] = pd.array(row_proposals, dtype="Int64")
    observations["row_tmdb_proposal_status"] = row_statuses
    observations["row_tmdb_candidate_evidence_by_id"] = row_evidence
    observations["row_tmdb_proposal_is_confirmed_movie_identity"] = False
    require(
        observations.resolved_tmdb_id.isna().all(),
        "Observation-level confirmed TMDB IDs must remain null",
    )
    require(
        not observations.row_tmdb_proposal_is_confirmed_movie_identity.any(),
        "Row-level TMDB proposals must remain unconfirmed",
    )
    return observations, movies


def summarize(observations: pd.DataFrame, movies: pd.DataFrame) -> dict[str, object]:
    yearly: list[dict[str, object]] = []
    for year, group in observations.groupby("screening_year", sort=True):
        yearly.append(
            {
                "screening_year": int(year),
                "observations": len(group),
                "direct_code_observations": int(
                    group.identity_method.eq("DIRECT_SOURCE_CODE").sum()
                ),
                "resolved_code_observations": int(group.resolved_kobis_movie_code.notna().sum()),
                "resolved_unique_kobis_codes": int(group.resolved_kobis_movie_code.nunique()),
                "code_unresolved_rows_with_unique_kobis_proposal": int(
                    group.proposed_kobis_movie_code.notna().sum()
                ),
                "tmdb_matched_observations": int(group.tmdb_match_status.eq("MATCHED").sum()),
                "tmdb_matched_unique_movies": int(group.resolved_tmdb_id.nunique()),
                "direct_code_rows_with_unique_tmdb_proposal": int(
                    group.tmdb_match_status.eq("PROPOSED_UNIQUE").sum()
                ),
                "code_unresolved_rows_with_unique_tmdb_proposal": int(
                    group.row_tmdb_proposal_id.notna().sum()
                ),
                "distinct_unresolved_proposed_tmdb_ids_not_confirmed_movies": int(
                    group.row_tmdb_proposal_id.nunique()
                ),
                "negative_adjustment_observations": int(
                    group.has_any_negative_collected_value.sum()
                ),
                "period_end": sorted(set(group.period_end))[-1],
                "is_partial_year": bool(group.is_partial_year.any()),
            }
        )
    nations: list[dict[str, object]] = []
    for nation, group in movies.groupby(
        "representative_nation_from_last_observation", dropna=False
    ):
        nations.append(
            {
                "representative_nation_from_last_observation": str(nation),
                "confirmed_kobis_movies": len(group),
                "tmdb_matched_movies": int(group.tmdb_match_status.eq("MATCHED").sum()),
                "kobis_movies_with_unique_tmdb_proposal": int(
                    group.tmdb_match_status.eq("PROPOSED_UNIQUE").sum()
                ),
                "positive_ml_rating_movies": int(group.ml_count.gt(0).sum()),
            }
        )
    return {
        "status": "LINKED_PENDING_INDEPENDENT_REVIEW",
        "observation_rows": len(observations),
        "identity_method_counts": {
            str(key): int(value) for key, value in observations.identity_method.value_counts().items()
        },
        "confirmed_kobis_movie_codes": len(movies),
        "confirmed_cross_source_tmdb_ids": int(movies.tmdb_id.notna().sum()),
        "code_unresolved_rows_with_unique_kobis_proposal": int(
            observations.proposed_kobis_movie_code.notna().sum()
        ),
        "unresolved_identity_observations_not_unique_movies": int(
            observations.resolved_kobis_movie_code.isna().sum()
        ),
        "row_tmdb_proposal_status_counts": {
            str(key): int(value)
            for key, value in observations.row_tmdb_proposal_status.value_counts().items()
        },
        "unresolved_identity_rows_with_unique_tmdb_proposal": int(
            observations.row_tmdb_proposal_id.notna().sum()
        ),
        "distinct_unresolved_proposed_tmdb_ids_not_confirmed_movies": int(
            observations.row_tmdb_proposal_id.nunique()
        ),
        "tmdb_match_status_counts": {
            str(key): int(value) for key, value in movies.tmdb_match_status.value_counts().items()
        },
        "last_observed_period_end_movie_counts": {
            str(key): int(value)
            for key, value in movies.last_observed_period_end.value_counts().sort_index().items()
        },
        "cumulative_conflict_movies": int(
            movies.last_observed_cumulative_status.ne("OBSERVED").sum()
        ),
        "pre_2004_open_date_movies": int(movies.pre_2004_open_date_observed.sum()),
        "negative_adjustment_observation_rows": int(
            observations.has_any_negative_collected_value.sum()
        ),
        "yearly_coverage": yearly,
        "nation_coverage": sorted(
            nations,
            key=lambda item: (
                -item["confirmed_kobis_movies"],
                item["representative_nation_from_last_observation"],
            ),
        ),
        "country_rule_version": COUNTRY_RULE_VERSION,
        "claim_boundaries": [
            "Annual observations are preserved; only directly observed KOBIS codes define movie entities.",
            "Exact metadata matches to a directly observed KOBIS code remain proposals and do not enter movie rollups.",
            "Unique TMDB metadata candidates are proposals, not shared-ID verification; confirmed IDs and linked MovieLens values remain null.",
            "Ambiguous KOBIS and TMDB candidates preserve per-candidate source-row evidence for review.",
            "Unresolved code-free observations are not a count of unique films.",
            "Missing KOBIS codes do not mean admissions were not collected: row-level figures and TMDB proposals remain available.",
            "Last-observed cumulative values are neither current-as-of values nor original lifetime totals.",
            "No annual or cumulative observations were summed/max-mixed to fabricate lifetime totals.",
            "Signed corrections are retained; negative is not converted to zero.",
            "Matching has no fuzzy fallback or unspecified-country inference.",
            "TMDB current snapshot and MovieLens old evaluation records are not time aligned.",
            "No model training, recommendation lift or Korean-film bonus was evaluated.",
        ],
    }


def check_expected_contract(
    label: str,
    file_pin: dict[str, object],
    actual_rows: int,
    expected_sha256: str | None,
    expected_rows: int | None,
) -> None:
    if expected_sha256 is not None:
        require(
            file_pin["sha256"] == expected_sha256.lower(),
            f"{label} sha256 mismatch: expected {expected_sha256.lower()}, got {file_pin['sha256']}",
        )
    if expected_rows is not None:
        require(
            actual_rows == expected_rows,
            f"{label} row-count mismatch: expected {expected_rows}, got {actual_rows}",
        )


def validate_inputs(
    original: pd.DataFrame, catalog: pd.DataFrame, ml: pd.DataFrame
) -> None:
    require(
        set(REQUIRED_OBSERVATION_COLUMNS) <= set(original.columns),
        "Observation input is missing required columns",
    )
    require(len(original) > 0, "Observation input must not be empty")
    require(original.source_row_id.is_unique, "Observation source_row_id must be unique")
    require(
        original.period_end.map(full_date).notna().all(),
        "Every period_end must be a valid full date",
    )
    require(catalog.tmdb_id.is_unique, "Catalog tmdb_id must be unique")
    require(catalog.service_movie_id.is_unique, "Catalog service_movie_id must be unique")
    require(ml.movieId.is_unique, "MovieLens movieId must be unique")


def validate_linked_outputs(
    original: pd.DataFrame,
    linked: pd.DataFrame,
    movies: pd.DataFrame,
    summary: dict[str, object],
) -> None:
    require_frame_equal(
        linked[original.columns], original, "Linked observations changed the original input columns"
    )
    require(len(linked) == len(original), "Linked observation row count changed")
    require(linked.source_row_id.is_unique, "Linked source_row_id must remain unique")
    require(movies.kobis_movie_code.is_unique, "Confirmed KOBIS movie codes must be unique")
    require(
        movies[NULL_CROSS_SOURCE_COLUMNS].isna().all().all(),
        "Validated output contains a confirmed cross-source or rating value",
    )
    require(
        sum(item["observations"] for item in summary["yearly_coverage"]) == len(original),
        "Yearly coverage does not account for all observations",
    )
    require(
        sum(summary["tmdb_match_status_counts"].values()) == len(movies),
        "TMDB proposal statuses do not account for all confirmed KOBIS movies",
    )
    require(
        summary["confirmed_cross_source_tmdb_ids"] == 0,
        "Proposal-only summary must report zero confirmed TMDB IDs",
    )


def write_outputs(
    directory: Path,
    original: pd.DataFrame,
    linked: pd.DataFrame,
    movies: pd.DataFrame,
    summary: dict[str, object],
) -> None:
    for name, frame in [
        ("linked-observations", linked),
        ("confirmed-movie-comparison", movies),
    ]:
        frame.to_parquet(directory / f"{name}.parquet", index=False)
        frame.to_csv(directory / f"{name}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(summary["yearly_coverage"]).to_csv(
        directory / "yearly-link-coverage.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(summary["nation_coverage"]).to_csv(
        directory / "nation-link-coverage.csv", index=False, encoding="utf-8-sig"
    )
    dump(directory / "summary.json", summary)
    report = f"""# 연도별 KOBIS 자료와 연결 제안 v3

상태: DRAFT — 독립 검산 전.

- 원 연도별 관측 {len(original):,}행을 모두 보존했다.
- 원문에서 직접 확인한 KOBIS 영화코드 {len(movies):,}개만 영화 단위로 정리했다.
- 직접 코드가 없는 {summary['unresolved_identity_observations_not_unique_movies']:,}행은 고유 영화 수로 세지 않았다.
- TMDB 후보 상태: {json.dumps(summary['tmdb_match_status_counts'], ensure_ascii=False)}.
- 공유 식별자로 확인한 TMDB 연결은 0건이다. 확정 TMDB·서비스·MovieLens ID와 연결 평점값은 비워 두었다.

영화별 누적은 코드가 직접 표시된 행 중 가장 늦은 `period_end`의 값을 선택했다. 코드가 없는 유사 메타데이터 행은 이 집계에 넣지 않았다. 현재 누적이나 원개봉부터의 완전 누적을 뜻하지 않는다. 같은 마지막 기간에 서로 다른 누적값이 있으면 충돌로 표시하고 확정값을 비웠다. 연도값을 더하거나 최대값을 취해 누적을 만들지 않았다.

연도별 원행과 음수 보정은 `linked-observations`에, 직접 확인한 KOBIS 코드별 관측 마지막 누적과 TMDB 후보는 `confirmed-movie-comparison`에 있다. 이 파일명에서 확정은 KOBIS 원문 코드만을 뜻한다. 미연결은 영화 부재나 0관객을 뜻하지 않는다. 후보가 여러 개면 후보별 근거 원행을 보존한다.

정확한 제목·개봉일·대표국적에서 유일하게 대응해도 제안일 뿐이다. 확정 교차출처 ID로 승격하지 않으며 서비스와 MovieLens 값을 결합하지 않는다.
"""
    (directory / "REPORT.md").write_text(report, encoding="utf-8")


def validate_written_files(
    directory: Path,
    original_rows: int,
    movie_rows: int,
    final_output: Path,
) -> list[dict[str, object]]:
    expected = [
        "linked-observations.parquet",
        "linked-observations.csv",
        "confirmed-movie-comparison.parquet",
        "confirmed-movie-comparison.csv",
        "yearly-link-coverage.csv",
        "nation-link-coverage.csv",
        "summary.json",
        "REPORT.md",
    ]
    for name in expected:
        path = directory / name
        require(path.is_file(), f"Missing staged output: {name}")
        require(path.stat().st_size > 0, f"Empty staged output: {name}")
    reread_linked = pd.read_parquet(directory / "linked-observations.parquet")
    reread_movies = pd.read_parquet(directory / "confirmed-movie-comparison.parquet")
    require(len(reread_linked) == original_rows, "Staged linked parquet row count changed")
    require(len(reread_movies) == movie_rows, "Staged movie parquet row count changed")
    require(
        reread_movies[NULL_CROSS_SOURCE_COLUMNS].isna().all().all(),
        "Staged parquet contains confirmed cross-source values",
    )
    return [
        pin(directory / name, logical_path=repo_logical_path(final_output / name))
        for name in expected
    ]


def run_linking(
    config: RunConfig,
    *,
    failure_injector: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Execute one fail-closed linking run and atomically publish its directory."""
    require(
        config.execute,
        "Execution requires reviewed --execute",
        ExecutionNotAuthorized,
    )
    output = config.output_path.resolve()
    require(not output.exists(), f"Preserve prior linking results: {output}", FileExistsError)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.parent / f".{output.name}.tmp-{uuid4().hex}"
    require(not temp.exists(), f"Temporary output collision: {temp}", FileExistsError)

    temp.mkdir()
    try:
        source_paths = [
            config.input_path.resolve(),
            config.catalog_path.resolve(),
            config.movielens_aggregates_path.resolve(),
            Path(__file__).resolve(),
        ]
        collection_summary = BASE / "collection-summary.json"
        canonical_observations = (BASE / "annual-observations.parquet").resolve()
        if config.input_path.resolve() == canonical_observations and collection_summary.exists():
            source_paths.append(collection_summary.resolve())
        before = [pin(path) for path in source_paths]

        original = pd.read_parquet(config.input_path)
        catalog = pd.read_parquet(config.catalog_path, columns=CATALOG_COLUMNS)
        ml = pd.read_parquet(config.movielens_aggregates_path, columns=MOVIELENS_COLUMNS)
        validate_inputs(original, catalog, ml)
        check_expected_contract(
            "input", before[0], len(original),
            config.expect_input_sha256, config.expect_input_rows,
        )
        check_expected_contract(
            "catalog", before[1], len(catalog),
            config.expect_catalog_sha256, config.expect_catalog_rows,
        )
        check_expected_contract(
            "movielens-aggregates", before[2], len(ml),
            config.expect_movielens_aggregates_sha256,
            config.expect_movielens_aggregates_rows,
        )

        linked = resolve_identity(original)
        movies = rollup_movies(linked)
        linked, movies = map_tmdb(linked, movies, catalog, ml)
        summary = summarize(linked, movies)
        validate_linked_outputs(original, linked, movies, summary)
        require(
            [pin(path) for path in source_paths] == before,
            "Read-only source drift detected",
        )

        write_outputs(temp, original, linked, movies, summary)
        if failure_injector is not None:
            failure_injector("after_write")
        output_pins = validate_written_files(temp, len(original), len(movies), output)
        manifest = {
            "schema_version": "kobis-expanded-link-v3",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "LINKED_PENDING_INDEPENDENT_REVIEW",
            "proposal_only": True,
            "confirmed_cross_source_ids": 0,
            "inputs": before,
            "expected_contracts": {
                "input": {
                    "sha256": config.expect_input_sha256,
                    "rows": config.expect_input_rows,
                },
                "catalog": {
                    "sha256": config.expect_catalog_sha256,
                    "rows": config.expect_catalog_rows,
                },
                "movielens_aggregates": {
                    "sha256": config.expect_movielens_aggregates_sha256,
                    "rows": config.expect_movielens_aggregates_rows,
                },
            },
            "outputs": output_pins,
        }
        dump(temp / "manifest.json", manifest)
        require(
            json.loads((temp / "manifest.json").read_text(encoding="utf-8")) == manifest,
            "Manifest round-trip validation failed",
        )
        if failure_injector is not None:
            failure_injector("before_publish")
        require(
            [pin(path) for path in source_paths] == before,
            "Read-only source drift detected before publication",
        )
        require(not output.exists(), f"Output appeared before atomic publish: {output}", FileExistsError)
        os.rename(temp, output)
        return summary
    except BaseException:
        if temp.exists():
            shutil.rmtree(temp)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--input", type=Path, default=BASE / "annual-observations.parquet")
    parser.add_argument("--catalog", type=Path, default=CAT)
    parser.add_argument("--movielens-aggregates", type=Path, default=ML)
    parser.add_argument("--output", type=Path, default=BASE / "linked-v3")
    parser.add_argument("--expect-input-sha256")
    parser.add_argument("--expect-input-rows", type=int)
    parser.add_argument("--expect-catalog-sha256", default=DEFAULT_CATALOG_SHA256)
    parser.add_argument("--expect-catalog-rows", type=int, default=DEFAULT_CATALOG_ROWS)
    parser.add_argument("--expect-movielens-aggregates-sha256")
    parser.add_argument(
        "--expect-movielens-aggregates-rows", type=int, default=DEFAULT_MOVIELENS_ROWS
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = RunConfig(
        execute=args.execute,
        input_path=args.input,
        catalog_path=args.catalog,
        movielens_aggregates_path=args.movielens_aggregates,
        output_path=args.output,
        expect_input_sha256=args.expect_input_sha256,
        expect_input_rows=args.expect_input_rows,
        expect_catalog_sha256=args.expect_catalog_sha256,
        expect_catalog_rows=args.expect_catalog_rows,
        expect_movielens_aggregates_sha256=args.expect_movielens_aggregates_sha256,
        expect_movielens_aggregates_rows=args.expect_movielens_aggregates_rows,
    )
    summary = run_linking(config)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "observation_rows": summary["observation_rows"],
                "confirmed_kobis_movie_codes": summary["confirmed_kobis_movie_codes"],
                "confirmed_cross_source_tmdb_ids": summary[
                    "confirmed_cross_source_tmdb_ids"
                ],
                "output": repo_logical_path(config.output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
