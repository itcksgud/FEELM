"""Read national cumulative admissions from KOBIS's public movie statistics tab.

The observed official detailLayer.js sends POST {code, sType: 'stat'}.
Only the KOBIS(발권)통계 / 전국 row is the current ticket-system total.
The monthly official yearbook table and daily opening/latest tables are NOT
substitutes. Missing data stays missing. This module performs no retries.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SOURCE_URL = 'https://www.kobis.or.kr/kobis/business/mast/mvie/searchMovieDtl.do'
DEFINITION_URL = 'https://www.kobis.or.kr/kobis/business/stat/boxs/findFormerBoxOfficeList.do'
MAX_RESPONSE_BYTES = 10_000_000


class StatisticsSchemaError(ValueError):
    """A present table has changed or cannot be safely identified."""


class StatisticsUnavailable(RuntimeError):
    """The public source failed; do not bypass or fabricate its data."""


def _text(value: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', value))).strip()


def _count(value: str) -> int | None:
    if value in {'', '-', 'N/A'}:
        return None
    match = re.fullmatch(r'(\d[\d,]*)\s*(?:\(\d+(?:\.\d+)?%\))?', value)
    if not match:
        raise StatisticsSchemaError(f'Unexpected cumulative count cell: {value!r}')
    digits = match.group(1)
    if ',' in digits and not re.fullmatch(r'\d{1,3}(?:,\d{3})+', digits):
        raise StatisticsSchemaError('Malformed thousands grouping')
    return int(digits.replace(',', ''))


def parse_movie_statistics(content: str | bytes, movie_code: str) -> dict[str, Any]:
    """Parse only the ticket-system national cumulative row, never annual totals.

    ``statistics_as_of`` is the date printed by KOBIS, not the fetch date.
    The underlying system's 2004-onward coverage does not establish an original
    lifetime total for earlier films; no completeness claim is returned.
    """
    movie_code = str(movie_code).strip().upper()
    if not re.fullmatch(r'[0-9A-Z]{8}', movie_code):
        raise ValueError('KOBIS movie code must have exactly eight alphanumeric characters')
    if isinstance(content, bytes):
        content = content.decode('utf-8')
    result: dict[str, Any] = {
        'kobis_movie_code': movie_code,
        'status': 'KOBIS_TICKET_STATISTICS_UNAVAILABLE',
        'audience_cumulative': None,
        'sales_krw_cumulative': None,
        'statistics_as_of': None,
        'region': '전국',
        'statistic_source': 'KOBIS(발권)통계',
        'audience_scope': 'NATIONAL_TICKET_SYSTEM_CUMULATIVE_AS_OF_PRINTED_DATE',
        'coverage_start_date': None,
        'original_lifetime_complete': None,
        'source_url': SOURCE_URL,
        'definition_url': DEFINITION_URL,
        'source_query': {'code': movie_code, 'sType': 'stat'},
    }
    if '<error/>' in content:
        result['status'] = 'MOVIE_STATISTICS_NOT_FOUND'
        return result
    headings = list(re.finditer(r'<strong\b[^>]*>(.*?)</strong>', content, re.S))
    hits = [hit for hit in headings if _text(hit.group(1)) == 'KOBIS(발권)통계']
    if not hits:
        return result
    if len(hits) != 1:
        raise StatisticsSchemaError('Ticket-system heading is not unique')
    start = hits[0].end()
    later = [h.start() for h in headings if h.start() > start]
    end = min(later) if later else len(content)
    section = content[start:end]
    expected_code = re.findall(r"dtlExcelDn\('movie','kb','([0-9A-Za-z]{8})'\)", section)
    expected_code = [value.upper() for value in expected_code]
    if expected_code != [movie_code]:
        raise StatisticsSchemaError('Statistics movie code does not match request')
    match_date = re.search(r'통계기준일\s*:\s*(\d{4})\.(\d{2})\.(\d{2})', _text(section))
    if not match_date:
        raise StatisticsSchemaError('Missing explicit daily statistics reference date')
    result['statistics_as_of'] = date(*(int(x) for x in match_date.groups())).isoformat()
    tables = re.findall(r'<table\b[^>]*>(.*?)</table>', section, re.S)
    matches = []
    for table in tables:
        caption = re.search(r'<caption\b[^>]*>(.*?)</caption>', table, re.S)
        if caption and _text(caption.group(1)) == 'KOBIS통계':
            matches.append(table)
    if len(matches) != 1:
        raise StatisticsSchemaError('Ticket-system table caption is not unique')
    table = matches[0]
    headings = [_text(x) for x in re.findall(r'<th\b[^>]*>(.*?)</th>', table, re.S)]
    if len(headings) != 4 or headings[0] != '지역' or headings[2:] != ['누적매출액(점유율)', '누적관객수(점유율)']:
        raise StatisticsSchemaError('Expected national cumulative headers are missing')
    national = []
    for block in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table, re.S):
        cells = [_text(x) for x in re.findall(r'<td\b[^>]*>(.*?)</td>', block, re.S)]
        if cells and cells[0] == '전국':
            national.append(cells)
    if not national:
        result['status'] = 'NATIONAL_CUMULATIVE_ROW_UNAVAILABLE'
        return result
    if len(national) != 1 or len(national[0]) != 4:
        raise StatisticsSchemaError('National cumulative row is not unique or malformed')
    row = national[0]
    result['sales_krw_cumulative'] = _count(row[2])
    result['audience_cumulative'] = _count(row[3])
    result['status'] = ('OK' if result['sales_krw_cumulative'] is not None and result['audience_cumulative'] is not None
                        else 'NATIONAL_CUMULATIVE_VALUES_UNAVAILABLE')
    return result


def fetch_movie_statistics(movie_code: str, raw_dir: str | Path, *,
                           opener: Any = None, timeout: float = 60) -> dict[str, Any]:
    """Fetch once, preserve raw response/provenance, or reuse a pinned cache.

    No authentication, retries, paging, or automatic rate-limit workaround.
    Callers control collection scope, pacing, and stopping on source failures.
    """
    movie_code = str(movie_code).strip().upper()
    if not re.fullmatch(r'[0-9A-Z]{8}', movie_code):
        raise ValueError('KOBIS movie code must have exactly eight alphanumeric characters')
    target = Path(raw_dir)
    target.mkdir(parents=True, exist_ok=True)
    raw_path = target / f'{movie_code}-stat.html'
    query_path = target / f'{movie_code}-stat-query.json'
    if raw_path.exists() or query_path.exists():
        if not raw_path.exists() or not query_path.exists():
            raise StatisticsUnavailable('Incomplete cache; preserve and inspect it before continuing')
        body = raw_path.read_bytes()
        metadata = json.loads(query_path.read_text(encoding='utf-8'))
        if metadata.get('sha256') != hashlib.sha256(body).hexdigest() or metadata.get('query') != {'code': movie_code, 'sType': 'stat'}:
            raise StatisticsUnavailable('Cached source hash or query mismatch')
        if metadata.get('http_status') != 200:
            raise StatisticsUnavailable(f"Cached source failed with HTTP {metadata.get('http_status')}; no retry")
        cached = True
    else:
        query = {'code': movie_code, 'sType': 'stat'}
        request = urllib.request.Request(SOURCE_URL, data=urllib.parse.urlencode(query).encode(),
            headers={'User-Agent': 'FEELM-Local-Data-Comparison/1.0',
                     'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
        if opener is None:
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        started = datetime.now(timezone.utc).isoformat()
        try:
            with opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES+1)
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            body = error.read(MAX_RESPONSE_BYTES+1)
        except (urllib.error.URLError, TimeoutError) as error:
            raise StatisticsUnavailable(f'Public statistics request failed without retry: {type(error).__name__}') from error
        if len(body) > MAX_RESPONSE_BYTES:
            raise StatisticsUnavailable('Statistics response exceeded size bound')
        metadata = {'url': SOURCE_URL, 'method': 'POST', 'query': query,
            'started_at_utc': started, 'fetched_at_utc': datetime.now(timezone.utc).isoformat(),
            'http_status': status, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        raw_path.write_bytes(body)
        query_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        if status != 200:
            raise StatisticsUnavailable(f'Public source returned HTTP {status}; response preserved, no retry')
        cached = False
    result = parse_movie_statistics(body, movie_code)
    result.update({'fetched_at_utc': metadata['fetched_at_utc'], 'raw_path': str(raw_path),
        'raw_sha256': metadata['sha256'], 'query_path': str(query_path), 'cache_reused': cached})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--movie-code', required=True)
    parser.add_argument('--raw-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(fetch_movie_statistics(args.movie_code, args.raw_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
