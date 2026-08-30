# REC-EV-003B — Cold-start dual-head blend

> 상태: `COMPLETED`  
> 생성 시각: 2026-08-29T11:44:09.020330+00:00  
> Test 사용: `NO`

## 1. 결론

선택·평가를 분리한 dual-head 실험에서 데이터 조건을 처음 통과한 지점은 K10_DATA_ONLY였다. 별점 head는 K별 Bias와 Fold-in을 혼합했지만, 순위 head는 KEEP_POPULARITY_ALPHA_0_FOR_ALL_K였다. 따라서 K 입력은 예상 별점 신뢰도를 높이는 용도로만 우선 사용하고, 추천 순위 개인화 효과는 콘텐츠 Hybrid나 full-catalog 검증 전까지 주장하지 않는다.

- 데이터 관점 최소 K: `K10_DATA_ONLY`
- 최초 통계적 개선 K: `K1`
- 예상 별점 Gate: `K10_FIRST_PRACTICALLY_SUPPORTED_FOR_STAR_HEAD`
- 추천 순위 Gate: `KEEP_POPULARITY_ALPHA_0_FOR_ALL_K`
- 제품 온보딩 결정: `WAITING_FOR_REACT_INPUT_COST_AND_FULL_CATALOG`

## 2. 선택과 최종 평가를 분리한 방식

예상 별점과 추천 순위에 같은 α를 강제하지 않았다.

- 별점: `(1-α) × K별 Bias + α × Fold-in`
- 순위: `(1-α) × Popularity + α × Fold-in`
- α 후보: [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
- 별점 α 선택: Validation 앞 절반을 다시 시간 분할해 앞 구간에서 Isotonic 학습, 뒤 구간에서
  사용자 macro MAE 최소화
- 별점 최종 평가: 기존 Validation 뒤 절반
- 순위 α 선택: sampled-ranking 사용자의 deterministic 절반
- 순위 최종 평가: α 선택에 쓰지 않은 나머지 사용자

따라서 아래 최종 수치는 α를 고른 행·사용자와 분리돼 있다.

## 3. 선택된 α

| K | Selected star α | Selection Macro MAE | Selected rank α | Selection NDCG@10 |
| --- | --- | --- | --- | --- |
| 1 | 0.1 | 0.7349 | 0.0 | 0.4669 |
| 3 | 0.1 | 0.7313 | 0.0 | 0.4669 |
| 5 | 0.1 | 0.7281 | 0.0 | 0.4669 |
| 10 | 0.3 | 0.7193 | 0.0 | 0.4669 |
| 20 | 0.4 | 0.7088 | 0.0 | 0.4669 |

## 4. 보지 않은 평가 구간 결과

| K | Star α | Macro MAE | Relative gain | Δ MAE vs K0 (95% CI) | Rank α | Eval NDCG@10 | Δ NDCG vs Popularity (95% CI) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.1 | 0.7426 | 1.66% | -0.0125 [-0.0148, -0.0100] | 0.0 | 0.4571 | +0.0000 [+0.0000, +0.0000] |
| 3 | 0.1 | 0.7379 | 2.28% | -0.0172 [-0.0210, -0.0138] | 0.0 | 0.4571 | +0.0000 [+0.0000, +0.0000] |
| 5 | 0.1 | 0.7355 | 2.61% | -0.0197 [-0.0239, -0.0156] | 0.0 | 0.4571 | +0.0000 [+0.0000, +0.0000] |
| 10 | 0.3 | 0.7253 | 3.95% | -0.0298 [-0.0348, -0.0244] | 0.0 | 0.4571 | +0.0000 [+0.0000, +0.0000] |
| 20 | 0.4 | 0.7145 | 5.39% | -0.0407 [-0.0463, -0.0354] | 0.0 | 0.4571 | +0.0000 [+0.0000, +0.0000] |

별점 차이는 `blend MAE - K0 Bias MAE`라서 음수가 개선이다. 순위 차이는
`blend NDCG - K0 Popularity NDCG`라서 음수가 악화다. 같은 사용자 단위 1,000회 bootstrap 95%
신뢰구간을 보고 Gate를 정했다. 통계적 개선만으로 입력 부담을 정당화하지 않으며, Test를 열기
전에 K0 대비 상대 MAE 개선 3% 이상을
실질적 품질 Gate로 잠갔다.

## 5. 해석

별점 head에서 K 입력이 유효하더라도 순위 head의 최적 α가 0이면, 입력한 평가를 추천 순위에
강제로 쓰지 않는다. 이 경우 Fold-in은 예상 별점·설명 신호로만 저장하고 추천 순위는 Popularity와
향후 콘텐츠 Hybrid가 담당한다. “개인화 데이터를 받았으니 반드시 순위를 바꿔야 한다”는 요구는
성능 근거가 아니다.

## 6. 한계

- ranking은 여전히 sampled 후보이며 full-catalog 결과가 아니다.
- α grid는 0.1 간격이고 ALS 하이퍼파라미터는 한 조합이다.
- 사용자 절반 분할은 시간 분할이 아니라 사용자 일반화 검사다.
- 최소 K는 데이터 품질 조건이며 실제 화면 이탈·입력 시간은 포함하지 않는다.

## 7. 재현

- 전체 실행: 6.66s
- Python `3.12.5`, scikit-learn `1.9.0`

```powershell
py -3 scripts/recommendation_cold_start_blend.py `
  --cold-start-manifest docs\recommendation\evidence\manifests\rec-ev-003.json `
  --output-dir outputs\recommendation-evidence\rec-ev-003b `
  --manifest docs\recommendation\evidence\manifests\rec-ev-003b.json `
  --evidence docs\recommendation\evidence\REC-EV-003B-cold-start-blend.md
```
