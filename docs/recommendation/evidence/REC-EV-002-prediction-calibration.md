# REC-EV-002 — Bias·Popularity·ALS와 예상 별점 calibration

> 상태: `COMPLETED`  
> 생성 시각: 2026-08-29T10:20:55.707035+00:00  
> Split: `global-time-v1`  
> Validation 내부 protocol: `validation-forward-half-v1`  
> Test 사용: `NO`

## 1. 결론

Validation 뒤 구간에서 ALS 직접 예측 coverage는 11.74%다. Warm 행의 보정 ALS MAE는 0.6268, 전체 행에서 Bias fallback을 합친 보정 MAE는 0.7345, 보정 Bias MAE는 0.7379였다. 따라서 ALS warm 성능을 전체 사용자 성능으로 확대 해석할 수 없고, 예상 별점 숫자 노출은 cold-start·구간별 confidence 실험 뒤에 결정한다.

- 예상 별점 숫자 제품 UI 결정: `HIDDEN_PENDING_PAIRED_SCALE_EVIDENCE`
- C6 local interpretation lab: `AUTHORIZED_WITH_DISPLAY_ELIGIBLE_FALSE`
- confidence 경계 결정: `WAITING_FOR_COLD_START_AND_ERROR_SEGMENTS`
- 다음 실험: `REC-EV-003 K0~K20 cold-start simulation`

## 2. 누수 방지와 비교 조건

- 모델 학습: REC-EV-001 Train만 사용
- Isotonic 보정 학습: Validation 앞 시간 구간 `< 1573330512`
- 아래 회귀 평가: Validation 뒤 시간 구간 `>= 1573330512`
- 경계 timestamp는 통째로 뒤 구간에 배치
- Test artifact는 CLI 입력도, 실행 입력도 아님
- ALS: rank `32`, regParam `0.1`, maxIter `10`, seed `42`
- ALS가 직접 예측하지 못한 신규 사용자·영화는 Bias fallback으로 별도 표시

## 3. 예상 별점 회귀·보정 결과

| Model | Rows | Coverage | MAE | RMSE | ECE | Within ±0.5 |
| --- | --- | --- | --- | --- | --- | --- |
| global_mean | 1,600,010 | 100.00% | 0.8254 | 1.0594 | 0.0569 | 41.36% |
| popularity | 1,600,010 | 100.00% | 0.7477 | 0.9824 | 0.0311 | 43.53% |
| bias_raw | 1,600,010 | 100.00% | 0.7339 | 0.9717 | 0.0534 | 44.59% |
| bias_isotonic | 1,600,010 | 100.00% | 0.7379 | 0.9701 | 0.0185 | 43.81% |
| als_warm_raw | 187,799 | 11.74% | 0.6351 | 0.8450 | 0.0939 | 50.68% |
| als_warm_isotonic | 187,799 | 11.74% | 0.6268 | 0.8341 | 0.0204 | 51.15% |
| als_bias_fallback_raw | 1,600,010 | 100.00% | 0.7320 | 0.9678 | 0.0304 | 44.57% |
| als_bias_fallback_isotonic | 1,600,010 | 100.00% | 0.7345 | 0.9668 | 0.0266 | 43.88% |

`als_warm_*`는 Train에 사용자와 영화가 모두 존재해 ALS가 직접 예측한 행만 평가한다.
`als_bias_fallback_*`는 신규 상태까지 포함해 100% 응답하는 서비스 형태다. 서로 다른 coverage의
MAE만 보고 모델 우열을 단정하지 않는다.

## 4. 신규 사용자·영화 상태별 결과

| Identity state | Rows | Bias MAE | ALS+fallback MAE | ALS direct coverage |
| --- | --- | --- | --- | --- |
| KNOWN_USER_KNOWN_ITEM | 187,799 | 0.6530 | 0.6372 | 100.00% |
| KNOWN_USER_NEW_ITEM | 53,892 | 0.7171 | 0.7141 | 0.00% |
| NEW_USER_KNOWN_ITEM | 1,275,668 | 0.7401 | 0.7387 | 0.00% |
| NEW_USER_NEW_ITEM | 82,651 | 0.9118 | 0.9049 | 0.00% |

전역 시간 분할에서는 신규 사용자가 다수이므로, ALS 자체 성능과 fallback 품질을 반드시 나눠
본다. 전체 평균 하나만 보고 ALS의 품질이라고 표현하면 안 된다.

## 5. 사용자 rating-style 구간

| Train mean quartile | Rows | Bias MAE | ALS warm MAE | ALS warm ECE |
| --- | --- | --- | --- | --- |
| Q1_LOWER_MEAN | 70,032 | 0.7163 | 0.6849 | 0.0473 |
| Q2 | 44,682 | 0.5849 | 0.5611 | 0.0267 |
| Q3 | 36,298 | 0.6436 | 0.6131 | 0.0331 |
| Q4_HIGHER_MEAN | 36,787 | 0.6243 | 0.6095 | 0.0300 |

공통 raw 4점 threshold 대신 Train 평균 구간별 오차와 calibration을 확인했다. 구간별 오차가
다르면 예상 별점 confidence를 동일하게 노출할 근거가 없다.

## 6. 동일 후보 순위 진단

> 주의: 다음 표는 `SAMPLED_1_POSITIVE_PLUS_99_NEGATIVES`이며 최종 채택용
> `FULL_CATALOG` 지표가 아니다. 모델 탈락과 파이프라인 검증에만 쓴다.

| Model | Users | Coverage | HR@10 | NDCG@10 | MRR | Median rank |
| --- | --- | --- | --- | --- | --- | --- |
| popularity | 3,553 | 100.00% | 0.7107 | 0.4727 | 0.4054 | 4.0 |
| bias | 3,553 | 100.00% | 0.6834 | 0.4106 | 0.3374 | 5.0 |
| als | 3,553 | 100.00% | 0.5598 | 0.2595 | 0.1909 | 9.0 |

각 사용자에서 개인 Train ECDF로 계산한 relative utility 0.7 이상인 최신 Validation 평가 1개와,
Train에 존재하지만 그 사용자가 평가하지 않은 영화 99개를
같은 seed로 비교했다. 미평가 영화를 실제 싫어요라고 주장하지 않는다.

## 7. 비용과 재현

- Bias 학습: 3.40s
- ALS 학습: 91.27s
- 전체 실행: 110.17s
- Spark master: `local[4]`
- Python `3.12.5`, PySpark `4.2.0`, scikit-learn `1.9.0`

```powershell
py -3 scripts/recommendation_baseline_calibration.py `
  --split-dir outputs\recommendation-evidence\global-time-v1 `
  --split-manifest docs\recommendation\evidence\manifests\global-time-v1.json `
  --output-dir outputs\recommendation-evidence\rec-ev-002 `
  --manifest docs\recommendation\evidence\manifests\rec-ev-002.json `
  --evidence docs\recommendation\evidence\REC-EV-002-prediction-calibration.md

py -3 scripts/verify_recommendation_baseline.py `
  --manifest docs\recommendation\evidence\manifests\rec-ev-002.json
```

## 8. 한계

- 하이퍼파라미터 grid search와 3개 seed 비교 전의 첫 기준선이다.
- sampled ranking은 full-catalog 순위를 대체하지 않는다.
- MovieLens의 미래 신규 사용자는 기존 사용자의 cold-start 시뮬레이션과 성격이 다르다.
- 오프라인 예상 별점 오차는 실제 서비스에서 숫자 표현을 이해하는지 증명하지 않는다.
- Test는 최종 후보와 기준을 잠근 뒤에만 한 번 사용한다.
