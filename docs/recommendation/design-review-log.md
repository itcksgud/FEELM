# 추천 설계 검토 기록

> 상태: `APPROVED` — 실험 결과가 아닌 제품·평가 설계 교정 이력을 보존한다.

## DR-REC-001 — 공통 4~5점 기준을 추천 성공으로 사용하지 않는다

- 일자: 2026-08-29
- 상태: `ACCEPTED`
- 기존 제안: 추천 노출 후 일정 기간 안에 실제 4~5점 Rating이 연결된 비율을 추천 성공 KPI로 사용
- 검토 의견: MovieLens 사용자와 FEELM 사용자는 평가 기준이 다를 수 있고, 같은 데이터 안에서도
  사용자마다 점수를 후하게 또는 박하게 주는 습관이 다르다. 영화 Rating과 추천 만족도도 같은
  개념이 아니다.
- 결론: 공통 raw threshold는 주 KPI에서 폐기한다.
- 반영:
  - 예상 별점은 해당 사용자의 개인 척도를 예측한다.
  - Top-N은 Train rating 분포 기반 `relativeUtility`로 사용자 안에서 평가한다.
  - 파티는 raw 예상 별점이 아니라 개인별 정규화 효용을 집계한다.
  - 온라인에서는 행동 funnel과 개인별 영화 결과를 연결하되 만족도와 같은 말로 단정하지 않는다.
- 후속 결정: `REC-PD-006`에서 별도 설문 없이 추천 결과 효용을 자동 추론하기로 확정했다.
- 검증 계획: raw threshold 방식과 사용자 정규화 방식의 사용자 구간별 결과를 같은 split에서
  민감도 분석으로 함께 보고, 모델 순위가 바뀌는지 기록한다.

## DR-REC-002 — 추천 만족도 설문 대신 결과 효용을 자동 추론한다

- 일자: 2026-08-29
- 상태: `ACCEPTED`
- 검토 의견: 추천 품질은 사용자의 평점 경향과 실제 행동을 분석한 시스템이 자동으로 판단해야
  하며, 매번 별도 만족도 입력을 요구하면 서비스 부담이 된다.
- 결론: MVP에는 추천 만족도 설문을 추가하지 않는다.
- 산출 개념: `estimatedRecommendationUtility`
- 입력:
  - 사용자별 rating 분포·bias·이력량
  - 추천 노출과 위치·유형·model version
  - 상세 진입·OTT 옵션 확인·감상 확인
  - 실제 Rating과 개인별 `relativeUtility`
- 제한:
  - 클릭만으로 만족했다고 판단하지 않는다.
  - 미선택·미평가는 부정 결과로 단정하지 않는다.
  - MovieLens에는 노출 행동이 없으므로 online utility 모델을 MovieLens만으로 학습했다고 주장하지
    않는다.
  - 자동 추론 결과는 `추천 만족도`가 아니라 `추천 결과 효용 추정치`로 표현한다.
- 구현 계약: `outcome-inference-contract.md`

## DR-REC-003 — 판단 자료 없이 권장안 일괄 승인을 요청하지 않는다

- 일자: 2026-08-29
- 상태: `ACCEPTED`
- 기존 문제: 예상 별점 표시, 온보딩 편수, 파티 공정성, 추천 이유 범위를 비교 결과 없이 권장안
  문장만으로 승인 요청했다.
- 결론: 기존 일괄 승인 요청을 철회한다.
- 새 원칙:
  - LLM이 cold-start 곡선, calibration, Pareto, 파티 사례, React 화면 비교를 먼저 생성한다.
  - 수치·사용자 구간·실패 사례·한계·되돌림 비용이 없는 결정은 `WAITING_FOR_EVIDENCE`다.
  - confidence 경계, 탐험 가중치, Fold-in SLA 같은 기술값은 LLM이 결과로 정하고 보고한다.
  - 소유자는 제품 경험과 공정성의 trade-off만 판단한다.
- 실행 계획: `decision-evidence-plan.md`, `docs/tasks/recommendation-evidence-backlog.yaml`

## DR-REC-004 — 통계적 유의성과 입력 부담을 정당화할 실질 개선을 구분한다

- 일자: 2026-08-29
- 상태: `ACCEPTED_AND_LOCKED_BEFORE_TEST`
- 계기: REC-EV-003B에서 K1 Bias/Fold-in blend가 K0보다 MAE 0.0125, 1.66% 개선됐고 95%
  신뢰구간은 0을 벗어났다.
- 검토 의견: 표본이 3,014명이면 작은 차이도 통계적으로 유의할 수 있다. 이 결과만으로 사용자가
  평가를 입력해야 할 제품 부담을 정당화할 수 없다.
- 결론:
  - 통계적 개선: 사용자 macro MAE 차이의 95% CI 상한 `< 0`.
  - 예상 별점 온보딩 제품 후보: 위 조건과 함께 K0 대비 상대 macro MAE `3% 이상` 개선.
  - 추천 순위: 별도 head와 별도 Gate를 사용하며 sampled 결과로 채택하지 않음.
- 영향: K1은 통계적 관찰로 보존하고, 최초 실질적 데이터 후보는 K10으로 기록한다.
- 잠금: 3% 값은 Test를 열기 전에 고정하며 Test 결과에 맞춰 변경하지 않는다.
- 남은 제품 결정: K10 입력 비용·완료 흐름을 React K5/K10 화면으로 비교한다.

## DR-REC-005 — Top-2와 cold-item protocol을 preflight 전 승인하지 않는다

- 일자: 2026-08-31
- 상태: `ACCEPTED_PROTOCOL_REVISION_REQUIRED`
- 계기: 독립 검증에서 Top-2 v3와 cold-item v1이 `CONDITIONAL GO/NO-GO` 판정을 받았다.
- 확인된 문제:
  - NATURAL-20이 GOOD/BAD 수 조건으로 사용자를 제외해 label-conditioned cohort가 됐다.
  - mid-rank, shuffle, EXTREME tie, ALL_AVAILABLE 공통 분모가 재현 가능하게 고정되지 않았다.
  - 5,000명과 Harm 0.5%p margin의 paired power 근거가 없었다.
  - Base Train q=0만으로는 Router/Validation item 노출을 막지 못했다.
  - q=100 가능한 head item을 하나의 density 곡선으로 연결하고 전역 재학습 간섭을 숨길 위험이 있었다.
- 결론:
  - Top-2 primary를 label 수로 제외하지 않는 `NATURAL_ALL`로 바꾸고 opportunity별 분모를 공개한다.
  - GOOD/BAD label-rich와 EXTREME-20은 diagnostic으로만 유지한다.
  - mid-rank·hash shuffle·common cohort·CVaR·full-catalog 분모를 v4에 고정한다.
  - provisional margin은 Validation paired power를 통과하기 전 승인값이 아니다.
  - 영화도 item Train/Validation/Locked Test로 분리하고 strict Q0 firewall을 둔다.
  - density masking은 `PANEL_5P/20P/100P`와 5-fold item cross-fit으로 나누고 control drift를 측정한다.
  - Top-2 v4와 cold-item v2 상태를 `PROPOSED_PROTOCOL_VALIDATION_PREFLIGHT_REQUIRED`로 둔다.
- 후속 Gate:
  - `REC-EV-020P-A/B` artifact contract·runner·unit·verifier·Validation power
  - `REC-EV-021P` item firewall·panel 표본·계산량 preflight
  - 위 Gate 전에는 Locked Test와 champion 교체 금지

## DR-REC-006 — preflight 구현 전에 endpoint·분모·방화벽을 기계적으로 닫는다

- 일자: 2026-08-31
- 상태: `ACCEPTED_CONTRACT_IMPLEMENTATION_REQUIRED`
- 계기: v4/v2 재검증에서 방향은 conditional GO였지만 서로 다른 구현자가 같은 통계량을 만들 수 없는
  정의가 남았다.
- 반영:
  - hash 정수·UTF-8 직렬화와 EXTREME score percentile을 수식으로 고정했다.
  - 조건부 지표는 사용자별 eligible-seed macro로 집계하고 zero-opportunity를 `NULL`로 고정했다.
  - binary NDCG gain·IDCG와 full-catalog known GOOD의 입력 제외 전후 집합을 고정했다.
  - `020P-B` baseline을 현재 승인 popularity policy, challenger를 사전 잠금 단일 artifact로 정했다.
  - Harm NI와 Miss superiority의 delta 방향·H0/HA·power target alternative를 분리했다.
  - model-dependent fallback segment를 core Gate에서 제거하고 CVaR CI를 bootstrap 안에서 재계산한다.
  - Item Validation까지 포함한 item role × user role allowed-use matrix를 추가했다.
  - cold q 결과는 mixed NATURAL slate 귀속과 target-fold 귀속을 구분하고 가중 Utility를 폐기했다.
  - transition은 target GoodHit simultaneous CI, Harm NI, non-target control equivalence를 모두 요구한다.
  - 사람 pilot의 평가 단위·distance·UNKNOWN coverage와 Validation/Test query·rater 분리를 고정했다.
- 실행 경계:
  - 설계 수정만으로 Locked Test를 열지 않는다.
  - v4/v2 Schema·artifact contract·runner·golden fixture·verifier·checksum을 구현한 뒤 Validation-only
    `REC-EV-020P-A/B`, `REC-EV-021P`를 통과해야 한다.

## DR-REC-007 — 결측 alpha와 비선형 request bootstrap을 명시적으로 정의한다

- 일자: 2026-09-01
- 상태: `ACCEPTED_CONTRACT_READY`
- 계기: 세 번째 독립 감사에서 BLOCKER는 없었지만, 결측 평가자가 섞인 사람 alpha와 여러 영화가
  관여하는 Top-2 OR event의 item bootstrap이 구현자마다 달라질 수 있음이 확인됐다.
- 교정:
  - 사람 alpha의 observed disagreement를 unit별 `2/(n_u-1)` coincidence weighting으로 수정하고,
    `n_u<2` unit은 `Do`, category count, `N` 모두에서 제외한다.
  - 일반 ordinal mode와 구분하여 distance를 `CUSTOM_SQUARED_RANK`로 명명하고 golden fixture를 요구한다.
  - request-level endpoint는 label-free membership item에 event를 같은 몫으로 전개한
    `user_weight × mean(item_weight)` multiplier bootstrap을 사용한다.
  - Target Bad Exposure는 TargetBadOpportunity 조건부 지표로 고정한다.
  - Miss power는 분석 가능 사용자 n과 구조적 Test n을 분리하고 Validation Wilson lower rate로 환산한다.
  - bootstrap hash weight, inverse Poisson, valid attempt, nearest-rank quantile을 공통 canonical contract로
    고정한다.
  - core segment Feature 수식·missing·tertile 경계와 실제 배포 state별 hypothesis ID registry를 고정한다.
  - 사람 모델 Gate는 Hybrid-Structured와 Hybrid-Text query-macro NDCG@5 Holm family로 고정한다.
- 경계: v4/v2를 입력으로 Schema·artifact contract·runner 구현을 시작할 수 있지만, 실제 Validation
  preflight·Locked Test·champion 승격은 여전히 별도 Gate다.

## DR-REC-008 — human NDCG gain·discount를 구현자 선택으로 남기지 않는다

- 일자: 2026-09-01
- 상태: `ACCEPTED_DESIGN_REVIEW_CLOSED`
- 계기: 최종 감사에서 human NDCG@5의 relevance와 common IDCG는 있었지만 graded gain 함수가 빠진
  HIGH 1건이 발견됐다.
- 결론:
  - relevance가 이미 `mean(0/1/2)/2`의 0~1 척도이므로 `G(rel)=rel` 선형 gain을 사용한다.
  - discount는 `1/log2(rank+1)`, common IDCG도 같은 gain·discount를 사용한다.
  - query macro는 두 Gate contrast가 모두 non-NULL인 동일 family-valid query의 산술평균이다.
  - 지수 gain은 금지하고 binary64·`1e-12` golden fixture를 v2 계약에 둔다.
- 경계: 추천 평가 설계 재검토를 종료하고 v4/v2 Schema·artifact contract·runner 구현으로 넘어간다.
  Validation 실행·Locked Test·champion 교체는 구현 및 기존 Gate 전까지 계속 금지한다.

## DR-REC-009 — density 역할은 strict ITEM_TRAIN 안에서만 나눈다

- 일자: 2026-09-01
- 상태: `ACCEPTED_AFTER_VALIDATION_PREFLIGHT`
- 계기: REC-EV-021P 최초 실행에서 독립 strict item split과 density split의 교차표를 계산했다.
- 발견:
  - Density Validation 17,416편 중 6,964편이 strict ITEM_VALIDATION 또는 ITEM_LOCKED_TEST였다.
  - density q를 만들기 위해 이 영화의 Base Train interaction을 읽으면 strict cold firewall을 우회한다.
- 교정:
  - density 역할은 strict `ITEM_TRAIN` 영화에만 부여한다.
  - strict Validation/Test 영화는 `DENSITY_OUT_OF_SCOPE`로 고정한다.
  - 별도 density salt는 ITEM_TRAIN 내부 Train/Validation/Locked Test 분리에만 사용한다.
- 재검증:
  - protected Density Validation collision `6,964 → 0`.
  - q≥5/20/100 안전 panel은 각각 3,662/1,963/994편이다.
  - 5-fold 최소 영화 수는 PANEL_100P에서도 191편이다.
- 남은 경계:
  - REC-EV-019B TMDB feature manifest가 없으므로 content/cold 모델 실행은 BLOCKED다.
  - REC-EV-020P-B 비교 예측 artifact가 없으므로 개인화 champion은 선택하지 않는다.
