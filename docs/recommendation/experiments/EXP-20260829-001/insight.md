# EXP-20260829-001 통찰

1. 모델 품질과 예측 가능 범위는 별도 축이다. warm ALS 오차 개선은 11.74% coverage 안의 결과다.
2. 별점 예측이 좋아져도 추천 순위가 자동으로 좋아지지 않는다. 첫 ALS는 sampled ranking에서
   Popularity를 이기지 못했다.
3. Isotonic 보정은 평균 calibration과 개별 MAE에 서로 다른 영향을 준다.
4. 다음 실험은 신규 사용자의 K개 입력을 의도적으로 제한해 fallback과 confidence를 비교해야 한다.

상세 관찰·수치·반례는 `../../insight-log.md`의 INSIGHT-20260829-003~005에 기록했다.
