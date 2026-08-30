# C6 local experiment acceptance tests

> 상태: `APPROVED_LOCAL_EXPERIMENT`

| ID | acceptance criterion | automated target |
| --- | --- | --- |
| AC-C6-001 | flag가 꺼진 FastAPI에는 실험 operation이 노출되지 않는다 | recommender API test |
| AC-C6-002 | fake service bearer가 없거나 다르면 401/403이다 | recommender API test |
| AC-C6-003 | K는 최근 순서에서 검증된 하위 bucket으로 선택된다 | recommender unit/API test |
| AC-C6-004 | K0은 baseline 예상값만 반환하고 상대 효용은 null이다 | recommender API test |
| AC-C6-005 | 상대 효용은 개인 rating 격자의 quantized-midrank ECDF와 일치하고 0~1 범위다 | recommender unit test |
| AC-C6-006 | 모든 prediction의 displayEligible는 false다 | recommender/backend test |
| AC-C6-007 | Spring flag 비활성·비-loopback·미인증 요청은 fail closed된다 | backend test |
| AC-C6-008 | 이미 평가한 영화는 prediction 후보에서 제외된다 | backend PostgreSQL test |
| AC-C6-009 | movie metadata와 모델 snapshot이 같은 catalog 경계에서 결합된다 | backend PostgreSQL test |
| AC-C6-010 | taste evidence는 실제 표본 수·평균·개인 평균 대비 lift를 포함한다 | backend PostgreSQL test |
| AC-C6-011 | 표본 1~2개를 확정 취향으로 승격하지 않는다 | backend/frontend test |
| AC-C6-012 | 응답은 private/no-store이며 원 rating 목록을 노출하지 않는다 | backend API test |
| AC-C6-013 | DEV route는 예상 별점과 개인 기준 기대 효용을 구분해 표시한다 | React test |
| AC-C6-014 | UI는 직접 측정한 만족도가 아니라는 제한을 항상 표시한다 | React test |
| AC-C6-015 | 정상 제품 추천 화면에는 C6 값이 추가되지 않는다 | React regression test |
| AC-C6-016 | 응답은 REC-EV-015로 선택한 utility policy version을 노출하고 미지 버전을 fail closed한다 | recommender/backend test |
