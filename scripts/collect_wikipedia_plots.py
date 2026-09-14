"""Collect identity-checked Wikipedia plot sections; never reads user ratings."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlparse

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/research/wikipedia-collection'
OUT = ROOT / 'outputs/recommendation-evidence/wikipedia-plots-v3'
RAW_CACHE_FALLBACKS = [ROOT / f'outputs/recommendation-evidence/{name}/cache'
                       for name in ('wikipedia-plots-v2', 'wikipedia-plots')]
SOURCES = {
    'identity': ROOT / 'outputs/recommendation-evidence/rec-ev-027-catalog/movie-identity.parquet',
    'metadata': ROOT / 'outputs/recommendation-evidence/rec-ev-045/metadata.parquet',
    'titles': ROOT / 'outputs/recommendation-evidence/rec-ev-033/metadata.parquet',
}
UA = 'FEELMResearch/0.3 (https://github.com/itcksgud/FEELM; local academic recommendation research) Python-requests'
HEAD = re.compile(r'^(={2,6})\s*(.*?)\s*\1\s*$', re.M)
PLOT_HEADINGS = {'plot', 'plot summary', 'synopsis', '줄거리', '시놉시스'}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(2**20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temp, path)


def write_parquet(path, df):
    temp = path.with_suffix('.tmp.parquet')
    df.to_parquet(temp, index=False)
    os.replace(temp, path)


def file_pin(path):
    return {'bytes': path.stat().st_size, 'sha256': sha(path)}


def verify_file_pin(path, expected):
    if file_pin(path) != expected:
        raise RuntimeError(f'Derived file integrity failure: {path.name}')


def seal_tables(out, names):
    write_json(out / 'table-seal.json', {'fingerprint': fingerprint(), 'files': {name: file_pin(out / name) for name in names}})


def fingerprint():
    return {str(p.relative_to(ROOT)): sha(p) for p in [Path(__file__).resolve(), ROOT / 'scripts/test_wikipedia_plots.py', DOC / 'PLAN.md']}


def reviewed():
    r = json.loads((DOC / 'execution-review.json').read_text(encoding='utf-8'))
    if r['status'] != 'PASS' or r['fingerprint'] != fingerprint():
        raise RuntimeError('Independent execution review missing or stale')


class Client:
    def __init__(self, out):
        self.out = out
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': UA, 'Accept-Encoding': 'gzip, deflate'})
        self.last = 0.
        self.network = 0
        self.hits = 0

    def get(self, url, params, post=False):
        spec = {'url': url, 'params': params, 'method': 'POST' if post else 'GET'}
        key = hashlib.sha256(canonical(spec)).hexdigest()
        path = self.out / 'cache' / key[:2] / (key + '.json.gz')
        compatible_specs = [spec]
        if url.endswith('/w/api.php') and params.get('action') == 'query':
            # The same read-only query can reuse an earlier POST/GET response. Preserve its actual request.
            compatible_specs.append({**spec, 'method': 'GET' if post else 'POST'})
        for cached_spec in compatible_specs:
            cached_key = hashlib.sha256(canonical(cached_spec)).hexdigest()
            candidates = [base / cached_key[:2] / (cached_key + '.json.gz') for base in [self.out / 'cache', *RAW_CACHE_FALLBACKS]]
            cached_path = next((p for p in candidates if p.exists()), None)
            if cached_path is None:
                continue
            with gzip.open(cached_path, 'rt', encoding='utf-8') as f:
                record = json.load(f)
            if record['request'] != cached_spec or hashlib.sha256(canonical(record['body'])).hexdigest() != record['body_sha256']:
                raise RuntimeError('Cached request/body integrity failure')
            self.hits += 1
            return record, str(cached_path.relative_to(ROOT))
        for attempt in range(6):
            time.sleep(max(0, 1.0 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            delay = min(60, 2 ** (attempt + 1))
            try:
                r = self.session.post(url, data=params, headers={'Promise-Non-Write-API-Action': 'true'}, timeout=(15, 90)) if post else self.session.get(url, params=params, timeout=(15, 90))
                self.network += 1
                if r.status_code in (429, 500, 502, 503, 504):
                    after = r.headers.get('Retry-After', '')
                    try:
                        delay = max(delay, float(after))
                    except ValueError:
                        try:
                            delay = max(delay, (parsedate_to_datetime(after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                    raise requests.exceptions.RetryError(f'HTTP {r.status_code}; retry after {delay}s')
                r.raise_for_status()
                body = r.json()
                if 'error' in body:
                    if body['error'].get('code') in ('maxlag', 'ratelimited', 'readonly'):
                        raise requests.exceptions.RetryError(str(body['error']))
                    raise RuntimeError(f'API error: {body["error"]}')
                if body.get('warnings'):
                    raise RuntimeError(f'API warnings require review: {body["warnings"]}')
                record = {'request': spec, 'fetched_at': now(), 'body': body,
                          'body_sha256': hashlib.sha256(canonical(body)).hexdigest(), 'http_status': r.status_code}
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix('.tmp')
                with gzip.open(temp, 'wt', encoding='utf-8') as f:
                    json.dump(record, f, ensure_ascii=False)
                os.replace(temp, path)
                return record, str(path.relative_to(ROOT))
            except (requests.exceptions.RetryError, requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                print(json.dumps({'event': 'RETRY', 'attempt': attempt + 1, 'error': str(e)[:200], 'seconds': delay}), flush=True)
                if attempt == 5:
                    raise
                time.sleep(delay)
        raise AssertionError('unreachable')


def prepare(out):
    out.mkdir(parents=True, exist_ok=True)
    pins = {k: {'path': str(v.relative_to(ROOT)), 'bytes': v.stat().st_size, 'sha256': sha(v)} for k, v in SOURCES.items()}
    manifest = {'sources': pins, 'fingerprint': fingerprint(), 'expected_movies': 85517}
    if (out / 'manifest.json').exists():
        old = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
        if {k: v for k, v in old.items() if k != 'catalog_pin'} != manifest:
            raise RuntimeError('Collection source/code drift; do not mix snapshots')
        verify_file_pin(out / 'catalog.parquet', old['catalog_pin'])
        return pd.read_parquet(out / 'catalog.parquet')
    identity = pd.read_parquet(SOURCES['identity'])
    meta = pd.read_parquet(SOURCES['metadata'], columns=['movie_id', 'tmdb_id', 'release_year', 'original_language', 'tmdb_vote_count'])
    titles = pd.read_parquet(SOURCES['titles'], columns=['movie_id', 'tmdb_id', 'title', 'original_title'])
    d = meta.merge(identity[['movie_id', 'imdb_id', 'tmdb_id', 'identity_status']], on=['movie_id', 'tmdb_id'], validate='one_to_one')
    d = d.merge(titles, on=['movie_id', 'tmdb_id'], validate='one_to_one')
    if len(d) != 85517 or d.movie_id.duplicated().any() or d.tmdb_id.isna().any():
        raise RuntimeError('Catalog alignment failure')
    d['tmdb_id'] = d.tmdb_id.astype('int64')
    d['imdb_id'] = d.imdb_id.fillna('')
    if not d.imdb_id.map(lambda s: not s or bool(re.fullmatch(r'tt\d+', s))).all():
        raise RuntimeError('Invalid IMDb ID')
    d['collection_order'] = d.movie_id.map(lambda x: hashlib.sha256(f'wikipedia-plots-v1|{x}'.encode()).hexdigest())
    d = d.sort_values('collection_order').reset_index(drop=True)
    write_parquet(out / 'catalog.parquet', d)
    manifest['catalog_pin'] = file_pin(out / 'catalog.parquet')
    write_json(out / 'manifest.json', manifest)
    return d


def query_for(rows):
    values = set()
    for row in rows:
        values.add(f'("{int(row["tmdb_id"])}" wdt:P4947)')
        if row['imdb_id']:
            values.add(f'("{row["imdb_id"]}" wdt:P345)')
    return '''SELECT DISTINCT ?lookup ?item ?tmdb ?imdb ?en ?ko WHERE {
VALUES (?lookup ?property) { ''' + ' '.join(sorted(values)) + ''' }
?item ?property ?lookup .
OPTIONAL { ?item wdt:P4947 ?tmdb } OPTIONAL { ?item wdt:P345 ?imdb }
OPTIONAL { ?en schema:about ?item; schema:isPartOf <https://en.wikipedia.org/> }
OPTIONAL { ?ko schema:about ?item; schema:isPartOf <https://ko.wikipedia.org/> }
}'''


def resolve_identity(row, bindings):
    tmdb, imdb = str(int(row['tmdb_id'])), row['imdb_id']
    relevant = [b for b in bindings if b.get('lookup', {}).get('value') in {tmdb, imdb}]
    qids = {b['item']['value'].rsplit('/', 1)[-1] for b in relevant}
    result = {'movie_id': int(row['movie_id']), 'tmdb_id': int(tmdb), 'imdb_id': imdb,
              'mapping_status': 'NO_ID_MATCH', 'qid': None, 'en_url': None, 'ko_url': None,
              'candidate_qids': sorted(qids)}
    if not qids:
        return result
    if len(qids) != 1:
        result['mapping_status'] = 'REVIEW_MULTIPLE_QIDS'
        return result
    qid = next(iter(qids))
    if not re.fullmatch(r'Q[1-9]\d*', qid):
        raise RuntimeError('Invalid QID')
    ids = {key: {b[key]['value'] for b in relevant if key in b} for key in ('tmdb', 'imdb', 'en', 'ko')}
    if (ids['tmdb'] and ids['tmdb'] != {tmdb}) or (imdb and ids['imdb'] and ids['imdb'] != {imdb}):
        result['mapping_status'] = 'REVIEW_CONFLICTING_IDS'
        return result
    if len(ids['en']) > 1 or len(ids['ko']) > 1:
        result['mapping_status'] = 'REVIEW_MULTIPLE_SITELINKS'
        return result
    result.update(qid=qid, mapping_status='MATCH_BOTH_IDS' if tmdb in ids['tmdb'] and imdb and imdb in ids['imdb'] else 'MATCH_SINGLE_ID')
    for lang in ('en', 'ko'):
        url = next(iter(ids[lang]), None)
        if url:
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.netloc != f'{lang}.wikipedia.org' or not parsed.path.startswith('/wiki/') or parsed.fragment:
                raise RuntimeError('Invalid sitelink URL')
        result[f'{lang}_url'] = url
    return result


def map_movies(out, d, client, max_batches=None):
    rows = d.to_dict('records')
    results = []
    for start in range(0, len(rows), 100):
        if max_batches is not None and start // 100 >= max_batches:
            break
        batch = rows[start:start + 100]
        record, cache = client.get('https://query.wikidata.org/sparql', {'query': query_for(batch), 'format': 'json'}, post=True)
        bindings = record['body']['results']['bindings']
        for row in batch:
            result = resolve_identity(row, bindings)
            result.update(mapping_cache=cache, mapping_fetched_at=record['fetched_at'])
            results.append(result)
        if (start // 100) % 10 == 0:
            print(json.dumps({'stage': 'mapping', 'movies': len(results), 'total': len(rows), 'network': client.network, 'cache_hits': client.hits}), flush=True)
    df = pd.DataFrame(results)
    accepted = df.mapping_status.str.startswith('MATCH_')
    duplicate_qids = set(df.loc[accepted & df.qid.duplicated(keep=False), 'qid'].dropna())
    if duplicate_qids:
        df.loc[accepted & df.qid.isin(duplicate_qids), 'mapping_status'] = 'REVIEW_QID_SHARED_BY_MOVIES'
    write_parquet(out / 'mappings.parquet', df)
    write_json(out / 'mapping-summary.json', {'movies': len(df), 'total': len(d), 'status': df.mapping_status.value_counts().to_dict(), 'network': client.network, 'cache_hits': client.hits, 'license': 'CC0', 'license_url': 'https://creativecommons.org/publicdomain/zero/1.0/'})
    return df


def sections(wikitext):
    # Comments cannot introduce a real section boundary.
    text = re.sub(r'<!--.*?-->', '', wikitext, flags=re.S)
    # Literal/extension tags can contain heading-looking text, not actual sections.
    masked = re.sub(r'<(nowiki|pre|source|syntaxhighlight|ref)\b[^>]*(?<!/)>.*?</\1\s*>',
                    lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text, flags=re.S | re.I)
    matches = list(HEAD.finditer(masked))
    chosen = []
    for i, m in enumerate(matches):
        label = re.sub(r"'{2,}", '', m.group(2)).strip().casefold()
        if label not in PLOT_HEADINGS:
            continue
        end = next((x.start() for x in matches[i + 1:] if len(x.group(1)) <= len(m.group(1))), len(text))
        # Do not duplicate a recognized heading nested inside an already selected plot.
        if any(start <= m.start() < stop for start, stop, _, _ in chosen):
            continue
        chosen.append((m.start(), end, m.group(2), text[m.end():end].strip()))
    return [{'heading': title, 'wikitext': body} for _, _, title, body in chosen]


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'p', 'div'):
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('p', 'div'):
            self.parts.append('\n')


def strip_balanced(text, opening, closing):
    result, i, depth = [], 0, 0
    while i < len(text):
        if text.startswith(opening, i):
            depth += 1
            i += len(opening)
        elif depth and text.startswith(closing, i):
            depth -= 1
            i += len(closing)
        else:
            if not depth:
                result.append(text[i])
            i += 1
    return ''.join(result), depth != 0


def plain_plot(text):
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    text = re.sub(r'<ref\b[^>]*?/>|<ref\b[^>]*>.*?</ref\s*>', '', text, flags=re.S | re.I)
    text = re.sub(r'<(gallery|timeline|math|score)\b[^>]*>.*?</\1\s*>', '', text, flags=re.S | re.I)
    template_count = text.count('{{')
    text, unclosed_template = strip_balanced(text, '{{', '}}')
    text, unclosed_table = strip_balanced(text, '{|', '|}')
    # File/category constructs are not prose, including nested caption links.
    text = re.sub(r'\[\[(?:File|Image|Category|파일|그림|분류):(?:[^\[\]]|\[\[[^\[\]]*\]\])*\]\]', '', text, flags=re.I)
    while re.search(r'\[\[[^\[\]]+\]\]', text):
        text = re.sub(r'\[\[([^\[\]]+)\]\]', lambda m: m.group(1).split('|')[-1].split('#')[0], text)
    text = re.sub(r'\[https?://\S+(?:\s+([^\]]*))?\]', lambda m: m.group(1) or '', text)
    text = HEAD.sub(lambda m: '\n' + m.group(2) + '\n', text)
    text = re.sub(r"'{2,5}", '', text)
    parser = PlainHTML()
    parser.feed(text)
    plain = html.unescape(''.join(parser.parts))
    plain = re.sub(r'[ \t]+', ' ', plain)
    plain = re.sub(r'\n\s*\n+', '\n\n', plain).strip()
    flags = []
    if unclosed_template or unclosed_table:
        flags.append('UNBALANCED_MARKUP')
    if any(x in plain for x in ('[[', ']]', '{{', '}}', '<ref', '{|', '|}')):
        flags.append('RESIDUAL_MARKUP')
    return plain, template_count, flags


def page_record(page, expected, record, cache, lang, rights):
    result = {'language': lang, 'qid': expected['qid'], 'requested_title': expected['requested_title'],
              'page_status': 'NO_PAGE', 'page_id': page.get('pageid'), 'title': page.get('title'),
              'page_qid': (page.get('pageprops') or {}).get('wikibase_item'),
              'revision_id': None, 'revision_timestamp': None, 'source_url': page.get('fullurl'),
              'revision_url': None, 'history_url': None, 'fetched_at': record['fetched_at'],
              'cache_path': cache, 'response_sha256': record['body_sha256'],
              'license_url': rights['url'], 'license_name': rights['text'],
              'plot_headings': [], 'plot_wikitext': '', 'plot_text': '', 'plot_characters': 0,
              'plot_wikitext_sha256': None, 'plot_text_sha256': None, 'extraction_version': 'wikipedia-plots-v2',
              'templates_removed': 0, 'quality_flags': [], 'plot_available': False,
              'feature_ready': False}
    if page.get('missing') or page.get('invalid'):
        return result
    if page.get('ns') != 0 or 'disambiguation' in (page.get('pageprops') or {}):
        result['page_status'] = 'REVIEW_NON_FILM_PAGE'
        return result
    if result['page_qid'] != expected['qid']:
        result['page_status'] = 'REVIEW_PAGE_QID_MISMATCH'
        return result
    revs = page.get('revisions', [])
    if len(revs) != 1 or 'content' not in revs[0].get('slots', {}).get('main', {}):
        result['page_status'] = 'NO_VISIBLE_REVISION'
        return result
    rev = revs[0]
    result.update(revision_id=rev['revid'], revision_timestamp=rev['timestamp'],
                  revision_url=f'https://{lang}.wikipedia.org/w/index.php?oldid={rev["revid"]}',
                  history_url=f'https://{lang}.wikipedia.org/w/index.php?curid={page["pageid"]}&action=history')
    chosen = sections(rev['slots']['main']['content'])
    result['plot_headings'] = [x['heading'] for x in chosen]
    result['plot_wikitext'] = '\n\n'.join(x['wikitext'] for x in chosen)
    plain, count, flags = plain_plot(result['plot_wikitext'])
    if re.search(r'<(nowiki|pre|source|syntaxhighlight)\b', result['plot_wikitext'], re.I):
        flags.append('LITERAL_MARKUP_REVIEW')
    result.update(plot_text=plain, plot_characters=len(plain), templates_removed=count, quality_flags=flags,
                  plot_available=bool(plain), page_status='PLOT_COLLECTED' if plain else ('EMPTY_PLOT_AFTER_CLEANING' if chosen else 'NO_PLOT_SECTION'))
    result['plot_wikitext_sha256'] = hashlib.sha256(result['plot_wikitext'].encode('utf-8')).hexdigest()
    result['plot_text_sha256'] = hashlib.sha256(plain.encode('utf-8')).hexdigest()
    # Collection success alone is not authorization to use this new representation in a model.
    return result


def collect_pages(out, mappings, client, max_batches=None):
    all_results = []
    batch_number = 0
    for lang in ('en', 'ko'):
        endpoint = f'https://{lang}.wikipedia.org/w/api.php'
        license_record, license_cache = client.get(endpoint, {'action': 'query', 'format': 'json', 'meta': 'siteinfo', 'siprop': 'rightsinfo', 'maxlag': 5})
        rights = license_record['body']['query']['rightsinfo']
        if not rights.get('url') or not rights.get('text'):
            raise RuntimeError('License not available')
        rows = mappings[mappings.mapping_status.str.startswith('MATCH_') & mappings[f'{lang}_url'].notna()]
        expected = []
        for row in rows.drop_duplicates('qid').to_dict('records'):
            title = unquote(urlparse(row[f'{lang}_url']).path[len('/wiki/'):]).replace('_', ' ')
            if '|' in title:
                raise RuntimeError('Ambiguous title separator')
            expected.append({'qid': row['qid'], 'requested_title': title})
        expected.sort(key=lambda r: r['qid'])
        for start in range(0, len(expected), 50):
            if max_batches is not None and start // 50 >= max_batches:
                break
            batch = expected[start:start + 50]
            params = {'action': 'query', 'format': 'json', 'formatversion': 2, 'titles': '|'.join(x['requested_title'] for x in batch),
                      'prop': 'info|pageprops|revisions', 'rvprop': 'ids|timestamp|content', 'rvslots': 'main',
                      'inprop': 'url', 'redirects': 1, 'maxlag': 5}
            # GET is the documented default for reading; reserve POST for overlong URLs.
            post = len(requests.Request('GET', endpoint, params=params).prepare().url) > 7000
            record, cache = client.get(endpoint, params, post=post)
            if record['body'].get('continue'):
                raise RuntimeError('Unexpected content continuation; must handle before accepting batch')
            query = record['body']['query']
            aliases = {r['from']: r['to'] for key in ('normalized', 'converted', 'redirects') for r in query.get(key, [])}
            pages = {p['title']: p for p in query['pages']}
            for row in batch:
                title, seen = row['requested_title'], set()
                while title in aliases and title not in seen:
                    seen.add(title)
                    title = aliases[title]
                if title not in pages:
                    raise RuntimeError(f'Page response missing requested title: {title}')
                result = page_record(pages[title], row, record, cache, lang, rights)
                result['license_cache'] = license_cache
                all_results.append(result)
            batch_number += 1
            if batch_number % 10 == 0 or start == 0:
                print(json.dumps({'stage': 'pages', 'language': lang, 'pages': len(all_results), 'language_expected': len(expected), 'network': client.network, 'cache_hits': client.hits}), flush=True)
    df = pd.DataFrame(all_results)
    if not len(df):
        empty = page_record({'missing': True}, {'qid': None, 'requested_title': ''},
                            {'fetched_at': '', 'body_sha256': ''}, '', 'en', {'url': '', 'text': ''})
        df = pd.DataFrame(columns=[*empty, 'license_cache'])
    write_parquet(out / 'pages.parquet', df)
    return df


def summarize(out, catalog, mappings, pages):
    joined = catalog.merge(mappings[['movie_id', 'mapping_status', 'qid', 'en_url', 'ko_url']], on='movie_id', how='left', validate='one_to_one')
    for lang in ('en', 'ko'):
        p = pages[pages.language.eq(lang)] if len(pages) else pd.DataFrame(columns=['qid', 'page_status', 'plot_characters'])
        joined = joined.merge(p[['qid', 'page_status', 'plot_characters']].rename(columns={'page_status': f'{lang}_status', 'plot_characters': f'{lang}_characters'}), on='qid', how='left', validate='many_to_one')
        pending = joined[f'{lang}_status'].isna()
        joined.loc[pending, f'{lang}_status'] = 'PENDING_FETCH'
        joined.loc[pending & joined[f'{lang}_url'].isna(), f'{lang}_status'] = 'NO_SITELINK'
        joined.loc[pending & ~joined.mapping_status.fillna('').str.startswith('MATCH_'), f'{lang}_status'] = 'NO_ACCEPTED_IDENTITY'
        joined.loc[joined.mapping_status.isna(), f'{lang}_status'] = 'PENDING_MAPPING'
        joined[f'{lang}_plot'] = joined[f'{lang}_status'].eq('PLOT_COLLECTED')
    joined['any_plot'] = joined.en_plot | joined.ko_plot
    joined['both_plots'] = joined.en_plot & joined.ko_plot
    joined['release_decade'] = ((joined.release_year // 10) * 10).astype('Int64').astype('string').fillna('UNKNOWN')
    joined['vote_count_band'] = pd.cut(joined.tmdb_vote_count, [-1, 0, 9, 49, 499, 4999, float('inf')], labels=['0', '1-9', '10-49', '50-499', '500-4999', '5000+']).astype('string').fillna('UNKNOWN')
    strata = []
    for column in ('release_decade', 'original_language', 'vote_count_band'):
        for name, group in joined.groupby(column, dropna=False):
            strata.append({'dimension': column, 'group': str(name), 'movies': len(group), 'mapped': int(group.mapping_status.fillna('').str.startswith('MATCH_').sum()), 'en_plot': int(group.en_plot.sum()), 'ko_plot': int(group.ko_plot.sum()), 'any_plot': int(group.any_plot.sum()), 'both_plots': int(group.both_plots.sum())})
    write_parquet(out / 'coverage.parquet', joined)
    pd.DataFrame(strata).to_csv(out / 'coverage-strata.csv', index=False, encoding='utf-8-sig')
    expected_pages = sum(len(mappings[mappings.mapping_status.str.startswith('MATCH_') & mappings[f'{lang}_url'].notna()].drop_duplicates('qid')) for lang in ('en', 'ko'))
    summary = {'generated_at': now(), 'status': 'COLLECTED_PENDING_REVIEW' if len(mappings) == len(catalog) and len(pages) == expected_pages else 'PARTIAL',
               'catalog_movies': len(catalog), 'mapping_movies_processed': len(mappings), 'mapping_status': mappings.mapping_status.value_counts().to_dict(),
               'expected_pages_among_processed_mappings': expected_pages, 'pages_processed': len(pages),
               'page_status': pages.page_status.value_counts().to_dict() if len(pages) else {},
               'pages_with_quality_flags': int(pages.quality_flags.map(len).gt(0).sum()) if len(pages) else 0,
               'movies_with_en_plot': int(joined.en_plot.sum()), 'movies_with_ko_plot': int(joined.ko_plot.sum()),
               'movies_with_any_plot': int(joined.any_plot.sum()), 'movies_with_both_plots': int(joined.both_plots.sum()),
               'feature_ready': False, 'no_model_training': True, 'no_user_ratings_read': True}
    write_json(out / 'summary.json', summary)
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['prepare', 'pilot', 'run', 'summarize'])
    args = parser.parse_args()
    reviewed()
    d = prepare(OUT)
    if args.stage == 'prepare':
        print(json.dumps({'prepared_movies': len(d)}), flush=True)
        return
    client = Client(OUT)
    if args.stage == 'summarize':
        seal = json.loads((OUT / 'table-seal.json').read_text(encoding='utf-8'))
        if seal['fingerprint'] != fingerprint():
            raise RuntimeError('Derived table code version drift')
        for name in ('mappings.parquet', 'pages.parquet'):
            verify_file_pin(OUT / name, seal['files'][name])
        mappings = pd.read_parquet(OUT / 'mappings.parquet')
        pages = pd.read_parquet(OUT / 'pages.parquet')
    else:
        if args.stage == 'run':
            gate = json.loads((DOC / 'pilot-review.json').read_text(encoding='utf-8'))
            if gate['status'] != 'PASS' or gate['fingerprint'] != fingerprint():
                raise RuntimeError('Pilot review missing or stale')
        mappings = map_movies(OUT, d, client, 2 if args.stage == 'pilot' else None)
        pages = collect_pages(OUT, mappings, client, 1 if args.stage == 'pilot' else None)
        seal_tables(OUT, ['mappings.parquet', 'pages.parquet'])
    summarize(OUT, d, mappings, pages)


if __name__ == '__main__':
    main()
