# C6 추천 해석 로컬 실험

> 상태: `APPROVED_LOCAL_EXPERIMENT`  
> 제품 노출: `NOT_APPROVED`  
> 운영 준비도: `BLOCKED`

C6는 예상 별점, 개인 기준 기대 효용, 취향 관측 근거를 localhost에서 계산하고 검증하기 위한
실험 slice다. 사용자에게 감정을 물어본 값이나 제품 추천 카드의 확정 기능이 아니다.
현재 상대 효용은 REC-EV-015에서 경계 편향을 교정한
`C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2`를 사용한다.

## 실험 질문

1. MovieLens에서 보정된 예상 별점을 C1 정수 평가와 병치했을 때 오차와 scale mismatch가 어떤가?
2. 절대 4~5점 대신 개인 평점 분포의 상대 위치를 쓰면 사용자별 척도 차이를 더 잘 설명하는가?
3. 장르·국가·감독별 관측 수와 평균을 함께 보여줄 때 취향 설명이 과도한 단정 없이 이해되는가?

## 구현 경계

- React route: `/__experiments/recommendation-interpretation`
- Spring route: `GET /api/v1/me/recommendation-interpretation-experiment`
- FastAPI route: `POST /internal/v1/experiments/recommendation-interpretation`
- local/DEV 명시적 flag와 fake authentication에서만 활성화한다.
- 예상 별점은 `REC-EV-003B` candidate를 실험 모드에서만 사용한다.
- 정상 추천 카드와 C2B ranking은 계속 예상 별점을 사용하지 않는다.

세부 계산과 금지 표현은 [local contract](./local-contract.md), 기존 오프라인 근거는
[REC-EV-002](../recommendation/evidence/REC-EV-002-prediction-calibration.md), 결과 해석 용어는
[추천 결과 효용 자동 추론 계약](../recommendation/outcome-inference-contract.md)을 따른다.
실제 PostgreSQL·Spring·FastAPI·React 연결 결과와 제품 판단 한계는
[REC-EV-014](../recommendation/evidence/REC-EV-014-local-interpretation-lab.md)에 기록했다.
