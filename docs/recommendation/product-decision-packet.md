# 추천 제품 결정 패킷

> 상태: `APPROVED_LOCAL_PRODUCT_BOUNDARIES`  
> 대상: `REC-PD-001`, `REC-PD-003`, `REC-PD-005`, `REC-PD-007`  
> 승인 결과: `RECORDED_LOCAL_PRODUCT_APPROVAL` — 2026-08-30 사용자의 standalone 개발 완료 지시로 아래 보수적 권장안을 선택했다.  
> 공개 UI/API/추천 champion 변경: `NO`

## 0. 한눈에 보는 권장안

| 결정 | 한 문장 제품 영향 | 선택한 보수적 경계 | local 적용 상태 |
| --- | --- | --- | --- |
| `REC-PD-001` | 숫자 예상 별점은 빠르게 읽히지만 서비스 정수 별점 척도에서 아직 검증되지 않은 확신을 줄 수 있다. | C1 paired calibration과 confidence policy 전에는 숫자를 숨기고 `NOT_COMPUTED`로 유지 | `displayEligible=false` |
| `REC-PD-003` | 최대 입력 수가 늘면 별점 추정 자료는 좋아지지만 가입 전 최소 조작 수와 중도 포기 지점도 늘어난다. | skip 허용, 1~10개에서 종료 가능, K10은 권장 목표이지 강제 minimum 아님 | C4A `DN-C4A-004` 차단 유지 |
| `REC-PD-005` | 파티 정책은 모두의 평균과 가장 불만인 구성원 보호 중 무엇을 우선하는지를 결정한다. | Average를 offline 기준선으로만 유지하고 public party 추천은 실제 파티 evidence 전 비활성 | `party_aggregation` champion `null` |
| `REC-PD-007` | 이유 개수는 설명량을 늘리지만 근거가 약한 이유와 화면 복잡도를 함께 늘릴 수 있다. | faithful typed reason이 있으면 최대 1개, 없으면 이유 영역 숨김 | public reason UI 비활성 |

이 권장안들은 함께 승인할 필요가 없다. 단 `REC-PD-003`은 동일한 제품 의미를 가진 C4A
`DN-C4A-004`와 반드시 한 결정으로 처리해야 한다.

---

## 1. REC-PD-001 — 예상 별점 숫자 표시

**제품 영향 한 문장:** 숫자 예상 별점은 영화 선택을 빠르게 만들 수 있지만, calibration이 맞지 않으면
실제 서비스 사용자가 줄 별점을 이미 안다는 식의 false precision을 만든다.

[예상 별점 표시 vs NOT_COMPUTED screenshot](./evidence/assets/rec-ev-008/stars-1440x1200.png)

### 동일 조건 수치·세그먼트

- 동일 cold-start cohort: 학습에서 완전히 제외한 MovieLens 사용자 3,014명
- 별점 평가: 같은 held-out 167,194 rating rows, 사용자 macro MAE, 1,000회 paired bootstrap
- 순위 평가: selection과 분리한 사용자 1,323명, 동일 sampled candidate

| 입력 K | Macro MAE | K0 대비 개선 | Δ MAE 95% CI | 초기 sampled Rank α | sampled NDCG@10 |
| ---: | ---: | ---: | --- | ---: | ---: |
| 5 | 0.7355 | 2.61% | -0.0197 `[-0.0239,-0.0156]` | 0.0 | 0.4571 |
| 10 | 0.7253 | 3.95% | -0.0298 `[-0.0348,-0.0244]` | 0.0 | 0.4571 |
| 20 | 0.7145 | 5.39% | -0.0407 `[-0.0463,-0.0354]` | 0.0 | 0.4571 |

화면의 `4.2/5`는 표현 비교용 정적 fixture이며 특정 사용자의 실제 모델 예측이 아니다. K10은 사전에
잠근 3% 별점 MAE Gate를 처음 통과했다. 위 표의 순위 값은 REC-EV-003B의 sampled candidate 결과다.

후속 REC-EV-011 full-catalog에서는 K10 alpha 0.2가 Popularity 대비 NDCG@10
`0.004723→0.006154`, candidate recall@500 `0.258503→0.278912`로 개선됐고 NDCG paired CI가
`[0.000253,0.002783]`이었다. 이 결과는 `K10_FULL_CATALOG_OFFLINE_CANDIDATE`이며 public champion,
예상 별점 UI 또는 C1 척도 calibration 승인이 아니다.

### 권장안

`C1 product integer scale paired calibration`과 versioned confidence policy가 통과할 때까지 숫자를
숨긴다. `NOT_COMPUTED`는 빈칸이 아니라 계산·표시 불가 이유를 담고, TMDB 10점 척도·FEELM 평균과
개인 예상값을 절대 같은 label로 합치지 않는다.

### 반대안과 손실

- 반대안: K10부터 `예상 4.x/5`를 노출한다.
- 얻는 것: 직관적인 즉시 보상과 경쟁 서비스와 비슷한 scanability.
- 잃는 것: MovieLens 0.5 척도에서 C1 정수 1~5로 옮기는 paired calibration이 없고, 낮은 confidence
  숫자가 신뢰를 과장할 수 있다.
- 권장안을 택할 때의 손실: 예상 별점이라는 차별 기능을 당장 보여주지 못한다.

### 불확실성·MovieLens 한계

MovieLens 평가자는 서비스 사용자와 다르고, 실제 FEELM integer 평가 쌍·숫자 이해도·선택 행동을
관측하지 않았다. offline MAE 개선은 숫자 UI 만족이나 추천 순위 개선이 아니다.

### 되돌림 비용

지금은 `displayEligible` conditional rendering만 바꾸므로 낮다. 숫자를 공개한 뒤 event·copy·cache
계약을 되돌리면 중간 비용이며 이미 형성된 사용자 기대는 코드 rollback으로 회수할 수 없다.

**선택됨:** `HIDE_NOT_COMPUTED`. C1 paired-scale·confidence evidence 전까지 예상 별점 숫자는 만들거나 표시하지 않는다.

---

## 2. REC-PD-003 — 온보딩 최대 입력 부담

**제품 영향 한 문장:** K10은 별점 추정의 첫 실질 data Gate지만 K5보다 최소 5회, skip보다 최소
10회 더 조작해야 하므로 강제하면 입구 부담이 된다.

[K5 vs K10 + skip screenshot](./evidence/assets/rec-ev-008/onboarding-1440x1200.png)

### 동일 조건 수치·세그먼트

| 안 | 영화 판단 | 완료/skip | 최소 조작 | 같은 cohort 별점 근거 | 순위 근거 |
| --- | ---: | ---: | ---: | --- | --- |
| K5 | 5 | 1 | 6 | MAE 0.7355, K0 대비 2.61% | sampled α=0.0; full-catalog CI borderline |
| K10 | 10 | 1 | 11 | MAE 0.7253, K0 대비 3.95%, 첫 3% Gate | full-catalog α=0.2 offline candidate |
| skip | 0 | 1 | 1 | K0 fallback | Popularity fallback |

조작 수는 LIKE/DISLIKE 1회와 완료/skip 1회만 센 정적 UI lower bound다. 읽기·수정·망설임·시간·이탈은
측정하지 않았다.

### 권장안

- maximum은 10개로 제한한다.
- skip은 첫 화면부터 허용한다.
- 1~10개 어느 지점에서도 완료할 수 있고 K10은 `권장 목표`로만 표현한다.
- K<10은 예상 별점을 계속 숨기되 입력 preference 자체는 version과 함께 저장한다.
- 재수행을 승인한다면 append가 아니라 새 journey version이 이전 preference를 `SUPERSEDED`하는
  replace 방식으로 한다.

### C4A DN-C4A-004 연결

`REC-PD-003`과 `DN-C4A-004`는 온보딩 maximum/minimum/skip/rerun이라는 같은 제품 결정이다.
한쪽만 승인하면 React 진행도, `completeOnboarding`, `restartOnboarding`, ERD invariant가 충돌한다.
제품 소유자가 위 권장안을 선택하면 C4A decision record에서 `maximum=10`, `submittedMinimum=1`,
`skipAtZero=true`, `rerun=VERSIONED_REPLACE`를 같은 변경으로 승인해야 한다. movie selection policy는
별도 versioned 기술 Gate로 남긴다.

### 반대안과 손실

- 반대안: K10을 완료 minimum으로 강제한다.
- 얻는 것: 예상 별점 head의 3% data Gate를 모든 완료 사용자에게 확보한다.
- 잃는 것: K5보다 최소 5회, skip보다 10회 조작이 늘며 실제 완료율 근거가 없다.
- 권장안을 택할 때의 손실: K1~K9 완료 사용자는 예상 별점 numeric Gate를 통과하지 못한다.

### 불확실성·MovieLens 한계

MovieLens에는 signup funnel, 포스터 선택 시간, skip, 수정, onboarding 이탈이 없다. K10의 offline MAE
효과를 실제 서비스 완료율이나 장기 retention으로 바꿔 말할 수 없다.

### 되돌림 비용

maximum·progress copy만 바꾸는 것은 낮다. 저장된 journey를 append semantics로 먼저 배포한 뒤
replace/versioned invariant로 바꾸면 migration과 aggregate 정리가 필요해 중간~높다.

**선택됨:** `OPTIONAL_UP_TO_10_WITH_SKIP`. `DN-C4A-004`와 함께 maximum=10, submittedMinimum=1,
skipAtZero=true, rerun=VERSIONED_REPLACE를 local profile에 적용한다.

---

## 3. REC-PD-005 — 파티 Average vs Balanced

**제품 영향 한 문장:** Balanced를 선택하면 최저 구성원을 보호한다는 메시지를 줄 수 있지만, 현재
근거에서는 Average보다 평균·최저 효용·격차 어느 것도 개선했다고 말할 수 없다.

[Average vs Balanced screenshot](./evidence/assets/rec-ev-008/party-1440x1200.png)

### 동일 조건 수치·세그먼트

- Test 합성 party 270개: 2/3/4명 × 유사/중간/상이 취향, cell당 30개
- 모든 구성원이 실제 평가한 후보에서 Top-3, 개인 rating-style relative utility 사용
- Balanced threshold/weight는 Validation에서 고정한 뒤 Test 평가

| 정책 | 평균 효용 | 최저 효용 | 구성원 격차 | predicted relevance 손실 |
| --- | ---: | ---: | ---: | ---: |
| Average | 0.5610 | 0.3944 | 0.3235 | 0.0000 |
| Balanced | 0.5596 | 0.3949 | 0.3192 | 0.0017 |

| Balanced − Average | 점 추정 | 95% CI |
| --- | ---: | --- |
| 평균 효용 | -0.0013 | `[-0.0037,+0.0007]` |
| 최저 효용 | +0.0005 | `[-0.0035,+0.0045]` |
| 격차 | -0.0042 | `[-0.0116,+0.0024]` |

세 CI가 모두 0을 포함한다. 4인 Test 공통평가 coverage는 취향 cell별 0.69%~1.02%다.

### 권장안

Average를 offline 비교 기준선으로 유지하되 public party 추천은 비활성으로 둔다. Balanced는 실험
후보 이름으로만 보존하고 `PARTY_BALANCED_V1`이나 공정성 개선 문구를 승인하지 않는다.

### 반대안과 손실

- 반대안: Balanced 후보를 public default로 선택한다.
- 얻는 것: 평균·최저·격차를 한 식으로 설명할 수 있는 fairness narrative.
- 잃는 것: 세 개선 CI가 모두 불확실하고 4인 observation bias가 심하며 threshold가 제품 가치 선택을
  암묵적으로 고정한다.
- 권장안을 택할 때의 손실: party 기능 출시가 늦어지고 Average도 실제 만족 policy로 쓸 수 없다.

### 불확실성·MovieLens 한계

MovieLens에는 party 생성, 공동 선택, 함께 감상, 만족도가 없다. 동일 사용자가 여러 합성 party에
재사용되고 공통평가 후보만 보므로 일반 파티·full catalog로 외삽할 수 없다.

### 되돌림 비용

offline policy flag만 보존하는 비용은 낮다. public default, 설명 copy, party history를 특정 policy로
저장한 뒤 되돌리면 재계산·event version·사용자 기대 때문에 높다.

**선택됨:** `KEEP_PARTY_PUBLIC_DISABLED`. Average는 local factual baseline일 뿐 public 추천 champion이 아니다.

---

## 4. REC-PD-007 — 추천 이유 1개 vs 최대 3개

**제품 영향 한 문장:** 최대 3개 이유는 설명량을 늘리지만 각 추천에서 실제로 순위를 바꾼 reason이
동시에 존재하지 않으면 근거처럼 보이는 영화 설명을 채울 위험이 있다.

[reason 1 vs up to 3 screenshot](./evidence/assets/rec-ev-008/reasons-1440x1200.png)

### 동일 조건 수치·세그먼트

- REC-EV-004의 동일 sampled Test recommendation position 40,000개
- exact scoring contribution + single-feature ablation rank/position effect + provenance + non-sensitive Gate

| Typed reason | Positive contribution | Emittable candidate | 차단 핵심 |
| --- | ---: | ---: | --- |
| `POPULARITY_BASELINE` | 100.00% | 99.98% | no rank effect 7건 |
| `LIST_DIVERSITY` | 78.86% | 59.98% | no rank effect 7,554건 |
| `LESS_POPULAR_DISCOVERY` | 100.00% | 24.31% | no rank effect 30,275건 |
| `GENRE_AFFINITY` | 0.00% | 0.00% | active policy 아님 40,000건 |

이 coverage는 reason별 비율이며 한 recommendation에서 3개가 동시에 emittable인 비율이 아니다.

### 권장안

exact policy/provenance에서 `EMITTABLE_CANDIDATE`인 이유가 있으면 정렬된 첫 1개만 보여주고 없으면
이유 영역을 숨긴다. 한국어 copy는 별도 승인 전 실험 문구를 public으로 사용하지 않는다. 최대 3개와
상세 펼치기는 row-level co-occurrence와 이해도 evidence가 생긴 뒤 다시 판단한다.

### 반대안과 손실

- 반대안: 가능한 typed reason을 최대 3개까지 표시한다.
- 얻는 것: 사용자가 추천의 여러 축을 볼 수 있고 설명 부족감을 줄일 가능성.
- 잃는 것: 동시 coverage가 없고 정보 밀도·반복·copy 이해도를 측정하지 않아 세 줄을 채우기 위한
  허위 또는 무관 reason 유인이 생긴다.
- 권장안을 택할 때의 손실: `POPULARITY_BASELINE` 하나가 반복돼 개인화 설명이 단조롭게 보일 수 있다.

### 불확실성·MovieLens 한계

현재 faithfulness는 sampled ranking에서의 score/rank effect다. 사용자가 이 문구를 이해하거나
신뢰했는지, 이유가 클릭·감상·평가에 영향을 줬는지는 관측하지 않았다.

### 되돌림 비용

typed reason과 display limit을 분리하면 1↔3 변경은 낮다. public copy·analytics·reason ordering을
계약에 고정한 뒤에는 중간 비용이며 reason code 의미를 바꾸면 과거 event 해석도 migration해야 한다.

**선택됨:** `SHOW_MAX_ONE_FAITHFUL_REASON`. 다만 현재 C2B local baseline에는 faithful personalized
contribution이 없으므로 reason UI는 계속 숨긴다.

---

## 5. 승인 기록과 재검토 경계

2026-08-30 standalone local 제품 승인에는 `HIDE_NOT_COMPUTED`, `OPTIONAL_UP_TO_10_WITH_SKIP`,
`KEEP_PARTY_PUBLIC_DISABLED`, `SHOW_MAX_ONE_FAITHFUL_REASON`을 포함한다. 이 승인은 local 계약과
구현 경계를 고정할 뿐 production 배포, expected-star 활성화, 개인화 champion, Party public 추천,
운영 provider 권위를 부여하지 않는다. 실제 사용자 evidence나 C1 paired-scale evidence가 생기면 해당
ID의 비교 자료·손실 예산·rollback 조건을 갱신한 뒤 다시 승인한다.
