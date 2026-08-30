# EXP-20260830-006 insight

> 상태: `ADOPTED_LOCAL_EXPERIMENT_ONLY`

예상 별점의 작은 소수점 차이가 이산 평점 분포의 동점 전체를 건너뛰는 문제를 확인했다.
이는 모델 오차가 아니라 효용 변환 규칙의 경계 문제였다. 격자 quantization과 midrank를 C6에만
채택했다. MovieLens offline 결과를 만족도로 재명명하지 않는다.
