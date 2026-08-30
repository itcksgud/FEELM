# REC-EV-003C — MovieLens→C1 rating scale alignment

> 상태: `COMPLETED_FAIL_CLOSED`  
> Test 사용: `NO`  
> DN-C2-008: `BLOCKED_PENDING_C1_PAIRED_VALIDATION`

## 1. 결론

현재 근거로 product-scale adapter를 채택하지 않는다. expected-star는 disabled를 유지하고
Popularity ranking은 그대로 제공한다. REC-EV-003B candidate는 champion이 아니며 숫자 UI도 승인되지 않았다.

MovieLens held-out 실제값은 0.5 간격 `0.5..5.0`이고 C1 입력·결과 계약은 integer `1..5`다.
동일한 held-out 167,194행/3,014명에서 네 옵션을 비교했지만,
C1 actual Rating과 prediction-before-rating이 paired된 행은 없다. 따라서 MovieLens 단위 변환만으로
C1 예상 별점의 MAE·calibration 또는 제품 의미를 검증할 수 없다.

## 2. 사전 선택 기준

1. product output이 `1..5` 범위를 보존한다.
2. 변환은 단조이며, 손실 변환이면 raw model-scale 값을 별도 snapshot한다.
3. clamp/round 같은 unversioned lossy 변환은 채택하지 않는다.
4. held-out C1 integer Rating에서 MAE와 calibration을 같은 row로 평가한다.
5. 숫자 rescale만으로 사용자가 입력한 1~5 Rating 의미가 같다고 주장하지 않는다.
6. adapter version과 source/target scale, fit/eval split, checksum을 artifact로 고정한다.

## 3. 동일 validation 행 비교

| Option | K10 output range | Macro MAE vs ML labels | ECE vs ML labels | Diagnostic macro MAE after same label transform | Invertible | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| AS_IS_0_5_TO_5 | 1.400..4.435 | 0.7253 | 0.0260 | 0.7253 | YES | REJECT_FOR_PRODUCT_STAR_OUTPUT |
| CLAMP_1_TO_5 | 1.400..4.435 | 0.7253 | 0.0260 | 0.7195 | NO | REJECT |
| ROUND_HALF_UP_TO_INTEGER_1_TO_5 | 1.000..4.000 | 0.7261 | 0.2165 | 0.7647 | NO | REJECT |
| AFFINE_0_5_TO_5_INTO_1_TO_5 | 1.800..4.498 | 0.6848 | 0.1714 | 0.6447 | YES | DO_NOT_ADOPT_WITHOUT_C1_PAIRED_VALIDATION |

- 표는 REC-EV-003B의 첫 실질 후보인 K10을 요약한다. machine manifest는 K1/K3/K5/K10/K20을
  모두 포함하며, as-is K1과 K3의 held-out prediction minimum은 실제로 0.5였다.
- `vs ML labels`는 prediction만 바꿔 원래 MovieLens label과 비교한 진단이다.
- `same label transform`은 prediction과 label에 같은 변환을 적용한 수학적 진단일 뿐 C1 검증이 아니다.
- affine의 낮아진 오차는 단위 폭이 `4.5→4.0`으로 줄어든 결과이며 모델 개선이 아니다.
- clamp는 `[0.5,1.0]`을 한 값으로 합치고 round는 calibration resolution을 없애므로 금지 후보다.
- affine은 range·단조성·invertibility를 만족하지만 내부 별점 anchor를 이동시키며 C1 label 근거가 없다.

## 4. 데이터 격차

- 평가 행의 non-integer MovieLens actual 비율: 45.17%
- product minimum 1 미만 actual 비율: 1.83%
- C1 paired integer labels: `NO`
- source prediction SHA-256: `bb3181d9d859c316c2da0421d339fa9205e3b5a2eac6109c91035b172a860727`

필요 artifact는 `c1-product-star-alignment-pairs-v1`이다. 실제 C1에서 rating 전에 저장된 model-scale
prediction과 이후 integer Rating을 leakage 없이 연결하되 userId/movieId/token을 export하지 않는다.
`CALIBRATION`과 이후 시간의 `VALIDATION` split을 모두 포함하고 adapter fit에는 CALIBRATION만 사용한다.

Exporter 입력은 정확히 `prediction_id`, `predicted_at`, `rated_at`, `model_scale_prediction`,
`actual_c1_rating`, `k`, `model_version`, `artifact_set_version`, `policy_version`, `split`만 허용한다.
`user_id`, `movie_id`, MovieLens ID, token은 거부한다. Rating은 prediction 뒤에 발생해야 하고 모든
CALIBRATION Rating 시각은 첫 VALIDATION prediction보다 앞서야 한다. 출력 payload와 sidecar는
canonical JSON과 SHA-256을 사용하며 생성시각을 포함하지 않는다.

```powershell
py -3 -m feelm_recommender export-product-scale-validation `
  --source outputs\c2\joined-product-scale-source.json `
  --payload outputs\c2\c1-product-scale-pairs.json `
  --metadata outputs\c2\c1-product-scale-pairs.metadata.json `
  --dataset-version c1-product-scale-v1
```

## 5. 옵션 판정

| Option | 판정 | 이유 |
| --- | --- | --- |
| 그대로 사용 | Reject for product output | 1 미만 값을 만들 수 있고 C1 척도와 다름 |
| clamp | Reject | 비가역·비엄격 단조, 낮은 값 의미 병합 |
| round | Reject | 비가역·이산화, 예상값과 사용자가 입력한 Rating을 혼동 |
| versioned affine | Hold | invertible하지만 C1 calibration·anchor 의미 근거 없음 |
| C1-label recalibration | Not evaluable | paired held-out C1 label artifact 없음 |
| fail closed | Selected | 근거 없는 UI 숫자와 의미 변환을 만들지 않음 |

## 6. 재현

```powershell
py -3 scripts/recommendation_rating_scale_alignment.py `
  --source-manifest docs/recommendation/evidence/manifests/rec-ev-003b.json `
  --predictions outputs/recommendation-evidence/rec-ev-003b/selected_star_predictions.parquet `
  --manifest docs/recommendation/evidence/manifests/rec-ev-003c.json `
  --evidence docs/recommendation/evidence/REC-EV-003C-rating-scale-alignment.md
```
