# C2B 로컬 baseline 발견 추천 계약

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`  
> 로컬 baseline 구현: `AUTHORIZED` — main OpenAPI의 조회·append·dismiss 3개 operation만
> 제품 결정: 승인 `1/6`; production activation·개인화·XAI·예상 별점·exposure/action 권위: `NO`

## 목적

이 디렉터리는 C2A 내부 Popularity ranking·Rating input·노출 snapshot을 재사용해 공개 개인·발견
추천으로 확장할 때 필요한 제품 계약을 정규화한다. 사용자는 비개인화 인기 baseline의 로컬
세로 기능 구현만 승인했다. offline 후보를 champion이나 production 공개 승인으로 승격하지 않는다.

현재 고정된 경계는 다음과 같다.

- REC-EV-011의 K10 Fold-in alpha 0.2는 full MovieLens Train-known offline **후보**다. K10은 개인화
  label의 필요조건이지만 충분조건이나 public champion이 아니다.
- K<10과 승인 전 모든 경로의 label은 `인기 기준 추천`이고 ranking은 C2A alpha 0 baseline이다.
- 최초 응답과 각 append delta는 UI_READY 후보가 충분하면 정확히 3편이다. REC-EV-013은 모든 constrained 2+1 후보를
  relevance Gate 실패로 기각했으므로 세 슬롯 모두
  `POPULARITY_BASELINE`; `2 PERSONALIZED + 1 DISCOVERY`라고 표현하지 않는다.
- REC-EV-004B `EXPLORE_05_ON_POPULARITY`는 NDCG@10 상대 손실 약 45.5%로 기각한다.
- 예상 별점은 REC-EV-003C에 따라 `NOT_COMPUTED`; reason UI는 REC-PD-007 승인 전 숨긴다.
- 추천 delivery, 실제 exposure acknowledgement, action, C1 WatchIntent/Viewing/Rating 연결을 서로 다른
  versioned record로 보존한다. 사건 없음은 negative outcome이 아니다.
- exposure와 action의 header/body 이중 멱등성은 정렬된 transaction advisory lock과 하나의
  `REQUIRES_NEW` transaction으로 domain row·안전 replay 결과까지 함께 commit/rollback한다.
- C0 Catalog activation과 C1 평가·감상 write, collection append/exposure final check는 Catalog singleton과
  사용자별 eligibility version row의 고정 lock 순서·typed mapping/catalog snapshot으로 선형화한다.
  같은 item의 action은 0..N개를 보존하되
  단일 projection은 stage, 서버 발생시각, actionEventId 순의 고정 winner 규칙으로만 갱신한다.
- `SafeIssue.code`는 `CANDIDATE_NOT_UI_READY`, `CANDIDATE_ALREADY_RATED`,
  `CANDIDATE_ALREADY_SEEN` 세 값만 허용하고 delivery당 0..3개다. MovieLens↔TMDB model mapping이나
  issue set/rank가 불완전하면 PARTIAL로 축소하지 않고 503 fail-closed한다.
- `DN-C2B-002`는 최초 3편과 요청당 최대 3편의 **누적** load-more를 승인했다. server-side collection의
  기존 active item은 추가 요청·새로고침으로 교체하지 않고, 평가 제출 완료 또는 명시적 `관심 없음`이면
  목록·향후 후보에서 제외한다. 감상만 완료하거나 무반응인 경우는 제거하지 않으며 Catalog invalidation은 별도 안전 상태다.
- 현 C1은 current click behaviorEventId를 반환하지 않으므로 OTT exact attribution은 TASK-C2B-011 전 차단된다.
- original mutation은 201/replayed=false, canonical domain replay는 200/replayed=true이며 event-before-action은
  immutable inbox에서 exact CREATED chain만 deterministic reconcile한다.

## 문서 지도

| 문서 | 역할 |
| --- | --- |
| `00-product-scope.md` | 포함·비범위·공개 차단 경계 |
| `01-glossary-and-policies.md` | label, K, 3-slot, attribution 용어 |
| `02-business-rules.md` | 실행 가능한 업무 규칙 |
| `03-state-machines.md` | delivery·exposure·action·outcome 상태 |
| `decision-needed.md` | 제품/evidence 결정 matrix |
| `product-decision-packet.md` | 6개 제품 결정의 수치·대안·privacy·rollback 권장안 |
| `evidence-dependencies.yaml` | REC-EV-013 v1 기각 결과와 후속 evidence dependency |
| `api/openapi.fragment.yaml` | 로컬 승인 3개 operation과 차단된 확장 2개를 함께 고정하는 원본 fragment |
| `data/*` | C2A 재사용과 추가 draft entity 계약 |
| `ui/*` | React 화면·navigation 계약 초안 |
| `testing/*` | fake fixture와 acceptance |
| `tasks/*`, `traceability/*` | 구현 DAG와 전체 연결 |
| `validate_contract.py` | 누락·조기 승인·REC-EV-013 drift를 fail-closed 검증 |
| `validate_product_decision_packet.py` | 권장안이 evidence/승인 1/6/구현 차단을 유지하는지 검증 |

## 검증

```powershell
npm run c2b:contracts:check
npm run c2b:decisions:check
```

validator PASS는 로컬 baseline 3개 operation의 계약 구현 준비만 뜻한다. production activation,
제품 champion, 개인화/XAI/예상 별점/exposure/action 구현 승인을 뜻하지 않는다.
