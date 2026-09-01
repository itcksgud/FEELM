# FEELM 콘텐츠 기반 cold-item 평가 설계 v2

> 문서 상태: `APPROVED_FOR_VALIDATION_PILOT_NOT_LOCKED_TEST`
> 개정일: 2026-08-31
> 적용 범위: MovieLens에 상호작용이 없거나 적은 영화를 TMDB 콘텐츠로 추천·검색하는 능력
> 선행 계약: [Top-2 위험 회피 추천 설계 v4](./02-top2-risk-aware-evaluation-design.md)

기계 판독 값은
[`protocols/rec-eval-content-cold-v2.json`](./protocols/rec-eval-content-cold-v2.json)에 고정한다. 이
REC-EV-021P preflight는 firewall·표본·TMDB manifest Gate를 통과했다. 문서와 JSON은 소규모 Validation
파일럿의 기준이며 Item Locked Test나 제품 채택 계약은 아니다.

## 1. 실험 질문과 주장 경계

두 질문을 분리한다.

1. 동일 영화의 Train 상호작용을 줄일 때 Content·Hybrid·CF의 상대 성능은 어떻게 달라지는가?
2. MovieLens에 전혀 없는 TMDB 영화의 콘텐츠 표현은 독립 관계와 사람의 유사성 판단을 복원하는가?

Train 밀도와 정답 정의를 같은 primary run에서 동시에 바꾸지 않는다. 밀도 실험은 같은 정답을
유지하고, label 민감도는 prediction을 고정한 별도 run으로 낸다.

MovieLens 전체 미등장 영화에는 사용자 선호 정답이 없으므로 유사도만 평가한다. L3 결과로 개인 추천
정확도·만족도·FEELM 신작 성능을 주장하지 않는다.

## 2. 사용자와 영화의 이중 분리

### 2.1 사용자 split

기존 user hash 40/10/10/40 Base Train·Router Train·Validation·Locked Test를 유지한다. Test 사용자의
평점은 모델·router·calibration 학습에 넣지 않는다.

### 2.2 item split

영화도 독립 hash로 분리한다.

```text
item_bucket = uint64_be(
  SHA256("feelm-cold-item-v2|" + movieId)[0:8]
) mod 100
```

`movieId`와 아래 모든 hash의 정수 입력은 부호·공백·선행 0 없는 ASCII base-10으로 직렬화하고, 전체
문자열의 UTF-8 byte를 hash 입력으로 사용한다. `uint64_be`는 digest 앞 8 byte의 unsigned big-endian
정수다.

| bucket | 역할 | 비율 |
| --- | --- | ---: |
| `0..59` | Item Train | 60% |
| `60..79` | Item Validation | 20% |
| `80..99` | Item Locked Test | 20% |

strict-cold의 allowed-use matrix는 다음과 같다. `label only`는 prediction과 candidate 순서를 먼저 artifact로
고정한 뒤 평가에만 읽으며 model/router Feature로 역류시킬 수 없다는 뜻이다.

| item role | Base Train user interaction | Router Train user interaction | Validation user interaction | Locked Test user interaction | MovieLens tag·factor·popularity |
| --- | --- | --- | --- | --- | --- |
| Item Train | base model 학습 허용 | router 학습 허용 | Validation label/tuning only | protocol lock 뒤 Test label only | Item Train interaction에서만 파생 허용 |
| Item Validation | 금지 | 금지 | strict-cold Validation label only | 금지 | 전부 금지 |
| Item Locked Test | 금지 | 금지 | 금지 | protocol lock 뒤 Test label only | 전부 금지 |

Item Validation target·cohort·candidate는 item hash와 TMDB 사전 정보만으로 만든다. base와 router의 모든
supervised parameter는 Item Train 영화만으로 맞추며, Item Validation label은 threshold·candidate N·단일
challenger 선택과 preflight에만 사용한다. Item Locked Test의 모든 MovieLens interaction·tag·factor·
popularity 통계는 protocol lock 전까지 열지 않는다. Test 시점에는 고정 모델이 TMDB Feature와 사용자
입력만 사용할 수 있다. 이 firewall을 통과한 결과만 `STRICT_ITEM_LOCKED_COLD`라고 부른다.

Base Train interaction만 0이고 Router/Validation에는 노출된 영화는
`BASE_TRAIN_ZERO_TUNING_EXPOSED_DIAGNOSTIC`이며 strict cold 일반화 근거로 쓰지 않는다.

### 2.3 controlled-density item split

L2는 strict unseen과 목적이 다르므로 별도 salt를 쓰되, **strict `ITEM_TRAIN` 안에서만** density 역할을
나눈다. 최초 설계는 모든 영화에 density 역할을 부여해 Density Validation 17,416편 중 6,964편이 strict
Validation 또는 Locked Test와 겹쳤다. 이 상태에서는 한 실험이 다른 실험의 숨긴 정답을 열 수 있으므로,
모델 실행 전에 parent scope를 `ITEM_TRAIN_ONLY`로 제한했다.

```text
density_item_bucket = uint64_be(
  SHA256("feelm-density-item-v2|" + movieId)[0:8]
) mod 100
```

strict `ITEM_VALIDATION`과 `ITEM_LOCKED_TEST`에는 density 역할을 부여하지 않고
`DENSITY_OUT_OF_SCOPE`로 둔다.

- Density Validation item으로 panel·mask·model·transition 규칙을 고정한다.
- Density Validation에서는 Base Train user interaction을 mask source로 읽고, hash로 남긴 q개만 해당 q의
  협업 학습에 사용할 수 있다. Router Train interaction은 router 학습용 retained q에만 같은 mask를 적용하고,
  Validation user interaction은 label only다.
- Density Locked Test item의 Base Train·Router Train interaction은 protocol lock 뒤 q masking의 원본으로만
  읽고 retained q만 학습에 사용할 수 있다. 제거 row는 어떤 파생 Feature에도 사용할 수 없다.
- 해당 item의 Test 사용자 rating 값과 성능은 protocol lock 전까지 열지 않는다.
- strict cold와 density masking을 같은 의미로 해석하지 않는다. strict cold는 숨긴 영화를 평가하고,
  density masking은 strict Train 안의 원래 warm 영화 interaction을 q개로 줄이는 강건성 평가다.
- TMDB-only query는 `SHA256("feelm-tmdb-query-v2|" + tmdbId)`로 별도 Train/Validation/Locked Test 분리한다.

## 3. 세 증거 수준

| 수준 | 영화 상태 | 정답 | 주장 가능한 범위 |
| --- | --- | --- | --- |
| `L1_NATURAL_SPARSE` | Base Train q가 0 또는 적음 | Test 사용자 관측 평점 | MovieLens 정적 선호 복원 density slice |
| `L2_MASKED_DENSITY` | 원래 warm 영화를 panel별 q로 masking | 동일 Test 사용자 관측 평점 | 해당 warm-item panel의 masking 강건성 |
| `L3_TMDB_ONLY` | MovieLens 전체에 없음 | 관계 label·사람 유사성 판단 | 콘텐츠 유사도 품질 |

L1의 strict Q0와 tuning-exposed Q0를 별도 표로 낸다. L2는 자연 신규 영화의 인과 효과가 아니라 원래
warm item을 희소화한 통제 ablation이다.

## 4. L1 — 자연 발생 희소 구간

Base Train 사용자 interaction 수 `q_i`를 결과를 보기 전에 계산해 다음으로 나눈다.

```text
Q0      = 0
Q1_4    = 1..4
Q5_19   = 5..19
Q20_99  = 20..99
Q100P   = 100 이상
```

각 q에서 사용자 입력은 `LEAVE_20_OUT_ALL_AVAILABLE`을 primary, K10을 sensitivity로 고정한다. 평가
slate 생성과 GOOD/BAD label은 Top-2 v4를 따른다. 모델은 q로 거르지 않은 동일 NATURAL_20 전체에서
Top-2를 고른다. 이후 노출 결과를 다음처럼 q에 귀속한다.

```text
QBadOpportunity(q)  = slate에 q-bin BAD item >= 1
QGoodOpportunity(q) = slate에 q-bin GOOD item >= 1
QBadExposure@2(q)   = Top-2에 q-bin BAD item >= 1
QGoodHit@2(q)       = Top-2에 q-bin GOOD item >= 1
```

q별 Top-2를 다시 뽑지 않는다. 한 요청이 최대 두 q에 동시에 귀속될 수 있으므로 q별 결과의 합을 전체
Harm/Miss로 해석하지 않는다. q 조건부 user-seed 집계와 zero-opportunity 처리는 Top-2 v4를 따른다. 전체
slate Harm@2가 primary safety이고 q 귀속 결과는 density 진단이다. 각 q별로 다음을 함께 낸다.

- 영화 수와 Test 사용자 수
- GOOD/BAD/NEUTRAL·opportunity event 수
- 영화당 Test label 수 p10·median·p90
- 한국-origin·언어·연대·Base Train popularity strata
- identity·structured·text Feature coverage
- strict item firewall 상태

Q0가 full catalog identity universe에 없는 경우 retrieval 실패와 catalog 결측을 섞지 않고
`CatalogCoverage`, `ConditionalRecall`, `EndToEndRecall`로 분리한다.

## 5. L2 — nested density panel

### 5.1 panel을 분리한다

q=100까지 비교하려고 원래 head item만 고르는 오류를 막기 위해 panel을 나눈다.

| panel | Base Train original q | 비교 density |
| --- | ---: | --- |
| `PANEL_5P` | `>=5` | `0/1/5` |
| `PANEL_20P` | `>=20` | `0/1/5/20` |
| `PANEL_100P` | `>=100` | `0/1/5/20/100/ALL` |

모든 panel은 원래 interaction을 mask하지 않은 `ALL_CONTROL` fit을 control drift 기준으로 추가한다.
`ALL_CONTROL`은 PANEL_5P/20P의 transition curve 점이 아니며 효과 transition 후보로 세지 않는다.
서로 다른 panel의 점을 한 곡선으로 연결하거나 하나의 보편 전환점으로 발표하지 않는다. target panel은
Base Train q·TMDB metadata·사전 strata만으로 선택한다. Locked Test rating 값이나 성능을 target 선택에
사용하지 않는다. Test label 존재율과 수는 결과 coverage로 보고한다.

### 5.2 item cross-fit과 masking

각 density role 안에서 panel target item을 locked hash로 5개 fold로 나눈다. 한 run에서는 한 fold만
target으로 masking하고 나머지는 non-target control로 유지한다. Validation role로 모든 선택을 끝낸 뒤
Locked Test role에는 동일한 고정 절차를 한 번 적용한다.

```text
TRAIN_Q = panel에 허용된 0/1/5/20/100/ALL
retained rows = SHA256("feelm-cold-mask-v2|seed|movieId|userId") 순서의 앞 q개
```

- 제거된 row는 ALS·ItemKNN·EASE·BPR·LightFM·popularity·router Feature 어디에도 사용하지 않는다.
- 각 density·fold에서 협업 모델과 결합 모델을 다시 학습한다.
- TMDB Feature, Test user label, 후보 universe와 target fold는 density 사이에서 고정한다.
- 같은 q의 모델 비교와 q 사이 비교는 동일 target item·Test 관측의 paired contrast다.
- non-target control item의 NDCG·Recall·calibration drift를 함께 측정한다.
- 전역 재학습 간섭이 control drift Gate를 넘으면 q의 item 효과라고 해석하지 않는다.
- 다섯 fold의 target 예측은 item마다 정확히 한 번 생성된 out-of-fold prediction으로 합친 뒤 지표를
  계산한다. fold 평균을 다시 단순 평균하지 않는다.

training mask seed 수와 예상 model fit 수는 REC-EV-021P에서 계산량과 Monte Carlo SE를 보고 잠근다.

### 5.3 사용자 입력을 고정한다

L2 primary는 `LEAVE_20_OUT_ALL_AVAILABLE`, sensitivity는 K10이다. target 영화 rating은 평가 slate에 있을
때 input에서 제외한다. 같은 density panel 안에서 user input·slate·label을 바꾸지 않는다.

각 panel·fold의 full NATURAL_20에서 target fold item을 표시하고, q마다 다음 endpoint를 계산한다.

```text
TargetGoodOpportunity = slate target-fold GOOD >= 1
TargetBadOpportunity  = slate target-fold BAD >= 1
TargetGoodHit@2(q)    = Top-2 target-fold GOOD >= 1
TargetBadExposure@2-given-target-bad-opportunity(q)
                      = Top-2 target-fold BAD >= 1, TargetBadOpportunity에서만 정의
OverallHarm@2(q)      = Top-2 BAD >= 1, target/control 구분 없음
```

모든 q는 같은 slate에서 Top-2를 다시 산출하며 target-good/bad opportunity mask는 모델·q와 무관하다.
target item이 없는 slate는 target endpoint에서 `NULL`이고, 전체 Harm에는 포함한다.

### 5.4 전환점의 제한된 정의

안전성과 효과를 임의의 가중합 `Utility`로 합치지 않는다. 각 panel 안에서 다음 primary effectiveness
contrast를 계산한다.

```text
delta_hybrid_content(q) = TargetGoodHit@2_Hybrid(q) - TargetGoodHit@2_Content(q)
delta_cf_hybrid(q)      = TargetGoodHit@2_CF(q) - TargetGoodHit@2_Hybrid(q)
```

bootstrap replicate마다 동일 user×item weight를 모든 panel·fold·q·두 contrast에 적용한다. predeclared
`panel × q × contrast` 전체의 max-T one-sided 95% simultaneous lower CI를 계산한다. family의 contrast를
`j`, paired estimate를 `delta_j`, bootstrap estimate를 `delta_j^b`, bootstrap SD를 `se_j`라 할 때
`T_b=max_j((delta_j-delta_j^b)/se_j)`, `c95=quantile_0.95(T_b)`,
`lower_j=delta_j-c95*se_j`로 고정한다. `se_j=0`이면 021P를 실패시켜 자동 통과시키지 않는다. 다음 조건을
모두 만족하는 첫 q만 **해당 panel의 transition candidate**로 부른다.

1. overall `Harm@2(q)`가 content baseline 대비 `+0.005` margin으로 non-inferior다.
2. `TargetBadExposure@2-given-target-bad-opportunity(q)`도 같은 margin으로 non-inferior다.
3. effectiveness simultaneous lower CI가 `0`보다 크다.
4. non-target control drift Gate를 통과한다.

1~4는 request를 모집단 단위로 보는 paired user bootstrap과 item panel 일반화를 보는 아래
multi-membership two-way bootstrap에서 모두 통과해야 한다.

모델별 적용 가능한 최소 q는 021P model-applicability matrix에서 잠근다. 적용 불가능한 q를 fallback 점수로
채워 유효 contrast처럼 만들지 않으며, 해당 contrast는 사전 family에서 제외하고 제외 사유를 기록한다.

control drift는 q와 `ALL`의 paired 차이로 계산한다. non-target `Harm@2` upper 95% CI는 `+0.005` 이하,
`NDCG@2`와 mean rank percentile의 90% CI는 각각 `[-0.01,+0.01]` 안에 완전히 들어와야 한다. 이 값은
provisional이므로 Density Validation에서 제품 허용 변화로 승인하고 protocol lock에 기록해야 하며,
Locked Test 결과를 본 뒤 넓힐 수 없다. 결과가 비단조이거나 Gate를 통과하지 못하면 전환점을 주장하지
않는다. 이 결과는 자연 신규·long-tail item 전체의 인과 효과가 아니다.

## 6. Ground truth 민감도

Train과 prediction을 고정한 뒤 다음 label별 방향을 별도 결과로 낸다.

| ID | 정의 | 역할 |
| --- | --- | --- |
| `G1_USER_RELATIVE` | Top-2 v4 mid-rank GOOD/BAD | 개인 선호 주 판정 |
| `G2_ABSOLUTE` | GOOD `>=4.0/4.5`, BAD `<=2.0/1.5` | 별점 습관 민감도 |
| `G3_RELATION` | leakage-redacted TMDB 관계 | TMDB-only 정량 sanity |
| `G4_HUMAN_SIMILARITY` | blind ordinal 0/1/2 판단 | 유사 영화 독립 Gate |

G1/G2는 개인 선호, G3/G4는 영화 유사성이다. 합산하지 않는다.

## 7. L3 — MovieLens 전체 미등장 TMDB 영화

### 7.1 관계별 leakage-redaction matrix

exact Feature 하나만 제거해서는 간접 누수를 막을 수 없다. relation별 structured-only와 text-redacted
run을 모두 만든다.

| 정답 관계 | 반드시 제거할 신호 예시 |
| --- | --- |
| collection/sequel | collection ID, franchise title token, sequel ordinal, overview·keyword의 sequel·캐릭터 고유명 |
| same director | director ID·이름, text의 감독 이름 |
| same cast | cast ID·이름, text의 배우·캐릭터 고유명 |
| genre/country/language/decade | 정답에 사용한 해당 structured field와 직접 text token |

redaction 사전과 적용 checksum을 protocol lock에 저장한다. 관계를 제거한 뒤에도 강한 proxy가 남을 수
있으므로 결과는 relation retrieval sanity지 완전한 의미 유사성 증명이 아니다.

### 7.2 사람 유사성 평가

- query를 TMDB-only/MovieLens-known, 한국-origin, 원어, 연대, metadata coverage로 층화한다.
- Structured·Text·Hybrid 후보를 deduplicate한 뒤 모델명과 순서를 숨겨 제시한다.
- 본 적 없거나 판단 불가능한 영화는 `UNKNOWN`을 허용한다.
- 평가 단위는 `queryId × candidateId`이고, category는 ordered `0<1<2`다.
- disagreement는 `CUSTOM_SQUARED_RANK`, 즉 `d(a,b)=(a-b)^2`로 고정한다. 일반 패키지의 기본
  `ordinal` mode라는 이름으로 대체하지 않는다. unit `u`의 유효 rating 수를 `n_u`, 유효 unit에 포함된
  category별 rating 수를 `N_c`, 그 합을 `N`이라 할 때 coincidence weighting으로 alpha를 계산한다.

  ```text
  Do = sum_u [2/(n_u-1) * sum_{r<r'} d(x_ur, x_ur')] / sum_u n_u
  De = sum_a sum_b N_a * (N_b - I(a=b)) * d(a,b) / (N * (N-1))
  alpha = 1 - Do / De
  ```

  `UNKNOWN`은 결측으로 처리하며 유효 rating 두 개 미만 unit은 `Do`, `N_c`, `N` 모두에서 제외한다.
  구현 언어·패키지를
  쓰더라도 이 수식과 일치하는 golden fixture 및 package/version을 artifact에 기록한다. 유효 unit 제거 후
  `N<2` 또는 `De=0`이면 alpha는 `NULL`이고 Gate를 통과하지 못한다.
- golden fixture는 unit `[0,0]`, `[0,1,2]`에 대해 `Do=1.2`, `De=1.6`, `alpha=0.25`를 요구한다.
- `valid pair coverage = 유효 rating 2개 이상 unit / 배정된 전체 query×candidate unit`이다.
- 최소 query 100, query당 후보 5, 평가자 3은 Validation pilot 시작값이지 자동 승인값이 아니다.
- pilot에서 예상 UNKNOWN·효과 크기·query cluster variance를 구해 필요한 assignment 수를 다시 잠근다.
- pilot은 TMDB Query Validation과 pilot 전용 rater ID만 사용한다. human Locked Test는 protocol version을
  올린 뒤 TMDB Query Locked Test와 pilot에 참여하지 않은 rater ID를 사용한다. query·rater 재사용은
  `RESEARCH_ONLY`로 강등한다.

provisional Gate는 다음과 같으며 pilot 뒤 Locked Test 전에 새 protocol version으로 확정한다.

```text
valid pair coverage >= 0.80
CUSTOM_SQUARED_RANK Krippendorff alpha point >= 0.67
alpha 95% CI lower >= 0.60
query-cluster paired model delta CI lower > 0
```

사람 모델 비교의 primary endpoint는 query-macro human `NDCG@5`다. candidate relevance는 해당
query×candidate의 유효 0/1/2 rating 평균을 2로 나눈 값이다. 비교하는 두 모델 Top-5의 deduplicated union
모든 candidate가 유효 rating 2개 이상일 때만 그 query contrast를 계산하며, 두 모델은 union relevance의
상위 5개로 만든 같은 IDCG를 사용한다. 각 모델 list는 중복 없는 5개여야 하며, common IDCG가 0이면 해당
query contrast는 `NULL`이다. valid contrast query coverage 분모는 배정된 전체 query, 분자는 두 모델
contrast가 모두 non-NULL인 query다.

graded gain은 선형으로 고정한다.

```text
rel_r = mean(valid ordinal ratings for query-candidate) / 2
G(rel_r) = rel_r
DCG@5 = sum_{r=1..5} rel_r / log2(r + 1)
IDCG@5 = deduplicated contrast union의 relevance 상위 5개에 같은 gain·discount 적용
NDCG@5 = DCG@5 / IDCG@5
queryMacroNDCG@5 = family-valid non-NULL query NDCG@5의 산술평균
```

지수 gain `2^rel-1`은 사용하지 않는다. Holm family의 두 contrast는 둘 다 non-NULL인 동일 family-valid
query 집합에서 계산한다. IEEE-754 binary64를 사용하며 golden fixture 허용 오차는 `1e-12`다.

```text
union relevance = [1.0, 0.5, 0, 0, 0]
model order      = [0.5, 1.0, 0, 0, 0]
DCG@5            = 1.1309297535714575
IDCG@5           = 1.3154648767857289
NDCG@5           = 0.8597186998521972
```

Gate family는 `Hybrid-Structured`와 `Hybrid-Text` 두 one-sided paired query contrast다. 두 contrast를
Holm `alpha=0.05`로 보정하고 adjusted lower CI가 모두 `0`보다 커야 한다. `Text-Structured`는 diagnostic이다.
provisional valid contrast query coverage는 `>=0.80`이며 pilot power로 최종 assignment 수를 정한다. 비교
model ID·ranking artifact URI·SHA-256과 유효 contrast query coverage를 protocol lock에 기록한다.

alpha CI는 query 2,000회 cluster bootstrap마다 `Do`, `De`, alpha를 다시 계산한다. query weight는
`feelm-bootstrap-v1|...|query|tmdbId`, rater sensitivity weight는
`feelm-bootstrap-v1|...|rater|raterPseudoId` hash에서 만든다. primary에서는 query의 모든 unit을 integer
weight만큼 복제하고, rater sensitivity에서는 각 rating을 `query_weight*rater_weight` multiplicity로
전개한 뒤 coincidence count를 다시 계산한다. sensitivity는 Gate를 자동 통과시키지 않는다. candidate 표시 순서는
canonical query/candidate/rater hash로 무작위화한다. UNKNOWN·position/order·
평가자 familiarity·한국-origin 유효 query 수를 공개한다. 사람 평가가 없으면 L3는 `RESEARCH_ONLY`다.

## 8. 모델과 Feature 경계

비교 모델:

- B0 Bayesian prior/fallback
- TMDB Structured Content
- TMDB Text Embedding
- Structured + Text Hybrid
- q가 허용할 때 ItemKNN·ALS·EASE·BPR·LightFM
- Content + CF RRF와 item-density-aware Router

TMDB 취향 Feature에 장르·감독·배우·키워드·줄거리·collection·국가·언어·연대·러닝타임과 missingness
mask를 허용한다. TMDB popularity·vote_average·vote_count·watch provider와 MovieLens 장르·자유 태그는
금지한다. 현재 TMDB metadata는 `CURRENT_METADATA_RETROSPECTIVE`다.

## 9. 지표와 통계

### 9.1 L1/L2 개인 선호

- CatalogCoverage·ConditionalRecall·EndToEndRecall@50/100/200/500/1000/2000
- GOOD/BAD mean rank percentile과 pairwise AUC
- 모든 후보가 관측 label인 cold-item slate의 conditional/unconditional Harm@2·Miss@2·NDCG@2
- panel·q별 Content/Hybrid/CF paired contrast
- Feature coverage·fallback·latency·artifact size
- target item과 non-target control item drift

L1 q metric은 mixed NATURAL slate의 q-attributed 노출이고, L2 target metric은 target fold 귀속이다. 두
estimand를 같은 곡선이나 같은 전환점 계산에 섞지 않는다.

### 9.2 user×item two-way uncertainty

사용자와 영화는 crossed dependency다. additive user-item endpoint에는 paired two-way Poisson/pigeonhole
bootstrap을 그대로 사용한다.

```text
user_weight_u ~ Poisson(1)
item_weight_i ~ Poisson(1)
observation_weight_ui = user_weight_u * item_weight_i
```

request-level OR endpoint는 한 request에 여러 item cluster가 있으므로 equal-share multi-membership weight를
사용한다. endpoint `e`의 label-free membership set을 `M_use`라고 한다.

```text
overall Top-2 endpoint: M_use = NATURAL_20 전체 item
L1 q-attributed endpoint: M_use = slate의 해당 q-bin item
L2 target endpoint: M_use = slate의 target-fold item
non-target control endpoint: M_use = slate의 non-target item

request_weight_use = user_weight_u * mean_{i in M_use}(item_weight_i)
```

`M_use`가 비면 endpoint는 `NULL`이다. opportunity 조건부 endpoint는 고정 event/mask를 이 request weight로
가중하며, bootstrap weight로 item을 제거·복제하거나 Top-2를 다시 정렬하지 않는다. 이는 request event를
membership item에 같은 몫으로 전개한 multiplier bootstrap이다. 같은 user/item weight와 membership set을
baseline/challenger와 모든 q에 적용한다.

user와 item weight는 Top-2 v4의 `feelm-bootstrap-v1` hash inverse-Poisson 규칙을 사용하되 axis를 각각
`user`, `item`으로 넣는다. first-2,000-valid-attempt, nearest-rank quantile, 무효 replicate manifest와 golden
fixture도 동일하다. 2,000회 CI와 user-only·item-only sensitivity를 함께 낸다. target item을 고정 사례로만
해석하는 표에는 user bootstrap을 primary로 쓸 수 있지만 item 모집단 일반화에는 위 multi-membership
two-way 결과를 사용한다. transition Gate는 user-only와 two-way가 모두 같은 방향으로 통과해야 한다.

### 9.3 L3 유사성

- query macro NDCG@5·MRR@5·Recall@5
- relation별 structured/text redaction delta
- query Feature coverage와 한국-origin·원어·연대 slice
- CUSTOM_SQUARED_RANK Krippendorff alpha와 query-cluster CI
- UNKNOWN·유효 pair·order effect

## 10. REC-EV-021 작업 분해

| 실험 | 질문 | 필수 출력 |
| --- | --- | --- |
| `REC-EV-021P` | item firewall·panel·표본·계산량이 가능한가? | q/panel/strata label·coverage, exposed Q0, model fit estimate |
| `REC-EV-021A` | 자연 q에서 어떤 모델이 안전한가? | strict/exposed Q0 분리, q별 Top-2·retrieval |
| `REC-EV-021B` | panel 안에서 q를 줄이면 무엇이 변하는가? | 5 item-fold paired contrast와 control drift |
| `REC-EV-021C` | G1/G2에 결과가 유지되는가? | prediction 고정 label sensitivity |
| `REC-EV-021D` | redaction 뒤 TMDB 관계를 복원하는가? | relation별 structured/text ablation |
| `REC-EV-021E` | 사람 유사성 판단과 가까운가? | pilot power·agreement·coverage·blind comparison |

## 11. 구현 준비도와 중단 조건

v2 JSON Schema, artifact contract, runner·unit test·verifier와 Validation preflight가 준비됐고,
REC-EV-019B 전체 TMDB feature manifest까지 확인됐다. 현재 상태는
`GO_FOR_SMALL_VALIDATION_PILOT_NOT_FULL_GRID`다. 먼저 panel 하나·mask seed 하나·ALS와 content baseline으로
시간·메모리·결측 fallback을 측정한다. 이 결과와 model-applicability matrix를 잠그기 전에는 Item Locked
Test와 사람 평가 Test를 열지 않는다.

다음이면 결과를 `INCONCLUSIVE`로 종료한다.

- item firewall 위반
- panel target을 Locked Test rating 값·성능으로 선택
- q별 Test label·candidate·user input 차이
- 제거 interaction의 popularity·factor·router 유입
- target 외 control drift가 Validation에서 잠근 한도 초과
- panel 사이 점을 연결해야만 전환점이 생김
- relation redaction checksum 불일치
- 사람 유효 coverage·agreement·power 미달

## 12. 허용되는 주장

| 결과 | 허용 | 금지 |
| --- | --- | --- |
| strict locked Q0 | interaction-derived tuning에 노출되지 않은 MovieLens item의 정적 선호 복원 proxy | 실제 출시 신작·FEELM 만족 |
| exposed Q0 | Base Train q=0인 tuning-exposed 진단 | strict cold 일반화 |
| TRAIN_Q=0 | 해당 warm-item panel의 masking 강건성 | 자연 신규 item의 인과 효과 |
| panel transition | 해당 panel·모델·정답의 전환 후보 | 보편적인 q 임계값 |
| TMDB relation | redacted 조건의 관계 retrieval sanity | 전체 의미 유사성·개인 선호 |
| 사람 유사성 | 정의한 blind task·평가자 모집단의 유사성 판단 | 감상 만족·개인 champion |

## 13. 참고 근거

- [Top-2 위험 회피 추천 설계 v4](./02-top2-risk-aware-evaluation-design.md)
- [MovieLens 평가 설계 재판단](./evidence/REC-DATA-003-evaluation-design-decision.md)
- [MovieLens 한국-origin coverage](./evidence/REC-DATA-002-korean-origin-coverage.md)
- [Strict cold-start benchmark를 분리한 Firzen](https://arxiv.org/abs/2410.07654)
- [인기도 구간별 행동·콘텐츠 역할 연구](https://arxiv.org/abs/2411.11225)
- [콘텐츠 기반 cold-start item representation](https://arxiv.org/abs/2404.13808)
- [Dependent data의 multiway uncertainty](https://arxiv.org/abs/1304.7406)
