"""Human-readable aggregate export; never reads user-level labels or profiles."""
from __future__ import annotations
import json
import shutil
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from cold_item_common import ROOT, DOC, OUT, VARIANTS, METHODS, pin, write_json, verify_seal, reviewed, require

def report():
    result=DOC/'RESULT.md'
    assert not result.exists(), 'preserve report'
    reviewed(); verify_seal(OUT/'prepared-seal.json',OUT)
    fit=verify_seal(OUT/'fit-seal.json',OUT)
    done=verify_seal(OUT/'completion.json',OUT)
    diagnostics=verify_seal(OUT/'diagnostic-seal.json',OUT)
    require(fit['prepared_seal']==pin(OUT/'prepared-seal.json') and done['fit_seal']==pin(OUT/'fit-seal.json'),'completion parents')
    require(diagnostics['prepared_seal']==pin(OUT/'prepared-seal.json') and diagnostics['generator']==pin(ROOT/'scripts/cold_item_diagnostics.py'),'diagnostic parents')
    m=pd.read_csv(OUT/'metrics.csv');m=m[m.cohort=='eligible_k']
    c=pd.read_csv(OUT/'contrasts.csv')
    info=json.loads((OUT/'feature-info.json').read_text())
    labels=['기존 정보','근거량 강화','대중 지표','사용자 반응']
    plt.rcParams['font.family']='Malgun Gothic';plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(2,2,figsize=(13,8),layout='constrained')
    lines=['# 평점 이력을 가린 영화: 콘텐츠 모델과 ALS 비교','',
           '상태: DRAFT — 계산 완료 후 독립 결과 검토 대상. 서비스 채택 판정이 아니다.','',
           '같은 영화의 다른 학습 사용자 과거 평점은 ALS에 남기고, 콘텐츠 모델의 모든 평점 학습 경로에서는 제거했다. 같은 평가 사용자·입력·미래 관측에서 비교했다.','',
           f'콘텐츠 학습은 {info["train_users"]:,}명·{info["train_rows"]:,}개 평점이다. 새 학습은 4조건 × FM·리지·GBT = 12개이며 참고 ALS는 기존 전체 학습 요인으로 입력 벡터를 다시 계산했다. 기본값을 ALS 점수로 쓰지 않았다.','',
           '![동일 영화에서 입력 정보 추가에 따른 성적 변화](comparison.png)','',
           '아래 숫자는 사용자 평균이다. MSE는 낮을수록, 선호 순서 정확도(PA)는 높을수록 좋다. 실제 MSE는 예측을0.5–5범위로 제한하고 PA·ALS재현도는 반올림하지 않은 원래 예측을 쓴다. ALS는 비교 참고선이며 실제 별점이 정답이다. 그래프의 순위만으로 우열을 확정하지 않는다.',
           '같은 K 안에서는 같은 사용자를 비교한다. K10과K30의 사용자 집합은 달라 K 간 차이를 입력 증가의 효과만으로 해석할 수 없다. 공통K30 사용자 비교는 전체 지표의 common_k30에 별도로 있다.','']
    def value(k,model,metric):
        a=m[(m.k==k)&(m.method==model)&(m.metric==metric)]
        assert len(a)==1
        return a.iloc[0]
    for row,k in enumerate([10,30]):
        mse=value(k,'ALS','mse');pa=value(k,'ALS','pa')
        lines += [f'## 입력 {k}편','',f'별점 오차: {int(mse.users_valid)}명·{int(mse.observations_valid)}관측·{int(mse.movies_valid)}영화. 순서 비교: {int(pa.users_valid)}명·{int(pa.comparable_pairs_valid):,}쌍.','',
                  '| 모델 | 기존 정보 MSE | 근거량 강화 | 대중 지표 추가 | 사용자 반응 추가 |','| --- | ---: | ---: | ---: | ---: |']
        for model in METHODS:
            vals=[value(k,v+'__'+model,'mse')['mean'] for v in VARIANTS]
            lines.append('| '+model+' | '+' | '.join(f'{x:.4f}' for x in vals)+' |')
        lines += [f'| 참고 ALS | {mse["mean"]:.4f} | — | — | — |','',
                  '| 모델 | 기존 정보 PA | 근거량 강화 | 대중 지표 추가 | 사용자 반응 추가 |','| --- | ---: | ---: | ---: | ---: |']
        for model in METHODS:
            vals=[value(k,v+'__'+model,'pa')['mean'] for v in VARIANTS]
            lines.append('| '+model+' | '+' | '.join(f'{100*x:.1f}%' for x in vals)+' |')
        lines += [f'| 참고 ALS | {100*pa["mean"]:.1f}% | — | — | — |','']
        for col,metric in enumerate(['mse','pa']):
            ax=axes[row,col];scale=100 if metric=='pa' else 1
            for model in METHODS:
                a=[value(k,v+'__'+model,metric) for v in VARIANTS]
                y=np.array([r['mean']*scale for r in a]);ax.plot(range(4),y,marker='o',label=model)
            ref=value(k,'ALS',metric)['mean']*scale
            ax.axhline(ref,color='#444444',ls='--',lw=1.4,label='참고 ALS')
            ax.set_xticks(range(4),labels);ax.set_title(f'입력 {k}편 · '+('별점 오차 MSE ↓' if metric=='mse' else '선호 순서 정확도 % ↑'))
            ax.grid(axis='y',alpha=.2);ax.legend(fontsize=8)
    fig.suptitle('MovieLens 평점이 가려진 동일 영화 · 동일 사용자 비교\n2026 TMDB 스냅샷 탐색 · 그래프는 점추정, 통계 판정은 본문 구간 참조',fontsize=14)
    fig.savefig(DOC/'comparison.png',dpi=160);plt.close(fig)
    lines += ['## 사전 고정한 짝 비교','',
              '인접 비교36개를 한 family, ALS 참고 비교48개를 별도 family로 보정했다. 아래는 보정 구간이0을 포함하지 않는 비교만 표시한다.','',
              '| 비교 | 입력 | 모델/조건 | 지표 | 평균 차이 | 보정 구간 | 방향 |','| --- | ---: | --- | --- | ---: | --- | --- |']
    significant=c[(c.ci_low>0)|(c.ci_high<0)]
    for _,r in significant.iterrows():
        good=r.ci_high<0 if r.metric=='mse' else r.ci_low>0
        lines.append(f'| {r.family} | {r.k} | {r["new"]} − {r.reference} | {r.metric} | {r.difference:.5g} | [{r.ci_low:.5g}, {r.ci_high:.5g}] | '+('개선' if good else '악화')+' |')
    if significant.empty:lines.append('| 전체 | — | — | — | — | — | 개선·악화 확정 없음 |')
    lines += ['', '차이를 확인하지 못한 비교를 동급으로 해석하지 않는다. 작은 표본과 넓은 구간 때문에 방향이 불확실할 수 있다.','',
              '## ALS 예측 재현도','',
              '| 입력 | 모델 | 조건 | ALS 점수 대비 MAE ↓ | MAE 인원 | ALS 순서 일치율 ↑ | 순서 인원/쌍 |','| ---: | --- | --- | ---: | ---: | ---: | --- |']
    for k in [10,30]:
        for model in METHODS:
            for v,label in zip(VARIANTS,labels):
                a=value(k,v+'__'+model,'als_mae');b=value(k,v+'__'+model,'als_order')
                lines.append(f'| {k} | {model} | {label} | {a["mean"]:.4f} | {int(a.users_valid)} | {100*b["mean"]:.1f}% | {int(b.users_valid)}명/{int(b.reference_pairs_valid):,}쌍 |')
    composition=pd.read_csv(OUT/'training-composition.csv')
    k0=composition[composition.k==0].iloc[0]
    resources=[]
    for v in VARIANTS:
        for model in METHODS:
            q=json.loads((OUT/'models'/v/model/'metrics.json').read_text())
            require(q.get('peak_bytes',0)>0,'measured memory peak required')
            resources.append({'condition':v,'model':model,'fit_seconds':q['fit_seconds'],'worker_seconds':q['seconds'],
                              'peak_gib':q['peak_bytes']/1024**3,'iterations':q.get('total_iterations'),
                              'optimizer_history_available':bool(q.get('objective_history')),
                              'train_raw_rmse':q['train_raw_rmse']})
    pd.DataFrame(resources).to_csv(DOC/'training-resources.csv',index=False)
    available=pd.read_csv(OUT/'feature-availability.csv')
    no_links=[]
    for block in ['DIRECTOR','CAST']:
        a=available[(available.k==30)&(available.feature==block+'_no_link')]
        require(len(a)==1 and a.iloc[0].meaning=='no_direct_link','unique defined coverage row')
        no_links.append(float(a.iloc[0].fraction))
    lines += ['', 'ALS와 비슷한 예측을 내는 능력과 실제 별점을 맞히는 능력은 다르다. 전체 카탈로그에서 정답 추천 순서를 평가한 결과가 아니다.','',
              '## 학습과 입력의 한계','',
              f'학습 목표의 {100*k0.target_share:.2f}%는 입력 K0이며, 입력이 있는 목표는 {int(composition.targets.sum()-k0.targets):,}건이다. 전체 평점 수를 모두 개인화 입력이 있는 학습 사례로 부르지 않는다.',
              f'입력30편에서도 감독 직접 연결이 없는 관측은{100*no_links[0]:.2f}%, 배우는{100*no_links[1]:.2f}%다. 연결이 없는 것을 해당 속성을 싫어하거나 쓸모없다는 뜻으로 해석하지 않는다.',
              f'콘텐츠12개 실제 학습 함수 시간 합은 {sum(q["fit_seconds"] for q in resources)/60:.1f}분이며 최대 컨테이너 메모리는 {max(q["peak_gib"] for q in resources):.2f}GiB다. 데이터 준비·감사·저장까지 포함한 총 작업 시간이나 서비스 추론 지연이 아니다.',
              '고정 반복 횟수와 학습 설정을 사용했다. 최적 수렴을 입증한 모델 순위표로 해석하지 않는다.', '',
              '## 해석 범위','',
              '- SUPPORT 차이는 근거량과 수축 표현 묶음의 효과다. CROWD_ITEM은 영화 대중 지표, CROWD_RESPONSE는 사용자 입력 분포와 반응 프로필 묶음의 추가 효과다.',
              '- 대중 지표는 2026년 TMDB 정보다. 과거에 이미 알던 값으로 취급하지 않으며, 외부 집계 평가가 없는 순수 콘텐츠 조건으로 부르지 않는다.',
              '- 기존에 확인한 MovieLens 개발 정답을 재사용했다. 단일 영화 보류 분할이며 사용자 bootstrap은 이 영화 집합에 조건부다. 2026년 한국 서비스 품질·개인별 인과효과·최적 학습 성능을 보증하지 않는다.',
              '- 학습 K0 비중, 최적화 종료 및 자원 기록, 세부 활동량·영화 지원층은 부록 파일과 최종 독립 검토에서 확인한다.',
              '', '[실행 명세](EXECUTION.md) · [전체 지표](metrics.csv) · [짝 비교](contrasts.csv) · [집단별 지표](strata.csv) · [학습 자원](training-resources.csv)', '']
    result.write_text('\n'.join(lines),encoding='utf-8')
    for name in ['metrics.csv','contrasts.csv','strata.csv','training-composition.csv','feature-availability.csv']:shutil.copyfile(OUT/name,DOC/name)
    write_json(DOC/'aggregate-export.json',{'exporter':pin(ROOT/'scripts/report_cold_item.py'),'source_completion':pin(OUT/'completion.json'),
               'diagnostic_seal':pin(OUT/'diagnostic-seal.json'),'files':{n:pin(DOC/n) for n in ['RESULT.md','comparison.png','metrics.csv','contrasts.csv','strata.csv','training-resources.csv','training-composition.csv','feature-availability.csv']}})
    print('AGGREGATE_REPORT_WRITTEN')

if __name__=='__main__':report()
