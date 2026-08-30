# C1 Rating·Film 자동 검증 지도

> 상태: `PASS` — 2026-08-29 실제 PostgreSQL·Compose·Chromium 재검증
> 승인 Slice: `C1_RATING_FILM` in `docs/spec/approved-slices.json`  
> AC mapping: `docs/testing/c1-ac-test-map.csv`

## 사용자 시나리오

| ID | 시나리오 |
| --- | --- |
| `SCN-C1-001` | OTT 이동 전에 WatchIntent를 원자적으로 기록하며 기록 실패 시 외부로 이동하지 않는다. |
| `SCN-C1-002` | 기한이 된 감상 확인에 응답하고 감상 기록만 남기거나 곧바로 평가한다. |
| `SCN-C1-003` | 감상한 영화에 정수 별점을 생성·수정·삭제하고 파생 투영과 집계를 함께 갱신한다. |
| `SCN-C1-004` | Film·Frame·Popcorn·Taste를 현재 활성 평가에서 조회한다. |
| `SCN-C1-005` | 평가 삭제 뒤 감상 사실은 유지하고 다시 평가할 수 있는 목록으로 돌린다. |

## 자동 테스트 묶음

| ID | 상태 | 위치·명령 | 책임 |
| --- | --- | --- | --- |
| `TEST-CONTRACT-C1-001` | PASS | `npm run c1:contracts:check`, `npm run contracts:check`, `npm run openapi:lint` | 11 operation, required auth, 계약 참조와 task dependency |
| `TEST-BE-C1-VIEWING` | PASS | `.\backend\gradlew.bat -p backend clean test` | WatchIntent 재클릭·due·expiry·confirmation·ViewingRecord |
| `TEST-BE-C1-RATING` | PASS | 같은 backend suite | Rating 멱등 생성·수정·soft delete·revision conflict |
| `TEST-BE-C1-DERIVED` | PASS | 같은 backend suite | Frame·Popcorn·Flavor/Taste contribution과 aggregate delta |
| `TEST-BE-C1-READ` | PASS | 같은 backend suite | pending·Film·Frame·Popcorn·Taste와 cursor |
| `TEST-BE-C1-EVENTS` | PASS | 같은 backend suite | 최초 mutation·replay event와 outbox journal |
| `TEST-BE-C1-TRANSACTION` | PASS | `C1MutationRollbackPostgresIntegrationTest` | confirmation/Rating/Popcorn/delete 중간 실패의 row count+canonical hash 전체 rollback |
| `TEST-BE-C1-AUTH` | PASS | 같은 backend suite | bearer 필수, invalid token, cross-owner 404 |
| `TEST-BE-C1-FAILURE` | PASS | `ActiveRatingInputOutboxWorkerPostgresIntegrationTest`와 같은 backend suite | bounded poll·claim 경쟁·retry/dead-letter·outbox 격리와 DB 장애 503 |
| `TEST-SEC-C1` | PASS | `C1SafeLoggingAcceptanceTest` | required-auth·Rating DB 오류 로그의 token·actor·rating body 비노출 |
| `TEST-FE-C1-001` | PASS | `npm run test --prefix frontend` | 8개 화면, mutation header, loading·empty·400·401·404·409·503 |
| `TEST-FE-C1-CONFIRM` | PASS | 같은 frontend suite | pending·watched·지연 평가 화면 |
| `TEST-FE-C1-RATING` | PASS | 같은 frontend suite | 정수 Rating·retry·revision·삭제 화면 |
| `TEST-FE-C1-FILM` | PASS | 같은 frontend suite | Film·Frame·빈 상태 |
| `TEST-FE-C1-BUCKET` | PASS | 같은 frontend suite | Popcorn 8 flavor·raw Taste·접근성 label |
| `TEST-E2E-C1-CORE` | PASS | `npm run test:c1 --prefix e2e` | React→nginx→Spring→PostgreSQL 5개 실제 브라우저 여정 |

## Acceptance→자동 증거 mapping

`docs/testing/c1-ac-test-map.csv`는 `AC-C1-001`~`AC-C1-059`를 한 행씩 갖는다. 59개 모두
`AUTOMATED`이며 실제 source와 고유 locator가 있어 validator가 파일 존재와 locator 포함을 검사한다.
기존 explicit GAP 8개는 `C1ExplicitGapPostgresAcceptanceTest`에서 다음의 서로 다른 실행 증거로
닫았다.

- 숨김 영화와 검증 기한이 지난 offer가 동일한 404를 반환하고 어떤 mutation row도 남기지 않음
- expired intent와 onboarding LIKE/DISLIKE 신호가 Viewing·Rating·aggregate·C2 Rating 입력에 들어가지 않음
- assignment 없는 Rating이 409 전에 Rating·Frame·Popcorn·aggregate·event를 전혀 쓰지 않음
- Frame 3개를 cursor 3 page로 순회해 시각 내림차순과 movie UUID tie-break, 중복·누락 0건을 검증
- active Catalog 교체 뒤 기존 contribution의 catalog version과 Flavor/Taste aggregate 및 C1 응답이 동일함
- 다섯 behavior event와 outbox payload가 event별 exact allowlist이고 token·URL·Rating value가 없음
- `CONFIRMED_NOT_WATCHED`, `EXPIRED` 각각에서 새 intent, 새 48h/7d window, 클릭 event 1건 생성

`AC-C1-018`, `028`은 blind handoff의 `BH-C1-003`에 따라 실제 service checkpoint 실패와
PostgreSQL 전체 상태 count/hash로 검증한다.
축약 주석(`AC-C1-001, 002`)은 증거 권위가 아니며 mapping CSV의 완전한 ID만 추적 기준으로 사용한다.

## E2E 데이터 원칙

- `WI-PENDING`과 `WI-PENDING-E2E`는 서로 다른 영화라 즉시 평가와 지연 평가가 상태를 공유하지 않는다.
- 수정·삭제 시나리오는 기존 `RATING-ONE`만 사용한다.
- C1 mutation 묶음은 사용자 aggregate row 경합을 피하도록 serial로 실행한다.
- DB를 테스트 중간에 초기화하거나 볼륨을 삭제하지 않는다. 로컬 fixture migration이 처음 적용된
  환경에서 before/after delta를 검증하며, CI는 새 Compose project를 사용한다.

실행 결과: backend 전체 72/72, frontend 29/29, C0+C1 Playwright 10/10이며 skip은 0이다.
blind handoff는 새 checkout 재현성까지 별도로 판정한다.
