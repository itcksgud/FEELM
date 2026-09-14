"""Readable three-stage summary from independently audited, sealed results only."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from rec046_common import require,pin,write_json
import foundation340_common as f
import combination340_common as c
import policy341_run as p

DOC=f.ROOT/'docs/recommendation/experiments/final341'
OUT=f.ROOT/'outputs/recommendation-evidence/final341'

def read(path): return json.loads(path.read_text())
def number(value,digits=6): return '미판정' if value is None or not np.isfinite(value) else f'{value:.{digits}g}'
def mean_user(frame,variant,metric):
    a=frame[frame.cap.eq(10)&frame.group.eq('ALL')&frame.h.gt(0)&frame.variant.eq(variant)][metric].dropna()
    return float(a.mean()),len(a)

def estimate(value,users,percent=False):
    if users==0 or value is None or not np.isfinite(value): return '없음 (0명)'
    return (f'{value:.2%}' if percent else f'{value:.4f}')+f' ({users}명)'

def policy_value(table,model,policy,metric,percent=False):
    rows=table[table.model.eq(model)&table.policy.eq(policy)&table.group.eq('H_POSITIVE')&table.metric.eq(metric)]
    require(len(rows)<=1,'unique policy summary cell')
    if rows.empty: return '없음 (0명)'
    row=rows.iloc[0]
    return estimate(row['mean'],int(row.users_valid),percent)

def interval_text(record,scale=1):
    lo,hi=record['interval']
    return f"{number(record['mean_delta']*scale if record['mean_delta'] is not None else None)} [{number(lo*scale if lo is not None else None)}, {number(hi*scale if hi is not None else None)}]"

def main():
    report_review=read(DOC/'code-review.json')
    require(report_review['status']=='PASS' and report_review['code']==pin(__file__),'independent report code review')
    f.reviewed(); f.verify('evaluation-seal.json'); c.reviewed(); c.verify('evaluation-seal.json'); p.verify()
    for module in [f,c,p]:
        review=read(module.DOC/'result-review.json')
        require(review['status']=='PASS' and review['evaluation_seal']==pin(module.OUT/'evaluation-seal.json'),'audited stage result')
    from policy341_evaluate import evaluation_fingerprint
    policy_evaluation=read(p.OUT/'evaluation-seal.json'); policy_code_review=read(p.DOC/'evaluation-code-review.json')
    require(policy_evaluation['evaluation_code']==evaluation_fingerprint() and policy_code_review['status']=='PASS' and policy_code_review['fingerprint']==evaluation_fingerprint(),'current approved policy evaluation')
    require(policy_evaluation['prediction_seal']==pin(p.OUT/'prediction-seal.json'),'actual policy prediction parent')
    for name,expected in policy_evaluation['files'].items(): require(pin(p.OUT/name)==expected,'actual audited policy result '+name)
    DOC.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
    require(not any((DOC/name).exists() for name in ['README.md','DETAILS.md','comparison.png','runtime.csv']) and not (OUT/'report-seal.json').exists(),'preserve final report')
    foundation=read(f.OUT/'selection.json'); combo=read(c.OUT/'selection.json'); policy=read(p.OUT/'decision.json')
    u1=pd.read_parquet(f.OUT/'user-metrics.parquet'); u2=pd.read_parquet(c.OUT/'user-metrics.parquet')
    policy_metrics=pd.read_csv(p.OUT/'metrics.csv'); catalog=pd.read_csv(p.OUT/'catalog-diagnostics.csv')
    foundation_catalog=pd.read_csv(f.OUT/'catalog-diagnostics.csv')
    selected=combo['selected']; selected_policy=policy['selected_policy']; base_ndcg,bn=mean_user(u1,'B','ndcg2'); final_ndcg,fn=mean_user(u2,selected,'ndcg2')
    names={'B':'기존 대중평점·기존 학습이력','R':'대중평점 근거량 보정','H':'목표시각 이전 학습이력','RH':'두 변경 결합'}
    model_names={'S':'1단계에서 고정한 FM','NO_RESPONSE':'대중 반응 6열을 제거한 FM','GBT':'분산 GBT',
                 'BLEND_0.25':'FM + ALS 25%','BLEND_0.5':'FM + ALS 50%','BLEND_0.75':'FM + ALS 75%','BLEND_1.0':'ALS 지원 여부에 따라 ALS/FM 사용'}
    policy_names={'P0':'점수순 추천 유지; 발견 교체는 보류','P2_010':'연결 장르·새로움 .10·예측손실 제한의 발견 교체','P2_020':'연결 장르·새로움 .20·예측손실 제한의 발견 교체'}
    runtime=[]
    for v in f.VARIANTS:
        m=read(f.OUT/v/'model/metrics.json'); runtime.append({'stage':1,'model':v,'fit_minutes':m['fit_seconds']/60 if m['fit_seconds'] is not None else None,'peak_bytes':m['peak_bytes'],'strict_memory_pass':m['strict_memory_pass']})
    for v in c.METHODS:
        m=read(c.OUT/v/'metrics.json'); runtime.append({'stage':2,'model':v,'fit_minutes':m['fit_seconds']/60,'peak_bytes':m['peak_bytes'],'strict_memory_pass':m['strict_memory_pass']})
    pd.DataFrame(runtime).to_csv(DOC/'runtime.csv',index=False)
    memory_exceptions=[r['model'] for r in runtime if r['strict_memory_pass'] is False]
    memory_unknown=[r['model'] for r in runtime if r['strict_memory_pass'] is None]
    known_time=[r['fit_minutes'] for r in runtime if r['fit_minutes'] is not None]
    plt.rcParams['font.family']='Malgun Gothic'; plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(2,2,figsize=(12,9)); fig.patch.set_facecolor('white')
    def contrast_plot(ax,labels,records,title,xlabel,threshold=0):
        y=np.arange(len(labels)); values=np.array([r['mean_delta'] for r in records],float)
        ax.scatter(values,y,color='#2563eb',zorder=3)
        for i,r in enumerate(records):
            lo,hi=r['interval']
            if lo is not None: ax.plot([lo,hi],[i,i],color='#2563eb',linewidth=2)
        ax.axvline(threshold,color='#64748b',linestyle='--',linewidth=1)
        ax.set_yticks(y,labels); ax.invert_yaxis(); ax.set_title(title,loc='left',fontweight='bold'); ax.set_xlabel(xlabel)
        ax.spines[['top','right']].set_visible(False); ax.grid(axis='x',alpha=.16)
    first=foundation['comparisons']
    contrast_plot(axes[0,0],[r['after']+'-'+r['before'] for r in first],[r['cohorts']['H_POSITIVE']['ndcg2'] for r in first],
                  '1. 학습 입력과 대중평점 (99% 구간)','입력 있는 사용자 NDCG@2 차이')
    second=combo['comparisons']
    contrast_plot(axes[0,1],[r['primary_group']+' · '+r['after'] for r in second],[r['primary'] for r in second],
                  '2. 정보 결합 / ALS 보조 (99.17% 구간)','동일 C 또는 W_DIRECT 후보 안의 NDCG@2 차이')
    third=policy['policies']
    contrast_plot(axes[1,0],[r['policy'] for r in third],[r['novelty_vs_own_P0'] for r in third],
                  '3. 첫 3편의 새로움 (99.29% 구간)','같은 모델의 점수순 추천 대비 장르 novelty 차이')
    contrast_plot(axes[1,1],[r['policy'] for r in third],[r['stars_vs_B_P0'] for r in third],
                  '3. 첫 3편의 실제 별점 (99.29% 구간)','기준 B의 점수순 추천 대비 별점 차이',-.1)
    fig.suptitle('추천 구성 비교: 관측된 MovieLens 개발 자료의 결과',fontsize=16,fontweight='bold')
    fig.tight_layout(rect=[0,0,1,.95]); fig.savefig(DOC/'comparison.png',dpi=160); plt.close(fig)
    selected_catalog=catalog[catalog.model.eq('SELECTED')&catalog.policy.eq(selected_policy)&catalog.group.eq('ALL')&catalog.n.eq(3)].iloc[0]
    stage_rows=[]
    for v in f.VARIANTS:
        nd,n=mean_user(u1,v,'ndcg2'); stars,sn=mean_user(u1,v,'stars2'); low,ln=mean_user(u1,v,'low2')
        exposed=foundation_catalog[foundation_catalog.variant.eq(v)&foundation_catalog.group.eq('H_POSITIVE')&foundation_catalog.n.eq(2)]
        require(len(exposed)==1,'unique foundation whole-catalog exposure cell')
        exposed=exposed.iloc[0]
        stage_rows.append(f'| {v}: {names[v]} | {nd:.4f} ({n}명) | {stars:.3f} ({sn}명) | {low:.2%} ({ln}명) | {int(exposed.one_vote_ten_slots)}/{int(exposed.slots)} | {int(exposed.unknown)}/{int(exposed.slots)} |')
    details=[]
    for r in combo['comparisons']:
        primary=r['primary']; lo,hi=primary['interval']
        details.append(f"| {r['after']}−S | {r['primary_group']} | {primary['users']}명 | {number(primary['mean_delta'])} | [{number(lo)}, {number(hi)}] | {'통과' if r['eligible'] else '미통과'} |")
    policies=[]
    for r in third:
        guard=r['h0_point_guard']
        policies.append(f"| {r['policy']} | {interval_text(r['novelty_vs_own_P0'])} | {interval_text(r['stars_vs_B_P0'])} | {interval_text(r['low_vs_B_P0'],100)}%p | {guard['stars_delta']:.3f}점 / {guard['low_delta']*100:.2f}%p ({'통과' if guard['pass'] else '미통과'}) | {'통과' if r['eligible'] else '미통과'} |")
    top_rows=[]
    for n in [2,4,6,10]:
        for label,frame,variant in [('기준 B',u1,'B'),('선택 모델',u2,selected)]:
            cells=[]
            for metric,percent in [('ndcg',False),('stars',False),('low',True)]:
                value,users=mean_user(frame,variant,metric+str(n)); cells.append(estimate(value,users,percent))
            top_rows.append(f'| Top{n} | {label} | '+ ' | '.join(cells)+' |')
    alternatives=[('기준 B·점수순','B','P0'),('선택 모델·점수순','SELECTED','P0')]
    alternatives.extend((f'선택 모델·{rule}'+(' (선정)' if rule==selected_policy else ''),'SELECTED',rule)
                        for rule in ['P1_010','P1_020','P2_010','P2_020'])
    combo_catalog=pd.read_csv(c.OUT/'catalog-diagnostics.csv'); catalog_rows=[]
    for variant in ['S','NO_RESPONSE','GBT','ACTUAL_ALS','BLEND_0.25','BLEND_0.5','BLEND_0.75','BLEND_1.0']:
        cells=combo_catalog[combo_catalog.variant.eq(variant)&combo_catalog.group.eq('H_POSITIVE')&combo_catalog.n.eq(2)]
        require(len(cells)==1,'unique combination catalog diagnostic')
        row=cells.iloc[0]
        catalog_rows.append(f'| {variant} | {int(row.slots)} | {int(row.known)} | {int(row.unknown)} | {int(row.one_vote_ten_slots)} | {int(row.unique_movies)} | {int(row.zero_train_slots)} |')
    supply_rows=[]
    for rule in ['P0','P1_010','P1_020','P2_010','P2_020']:
        cells=catalog[catalog.model.eq('SELECTED')&catalog.policy.eq(rule)&catalog.group.eq('H_POSITIVE')&catalog.n.eq(3)]
        require(len(cells)==1,'unique policy first-set catalog supply')
        row=cells.iloc[0]
        fallback=policy_value(policy_metrics,'SELECTED',rule,'first_fallback',True) if rule!='P0' else '해당 없음'
        full_fallback=f'{int(row.fallback_blocks)}/{int(row.total_blocks)}' if rule!='P0' else '해당 없음'
        supply_rows.append(f'| {rule} | '+policy_value(policy_metrics,'SELECTED',rule,'first_discovery',True)+f' | {fallback} | {int(row.discovery_slots)}/{int(row.total_blocks)} | {full_fallback} | {int(row.known)} / {int(row.unknown)} |')
    set_rows=[]; taste_rows=[]; discovery_rows=[]
    for label,model,rule in alternatives:
        for n in [3,6,9]:
            cells=[policy_value(policy_metrics,model,rule,metric+str(n),metric=='low') for metric in ['ndcg','stars','low','novelty']]
            set_rows.append(f'| {label} | {n}편 | '+' | '.join(cells)+' |')
        for n in [2,4,6]:
            taste_rows.append(f'| {label} | {n}편 | '+policy_value(policy_metrics,model,rule,'taste_stars'+str(n))+' | '+policy_value(policy_metrics,model,rule,'taste_low'+str(n),True)+' |')
        for title,prefix,suffix in [('첫 묶음 발견','first_slot_discovery_','')]+[(f'실제 발견 앞{n}편','discovery_',str(n)) for n in [1,2,3]]:
            cells=[policy_value(policy_metrics,model,rule,prefix+metric+suffix,metric=='low') for metric in ['stars','low','novelty']]
            discovery_rows.append(f'| {label} | {title} | '+' | '.join(cells)+' |')
    detail_text=f'''# 편수별 상세 비교

상태: DRAFT — 본문의 독립 검산한 개발 비교를 펼친 기술통계다. 추가 모델 선택이나 검정에 사용하지 않았다.
모두 H10에서 실제 입력이 있는 사용자다. 괄호는 각 지표의 유효 인원이다.
관측 후보가 N편 이상인 사람만 그 TopN을 채점하므로 편수 간 모집단이 다를 수 있다.
NDCG는 이상적인 순서에 가까울수록 높고, 별점은 높을수록, 낮은별점(≤2점) 비율은 낮을수록 좋다.

| 점수순 추천 | 모델 | NDCG | 평균별점 | 낮은별점 비율 |
|---|---|---:|---:|---:|
{chr(10).join(top_rows)}

다음은 2단계 각 모델이 전체 카탈로그에서 실제로 올린 Top2다. 입력이 있는186명·372슬롯 기준이다.
투표1개·10점 영화의 노출 감소와 노출 영화 종류의 증가는 진단값이며, UNKNOWN 영화의 실제 선호 개선을 입증하지 않는다.
ALS는 직접 점수를 만들 수 있는 후보만 반환한다. 다른 행은 동일한 전체 후보에서 정렬했다.

| 모델 | 반환 슬롯 | 관측별점 있음 | UNKNOWN | 투표1개·10점 | 노출 영화 종류 | 학습평점0 슬롯 |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(catalog_rows)}

발견 대체를 포함한 반환 순서 전체의 품질이다. 발견 조건을 충족하지 못해 맞춤으로 채운 경우도 포함한다.
세트 NDCG의 이상적인 순서는 관측 후보 전체에서 구했다. 장르 novelty는 새로움의 대리 지표다.
선정되지 않은 발견 정책도 모두 펼쳐 놓았다. P1은 연결 장르·새로움 조건, P2는 여기에 예측별점 손실 제한을 더한 조건이다.
010/020은 새로움 증가 기준 .10/.20이다. 이 상세 표로 선정 기준을 다시 바꾸지 않는다.

| 구성 | 반환 편수 | NDCG | 평균별점 | 낮은별점 비율 | 장르 novelty |
|---|---|---:|---:|---:|---:|
{chr(10).join(set_rows)}

발견 후보가 실제로 있었는지도 별도로 확인한다. 아래는 선택 모델의 **첫 묶음만**이다.
관측 후보는 입력 있는158명, 전체 카탈로그는 입력 있는186명이므로 두 공급률을 같은 모집단의 차이로 해석하지 않는다.
전체 후보의 관측별점/UNKNOWN 열은 첫3편 전체558슬롯이다. P0은 발견을 시도하지 않아 fallback이 해당되지 않는다.

| 정책 | 관측 후보: 발견 공급률 | 관측 후보: 맞춤 대체율 | 전체 후보: 발견/묶음 | 전체 후보: 맞춤 대체/묶음 | 전체 후보: 관측별점 / UNKNOWN |
|---|---:|---:|---:|---:|---:|
{chr(10).join(supply_rows)}

맞춤 슬롯은 각 묶음의 첫 두 자리(1·2·4·5·7·8번째)다. 마지막 묶음이 부분 반환이어도 존재하는 맞춤 자리는 포함한다.

| 구성 | 고정 맞춤 슬롯 | 평균별점 | 낮은별점 비율 |
|---|---|---:|---:|
{chr(10).join(taste_rows)}

아래는 실제 발견을 받은 사람만의 조건부 수치다. 분모가 다르므로 위 전체 세트 품질이나 정책 간 인과 비교로 읽지 않는다.
첫 묶음 발견과 세 묶음 중 처음 실제로 받은 발견은 서로 다를 수 있다. 선택 정책이 P0이면 발견은 반환되지 않는다.

| 구성 | 실제 발견 | 평균별점 | 낮은별점 비율 | 장르 novelty |
|---|---|---:|---:|---:|
{chr(10).join(discovery_rows)}

[판단과 한계로 돌아가기](README.md)
'''
    (DOC/'DETAILS.md').write_text(detail_text,encoding='utf-8')
    text=f'''# 추천 시스템 비교 — 3단계 최종 정리

상태: DRAFT — 독립 검산한 로컬 연구 결론이며 서비스 구현 계약은 아니다.

이번 개발 비교의 후속 추천안은 **{model_names[selected]}**, 정책은 **{policy_names[selected_policy]}**다.
앞 단계의 입력 구성은 **{names[foundation['selected']]}**다. 후보 자격이 없으면 기존 구성을 유지하는 사전 규칙을 적용했다.
여기서 유지는 개발 비교 기준의 유지다. 전체 카탈로그의 실제 만족도를 검증하지 못했으므로 서비스 채택 판단은 보류한다.

기초 모델은 TMDB의 장르·키워드·국가/언어·감독·배우·시대/상영시간·대중 지표 등과,
사용자가 입력한 별점에서 만든 반응 및 근거량을 230개 특징으로 사용했다.
줄거리·Wikipedia 텍스트는 앞선 [텍스트 비교](../text339/README.md)와 [정제 비교](../text339-clean/README.md)에서
이번 기준 모델의 개선안으로 선정되지 않아 추가하지 않았다. 텍스트의 가능성 전체를 기각한 결론은 아니다.

| 판단 | 결과 |
|---|---|
| 1. 기준 모델 정리 | {foundation['selected']}: {names[foundation['selected']]} |
| 2. 정보 결합·ALS 보조 | {model_names[selected]} |
| 3. 맞춤·발견 정책 | {policy_names[selected_policy]} |
| 공통 관측후보 Top2 | B {base_ndcg:.4f} → 최종 {final_ndcg:.4f} (NDCG, 입력 있는 {fn}명) |
| 전체270명: 전체 후보 첫3편의 채점 가능성 | {int(selected_catalog.slots)}슬롯 중 관측별점 {int(selected_catalog.known)}, UNKNOWN {int(selected_catalog.unknown)} |

이 중 **개인 입력이 있는186명의 첫3편558슬롯은 모두 UNKNOWN**이다. 위 전체270명 수치의
관측별점104개는 입력이 없는84명에서만 나왔으므로 개인화 추천의 품질 근거로 사용할 수 없다.

**숫자가 높은 순서와 개선을 확인한 것은 다르다.** 아래 구간과 사전 자격 기준으로 판단했고,
전체 후보 UNKNOWN은 낮은별점으로 간주하지 않았다. ALS가 직접 점수를 못 내는 행의 혼합은 콘텐츠와 정확히 같다.

![단계별 차이와 보정구간](comparison.png)

1단계는 같은39,859명·4,997,069개 목표 별점을 사용했다. 학습 이력만 바꾸면 입력0 목표가82.34%에서21.04%로 줄었다.
대중평점 보정과 이력 변경을 분리하려고 기준 모델도 다시 학습하고, 네 조건의 실제 Spark 행 배정과 순서를 맞췄다.

| 조건 | 관측후보 Top2 NDCG | 추천2편 평균별점 | 낮은별점 비율 | 전체후보 Top2: 투표1개·10점 노출 | 전체후보 Top2: UNKNOWN |
|---|---:|---:|---:|---:|---:|
{chr(10).join(stage_rows)}

마지막 두 열은 입력이 있는186명의 전체 카탈로그 추천372슬롯이다.
평가 1개로 평균10점인 영화의 노출 감소만으로 추천 품질이 좋아졌다고 판정하지 않는다.

2단계에서 **C는 학습평점을 일부러 가린 영화**, **W_DIRECT는 실제 ALS로 점수를 만들 수 있는 영화**다.
각 행은 그 집단의 동일 후보에서 S와 비교한 값이며, C와 W의 숫자로 두 집단의 우열을 판단하지 않는다.

| 대조 | 후보 집단 | 유효 인원 | NDCG2 차이 | 명목99.17% 구간 | 후속 후보 자격 |
|---|---|---:|---:|---|---|
{chr(10).join(details)}

3단계는 맞춤2편을 유지하고 발견1편을 교체했다. 적합한 발견이 없으면 맞춤3편을 반환하며,
이 fallback도 첫3편 평가에 포함했다. 새로움은 같은 모델의 P0, 별점 손실은 최초 기준 B의 P0와 비교했다.

최종 모델−B의 Top2 NDCG 차이는 **{interval_text(policy['model_check'])}**다(176명, 명목99.29% 구간).
이 1개와 아래 정책별 3개씩을 묶어 7대조를 보정했다. 각 셀은 차이 [하한, 상한]이다.
새로움 하한>0, 별점 하한≥−0.1점, 낮은별점 증가 상한≤3%p와 입력0 사용자의 점추정 기준을 함께 적용했다.

| 정책 | 첫3편 novelty 차이 | 실제 별점 차이 | 낮은별점 비율 차이 | 입력0 84명: 별점 / 낮음 차이 | 발견 후보 자격 |
|---|---:|---:|---:|---|---|
{chr(10).join(policies)}

정책 주평가는 입력이 있는158명이며 모델 Top2의176명과 다르다. 실제 발견을 받은 경우만의 품질은 별도 기술통계다.
`first_slot_discovery_*`는 첫 묶음의3번째 슬롯, `discovery_*1/2/3`은 세 묶음에서 실제 반환된 발견의 앞N편이다.
**[Top2·4·6·10, 전체3·6·9편, 맞춤·발견별 비교 표](DETAILS.md)**에서 편수별 값과 각 분모를 볼 수 있다.

새 학습은 총7회(FM5·GBT1·ALS1)다. 시간이 기록된{len(known_time)}회의 학습 함수 시간 합계는{sum(known_time):.1f}분이다.
B는 학습·예측 저장 뒤 메모리 계측 오류가 나서 보존된 모델을 재학습 없이 복구했다.
B의 학습시간·학습 RMSE·최고 메모리는 미확보다([복구 기록](../foundation340/RUNTIME-RECOVERY.md)).
엄격12GiB 메모리 예외 모델: {', '.join(memory_exceptions) if memory_exceptions else '없음'}.
최고 메모리·엄격 준수 여부를 확인하지 못한 모델: {', '.join(memory_unknown) if memory_unknown else '없음'}.
정책 timing.csv는 캐시 로드·프로필 계산을 뺀 순위 처리 시간이다. 모델 비용과 합쳐 종단 지연이라고 주장하지 않는다.

이 결과는 이미 사용한 MovieLens 개발 표본·조건당 한 번의 학습·현재 TMDB에 대한 판단이다.
보정구간도 앞 단계의 모델 선택 불확실성이나 학습 무작위성까지 보정하지 않는다.
2026년 한국 사용자, 최신작의 실제 만족도, 장르 novelty가 원하는 발견인지까지 검증한 것은 아니다.
새 최종 test나 서비스 채택으로 표현하지 않는다. Jira·GitLab·운영 설정은 변경하지 않았다.

실행과 상세 수치는 [1단계](../foundation340/EXECUTION.md), [2단계](../combination340/EXECUTION.md),
[3단계](../policy341/EXECUTION.md), [학습 비용](runtime.csv)을 따른다. 원본·모델·예측·검토 봉인은 로컬에 보존했다.
'''
    (DOC/'README.md').write_text(text,encoding='utf-8')
    sources=[module.OUT/name for module in [f,c,p] for name in ['evaluation-seal.json']]+[module.DOC/'result-review.json' for module in [f,c,p]]
    write_json(OUT/'report-seal.json',{'code':pin(__file__),'parents':{path.relative_to(f.ROOT).as_posix():pin(path) for path in sources},
                                     'files':{path.relative_to(f.ROOT).as_posix():pin(path) for path in [DOC/'README.md',DOC/'DETAILS.md',DOC/'comparison.png',DOC/'runtime.csv']}})
    print('FINAL_REPORT_CREATED',selected,selected_policy,flush=True)

if __name__=='__main__':main()
