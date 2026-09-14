"""Readable local result tables and one scientific comparison figure."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from textclean_source import OUT,DOC
from textclean_run import guard,verify
from rec046_common import require,pin,write_json

def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])

def report():
    require(not (DOC/'RESULT.md').exists(),'preserve result report')
    guard()
    for stage in ['clean-seal.json','prepared-seal.json','fit-seal.json','catalog-seal.json']:verify(stage)
    seal=json.loads((OUT/'evaluation-seal.json').read_text())
    for key,name in [('fit_seal','fit-seal.json'),('catalog_seal','catalog-seal.json')]:
        require(seal[key]==pin(OUT/name),'actual evaluation parent '+name)
    for n,p in seal['files'].items():require(pin(OUT/n)==p,'actual evaluated artifact '+n)
    user=pd.read_parquet(OUT/'user-metrics.parquet');a=user[(user.cap==10)&user.group.eq('ALL')]
    decision=json.loads((OUT/'decision.json').read_text());clean=json.loads((OUT/'clean-seal.json').read_text())
    exposure=json.loads((OUT/'preparation-summary.json').read_text());resource=json.loads((OUT/'resources.json').read_text())
    cat=pd.read_csv(OUT/'catalog-diagnostics.csv');deletions=pd.read_parquet(OUT/'deletions.parquet')
    names={'T0':'텍스트 없음(진단)','T3':'기존 본문','CLEAN':'의미 정제 후'};rows=[]
    for variant in names:
        b=a[a.variant.eq(variant)];valid=b[b.ndcg2.notna()]
        rows.append([names[variant],f'{len(valid)}/{int(b.stars2.notna().sum())}',f'{valid.ndcg2.mean():.6f}',f'{b.stars2.mean():.4f}',f'{b.low2.mean()*100:.2f}%'])
    delta=decision['comparisons']['ndcg2'];ci=delta['interval_95']
    conclusion='고정한 T3 대비 개선 기준을 통과했다.' if decision['improvement_criterion_passed'] else '고정한 T3 대비 개선 기준을 통과하지 못했다.'
    text='# 본문 의미 정제: 실제 학습·평가 결과\n\n상태: DRAFT — 로컬 개발 표본의 연구 결과. 제품 채택 계약이 아니다.\n\n'
    text+=conclusion+' 원문을 정제하면 언제나 좋아진다거나 텍스트가 쓸모없다는 결론은 아니다.\n\n'
    text+=f'85,517편에서 좁은 규칙으로 1,843개 문장 구간을 찾았다. 문맥 검토와 독립 재검토를 거쳐 **{clean["removed_spans"]}개 구간**을 삭제했다. 크레디트 단편 등도 있어 완전한 문장의 수와 같지는 않다. '
    text+=f'**{clean["changed_movies"]}편, {clean["changed_documents"]}개 본문**이 바뀌었고, {clean["empty_documents"]}개 본문은 비었다. 영화·후보 행은 유지했다.\n\n'
    text+='줄거리·인물·관계·감정·결말·다큐멘터리의 실제 대상은 유지했다. 외부 제작진/개봉/평론/홍보만 담긴 문장을 제거했고, 혼합되거나 경계가 모호하면 남겼다. 모든 불필요한 문장을 찾아낸 정제는 아니다.\n\n'
    text+='첫 적용본의 독립 검토에서 작품의 형식·원작·장르 정보가 섞인 삭제를 발견해 5개를 복원했고, 같은 보수적 보존 기준을 적용해 86개를 추가로 복원했다(삭제 440→435→349구간). 새 임베딩 생성·추가 학습 전에 교정했으며 실패한 두 적용본과 검토 기록을 보관했다.\n\n'
    text+='## 이미 별점을 남긴 후보 안의 Top2 비교\n\n'
    text+='입력 상한 H10 조건이다. 실제 입력 수 h는 0~10편이며, 사용자가 나중에 별점을 남긴 관측 후보가 2편 이상(J≥2)인 경우만 Top2를 계산한다. NDCG는 정답 순서의 점수(IDCG)가 0이면 제외하고, 평균별점·낮은 별점 비율은 유지한다.\n\n'
    text+=table(['입력','사용자(NDCG/별점)','NDCG@2 ↑','Top2 평균별점 ↑','2점 이하 비율 ↓'],rows)+'\n\n'
    fmt=lambda value:f'{value:+.6f}' if value is not None else '계산 불가'
    text+=f'정제 후−기존 본문의 NDCG@2 차이 **{fmt(delta["mean_delta"])}**, 사용자 짝 bootstrap 95% 구간 **[{fmt(ci[0])}, {fmt(ci[1])}]**. '
    text+='좋아요는 실제 별점 4점 이상, 낮은 별점은 2점 이하다. 평균별점은 여러 0.5 단위 실측값을 평균한 수치다.\n\n'
    text+=f'사전에 정한 개선 조건은 위 구간 하한>0, Top2 평균별점 차이≥−0.1점, 2점 이하 비율 차이≤+3%p를 모두 충족하는 것이다. 실제 보조 차이는 별점 {fmt(decision["comparisons"]["stars2"]["mean_delta"])}점, 낮은 별점 비율 {fmt(None if decision["comparisons"]["low2"]["mean_delta"] is None else decision["comparisons"]["low2"]["mean_delta"]*100)}%p다. 보조 조건은 큰 악화를 걸러내는 기준이며 서비스 안전성을 입증하지 않는다.\n\n'
    top_rows=[]
    for n in [2,4,6,10]:
        b=a[a.variant.eq('T3')];c=a[a.variant.eq('CLEAN')]
        top_rows.append([n,f'{int(b[f"ndcg{n}"].notna().sum())}/{int(b[f"stars{n}"].notna().sum())}',f'{b[f"ndcg{n}"].mean():.6f}',f'{c[f"ndcg{n}"].mean():.6f}',f'{c[f"stars{n}"].mean()-b[f"stars{n}"].mean():+.4f}',f'{(c[f"low{n}"].mean()-b[f"low{n}"].mean())*100:+.2f}%p'])
    text+='## 추천 개수별 기술통계\n\n'+table(['Top N','사용자(NDCG/별점)','기존 NDCG','정제 NDCG','평균별점 차이','낮은 별점 비율 차이'],top_rows)+'\n\n'
    text+='N마다 관측 후보가 부족한 사용자를 제외하므로 분모가 다르다. 주 판정은 Top2 하나이며 Top4/6/10이나 부분집단을 보고 판정 기준을 바꾸지 않았다. 같은 J≥6 사용자 표는 원시 metrics.csv의 common_j6에 있다.\n\n'
    text+='## 실제 전체 후보에서 추천하면\n\n'
    rr=[]
    for variant in ['T0','T3','CLEAN']:
        for group in ['ALL','h_positive']:
            row=cat[(cat.variant==variant)&(cat.n==2)&(cat.group==group)].iloc[0]
            rr.append([names[variant],'전체' if group=='ALL' else '실제 입력 있음',int(row.users),f'{int(row.known)}/{int(row.slots)}',int(row.unique_movies),f'{int(row.tmdb_one_vote_ten_slots)}/{int(row.slots)}'])
    text+=table(['입력','사용자군','사용자','채점 가능한 슬롯','추천된 영화 종류','TMDB 투표 1개·10점 슬롯'],rr)+'\n\n'
    text+='미평가 영화는 UNKNOWN이다. 이 표의 채점 가능 비율이나 투표 수를 추천 정확도로 바꾸지 않는다. 대중 평점 신뢰도 문제를 이번 본문 정제와 함께 수정하지 않았다.\n\n'
    text+='## 정제가 실제로 닿은 범위와 비용\n\n'
    text+=f'학습 목표 {exposure["train_target_rows"]:,}행, 학습 입력 {exposure["train_input_slots"]:,}슬롯에 변경 본문 영화가 있었다. H10 평가에서는 후보 {exposure["eval_changed_candidate_rows"]:,}행과 입력 {exposure["eval_changed_input_slots"]:,}슬롯에 포함됐고, 전체 270명 중 직접 노출 사용자는 {exposure["eval_direct_exposure_users"]}명이다. 이는 입력 또는 관측 후보에 변경 영화가 있는 사람 수이며, 추천 결과가 바뀐 사람 수나 Top2 채점 유효 사용자 수는 아니다. 직접 노출되지 않아도 재학습한 계수 변화의 영향을 받을 수 있다.\n\n'
    text+=table(['조건','학습 시간(분)','최고 메모리(bytes)','12GiB 엄격 상한'],[[names[v],f'{resource[v]["fit_seconds"]/60:.2f}',resource[v].get('peak_bytes'),'PASS' if resource[v]['strict_peak_12GiB_pass'] else 'EXCEPTION'] for v in ['T3','CLEAN']])+'\n\n'
    text+='기존 T3의 최고 메모리 12GiB+4,096바이트 초과는 과거 실행의 예외 기록이다. 이번에 T3를 다시 학습한 것은 아니며 CLEAN의 자원 결과와 구분한다.\n\n'
    base='../../../../outputs/recommendation-evidence/text339-clean/'
    text+='## 보조 결과를 확인하는 곳\n\n'
    text+=f'- [관측 후보 전체 지표]({base}metrics.csv): Top2/4/6/10의 좋아요·Recall, 평점 오차와 순서 일치. C는 의도적으로 학습 평점을 가린 영화, W는 학습 평점이 있는 나머지 영화, NATURAL_ZERO는 원래 학습 평점이 없는 나머지 영화다. 각 그룹의 관측 후보 안에서 다시 순위를 매긴 결과다.\n'
    text+=f'- [사용자별 조건 집계]({base}user-strata.csv): 실제 입력 수 h, 평가 활동량, 직접 노출 유무. 각 지표의 users_valid 분모를 함께 읽고 30명 미만 집단은 기술통계로만 해석한다.\n'
    text+=f'- [전체 카탈로그 진단]({base}catalog-diagnostics.csv): Top2/4/6/10의 UNKNOWN·노출 집중·학습 지원량. 미평가 추천의 정확도를 측정한 표가 아니다.\n\n'
    text+='## 해석과 다음 판단\n\n'
    text+='- 이미 여러 번 읽은 개발 사용자 270명에 대한 결과다. 새로운 최종 test나 2026 한국 서비스 성능 검증이 아니다.\n'
    text+='- 동일한 4,997,069개 학습 행·입력 이력·구조 특징·FM 설정을 사용했다. 바뀌지 않은 본문 임베딩은 비트 단위로 재사용하고 PCA도 고정했다.\n'
    text+='- 기준 T3와 CLEAN은 각각 한 번 학습했다. 동일 seed라도 Spark의 학습 경로가 완전히 같지는 않을 수 있다. 사용자 구간은 학습 무작위성을 포함하지 않는다.\n'
    text+='- TMDB와 Wiki를 함께 정제했으므로 두 출처의 개별 효과를 구분하지 못한다. 학습 h=0 목표 비중 82.34%도 그대로다.\n'
    text+='- 현재 TMDB·Wiki 스냅샷을 과거 MovieLens 별점과 결합했다. 메타데이터와 평가의 시점·이용자 편향 차이는 해결하지 못했다.\n'
    text+='- T0는 진단 기준이다. CLEAN이 T3를 넘어도 이 실험 하나로 T0 대체나 서비스 채택을 결정하지 않는다.\n\n'
    text+='[실행 설계](PLAN.md) · [원문/정제 예시](EXAMPLES.md) · [독립 결과 검토](result-review.json)\n'
    (DOC/'RESULT.md').write_text(text,encoding='utf-8')
    examples='# 실제 원문과 정제 결과\n\n상태: DRAFT — 삭제 전후 예시. 전체 외부 설명을 빠짐없이 정제했다는 뜻은 아니다.\n\n'
    sample=deletions[deletions.after.ne('')].copy();sample['lang_group']=sample.language.map(lambda s:'ko' if s.startswith('ko') else 'en')
    sample['length']=sample.before.str.len();chosen=sample.sort_values(['length','movie_id']).groupby(['source','lang_group'],sort=True).head(1)
    for r in chosen.itertuples(index=False):
        examples+=f'## MovieLens {r.movie_id} · {r.source} · {r.language}\n\n**원문**\n\n{r.before}\n\n**정제 후**\n\n{r.after}\n\n'
    (DOC/'EXAMPLES.md').write_text(examples,encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False})
    fig,axes=plt.subplots(1,3,figsize=(12,4.3),layout='constrained')
    for ax,metric,label in zip(axes,['ndcg2','stars2','low2'],['NDCG@2 ↑','Top2 평균별점 ↑','2점 이하 비율 ↓']):
        vals=[a[a.variant.eq(v)][metric].mean() for v in ['T0','T3','CLEAN']]
        if metric=='low2':vals=[v*100 for v in vals]
        ax.bar(['텍스트 없음','기존 본문','의미 정제'],vals,color=['#9aa6b2','#3269a8','#29968a'])
        for i,value in enumerate(vals):ax.text(i,value,f'{value:.4f}'+('%' if metric=='low2' else ''),ha='center',va='bottom',fontsize=10)
        ax.set_title(label);ax.spines[['top','right']].set_visible(False);ax.set_ylim(0,max(vals)*1.16)
    fig.suptitle('본문 의미 정제 비교 · 같은 개발 사용자와 관측 후보',fontsize=15)
    fig.savefig(DOC/'comparison.png',dpi=160);plt.close(fig)
    write_json(OUT/'report-seal.json',{'code':pin(__file__),'evaluation_seal':pin(OUT/'evaluation-seal.json'),
        'clean_seal':pin(OUT/'clean-seal.json'),'prepared_seal':pin(OUT/'prepared-seal.json'),
        'files':{n:pin(DOC/n) for n in ['RESULT.md','EXAMPLES.md','comparison.png']}})
    print('REPORT WRITTEN',flush=True)

if __name__=='__main__':report()
