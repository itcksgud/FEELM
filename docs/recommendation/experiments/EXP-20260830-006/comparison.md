# EXP-20260830-006 comparison

> 상태: `COMPLETED`

v1은 연속 predicted star에 right-inclusive ECDF를 바로 적용했다. v2는 예측을 평점 격자에
quantize하고 동점을 midrank로 처리했다. v2는 K1/3/5/10/20 모두에서 MAE, 절대 bias,
Spearman, 평점 성향 구간 Gate를 통과했다. 세부 수치는 `REC-EV-015-relative-utility.md`를 따른다.

이 비교는 relative-utility 정규화 규칙만 바꾸며 예상 별점 모델·추천 순위·제품 노출을
승인하지 않는다.
