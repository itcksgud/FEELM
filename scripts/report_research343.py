"""Readable tables from sealed evidence; no fitting or model selection search."""
import json
import argparse
import shutil
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from research343_common import *
from research343_evaluate import MODELS,eval_fingerprint

LABELS={'FM_B':'기존 FM','FM_R':'근거량 보정 FM','FM_RH':'근거량·이력 보정 FM','GBT_B':'기존 GBT','ALS':'ALS',
        'FM_ALS25':'기존 FM + ALS25%','GBT_R':'근거량 보정 GBT','LGBM_REG_R':'LightGBM 별점 회귀','LGBM_RANK_R':'LightGBM 순위 학습'}

def fmt(v,d=4):
    return 'N/A' if v is None or pd.isna(v) else f'{v:.{d}f}'
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])

def main(revise=False):
    records={n:verify(n) for n in ['evaluation-seal.json','catalog-seal.json','calibration-seal.json']}
    expected=eval_fingerprint()
    require(all(r['execution']==expected for r in records.values()),'same reviewed evaluation implementation')
    require(records['evaluation-seal.json']['calibration_seal']==pin(OUT/'calibration-seal.json'),'same calibration parent')
    for n in ['gbt-fit-seal.json','LGBM_REG_R-fit-seal.json','LGBM_RANK_R-fit-seal.json']:verify(n)
    existing=[p for p in [DOC/'RESULT.md',DOC/'comparison.png',DOC/'report-manifest.json',OUT/'resources.csv'] if p.exists()]
    if existing:
        require(revise,'existing report: use --revise to preserve previous version')
        versions=DOC/'report-history';versions.mkdir(exist_ok=True)
        archive=versions/f'version-{len(list(versions.iterdir()))+1:03d}';archive.mkdir(exist_ok=False)
        for p in existing:
            require(p.resolve().is_relative_to(ROOT.resolve()),'report revision stays within research workspace')
            shutil.copy2(p,archive/p.name)
    summary=pd.read_csv(OUT/'summary.csv');pages=pd.read_parquet(OUT/'page-metrics.parquet');catalog=pd.read_csv(OUT/'catalog-summary.csv')
    contrast=pd.read_csv(OUT/'primary-contrasts.csv');cost=pd.read_csv(OUT/'catalog-timing.csv');movie=pd.read_csv(OUT/'movie-metrics.csv')
    def metric(model,metric,group='ALL',cohort='natural',hgroup='H_POSITIVE',cap=10):
        a=summary[(summary.model==model)&(summary.metric==metric)&(summary.group==group)&(summary.cohort==cohort)&(summary.h_group==hgroup)&(summary.cap==cap)]
        require(len(a)==1,'one report metric');return a.iloc[0]
    models=[m for m in MODELS if m!='ALS'];mainrows=[]
    for m in models:
        n=metric(m,'native_mse');cal=metric(m,'calibrated_mse');top=metric(m,'ndcg2')
        mainrows.append([LABELS[m],fmt(n['mean']),fmt(cal['mean']),fmt(top['mean']),fmt(metric(m,'stars2')['mean'],3),fmt(100*metric(m,'low2')['mean'],2)+'%',f'{int(n.valid)}/{int(cal.valid)}',f'{int(top.valid)}/{int(metric(m,"stars2").valid)}'])
    direct=[]
    for m in MODELS:
        a=metric(m,'native_mse','W_DIRECT');b=metric(m,'ndcg2','W_DIRECT')
        direct.append([LABELS[m],fmt(a['mean']),fmt(metric(m,'calibrated_mse','W_DIRECT')['mean']),fmt(b['mean']),f'{int(a.valid)}/{int(metric(m,"calibrated_mse","W_DIRECT").valid)}',int(b.valid)])
    rankrows=[]
    for m in models:
        rankrows.append([LABELS[m]]+[fmt(metric(m,'ndcg'+str(n),cohort='common_j6')['mean'])+f' ({int(metric(m,"ndcg"+str(n),cohort="common_j6").valid)}명)' for n in [1,2,4,6]])
    pagerows=[]
    for m in models:
        a=pages[pages.cap.eq(10)&pages.h.gt(0)&pages.group.eq('ALL')&pages.model.eq(m)&pages.j.ge(6)]
        pagerows.append([LABELS[m]]+[fmt(a[a.end.eq(n)].stars.mean(),3) for n in [2,4,6]]+[fmt(100*a[a.end.eq(n)].both_low.mean(),2)+'%' for n in [2,4,6]]+[a.uid.nunique()])
    full=[]
    for m in MODELS:
        a=catalog[catalog.h_group.eq('H_POSITIVE')&catalog.model.eq(m)&catalog.end.eq(2)].iloc[0]
        full.append([LABELS[m],f'{int(a.one_vote_ten)}/{int(a.returned)}',int(a.unique_movies),fmt(a.hhi),fmt(100*a.max_share,2)+'%',int(a.unknown),int(a.missing)])
    cold=[]
    for group in ['C','NATURAL_ZERO']:
        for m in models:
            a=metric(m,'native_mse',group);b=metric(m,'ndcg2',group)
            cold.append([group,LABELS[m],fmt(a['mean']),fmt(metric(m,'calibrated_mse',group)['mean']),fmt(b['mean']),fmt(metric(m,'stars2',group)['mean'],3),f'{int(a.valid)}/{int(metric(m,"calibrated_mse",group).valid)}',f'{int(b.valid)}/{int(metric(m,"stars2",group).valid)}'])
    resources=[]
    paths={'GBT_B':COMBO/'GBT/metrics.json','ALS':COMBO/'ALS/metrics.json','GBT_R':OUT/'GBT_R/GBT/metrics.json',
           **{m:FOUND/v/'model/metrics.json' for m,v in [('FM_B','B'),('FM_R','R'),('FM_RH','RH')]},
           **{m:OUT/m/'metrics.json' for m in ['LGBM_REG_R','LGBM_RANK_R']}}
    rrows=[]
    for m,p in paths.items():
        if m in ['FM_B','FM_R','FM_RH']:
            v={'FM_B':'B','FM_R':'R','FM_RH':'RH'}[m];parent=read(FOUND/'fit-seal.json')
            modelseal=FOUND/v/'model/model-seal.json';require(pin(modelseal)==parent['files'][f'{v}/model/model-seal.json'],'sealed FM model parent')
            require(pin(p)==read(modelseal)['files']['metrics.json'],'sealed FM resource metrics')
        elif m in ['GBT_B','ALS']:
            require(pin(p)==read(COMBO/'fit-seal.json')['files'][p.relative_to(COMBO).as_posix()],'sealed original resource metrics')
        r=read(p);a=cost[cost.model.eq(m)];state='UNKNOWN' if r.get('peak_bytes') is None else 'PASS' if r.get('strict_memory_pass') else 'EXCEPTION'
        modelpath=OUT/'GBT_R/GBT/native' if m=='GBT_R' else OUT/m/'model.txt' if m.startswith('LGBM') else None
        size=sum(p.stat().st_size for p in modelpath.rglob('*') if p.is_file()) if modelpath is not None and modelpath.is_dir() else modelpath.stat().st_size if modelpath is not None else None
        row={'model':m,'fit_seconds':r.get('fit_seconds'),'peak_bytes':r.get('peak_bytes'),'resource_status':state,'model_bytes':size,
             'catalog_median_seconds':a.standalone_total_seconds.median() if len(a) else None,'catalog_p95_seconds':a.standalone_total_seconds.quantile(.95) if len(a) else None}
        resources.append(row);rrows.append([LABELS[m],fmt(None if row['fit_seconds'] is None else row['fit_seconds']/60,2),fmt(None if row['peak_bytes'] is None else row['peak_bytes']/1024**3,2),state,
                                         fmt(None if size is None else size/1024,1),fmt(row['catalog_median_seconds'],3),fmt(row['catalog_p95_seconds'],3)])
    pd.DataFrame(resources).to_csv(OUT/'resources.csv',index=False)
    contrasts=[]
    for r in contrast.itertuples():contrasts.append([LABELS[r.after]+' − '+LABELS[r.before],r.metric,fmt(r.delta,6),f'[{r.ci_low:.6f}, {r.ci_high:.6f}]',r.users])
    mm=[]
    for m in models:
        a=movie[movie.cap.eq(10)&movie.h_group.eq('H_POSITIVE')&movie.group.eq('ALL')&movie.model.eq(m)&movie.metric.eq('native_se')].iloc[0]
        mm.append([LABELS[m],fmt(metric(m,'native_mae')['mean']),fmt(metric(m,'p95_user_native_mae')['mean']),fmt(a.movie_macro),fmt(a.row_micro),int(a.movies)])
    doc=f'''# 새 모델과 기존 모델의 최종 비교

상태: DRAFT — 로컬 개발 표본 비교. 한국2026 서비스 성능 검증이 아니다.

{(DOC/'CONCLUSION.md').read_text(encoding='utf-8') if (DOC/'CONCLUSION.md').exists() else '수치 생성 완료, 독립 결과 감사와 결론 작성 중.'}

## 같은 조건에서 보는 별점 오차와 첫2편

기존 개발270명 중 보정90명과 비교180명을 분리했다. 아래는 비교180명 중 실제 입력이 있는124명,
입력상한10편이다. MSE는 사용자별 오차를 먼저 평균해 사용자를 동등하게 반영한다.
예측 별점만0.5..5로 제한하며, 실제 첫2편 별점은 원 평가값이다. 순위는 raw score다.
이전270명 표와 직접 증감을 비교하지 않는다.
순위 학습 모델의 원점수는 별점이 아니어서 native MSE=N/A, 별도 보정을 거친 MSE로 비교한다.
기존 회귀 모델도 입력상한별로 동일한2계수 보정을 적용했다. 보정용90명 중 유효 점수가 있는
사용자만 기여하므로 ALS는90명 모두를 쓰지 않는다.

{table(['모델','native MSE↓','보정 MSE↓','NDCG@2↑','첫2편 별점↑','별점≤2↓','native/보정 MSE명','NDCG/별점명'],mainrows)}

## ALS와 같은 영화에서 직접 비교

아래는 ALS가 입력과 목표영화를 지원하는 **동일 W_DIRECT**에서의 비교다.
ALL에서의 ALS 오차는 지원행 일부의 오차이므로 위 전체 표에 섞지 않았다.
ALS는 평가 근거가 없는 영화에 임의의 기본점수를 붙이지 않는다.

{table(['모델','native MSE↓','보정 MSE↓','NDCG@2↑','native/보정 MSE명','NDCG 유효명'],direct)}

## 사전에 고정한 두 대조

두 대조×MSE/NDCG2, 총4검정에98.75% paired-user bootstrap CI를 적용했다.
MSE차는 음수, NDCG차는 양수가 개선이다. 모델·보정 계수를 고정한 조건부 구간이다.
다른 보조 지표까지 검정을 확장해 승자를 고르지 않았다.

{table(['대조','지표','차이','98.75% CI','유효명'],contrasts)}

## Top1·2·4·6와 추가2편

아래 순위와 페이지 표는 각 사용자에게 실제 평가영화 J가6편 이상인 공통 모집단이다.
NDCG는 IDCG>0도 필요하므로 실제 별점 지표와 유효 분모가 다를 수 있다.

{table(['모델','NDCG@1','NDCG@2','NDCG@4','NDCG@6'],rankrows)}

{table(['모델','1–2편 별점','3–4편 별점','5–6편 별점','1–2 둘다≤2','3–4 둘다≤2','5–6 둘다≤2','명'],pagerows)}

## 평가 근거 없는 영화와 전체 후보

C는 평가를 의도적으로 학습에서 차단한 영화, NATURAL_ZERO는 학습평점이 자연적으로 없는 영화다.
둘 다 ALS가 직접 점수를 만들 수 없는 구간이며 서로 섞어서 해석하지 않는다.

{table(['구간','모델','native MSE↓','보정 MSE↓','NDCG@2↑','첫2편 별점','native/보정 MSE명','NDCG/별점명'],cold)}

전체 후보 첫2편은 실제 입력124명에게 최대248슬롯을 제공한다.
아래 UNKNOWN은 MovieLens에 그 사용자의 별점이 없어 품질을 채점할 수 없다는 뜻이다.
추천 실패/싫어요로 처리하지 않는다. 고유 영화수·HHI도 정답률이 아닌 쏠림 진단이다.

{table(['모델','투표1개·10점/반환','고유영화','HHI↓','최대영화 비중','UNKNOWN','미반환'],full)}

## 평균에 가려지는 오차와 비용

사용자 MAE의95백분위와 영화마다 같은 가중치를 주는 MSE를 함께 기록했다.
native 지표이므로 순위 모델은N/A다. 보정 지표와 입력상한0/1/5/10/30, 학습지원량0/1–9/10–49/50+
전체 수치는 연결된 CSV에 보존했다.

{table(['모델','user MAE','사용자MAE P95','movie MSE','행평균 MSE','유효영화수'],mm)}

{table(['모델','학습분','peak GiB','자원판정','새모델 KiB','후보추론 중앙초','후보추론 P95초'],rrows)}

이번 새 학습은3회이며 모델당4CPU/12GiB에서 한 번씩 수행했다. 기존 모델은 과거 기록을 재사용했다.
후보 추론비용은 입력0을 포함한 비교180명 기준으로, 위 입력 있는124명 품질표와 분모가 다르다.
크기는 GBT의 Spark native폴더, LightGBM의 native model.txt로 서로 다른 저장형식이다.
LightGBM은 Spark의 SynapseML로 학습했다. 이번 local[4] 실행에서는 단일 executor의 shared
dataset을 native learner 한 개가 학습했으며, 두 모델의 모든 tree가4997069행을 사용했다.
다중 노드 분산 학습·확장성은 이번에 검증하지 않았다.
전체후보 비용은 검산한 portable Python 추론+특징생성+Top10 계산이다. API SLA나 다중 서버
속도 측정이 아니다. 기존 모델의 개별 추론비용은 이번에 재측정하지 않아N/A다.
고정60회는 수렴이나 각 알고리즘 최적 성능을 보증하지 않는다.

## 해석 범위와 재현 자료

- 학습사용자39,859명/4,997,069행, 개발보정90명/비교180명은 사용자 단위로 분리했다.
- 비교180명도 과거에 확인한 개발 데이터다. 새로운 최종 test나 한국2026 성능으로 부르지 않는다.
- 관측 순위는 실제 평가한 J 안의 선택이다. 전체 후보의 미평가영화를 싫어요로 만들지 않았다.
- 전체 후보도 MovieLens와 연결된85,517편 연구 카탈로그에서 개봉일·기시청 조건을 적용한 것이며,
  2026년 한국에서 볼 수 있는 모든 영화 카탈로그가 아니다.
- TMDB는 현재 스냅샷이고, MovieLens의 사용자·시점·노출 편향을 다양한 지표만으로 제거할 수 없다.
- K-means/분류별 맞춤·발견 정책, 새 ALS 가중치 탐색, 추가 텍스트 학습, Jira·GitLab 게시는 수행하지 않았다.

[설계](DESIGN.md) · [구현 명확화](IMPLEMENTATION.md) · [독립 결과검토](result-review.json)

[주 지표와 모든 구간](../../../../outputs/recommendation-evidence/research343/summary.csv) ·
[영화별 오차](../../../../outputs/recommendation-evidence/research343/movie-metrics.csv) ·
[공식 대조](../../../../outputs/recommendation-evidence/research343/primary-contrasts.csv) ·
[전체 후보](../../../../outputs/recommendation-evidence/research343/catalog-summary.csv) ·
[보정 계수](../../../../outputs/recommendation-evidence/research343/calibration.json) ·
[사용자 분리](../../../../outputs/recommendation-evidence/research343/roles.csv)
'''
    (DOC/'RESULT.md').write_text(doc,encoding='utf-8')
    plt.rcParams['font.family']='Malgun Gothic';plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(1,3,figsize=(14,5.8),sharey=True,layout='constrained')
    ys=np.arange(len(models));axes[0].set_yticks(ys,[LABELS[m] for m in models]);axes[0].invert_yaxis()
    for axis,name,title in [(axes[0],'calibrated_mse','보정 MSE ↓'),(axes[1],'ndcg2','NDCG@2 ↑')]:
        values=np.array([metric(m,name)['mean'] for m in models]);span=max(float(np.ptp(values)),.01)
        axis.scatter(values,ys,s=45,color='#336699');axis.set_xlim(values.min()-.2*span,values.max()+.6*span)
        for y,v in zip(ys,values):axis.annotate(f'{v:.4f}',(v,y),xytext=(6,0),textcoords='offset points',va='center',fontsize=8)
        axis.set_title(title+f' · {int(metric(models[0],name).valid)}명',fontsize=11);axis.grid(axis='x',alpha=.2)
        axis.set_xlabel('점추정치 · 사전 대조의 차이 CI는 본문 표')
    sub=catalog[catalog.h_group.eq('H_POSITIVE')&catalog.end.eq(2)].set_index('model').loc[models]
    shares=(100*sub.one_vote_ten/sub.returned).to_numpy();axes[2].barh(ys,shares,color='#779955',height=.55)
    for y,v in zip(ys,shares):axes[2].text(v+2,y,f'{v:.1f}%',va='center',fontsize=8)
    axes[2].set(xlabel='투표1개·10점 영화 노출 (%)',xlim=(0,115),xticks=[0,25,50,75,100],title='전체 후보 첫2편 · 248슬롯')
    fig.suptitle('별점 정확도 · 첫2편 순위 · 추천 쏠림을 따로 비교\nMovieLens 개발표본 · 한국2026 검증 아님',fontsize=12)
    fig.savefig(DOC/'comparison.png',dpi=170);plt.close(fig)
    write_json(DOC/'report-manifest.json',{'report_code':pin(__file__),'result':pin(DOC/'RESULT.md'),'figure':pin(DOC/'comparison.png'),
      'evaluation_seal':pin(OUT/'evaluation-seal.json'),'catalog_seal':pin(OUT/'catalog-seal.json'),'resources':pin(OUT/'resources.csv')})
    print('REPORT_GENERATED',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--revise',action='store_true');a=p.parse_args();main(a.revise)
