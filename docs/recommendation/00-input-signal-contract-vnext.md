# FEELM 추천 입력 신호 계약 vNext

> 문서 상태: `APPROVED` — REC-EV-019 이후 오프라인 구현 기준이며 현재 `APPROVED` C2 계약은 변경하지 않는다.
> 개정일: 2026-08-30
> 적용 대상: `REC-EV-019` 이후의 오프라인 실험과 향후 C2 vNext 후보
> 비적용 대상: 현재 서비스의 `APPROVED_C2A_INTERNAL_POPULARITY_ONLY` 경로

## 1. 목적

서비스의 실제 최초 입력은 최대 10개의 `LIKE/DISLIKE`이고, 관람 후 평가는 별도의 1~5점
`Rating`이다. 두 신호를 하나의 “K개 별점”으로 취급하면 실제 제품과 다른 문제를 평가하게 된다.
이 문서는 두 입력을 분리하고 각각 무엇을 학습·평가·표시할 수 있는지 고정한다.

## 2. 입력 원장

| 신호 | Source of truth | 값 | 발생 시점 | 추천에서의 역할 |
| --- | --- | --- | --- | --- |
| 온보딩 선택 | C4 onboarding selection | `LIKE`, `DISLIKE` | 가입·최초 설정, 최대 10편 | 이진 취향 프로필과 cold-user ranking |
| 활성 평가 | C1 Rating | 정수 `1..5` | 사용자가 영화를 평가·수정할 때 | 명시적 선호, 예상 별점, 장기 ranking 후보 |
| 조회·클릭·OTT 이동 | 현재 범위에서 학습 신호 아님 | 이벤트 | 서비스 사용 중 | 저장·학습을 전제하지 않음 |
| MovieLens rating | 외부 연구 데이터 | `0.5..5.0` | 과거 rating timestamp | 사용자 관계 사전 학습과 대리 오프라인 정답 |

MovieLens 사용자는 FEELM 사용자가 아니다. MovieLens rating을 FEELM DB에 사용자 평가로 적재하지
않으며, 오프라인 모델 artifact와 평가 결과만 제품 판단에 사용한다.

## 3. 제품 상태와 K의 의미

`K` 하나로 서로 다른 신호를 표현하지 않는다.

| 기호 | 의미 | 제품 구간 | 연구 구간 |
| --- | --- | --- | --- |
| `K_b` | 온보딩 binary 선택 수 | `0`, `1..4`, `5..9`, `10` | `0`, `5`, `10`이 primary; `1`, `3`은 진단 |
| `K_r` | 활성 1~5점 평가 수 | 실제 누적 평가 수 | `0`, `1`, `3`, `5`, `10`, `20`, `30`, `50` |

- `K_b > 10`은 제품 상태로 표현하지 않는다.
- `K_r = 20/30/50`은 성숙 사용자 연구 구간이며 온보딩 성능으로 발표하지 않는다.
- 화면과 evidence에는 반드시 `K_b`, `K_r`를 따로 기록한다.

## 4. 온보딩 binary 프로필

### 4.1 값과 사용자 벡터

```text
binaryWeight(LIKE)    = +1
binaryWeight(DISLIKE) = -1

onboardingProfile(u)
  = normalize(
      sum_i binaryWeight(action_ui)
            * metadataCoverageWeight(i)
            * itemVector(i)
    )
```

- `LIKE/DISLIKE`를 `4점/1점` 같은 가짜 별점으로 변환하지 않는다.
- 선택하지 않은 영화는 중립·부정 어느 쪽으로도 사용하지 않는다.
- 같은 영화는 payload 안에서 한 번만 허용한다.
- 이후 활성 Rating이 생기면 그 영화의 binary 신호는 결합 단계에서 중복 가산하지 않는다.
- binary 신호만으로 `predictedRating`을 만들지 않는다.

`metadataCoverageWeight(i)`는 누락 metadata 때문에 벡터 크기가 달라지는 현상만 보정한다. 영화의
TMDB 인기도·평점·OTT 제공 여부는 이 가중치에 넣지 않는다.

### 4.2 허용 모델

| 모델 | binary 입력 사용 | 목적 |
| --- | --- | --- |
| TMDB Structured/Text Content | 사용 | 선택 영화와 가까운 후보 생성·ranking |
| Binary ItemKNN | 별도 실험에서 사용 | 공동 LIKE/DISLIKE 관계 기준선 |
| BPR | MovieLens proxy binary로 사전 학습 후 fold-in 가능성 평가 | implicit top-N 기준선 |
| LightFM | binary interaction + TMDB item feature | cold-user/cold-item hybrid 기준선 |
| Explicit ALS | 사용하지 않음 | binary를 rating으로 오인하지 않음 |
| 예상 별점 head | 사용하지 않음 | 숫자 별점 근거 없음 |

## 5. 활성 Rating 프로필

### 5.1 척도 정렬

FEELM의 `1..5`와 MovieLens의 `0.5..5.0`을 원점수로 바로 합치지 않는다. 관측된 사용자 rating 안에서
mid-rank ECDF를 계산하고, 표본이 작으면 MovieLens Train 전체 분포로 수축한다.

```text
n            = 사용자의 현재 rating 수
lambda       = Validation에서 {5, 10, 20} 중 선택
F_user_mid   = 사용자 관측 rating의 mid-rank ECDF
F_global_mid = MovieLens Train rating의 mid-rank ECDF

relativeRating(u, i)
  = n / (n + lambda) * (F_user_mid(r_ui) - 0.5)
  + lambda / (n + lambda) * (F_global_mid(r_ui) - 0.5)
```

- `lambda`는 Test 결과를 보고 고르지 않는다.
- `K_r=1`에서 사용자 ECDF가 항상 0이 되는 문제를 global term이 보완한다.
- 예상 별점은 이 상대 효용을 그대로 1~5로 역변환하지 않고 별도 Bias/Explicit ALS head로 평가한다.

### 5.2 허용 모델

활성 Rating은 Bias, ItemKNN, Explicit ALS fold-in, TMDB content profile의 입력 후보가 된다. 추천 순위
모델의 score와 예상 별점 모델의 출력은 별도로 저장하고 평가한다.

## 6. 두 신호가 함께 있을 때

향후 C2 vNext 후보 정책은 다음 순서를 사용한다.

1. 동일 영화에 활성 Rating이 있으면 ranking profile에서 Rating을 우선하고 onboarding binary를
   중복 가산하지 않는다.
2. binary head와 rating head는 각각 독립적으로 후보와 score를 계산한다.
3. 서로 다른 원점수는 직접 더하지 않는다.
4. 결합은 Validation에서 고정한 `RRF` 또는 validation-only percentile calibration만 허용한다.
5. 한 head가 계산 불가능하면 남은 head와 명시적 fallback으로 결과를 만든다.

기본 결합 기준선은 Reciprocal Rank Fusion이다.

```text
RRF(u, i) = sum_h weight_h / (c + rank_h(u, i))
```

- `c ∈ {10, 30, 60}`과 `weight_h`는 Validation에서만 선택한다.
- 후보에 없는 head는 해당 항의 기여가 0이다.
- 모델별 score min-max 정규화를 Test catalog에 맞춰 다시 계산하지 않는다.

## 7. MovieLens에서 온보딩을 모사하는 방법

MovieLens에는 “화면에 노출된 포스터 중 LIKE/DISLIKE를 선택했다”는 로그가 없다. 따라서 다음 두
proxy를 분리하고 어느 것도 실제 FEELM 만족도라고 부르지 않는다.

### 7.1 `FIRST_OBSERVED_BINARY_PROXY`

1. 목표 사용자의 cutoff 이전 rating만 사용한다.
2. 해당 사용자 pre-cutoff rating의 shrunk ECDF를 계산한다.
3. `relative utility >= +0.15`는 LIKE, `<= -0.15`는 DISLIKE로 변환한다.
4. 중립 구간은 온보딩 선택으로 사용하지 않는다.
5. timestamp 순으로 최초 `K_b`개를 사용하고, 두 class가 모두 존재하는지 별도 coverage로 보고한다.

이 실험은 chronology proxy다. MovieLens rating timestamp가 관람 시각이라는 주장은 하지 않는다.

### 7.2 `CURATED_POOL_BINARY_PROXY`

1. Base Train 데이터와 TMDB metadata만으로 고정 elicitation pool을 만든다.
2. 장르·연대·언어·인기도 구간을 균형화하고 Test 사용자 결과는 pool 생성에 사용하지 않는다.
3. 목표 사용자가 실제로 rating한 pool 영화만 관측 가능 신호로 사용한다.
4. `K_b=5/10`을 충족한 사용자의 비율과 탈락 편향을 반드시 보고한다.

이 실험은 실제 UI에 더 가깝지만 “노출됐는데 선택하지 않음”을 관측하지 못한다. 두 proxy의 결론이
다르면 binary onboarding 모델을 제품 champion으로 채택하지 않는다.

## 8. 입력 계약의 출력 제한

| 상태 | 개인 ranking | 예상 별점 | 설명 |
| --- | --- | --- | --- |
| `K_b=0, K_r=0` | 비개인화 fallback | `null` | “평가 근거 없음” |
| `K_b>0, K_r=0` | binary gate 통과 시만 | `null` | 선택한 영화·feature 기반 이유만 |
| `K_r>0` | rating gate 통과 시만 | 별도 별점 gate 통과 시만 | 실제 기여 feature/model version 포함 |
| 모든 개인화 head 실패 | popularity/catalog fallback | `null` | 개인화 표현 금지 |

## 9. 수용 기준

- [ ] API·manifest가 `K_b`와 `K_r`를 구분한다.
- [ ] binary 입력을 numeric rating으로 변환하는 코드가 없다.
- [ ] 미선택·미평가 영화를 negative로 생성하지 않는다.
- [ ] 동일 영화의 binary/rating 중복 가산을 막는다.
- [ ] binary-only 응답의 `predictedRating`은 항상 `null`이다.
- [ ] MovieLens proxy 종류와 eligible-user coverage가 evidence에 기록된다.
- [ ] Test 사용자는 base model, elicitation pool, threshold 선택에 사용되지 않는다.
- [ ] 현재 승인된 popularity-only 경로는 vNext 승격 전까지 변하지 않는다.

## 10. 추적 근거

- [요구사항 원문](../requirements/00-source.md)
- [요구사항 분해](../requirements/01-decomposition.md)
- [C2 승인 업무 규칙](../c2-recommendation/01-business-rules.md)
- [C4 온보딩 업무 규칙](../c4-membership-onboarding/02-business-rules.md)
- [오프라인 평가 프로토콜 vNext](./01-offline-evaluation-protocol-vnext.md)
- [추천 serving 계약](./serving-contract.md)
