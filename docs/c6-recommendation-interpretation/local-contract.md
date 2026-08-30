# C6 recommendation interpretation local contract

> 상태: `APPROVED_LOCAL_EXPERIMENT`  
> contract version: `c6-recommendation-interpretation-v2`

## 1. 의미 계약

| 값 | 정의 | 금지 해석 |
| --- | --- | --- |
| `predictedRating` | REC-EV-003B MovieLens 보정 후보의 0.5~5 실험 예측값. C1 정수 척도와의 calibration은 미검증 | 실제 평가, 보장된 별점, C1 제품 척도 |
| `expectedRelativeUtility` | 예측값을 사용자 평점 격자로 정렬한 뒤 동점 중간을 쓴 Laplace-smoothed ECDF | 직접 측정한 만족도, 감정 |
| `tasteEvidence` | dimension별 실제 rating 수·평균·개인 평균 대비 차이 | 성격 진단, 영구 취향 단정 |

`q = round_to_rating_step(predictedRating)`,
`expectedRelativeUtility = (1 + count(rating < q) + 0.5 * count(rating = q)) / (n + 2)`로 계산한다.
C1 local runtime의 rating step은 1.0이며, REC-EV-015 MovieLens 검증은 0.5를 사용했다. 정책 버전은
`C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2`다. rating이 없으면 null이다. 미평가·미클릭·누락 metadata는
부정 신호가 아니다.

## 2. K 선택과 confidence

입력 rating은 Spring이 `updated_at DESC, movie_id`로 보낸다. FastAPI는 가장 최근 항목부터 사용하며
`{0,1,3,5,10,20}` 중 입력 수 이하의 가장 큰 K를 선택한다. 20개를 넘으면 20개까지만 사용한다.
정책 버전은 `C6_MOST_RECENT_VALIDATED_K_FLOOR_V1`이다.

| used K | confidence |
| --- | --- |
| 0 | `INSUFFICIENT_DATA` |
| 1, 3, 5 | `LOW` |
| 10 | `MEDIUM` |
| 20 | `HIGH` |

K bucket 때문에 제외된 rating이 있다는 사실은 snapshot의 `availableRatingCount`와
`usedRatingCount`로 드러내야 한다. 모든 prediction은 `displayEligible=false`다.

## 3. 취향 관측 confidence

dimension별 실제 rating 수로만 confidence를 정한다.

- 0~2: `INSUFFICIENT_DATA`
- 3~4: `LOW`
- 5~9: `MEDIUM`
- 10 이상: `HIGH`

표본이 부족해도 관측 record를 숨기지는 않지만 UI는 수와 confidence를 함께 표시한다.
`liftFromUserMean`은 dimension 평균에서 사용자의 전체 active-rating 평균을 뺀 값이며, 전체 평균이
없으면 null이다.

## 4. 보안·운영 경계

- Spring은 local profile, `C6_LOCAL_ENABLED=true`, loopback 요청에서만 응답한다.
- FastAPI는 `C6_LOCAL_EXPERIMENT_ENABLED=true`와 기존 fake service bearer가 모두 필요하다.
- 응답은 `Cache-Control: private, no-store`다.
- raw token, email, 개인 식별자, 전체 rating row를 응답·로그·증거에 남기지 않는다.
- production origin, 실사용자 만족도, 온라인 A/B 효과를 증명하지 않는다.

## 5. 채택 Gate

제품 추천 카드에 예상 별점 또는 취향 문구를 노출하려면 별도 결정에서 최소 다음을 확인한다.

1. 고정 time split에서 baseline 대비 MAE·ECE와 사용자 rating-style 구간별 편차
2. prediction coverage와 fallback 비율
3. confidence 구간별 실제 오차
4. 표현 이해도 및 오해 가능성 UI 검증
5. 실제 서비스 outcome과의 calibration. 클릭을 만족으로 간주하지 않는다.

v2 정책의 선택 근거는 [REC-EV-015](../recommendation/evidence/REC-EV-015-relative-utility.md)다.
