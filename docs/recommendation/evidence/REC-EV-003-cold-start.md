# REC-EV-003 — K0~K20 leakage-safe cold-start 곡선

> 상태: `COMPLETED`  
> 생성 시각: 2026-08-29T11:38:13.840810+00:00  
> Cohort: `cold-start-cohort-v1` / 3,014명  
> Test 사용: `NO`

## 1. 결론

평가 사용자 3,014명을 학습에서 제외한 결과, 사용자 macro MAE는 K0 0.7552에서 K20 0.7140로 변했고 sampled NDCG@10은 K0 0.4618에서 K20 0.2219로 변했다. 두 지표의 최선에서 모두 0.01 이내인 가장 작은 K는 NO_SINGLE_K_WITHIN_0.01_OF_BOTH_BEST_METRICS다. K10은 K0 대비 예상 별점 MAE가 처음 유의하게 개선된 지점이지만 모든 K의 sampled ranking이 K0 Popularity보다 나빴다. 따라서 Fold-in을 단독 순위로 전환하지 않고 Popularity prior와 혼합하는 후속 실험이 먼저다.

- 데이터 품질 knee: `NO_SINGLE_K_WITHIN_0.01_OF_BOTH_BEST_METRICS`
- 온보딩 최대 입력 수 제품 결정: `BLOCKED_BY_RANKING_MODEL_AND_REACT_INPUT_COST`
- 예상 별점 confidence 결정: `K10_FIRST_SIGNIFICANT_MAE_GAIN_BUT_SEGMENT_REGRESSION`
- 추천 순위 Gate: `FAILS_K0_POPULARITY_BASELINE_AT_ALL_K`
- 다음 실험: `REC-EV-003B popularity-prior blend and ALS tuning`

## 2. 왜 기존 ALS factor를 그대로 쓰지 않았는가

평가 사용자 3,014명을 ALS와 Bias 학습에서 통째로 제외했다. 이들의 전체 Train 이력을
학습한 item factor를 사용한 뒤 K개만 보였다고 주장하면 숨긴 이력이 item factor에 간접 반영된다.
이번 실험은 나머지 사용자 167,477명, 평점
23,646,123개로 item factor를 다시 학습하고, 평가 사용자의 시간상 최초 K개
평점만 Fold-in에 사용했다.

평가 사용자는 모두 Train 이력 20개 이상이며 Validation 앞·뒤 구간에 future rating이 있다.
앞 구간은 K별 Isotonic 보정, 뒤 구간은 최종 곡선에만 사용했다. Test는 읽지 않았다.

## 3. K별 결과

| K | Direct user coverage | Direct row coverage | Macro MAE | Micro MAE | ECE | Sampled NDCG@10 | Profile mean MAE |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.00% | 0.00% | 0.7552 | 0.7285 | 0.0505 | 0.4618 | N/A |
| 1 | 99.93% | 71.25% | 0.7743 | 0.7452 | 0.0281 | 0.1153 | 0.8912 |
| 3 | 100.00% | 71.27% | 0.7596 | 0.7320 | 0.0345 | 0.1555 | 0.6897 |
| 5 | 100.00% | 71.27% | 0.7507 | 0.7256 | 0.0320 | 0.1749 | 0.6036 |
| 10 | 100.00% | 71.27% | 0.7311 | 0.7107 | 0.0323 | 0.1940 | 0.5079 |
| 20 | 100.00% | 71.27% | 0.7140 | 0.6962 | 0.0277 | 0.2219 | 0.4124 |

`Macro MAE`는 사용자를 동일 가중한 예상 별점 오차이고 주 곡선이다. `Micro MAE`는 평가 행을
동일 가중해 활동량이 많은 사용자의 영향이 더 크다. K0는 개인 factor가 없으므로 cohort 제외
Bias/Popularity fallback만 사용한다. K1 이상은 직접 Fold-in이 불가능한 영화에 Bias fallback을
사용해 서비스 coverage 100%를 유지한다.

`Sampled NDCG@10`은 REC-EV-002와 같은 positive 1개+미평가 99개 진단 후보이며 full-catalog
지표가 아니다. 미평가를 실제 싫어요로 간주하지 않는다.

## 4. K0 대비 사용자 단위 paired bootstrap

| K vs K0 | Users | Macro MAE difference | 95% CI |
| --- | --- | --- | --- |
| 1 | 3,014 | +0.0191 | [+0.0143, +0.0243] |
| 3 | 3,014 | +0.0045 | [-0.0003, +0.0094] |
| 5 | 3,014 | -0.0044 | [-0.0098, +0.0004] |
| 10 | 3,014 | -0.0240 | [-0.0293, -0.0186] |
| 20 | 3,014 | -0.0412 | [-0.0468, -0.0358] |

차이는 `K MAE - K0 MAE`다. 음수이면 K 입력이 사용자 macro MAE를 줄였다는 뜻이다. 같은 사용자를
1,000회 bootstrap했으며 Test를 본 뒤 기준을 바꾸지 않았다.

## 5. 사용자 rating-style 구간별 Macro MAE

| Train mean quartile | K0 | K1 | K3 | K5 | K10 | K20 |
| --- | --- | --- | --- | --- | --- | --- |
| Q1_LOWER_MEAN | 0.7260 | 0.7645 | 0.7523 | 0.7480 | 0.7406 | 0.7340 |
| Q2 | 0.6756 | 0.7004 | 0.6937 | 0.6887 | 0.6774 | 0.6688 |
| Q3 | 0.7417 | 0.7572 | 0.7460 | 0.7368 | 0.7115 | 0.6932 |
| Q4_HIGHER_MEAN | 0.9268 | 0.9085 | 0.8753 | 0.8536 | 0.8084 | 0.7628 |

공통 4점 threshold가 아니라 각 사용자의 실제 별점 오차를 비교한다. 특정 quartile만 개선되면
모든 사용자에게 같은 confidence를 표시할 수 없다.

## 6. 판단 범위

판단 가능:

- K가 늘 때 예상 별점·sampled ranking·개인 평균 추정이 실제로 얼마나 변하는지
- 데이터 관점에서 성능 증가가 둔화하는 구간
- K0 fallback과 K개 Fold-in 사이의 coverage·비용 차이

아직 판단 불가:

- 사용자가 K개 입력 화면을 실제로 완료하는 비율과 시간
- full-catalog Top-N에서의 최종 순위 우승 모델
- MovieLens 결과가 실제 FEELM 신규 사용자와 동일한지
- 예상 별점 숫자를 어떤 문구로 보여줄지

## 7. 비용과 재현

- Cohort 제외 ALS 학습: 146.34s
- 전체 실행: 179.69s
- Spark master: `local[4]`
- Python `3.12.5`, PySpark `4.2.0`, scikit-learn `1.9.0`

```powershell
py -3 scripts/recommendation_cold_start_curve.py `
  --split-dir outputs\recommendation-evidence\global-time-v1 `
  --split-manifest docs\recommendation\evidence\manifests\global-time-v1.json `
  --baseline-manifest docs\recommendation\evidence\manifests\rec-ev-002.json `
  --baseline-predictions outputs\recommendation-evidence\rec-ev-002\validation_predictions.parquet `
  --baseline-candidates outputs\recommendation-evidence\rec-ev-002\sampled_ranking_scored.parquet `
  --output-dir outputs\recommendation-evidence\rec-ev-003 `
  --cohort-manifest docs\recommendation\evidence\manifests\cold-start-cohort-v1.json `
  --manifest docs\recommendation\evidence\manifests\rec-ev-003.json `
  --evidence docs\recommendation\evidence\REC-EV-003-cold-start.md
```

## 8. 한계

- ALS rank/regParam은 REC-EV-002 첫 기준선이며 grid·다중 seed 전이다.
- Fold-in은 MovieLens explicit rating만 사용하고 FEELM 행동·콘텐츠 특징은 아직 없다.
- 평가 cohort는 미래 평가가 앞·뒤 구간에 모두 있는 활동 사용자라 전체 신규 가입자를 대표하지
  않는다.
- sampled ranking은 후보 추출 편향이 있으며 최종 채택에 사용하지 않는다.
- 온보딩 UX 비용은 React 비교 화면에서 별도로 판단해야 한다.
