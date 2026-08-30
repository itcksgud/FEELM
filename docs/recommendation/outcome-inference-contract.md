# 추천 결과 효용 자동 추론 계약

> 상태: `APPROVED` — C2 추천 계약의 입력 기준  
> 결정: 사용자에게 별도 만족도 설문을 요구하지 않고 관측 가능한 경향과 행동으로 결과 효용을 추론

## 1. 서로 다른 세 값을 구분한다

| 값 | 시점 | 의미 |
| --- | --- | --- |
| `predictedRating` | 추천 전 | 모델이 예측한 별점. MovieLens 실험값과 C1 제품 척도 calibration은 분리한다 |
| `expectedRelativeUtility` | 추천 전 | 예상 별점을 사용자의 이산 rating 격자에 quantize한 후 midrank ECDF로 바꾼 0~1 상대 위치 |
| `observedRelativeUtility` | 실제 평가 후 | 실제 Rating을 해당 사용자의 평가 전 rating profile로 정규화한 결과 |

이 값은 어느 것도 사용자의 감정을 직접 읽은 `satisfaction`이 아니다. 추천 전 값은 예측이고,
추천 후 값은 관측된 영화 결과를 개인 경향에 맞게 해석한 값이다.

C6 local experiment의 relative-utility 정책은
`C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2`이다. 이 선택은 REC-EV-015의 MovieLens offline
경계 편향 검증에만 근거하며 제품 만족도 주장을 허용하지 않는다.

## 2. 추천 노출과 결과 연결

추천 항목마다 `recommendationItemId`, `recommendationVersion`, `modelVersion`, `inputVersion`,
`position`, `recommendationType`, `exposedAt`을 보존한다. 후속 사건은 가능한 경우 같은
`recommendationItemId`를 전달한다.

```text
EXPOSED
  → DETAIL_OPENED
    → OTT_OPTION_OPENED
      → WATCH_CONFIRMED
        → RATED
```

- 중간 단계를 건너뛸 수 있다.
- 사건이 없다는 이유만으로 `NEGATIVE`를 만들지 않는다.
- 검색 등 다른 경로에서 같은 영화를 다시 만났다면 추천 attribution과 영화 결과를 분리한다.
- 여러 번 노출된 영화는 각 노출을 보존하고 attribution policy version을 기록한다.

## 3. v0 결과 record

| 필드 | 의미 |
| --- | --- |
| `recommendationItemId` | 결과가 연결된 추천 항목 |
| `adoptionStage` | `EXPOSED`, `DETAIL_OPENED`, `OTT_OPTION_OPENED`, `WATCH_CONFIRMED`, `RATED` |
| `predictedRating` | 노출 당시 개인 척도 예상 별점 |
| `expectedRelativeUtility` | 노출 당시 개인 상대 효용 예측 |
| `actualRating` | 실제 Rating, 없으면 null |
| `observedRelativeUtility` | 평가 전 사용자 profile로 정규화한 실제 결과, 없으면 null |
| `predictionError` | actual minus predicted, 없으면 null |
| `attributionStrength` | `DIRECT`, `ASSISTED`, `UNKNOWN` |
| `inferenceConfidence` | `HIGH`, `MEDIUM`, `LOW`, `INSUFFICIENT_DATA` |
| `outcomeVersion` | 정규화·attribution 규칙 버전 |

평가 후 갱신된 사용자 분포로 과거 결과를 다시 계산하지 않는다. Rating 직전의 profile version을
사용해 자기 결과가 정답에 섞이는 leakage를 막는다.

## 4. 자동 판단 원칙

v0에서는 다음 성분을 한 점수로 임의 결합하지 않는다.

- 추천이 선택 과정에 사용됐는가: `adoptionStage`, `attributionStrength`
- 선택한 영화가 개인 경향상 좋은 결과였는가: `observedRelativeUtility`
- 예상 별점이 개인 척도에서 맞았는가: `predictionError`
- 탐험 추천이 새 영역이면서 좋은 결과였는가: discovery distance + observed utility

FEELM 노출 데이터가 충분히 쌓이면 adoption probability와 post-watch relative utility를 별도 모델로
학습한다. 두 모델의 결합식은 offline replay와 시간 분할 online backtest에서 기준선을 이긴 뒤 새
`outcomeVersion`으로만 도입한다.

## 5. 보고 가능한 표현

가능:

- 추천을 통해 상세·OTT·감상 단계로 이어진 비율
- 선택된 추천 영화의 개인 상대 효용 분포
- 예상 별점 오차와 사용자 이력 구간별 confidence
- 탐험 추천의 선택률과 선택 후 상대 효용

불가:

- 클릭했으므로 추천에 만족했다
- 평가하지 않았으므로 추천에 불만족했다
- MovieLens offline 지표가 높으므로 FEELM 만족도가 높다
- 자동 추론값을 설문으로 직접 측정한 만족도처럼 표현한다
