# FEELM Top-2 위험 회피 추천 설계 v4

> 문서 상태: `PROPOSED_PROTOCOL_VALIDATION_PREFLIGHT_REQUIRED`
> 개정일: 2026-08-31
> 적용 범위: 취향 기반 개인 추천의 최초 2편과 이후 2편 단위 추가 추천
> 제품 전제: 기존 추천은 평가 또는 `관심 없음` 전까지 유지하고, 새 요청마다 중복 없이 2편을 추가한다.

기계 판독 값은 [`protocols/rec-eval-top2-v4.json`](./protocols/rec-eval-top2-v4.json)에 고정한다. 이
문서와 JSON은 REC-EV-020P preflight 전까지 구현 승인 계약이 아니다. 기존 `rec-eval-vnext-2`와
REC-EV-019A/019B artifact는 과거 실험 재현을 위해 변경하지 않는다.

## 1. 제품 목표와 증거 경계

FEELM은 요청당 2편만 추가한다. 모델 선택 순서를 다음처럼 고정한다.

1. 싫어한 영화가 Top-2에 들어가는 위험을 낮춘다.
2. 좋아한 영화가 후보에 있는데도 Top-2에서 놓치는 비율을 낮춘다.
3. 두 편 모두 좋아한 영화인 비율과 첫 번째 순위 품질을 높인다.
4. 위 조건을 지킨 후보 사이에서 coverage·다양성·비용을 비교한다.

이 평가는 MovieLens 사용자가 **이미 평가한 영화**를 가려 놓고 선호를 복원하는 proxy다. 다음은 주장할
수 없다.

- 보지 않은 영화의 실제 만족도
- FEELM 한국 사용자의 절대 BAD 노출률
- MovieLens timestamp 기반 관람 순서·취향 변화
- 미평가 영화의 GOOD/BAD 여부

따라서 `Observed-Dislike Harm@2`는 관측 평가 slate 안에서의 상대 비교 지표다. 제품 실제 Harm으로
축약하지 않는다.

## 2. 고정 사용자 분리

기존 hash assignment를 유지한다.

| bucket | 역할 | 비율 |
| --- | --- | ---: |
| `0..39` | Base model Train | 40% |
| `40..49` | Router/stacker Train | 10% |
| `50..59` | Validation | 10% |
| `60..99` | Locked Test | 40% |

Router/Validation/Test 사용자의 평점은 base ALS·ItemKNN·EASE·BPR 학습에 한 건도 넣지 않는다. Test는
REC-EV-020P, artifact contract, runner, verifier, power Gate를 모두 통과하기 전에는 열지 않는다.

## 3. 평가 label을 정확히 고정한다

### 3.1 사용자 상대 효용

MovieLens snapshot에서 사용자 `u`의 모든 관측 평점 수를 `n_u`, 대상 평점을 `r_ui`라고 할 때 평가용
mid-rank utility는 다음과 같다.

```text
lower = count_j(r_uj < r_ui)
equal = count_j(r_uj = r_ui)

utility(u,i) = (1 + lower + 0.5 * equal) / (n_u + 2)
```

- 대상 rating 자체를 `equal`에 포함한다.
- 전체 immutable user snapshot으로 한 번만 계산한다.
- label은 평가에만 사용하고 모델 Feature·threshold tuning·후보 생성에 전달하지 않는다.
- 이력 전체를 label 정의에 쓰는 것은 미래 예측이 아니라 snapshot 선호 복원의 결과 정의다.

| 등급 | 주 판정 | 절대 별점 민감도 |
| --- | --- | --- |
| `GOOD` | utility `>= 0.65` | rating `>=4.0`, strict `>=4.5` |
| `BAD` | utility `<= 0.35` | rating `<=2.0`, severe `<=1.5` |
| `NEUTRAL` | 그 사이 | 그 사이 |
| `UNKNOWN` | 미평가 | BAD로 변환 금지 |

주 판정과 절대 기준의 충돌률을 K·사용자 구간별로 공개한다.

### 3.2 재현 가능한 순서

각 사용자·seed의 rating은 다음 key의 unsigned byte lexicographic 순으로 정렬한다.

```text
SHA256(
  "feelm-top2-v4|" + seed + "|" + userId + "|" + movieId
)
```

문자열 결합 전 `seed`, `userId`, `movieId`는 부호·공백·선행 0이 없는 ASCII base-10 정수로
canonical serialization한다. salt와 구분자를 포함한 전체 문자열의 UTF-8 byte를 SHA-256에 넣고, 결과
32 byte를 unsigned lexicographic 순서로 비교한다. 동일 digest는 `movieId` 정수 오름차순으로 정리한다.
MovieLens의 `(userId, movieId)`가 중복이면 preflight를 실패시킨다. seed 목록·salt·직렬화 규칙은 JSON과
protocol lock에 저장한다.

## 4. NATURAL_ALL 주 평가

### 4.1 slate와 입력

각 사용자·seed에서 label을 보지 않고 다음처럼 나눈다.

```text
정렬된 전체 평가
├─ 앞 20편: NATURAL_20 평가 slate
└─ 나머지: input reservoir
```

- `K=0/1/3/5/10/20/30/50`은 reservoir의 중첩 prefix를 사용한다.
- `LEAVE_20_OUT_ALL_AVAILABLE`은 reservoir 전체를 입력에 사용한다.
- 모델에 history cap이 있으면 cap·잘린 비율을 기록하고 ALL이라고 부르지 않는다.
- 구조적 eligibility는 `history >= 20 + K`뿐이다. GOOD/BAD 수로 사용자를 제외하지 않는다.
- 같은 user·seed의 모든 K와 ALL은 같은 slate를 사용한다.
- 주 분석 slate는 20편이다. `REC-EV-020P-A`에서만 label-free prefix slate `10/20/30` 민감도를
  계산한다. 각 크기의 구조적 eligibility는 `history >= sensitivity_slate_size + K`이며, 같은 hash 순서의
  앞 10/20/30편을 사용한다. 민감도는 champion 선택이나 Test 사후 변경에 사용하지 않는다.

### 4.2 공통 비교 cohort

| cohort | 조건 | 비교 |
| --- | --- | --- |
| `COLD_COMMON` | history `>=30` | K0/1/3/5/10 |
| `MATURE_COMMON` | history `>=70` | K20/30/50 |
| `PAIR_K_ALL` | 각 K의 구조적 eligible | 해당 K vs LEAVE_20_OUT_ALL |
| `ALL_HISTORY_BIN` | reservoir `0~9/10~29/30~99/100~299/300+` | ALL 내부 이력량 차이 |

분모가 다른 K를 하나의 paired 학습 곡선으로 연결하지 않는다. 전체 eligible 결과와 common-cohort
결과를 함께 낸다.

### 4.3 opportunity와 요청 상태

slate label 분포가 모델 선택 기회를 제한하므로 먼저 다음을 계산한다.

```text
BadOpportunity      = slate BAD >= 1
GoodOpportunity     = slate GOOD >= 1
TwoGoodOpportunity  = slate GOOD >= 2
```

요청 결과는 다섯 상태로 저장한다.

| 상태 | 정의 |
| --- | --- |
| `D_HARM` | Top-2에 BAD 1편 이상 |
| `A_BOTH_GOOD` | BAD 0, GOOD 2 |
| `B_ONE_GOOD_SAFE` | BAD 0, GOOD 1 |
| `C_MISS` | BAD 0, GOOD 0, slate에는 GOOD 1편 이상 존재 |
| `E_NO_GOOD_OPPORTUNITY` | BAD 0, GOOD 0, slate에 GOOD 없음 |

다섯 상태는 상호 배타적이며 합이 1이어야 한다. GOOD 기회가 없는 사용자를 Miss로 벌하지 않는다.

### 4.4 주 지표와 분모

user `u`, seed `s`의 Top-2 GOOD/BAD 수를 `g_us`, `b_us`라고 한다. binary NDCG는 `GOOD=1`, 그 외
`0`으로 두고 다음처럼 계산한다.

```text
DCG@2  = rel_1 / log2(2) + rel_2 / log2(3)
IDCG@2 = sum_{r=1..min(2, slateGoodCount)} 1 / log2(r + 1)
NDCG@2 = DCG@2 / IDCG@2
```

`GoodOpportunity=0`이면 NDCG는 `NULL`이다. 지표별 opportunity mask와 numerator는 다음으로 고정한다.

| 지표 | opportunity mask | user-seed numerator/value |
| --- | --- | --- |
| `Harm@2-unconditional` | `1` | `I(b_us >= 1)` |
| `Harm@2-given-bad-opportunity` | `slateBadCount>=1` | `I(b_us >= 1)` |
| `Harm@1-given-bad-opportunity` | `slateBadCount>=1` | `I(rank1 is BAD)` |
| `Miss@2-given-good-opportunity` | `slateGoodCount>=1` | `I(g_us = 0)` |
| `BothGood@2-given-two-good-opportunity` | `slateGoodCount>=2` | `I(g_us = 2)` |
| `SafeHit@2-given-good-opportunity` | `slateGoodCount>=1` | `I(b_us = 0 and g_us >= 1)` |
| `NDCG@2-given-good-opportunity` | `slateGoodCount>=1` | 위 binary `NDCG@2` |

조건부 지표의 사용자 값은 `sum_s(mask_us * value_us) / sum_s(mask_us)`다. 20개 seed 모두에서 mask가
0인 사용자는 해당 지표에서 `NULL`로 두고 bootstrap 분모에서 제외한다. opportunity mask는 모델과
무관한 동일 slate에서 계산하므로 baseline/challenger가 같은 seed 분모를 사용한다. 무조건 지표는 20개
seed 전체 평균이다. seed별 macro 결과와 참고용 pooled user-seed 결과를 별도 표기하며 서로 대체하지
않는다.

절대 비율마다 opportunity rate, 분모 사용자 수, event 수를 함께 표시한다. 모델 간 판정은 동일
user·seed의 paired delta로 한다.

추가 추천은 누적 깊이 `2/4/6`에서 같은 지표를 낸다. 이미 노출한 영화의 중복은 금지하지만 실제 화면의
기존 추천은 평가 또는 `관심 없음` 전까지 유지한다.

## 5. 두 진단 Track

### 5.1 NATURAL_LABEL_RICH_DIAGNOSTIC

NATURAL_20에 GOOD 2편·BAD 2편 이상인 사용자는 분리력 진단용으로만 낸다. 이 집단은 rating 분산과
활동량에 조건부이므로 자연 사용자 분포나 제품 위험률을 대표하지 않는다. 포함/제외 사용자의 history,
rating mean/std, label 비율, 인기도 분포 차이를 공개한다.

### 5.2 EXTREME_20

별도 run에서 사용자 상대 utility 상위 10편과 하위 10편을 평가 slate로 예약한다.

- `min(top utility) - max(bottom utility) >= 0.30`이어야 한다.
- 경계 utility tie는 위 keyed hash 순서로 결정하고 tie rate를 보고한다.
- 상·하 집합이 겹치거나 각 10편을 만들 수 없으면 diagnostic에서 제외하고 사유를 기록한다.
- input reservoir는 EXTREME slate를 제외한 나머지 rating이다.
- 실제 Harm prevalence로 해석하지 않는다.

지표는 pairwise AUC, `ExtremeBad@2`, NDCG@2와 다음 margin이다.

```text
scoreLower(u,i) = count_j(score_uj < score_ui)
scoreEqual(u,i) = count_j(score_uj = score_ui), target 포함
scorePercentile(u,i) = (1 + scoreLower + 0.5 * scoreEqual) / 22
ExtremeMargin_u = mean_top(scorePercentile) - mean_bottom(scorePercentile)
```

NaN·무한 score나 20개 전부를 점수화하지 못한 모델-사용자는 실패 artifact로 남기고 진단 분모에서
제외하지 않는다. raw score 단위가 다른 모델 사이에 raw margin을 직접 비교하지 않는다.

## 6. 전체 카탈로그 positive-unlabeled retrieval

full catalog에는 UNKNOWN이 대부분이므로 Precision@2·Harm@2를 계산하지 않는다. 최종 TMDB identity
allowlist 카탈로그를 공통 universe로 쓰고 입력 K편만 제외한다. known GOOD를 강제로 넣지 않는다.

user·seed·K별 `AllKnownGoodAfterInput`은 immutable snapshot에서 GOOD인 모든 영화 중 해당 K 입력에
들어간 영화를 제외한 집합이다. NATURAL slate의 GOOD과 reservoir에서 K에 들지 않은 GOOD은 포함한다.
`UniverseKnownGood = AllKnownGoodAfterInput ∩ identity universe`다. ALL_AVAILABLE에서는 reservoir 전체가
입력이므로 NATURAL slate의 GOOD만 평가 positive로 남는다.

```text
CatalogCoverage
  = |UniverseKnownGood| / |AllKnownGoodAfterInput|

ConditionalRecall@N
  = |TopN ∩ UniverseKnownGood| / |UniverseKnownGood|

EndToEndRecall@N
  = |TopN ∩ AllKnownGoodAfterInput| / |AllKnownGoodAfterInput|
```

각 분모가 0이면 해당 user-seed 지표는 `NULL`이며 별도 zero-denominator count를 낸다. Top-N은 공통
identity universe에서 입력 영화만 제거한 뒤 생성하고, 평가 positive를 candidate에 주입하지 않는다.

`N=50/100/200/500/1000/2000` 곡선, MRR, rank percentile, latency·memory를 낸다. 후보 N은 Validation에서
고정한다. Q0와 TMDB-only coverage는 [cold-item 설계](./03-content-cold-item-evaluation-design.md)에서
별도로 판정한다.

## 7. 후보 모델과 위험 회피 reranker

동일 slate·candidate·seed에서 다음을 비교한다.

- B0 Bayesian Popularity
- Global/User/Item Bias
- ItemKNN
- Explicit ALS Fold-in
- EASE와 BPR
- TMDB Structured/Text Content
- RRF/Hybrid
- K·item-density-aware Router
- risk-aware reranker

Validation에서 raw score를 `pGood`, `pBad`, uncertainty로 보정한다. 첫 reranker는 pBad 상한이 허용
threshold를 넘는 후보를 보류하고, 남은 후보를 pGood 보수적 하한으로 정렬한다. 안전 후보가 2편 미만이면
B0 또는 TMDB Content fallback을 사용한다. MovieLens calibration으로 FEELM 사용자에 대한 conformal
보장을 주장하지 않는다.

## 8. 통계와 채택 Gate

### 8.1 seed 집계와 bootstrap

- preflight seed는 JSON의 고정 20개다.
- 각 metric을 user·seed별로 계산한 뒤 사용자 안에서 seed 평균을 먼저 구한다.
- 모델 delta는 같은 user·seed를 paired로 계산한다.
- CI는 사용자 paired cluster bootstrap 2,000회로 구한다.
- seed별 결과와 Monte Carlo SE를 별도 공개한다.
- seed Monte Carlo SE는 20개 seed별 macro estimate의 표준편차를 `sqrt(20)`으로 나눈 값이다.
- `seed Monte Carlo SE >0.001`이면 seed를 늘리고 새 protocol version을 만든다.

bootstrap은 구현체 PRNG에 의존하지 않고 attempt별 cluster weight를 keyed hash로 만든다.

```text
digest = SHA256(
  "feelm-bootstrap-v1|" + protocolVersion + "|" + attempt + "|user|" + userId
)
X = uint64_be(digest[0:8])
U = (2*X + 1) / 2^65
weight = min{k >= 0 : exp(-1) * sum_{j=0..k}(1/j!) >= U}
```

- 정수 직렬화·UTF-8 규칙은 3.2절과 같다.
- Poisson CDF는 decimal precision 80 이상으로 계산하고 golden weight fixture를 계약에 둔다.
- attempt는 `0..9999`이며 그중 endpoint 분모가 0이 아닌 첫 2,000개를 사용한다.
- 10,000 attempt 안에 2,000 valid replicate가 없으면 preflight를 실패시킨다.
- 무효 replicate 수·사유·attempt ID를 manifest에 남긴다.
- empirical quantile은 1-indexed nearest-rank `x_(ceil(p*B))`를 사용하며 선형 보간하지 않는다.
- two-sided percentile CI는 `p=.025/.975`, one-sided upper/lower와 max-T critical value는 `p=.95`다.

### 8.2 provisional safety margin과 power

`REC-EV-020P-B` 입력은 checksum이 있는 다음 세 artifact다.

- baseline: `APPROVED_C2A_INTERNAL_POPULARITY_ONLY` serving-policy artifact
- challenger: primary endpoint·power 계산 전에 candidate ID와 사전 명시 선택 규칙을 잠근 단일
  challenger artifact. 선택 규칙이 Validation을 사용했다면 사용 지표와 후보 수를 artifact에 기록한다.
- evaluation: 동일 user·seed·slate와 endpoint를 보장하는 `REC-EV-020P-A` artifact

artifact URI·model/policy ID·SHA-256이 없거나 challenger가 둘 이상이면 preflight를 실패시킨다. 다음 값은
제품 승인 threshold가 아니라 Validation power와 제품 허용 손실 검토를 함께 받아야 하는 provisional 값이다.

```text
delta_harm = Harm_challenger - Harm_baseline
H0_harm: delta_harm >= +0.005
HA_harm: delta_harm <  +0.005
power target alternative: delta_harm = 0.000

delta_miss = Miss_challenger - Miss_baseline
H0_miss: delta_miss >= 0.000
HA_miss: delta_miss <  0.000
power target alternative / SESOI: delta_miss = -0.010

one-sided alpha             = 0.05
target power                = 0.80
```

primary safety endpoint는 overall `Harm@2-unconditional`이다. primary benefit endpoint는
`Miss@2-given-good-opportunity`다. Validation의 user-level seed-mean paired delta 분산으로 endpoint별
`n_power`를 계산한다. `var_delta`는 분석 가능한 Validation 사용자의 paired delta sample variance
(`ddof=1`)다. `z_(1-alpha)=1.6448536269514722`, `z_(power)=0.8416212335729143`으로 고정한다.
모든 값은 protocol lock에 열거할 deployment input state별로 따로 계산한다.

```text
n_power = ceil(var_delta * (z_(1-alpha) + z_(power))^2 / detectable_gap^2)
detectable_gap_harm = 0.005 - 0.000 = 0.005
detectable_gap_miss = 0.000 - (-0.010) = 0.010
n_analysis_required_harm = n_power_harm
n_analysis_required_miss = n_power_miss
```

Miss는 20개 seed 중 GoodOpportunity가 한 번이라도 있는 사용자만 분석 가능하다. Validation에서 이 비율
`p_miss_nonnull`과 Wilson one-sided 95% lower bound `p_miss_nonnull_L95`를 계산한다.

```text
p = x / n
z = 1.6448536269514722
p_miss_nonnull_L95 =
  (p + z^2/(2n) - z*sqrt(p*(1-p)/n + z^2/(4n^2))) / (1 + z^2/n)

n_total_required_miss = ceil(n_power_miss / p_miss_nonnull_L95)
n_total_required(state) = max(5000, n_power_harm(state), n_total_required_miss(state))
```

`p_miss_nonnull_L95=0`이면 preflight를 실패시킨다. Locked Test를 연 뒤에도 prediction 결과를 보기 전에
구조·opportunity count만 먼저 검증하여 `n_test_harm_nonnull >= n_power_harm` 및
`n_test_miss_nonnull >= n_power_miss`를 deployment state마다 모두 요구한다. 부족하면 margin을 넓히지 않고 결과는
`INCONCLUSIVE`다. Miss 승격은 one-sided upper CI `<0`과 point estimate `<=-0.010`을 모두 요구한다.
power가 margin의 제품 타당성을 대신하지 않으므로 `+0.005` 허용 손실은 protocol lock 전에 별도 승인
기록을 가져야 한다. Test를 본 뒤 margin·SESOI·cohort를 바꾸지 않는다.

### 8.3 core safety segment

Validation 분포로 threshold를 잠그고 다음 segment family를 Test 전에 고정한다.

- history: low/mid/high
- input rating variance: no-input/single-input/low/mid/high
- input 영화 Base Train popularity: tail/mid/head
- TMDB Feature coverage: complete/incomplete

segment membership은 model output, 평가 slate label, fallback 사용 여부를 포함하지 않는다. history는
평점값이 아닌 관측 개수만, rating variance·popularity·TMDB coverage는 해당 K/ALL 입력 영화만 사용한다.
K0의 input 기반 family는 별도 `NO_INPUT` segment다. 배포 대상 K/ALL 각각에서 같은 사용자로
baseline/challenger를 paired 비교한다. fallback used/not-used는 모델 의존적이므로 diagnostic으로만
보고한다.

feature 계산은 다음으로 고정한다.

| family | 값 | missing·경계 정책 |
| --- | --- | --- |
| history | immutable snapshot의 관측 rating 개수 | 값 자체만 사용, rating 값 미사용 |
| input rating variance | K/ALL 입력 raw rating의 population variance `sum((r-mean)^2)/K` | K0=`NO_INPUT`, K1=`SINGLE_INPUT` |
| input popularity | 입력 영화별 `log1p(BaseTrain interaction count)`의 median; 짝수 K는 중앙 두 값 평균 | K0=`NO_INPUT`, item count missing=0 |
| input TMDB complete | 모든 입력 영화가 identity, genre 1개 이상, release year, original language, nonblank overview를 가짐 | K0=`NO_INPUT`, 하나라도 누락=`INCOMPLETE` |

연속 family는 배포 input state별 Validation 값의 nearest-rank 1/3·2/3 quantile로 low/mid/high를 정한다.
`x<=q1`은 low, `q1<x<=q2`는 mid, `x>q2`는 high다. `q1=q2`면 해당 family는
`SEGMENT_NOT_IDENTIFIABLE` diagnostic으로 내리고 safety Gate에 넣지 않는다. threshold·필드 coverage·
분포 checksum을 protocol lock에 기록한다.

각 core segment는 Validation의 해당 segment paired variance로 `n_power_harm_segment`를 별도 계산하고
`max(500, n_power_harm_segment)` 이상의 Test 사용자를 요구한다. 조건부 Harm 진단에는 BadOpportunity
50건도 요구하되 이는 overall safety power를 대신하지 않는다. 미달 segment는 `SEGMENT_INCONCLUSIVE`이며
전체 배포 승인을 막고 해당 구간 baseline fallback을 유지한다. 한국-origin은 같은 power 조건을 충족할
때만 core로 승격하고, 아니면 diagnostic으로 보고한다.

### 8.4 CVaR90

사용자별 seed 평균 `Harm@2-unconditional`을 `h_u`로 두고 내림차순 정렬한 최악 10%의 평균을 empirical
expected shortfall로 계산한다. 10% 경계 tie는 필요한 질량만 fractional allocation한다. 각 paired user
bootstrap replicate에서 baseline과 challenger의 tail membership과 CVaR를 각각 다시 계산한다.
`ΔCVaR90 = CVaR_challenger - CVaR_baseline`의 one-sided 95% upper CI가 `0` 이하일 때만 통과한다.

### 8.5 Locked Test 비교

- baseline 1개, Validation에서 고른 challenger 1개, serving policy 1개만 Test에 올린다.
- `comparison-input-lock.json`은 Test 전에 실제 배포 input state를
  `K0/K1/K3/K5/K10/K20/K30/K50/ALL` 중 하나 이상으로 열거한다. wildcard나 `deployment K/ALL` 문자열은
  허용하지 않는다.
- safety hypothesis ID는 각 state의 `HARM_NI/OVERALL`, 식별 가능한 각
  `HARM_NI/SEGMENT/{family}/{level}`, `CVAR90_NONINCREASE`를 전부 열거한다.
- secondary hypothesis ID는 각 state의 overall `MISS/BOTH_GOOD/SAFE_HIT/NDCG`를 전부 열거한다.
- safety family는 배포 K/ALL별 overall Harm NI, model-independent core segment Harm NI, CVaR90 non-increase다.
- safety family의 모든 가설을 통과해야 하는 intersection–union Gate다.
- safety Gate에는 Holm을 적용해 통과를 쉽게 만들지 않는다.
- safety 통과 후 `Miss`, `BothGood`, `SafeHit`, `NDCG` secondary superiority family에 Holm을 적용한다.
- 좋은 작품 증가로 Harm 증가를 상쇄하는 가중합은 금지한다.

## 9. REC-EV-020 작업 분해

| 실험 | 질문 | 상태·필수 출력 |
| --- | --- | --- |
| `REC-EV-020P-A` | cohort·label·slate·seed가 실행 가능한가? | preflight: slate 10/20/30, label/opportunity, 선택 편향, common cohort |
| `REC-EV-020P-B` | provisional margin의 검정력이 있는가? | Validation baseline/challenger paired variance·discordance·n_power |
| `REC-EV-020A` | K별 Top-2 안전성이 개선되는가? | NATURAL_ALL conditional/unconditional metric과 CI |
| `REC-EV-020B` | known GOOD를 full catalog에서 회수하는가? | Coverage·Conditional/EndToEnd Recall 곡선 |
| `REC-EV-020C` | 다음 평가 세션에서도 방향이 유지되는가? | `NEXT_RATING_SESSION_PROXY`, champion 권한 없음 |
| `REC-EV-020D` | 더 많은 이력이 같은 사용자에게 도움이 되는가? | PAIR_K_ALL과 ALL_HISTORY_BIN |
| `REC-EV-020E` | 명확한 호불호를 구별하는가? | LABEL_RICH와 EXTREME diagnostic |

## 10. 구현 준비도와 중단 조건

v4 설계를 입력으로 Schema·artifact contract·runner를 구현하는 것은
`GO_FOR_020P_CONTRACT_IMPLEMENTATION`이다. 다만 preflight 실행 준비도는
`NO_GO_PENDING_020P_CONTRACT_AND_RUNNER`이며, 다음이 생기기 전에는 Validation preflight 완료나 Locked
Test 가능 상태로 표시하지 않는다.

- v4 JSON Schema와 artifact contract
- REC-EV-020P-A runner, unit test, verifier
- source/output checksum과 protocol lock
- Validation-only 020P-A 결과
- 020P-B baseline/challenger power artifact
- 성공·실패 run을 모두 보존하는 manifest

다음이면 `BLOCKED` 또는 `INCONCLUSIVE`로 종료한다.

- label-conditioned primary cohort
- required Test 사용자가 예상 eligible보다 많음
- seed/slate size에 따라 모델 방향이 바뀜
- Test 확인 후 margin·cohort·threshold 변경
- UNKNOWN을 negative로 변환
- full-catalog positive injection 또는 모델별 universe 차이

## 11. 허용되는 주장

| 결과 | 허용 | 금지 |
| --- | --- | --- |
| NATURAL_ALL | 평가한 무작위 20편 안의 정적 선호 복원 상대 성능 | 실제 unseen 만족·FEELM 절대 Harm |
| LABEL_RICH | 호불호 label이 충분한 사용자 조건부 분리 | 자연 사용자 대표성 |
| ALL_AVAILABLE | 20편 holdout 뒤 나머지 이력 증가의 paired 효과 | 제품에서 모든 이력을 쓴 실제 효과 |
| EXTREME_20 | 명확한 상·하 선호 분리 stress test | 실제 Harm prevalence |
| Full catalog | known GOOD coverage·회수·rank | UNKNOWN 포함 Precision/Harm |

## 12. 참고 근거

- [MovieLens 평가 설계 재판단](./evidence/REC-DATA-003-evaluation-design-decision.md)
- [추천 입력 신호 계약](./00-input-signal-contract-vnext.md)
- [콘텐츠 cold-item 평가 설계](./03-content-cold-item-evaluation-design.md)
- [MNAR 추천 평가의 편향 연구](https://arxiv.org/abs/2403.00817)
- [Conformal risk control과 unwanted recommendation](https://doi.org/10.1145/3705328.3748054)
- [Risk-sensitive ranking](https://doi.org/10.1016/j.ipm.2025.104126)
