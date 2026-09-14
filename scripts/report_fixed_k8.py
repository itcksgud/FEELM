"""Render descriptive reports from frozen artifacts; does not change selections."""
from pathlib import Path
import json
import shutil
import numpy as np
import pandas as pd
from k8_common import DOC,OUT,read,write,pin,verify_seal

def table(df,columns=None):
    if columns is not None:df=df[columns]
    def cell(v):
        if isinstance(v,(float,np.floating)):return '' if not np.isfinite(v) else f'{v:.6f}'
        return str(v).replace('|','/').replace('\n',' ')
    return '| '+' | '.join(map(str,df.columns))+' |\n| '+' | '.join(['---']*len(df.columns))+' |\n'+'\n'.join('| '+' | '.join(cell(v) for v in row)+' |' for row in df.itertuples(index=False,name=None))+'\n'

def main():
    for stage in ['prepare','train','recommend','final']:verify_seal(stage)
    prep=read(OUT/'prepare/report.json');content=read(OUT/'train/content-selection.json')
    selection=read(OUT/'recommend/selection.json');decision=read(OUT/'recommend/decision.json');invariance=read(OUT/'final/invariance.json')
    manifest=read(OUT/'final/manifest.json');eligibility=read(OUT/'recommend/eligibility.json')
    packets=read(OUT/'final/descriptors.json');assignments=pd.read_parquet(OUT/'final/assignments.parquet')
    vf=pd.read_csv(OUT/'recommend/validation-curves.csv');ff=pd.read_parquet(OUT/'recommend/verification-per-user.parquet')
    pred=pd.read_parquet(OUT/'recommend/rating-predictions.parquet');supply=pd.read_parquet(OUT/'recommend/deployment-supply.parquet')
    audit=read(OUT/'recommend/interaction-source-audit.json');loss=pd.DataFrame(audit['viewed_and_mapping_losses'])
    summary=pd.DataFrame(read(OUT/'recommend/verification-summary.json'))
    mse=[]
    for (model,support),a in pred.assign(support=np.where(pred.als_item_supported,'ALS-item','no-ALS-item')).groupby(['model','support']):
        err=a.prediction-a.rating;byuser=a.assign(ae=np.abs(err),se=err**2).groupby('uid')[['ae','se']].mean()
        mse.append({'model':model,'support':support,'ratings':len(a),'users':int(a.uid.nunique()),'MAE_micro':float(np.abs(err).mean()),'RMSE_micro':float(np.sqrt((err**2).mean())),
                    'MAE_macro':float(byuser.ae.mean()),'RMSE_user_MSE':float(np.sqrt(byuser.se.mean()))})
    mse=pd.DataFrame(mse);mse.to_csv(DOC/'rating-error.csv',index=False)
    cohort=[]
    for (policy,zero),a in ff[ff.cap==10].groupby(['policy','actual_zero_history']):
        cohort.append({'policy':policy,'actual_zero_history':bool(zero),'users':len(a),'positive_users':int(a.valid_positive.sum()),
                       'NDCG10':float(a.ndcg10.mean()),'Recall10':float(a.recall10.mean()),'underseen':float(a.underseen_share.mean()),'UNKNOWN':float(a.unknown_share.mean())})
    cohort=pd.DataFrame(cohort);cohort.to_csv(DOC/'cohort-metrics.csv',index=False)
    supply_rows=[]
    for policy,a in supply.groupby('policy'):
        exposed=np.unique(np.concatenate(a.ranked.to_numpy()))
        supply_rows.append({'policy':policy,'users':len(a),'unique_exposed':len(exposed),'mean_candidates':float(a.candidate_count.mean()),
                            'p50_ms':float(a.latency_ms.median()),'p95_ms':float(a.latency_ms.quantile(.95)),
                            'service_only_slots':int(a.service_only_slots.sum()),'zero_train_slots':int(a.zero_train_slots.sum()),'UNKNOWN_share':float(a.unknown_share.mean())})
    supply_rows=pd.DataFrame(supply_rows)
    for name,source in [('content-selection.json',OUT/'train/content-selection.json'),('child-search.json',OUT/'train/child-search.json'),
                        ('selection.json',OUT/'recommend/selection.json'),('decision.json',OUT/'recommend/decision.json'),
                        ('invariance.json',OUT/'final/invariance.json'),('validation-curves.csv',OUT/'recommend/validation-curves.csv'),
                        ('verification-summary.json',OUT/'recommend/verification-summary.json')]:shutil.copyfile(source,DOC/name)
    curves=vf[vf.kind=='group'].sort_values(['ndcg10','id'],ascending=[False,True]).drop_duplicates(['hierarchy','budget']).sort_values(['hierarchy','budget'])
    curves.to_csv(DOC/'best-validation-by-hierarchy-budget.csv',index=False)
    subgroup=[]
    for p in packets:
        if p['kind']=='group':subgroup.append({'group_id':p['id'],'taste_id':p['parent'],'name':manifest['names']['groups'][str(p['id'])],
                     'movies':p['count'],'top_unsupported':p['top_unsupported'],'no_content':p['no_content'],
                     'genres':'; '.join(g for g,n in p['genre_top'][:3]),'keywords':'; '.join(k for k,n in p['keyword_top'][:5]),
                     'examples':'; '.join(x['title'] for x in p['examples'][:3]),'korean_examples':'; '.join(x['title'] for x in p['korean_examples'][:2])})
    subgroup=pd.DataFrame(subgroup);subgroup.to_csv(DOC/'subgroups.csv',index=False,encoding='utf-8-sig')
    guide=['# 8개 팝콘 맛과 하위 그룹','', '상태: DRAFT 개인 연구 결과. 이름은 관측 분포를 보고 붙인 설명이며 팀 제품 계약이 아니다.',
           '', '예시는 실제 배정을 조회한 결과다. 자동 목록은 TMDb 투표 수가 많은 영화와 한국어 원어 영화를 표시한다. 투표 수가 한국인에게 익숙함을 보장하지는 않는다. 투표 수는 설명용 사례 선정에만 사용했다. 별도 한국 익숙한 영화 설명은 실제 ID/배정을 대조해 사람이 검토한다. 몇 작품이 익숙하거나 비슷해 보인다는 이유로 군집 품질을 판정하지 않는다.', '']
    for p in packets:
        if p['kind']!='taste':continue
        name=manifest['names']['tastes'][str(p['id'])]
        guide += [f'## {p["id"]}. {name}', '',f'{p["count"]:,}편 · 하위 그룹 {int((subgroup.taste_id==p["id"]).sum())}개 · 상위 특징 미지원 {p["top_unsupported"]:,}편 · 모든 콘텐츠 없음 {p["no_content"]:,}편.', '',
                  '장르 분포: '+', '.join(f'{g} {n:,}편' for g,n in p['genre_top'][:5])+'.',
                  '빈도가 높은 키워드: '+', '.join(k for k,n in p['keyword_top'][:8])+'.',
                  '투표 수가 많은 작품 예시: '+', '.join(x['title'] for x in p['examples'][:6])+'.',
                  '한국 영화 예시: '+(', '.join(x['title'] for x in p['korean_examples']) or '이 그룹에 한국어 원어 영화가 없음')+'.','',
                  table(subgroup[subgroup.taste_id==p['id']][['group_id','movies','genres','keywords','examples','korean_examples','top_unsupported']])]
    guide += ['', '## 근거가 약한 영화', '',
              '하위 그룹15의 액션·모험·SF 표시는 정보가 있는 영화들에서 빈도가 높은 장르 설명이다.44,414편 중30,577편(약68.84%)은 상위 장르 특징이 미지원이므로 그룹 전체를 근거 있는 액션 취향으로 설명하지 않는다. 영화별 top_supported/weak_evidence 필드를 함께 확인한다.', '',
              f'전체에서 변환 후 상위 특징 미지원 {invariance["top_unsupported"]:,}편, 약한 근거 {invariance["weak_evidence"]:,}편. 키워드가 있지만 전부 고정 어휘 밖인 영화 {invariance["keyword_oov_only"]:,}편, 줄거리가 있지만 고정 표현에서 영벡터인 영화 {invariance["overview_oov_only"]:,}편이다. 이 영화도 정확히 하나의 맛/하위 그룹에 배정하지만 의미가 검증됐다는 뜻은 아니다. 추천 노출에는 공통 콘텐츠 지원 및 날짜/상태 필터를 별도로 적용했다.', '',
              table(assignments[assignments.weak_evidence].head(20)[['service_movie_id','title','taste_id','group_id','top_supported','keyword_oov_only','overview_oov_only']])]
    (DOC/'KOREAN-GUIDE.md').write_text('\n'.join(guide),encoding='utf-8')
    primary=summary[summary.cap==10]
    high_search=read(OUT/'train/child-search.json')
    high_counts=[{'K':k,'fitted':sum(r.get('k')==k and 'validation_distortion' in r for r in high_search),
                  'mathematical_skip':sum(r.get('k')==k and 'skip_reason' in r for r in high_search),
                  'passed_min100':sum(r.get('k')==k and r.get('accepted',False) for r in high_search)} for k in [64,128,256]]
    known=[]
    for policy,a in ff[ff.cap==10].groupby('policy'):
        slots=int(a.known_slots.sum())
        known.append({'policy':policy,'known_slots':slots,'known_star_mean':float((a.known_stars_mean.fillna(0)*a.known_slots).sum()/slots) if slots else None,
                      'zero_train_slots':int(a.zero_train_slots.sum()),'service_only_slots':int(a.service_only_slots.sum()),'all_returned_slots':int(a.returned_count.sum())})
    known=pd.DataFrame(known)
    history_diag=read(DOC/'history-stratified-diagnostic.json')
    warm=history_diag['strata']['False'];cold=history_diag['strata']['True']
    lines=['# 고정8맛·발견 추천 실험 결과','', '상태: DRAFT — 개인 연구, DEVELOPMENT_ONLY. 서비스 채택·한국 사용자 만족도·최적해를 주장하지 않는다.', '',
           '**적용 제안: 고정8맛·16하위 그룹 산출물은 제공하지만, 개인화 발견 추천으로의 교체는 보류하고 무그룹 기준선을 유지한다.** 전체 평균의 사전 통과와 사용자가 이미 남긴 평가를 활용하는 개인화 개선은 같지 않았다.', '',
           f'사전 비교의 연구 판단: **{decision["recommendation"]} — 같은 예산의 무그룹 기준선에 대한 조건 통과**. 서비스 채택 또는 전체 추천 방식 중 우승 선언은 아니다. 선택한 하위 계층은 `{selection["selected_hierarchy"]}`, 그룹 후보 정책은 `{selection["group"]["id"]}`, 같은 예산의 기준선은 `{selection["baseline"]["id"]}`다.', '',
           f'전체 서비스 {prep["rows"]:,}편을 정확히8맛과 {invariance["groups"]}개 하위 그룹으로 배정했다. 분류 불변성 검사는 PASS이며 추천 채택 판단과 별개다.', '',
           f'이력이 있는 사용자 {warm["positive_users"]}명의 NDCG@10은 {warm["flat_ndcg"]:.6f}→{warm["group_ndcg"]:.6f}로 낮아졌다. 이력이 없는 {cold["positive_users"]}명은 {cold["flat_ndcg"]:.6f}→{cold["group_ndcg"]:.6f}로 높아졌다. 즉 전체 개선으로 개인 취향 벡터의 발견 효과를 입증하지 못했다. 그룹 수를 많이 늘리는 것만으로 해결되지 않았다.', '',
           f'실제 무이력 여부는 사전 진단 층이며, 층별 paired CI는 결과 검토 후 추가한 **설명용 분석**이다: 이력 있음 차이 CI {warm["delta_ci"]["ci95"]}, 이력 없음 {cold["delta_ci"]["ci95"]}. 이 분석으로 원래 선택·전체 통과 기준을 소급 변경하지 않았다. 개인화 서비스 적용 제안의 한계를 설명한다.', '',
           '## 추천 품질과 시간', '',table(primary[['policy','users','positive_users','ndcg2','ndcg6','ndcg10','recall10','candidate_recall','underseen_share','unknown_share','candidate_count','latency_p50_ms','latency_p95_ms']]),'',
           f'그룹−무그룹 NDCG@10 차이 {decision["ndcg_delta"]["delta"]:.6f}, paired95%CI {decision["ndcg_delta"]["ci95"]}. 저이력 그룹 출력 비율 차이 {decision["underseen_delta"]["delta"]:.6f}, CI {decision["underseen_delta"]["ci95"]}. NDCG 하한≥−0.01과 저이력 그룹 노출 비율 차이 하한>0을 모두 요구한 사전 기준으로 판정했다.', '',
           '선택안의 후보 양성 회수율은0.141896으로 무그룹0.181443보다 낮다. 평균100개 후보 중52.5개는 전역 품질 목록으로 채웠다. 단순 인기순은 NDCG@10=0.118524로 선택안0.115046보다 수치상 높고, Recall@10도 높다. 인기순 대비 우위를 입증했다고 결론내리지 않는다.', '',
           table(known),'', '관측 평균 별점은 실제 알려진 출력 슬롯에 한정한 조건부 진단이다. 관측되지 않은 슬롯을 제외한 이 평균만으로 정책을 선택하지 않았다.', '',
           '미평가는 UNKNOWN이다. 전체 후보 순위의 관측 gain이0인 것은 비선호 판정이 아니다. 따라서 NDCG/Recall은 알려진 미래 선호를 다시 찾아내는 개발 지표이며 전체 추천 만족도를 측정하지 못한다.3.5점에도 gain0.5를 부여하고,4점 이상 정답이 있는 사용자를 주평가 분모로 삼았다.', '',
           '## 하위 그룹 수·후보 예산 선택', '',
           f'**BAND_EMPTY={selection["band_empty"]}**: 검증90명에서 최고 NDCG−0.01 이내와 최고 후보 회수율의90% 이상을 동시에 만족한 설정이 없었다. 따라서 사전에 정한 최고NDCG 대체 규칙을 적용했다.100편 예산이 두 목표 구간을 만족했다거나 최적의 품질·속도 절충임을 입증한 것이 아니다.', '',
           '맛당1·2·4·8·16·32·64·128·256을 시도했다. 고유 벡터가 부족한 K는 수학적 실행 불가로 기록했고, 실행 후 최소100개 train 영화가 없는 하위 그룹을 가진 K는 선택에서 제외했다. 각 맛에서 최저 검증 distortion의2% 안에 드는 가장 작은 K를 택했다. cap8/32/128/256과fixed2의 추천 결과도 비교했으며, 동일한 계층은 alias로 기록했다.', '',
           table(pd.DataFrame(high_counts)), '', '실행한64/128/256은 모두 최소100편 기준 미달이었다. 따라서 높은K 자체의 추천 품질이 나쁘다고 확인한 결과는 아니다. cap128/256은 사전 선택 규칙상 cap32와 같은 계층으로 수렴해 alias로 평가했다.', '',
           table(curves[['hierarchy','budget','id','n_groups','ndcg10','candidate_recall','latency_p50_ms','latency_p95_ms']]),'',
           '전체 설정별 곡선은 [validation-curves.csv](validation-curves.csv),128/256 포함 실제 K별 시도·제외·alias는 [child-search.json](child-search.json)에 보존했다. 검증180명의 결과를 보고 설정을 다시 선택하지 않았다.', '',
           '## 분류 선택과 데이터 근거', '',
           f'선택 표현 `{content["selected"]["key"]}`. 별도 content verification에서 도전−장르 기준선 D8 차이는 {content["verification_delta"]["delta"]:.6f}, CI {content["verification_delta"]["ci95"]}. 이는 동일 장르/키워드/어휘 줄거리 표현의 복원 손실 비교이며 객관적 영화 의미나 추천 만족도 정답이 아니다. 이후 선택 설정으로 전체 카탈로그 중심을 한 번 refit했으므로 앞의 점수는 최종 전체 refit 모델의 미관측 성능으로 부르지 않는다.', '',
           f'키워드 SVD 설명분산은 {content["preprocessor_variance"]["keyword_explained_variance"]:.4%}, 어휘 줄거리 SVD는 {content["preprocessor_variance"]["text_explained_variance"]:.4%}다. D8는 이미 축소된 참조 공간과 블록별 지원 영화에 대한 손실이다. 분류 정확도 또는 영화 의미 보존율이 아니다.', '',
           table(pd.DataFrame(content['validation'])[['key','D8','iterations','converged_before_cap']]),'',
           f'본문 ID {prep["all_body_ids_verified"]:,}개와 키워드 본문 ID {prep["keyword_body_ids_verified"]:,}개를 전수 검증했다. 키워드 파일 누락 {prep["keyword_missing_files"]}편. 실제 가용량 {prep["availability"]}; 줄거리·키워드 둘 다 없음 {prep["neither_overview_nor_keyword"]:,}편. 원본 adult/video {prep["adult_or_video"]}편은 분류에 보존했다.', '',
           f'상위 특징 미지원 {invariance["top_unsupported"]:,}편, 모든 콘텐츠 없음 {invariance["no_content"]:,}편, 약한 근거 {invariance["weak_evidence"]:,}편. MATCHED/서비스전용/모호매핑 분모 {prep["mapping_status"]}. 모호한35편의 MovieLens 연결은 확정하지 않았다.', '',
           '## 사용자·예상 별점·시점', '',table(mse),'', 'ALS-item은 영화 factor 가용 여부다. 해당 사용자의 지원 입력이0이면 factor가 있는 영화도 fallback으로 예측한다. RMSE_user_MSE는 사용자별 MSE를 평균한 뒤 제곱근을 취한 값이다.', '',table(cohort),'',
           '입력 제한0/1/5/10/30 진단:', '',table(summary[summary.policy.isin([selection['group']['id'],selection['baseline']['id']])][['policy','cap','users','positive_users','ndcg10','recall10','underseen_share','candidate_count','latency_p95_ms']]),'',
           f'훈련은 {audit["train_rows"]:,}행, {audit["train_users"]:,}명이고 모두2023-01-01 이전이다. 기존 ALS32를 원본 해시와 train ID로 검증했다.270명의 과거 개발 사용자를90/180으로 나눴다. 전체 원본으로 확장한 viewed 목록은 {int(loss.extra_viewed.sum()):,}건의 추가 서비스 영화 이력을 찾았다. 기존 context의 매핑 미지원 target 합계 {int(loss.unmapped_targets.sum()):,}건은 제외 및 기록했다. 관측 정답 범위 자체는 기존 카탈로그가 선택한 개발 slice다.', '',
           '사용자 벡터와 그룹 대표는 같은 고정 콘텐츠 공간이며, 최종 ALS는 별도32차원 공간이다. ALS 미지원 영화는 train Bayesian 평균+사용자 bias+콘텐츠 ridge로 예상별점을 낸다. cap0/1/5/10/30은 기존 평가 입력을 제한한 진단이고, cap0에서도 알려진 시청 영화는 제외한다. 실제 무이력과 구분한 표를 함께 남겼다.', '',
           f'공통 과거 적격 후보는 {eligibility["historical_snapshot_assisted"]:,}편으로 현재 콘텐츠와 공개 날짜/상태를 사용한 snapshot-assisted 비교다. 현재 TMDb 투표·인기도는 순위 점수에 쓰지 않았다.2026 서비스 전용151,601편에는 사용자별 정답이 없어 추천 품질을 검증했다고 할 수 없다.', '',
           '서비스 전용 영화는 이 스냅샷에서2023년 이후 개봉151,597편과 날짜 없음4편이므로 과거 적격 후보에는0편이다. 실제 과거 후보는 MATCHED84,029편과 모호매핑34편이며 모호매핑은 정답 연결에 사용하지 않았다. 신규 영화의 실행 가능성과 과거 오프라인 품질 검증 범위를 구분한다.', '',
           '## 현재 스냅샷 공급 진단', '',table(supply_rows),'',
           '현재 공급 진단의 UNKNOWN/노출/시간은 관측 만족도 우열이 아니다. 시간은 초기화·파일읽기·네트워크를 제외한 CPU warm component latency이며, 후보 생성과 실제 제한 후보 별점 계산을 포함한다. 인기 기준선에는 불필요한 예측 계산을 넣지 않았다.', '',
           '## 고정 배정·실행 산출물', '',
           f'버전 `{manifest["version"]}`, bundle SHA256 `{manifest["bundle"]["sha256"]}`. 어휘/IDF/SVD/가중치/상위·하위 중심/이름/동점·결측 규칙과 런타임을 함께 고정했다.', '',
           '[전체 배정 Parquet](../../../../outputs/fixed-k8-discovery/final/assignments.parquet) · [CSV.gz](../../../../outputs/fixed-k8-discovery/final/assignments.csv.gz) · [모델 명세](../../../../outputs/fixed-k8-discovery/final/manifest.json) · [8맛과 모든 하위 그룹 설명](KOREAN-GUIDE.md).', '',
           '[한국에서 익숙한 영화의 검증된 ID별 설명](KOREAN-EXAMPLES.md)에는 기생충·곡성·살인의 추억·겨울왕국 등의 실제 배정과 시리즈/동명 영화 반례를 함께 남겼다.', '',
           '추가·삭제·전체 역순·37분할·저장/재로드·OOV 입력 검사가 동일 배정을 확인했다. 신규 영화는 `k8_assign.py`의 transform/assign만 호출한다. 기존 입력 내용이 바뀌면 배정이 달라질 수 있으며, 카탈로그 회원 추가/삭제만으로는 달라지지 않는다. 모델 버전 변경과 단순 catalog 업데이트를 구분한다.', '',
           '코드/설계/검토 기록은 이 worktree에만 있고 대용량 모델과 개인별 결과는 ignored outputs에 있다. 기존 저장소 미커밋 파일, 팀 API/DB, Jira/GitLab/Notion은 변경하지 않았다. 커밋·Push·배포는 수행하지 않았다.', '']
    (DOC/'RESULT.md').write_text('\n'.join(lines),encoding='utf-8')
    consumed=['prepare/report.json','train/content-selection.json','train/child-search.json','recommend/selection.json','recommend/decision.json',
              'final/invariance.json','final/manifest.json','recommend/eligibility.json','final/descriptors.json','final/assignments.parquet',
              'recommend/validation-curves.csv','recommend/verification-per-user.parquet','recommend/rating-predictions.parquet',
              'recommend/deployment-supply.parquet','recommend/interaction-source-audit.json','recommend/verification-summary.json']
    write(DOC/'report-inputs.json',{'script':pin(Path(__file__)),'history_diagnostic':pin(DOC/'history-stratified-diagnostic.json'),'files':{p:pin(OUT/p) for p in consumed}})
    print(str(DOC/'RESULT.md'))

if __name__=='__main__':main()
