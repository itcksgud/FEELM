# S15P21E106-622 GBT 0~N validation 결과

실행일: 2026-09-19
상태: **validation 완료, FINAL_TEST 미개봉**

## 입력과 실행 환경

- run: `ten-percent-input-v6` → `ten-percent-prepared-v6` → `ten-percent-fits-v6`
- MovieLens 32M 동결 snapshot: `ratings.csv` SHA-256 `91159850...5261ee`,
  `movies.csv` SHA-256 `b37ca1ab...c5cc2`
- 역할별 사용자: TRAIN 15,000 / VALIDATION 3,000 / FINAL_TEST 2,000
- 관측 target: TRAIN 59,030 / VALIDATION 11,756 / FINAL_TEST 7,730
- 학습 행: 197,931, validation target 행: 42,239, validation 후보 행: 4,061,200
- Spark 4.1.3, `local[4]`, Docker 12 GiB 제한, seed 622
- target은 평점을 보지 않는 SHA-256 순서로 사용자당 최대 4개 선택했다.
- 후보와 target은 예측 시점 연도까지 개봉연도가 확인된 영화만 사용했다.
- `UNKNOWN_SAMPLED`는 학습 label로 사용하지 않았다.
- feature schema digest: `6be809f0...b812`; ordered names digest: `ecaebaf3...97e2`
- runtime source bundle digest: `eb4ede37...cc70`
- metric calculator: `GBT_ZERO_N_EVALUATOR_V3_NESTED_USER_BOOTSTRAP`, SHA-256 `89c61798...665b`
- final-test seal ID: `a6e3ea39...19f8`

시간 분할 때문에 영화 연도와 TRAIN 지원량에는 분포 이동이 있다. TRAIN–VALIDATION 평점 분포의
최대 차이는 1.35%p이고 positive 비율 차이는 1.44%p였다. 보고서 상태 `DISTRIBUTION_SHIFT`를
유지해 이 차이를 숨기지 않는다.

## Validation 결과

| profile | 특징 수 | 사용자 macro MSE | 사용자 macro MAE | 관측 NDCG@10 | peak memory | fit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| movie_only | 23 | 0.94835 | 0.75119 | **0.11801** | 2.17 GiB | 15.53s |
| history_aggregate | 35 | 0.92351 | 0.73518 | 0.07191 | 2.14 GiB | 16.36s |
| response_relation | 42 | **0.92317** | **0.73335** | 0.07064 | 2.35 GiB | 16.61s |

전체 사용자 macro MSE는 `response_relation`이 가장 낮지만 `history_aggregate`와의 차이는
0.00034다. 선택에는 같은 user-target의 N=0 예측과 각 N 예측을 짝지은 추가 게이트를 적용했다.
모든 N 구간이 N=0보다 나쁘지 않고 하나 이상의 구간이 실제로 개선돼야 한다. 이 게이트는
`history_aggregate`만 통과했다. `movie_only` 대비 사용자 macro MSE 차이는 -0.02484이고 사용자 단위
2,000회 bootstrap 95% CI는 [-0.03321, -0.01632]다.

반면 관측 NDCG@10은 `movie_only`가 가장 높다. 후보 상위 슬롯의 약 96%가 MovieLens에서
평가되지 않은 `UNKNOWN_SAMPLED`라서 이를 비선호로 간주할 수 없다. 따라서 이 순위 지표는
최종 모델 채택 근거가 아니며, 서비스 대상 사용자 평가가 필요하다.

## 같은 user-target의 N=0 대비 사용자 macro MSE 변화

음수일수록 이력을 추가했을 때 좋아진 것이다. 각 열의 N=0과 N>0은 같은 사용자와 target만
비교한다.

| N | history_aggregate 변화 [95% CI] | response_relation 변화 [95% CI] |
| --- | ---: | ---: |
| 1 | **-0.00506** [-0.02278, +0.01196] | +0.00785 [-0.00918, +0.02384] |
| 2 | **-0.01375** [-0.03139, +0.00444] | +0.01570 [-0.00260, +0.03401] |
| 3–4 | **-0.01541** [-0.03417, +0.00415] | +0.00757 [-0.00950, +0.02576] |
| 5–9 | **-0.01894** [-0.03760, +0.00074] | -0.00381 [-0.02137, +0.01421] |
| 10–19 | **-0.03577** [-0.05340, -0.01811] | -0.01205 [-0.03153, +0.00565] |
| 20–29 | **-0.03576** [-0.05430, -0.01735] | -0.02457 [-0.04259, -0.00466] |
| 30–49 | **-0.04177** [-0.05874, -0.02327] | -0.03542 [-0.05408, -0.01663] |
| 50+ | **-0.08484** [-0.10461, -0.06510] | -0.07644 [-0.09764, -0.05772] |

`history_aggregate`의 점추정은 모든 N 구간에서 개선 방향이지만 N=1~5의 CI는 0을 포함한다.
N=10 이상에서만 이번 validation의 95% CI가 0 아래다. 따라서 저이력 개선은 아직 확정 근거가 아니다.

## 판정과 다음 단계

- `history_aggregate`를 validation 선택 후보로 유지하되 FINAL_TEST는 아직 열지 않는다.
- `response_relation`은 N=1~4의 paired MSE가 N=0보다 나빠 현재 선택 게이트에서 제외한다.
- MovieLens 결과는 목표 페르소나와 다르므로 약한 오프라인 근거로만 사용한다.
- KOBIS 2,176행은 검증된 MovieLens/TMDB/서비스 crosswalk가 0행이라 이번 profile에 넣지 않았다.
- 서비스 반영 전에는 실제 평가 이벤트로 사용자 평가를 수행하고, 그 단계에서 title 우선순위와
  TMDB/KOBIS 인기 신호를 별도 rerank 또는 profile로 비교한다.
- `verification.json`은 runtime source 7개와 config·calculator의 hash, episode 277,731행, nested group
  78,516개, 후보 6,869,265행을 전수 검사해
  역할 중복·시간 누수·prefix·candidate digest·as-of 개봉연도·UNKNOWN 표본 가중치가 모두 PASS했다.
- 미래 개봉작과 평점 조건 target 선택이 포함됐던 `ten-percent-fits-v1`, 계약 namespace와 표본
  lineage가 빠진 `ten-percent-fits-v2`, exact runtime source bundle·paired-N gate·bootstrap 전의
  `ten-percent-fits-v3`~`v5`는 채택 근거로 사용하지 않는다.
- paired-N 선택 게이트는 사용자가 실험 전에 요구한 방향을 validation 진단 후 기계식 조건으로
  명문화한 탐색적 기준이다. 사전 독립 검토를 거친 확증 gate로 표현하지 않는다.
- 별도 세션의 설계·결과 독립 검토는 아직 수행하지 않았다. 독립 검토와 실제 사용자 평가 전에는
  팀 채택 또는 서비스 적용 결론이 아니다.
