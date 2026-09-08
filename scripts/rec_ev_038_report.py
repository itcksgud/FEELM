"""Render readable group and judgment indexes from sealed REC038 artifacts."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'outputs/recommendation-evidence/rec-ev-038'
DOC = ROOT / 'docs/recommendation/experiments/rec-ev-038'
NAMES = {
    'RULE_GENRE': '기존 단순 장르 규칙', 'KM_TEXT': '기존 전체 텍스트 + K-means',
    'KM_GENRE': '장르 + K-means', 'KM_TAG': '태그 + K-means', 'KM_BOTH': '장르·태그 + K-means',
    'NMF_GENRE': '장르 + NMF', 'NMF_TAG': '태그 + NMF', 'NMF_BOTH': '장르·태그 + NMF',
}
STATUS = {'COHERENT': '구체적 공통 설명', 'BROAD': '넓은 공통 범주', 'MIXED': '일부 영화만 설명', 'INSUFFICIENT': '원문 부족'}


def read(name):
    return json.loads((DATA / name).read_text(encoding='utf-8'))


def clean(value):
    return str(value).replace('|', ' / ').replace('\n', ' ').replace('\r', ' ')


def render():
    # Verify all phases before using any linked judgment. This never fits a model.
    for name in ('numeric-seal.json', 'description-seal.json', 'judgment-seal.json', 'evaluation-seal.json'):
        if not (DATA / name).is_file():
            raise RuntimeError('Complete every REC038 phase before rendering: missing ' + name)
    from rec_ev_038_evaluation import run
    run()
    result = read('evaluation-summary.json')
    metrics = read('metrics.json')['methods']
    terms = read('top-terms.json')
    cards = {r['movie_id']: r for r in read('card-key.json')}
    keys = {(r['method'], r['native_group']): r for r in read('description-key.json')}
    descriptions = {r['packet']: r for r in read('descriptions.json')}
    lines = ['# REC038 — 8개 안의 실제 군집과 설명', '',
        '상태: DRAFT — 봉인 결과의 읽기용 색인. 개인 연구 자료.', '',
        '각 방법의 01~08은 그 방법 안에서만 쓰는 번호다. 서로 같은 맛이나 색을 뜻하지 않는다.',
        '설명은 군집마다 평가 영화와 겹치지 않는 hash 4편으로 만들었다. 상위 장르·태그는 전체 native 군집의',
        'c-TF-IDF 설명 자료이며 블라인드 설명자와 평가자는 보지 않았다. 대표어가 있다고 원문 공통 내용이 입증되지는 않는다.',
        '태그 전용 안의 대표어 비교 모집단은 태그 지원 61,942편이고 다른 안과 분모가 다르다.',
        'E는 방법마다 같은 128편, D는 방법별 군집에서 뽑은 설명용 4편이다. D의 총256슬롯은 고유85편이다.',
        '이 문서의 영화 제목과 배정은 판정 봉인 이후 연결했다. 원문·정확한 구절은 ignored 산출물에 보존한다.', '']
    for method, label in NAMES.items():
        lines += [f'## {label} ({method})', '',
            '| 군집 | 입력 근거 배정 수 | 장르 상위 3개 | 태그 상위 5개 | 고정 D 설명 | D 설명 상태 | E 영화 수 |',
            '| --- | ---: | --- | --- | --- | --- | ---: |']
        for k in range(8):
            key = keys[(method, k)]; d = descriptions[key['packet']]
            top = lambda channel, n: ', '.join(clean(r['text']) for r in terms[method][channel]['top'][str(k)][:n])
            e = result['methods'][method]['native_groups'][str(k)]['evaluation_movies']
            lines.append(f"| {k+1:02d} | {metrics[method]['native_sizes'][k]:,} | {top('genre',3)} | {top('keyword',5)} | {clean(d['description'])} | {STATUS[d['coherence']]} | {e} |")
        lines += ['', '설명에 사용한 영화와 개별 지지 판정:', '']
        for k in range(8):
            key = keys[(method, k)]; d = descriptions[key['packet']]
            supports = {f['card']: f['support'] for f in d['films']}
            examples = '; '.join(f"{clean(cards[mid]['title'])} [MovieLens {mid}; {supports[cards[mid]['card']]}]" for mid in key['movie_ids'])
            lines.append(f"- **{k+1:02d}** — {examples}. {clean(d['note'])}")
        lines += ['']
    (DOC / 'GROUPS.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')

    frame = pd.read_parquet(DATA / 'evaluation-linked.parquet')
    out = ['# REC038 — 공통 평가 영화별 판정', '', '상태: DRAFT — 봉인된 128편 × 8안의 연결 색인.', '',
        'acceptable은 해당 방법 안의 익명 G코드다. 방법마다 코드 의미가 다르다.',
        '배정 수용은 실제 그룹이 허용 집합에 포함된 경우이며, UNIQUE_ACCEPT는 그 그룹 하나만 맞는 경우다.',
        'NO_NATIVE_EVIDENCE는 태그 등 입력 근거가 없는 배정이다. 임시 표시 배정은 성공으로 합산하지 않았다.',
        '이 블라인드 판정에서는 줄거리를 판단 원문으로 사용했다. 기존 전체 텍스트 안의 E5 입력에도 줄거리가 포함됐다.',
        '이 판정은 AI의 설명 대조이며 실제 사용자의 취향 정답이 아니다.', '']
    for mid, group in frame.groupby('movie_id', sort=True):
        card = cards[int(mid)]
        out += [f"## {clean(card['title'])} — MovieLens {mid} / {card['card']}", '',
            f"태그 지원: {'있음' if card['tag_supported'] else '없음'}. 원문은 evaluation-packets.json의 해당 card에 보존.", '',
            '| 방법 | 실제 군집 | 허용 익명 그룹 | 판정 | 이유 |', '| --- | --- | --- | --- | --- |']
        for method in NAMES:
            row = group[group.method == method].iloc[0]
            native = f'{int(row.native_group)+1:02d} ({row.native_anonymous_group})' if row.native_group >= 0 else '근거 없음'
            accepted = ', '.join(row.acceptable) or '없음'
            out.append(f'| {NAMES[method]} | {native} | {accepted} | {row.category} | {clean(row.reason)} |')
        out += ['']
    (DATA / 'JUDGMENTS.md').write_text('\n'.join(out)+'\n', encoding='utf-8')
    print('RENDERED_64_GROUPS_AND_1024_JUDGMENTS')


if __name__ == '__main__':
    render()
