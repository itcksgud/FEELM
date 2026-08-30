# EXP-20260829-005 비교

Validation에서 `floor=0.5`, `floorWeight=0.5`, `gapWeight=0.25`인 Balanced 후보를 선택하고 고정한
뒤, 별도 Test에서 Average·Least Misery·Most Happiness와 비교했다. Balanced의 Average 대비
평균 효용, 최저 효용, 격차 차이는 각각 -0.0013, +0.0005, -0.0042였지만 세 paired-bootstrap
95% CI가 모두 0을 포함했다.

따라서 Balanced는 설명 가능한 비교 후보일 뿐 개선된 정책이 아니다. 특히 모든 구성원이 같은
영화를 평가해야 하는 후보 경계 때문에 4인 Test 평가 가능 coverage가 0.69%~1.02%로 급락했다.
실제 파티 만족도나 일반 파티 정책 성능으로 외삽하지 않는다.
