"""Export a small provenance-linked coverage report, without publishing source text."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import pandas as pd
from collect_wikipedia_plots import ROOT, OUT, DOC, file_pin, verify_file_pin, write_json, fingerprint


def validate_tables(summary, catalog, mappings, pages, coverage, strata):
    clean_present = pages.plot_text.str.len().gt(0)
    raw_present = pages.plot_wikitext.str.strip().str.len().gt(0)
    section_present = pages.plot_headings.map(len).gt(0)
    if (not pages.page_status.eq('PLOT_COLLECTED').equals(clean_present)
            or not pages.plot_available.equals(clean_present)
            or not pages.plot_characters.eq(pages.plot_text.str.len()).all()
            or (clean_present & (~raw_present | ~section_present)).any()):
        raise RuntimeError('Plot status, availability, source section or length mismatch')
    if not pages.page_status.eq('EMPTY_PLOT_AFTER_CLEANING').equals(section_present & ~clean_present):
        raise RuntimeError('Empty plot section status mismatch')
    for content, digest in [('plot_text', 'plot_text_sha256'), ('plot_wikitext', 'plot_wikitext_sha256')]:
        for value, expected_digest in zip(pages[content], pages[digest]):
            if expected_digest is not None and pd.notna(expected_digest):
                if hashlib.sha256(value.encode('utf-8')).hexdigest() != expected_digest:
                    raise RuntimeError('Plot content hash mismatch')
            elif value:
                raise RuntimeError('Nonempty plot has no content hash')
    ids = set(catalog.movie_id)
    if len(ids) != 85517:
        raise RuntimeError('Unexpected catalog population')
    for name, frame in [('catalog', catalog), ('mappings', mappings), ('coverage', coverage)]:
        if len(frame) != len(ids) or frame.movie_id.duplicated().any() or set(frame.movie_id) != ids:
            raise RuntimeError(f'Population mismatch: {name}')
    accepted = mappings[mappings.mapping_status.str.startswith('MATCH_')]
    if accepted.qid.duplicated().any():
        raise RuntimeError('Accepted QID shared by films')
    expected = {(lang, r.qid) for lang in ('en', 'ko') for r in accepted[accepted[f'{lang}_url'].notna()].itertuples()}
    actual = set(zip(pages.language, pages.qid))
    if expected != actual or len(actual) != len(pages):
        raise RuntimeError('Expected Wikipedia page population mismatch')
    direct = catalog[['movie_id', 'release_year', 'original_language', 'tmdb_vote_count']].merge(mappings, on='movie_id', validate='one_to_one')
    for lang in ('en', 'ko'):
        good = set(pages[pages.language.eq(lang) & pages.page_status.eq('PLOT_COLLECTED')].qid)
        direct[f'{lang}_plot'] = direct.qid.isin(good)
        if coverage[f'{lang}_status'].str.startswith('PENDING_').any():
            raise RuntimeError('Pending collection records')
    direct['any_plot'] = direct.en_plot | direct.ko_plot
    direct['both_plots'] = direct.en_plot & direct.ko_plot
    for column in ('en_plot', 'ko_plot', 'any_plot', 'both_plots'):
        left, right = direct.set_index('movie_id')[column].sort_index(), coverage.set_index('movie_id')[column].sort_index()
        if not left.equals(right):
            raise RuntimeError(f'Movie-level coverage mismatch: {column}')
    checks = {'catalog_movies': len(catalog), 'mapping_movies_processed': len(mappings), 'pages_processed': len(pages),
              'expected_pages_among_processed_mappings': len(expected), 'mapping_status': mappings.mapping_status.value_counts().to_dict(),
              'page_status': pages.page_status.value_counts().to_dict(), 'movies_with_en_plot': int(direct.en_plot.sum()),
              'movies_with_ko_plot': int(direct.ko_plot.sum()), 'movies_with_any_plot': int(direct.any_plot.sum()),
              'movies_with_both_plots': int(direct.both_plots.sum()), 'pages_with_quality_flags': int(pages.quality_flags.map(len).gt(0).sum())}
    if any(summary[k] != v for k, v in checks.items()):
        raise RuntimeError('Summary mismatch')
    if pages.feature_ready.any() or summary['feature_ready']:
        raise RuntimeError('Unexpected model-use flag')
    direct['release_decade'] = ((direct.release_year // 10) * 10).astype('Int64').astype('string').fillna('UNKNOWN')
    direct['vote_count_band'] = pd.cut(direct.tmdb_vote_count, [-1, 0, 9, 49, 499, 4999, float('inf')], labels=['0', '1-9', '10-49', '50-499', '500-4999', '5000+']).astype('string').fillna('UNKNOWN')
    expected_groups = set()
    for dim in ('release_decade', 'original_language', 'vote_count_band'):
        for name, group in direct.groupby(dim, dropna=False):
            key = (dim, str(name)); expected_groups.add(key)
            found = strata[strata.dimension.eq(dim) & strata.group.eq(str(name))]
            if len(found) != 1:
                raise RuntimeError('Strata group missing/duplicated')
            values = {'movies': len(group), 'mapped': int(group.mapping_status.str.startswith('MATCH_').sum())}
            values.update({col: int(group[col].sum()) for col in ['en_plot', 'ko_plot', 'any_plot', 'both_plots']})
            if any(int(found.iloc[0][k]) != v for k, v in values.items()):
                raise RuntimeError('Strata aggregate mismatch')
    if expected_groups != set(zip(strata.dimension, strata.group)):
        raise RuntimeError('Unexpected strata group')


def main():
    seal = json.loads((OUT / 'table-seal.json').read_text(encoding='utf-8'))
    manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
    if seal['fingerprint'] != fingerprint() or manifest['fingerprint'] != fingerprint():
        raise RuntimeError('Collector fingerprint drift')
    verify_file_pin(OUT / 'catalog.parquet', manifest['catalog_pin'])
    for name, pin in seal['files'].items():
        verify_file_pin(OUT / name, pin)
    summary = json.loads((OUT / 'summary.json').read_text(encoding='utf-8'))
    mappings = pd.read_parquet(OUT / 'mappings.parquet')
    pages = pd.read_parquet(OUT / 'pages.parquet')
    coverage = pd.read_parquet(OUT / 'coverage.parquet')
    catalog = pd.read_parquet(OUT / 'catalog.parquet')
    if summary['status'] != 'COLLECTED_PENDING_REVIEW' or len(coverage) != 85517:
        raise RuntimeError('Full collection has not finished')
    for lang in ('en', 'ko'):
        if int(coverage[f'{lang}_plot'].sum()) != summary[f'movies_with_{lang}_plot']:
            raise RuntimeError('Coverage aggregate mismatch')
    strata = pd.read_csv(OUT / 'coverage-strata.csv', keep_default_na=False, dtype={'dimension': 'string', 'group': 'string'})
    validate_tables(summary, catalog, mappings, pages, coverage, strata)
    size = len(coverage)
    fmt = lambda n: f'{int(n):,}'
    pct = lambda n, d=size: f'{n / d * 100:.1f}%' if d else '—'
    a, b, both, any_plot = (summary[k] for k in ['movies_with_en_plot', 'movies_with_ko_plot', 'movies_with_both_plots', 'movies_with_any_plot'])
    text = [
        '# Wikipedia 줄거리 수집 결과', '',
        '상태: DRAFT — 로컬 수집 결과. 독립 검토 상태는 result-review.json을 확인한다.', '',
        f'현재 영화 **{fmt(size)}편 전체의 연결을 조회**했고, 그중 **{fmt(any_plot)}편({pct(any_plot)})**에서',
        '영어 또는 한국어 Wikipedia 줄거리 절에서 비어 있지 않은 정제본문을 확보했다. 모델 학습은 하지 않았다.', '',
        '| 항목 | 영화 수 | 모집단 대비 |', '|---|---:|---:|',
        f'| 영어 줄거리 | {fmt(a)} | {pct(a)} |',
        f'| 한국어 줄거리 | {fmt(b)} | {pct(b)} |',
        f'| 두 언어 모두 | {fmt(both)} | {pct(both)} |',
        f'| 한 언어 이상 | {fmt(any_plot)} | {pct(any_plot)} |',
        f'| 이번 규칙으로 줄거리 미확보 | {fmt(size-any_plot)} | {pct(size-any_plot)} |', '',
        '영어·한국어는 별도로 보관하며 두 언어 합계는 중복 영화를 포함한다.',
        '위 수는 연결 문서의 정제본문 가용량이며 영화 단위 의미 정합성을 전수 확인한 사용 승인 건수가 아니다.',
        '짧은 소개 수준의 줄거리도 포함되어 있으므로 확보 수가 곧 결말까지 갖춘 영화 수는 아니다.', '',
        '## 연결과 문서 상태', '', '| ID 연결 상태 | 영화 수 |', '|---|---:|',
    ]
    text += [f'| {k} | {fmt(v)} |' for k, v in mappings.mapping_status.value_counts().items()]
    text += ['', '| 문서 처리 상태 | 문서 수 |', '|---|---:|']
    text += [f'| {k} | {fmt(v)} |' for k, v in pages.page_status.value_counts().items()]
    text += ['', f'처리한 문서는 {fmt(len(pages))}개다. 예상 QID와 문서의 QID가 다르면 본문을 사용하지 않았다.',
             'ID가 불명확한 영화·해당 언어 문서 없음·줄거리 절 없음·본문 정리 후 빈 상태를 각각 저장했다.', '',
             '## 원문과 정제본문의 차이', '',
             '| 언어 | 인정한 줄거리 절 있음 | 비어 있지 않은 원문 | 비어 있지 않은 정제본문 | 원문 있으나 정제 후 빈 문서 | 템플릿 제거 문서 전체 |', '|---|---:|---:|---:|---:|---:|']
    raw_clean_counts = {}
    for lang in ('en', 'ko'):
        d = pages[pages.language.eq(lang)]
        raw = d.plot_wikitext.str.strip().str.len().gt(0)
        counts = {'recognized_section': int(d.plot_headings.map(len).gt(0).sum()),
                  'nonempty_raw': int(raw.sum()), 'nonempty_clean': int(d.plot_available.sum()),
                  'nonempty_raw_empty_clean': int((raw & ~d.plot_available).sum()),
                  'pages_with_templates_removed': int(d.templates_removed.gt(0).sum())}
        raw_clean_counts[lang] = counts
        text.append('| ' + lang + ' | ' + ' | '.join(fmt(v) for v in counts.values()) + ' |')
    text += ['', '문서 수 기준이다. 빈 줄거리 절과 원문은 있지만 정제 중 전부 제거된 문서를 구분한다.', '',
             '## 정보량과 수집 편향', '', '아래 정보량은 비어 있지 않은 **정제본문 기준**이다.', '',
             '| 언어 | 정제본문 있는 문서 | 길이 중앙값(문자) | 500자 미만 | 이 중 템플릿 제거 |', '|---|---:|---:|---:|---:|']
    for lang in ('en', 'ko'):
        d = pages[pages.language.eq(lang) & pages.plot_available]
        median = f'{d.plot_characters.median():,.1f}' if len(d) else '—'
        text.append(f'| {lang} | {fmt(len(d))} | {median} | {fmt(d.plot_characters.lt(500).sum())} | {fmt(d.templates_removed.gt(0).sum())} |')
    text += ['', '| TMDB 투표 수 | 전체 영화 | 줄거리 확보 | 확보율 |', '|---|---:|---:|---:|']
    for band in ('0', '1-9', '10-49', '50-499', '500-4999', '5000+'):
        r = strata[strata.dimension.eq('vote_count_band') & strata.group.eq(band)]
        if len(r):
            r = r.iloc[0]
            text.append(f'| {band} | {fmt(r.movies)} | {fmt(r.any_plot)} | {pct(r.any_plot, r.movies)} |')
    flags = Counter(x for values in pages.quality_flags for x in values)
    text += ['', f'추출 검토 표시가 있는 문서: {fmt(pages.quality_flags.map(len).gt(0).sum())}개. 종류별 건수: `{dict(flags)}`.',
             '전체 개봉연대·원어·투표 수 구간 집계는 [coverage-strata.csv](coverage-strata.csv)에 있다.', '',
             '## 사용할 때의 범위', '',
             '- 모집단은 기존 TMDB 구조화 정보·텍스트를 갖춘 85,517편이다. 전체 영화·2026년 한국 시장의 전수율이 아니다.',
             '- 현재 Wikipedia 문서다. 과거 MovieLens 평가 시점에 같은 내용이 존재했다고 간주하지 않는다.',
             '- 줄거리 없음은 부정 선호가 아니다. 줄거리가 있는 영화만 비교하면 수집 편향을 다시 확인해야 한다.',
             '- 원문, 추출한 줄거리 wikitext, 정리한 텍스트를 분리했다. 모든 템플릿 본문을 제거하므로 실제 서술이 일부 또는 전부 소실될 수 있다. 다른 문서 transclusion을 펼치지 않는다. 정제 후 빈 상태가 Wikipedia 자체에 줄거리가 없다는 뜻은 아니다.',
             '- API 원문 전체는 캐시에 보존한다. 추출 블록에는 선행 각주·빈 태그가 빠진 사례가 있어 문서의 정확한 원문 사본으로 부르지 않는다. ID가 맞아도 시리즈 전체처럼 범위가 더 넓은 문서가 있을 수 있다. [내용 품질 검토](QUALITY.md)를 따른다.',
             '- 모든 행의 feature_ready는 false다. 다음 표현·비교 설계에서 품질 표시와 길이를 확인한 뒤 사용 여부를 정한다.', '',
             '## 파일과 재현', '',
             f'- [전체 데이터 폴더]({OUT.as_posix()}): 원자료와 수집·연결·추출 결과.',
             '- `pages.parquet`: 언어별 줄거리, 추출한 줄거리 원문·정리본문 해시와 API 응답 해시, page/revision ID, 조회·revision 시각, 원URL, 라이선스, 정리 상태.',
             '- `mappings.parquet`, `coverage.parquet`: 전체 영화의 ID 연결과 누락 상태.',
             '- `cache/`: 공식 API 원응답. 이전 수집의 동일 응답은 wikipedia-plots/cache와 wikipedia-plots-v2/cache를 참조한다.',
             '- 코드와 명령: [수집 설계](PLAN.md), [로컬 실행 문서](../../runbook/local-development.md#27-wikipedia-영화-줄거리-수집).',
             '- 첫 pilot 추출 경계 오류는 보존하고 수정 후 같은 원문을 다시 검토했다. [첫 검토 기록](pilot-v1-review.json).',
             '- 대용량 원문·Parquet은 Git 제외다. 이 수집 코드·문서는 완료 실험의 Jira·MR 게시 범위에 포함하지 않았다.', '',
             'Wikipedia 본문 라이선스는 수집 당시 각 언어 사이트의 공식 rightsinfo 응답을 행별 기록했다.',
             'Wikidata 식별자 데이터의 CC0와 별개다. 원문·revision·기여자 이력 링크를 함께 보존한다.', '',
             '[Wikidata 데이터 접근](https://www.wikidata.org/wiki/Wikidata:Data_access) · '
             '[MediaWiki API](https://www.mediawiki.org/wiki/API:Etiquette)', '']
    report = DOC / 'RESULT.md'
    if report.exists():
        raise RuntimeError('Do not overwrite an existing final collection report')
    report.write_text('\n'.join(text), encoding='utf-8')
    shutil.copy2(OUT / 'coverage-strata.csv', DOC / 'coverage-strata.csv')
    write_json(DOC / 'collection-summary.json', summary)
    write_json(DOC / 'raw-clean-coverage.json', raw_clean_counts)
    files = ['manifest.json', 'table-seal.json', 'catalog.parquet', 'mappings.parquet', 'pages.parquet',
             'coverage.parquet', 'coverage-strata.csv', 'summary.json']
    write_json(DOC / 'report-seal.json', {
        'sources': {str((OUT / name).relative_to(ROOT)): file_pin(OUT / name) for name in files},
        'report_code': file_pin(Path(__file__)),
        'outputs': {str(p.relative_to(ROOT)): file_pin(p) for p in [report, DOC / 'coverage-strata.csv', DOC / 'collection-summary.json', DOC / 'raw-clean-coverage.json']},
    })
    print(json.dumps({'report': str(report), 'movies_with_plot': any_plot}))


if __name__ == '__main__':
    main()
