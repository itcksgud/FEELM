"""Render the completed, sealed final FM/GBT comparison without choosing new rules."""
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from final344_common import *

def number(x, digits=4):
    return 'N/A' if x is None or pd.isna(x) else f'{x:.{digits}f}'

def scaled(value, divisor):
    return None if value is None else value / divisor

def precise(value):
    return 'N/A' if value is None or pd.isna(value) else f'{value:.6g}'

def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---']*len(headers)) + ' |'] +
                     ['| ' + ' | '.join(map(str, r)) + ' |' for r in rows])

def means(frame, model, group='ALL'):
    a = frame[frame.model.eq(model) & frame.group.eq(group) & frame.cap.eq(10) & frame.h.gt(0)]
    return {k: (float(a[k].mean()), int(a[k].count())) for k in ['raw_mse', 'native_mse', 'calibrated_mse', 'calibrated_mae', 'ndcg1', 'ndcg2', 'ndcg4', 'ndcg6', 'low2', 'good2', 'stars2']}

def report():
    lock()
    parents = ['selection-seal.json', 'final-calibration-seal.json', 'evaluation-seal.json', 'catalog-seal.json']
    records = {name: verify(name) for name in parents}
    snapshots = {name: pin(OUT / name) for name in parents}
    require(records['evaluation-seal.json']['selection_seal'] == snapshots['selection-seal.json'] and
            records['evaluation-seal.json']['calibration_seal'] == snapshots['final-calibration-seal.json'] and
            records['final-calibration-seal.json']['selection_seal'] == snapshots['selection-seal.json'] and
            records['catalog-seal.json']['selection'] == snapshots['selection-seal.json'], 'consistent report parents')
    for p in [DOC / 'RESULT.md', DOC / 'comparison.png', OUT / 'report-manifest.json']:
        require(not p.exists(), 'preserve report output: ' + str(p))
    selection = read(OUT / 'selection.json'); decision = read(OUT / 'decision.json')
    reproduction = read(OUT / 'fm150-reproduction-check.json')
    reproduction_pin = pin(OUT / 'fm150-reproduction-check.json')
    require(reproduction['new_fit_seal'] == pin(OUT / 'FM150_s339-seal.json') and
            reproduction['old_predictions'] == pin(FOUND / 'predictions.npz'), 'reproduction source identity')
    users = pd.read_parquet(OUT / 'user-metrics.parquet')
    seed_users = pd.read_parquet(OUT / 'seed-mean-user-metrics.parquet')
    combined = pd.concat([seed_users, users[users.kind.eq('DESCRIPTIVE_REFERENCE')]], ignore_index=True)
    contrasts = pd.read_csv(OUT / 'primary-contrasts.csv')
    common = decision['common_seeds']
    selected_names = ['SEED_MEAN_FM', 'SEED_MEAN_GBT'] if decision['complete_paired_seeds'] else sorted(users.loc[users.kind.eq('CANDIDATE') & users.seed.isin(common), 'model'].unique())
    if not decision['complete_paired_seeds']:
        combined = users
    names = selected_names + ['REFERENCE_FM_RH', 'REFERENCE_GBT_B']
    statistics = {n: means(combined, n) for n in names}
    catalog = pd.read_csv(OUT / 'catalog-summary.csv')
    costs = pd.read_csv(OUT / 'catalog-timing-summary.csv')
    contribution = read(BASE / 'final344-availability-audit/training-contribution.json')
    fitrows = []
    fit_ids = {recipe + '_s339' for recipe in RECIPES} | set(records['evaluation-seal.json']['evaluation_inputs']['fit_seals'])
    for fit_id in sorted(fit_ids):
        verify(fit_id + '-seal.json'); snapshots[fit_id + '-seal.json'] = pin(OUT / (fit_id + '-seal.json'))
        p = OUT / fit_id / 'outcome.json'
        o = read(p); mp = p.parent / 'model/metrics.json'; m = read(mp) if mp.exists() else {}
        excess = None if m.get('peak_bytes') is None else max(0, m['peak_bytes'] - 12*1024**3)
        fitrows.append([o['fit_id'], o['status'], o['resource_status'], number(scaled(m.get('fit_seconds'), 60), 1),
                        number(scaled(m.get('peak_bytes'), 1024**3), 4), 'N/A' if excess is None else f'{excess:,}',
                        m.get('actual_iterations') or '관측 불가'])
    metric_rows = [[name] + [number(statistics[name][m][0]) for m in ['raw_mse', 'native_mse', 'calibrated_mse', 'calibrated_mae', 'ndcg2', 'stars2', 'low2']] +
                   ['/'.join(str(statistics[name][m][1]) for m in ['calibrated_mse', 'ndcg2', 'stars2'])] for name in names]
    coldrows = []
    for group in ['W_DIRECT', 'C', 'NATURAL_ZERO']:
        groupnames = selected_names + (['REFERENCE_ALS'] if group == 'W_DIRECT' else [])
        for name in groupnames:
            s = means(combined, name, group)
            coldrows.append([group, name, number(s['calibrated_mse'][0]), s['calibrated_mse'][1], number(s['ndcg2'][0]), s['ndcg2'][1]])
    choice_rows = []
    for name, c in selection['candidates'].items():
        choice_rows.append([name, c['status'], number(c['mse']), number(c['ndcg2']), '선택' if c['recipe'] == selection['selected'][c['family']] else ''])
    cirows = [[r.group, r.metric, int(r.users), precise(r.delta), f"[{precise(r.ci_low)}, {precise(r.ci_high)}]", str(r.seed_direction_consistent)] for r in contrasts.itertuples()]
    top = catalog[catalog.seed.isin(common) & catalog.h_group.eq('H_POSITIVE') & catalog.view.eq('cumulative') & catalog.end.eq(2)]
    catrows = [[r.model, int(r.returned), int(r.unknown), int(r.one_vote_ten), int(r.unique_movies), number(r.hhi), int(r.support0)] for r in top.itertuples()]
    costrows = [[r.model, r.metric, number(r.p50, 3), number(r.p95, 3), number(r.native_bytes/1024**2, 2)] for r in costs[costs.seed.isin(common) & costs.metric.isin(['prediction_seconds', 'component_total_seconds'])].itertuples()]
    page = pd.read_parquet(OUT / 'seed-mean-page-metrics.parquet') if decision['complete_paired_seeds'] else pd.read_parquet(OUT / 'page-metrics.parquet')
    pagerows = []
    for name in selected_names:
        for end in [2, 4, 6]:
            a = page[page.model.eq(name) & page.cap.eq(10) & page.h.gt(0) & page.group.eq('ALL') & page.j.ge(6) & page.end.eq(end)]
            pagerows.append([name, f'{end-1}–{end}', len(a), number(a.stars.mean()), number(a.good.mean()), number(a.low.mean()), number(a.both_low.mean())])
    titles = {'NO_CLEAR_WINNER_OR_TRADEOFF': '모든 기준에서 앞서는 단일 모델은 확인되지 않았다.',
              'NO_WINNER_INCOMPLETE_OR_INSUFFICIENT': '완료 조건 또는 표본 크기가 부족해 단일 승자를 판정할 수 없다.',
              'GBT_CONSISTENT_OBSERVED_IMPROVEMENT': 'GBT가 이번 개발 표본의 사전 네 비교 기준에서 일관된 개선을 보였다.',
              'FM_CONSISTENT_OBSERVED_IMPROVEMENT': 'FM이 이번 개발 표본의 사전 네 비교 기준에서 일관된 개선을 보였다.'}
    conclusion = titles[decision['decision']]
    seed_description = ('선택된 설정의339/344/345 결과를 비교했다. 각 사용자 지표를 seed별 계산한 뒤 평균하며 점수를 섞은 앙상블은 아니다.'
                        if decision['complete_paired_seeds'] else
                        f'3개 seed의 완료 조건을 충족하지 못했다. 본 비교에는 완료된 공통 seed {common}의 개별 기술통계만 제시하며 3seed 평균·공식 CI는 내지 않는다.')
    body = f'''# 최종 FM·GBT 비교 결과

**{conclusion}**

이번 실험은 같은 RH 입력 4,997,069행·39,859명·230개 특징을 사용했다. 보정90명과 설정 선택·개발 비교180명을 분리했다.
설정 선택은 seed339에서 했다. {seed_description}
완료 여부: **{decision['complete_paired_seeds']}**, 선택된6개 학습의 엄격한 자원 한도 전부 PASS: **{decision['strict_resources_all_pass']}**.

## 설정 선택

{table(['설정', '상태', '보정 MSE', 'NDCG@2', '선택'], choice_rows)}

큰 설정이 보정 MSE를 높이지 않고 NDCG@2를 낮추지 않으며 하나라도 엄격히 개선할 때만 선택했다.
충돌·동률이면 작은 설정을 유지한다. 이는 자원 사용을 줄이려는 사전 규칙이며 두 모델의 동등함을 입증한 것은 아니다.

## 같은 개발 사용자에서의 비교

cap10, 실제 입력이 있는 사용자 기준이다. MSE/MAE는 작을수록, NDCG와 선택 별점은 클수록 좋다.
raw는 범위를 제한하지 않은 예측, native는 raw를[0.5,5]로 clip한 예측이다. 보정은 별도90명의 사용자 균등 affine(a+b*x,b≥0)를 적용한 뒤 clip한다.
순위는 항상 raw 점수와 movie_id 오름차순 동점을 사용한다. 실제 입력·정답 별점은0.5단위 그대로이다.
NDCG는 사용자가 평가한 영화들 안에서의 정렬 품질이다. 정확도나 서비스 만족 확률로 읽지 않는다.
MSE·NDCG@2·선택별점은 유효 사용자 수를 각각 표시한다. IDCG=0이면 NDCG는 없지만 선택별점은 관측할 수 있다.
low2는 관측 별점이2점 이하인 비율이며 선택별점과 같은 분모이다.

{table(['모델', 'raw MSE', 'native MSE', '보정 MSE', '보정 MAE', 'NDCG@2', '선택 별점', 'low2', '유효 MSE/NDCG/별점'], metric_rows)}

REFERENCE 모델은 기존 단일 학습 참고값이다. 새 공통 RH·3seed 비교와 같은 재학습 조건이 아니다.
같은seed339의 새 FM150은 기존 FM_RH의93,230개 예측 중{reproduction['exactly_equal_rows']:,}개를 정확히 재현했다.
최대 예측 차이는{precise(reproduction['max_absolute_prediction_difference'])}이다. 이는 재현성 확인이며 새 품질 개선이라는 뜻은 아니다.

## 학습 지원과 미지원 영화

{table(['영화 집합', '모델', '보정 MSE', 'MSE 사용자', 'NDCG@2', 'Top2 사용자'], coldrows)}

W_DIRECT는 실제 ALS 점수를 계산할 수 있는 같은 영화 집합이다. C는 전역 학습 평점·이력에서 제외한 영화,
NATURAL_ZERO는 원래 학습 평점이 없는 영화이다. ALS가 계산하지 못하는 영화에 임의 점수를 채우지 않았다.
전체 카탈로그의 새 영화 점수 계산 가능성과 실제 추천 품질 검증을 구분한다.

## 사전 네 비교와 불확실성

차이는 GBT−FM이다. MSE는 음수, NDCG@2는 양수 방향이 GBT에 유리하다.
완료·표본 조건을 만족하면98.75% 사용자 paired bootstrap CI를 각각 사용한다(20,000회, seed344, 최소30명).

{table(['집합', '지표', '사용자', '차이', '98.75% CI', 'seed 방향 일치'], cirows)}

고정된 학습·보정기와 재사용한 개발 사용자를 조건으로 한 구간이다. 학습 seed 전체 불확실성이나 새 평가 사용자에 대한 구간이 아니다.
유의하지 않음을 동등함으로 해석하지 않는다. 네 CI가 모두0을 제외하고 ALL과 C의 두 지표가 같은 모델에 유리할 때만 일관된 개선을 주장한다.

## 2편씩 이어지는 관측 추천

세 페이지 모두 관측 영화가 있는 J≥6 동일 사용자 집합이다. 전체 카탈로그 만족도와는 구분한다.

{table(['모델', '페이지', '사용자', '선택 별점', '4점 이상', '2점 이하', '두 편 모두2점 이하'], pagerows)}

## 전체 후보 노출

같은85,517편에서 고정 snapshot의 개봉 가능 영화와 기존 감상 제외를 적용했다. 임의100/500개 후보를 만들지 않았다.
cap10의 입력 있는 사용자에게 첫2편을 표시한 결과이며 seed별로 제시한다. UNKNOWN은 기록된 평가가 없다는 뜻이며 싫어한다는 뜻이 아니다.

{table(['모델·seed', '표시', 'UNKNOWN', '투표1개·10점', '서로 다른 영화', 'HHI', '학습지원0'], catrows)}

전체 점수는 사용자별 catalog-cache에 보존했다. 누적Top4/6과 다음2편의 노출은 catalog-summary.csv에 있다.

## 실행 비용

{table(['학습', '수치 결과', '자원', 'fit 분', 'peak GiB', '한도 초과 byte', '관측 완료 반복/트리'], fitrows)}

FM maxIter는 상한이며 실제 완료 반복 수가 기록되지 않으면 관측 불가로 둔다. GBT는 native 트리 수이다.
자원 EXCEPTION/UNKNOWN을 수치 열등으로 취급하지 않는다. 실패·시간 초과는 보존하며 재시도하지 않았다.

{table(['모델', '시간 항목', 'p50초', 'p95초', 'native MiB'], costrows)}

component_total은 공유 특징 생성+개별 예측+정렬 시간의 합이다. 모든 선택 모델을 함께 적재한 로컬 runner에서 측정했고,
초기화·파일 I/O·API 요청 비용은 제외한다. 실제 단독 운영 지연시간이나 다중 노드 확장성 수치는 아니다.

## 해석 범위와 남는 판단

사용자·영화 균등 오차, cap0/1/5/10/30, 실제 입력 수·활동량·메타데이터 누락·개봉 시기·평가 수별 결과,
보정 구간과 오차 꼬리, 실제 입력 대 빈 입력의 비교도 별도 CSV/Parquet에 남겼다.
학습은 행 가중치1이다. 상위1%인399명이 학습행의12.84%를 차지한다. 평가 때 사용자를 같은 비중으로 보는 것만으로 학습 기여 편향이 없어지지는 않는다.

**이번180명은 이미 사용한 개발 사용자다.** 미사용 신규 표본을 입증하지 못해 DEVELOPMENT_ONLY로 완료했다.
MovieLens의 과거 해외 평가와 최신 TMDB·2026년 한국 서비스의 차이는 남는다. 이 결과만으로 서비스 만족도나 전환K를 확정하지 않는다.
K-means·분류 기반 맞춤/발견 정책, ALS 혼합, 새 특징 추가는 이번 비교에 포함하지 않았다.

## 재현 자료

- [승인 설계](../../plans/final-fm-gbt/README.md), [실행 명세](IMPLEMENTATION.md)
- 입력·fit·선택·보정·평가·전체 후보 봉인: `outputs/recommendation-evidence/final344/`
- 세부 평점/순위: `summary.csv`, `seed-mean-summary.csv`, `movie-metrics.csv`, `page-summary.csv`
- 그룹·보정·개인화: `support-metadata-metrics.csv`, `calibration-bins.csv`, `personalization-summary.csv`
- 독립 검토와 가용성 감사는 각 final344 감사 폴더에 보존한다. 이 문서의 수치·그림은 완료 후 독립 검토 대상이다.
'''
    lock()
    for name, expected in snapshots.items():
        verify(name); require(pin(OUT / name) == expected, 'unchanged report source: ' + name)
    plt.rcParams['font.family'] = ['Malgun Gothic', 'DejaVu Sans']
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.7))
    for ax, metric, title in zip(axes, ['calibrated_mse', 'ndcg2'], ['보정 MSE · 낮을수록 좋음', '관측 NDCG@2 · 높을수록 좋음']):
        values = [statistics[n][metric][0] for n in names]
        ax.bar(np.arange(len(names)), values, color=['#377eb8', '#e68632']+['#aaaaaa']*max(0,len(names)-2))
        ax.set_xticks(np.arange(len(names)), [n.replace('SEED_MEAN_', '3seed ').replace('REFERENCE_', '기존 ') for n in names], rotation=20, ha='right')
        for i, value in enumerate(values):
            if np.isfinite(value): ax.text(i, value, f'{value:.4f}', ha='center', va='bottom', fontsize=10)
        ax.set_title(title); ax.set_ylim(0, max([v for v in values if np.isfinite(v)] or [1])*1.15)
        ax.spines[['top', 'right']].set_visible(False)
    fig.suptitle('동일 개발 사용자 cap10 · 입력 있는 사용자\n현재 RH·seed 비교와 기존 참고값을 구분', fontsize=13)
    fig.tight_layout(); fig.savefig(DOC / 'comparison.png', dpi=180); plt.close(fig)
    body = body.replace('## 같은 개발 사용자에서의 비교', '![FM·GBT 비교](comparison.png)\n\n## 같은 개발 사용자에서의 비교')
    (DOC / 'RESULT.md').write_text(body, encoding='utf-8')
    lock()
    for name, expected in snapshots.items():
        verify(name); require(pin(OUT / name) == expected, 'unchanged report source after rendering: ' + name)
    require(pin(OUT / 'fm150-reproduction-check.json') == reproduction_pin, 'unchanged reproducibility diagnostic')
    write_json(OUT / 'report-manifest.json', {'script': pin(Path(__file__)), 'result': pin(DOC / 'RESULT.md'),
                'figure': pin(DOC / 'comparison.png'), 'selection': pin(OUT / 'selection-seal.json'),
                'evaluation': snapshots['evaluation-seal.json'], 'catalog': snapshots['catalog-seal.json'], 'sources': snapshots,
                'input_lock': pin(OUT / 'input-lock.json'), 'reproduction_check': reproduction_pin})
    print('REPORT_WRITTEN', flush=True)

if __name__ == '__main__': report()
