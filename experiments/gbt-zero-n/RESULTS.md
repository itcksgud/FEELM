# S15P21E106-622 GBT 0~N validation 결과

실행일: 2026-09-19

상태: **증거 생성 완료 / DO_NOT_PROMOTE / FINAL_TEST 미개봉**

## 입력과 재현 정보

- run: `ten-percent-input-v7` → `ten-percent-prepared-v7` → `ten-percent-fits-v7`
- MovieLens 32M: `ratings.csv` SHA-256 `91159850...5261ee`, `movies.csv` `b37ca1ab...c5cc2`
- 역할별 사용자: TRAIN 15,000 / VALIDATION 3,000 / FINAL_TEST 2,000
- 관측 target: TRAIN 59,030 / VALIDATION 11,756 / FINAL_TEST 7,730
- 학습 행 199,195 / validation target 행 42,572 / validation 후보 행 4,093,512
- 전체 episode 279,625 / candidate 행 6,869,265 / nested group 78,516
- Spark 4.1.3, `local[4]`, Docker 12 GiB 제한, 기본 seed 622
- feature profile digest `6be809f0...b812`; runtime source bundle `df64a5aa...d34b`
- evaluator `GBT_ZERO_N_EVALUATOR_V4_ARBITRARY_N_AND_COHORTS`, SHA-256 `d042b884...93e0`
- FINAL_TEST seal `358f392e...f70`

시간 분할 때문에 영화 연도와 TRAIN 지원량에는 분포 이동이 있어 manifest 상태를
`DISTRIBUTION_SHIFT`로 유지한다. MovieLens는 목표 페르소나와 다른 약한 proxy이며,
`UNKNOWN_SAMPLED`를 비선호로 해석하지 않는다.

v6는 총 이력이 3, 5, 41처럼 고정 진단 지점 사이에 있을 때 실제 전체 N variant를 만들지 않았다.
v7은 모든 user-target에 전체 N을 정확히 하나 포함한다. v6와 그 이전 결과는 임의 0~N 계약의
채택 근거로 사용하지 않는다.

## 회귀 GBT 결과

| profile | 특징 수 | 사용자 macro MSE | 사용자 macro MAE | 관측 NDCG@10 | peak memory | fit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| movie_only | 23 | 0.94196 | 0.74662 | **0.19494** | 2.58 GiB | 16.21s |
| history_aggregate | 35 | **0.92334** | 0.73524 | 0.10163 | 2.07 GiB | 16.69s |
| response_relation | 42 | 0.92454 | **0.73488** | 0.11113 | 1.86 GiB | 16.08s |

`history_aggregate`는 movie-only보다 사용자 macro MSE가 0.01862 낮았고 bootstrap 95% CI는
[-0.02648, -0.01046]였다. 기본 seed에서는 모든 N 구간의 점추정이 같은 user-target의 N=0보다
좋아 validation 선택 게이트를 통과했다. `response_relation`은 일부 저이력 구간이 나빠 탈락했다.

반면 `history_aggregate`의 관측 NDCG@10은 movie-only보다 0.09331 낮다. 상위 10개 슬롯의
96.32%가 평가되지 않은 후보이므로 절대값을 강한 품질 근거로 쓸 수 없지만, 회귀 오차 개선이
추천 순위 개선으로 이어졌다고도 말할 수 없다.

## 실제 전체 이력 기준 LOW_HISTORY_COHORT

| 전체 N | 사용자 수 | history_aggregate 사용자 macro MSE |
| --- | ---: | ---: |
| 0 | 1,955 | 0.91373 |
| 1 | 2 | 0.71195 |
| 2 | 1 | 0.69765 |
| 3–4 | 2 | 0.68151 |
| 5–9 | 7 | 0.91154 |
| 10–19 | 27 | 1.01580 |
| 20–29 | 27 | 1.15791 |
| 30–49 | 36 | 1.02213 |
| 50+ | 943 | 0.99067 |

N=1과 N=2는 각각 2명과 1명뿐이라 저이력 사용자에 대한 일반화 근거가 부족하다. 이것은
진단 prefix N=1/2 표본과 구분되는 결과다. 표의 LOW_HISTORY_COHORT는 사용자의 전체 가용 이력이
실제로 해당 N인 경우만 집계한다.

## Seed 민감도

| seed | 사용자 macro MSE | 모든 N 구간 점추정 비열화 없음 |
| ---: | ---: | --- |
| 622 | 0.92334 | PASS |
| 1622 | 0.92731 | **FAIL** |
| 2622 | 0.92626 | PASS |

전체 MSE 범위는 0.92334~0.92731로 좁지만 seed 1622에서 N=1은 N=0 대비 +0.02710
(95% CI [+0.01093, +0.04378]), N=2는 +0.01728
(95% CI [+0.00064, +0.03373]) 악화됐다. “평가가 하나씩 늘 때 최소한 나빠지지 않는다”는 조건이
seed에 안정적이지 않다.

## Pairwise ranking 목적함수

같은 validation 후보 4,093,512행에 대해 user·prediction_at·N 내부의 서로 다른 관측 별점 쌍
442,582개로 GBT 분류기를 학습했다. 후보별 점수는 결정적으로 고른 anchor 3개와의 선호 확률
평균이다.

| 방식 | 관측 NDCG@10 | 관측 positive recall@10 | unknown top-10 비율 |
| --- | ---: | ---: | ---: |
| response_relation 회귀 | 0.11113 | 0.12072 | 0.96322 |
| pairwise rank | 0.04410 | 0.06137 | 0.97946 |

pairwise rank의 사용자 macro NDCG@10 차이는 -0.06703이고 bootstrap 95% CI는
[-0.07238, -0.06157]다. 모든 후보를 정확히 3개 anchor와 비교했다. 소규모 fixture에서 보였던
개선이 10% 실행에서 재현되지 않아 이 목적함수는
탈락시킨다. pairwise 점수는 평점 척도 예측이 아니므로 rating MSE를 계산하지 않았다.

## Legacy 감사와 판정

`GBT120_s339`는 230개 feature, 4,997,069개 학습 행, 120회 iteration, seed 339로 정상 완료된
기존 산출물임을 hash로 확인했다. 다만 cohort, RH230 feature, 행 생성, sample-weight 정책이 달라
v7과 성능 수치를 직접 비교하지 않는다. 12 GiB에 도달한 자원 예외도 기록에 남긴다.

`ten-percent-completion-v4/completion-report.json`의 증거 상태는 `COMPLETE`, 승격 판단은
`DO_NOT_PROMOTE`다. 이 승격 해석은 탐색 결과의 완료 판단이며 사전 등록된 확증 기준이 아니다.

- 기본 seed의 회귀 오차 개선은 확인했다.
- seed 1622에서 저이력 N=1/2 비열화 조건이 깨졌다.
- pairwise ranking 대안은 같은 후보에서 유의하게 악화됐다.
- 실제 전체 이력이 N=1/2인 validation 사용자가 너무 적다.
- 상위 10개 후보의 96% 이상이 미평가라 MovieLens 순위 근거가 약하다.

따라서 Jira 622는 “현재 GBT를 채택”하는 결과가 아니라, 재현 가능한 실험과 탈락 근거를 완성한
것으로 종료한다. FINAL_TEST는 열지 않는다. 다음 후보를 만들 경우 저이력 표본을 보강하고,
seed 안정성과 추천 순위를 동시에 만족한 뒤에만 625의 최종 비교로 넘긴다. title 우선순위와
TMDB/KOBIS 인기도는 MovieLens 학습 지표를 오염시키지 않도록 실제 사용자 평가 직전 별도
rerank/profile에서 검증한다.
