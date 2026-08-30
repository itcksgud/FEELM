# REC-EV-015 — discrete relative-utility policy

> 상태: `COMPLETED_OFFLINE_EVIDENCE`  
> 제품 만족도 주장: `NO`  
> 제품 노출 승인: `NO`

## 결론

MovieLens 0.5 단위의 동점을 상단으로 보내는 v1 right-inclusive ECDF는 연속 예측값이
실제 평점보다 아주 작게 낮아도 해당 동점 전체를 놓쳐 상대 효용을 체계적으로 낮게 추정했다.
예측을 평점 격자에 quantize한 뒤 동점의 중간을 쓰는 v2 midrank ECDF는 K1·K3·K5·K10·K20
모두 잠겨 둔 Gate를 통과했다. 따라서 `ADOPT_C6_LOCAL_EXPERIMENT_V2_KEEP_PRODUCT_BLOCKED`로 기록한다.

이는 사용자 만족도를 측정했다는 뜻이 아니다. 숨겨진 실제 평점을 개인의 이전 평점 분포에서
일관되게 위치시키는 정규화 규칙을 검증한 것이다.

## 프로토콜

- 소스: REC-EV-003B의 선택이 끝난 예상 별점과 REC-EV-003 onboarding history
- 평가: 선택 경계 `1573330512` 이후 `167,194`건, `3,014`명
- MovieLens Test 사용: `NO`
- baseline: `C6_RIGHT_INCLUSIVE_RAW_ECDF_V1`
- candidate: `C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2`
- candidate 공식: `q = round_to_rating_step(prediction)`, `(1 + count(r < q) + 0.5 * count(r = q)) / (n + 2)`
- 검증 격자: `0.5`

## K별 결과

| K | v1 MAE | v2 MAE | v2 개선 | 평균 오차 v1 → v2 | Spearman v1 → v2 | Gate |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 0.071683 | 0.054171 | 24.43% | -0.050909 → -0.019175 | 0.529747 → 0.639479 | PASS |
| 3 | 0.135584 | 0.102909 | 24.10% | -0.100099 → -0.049593 | 0.537896 → 0.637580 | PASS |
| 5 | 0.163124 | 0.125387 | 23.13% | -0.116154 → -0.059394 | 0.523614 → 0.614521 | PASS |
| 10 | 0.191921 | 0.147411 | 23.19% | -0.131773 → -0.061779 | 0.498314 → 0.581074 | PASS |
| 20 | 0.216431 | 0.164265 | 24.10% | -0.151760 → -0.063948 | 0.440511 → 0.516696 | PASS |

Gate는 각 K에서 MAE 15% 이상 개선, 절대 bias 40% 이상 감소, Spearman 비열화,
평점 평균 4분위 모든 구간의 MAE 개선을 동시에 요구했다.

## 한계와 채택 경계

- MovieLens 0.5 척도 결과이며 C1 정수 1~5의 제품 calibration을 증명하지 않는다.
- v2는 C6 local experiment의 상대 효용 표현에만 채택한다.
- C2B 예상 별점·추천 순위·제품 문구를 열지 않는다.
- 미평가는 부정 신호가 아니며, 이 값을 `satisfaction`으로 이름 바꾸지 않는다.
