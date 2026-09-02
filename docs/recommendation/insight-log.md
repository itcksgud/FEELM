# 추천 통찰 기록

> 상태: `APPROVED` — 실험 전에는 결과를 채우지 않는다.

검증하지 않은 아이디어를 성과처럼 기록하지 않는다. 각 통찰은 연결된 `run_id`와 artifact가 있어야
한다.

## 기록 Template

### INSIGHT-YYYYMMDD-NNN — 제목

- 상태: `OBSERVED | REPRODUCED | ADOPTED | REJECTED`
- 관련 run: `run_id`
- 비교 기준선: `baseline run_id`
- 관찰:
- 영향 구간:
- 해석:
- 악화된 지표·비용:
- 반례·한계:
- 결정:
- 후속 실험:
- 관련 model version:

## 현재 검증된 통찰

### INSIGHT-20260829-001 — 공통 4점 threshold는 사용자마다 다른 양성 비율을 만든다

- 상태: `OBSERVED`
- 관련 run: `REC-EV-001 / global-time-v1`
- 비교 기준선: 없음 — 데이터 성향 profile
- 관찰: Train rating mean 하위 quartile의 사용자별 raw 4+ 비율 중앙값은 33.3%, 상위 quartile은
  79.8%였다. P10~P90 범위도 각각 16.6%~46.8%, 69.7%~92.4%로 크게 달랐다.
- 영향 구간: 전체 Train 사용자 170,491명. raw 4+가 한 번도 없는 사용자 230명, 모두 raw 4+인
  사용자 1,015명.
- 해석: 영화 선택 구성과 점수 사용 경향이 사용자마다 달라 공통 `rating >= 4` label이 같은 의미를
  갖지 않는다.
- 악화된 지표·비용: 아직 모델을 실행하지 않아 없음.
- 반례·한계: 평균 차이가 순수한 rating 습관만을 뜻하지 않고 각 사용자가 본 영화 구성도 섞여
  있다. 추천 모델 개선을 증명한 결과가 아니다.
- 결정: raw 4+는 민감도 분석으로만 남기고 사용자 Train ECDF 기반 상대 효용을 주 평가에 사용한다.
- 후속 실험: REC-EV-002에서 raw threshold와 상대 효용 기준이 모델 비교 순위를 바꾸는지 확인.
- 관련 model version: 없음.

### INSIGHT-20260829-002 — 전역 미래 평가는 cold-start fallback이 지배한다

- 상태: `OBSERVED`
- 관련 run: `REC-EV-001 / global-time-v1`
- 비교 기준선: 없음 — split 진단
- 관찰: Train에 존재한 사용자와 영화가 함께 있는 row coverage는 Validation 15.03%, Test
  9.78%였다.
- 영향 구간: Validation 3,200,020행, Test 3,200,021행.
- 해석: 전역 시간 분할은 실제 미래의 신규 사용자·영화 대응을 평가하는 데 중요하지만, 이 결과
  하나로 warm 개인화 모델의 순수 능력을 판단하면 fallback 성능이 모델 성능을 가린다.
- 악화된 지표·비용: warm-only 진단을 추가해야 하므로 평가 파이프라인이 하나 더 필요하다.
- 반례·한계: MovieLens 사용자 ID의 시간적 유입 구조가 실제 FEELM 가입 패턴과 같다고 볼 수 없다.
- 결정: 전역 split을 대표 제품 평가로 유지하고 사용자별 시간 split warm diagnostic을 별도 보고.
- 후속 실험: REC-EV-002에서 global/fallback 포함 결과와 warm-user 결과를 나란히 산출.
- 관련 model version: 없음.

### INSIGHT-20260829-003 — ALS 예상 별점 개선과 서비스 coverage는 분리해야 한다

- 상태: `REPRODUCED`
- 관련 run: `EXP-20260829-001 / REC-EV-002`
- 비교 기준선: regularized Bias + isotonic
- 관찰: Validation 뒤 구간에서 보정 ALS의 warm MAE는 0.6268이었고 보정 Bias의 warm MAE는
  0.6530이었다. 그러나 ALS 직접 예측은 전체 1,600,010행 중 187,799행, 11.74%만 가능했다.
  Bias fallback을 합친 보정 전체 MAE는 0.7345였다.
- 영향 구간: Train에 사용자·영화가 모두 존재하는 warm 행과 신규 identity 상태.
- 해석: ALS는 과거가 있는 사용자·영화 조합의 별점 추정에는 가치가 있지만, 전역 미래 서비스의
  대부분을 담당하는 모델이라고 표현할 수 없다.
- 악화된 지표·비용: 25,600,163행 전체 ALS 학습 91.27초, fallback 계약과 별도 coverage 보고 필요.
- 반례·한계: rank 32, regParam 0.1, seed 42 한 조합이며 grid와 다중 seed 비교 전이다.
- 결정: ALS를 champion으로 채택하지 않고 Bias를 fallback 기준선으로 유지한다.
- 후속 실험: REC-EV-003 K0~K20 Fold-in/content fallback 곡선.
- 관련 model version: `als-r32-reg010-i10-s42-exp-20260829-001`.

### INSIGHT-20260829-004 — 첫 ALS는 sampled ranking에서 Popularity를 이기지 못했다

- 상태: `OBSERVED`
- 관련 run: `EXP-20260829-001 / REC-EV-002`
- 비교 기준선: Bayesian Popularity
- 관찰: 동일한 positive 1개+uniform unseen 99개 후보에서 NDCG@10은 Popularity 0.4727, Bias
  0.4106, ALS 0.2595였다. ALS의 median positive rank는 9위, Popularity는 4위였다.
- 영향 구간: warm Validation-eval에서 relative utility 0.7 이상 평가가 있는 3,553명.
- 해석: 관측된 held-out 영화가 uniform 미평가 영화보다 인기 있을 가능성이 커 Popularity가 강한
  프로토콜이며, 현재 ALS 점수도 이 기준선을 넘지 못한다.
- 악화된 지표·비용: sampled 후보는 full-catalog와 순위가 뒤집힐 수 있어 최종 채택 지표로 사용할
  수 없다.
- 반례·한계: 미평가는 부정 정답이 아니며 후보가 uniform sample이다. 실제 노출 로그가 없다.
- 결정: 현재 ALS를 개인 순위 모델로 채택하지 않는다. 결과를 숨기지 않고 강한 기준선으로 유지한다.
- 후속 실험: full-catalog known-at-cutoff 평가와 ALS grid, ItemKNN/Hybrid 비교.
- 관련 model version: `als-r32-reg010-i10-s42-exp-20260829-001`.

### INSIGHT-20260829-005 — Isotonic은 calibration을 개선하지만 MAE를 항상 개선하지 않는다

- 상태: `OBSERVED`
- 관련 run: `EXP-20260829-001 / REC-EV-002`
- 비교 기준선: 각 모델의 raw prediction
- 관찰: warm ALS는 MAE 0.6351→0.6268, ECE 0.0939→0.0204로 함께 개선됐다. 전체 Bias는
  ECE 0.0534→0.0185로 개선됐지만 MAE는 0.7339→0.7379로 소폭 악화됐다.
- 영향 구간: Validation 앞 절반에서 보정기를 학습하고 뒤 절반에서 평가한 모든 행과 warm 행.
- 해석: 평균 보정이 좋아지는 것과 개별 별점 절대오차가 줄어드는 것은 같은 목표가 아니다.
- 악화된 지표·비용: Bias MAE +0.0040, 별도 calibrator version과 threshold 저장 필요.
- 반례·한계: isotonic 한 방식만 비교했고 사용자 구간별 보정기는 아직 검증하지 않았다.
- 결정: 보정 모델은 ECE와 MAE를 함께 보고 선택하며 숫자 UI 노출 근거로 ECE만 사용하지 않는다.
- 후속 실험: REC-EV-003의 이력량별 오차와 confidence coverage.
- 관련 model version: `bias-fallback-exp-20260829-001`,
  `als-r32-reg010-i10-s42-exp-20260829-001`.

### INSIGHT-20260829-006 — Cold-start 평가는 대상 사용자를 item factor 학습에서도 빼야 한다

- 상태: `REPRODUCED`
- 관련 run: `EXP-20260829-002 / REC-EV-003`
- 비교 기준선: 전체 Train item factor를 재사용하는 단순 K 절단
- 관찰: 평가 사용자 3,014명의 Train 1,954,040행을 ALS·Bias에서 제외하고 남은 23,646,123행으로
  item factor를 재학습했다. 각 사용자의 최초 20개만 별도 artifact로 고정했다.
- 영향 구간: Validation 앞·뒤 warm rating이 있고 Train 이력 20개 이상인 사용자.
- 해석: 대상 사용자의 숨긴 K+1 이후 평점이 item factor에 남으면 신규 사용자 성능을 낙관한다.
- 악화된 지표·비용: ALS 재학습 146.34초, 활동 사용자 cohort 선택 편향.
- 반례·한계: 실제 FEELM 신규 사용자와 MovieLens 활동 사용자의 유입 조건은 다르다.
- 결정: cold-start/Fold-in 채택 실험은 cohort-excluded factor만 유효한 근거로 인정한다.
- 후속 실험: 콘텐츠 fallback도 동일 cohort로 평가한다.
- 관련 model version: `cold-foldin-standalone-exp-20260829-002`.

### INSIGHT-20260829-007 — K1 통계적 개선과 K10 실질적 개선은 다른 결정이다

- 상태: `REPRODUCED`
- 관련 run: `EXP-20260829-003 / REC-EV-003B`
- 비교 기준선: K0 Bias 예상 별점
- 관찰: 선택 구간에서 정한 Bias/Fold-in α로 미래 구간을 평가하자 K1의 macro MAE 개선은 1.66%
  (`-0.0125`, 95% CI `[-0.0148,-0.0100]`)였다. K10은 3.95% 개선(`-0.0298`,
  `[-0.0348,-0.0244]`)으로 사전에 잠근 3% 실질 개선 Gate를 처음 통과했다.
- 영향 구간: cohort 3,014명, 미래 평가 167,194행.
- 해석: 표본이 크면 작은 차이도 유의해지므로 입력 부담을 정당화하려면 실질적 최소 개선이 필요하다.
- 악화된 지표·비용: K10은 K1보다 9개 입력이 더 필요하며 실제 이탈 비용은 미측정이다.
- 반례·한계: 3%는 Test 전 기술 Gate이며 제품 UX 가치 자체를 뜻하지 않는다.
- 결정: K1은 통계적 관찰, K10은 예상 별점 React 비교 후보로 구분한다.
- 후속 실험: K5/K10 화면 조작 수·완료 시간 비교.
- 관련 model version: `cold-dual-head-blend-exp-20260829-003`.

### INSIGHT-20260829-008 — 개인화 입력이 있어도 추천 순위를 바꾸지 않는 것이 최선일 수 있다

- 상태: `REPRODUCED`
- 관련 run: `EXP-20260829-003 / REC-EV-003B`
- 비교 기준선: K0 Bayesian Popularity sampled ranking
- 관찰: 사용자 절반에서 선택한 `Popularity ↔ Fold-in` α는 K1/K3/K5/K10/K20 모두 0이었다.
  선택에 쓰지 않은 1,323명에서도 α=0 NDCG@10 0.4571을 그대로 유지했다.
- 영향 구간: relative utility 0.7 이상 미래 positive가 있는 cold-start cohort 2,553명.
- 해석: rating prediction과 Top-N ranking은 다른 문제다. 입력을 받았다는 사실이 ALS 순위 반영의
  성능 근거가 아니다.
- 악화된 지표·비용: 현재 개인 입력은 예상 별점에만 기여하며 추천 순위 차별화는 아직 없다.
- 반례·한계: sampled 후보이고 full-catalog·콘텐츠 특징을 쓰지 않았다.
- 결정: Fold-in 순위 가중치는 0으로 유지하고 콘텐츠 Hybrid 전까지 개인 순위 개선을 주장하지 않는다.
- 후속 실험: REC-EV-004 Hybrid·탐험 Pareto와 full-catalog ranking.
- 관련 model version: `cold-dual-head-blend-exp-20260829-003`.

### INSIGHT-20260829-009 — 숫자 범위 정렬은 product calibration 근거가 아니다

- 상태: `REPRODUCED`
- 관련 run: `REC-EV-003C / rating-scale-alignment-v1`
- 비교 기준선: REC-EV-003B selected star predictions 원척도
- 관찰: 동일 held-out 167,194행에서 actual의 45.17%가 half-star였고 1.83%는 C1 minimum 1보다
  낮았다. K10 round는 원래 MovieLens label 기준 ECE를 0.0260에서 0.2165로 악화시켰다. affine은
  단조·가역이고 범위를 맞추지만 interior anchor를 이동시키며 paired C1 label이 없었다.
- 영향 구간: REC-EV-003B cohort 3,014명의 미래 평가, K1/K3/K5/K10/K20.
- 해석: source와 target endpoint를 맞추는 수학적 변환은 실제 C1 integer Rating 의미와 calibration을
  학습한 것이 아니다. 변환된 양쪽 label에서 오차 단위가 작아지는 것도 모델 개선이 아니다.
- 악화된 지표·비용: C1 prediction-before-rating paired artifact와 별도 시간 split이 필요하다.
- 반례·한계: 실제 C1 outcome이 아직 없어 affine 또는 C1-label recalibration의 온라인 적합성을 비교하지 못했다.
- 결정: clamp/round를 기각하고 affine을 보류한다. DN-C2-008은 fail-closed이며 Popularity만 유지한다.
- 후속 실험: `export-product-scale-validation`으로 CALIBRATION/VALIDATION paired artifact를 만든 뒤
  versioned affine과 C1-label recalibration을 같은 held-out C1 row에서 비교한다.
- 관련 model version: `cold-dual-head-blend-exp-20260829-003` candidate, champion 아님.

### INSIGHT-20260829-010 — 실제 C2A HTTP와 Fold-in 코어 latency는 같은 경로가 아니다

- 상태: `REPRODUCED`
- 관련 run: `EXP-20260829-004 / REC-EV-007`
- 비교 기준선: `rec-ev-007-v1` 사전 local engineering Gate
- 관찰: 실제 Uvicorn Popularity-only HTTP 후보≤100 worst p95는 4.1012 ms, 후보 1000은
  30.6875 ms였다. 별도 비활성 Fold-in+score 코어의 worst p95는 2.1281 ms였다.
- 영향 구간: synthetic service UUID 후보 10/100/1000, K0/1/3/5/10/20, 동시성 1/4/8.
- 해석: 현재 HTTP 성공 경로는 ranking alpha 0이라 Rating K별 Fold-in 계산을 수행하지 않는다.
  Fold-in 코어가 빠르다는 관찰을 개인화 순위나 expected-star 품질로 확대하면 안 된다.
- 악화된 지표·비용: 없음. 다만 운영 hop과 resource contention을 포함한 재실행 비용이 남았다.
- 반례·한계: 단일 개발 PC loopback이며 실제 Catalog·사용자 traffic, Spring·DB·TLS가 없다.
- 결정: 사전 규칙으로 timeout 750 ms와 healthy freshness 3000 ms를 local 후보로 선택하되 운영
  SLA 채택은 배포 topology 검증 뒤로 미룬다. stale success와 expected-star는 계속 비활성화한다.
- 후속 실험: Spring client, DB snapshot read, outbox 지연, container limit, remote hop을 포함한 부하 시험.
- 관련 model version: Popularity-only serving `rec-ev-007-v1`; Fold-in은 비활성 진단.

### INSIGHT-20260829-011 — 합성 Balanced는 Average보다 낫다는 held-out 근거가 없었다

- 상태: `OBSERVED`
- 관련 run: `EXP-20260829-005 / REC-EV-005`
- 비교 기준선: Average party aggregation
- 관찰: Validation에서 선택한 Balanced의 held-out Test 차이는 평균 효용 -0.0013(95% CI
  [-0.0037, 0.0007]), 최저 효용 +0.0005([-0.0035, 0.0045]), 격차 -0.0042
  ([-0.0116, 0.0024])였고 세 CI가 모두 0을 포함했다.
- 영향 구간: 2/3/4명 × 유사/중간/상이 취향 각 30개, 총 270개 합성 Test party.
- 해석: 설명 가능한 penalty 조합을 Validation에서 선택할 수는 있지만 Test에서 Average 대비 개선을
  입증하지 못했다. 비교 후보라는 상태와 제품 채택은 구분해야 한다.
- 악화된 지표·비용: Balanced의 평균 효용은 점 추정상 -0.0013, predicted relevance loss는 0.0017이었다.
- 반례·한계: 모든 구성원이 평가한 후보만 사용해 4인 Test 평가 가능 coverage가 0.69%~1.02%였고,
  MovieLens에는 실제 파티 선택·공동 감상·만족 데이터가 없다.
- 결정: `party_aggregation` champion을 null로 유지하고 PARTY_BALANCED_V1·공개 API·UI를 승인하지 않는다.
- 후속 실험: 실제 FEELM party 노출·선택·구성원별 사후 평가 로그 또는 관측 편향을 줄인 후보 평가.
- 관련 model version: `party-balanced-offline-exp-20260829-005` 비교 후보, champion 아님.

### INSIGHT-20260830-012 — Validation 1% 탐험 손실 후보가 Test 1%를 지키지 못했다

- 상태: `OBSERVED`
- 관련 run: `REC-EV-004 / rec-ev-004-sampled-exploration-pareto-v1`
- 비교 기준선: Bayesian Popularity sampled ranking
- 관찰: Validation에서 NDCG 손실 0.69%로 1% candidate budget 안에 있던 `EXPLORE_05_ON_POPULARITY`는
  held-out Test에서 NDCG@10이 0.3779에서 0.3719로 낮아져 상대 손실 1.59%였다. paired 차이의
  95% CI도 `[-0.0083,-0.0038]`이었다.
- 영향 구간: Validation 6,020명, Test 4,000명, 사용자별 1 positive + 199 negatives sampled 후보.
- 해석: Validation의 작은 relevance 손실은 Test에서 같은 budget을 보장하지 않는다.
- 악화된 지표·비용: Test NDCG -1.59%; novelty·diversity·coverage·long-tail은 개선됐다.
- 반례·한계: sampled 후보이므로 full-catalog에서 우열과 손실이 달라질 수 있다.
- 결정: 3% budget으로 사후 변경하지 않고 exploration champion·weight·2+1 구성을 미승인으로 유지한다.
- 후속 실험: 같은 locked 후보의 full-catalog 평가와 제품 소유자 loss-budget 결정.
- 관련 model version: 없음. 모두 validation 후보이며 champion 아님.

### INSIGHT-20260830-013 — 점수 기여가 있어도 순위를 바꾸지 않으면 faithful reason이 아니다

- 상태: `OBSERVED`
- 관련 run: `REC-EV-006 / rec-ev-006-score-contribution-ablation-v1`
- 비교 기준선: REC-EV-004 선택 후보의 actual Test scoring contribution
- 관찰: 40,000개 추천 위치에서 novelty contribution은 모두 양수였지만 제거 ablation에서 위치 효과가
  있었던 비율은 24.31%였다. diversity는 양의 contribution 78.86%, 위치 효과 59.98%였다.
- 영향 구간: REC-EV-004 sampled Test 4,000명 × Top-10.
- 해석: feature 존재·양의 contribution만으로 설명을 표시하면 실제 순위 결정과 무관한 이유가 섞인다.
- 악화된 지표·비용: strict rank-effect Gate는 reason 후보 coverage를 낮춘다.
- 반례·한계: 단일-feature ablation은 feature 상호작용의 인과 설명이나 사용자 이해도를 증명하지 않는다.
- 결정: active feature, 양의 contribution, rank effect, provenance version을 모두 요구하고 나머지는
  typed `BLOCKED`로 둔다. `EMITTABLE_CANDIDATE`도 UI 승인이 아니다.
- 후속 실험: REC-EV-008에서 문구·표시 개수·이해도를 별도 비교한다.
- 관련 model version: 없음. reason UI와 ranking champion 모두 미승인.

### INSIGHT-20260830-014 — positive 비주입 full-catalog에서는 sampled relevance를 그대로 읽을 수 없다

- 상태: `OBSERVED`
- 관련 run: `REC-EV-004B / rec-ev-004b-full-catalog-v1`
- 비교 기준선: 50,977개 Train-known 전체를 scan한 Bayesian Popularity Top-500/Top-10
- 관찰: held-out Test 4,000명에서 Popularity candidate recall@500은 0.3080, NDCG@10은 0.009382였다.
  genre 미상 zero-vector를 diversity로 보상하지 않은 Explore 5%는 같은 candidate recall에서
  NDCG@10 0.005113이었고 paired 차이 95% CI는 `[-0.006604, -0.002002]`였다. Explore의
  list/pair genre coverage는 0.90185/0.803722였다. Validation/Test 총 510,789,540 score
  evaluation을 수행했다.
- 영향 구간: `global-time-v1` warm Validation 6,020명과 Test 4,000명, Train-known 50,977편.
- 해석: sampled 후보의 positive 포함 NDCG를 full-catalog relevance로 확대할 수 없다. candidate
  generation recall과 Top-10 ranking relevance를 분리해서 봐야 한다.
- 악화된 지표·비용: full scan은 Validation 51.547초/Test 35.651초, observed peak RSS 약 1.77GB였다.
- 반례·한계: MovieLens Train-known 범위이며 서비스 Catalog, 온라인 반응, production latency를 대표하지 않는다.
- 결정: Popularity fallback을 바꾸지 않고 exploration weight·2+1·개인 ranking champion을 미승인으로 유지한다.
- 후속 실험: 서비스 Catalog identity/availability 후보와 실제 사용자 outcome이 준비된 뒤 동일 Gate를 재검증한다.
- 관련 model version: 없음. 네 정책 모두 offline 비교 후보이며 champion 아님.

### INSIGHT-20260830-015 — cold Fold-in은 full-catalog K10에서만 명확한 offline 후보였다

- 상태: `OBSERVED`
- 관련 run: `REC-EV-011 / rec-ev-011-cold-foldin-full-catalog-v1`
- 비교 기준선: REC-EV-002 Train-known 50,977편 Popularity, first-K seen 제외
- 관찰: selection이 K1/3/5/10/20 alpha를 0.2/0.2/0.1/0.2/0.3으로 잠갔다. evaluation의
  paired NDCG CI는 K10만 `[0.000253,0.002783]`로 명확히 0 위였고 K5는 하한 0.000016의 경계였다.
- 해석: sampled REC-EV-003B의 전 K alpha 0과 달리 full-catalog에서는 K10 후보가 생겼지만 effect가 작다.
- 반례·한계: 첫 임시 42,299편 run은 universe 계약 위반으로 폐기했다. 수정된 50,977편 protocol은
  runtime 제외 selection/evaluation canonical hash가 독립 두 실행에서 각각 일치했다.
- 결정: K10을 후속 offline 후보로만 두며 champion·expected-star·공개 UI는 승인하지 않는다.
- 후속 실험: 서비스 Catalog mapping/availability 후보와 implicit-feedback ALS retraining 비교.
- 관련 model version: cohort-excluded REC-EV-002 ALS configuration; champion 아님.

### INSIGHT-20260830-016 — REC-EV-012 조건은 성립하지 않았다

- 상태: `REPRODUCED`
- 관련 run: `REC-EV-011` 후속 Gate
- 관찰: REC-EV-012는 REC-EV-011이 전 K alpha 0일 때만 재학습 없는 factor-similarity 후보를 여는
  조건부 실험이었으나 K10 alpha 0.2 offline candidate가 잠겼다.
- 결정: REC-EV-012를 실행하지 않고 `SKIPPED_BY_PREDECLARED_GATE`로 기록한다. 숨은 결과나 champion은 없다.
- 후속 실험: constrained 2+1은 REC-EV-011 K10 base를 직접 사용한다.

### INSIGHT-20260830-017 — 한 자리만 바꿔도 sparse NDCG@3 relevance budget을 지키지 못했다

- 상태: `OBSERVED`
- 관련 run: `REC-EV-013 / rec-ev-013-constrained-two-plus-one-v1`
- 비교 기준선: REC-EV-011 K10 alpha 0.2 Top-3
- 관찰: selection의 최소 손실 후보도 NDCG@3가 0.002846에서 0.002033으로 28.57% 낮아져
  1%/3%/5% budget을 모두 실패했다. evaluation 진단 후보 차이는 -0.001134, CI
  `[-0.002646,0.0]`였다.
- 해석: 상위 2개 고정은 전체 rerank보다 좁지만 단일 positive sparse NDCG@3에서는 세 번째 교체도
  relevance 손실이 크다. diversity·novelty 개선만으로 정책을 열 수 없다.
- 결정: `two_plus_one`과 `discovery_policy`를 null로 유지한다. product/public 승인은 없다.
- 반례·한계: MovieLens Train-known full catalog의 단일 held-out positive이며 온라인 만족이 아니다.

### INSIGHT-20260830-018 — 연속 예상 별점을 이산 평점 ECDF에 바로 넣으면 경계 편향이 생긴다

- 상태: `ADOPTED_LOCAL_EXPERIMENT_ONLY`
- 관련 run: `EXP-20260830-006 / REC-EV-015`
- 비교 기준선: `C6_RIGHT_INCLUSIVE_RAW_ECDF_V1`
- 관찰: 숨겨진 Validation tail 167,194건·3,014명에서 quantized-midrank v2의 K10 utility
  MAE는 0.191921→0.147411(-23.19%), 절대 bias는 53.12% 감소했고 Spearman은
  0.498314→0.581074로 늘었다. K1/3/5/10/20과 평점 평균 4분위 모든 구간이 사전 Gate를 통과했다.
- 해석: 3.99 예측은 실제 4.0과 가깝지만 right-inclusive ECDF에서 4.0 동점 전체를 놓친다.
  평점 격자로 정렬하고 동점 중간을 쓰면 이 불연속을 줄일 수 있다.
- 악화된 지표·비용: 계산량은 동일 O(n)이며 사전 검증 지표의 회귀는 없었다.
- 반례·한계: MovieLens 0.5 격자 결과이며 C1 정수 척도 제품 calibration·사용자 만족도·
  표현 이해도를 증명하지 않는다.
- 결정: `C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2`를 C6 local experiment에만 채택하고
  `displayEligible=false`와 C2B `NOT_COMPUTED`를 유지한다.
- 후속 실험: C1 운영 데이터를 수집하지 않는 현 범위에서는 추가 제품 채택 주장을 하지 않는다.
- 관련 model version: `c6-discrete-quantized-midrank-ecdf-v2`.

### INSIGHT-20260830-019 — 전체 평균 개선은 동일 사용자의 개선을 보장하지 않는다

- 상태: `OBSERVED`
- 관련 run: `REC-EV-016 / rec-ev-016-user-case-a-v1`
- 비교 기준선: 동일한 비식별 MovieLens 사용자 A의 full-catalog Popularity
- 관찰: 결과를 보기 전에 고정 해시로 선택한 사용자 A에서 장르 Content·ALS·Explore Top-10은
  Popularity와 0/10, Hybrid는 1/10만 겹쳤다. Content는 같은 4개 장르 조합 10편으로 수렴했고,
  raw ALS는 평균 novelty 23.554 bits인 희귀·메타데이터 미상 영화와 5점 범위를 넘는 내적 점수로
  상단을 채웠다. K10 Fold-in은 2편을 교체했으나 cold held-out 순위는 3,979→5,363위로 악화됐다.
- 영향 구간: warm Test와 leakage-safe cold-start evaluation 교집합 1,011명 중 사전 고정 1명,
  Train-known 50,977편 positive 비주입 후보.
- 해석: 작은 weight도 Top-10 구성은 크게 바꿀 수 있고, aggregate의 작은 양의 평균 효과 안에는
  개인별 악화가 존재한다. 개인 사례는 알고리즘 행동과 실패 형태를 설명하지만 채택 통계는 아니다.
- 악화된 지표·비용: 이 사례의 모든 warm 정책은 자연 발생 held-out Top-10을 놓쳤다. K10 Fold-in도
  해당 개인의 정답 순위를 1,384계단 악화시켰다.
- 반례·한계: 한 명의 결과이며, 선택 편향을 막았어도 모집단 효과를 추정하지 않는다. 미평가 영화는
  부정 정답이 아니고 held-out 한 편은 전체 만족도가 아니다.
- 결정: Popularity fallback과 champion null을 유지한다. 사례 결과로 정책을 채택하지 않고,
  REC-EV-004B/011 aggregate·paired CI와 함께만 해석한다.
- 후속 실험: 다음 모델 후보도 aggregate 평가와 별도로 동일 고정 사용자 A의 제목 단위 diff를 자동 생성한다.
- 관련 model version: REC-EV-002 warm ALS, REC-EV-004B 정책, REC-EV-011 K10 candidate; 모두 champion 아님.

### INSIGHT-20260830-020 — 자유 태그 Hybrid의 전체 개선은 head 구간이 만들었다

- 상태: `OBSERVED`
- 관련 run: `REC-EV-017 / rec-ev-017-relational-tag-ablation-v1`
- 비교 기준선: Bayesian Popularity, Train-known 50,977편 full-catalog
- 관찰: Validation에서 선택한 Tag alpha 0.1은 Test 4,000명에서 NDCG@10 0.009382→0.016769,
  candidate recall@500 0.3080→0.3275였고 paired 차이 +0.007386의 95% CI는
  `[0.003948,0.010696]`였다. 그러나 P2 영화 인기도 구간 차이는 -0.011484,
  CI `[-0.017592,-0.006182]`로 명확히 회귀했고 P1 long-tail NDCG는 두 정책 모두 0이었다.
  P4 head 차이 +0.033664가 전체 향상을 주도했다.
- 영향 구간: global-time-v1 warm Test 4,000명. 평가 사용자 결과로 tag feature가 오염되지 않도록
  Validation·Test에 등장한 38,272명의 태그 기여를 모두 제외했다.
- 해석: 자유 태그는 장르보다 풍부한 의미 신호를 주지만, tag coverage와 기여가 인기작에 집중돼
  전체 평균 향상이 새로운 영화 발견이나 전 구간 개인화 개선을 뜻하지 않는다.
- 악화된 지표·비용: P2 회귀, P1 무효과. Train-known 50,977편 중 tag vector가 있는 영화는
  9,857편뿐이다.
- 반례·한계: 자유 태그는 Tag Genome이 아니며 contributor bias가 남는다. 미평가는 부정 label이
  아니고 MovieLens Test는 실제 FEELM 노출 로그가 아니다.
- 결정: alpha 0.1을 일반 ranking 후보·champion으로 채택하지 않고 Popularity fallback을 유지한다.
  영화·장르 관계도 설명 후보일 뿐 공개 reason으로 승인하지 않는다.
- 후속 실험: 전수 Train-known TMDB 감독·배우·키워드·overview feature artifact를 만든 뒤
  동일 split에서 `genre → structured TMDB → text embedding → tags` ablation을 수행한다.
- 관련 model version: `rec-ev-017-tag-alpha-010`, rejected for generic ranking.

### INSIGHT-20260902-021 — 논리적 role 분리만으로 Locked Test 누출을 막을 수 없다

- 상태: `REPRODUCED`
- 관련 run: `REC-EV-019A / rec-ev-019c-validation-artifacts-v1`
- 비교 기준선: 역할 혼합 `binary-prefixes.parquet`, `evaluation-windows.parquet`
- 관찰: 사용자 role과 checksum은 정확했지만 Validation·Locked Test 행이 같은 물리 파일에 있었다.
  Validation 코드가 role filter를 잘못 쓰거나 늦게 적용하면 Test 정답을 읽을 수 있는 경계였다.
- 영향 구간: Router Train·Validation·Locked Test의 K0/K5/K10 prefix와 미래 평가 window.
- 해석: 데이터 누출 방지는 split 수식뿐 아니라 프로세스가 열 수 있는 파일의 allowlist까지 포함해야 한다.
- 악화된 지표·비용: 역할별 prefix·window 6개를 추가 저장하고 combined 파일과 동등성 검사가 필요해졌다.
- 반례·한계: 실제 모델이 Test 결과를 사용한 사건은 아니며, 모델 성능 결과도 아니다.
- 결정: combined 파일은 감사용으로만 남기고 019C Validation runner에는 역할별 Validation 파일 두 개만
  허용한다. Router·Locked Test·원본 Test·혼합 파일은 open 전에 거부한다.
- 후속 실험: 합성 preflight에서 금지 경로, 누락 feature fallback, checkpoint resume, hash 불일치를 검사한다.
- 관련 model version: 없음 — 실행 계약·artifact boundary 개선.

### INSIGHT-20260902-022 — 같은 BPR 이름도 negative 의미가 다르면 같은 실험이 아니다

- 상태: `REPRODUCED`
- 관련 run: `REC-EV-019C-LIGHTFM-LINUX-SMOKE`
- 비교 기준선: B8의 최초 `loss=[bpr, warp]` 계약
- 관찰: LightFM BPR/WARP는 positive-only implicit feedback에서 미관측 항목을 negative로 샘플링한다.
  이는 B4의 “실제 관측 LIKE > 실제 관측 DISLIKE pair” 및 FEELM의 “미평가는 UNKNOWN”과 다르다.
- 영향 구간: B8 LightFM hybrid의 Base 학습과 K5/K10 사용자 fold-in.
- 해석: 알고리즘 이름이 같아도 라이브러리의 interaction·negative sampling 의미를 확인하지 않으면 서로
  다른 학습 문제를 같은 모델 비교처럼 기록하게 된다.
- 악화된 지표·비용: 최초 B8 탐색안을 폐기하고 signed logistic, 별도 frozen-item fold-in, Linux wheel
  dependency lock과 smoke를 추가했다.
- 반례·한계: 3명×5편 합성 smoke는 실행 가능성만 보여 주며 실제 추천 품질을 증명하지 않는다.
- 결정: B8은 관측 LIKE/DISLIKE `+1/-1` logistic만 사용한다. sample weight는 0 이상 confidence로 제한하고
  BPR/WARP는 B8에서 금지한다. 고정 Linux 환경에서 9개 dependency 검사를 통과했다.
- 후속 실험: 실제 runner 구현·dry-run 검토 뒤에만 Validation 실행 승인 여부를 판단한다.
- 관련 model version: `lightfm-next==1.19.0`, champion 아님.
