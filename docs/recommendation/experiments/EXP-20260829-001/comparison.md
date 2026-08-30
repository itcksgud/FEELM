# EXP-20260829-001 비교

## 결과

- Warm 예상 별점: 보정 ALS MAE 0.6268로 보정 Bias 0.6530보다 낮았다.
- 전체 미래 행: ALS 직접 coverage 11.74%, Bias fallback 결합 보정 MAE 0.7345였다.
- Sampled 순위: NDCG@10 Popularity 0.4727 > Bias 0.4106 > ALS 0.2595였다.
- Calibration: ALS는 MAE와 ECE가 함께 개선됐지만 Bias는 ECE 개선과 MAE 소폭 악화가 교환됐다.

## 결정

ALS를 예상 별점·개인 순위 champion으로 채택하지 않는다. Bias를 fallback 기준선으로 유지하고,
K0~K20 cold-start, full-catalog ranking, ALS grid와 다중 seed를 실행한 뒤 재검토한다. 예상 별점
숫자 UI도 아직 승인 요청하지 않는다.

## 해석 제한

sampled 후보의 미평가 영화는 실제 부정 정답이 아니며 Popularity에 유리한 노출 편향이 섞일 수
있다. 반대로 warm 예상 별점 결과를 신규 사용자에게 일반화할 수도 없다.
