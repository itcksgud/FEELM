# REC-EV-014 — local recommendation interpretation lab

> 상태: `LOCAL_INTEGRATION_PASS_PRODUCT_DECISION_PENDING`  
> 제품 채택: `NO`

## 목적

REC-EV-002/003B의 예상 별점 후보를 실제 FEELM local rating과 movie UUID 경계에 연결하고,
예상 별점·개인 상대 효용·취향 관측 근거가 같은 화면에서 어떤 의미로 전달되는지 검증한다.

## 사전 근거

- REC-EV-002 전체 ALS+fallback 보정 MAE 0.7345, 직접 ALS coverage 11.74%
- REC-EV-003B는 별점 head candidate이며 ranking champion이 아니다.
- rating-style 구간별 오차가 달라 단일 confidence를 모든 사용자에게 적용할 수 없다.

## 이번 실험이 남길 값

- artifact/policy/input/K-selection version
- available/used rating count와 confidence
- candidate별 calibrated predicted rating
- 개인 ECDF 기반 expected relative utility
- dimension별 표본 수·평균·개인 평균 대비 lift
- local/API/UI acceptance 결과

## 채택하지 않는 주장

- expected relative utility가 사용자의 감정을 직접 측정했다.
- local fixture가 실사용자 만족도나 production 품질을 증명했다.
- 예상 별점이 C2B 제품 카드에 노출 가능하다.

## 2026-08-30 local fixture 실행 결과

| 항목 | 결과 |
| --- | --- |
| 격리 Compose project | `feelm-local-mvp-e2e-20260830030050-13804` |
| 실제 구성 | PostgreSQL + Spring + FastAPI + React + Mailpit |
| 브라우저 결과 | Playwright `1/1 PASS` |
| fixture active rating | 1건, 평균/중앙 4.0 |
| 검증 K·신뢰도 | K1, `LOW` |
| 예측 대상 | 이미 평가한 1편을 제외한 candidate 7편 |
| 취향 근거 | genre/country/director 각 1건, 각 `INSUFFICIENT_DATA` |
| 제품 노출 | 모든 prediction `displayEligible=false` |
| 안전 종료 | 실험 container 0개, 기본 개발 volume 변경 없음 |

실제 controller 경계는 200/401, `Cache-Control: no-store, private`, `Referrer-Policy: no-referrer`를
검증했다. 추천 adapter는 응답 필드, limitation, candidate 전체성, 예상 별점 0.5~5,
상대 효용 0~1, K별 confidence와 item/profile confidence 일치를 fail-closed로 검증한다.

## 이 실행에서 판단할 수 있는 것

- Spring·FastAPI·PostgreSQL·React 간 계약과 로컬 실행 경계는 연결됐다.
- 절대 4~5점 기준 대신 개인 평가 분포상 상대 위치를 별도 값으로 보여줄 수 있다.
- K1이나 dimension 1건처럼 근거가 약한 경우 신뢰도를 낮게 노출해 단정을 막을 수 있다.

다만 1건 fixture는 사용자 이해도·유용성·만족도와 예상 별점 calibration을 판단하는 증거가 아니다.
제품 채택 전에는 C1 규모의 paired time-split 검증과 사용자가 실제로 보고한 이해도·만족도
evidence가 별도로 필요하다.

> 후속 교정: 이 실행의 right-inclusive ECDF v1은 REC-EV-015에서 평점 격자 경계 편향이
> 확인돼 `C6_DISCRETE_QUANTIZED_MIDRANK_ECDF_V2`로 대체됐다. 제품 노출 금지는 그대로다.
