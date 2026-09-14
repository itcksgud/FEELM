"""Compact figures and factual result tables; no fitting or new selections."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from text339_common import *
from text339_prepare import load_embeddings
from text339_run import verify_model

def main():
    reviewed()
    for name in ['prepared-seal.json','fit-seal.json','catalog-seal.json','evaluation-seal.json']:verify(name)
    load_embeddings()
    for variant in VARIANTS:verify_model(variant)
    selection=json.loads((OUT/'selection.json').read_text())
    require(not (DOC/'RESULT.md').exists(),'preserve published local result draft')
    metrics=pd.read_csv(OUT/'metrics.csv');catalog=pd.read_csv(OUT/'catalog-diagnostics.csv');pre=json.loads((OUT/'preflight.json').read_text())
    sources=json.loads((OUT/'text-source-report.json').read_text());embedding=json.loads((OUT/'embedding-summary.json').read_text())
    timing=pd.read_csv(OUT/'catalog-timing.csv')
    def value(v,metric):
        row=metrics[(metrics.cap==10)&(metrics.variant==v)&(metrics.group=='ALL')&(metrics.cohort=='eligible_n')&(metrics.metric==metric)].iloc[0]
        return float(row['mean']),int(row.users_valid)
    font=Path('C:/Windows/Fonts/malgun.ttf')
    if font.exists():font_manager.fontManager.addfont(str(font));plt.rcParams['font.family']='Malgun Gothic'
    plt.rcParams['axes.unicode_minus']=False
    colors=['#7a8694','#4b85ca','#41a496','#bb8355']
    fig,axes=plt.subplots(1,3,figsize=(12,4.4),layout='constrained')
    for ax,metric,title,suffix in zip(axes,['ndcg2','stars2','low2'],['관측후보 NDCG@2 ↑','상위2편 평균 실제 별점 ↑','상위2편 낮은 별점 비율 ↓'],['','점','%']):
        values=[value(v,metric)[0]*(100 if suffix=='%' else 1) for v in VARIANTS]
        ax.bar(VARIANTS,values,color=colors,width=.65);ax.set_title(title,fontweight='bold');ax.spines[['top','right']].set_visible(False)
        ax.set_ylim(0,(5 if metric=='stars2' else 1 if metric=='ndcg2' else max(values)*1.3+1))
        for i,y in enumerate(values):ax.text(i,y,f'{y:.3f}{suffix}' if metric=='ndcg2' else f'{y:.2f}{suffix}',ha='center',va='bottom',fontsize=10)
        ax.set_xlabel(f'H≤10 · 유효 사용자 {value("T0",metric)[1]}명')
    fig.suptitle('줄거리·Wikipedia 추가 효과 — 개발 자료의 조건부 비교',fontsize=16,fontweight='bold')
    fig.savefig(DOC/'comparison.png',dpi=170);plt.close(fig)
    rows=[]
    for v in VARIANTS:
        rows.append('| '+v+' | '+' | '.join(f'{value(v,m)[0]*100:.2f}%' if m=='low2' else f'{value(v,m)[0]:.4f}' for m in ['ndcg2','stars2','low2','mse','pa'])+' |')
    longer=[]
    for n in [4,6,10]:
        for v in VARIANTS:
            ndcg,valid=value(v,'ndcg'+str(n))
            longer.append(f'| {n} | {v} | {valid} | {ndcg:.4f} | {value(v,"stars"+str(n))[0]:.4f} | {value(v,"low"+str(n))[0]*100:.2f}% |')
    contrasts=[]
    for r in selection['comparisons']:
        a=r['ndcg2'];lo,hi=a['interval'];ci=f'[{lo:.4f}, {hi:.4f}]' if lo is not None else '표본 부족'
        contrasts.append(f'| {r["comparison"]} | {a["users"]} | {a["mean_delta"]:.4f} | {ci} | {"다음 단계 채택" if r["advance"] else "채택 중단/진단"} |')
    totals=[]
    for v in VARIANTS:
        a=catalog[(catalog.variant==v)&(catalog.n==2)].iloc[0]
        totals.append(f'| {v} | {a.known_fraction*100:.2f}% | {a.like_lower_bound*100:.2f}–{a.like_upper_bound*100:.2f}% | {int(a.unique_movies)} |')
    selected=selection['selected'];why=('이번 기준에서는 텍스트 없는 T0을 유지한다. 더 복잡한 표현을 채택할 주 근거가 충족되지 않았다.' if selected=='T0' else f'이번 개발 비교에서는 {selected}를 후속 정보 결합 비교의 표현으로 선택한다.')
    cumulative=[]
    for m in ['ndcg2','stars2','low2']:
        cumulative.append(f'{m}: {value(selected,m)[0]-value("T0",m)[0]:+.4f}')
    model_cost=[]
    for v in VARIANTS:
        d=json.loads((OUT/'models'/v/'metrics.json').read_text())
        peak=f'{d["peak_bytes"]/1024**3:.2f}' if d.get('peak_bytes') is not None else '미측정'
        model_cost.append(f'| {v} | {d["dimensions"]} | {d["fit_seconds"]/60:.1f} | {peak} |')
    text=f'''# 줄거리·Wikipedia 추가 효과

상태: DRAFT — 실제 실행 결과. 독립 결과 검산 완료 여부는 result-review.json을 따른다.

**{why}** FM을 최종 추천 모델로 확정한 결과는 아니다.

![4조건 비교](comparison.png)

## 같은 조건에서 무엇이 달라졌나

T0=구조화 정보·개인 반응, T1=T0+TMDB 줄거리, T2=T1+Wiki 기본 정제,
T3=T1+검토한 본문 복원. H는 입력 상한이며 부족한 사용자를 제외하지 않았다.

| 표현 | NDCG@2 | 평균 별점@2 | 낮은 별점@2 | 사용자 평균 MSE | 별점쌍 순서 정확도 |
|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

별점은 실제0.5단위다. 좋음≥4,낮음≤2. 위 표는 **그 사용자가 미래 기간에 실제 평가한
영화 안에서 고르는 조건부 품질**이다. 미평가 영화를 싫어요로 처리하지 않았다.

| 인접 비교 | 사용자 | NDCG@2 차이 | 보정98.333% 구간 | 사전 규칙의 처리 |
|---|---:|---:|---|---|
{chr(10).join(contrasts)}

사용자 짝 bootstrap20000회,3대조 Bonferroni의 근사 동시 구간이다. 첫 실패에서 선정이 멈춘다.
뒤 단계의 비채택을 효과 없음이나 동급의 증명으로 해석하지 않는다.
선택표현−T0의 누적 점추정: {', '.join(cumulative)}.
인접 손실 veto는 누적 손실 상한이나 서비스 안전 보증이 아니다.

| Top-N | 표현 | NDCG 유효 사용자 | NDCG | 평균 별점 | 낮은 별점 비율 |
|---|---|---:|---:|---:|---:|
{chr(10).join(longer)}

위 보조표는 각 N에 충분한 관측후보가 있는 사용자 기준이다. 같은 사용자를 유지한
Top2/4/6 비교와 전체 보조지표는 [전체 지표](../../../../outputs/recommendation-evidence/text339/metrics.csv)에 있다.
NDCG의 IDCG0 제외와 평균별점의 분모는 다를 수 있으며 분모표에 함께 기록했다.

## 무엇을 채점할 수 있었나

- 현재 실행의 학습 {pre['train_users']:,}명·{pre['train_rows']:,}평점. C영화 {pre['blocked_movies']:,}편은 학습 목표·입력·집계 모두 제외.
- 개발 사용자 후보 {pre['candidate_users']}명 → 개봉일 조건 후 {pre['eligible_users']}명·미래 관측 {pre['future_rows_after_release_filter']:,}개.
- 원문 검토 후 사용 가능한 overview {sources['overview_nonempty']:,}편, T2 {sources['T2_nonempty']:,}편, T3 {sources['T3_nonempty']:,}편.
  T3 본문 복원 {sources['restored_body']}편, 양쪽 비결측 본문 변경 {sources['changed_nonempty']}편.
- [분모표](../../../../outputs/recommendation-evidence/text339/cohort-denominators.csv)는 N별 후보 부족·좋음0·동점,
  C/W/본래지원0·위키·언어·개봉연대별 조건부 분모를 보존한다. 실제 파일은 outputs에 있으며 Git 제외다.

현재 평가271명 전원이 과거 개발에 사용됐고261명은 과거 학습에 들어간 적 있다.
이번 실행 내 역할 분리는 지켰지만 **새 최종 시험은 아니다**. 과거 MovieLens 평점과 최신
TMDB/Wiki를 결합한 결과이며 현재 한국 사용자 품질은 미검증이다. h=0도 H10에 들어가므로
전체 개선을 바로 개인화 개선으로 부르지 않는다.

복원7편은 이 평가 사용자의 입력과 미래 관측에 없어서 해당 영화의 추천 품질을 직접 검증할 수 없다.
본문 변경92편 중 미래 관측은23편·53평점·31사용자, H30 입력은11편·17사용자에 해당한다.
T3의 전체 차이를 이들 사례의 개별 개선이나 결말 정보의 효과로 단정하지 않는다.

## 전체 연구 카탈로그에서는

미래 관측 여부로 후보를 줄이지 않고 H10에서 알려진 과거 감상 전체를 제외해 별도로 생성했다.
MovieLens와 연결한 연구85517편의 현재 메타데이터 진단이며 실제 서비스 신규영화·KR OTT 공급 전체는 아니다.

| 표현 | Top2 채점 가능 비율 | 좋음 비율의 가능 범위 | Top2 노출 영화 수 |
|---|---:|---:|---:|
{chr(10).join(totals)}

가능 범위는 UNKNOWN의 별점을 모르기 때문에 생기는 느슨한 범위이며 신뢰구간이 아니다.
평가된 항목만의 평균으로 전체 추천 만족도를 확정하지 않는다. 부족 슬롯과 UNKNOWN은 별도로 셌다.

## 비용과 다음 결정

| 표현 | 특징 차원 | 학습 분 | 컨테이너 최고GiB |
|---|---:|---:|---:|
{chr(10).join(model_cost)}

임베딩 {embedding['unique_documents']:,}개 고유 본문·{embedding['chunks']:,}청크, {embedding['seconds']/60:.1f}분.
특징 생성+4모델을 합친 사용자별 전체 카탈로그 점수화 p50={timing.joint_4model_seconds.quantile(.5):.2f}초,
p95={timing.joint_4model_seconds.quantile(.95):.2f}초. 운영 API의 응답시간이나 서버 분산 성능으로 부르지 않는다.

이 표현을 고정한 뒤339의 결과 검산을 통과하면340에서 정보 결합·ALS 보조 기여를 비교한다.
전체 결과는 outputs/recommendation-evidence/text339의 봉인된 CSV/Parquet/모델과 함께 재현한다.
원문·모델·사용자별 결과는 Git 제외이며 아직 팀 운영 계약으로 승격하지 않았다.
'''
    (DOC/'RESULT.md').write_text(text,encoding='utf-8')
    write_json(OUT/'report-seal.json',{'source':pin(OUT/'evaluation-seal.json'),'code':pin(__file__),
                                    'files':{p.relative_to(ROOT).as_posix():pin(p) for p in [DOC/'RESULT.md',DOC/'comparison.png']}})

if __name__=='__main__':main()
