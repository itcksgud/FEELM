"""Offline independent audit of the full 2004-2026 KOBIS collection.

Uses the standard-library HTML parser, not collector parsing functions. Reads
collection inputs only; --execute writes result-review.json/md in the same output
directory. It performs no network access, identity inference, or model training.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/recommendation-evidence/kobis-expanded-20260913"
ORIGIN = "https://www.kobis.or.kr"
PERIOD = ORIGIN + "/kobis/business/stat/boxs/findPeriodBoxOfficeList.do"
ANNUAL = ORIGIN + "/kobis/business/stat/boxs/findYearlyBoxOfficeList.do"
SCOPE = "KOBIS_TICKET_SYSTEM_AS_OF_QUERY_END_NOT_FULL_LIFETIME"
YEARS = list(range(2004, 2027))
PERIOD_HEADERS = ["순위", "영화명", "개봉일", "매출액", "매출액 점유율", "누적매출액", "관객수", "누적관객수", "스크린수", "상영횟수", "대표국적", "국적", "제작사", "배급사", "등급", "장르", "감독", "배우"]
ANNUAL_HEADERS = ["순위", "영화명", "개봉일", "매출액", "매출액 점유율", "관객수", "스크린수", "상영횟수", "대표국적", "국적", "배급사"]
COLUMNS = ["source_row_id", "screening_year", "period_start", "period_end", "is_partial_year", "rank", "kobis_movie_code", "title", "open_date", "annual_sales_krw", "annual_admissions", "cumulative_sales_krw_at_period_end", "cumulative_admissions_at_period_end", "screens", "shows", "representative_nation", "nations", "production_companies", "distributors", "grades", "genres", "directors", "actors", "has_negative_adjustment", "source_url", "fetched_at_utc", "raw_sha256", "cumulative_scope"]
FINGERPRINTS: dict[str, dict] = {}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    data = path.read_bytes()
    FINGERPRINTS[str(path.relative_to(ROOT)).replace("\\", "/")] = {
        "bytes": len(data), "sha256": digest(data)
    }
    return data


def read_json(path):
    return json.loads(read(path).decode("utf-8-sig"))


def integer(value):
    value = value.replace(",", "")
    require(re.fullmatch(r"-?[0-9]+", value) is not None, f"Invalid integer: {value!r}")
    return int(value)


class Document(HTMLParser):
    """Record every physical table row, its cell types, and explicit movie links."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.text = []
        self.current_row = None
        self.current_cell = None
        self.hidden = 0
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag == "tr":
            require(self.current_row is None, "Nested table row")
            self.current_row = {"cells": [], "kinds": [], "codes": []}
        if tag in ("td", "th") and self.current_row is not None:
            require(self.current_cell is None, "Nested cell")
            self.current_row["kinds"].append(tag)
            self.current_cell = []
        if self.current_row is not None:
            for name, value in attrs:
                if name == "onclick" and value:
                    self.current_row["codes"].extend(re.findall(r"mstView\(\s*'movie'\s*,\s*'([^']+)'\s*\)", value))

    def handle_data(self, value):
        if not self.hidden:
            self.text.append(value)
            if self.current_cell is not None:
                self.current_cell.append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden -= 1
        if tag in ("td", "th") and self.current_cell is not None:
            self.current_row["cells"].append(" ".join("".join(self.current_cell).split()))
            self.current_cell = None
        if tag == "tr" and self.current_row is not None:
            require(self.current_cell is None, "Unclosed cell")
            self.rows.append(self.current_row)
            self.current_row = None


def verify_snapshot(label, url, query):
    path = OUT / "raw" / f"{label}.html"
    data = read(path)
    metadata = read_json(path.with_suffix(".json"))
    require(set(metadata) == {"url", "query", "started_at_utc", "fetched_at_utc", "http_status", "sha256", "bytes"}, f"Metadata schema {label}")
    require(metadata["url"] == url and metadata["query"] == query, f"URL/query {label}")
    require(metadata["http_status"] == 200, f"HTTP status {label}")
    require(metadata["sha256"] == digest(data) and metadata["bytes"] == len(data), f"Bytes/hash {label}")
    require(0 < len(data) <= 30_000_000, f"Response bound {label}")
    started = datetime.fromisoformat(metadata["started_at_utc"])
    fetched = datetime.fromisoformat(metadata["fetched_at_utc"])
    require(started.utcoffset() == fetched.utcoffset() == timedelta(0), f"UTC timestamp {label}")
    require(started <= fetched <= datetime.now(timezone.utc), f"Timestamp order {label}")
    return Document(data.decode("utf-8")), metadata


def parse_export(document, annual, year, end):
    headers = ANNUAL_HEADERS if annual else PERIOD_HEADERS
    header_rows = [i for i, row in enumerate(document.rows) if "th" in row["kinds"]]
    require(len(header_rows) == 1, f"Exactly one export header {year}/{annual}")
    header_index = header_rows[0]
    header = document.rows[header_index]
    require(header["kinds"] == ["th"] * len(headers), "Header cell types")
    require([x.replace(" ", "") for x in header["cells"]] == [x.replace(" ", "") for x in headers], "Export schema")
    require(all(len(x["cells"]) == 1 for x in document.rows[:header_index]), "Unexpected pre-header table data")
    body = document.rows[header_index + 1:]
    require(len(body) > 1, f"Empty export {year}/{annual}")
    require(all(row["kinds"] == ["td"] * len(headers) for row in body), "Physical data-row width/type")
    require(body[-1]["cells"][0] == "합계", "Final total row")
    rows = [row["cells"] for row in body[:-1]]
    require(all(re.fullmatch(r"[0-9]+", row[0]) for row in rows), "Unexpected/skipped physical row")
    require(not any(row["codes"] for row in document.rows), "Unexpected code-bearing export schema")
    total = body[-1]["cells"]
    query_echo = f"조회기간: {year}" if annual else f"조회기간: {year}-01-01~{end}"
    query_echo += " 영화구분: 전체 국적: 전체 지역: 전체"
    require(query_echo in " ".join(" ".join(document.text).split()), "Scope echo")
    admissions_index = 5 if annual else 6
    ranks = [integer(row[0]) for row in rows]
    admissions = [integer(row[admissions_index]) for row in rows]
    require(all(x > 0 for x in ranks) and ranks == sorted(ranks), "Positive nondecreasing source ranks")
    require(admissions == sorted(admissions, reverse=True), "Admission order")
    columns = [3, 5, 6, 7] if annual else [3, 5, 6, 7, 8, 9]
    totals = {}
    for i in columns:
        observed = sum(integer(row[i]) for row in rows)
        require(observed == integer(total[i]), f"Signed total {year}/{annual}/{headers[i]}")
        totals[headers[i]] = observed
    return rows, totals, {
        "all_physical_tr_rows": len(document.rows),
        "descriptive_rows_before_header": header_index,
        "header_rows": 1, "movie_rows": len(rows), "total_rows": 1,
        "unclassified_or_skipped_rows": 0,
    }


def parse_screen(document, rows):
    count_matches = re.findall(r"총\s+([0-9,]+)\s*건", " ".join(document.text))
    require(count_matches and len(set(count_matches)) == 1, "Consistent nonempty screen totals")
    displayed_total = integer(count_matches[0])
    require(displayed_total == len(rows), "Full export equals displayed count")
    coded = [row for row in document.rows if row["codes"]]
    require(len(coded) == min(100, len(rows)), "Exactly first min(100,total) direct codes")
    codes = []
    for position, screen in enumerate(coded):
        require(len(screen["cells"]) == 10 and len(screen["codes"]) == 1, "Screen schema/single direct code")
        code = screen["codes"][0]
        require(re.fullmatch(r"[0-9A-Za-z]{8}", code) is not None, "Direct code format")
        source = rows[position]
        cells = screen["cells"]
        require((integer(cells[0]), cells[1], cells[2], integer(cells[6]), integer(cells[7])) == (integer(source[0]), source[1], source[2], integer(source[6]), integer(source[7])), f"Ordered screen identity at ordinal {position + 1}")
        codes.append(code)
    return codes


def verify_frame(frame, expected, label):
    require(list(frame.columns) == COLUMNS, f"Column schema {label}")
    require(frame.shape == expected.shape, f"Shape {label}: {frame.shape} != {expected.shape}")
    require(frame.source_row_id.is_unique, f"Unique source_row_id {label}")
    equality = frame.eq(expected) | (frame.isna() & expected.isna())
    if not equality.to_numpy().all():
        examples = []
        for i in range(len(frame)):
            for column in COLUMNS:
                if not equality.at[i, column]:
                    examples.append({"source_row_id": expected.at[i, "source_row_id"], "column": column, "stored": str(frame.at[i, column]), "raw_expected": str(expected.at[i, column])})
                    if len(examples) == 5:
                        break
            if len(examples) == 5:
                break
        raise ValueError(f"{label}: {int((~equality).to_numpy().sum())} differing cells; first examples: {json.dumps(examples, ensure_ascii=False)}")
    pd.testing.assert_frame_equal(frame.reset_index(drop=True), expected.reset_index(drop=True), check_dtype=False, check_exact=True, obj=label)


def audit_year(year):
    end = "2026-09-12" if year == 2026 else f"{year}-12-31"
    fields = {"loadEnd": "0", "searchType": "excel", "sMultiMovieYn": "", "sRepNationCd": "", "sWideAreaCd": "", "sSearchFrom": f"{year}-01-01", "sSearchTo": end}
    annual_fields = {"loadEnd": "0", "searchType": "excel", "sMultiMovieYn": "", "sRepNationCd": "", "sWideAreaCd": "", "sSearchYearFrom": str(year)}
    period, pm = verify_snapshot(f"period-{year}-full", PERIOD, fields)
    annual, am = verify_snapshot(f"annual-{year}-full", ANNUAL, annual_fields)
    screen, sm = verify_snapshot(f"period-{year}-screen", PERIOD, {**fields, "searchType": "search"})
    pr, pt, physical_period = parse_export(period, False, year, end)
    ar, at, physical_annual = parse_export(annual, True, year, end)
    # Include screens/shows as well as every shared field in the collector check.
    pprojection = [(r[1], r[2], integer(r[3]), integer(r[6]), integer(r[8]), integer(r[9]), r[10], r[11], r[13]) for r in pr]
    aprojection = [(r[1], r[2], integer(r[3]), integer(r[5]), integer(r[6]), integer(r[7]), r[8], r[9], r[10]) for r in ar]
    require(Counter(pprojection) == Counter(aprojection), f"Every-row annual/period multiset {year}")
    codes = parse_screen(screen, pr)
    expected_rows = []
    for ordinal, r in enumerate(pr, 1):
        signed = [integer(r[i]) for i in (3, 5, 6, 7)]
        expected_rows.append([
            f"{year}:{ordinal}", year, f"{year}-01-01", end, year == 2026,
            integer(r[0]), codes[ordinal - 1] if ordinal <= len(codes) else None,
            r[1], r[2] or None, integer(r[3]), integer(r[6]), integer(r[5]), integer(r[7]),
            integer(r[8]), integer(r[9]), r[10], r[11], r[12], r[13], r[14], r[15], r[16], r[17],
            any(value < 0 for value in signed), pm["url"], pm["fetched_at_utc"], pm["sha256"], SCOPE,
        ])
    expected = pd.DataFrame(expected_rows, columns=COLUMNS)
    parquet_path = OUT / f"year-{year}.parquet"
    read(parquet_path)
    verify_frame(pd.read_parquet(parquet_path), expected, f"year-{year}.parquet")
    checks = read_json(OUT / "checks" / f"{year}.json")
    wanted_checks = {"year": year, "rows": len(pr), "displayed_total": len(pr), "screen_codes": len(codes), "annual_admissions_total": pt["관객수"], "annual_sales_total": pt["매출액"], "annual_export_all_rows_equal": True, "negative_adjustment_rows": int(expected.has_negative_adjustment.sum()), "period_end": end, "raw": [pm, am, sm]}
    require(checks == wanted_checks, f"Saved checks exactly recomputed {year}")
    negative_counts = {column: int((expected[column] < 0).sum()) for column in ("annual_admissions", "annual_sales_krw", "cumulative_admissions_at_period_end", "cumulative_sales_krw_at_period_end")}
    summary = {
        "year": year, "status": "PASS", "period_start": f"{year}-01-01", "period_end": end,
        "is_partial_year": year == 2026, "rows": len(pr), "direct_code_rows": len(codes),
        "unresolved_code_rows": len(pr) - len(codes), "period_physical_rows": physical_period,
        "annual_physical_rows": physical_annual, "period_signed_totals": pt, "annual_signed_totals": at,
        "negative_adjustment_rows": int(expected.has_negative_adjustment.sum()),
        "negative_fields": negative_counts, "all_rows_all_columns_parquet_equal": True,
        "full_annual_period_nine_field_multiset_equal": True,
        "direct_codes_match_same_ordinal_screen_and_tail_is_null": True,
        "blank_open_date_rows": int(expected.open_date.isna().sum()),
        "opening_before_2004_rows": sum(bool(r[2]) and r[2][:4].isdigit() and int(r[2][:4]) < 2004 for r in pr),
        "source_rank_gap_count": len(set(range(1, max(integer(r[0]) for r in pr) + 1)) - {integer(r[0]) for r in pr}),
    }
    return expected, summary


def audit_approval():
    review_path = OUT / "execution-review.json"
    review = read_json(review_path)
    addendum = read_json(OUT / "rank-source-addendum-review.json")
    require(review["status"] == addendum["status"] == "PASS", "Pre-execution reviews must pass")
    for key in ("design_unchanged",):
        record = addendum["authorized_fingerprint"][key]
        data = read(ROOT / record["path"])
        require(record["sha256"] == digest(data) and record["bytes"] == len(data), f"Approved fingerprint {key}")
    baseline = addendum["base_execution_review"]
    require(baseline["sha256"] == digest(review_path.read_bytes()) and baseline["bytes"] == review_path.stat().st_size, "Approval chain")
    require(review["authorized_fingerprint"]["design"] == {**addendum["authorized_fingerprint"]["design_unchanged"]}, "Design approval unchanged")
    text_review = read_json(OUT / "text-parser-addendum-review.json")
    require(text_review["status"] == "PASS", "Text parser pre-execution review")
    prior = text_review["prior_rank_addendum"]
    prior_data = read(ROOT / prior["path"])
    require(digest(prior_data) == prior["sha256"] and len(prior_data) == prior["bytes"], "Text review links exact rank addendum")
    script_record = text_review["authorized_fingerprint"]["script"]
    script_data = read(ROOT / script_record["path"])
    require(digest(script_data) == script_record["sha256"] and len(script_data) == script_record["bytes"], "Current collector matches text review")
    original_script = read(OUT / "history/pre-text-repair/collect_kobis_expanded.py")
    require(digest(original_script) == addendum["authorized_fingerprint"]["script"]["sha256"], "Prior collector preserved")
    return script_record["sha256"]


def audit_repair(expected):
    archive = OUT / "history/pre-text-repair"
    prior_review = read_json(archive / "result-review.json")
    read(archive / "result-review.md")
    require(prior_review["status"] == "FAIL", "Pre-repair failed review preserved")
    manifest = read_json(archive / "archive-manifest.json")
    for record in manifest["files"]:
        archived = Path(record["archive_path"])
        require(archived.is_relative_to(archive), "Archive manifest path confined")
        require(digest(read(archived)) == record["sha256"], "Archived derived file hash")
    old = pd.read_parquet(archive / "annual-observations.parquet")
    require(old.shape == expected.shape and list(old.columns) == COLUMNS, "Repair does not add/drop rows or columns")
    same = old.eq(expected) | (old.isna() & expected.isna())
    differences = {column: int((~same[column]).sum()) for column in COLUMNS if not same[column].all()}
    require(set(differences).issubset({"title", "production_companies", "distributors"}), "Only diagnosed text columns changed")
    count = sum(differences.values())
    require(count == prior_review["pre_repair_diagnostic"]["text_mismatched_cells"], "Every diagnosed text cell restored")
    return {"status": "RESOLVED", "prior_review": "history/pre-text-repair/result-review.json", "corrected_cells": count, "affected_rows": int((~same).any(axis=1).sum()), "corrected_columns": differences, "all_numeric_scope_identity_and_provenance_cells_unchanged": True, "original_derived_outputs_and_collector_hashes_verified": True}


def audit_all(report):
    collector_hash = audit_approval()
    frames = []
    for year in YEARS:
        frame, yearly = audit_year(year)
        frames.append(frame)
        report["yearly"].append(yearly)
        print(json.dumps({"year": year, "rows": len(frame), "status": "PASS"}), flush=True)
    expected = pd.concat(frames, ignore_index=True)
    for name in ("annual-observations.parquet", "annual-observations.csv"):
        read(OUT / name)
    combined = pd.read_parquet(OUT / "annual-observations.parquet")
    verify_frame(combined, expected, "annual-observations.parquet")
    csv = pd.read_csv(OUT / "annual-observations.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    canonical = expected.map(lambda value: "" if pd.isna(value) else str(value))
    pd.testing.assert_frame_equal(csv, canonical, check_dtype=True, check_exact=True, obj="CSV every cell")
    summary = read_json(OUT / "collection-summary.json")
    wanted = {"status": "COLLECTED_PENDING_IDENTITY_LINK_AND_AUDIT", "years": [2004, 2026], "period_end": "2026-09-12", "rows": len(expected), "direct_code_rows": int(expected.kobis_movie_code.notna().sum()), "direct_unique_codes": int(expected.kobis_movie_code.nunique()), "negative_adjustment_rows": int(expected.has_negative_adjustment.sum()), "script_sha256": collector_hash, "new_model_training": False}
    require(summary == wanted, "Collection summary exactly recomputed")
    require(set(expected.screening_year) == set(YEARS), "Every expected year present")
    require(expected.source_row_id.is_unique, "Global ordinal identity unique")
    report["resolved_text_repair"] = audit_repair(expected)
    raw_expected = {f"{kind}-{year}-{mode}.{ext}" for year in YEARS for kind, mode in (("period", "full"), ("annual", "full"), ("period", "screen")) for ext in ("html", "json")}
    require({path.name for path in (OUT / "raw").iterdir()} == raw_expected, "Raw snapshot inventory exactly 69 complete pairs")
    # Fail if any input changed while being checked.
    for relative, recorded in FINGERPRINTS.items():
        data = (ROOT / relative).read_bytes()
        require(len(data) == recorded["bytes"] and digest(data) == recorded["sha256"], f"Input changed during audit: {relative}")
    report["totals"] = {**{k: v for k, v in wanted.items() if k != "status"}, "raw_snapshot_pairs": 69, "verified_years": len(YEARS), "unresolved_code_rows": int(expected.kobis_movie_code.isna().sum()), "new_model_training": False}
    report["checks"] = {name: "PASS" for name in [
        "reviewed_collector_design_and_approval_chain", "all_raw_url_query_status_bytes_sha_and_utc_metadata",
        "all_physical_rows_classified_without_rank_based_row_count", "all_signed_annual_and_cumulative_totals",
        "annual_and_period_full_nine_field_multisets", "all_per_year_parquet_cells_against_raw",
        "combined_parquet_and_csv_all_cells_against_raw", "unique_source_row_id",
        "full_screening_year_scope_and_2026_partial_to_september_12", "negative_correction_flags",
        "only_direct_screen_top100_codes_and_no_guessed_tail_codes", "saved_checks_and_collection_summary",
        "complete_snapshot_inventory", "all_inputs_stable_during_audit", "diagnosed_text_loss_repaired_and_original_failure_preserved",
    ]}
    report["status"] = "PASS"


def write_report(report):
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["input_fingerprints"] = FINGERPRINTS
    report["auditor"] = {"path": str(Path(__file__).relative_to(ROOT)).replace("\\", "/"), "sha256": digest(Path(__file__).read_bytes())}
    (OUT / "result-review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = ["# KOBIS 전체 수집 독립 결과 감사", "", f"상태: **{report['status']}**. 감사 시각: {report['reviewed_at_utc']}.", "", "수집 코드의 파서를 재사용하지 않고 로컬 HTMLParser로 실제 표 행과 모든 저장 셀을 재구성했다. 네트워크 요청·영화 식별 추정·학습·외부 게시를 수행하지 않았다.", ""]
    if report["status"] == "PASS":
        total = report["totals"]
        repair = report["resolved_text_repair"]
        lines += [f"최초 감사에서 발견한 원문 괄호 텍스트 손실 {repair['corrected_cells']:,}셀을 복구하고 재검산했다. 최초 FAIL 기록과 수정 전 산출물은 `history/pre-text-repair`에 보존했다. 수치·행수·식별·조회 범위·출처 값은 바뀌지 않았다.", ""]
        lines += [f"2004–2025 전체 달력 연도와 2026-01-01–09-12 부분 연도, 총 {total['rows']:,}개 **영화×상영연도 관측**을 검산했다. 고유 영화 수로 해석하지 않는다.", "", f"69개 원문/메타데이터 쌍의 URL·조건·상태·바이트·SHA256·UTC 시각, 연도별 물리 행수와 전체 연간/누적 관객·매출 및 스크린/상영횟수 합계, 두 Excel의 모든 영화 행 다중집합, 연도별 Parquet 및 통합 Parquet/CSV의 모든 셀이 일치한다. source_row_id는 전체에서 유일하다.", "", f"직접 코드 {total['direct_code_rows']:,}행 / 고유 코드 {total['direct_unique_codes']:,}개, 코드 미확정 {total['unresolved_code_rows']:,}행이다. 코드는 각 연도 화면의 첫 100행에서 동일 순서·작품 정보를 확인한 값만 저장했다. 음수 정정 {total['negative_adjustment_rows']:,}행을 보존하고 flag를 전수 확인했다.", "", "누적값은 KOBIS 발권통계의 각 조회 종료일 기준 값이다. 연도별 누적값을 합산한 영화 생애 총관객 수나 최종 최신 누적값을 이번 감사가 확정하지 않는다. 2004 이전 개봉작도 해당 기간 상영 실적이 있으면 포함된다. 별도 코드 연결, 영화별 누적값 정리, 모델 입력 적합성·추천 품질은 이 수집 감사의 승인 범위가 아니다.", "", "|연도|영화 행|직접 코드|음수 정정|연간 관객 합계|", "|---|---:|---:|---:|---:|"]
        lines += [f"|{r['year']}{' (09-12까지)' if r['is_partial_year'] else ''}|{r['rows']:,}|{r['direct_code_rows']:,}|{r['negative_adjustment_rows']:,}|{r['period_signed_totals']['관객수']:,}|" for r in report["yearly"]]
    else:
        lines += ["감사는 완료되지 않았다. 통과한 연도도 전체 자료 승인으로 간주하지 않는다.", "", f"실패: {report['blocking_findings'][0]}"]
    lines += ["", "재현: `python scripts/audit_kobis_expanded.py --execute`. 상세 항목·원본과 산출물의 해시는 `result-review.json`에 보존한다.", ""]
    (OUT / "result-review.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--execute", action="store_true", help="Audit all 23 years and write final review")
    group.add_argument("--check-year", type=int, choices=YEARS, help="Check one completed year without writing a report")
    args = parser.parse_args()
    if args.check_year:
        _, result = audit_year(args.check_year)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    report = {"schema_version": 1, "review_id": "kobis-expanded-20260913-independent-result-review", "reviewer": "/root/kobis_collection_result_audit", "reviewed_at_utc": datetime.now(timezone.utc).isoformat(), "status": "FAIL", "scope": "Offline full annual observation collection integrity only; identity linking, final lifetime cumulative admissions, model features, training and recommendation-quality claims excluded.", "yearly": [], "blocking_findings": []}
    try:
        audit_all(report)
    except Exception as exc:
        report["blocking_findings"].append(f"{type(exc).__name__}: {exc}")
    write_report(report)
    print(json.dumps({"status": report["status"], "blocking_findings": report["blocking_findings"]}, ensure_ascii=False))
    if report["status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
