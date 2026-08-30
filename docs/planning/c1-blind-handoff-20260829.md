# C1 Rating·Film 독립 blind handoff 감사

> 감사일: 2026-08-29  
> 감사 범위: `TASK-C1-011`  
> 감사 방식: 기존 구현 설명을 신뢰하지 않고 승인 계약·명령·테스트 XML·실행 Compose·기존 Playwright 산출물·읽기 전용 DB/API로 재검증  
> 초기 판정: **83/100, NO-GO — 현재 workspace 기능 PASS / 독립 handoff와 계약 승격 FAIL**  
> 2026-08-29 후속 판정: **93/100, NO-GO — 계약·rollback·outbox·운영 보완 / revision Gate 잔존**
> 2026-08-30 재검증: **로컬 기능 PASS — 59/59 AC 자동화, backend 72/72, React 29/29, Playwright 10/10 / revision Gate 잔존**

## 1. 최종 판정

C1의 현재 로컬 기능 경로는 동작한다. 공통 OpenAPI의 C1 11개 operation, Spring/PostgreSQL 수직
경로, React 8개 화면, 실제 Compose와 Chromium E2E, 인증·소유권, 멱등성·동시성, 현재 DB의
Frame·Popcorn·aggregate 불변식은 감사 시점에 모두 유효했다.

그러나 `GO`로 판정할 수 없다.

1. `HEAD=origin/main=e2768cd`에는 C1 관련 추적 파일이 **0개**다. 저장소 전체 tracked file은 10개,
   untracked file은 298개여서 새 clone/worktree에는 구현·계약·검증기가 존재하지 않는다.
2. 최상위 승인 제품 범위 `docs/spec/00-product-scope.md`는 여전히 “현재 승인 구현 단위는 C0”이며
   WatchIntent·감상 확인·평가를 명시적으로 제외한다. 반면 `docs/api/openapi.yaml`과
   `docs/c1-draft/**`는 C1을 승인·완료로 선언한다. 새 작업자가 어느 승인 제품 범위를 따라야 하는지
   specification authority만으로 결정할 수 없다.
3. `docs/testing/c1-automated-tests.md`는 transaction rollback integration을 `PASS`로 표시하지만,
   실제 suite에는 confirmation 내부 실패, `FAIL-AFTER-RATING`, `FAIL-AFTER-POPCORN`, delete aggregate
   역산 실패를 주입해 전체 상태 보존을 검증하는 service-level test가 없다. 현재 generic journal
   transaction rollback과 정상 lifecycle 원자성만 검증한다.

따라서 이 점수는 “현재 PC에서 잘 실행된다”는 점수이지, 다른 세션·새 checkout이 C1을 독립적으로
인수할 수 있다는 점수가 아니다.

## 2. 점수

| 항목 | 점수 | 독립 근거 |
| --- | ---: | --- |
| 계약·추적성 | 17/20 | C1 validator 11 operation·8 screen·52 rule·59 AC·12 trace row·11 task PASS, 공통 validator와 OpenAPI lint PASS. 다만 공통 승인 scope/UI/data/trace는 C0 상태라 C1 승인 문서와 충돌 |
| Backend 기능 정확성 | 20/20 | PostgreSQL clean suite 35/35, 실패·오류·skip 0. WatchIntent, confirmation, Rating lifecycle, 조회, scheduler, cursor, 503가 실제 PostgreSQL test에 존재 |
| 원자성·멱등성·동시성·불변식 | 15/20 | 같은 key replay/다른 body 409, 서로 다른 key의 동시 클릭 직렬화, revision conflict, delete 역산, outbox retry test와 현재 DB 불변식 PASS. 실제 중간 실패 injection 3종이 없고 runtime outbox worker가 연결되지 않음 |
| Frontend·실제 E2E | 20/20 | React 22/22, OpenAPI type 재생성+TypeScript+Vite build PASS. Playwright 내장 report 직접 해석 결과 C0 5+C1 5, unexpected/flaky/skipped 0 |
| 보안·장애 격리 | 9/10 | 11 operation required bearer test, 실제 API 401/401/cross-owner 404, secret pattern 0, root/frontend/e2e audit high 이상 0. 문서가 주장하는 runtime safe-log capture test는 없음 |
| 독립 재현성·운영 문서 | 2/10 | Compose config/health와 기존 report는 유효. 그러나 C1 tracked file 0으로 새 checkout 불가, Windows Runbook backend 명령 1개가 실제 경로와 불일치 |
| **합계** | **83/100** | 통과 기준 85 미달이며, revision·승인 명세 충돌은 점수와 무관한 hard NO-GO gate |

## 3. 실행·산출물 검증

### 3.1 계약

| 명령 | 결과 |
| --- | --- |
| `npm run c1:contracts:check` | PASS — 11 operations, 8 screens, 52 rules, 59 AC, 12 trace rows, 11 tasks |
| `npm run contracts:check` | PASS — 공통 C0 18 operations, 5 screens, 50 AC, 9 trace rows, 13 tasks |
| `npm run openapi:lint` | PASS — warning/error 없음 |
| `docker compose config --quiet` | PASS |

두 validator가 동시에 통과해도 공통 validator는 C1 screen·entity·trace를 검사하지 않는다. 공통 결과의
`5 screens`와 C1 결과의 `8 screens`가 분리된 채이며, 이것이 계약 승격을 증명하지는 않는다.

### 3.2 Backend

Runbook에 적힌 루트 명령 `./gradlew.bat -p backend test`는 wrapper가 루트에 없어 실행되지 않았다.
실제 재현 명령은 다음과 같았다.

```powershell
.\backend\gradlew.bat -p backend clean test
```

감사 시점 XML 합계:

```text
tests=35, failures=0, errors=0, skipped=0
C1VerticalPostgresAcceptanceTest=6
C1FoundationPostgresIntegrationTest=5
C1RequiredAuthAcceptanceTest=2
C1UnavailableApiTest=1
기타 C0/importer=21
```

이 clean test는 다른 agent의 후속 backend 작업과 겹치지 않도록 메시지 수신 뒤 반복하지 않았다.

### 3.3 Frontend

| 명령 | 결과 |
| --- | --- |
| `npm run test --prefix frontend` | PASS — C0 10+C1 12, 합계 22/22 |
| `npm run build --prefix frontend` | PASS — 공통 `docs/api/openapi.yaml`에서 단일 schema 재생성, `tsc -b`, Vite production build |

OTT 버튼은 `createWatchIntent`가 성공한 뒤에만 server destination의 `http/https` URL로
`location.assign`한다. 실패 시 이동하지 않고, 같은 offer retry는 동일 Idempotency-Key를 사용한다.
RTL/MSW test가 성공 destination과 실패 시 무이동을 각각 고정한다.

### 3.4 Compose와 실제 API

감사 시점 `postgres`, `backend`, `frontend`는 모두 `healthy`였고:

```text
GET :8080/actuator/health = 200 {status: UP}
GET :5173/me/film = 200
owner GET /api/v1/me/film = 200
missing bearer GET /api/v1/me/film = 401
invalid bearer GET /api/v1/me/film = 401
other bearer GET owner frame = 404
```

현재 owner projection은 Film 2, active Rating 2, unrated ViewingRecord 2, Popcorn 2, flavor reference
8개였다. 응답·오류에 다른 사용자의 상태를 노출하지 않았다.

### 3.5 기존 Playwright 결과

mutation fixture가 이미 소비되었으므로 E2E를 다시 실행하거나 DB를 reset/delete하지 않았다.
대신 `e2e/playwright-report/index.html`에 내장된 ZIP의 `report.json`과
`e2e/test-results/.last-run.json`을 읽기 전용으로 해석했다.

```text
report timestamp = 2026-08-29 23:12:41 +09:00
total=10, expected=10, unexpected=0, flaky=0, skipped=0, ok=true
c1-rating-film.spec.ts=5/5
catalog.spec.ts=5/5
.last-run.status=passed, failedTests=[]
report SHA-256=D99A0FD0DB78A00F3199D8EFCDCFDF3C9EED859C03CBDFAE68B94735940F6322
```

### 3.6 현재 DB 불변식 — 읽기 전용

`BEGIN TRANSACTION READ ONLY`에서 재계산했다.

| 검사 | 결과 |
| --- | ---: |
| active Rating / Frame / Popcorn | 2 / 2 / 2 |
| orphan Frame / Popcorn | 0 / 0 |
| FlavorAggregate 재계산 불일치 | 0 |
| TasteAggregate 재계산 불일치 | 0 |
| active/deleted Rating↔Viewing↔projection 상태 불일치 | 0 |
| Rating↔Viewing owner/movie 불일치 | 0 |
| user/movie active WatchIntent 중복 | 0 |
| UI_READY 영화 active flavor assignment 불일치 | 0 |
| behavior event 금지 payload key | 0 |
| Rating 상태 | ACTIVE 2, DELETED 1 |
| idempotency record | 6 |
| outbox | PENDING 6, attempt_count 0 |

삭제된 Rating 한 건은 ViewingRecord가 `WATCHED_CONFIRMED`로 남고 Frame·Popcorn·Taste contribution은
없는 상태라 승인된 delete 불변식을 만족했다.

## 4. 초기 상세 감사 snapshot

이 절은 최초 83점 판정 시점의 관찰을 보존한다. 이후 닫힌 항목의 현재 상태는 7절이 우선한다.

### 4.1 인증·소유권

- `C1RequiredAuthFilter`는 공통 OpenAPI의 C1 11개 operation과 같은 method/path를 required bearer로
  보호한다.
- missing·invalid fake bearer는 모든 operation에 대해 401을 검증한다.
- actor는 request body/path가 아니라 인증 resolver에서만 결정된다.
- service query는 actor 조건을 사용하고, 실제 owner Frame에 other token을 사용한 결과 404였다.
- local fake token은 실제 credential이 아니며, C1 범위가 실제 OAuth/JWT adapter를 포함한다고
  표현하지 않았다.

### 4.2 멱등성·race

- idempotency primary key는 `(actor_user_id, operation_code, idempotency_key)`다.
- request JSON canonicalization 뒤 SHA-256을 비교하고, 같은 요청은 저장된 status/body를 replay한다.
- 다른 body 재사용은 409 `IDEMPOTENCY_KEY_REUSED`다.
- key lock과 별도로 actor/resource advisory transaction lock이 있어 다른 key의 같은 user/movie 클릭을
  직렬화한다. PostgreSQL 동시 test가 201/200, WatchIntent 1개, 실제 클릭 event 2개를 확인한다.
- Rating mutation은 user/movie domain lock과 `expectedRevision`을 함께 사용한다.

### 4.3 Transaction·outbox

- mutation supplier, behavior event, outbox, idempotency response는 `TransactionTemplate` 내부에서 실행된다.
- 정상 create/update/delete의 projection identity와 aggregate delta는 실제 PostgreSQL test와 현재 DB에서
  일치한다.
- generic `FAIL_AFTER_JOURNAL` rollback test는 event/outbox/idempotency가 함께 rollback되는 것을 확인한다.
- 그러나 AC-C1-018/028의 구체적인 confirmation/Rating/delete 중간 실패는 주입하지 않는다. 정상 경로와
  generic transaction test를 해당 AC 전체의 `PASS`로 확장해서는 안 된다.
- `C1OutboxDispatcher.dispatchOne`은 retry/backoff/savepoint와 eventId consumer dedup 계약을 테스트하지만,
  main runtime에서 PENDING row를 선택해 consumer에 전달하는 scheduler/worker 호출자가 없다. 실제 local
  DB의 outbox 6건도 모두 PENDING, attempt 0이다. 추천 갱신은 C1 쓰기를 막지 않지만
  `TASK-C1-007 DONE`의 “consumer” 완료 주장은 범위를 “dispatcher component”로 좁히거나 worker를 연결해야 한다.

### 4.4 비밀·의존성

- JWT/TMDB token, AWS key, GitHub/GitLab token pattern file match: 0.
- `.env.local`은 존재하지만 `.gitignore`의 `.env.*` 규칙으로 ignore되고 값을 출력하지 않았다.
- Playwright report/test-results, frontend dist, backend build도 ignore됨을 `git check-ignore`로 확인했다.
- root/frontend/e2e `npm audit --audit-level=high`: 모두 vulnerabilities 0.
- behavior payload에는 authorization/token/email/destination URL/free text/rating value 금지 key가 0건이었다.
- runtime log를 캡처해 token·userId·rating value 부재를 assert하는 자동 test는 찾지 못했다.

## 5. 초기 차단·개선사항

아래 표는 최초 감사 시점의 목록이다. 후속 `CLOSED` 여부와 남은 Gate는 7절이 우선한다.

| ID | 심각도 | 문제 | 완료 조건 |
| --- | --- | --- | --- |
| `BH-C1-001` | **BLOCKER** | C1 구현·계약·테스트가 모두 untracked이며 origin/main과 HEAD에 없음 | 사용자 승인 하에 검토 가능한 commit/revision으로 고정하고 새 clone/worktree에서 validator, backend, frontend, Compose, E2E를 재실행 |
| `BH-C1-002` | **BLOCKER** | 공통 승인 `docs/spec`은 C1을 제외하지만 공통 OpenAPI와 `docs/c1-draft`는 C1 승인 | C1 product scope·rules·state·UI·ERD/data·fixture·AC·trace를 공통 계약에 승격하거나, 공통 문서를 SUPERSEDED/명시적 slice index로 연결하고 validator가 충돌을 실패시킴 |
| `BH-C1-003` | **HIGH** | AC-C1-018/028 service-level 중간 실패 rollback test 없이 `TEST-BE-C1-FAILURE=PASS` | confirmation update 후, Rating/Popcorn 중간, delete aggregate 역산 중간 실패를 각각 주입하고 이전 상태 전체 hash/count 불변을 PostgreSQL integration test로 검증 |
| `BH-C1-004` | **HIGH** | runtime outbox worker 연결 없음; 현재 6건 모두 PENDING/attempt 0 | dispatcher polling/claim worker와 실제 consumer를 연결하거나 C1 완료 범위를 dispatcher component까지만 정정하고 C2 dependency로 명시 |
| `BH-C1-005` | **MEDIUM** | Runbook Windows backend 명령이 실제 wrapper 위치와 다름 | `./backend/gradlew.bat -p backend test` 또는 `cd backend; ./gradlew.bat test`로 수정하고 새 shell에서 검증 |
| `BH-C1-006` | **MEDIUM** | 문서의 safe-log PASS에 대응하는 runtime log assertion 없음 | 인증 실패·DB 실패·Rating mutation 로그를 capture하여 Authorization, email, raw external body, userId/rating value가 message·metric label에 없음을 자동 검증 |
| `BH-C1-007` | **LOW** | backend test의 AC 주석은 첫 ID만 `AC-C1-` prefix를 쓰고 나머지는 숫자만 써 기계 추적이 약함 | 각 test 또는 자동 검증 지도에 실제 AC ID를 완전한 형태로 연결하고 미커버 AC를 validator가 실패시킴 |

## 6. 초기 GO 재판정 Gate

다음 순서가 모두 끝나야 `GO`를 재검토한다.

1. `BH-C1-001`, `BH-C1-002`를 닫아 새 checkout에서 계약 권위와 파일 revision을 확보한다.
2. `BH-C1-003` 실제 mutation rollback failure injection을 추가한다.
3. Runbook 명령을 수정하고 빈 shell에서 `npm ci`부터 재현한다.
4. 새 Compose project/DB에서 `npm run verify`와 C0+C1 Playwright 10개를 실행한다.
5. backend XML과 Playwright report에서 failure/error/unexpected/flaky/skipped가 모두 0인지 확인한다.
6. E2E 후 읽기 전용 SQL로 active Rating=Frame=Popcorn, aggregate mismatch=0, orphan=0을 재검증한다.

커밋·Push·DB 초기화는 이번 감사에서 수행하지 않았다. `TASK-C1-011`은 이 보고서가 존재한다는 이유만으로
`DONE`이 아니며, blocker가 닫히고 새 revision에서 blind handoff가 재현된 뒤 상태를 갱신해야 한다.

## 7. 2026-08-29 후속 보완

초기 감사의 계약·운영 문서 문제를 다음과 같이 보완했다. 초기 83점의 실행 증거는 과거 snapshot으로
보존하고, 후속 점수는 변경된 계약·validator·backend 전체 suite를 재평가했다.

| ID | 상태 | 보완·검증 |
| --- | --- | --- |
| `BH-C1-002` | **CLOSED** | `docs/spec/approved-slices.json`을 canonical registry로 추가하고 공개 제품 권위를 `C0_CATALOG` base + `C1_RATING_FILM` extension으로 고정했다. `docs/spec`, `ui`, `data`, `testing`, `traceability`의 index와 공통 문서가 안정 경로 `docs/c1-draft`를 연결한다. C2A는 `APPROVED_C2A_INTERNAL_POPULARITY_ONLY`, `includedInPublicProductAuthority=false`로 격리했다. |
| `BH-C1-005` | **CLOSED** | Windows 명령을 `.\backend\gradlew.bat -p backend test`, Linux/macOS 명령을 `./backend/gradlew -p backend test`로 수정했다. 같은 Windows wrapper 경로의 targeted test가 실제 실행됐다. |
| `BH-C1-006` | **CLOSED — C1 API path** | `C1SafeLoggingAcceptanceTest`가 invalid Authorization과 Rating DB 503을 실제 MockMvc로 호출하고 token·idempotency key·actor UUID·Rating body·email canary 비노출을 capture/assert한다. 선택 실행과 전체 suite가 통과했다. outbox worker는 allowlist event projection만 소비하고 사용자 Authorization·Rating request body를 받지 않는다. |
| `BH-C1-003` | **CLOSED** | `C1MutationRollbackPostgresIntegrationTest`가 confirmation 상태 갱신 뒤, Rating row 뒤, Popcorn 뒤, delete aggregate 역산 뒤의 4개 checkpoint에서 실패를 주입하고 11개 상태 집합의 row count와 canonical SHA-256이 전후 동일함을 검증한다. |
| `BH-C1-004` | **CLOSED** | `ActiveRatingInputOutboxWorker`가 실제 C2 active Rating projection consumer를 bounded poll로 호출한다. 재진입 방지, `FOR UPDATE SKIP LOCKED` 경쟁, bounded retry와 `DEAD_LETTER`, C1 commit 격리를 PostgreSQL integration test로 검증한다. |
| `BH-C1-007` | **CLOSED — mapping 형식** | `docs/testing/c1-ac-test-map.csv`에 `AC-C1-001`~`059`를 완전한 ID로 한 행씩 기록했다. validator가 중복·누락·미등록 test ID·source/locator 부재를 실패시킨다. 자동 증거 51개와 GAP 8개를 구분하며 GAP을 PASS로 계산하지 않는다. |

### 7.1 추가된 회귀 Gate

- `npm run contracts:check`: registry의 공개 Slice가 정확히 C0+C1인지, C1 stable extension인지,
  모든 canonical 파일이 존재하고 승인 상태인지, C2A가 공개 제품으로 승격되지 않았는지 검사한다.
- `npm run c1:contracts:check`: 공통 제품 범위가 C0-only로 회귀하거나 registry 상태가 충돌하면
  실패한다. 59개 AC mapping의 완전성과 자동 증거 locator도 검사한다.
- `scripts/tests/test_contract_slice_registry.py`: 임시 복제 문서에서 C0-only 문구와 C1 `DRAFT`
  registry 변조가 실제로 거부되는지 2개 negative test로 검증한다.

후속 실행 결과:

```text
npm run contracts:check
  PASS — 2 approved public slices, 18 operations, 13 screens,
         109 acceptance tests, 21 trace rows
npm run c1:contracts:check
  PASS — 11 operations, 8 screens, 52 rules,
         59 acceptance tests (59 automated, 0 explicit gaps), 12 trace rows, 11 tasks
py -3.12 -m unittest scripts.tests.test_contract_slice_registry
  PASS — 2/2
.\backend\gradlew.bat -p backend test --tests com.feelm.catalog.security.C1SafeLoggingAcceptanceTest
  PASS — BUILD SUCCESSFUL
backend full-suite
  PASS — 72 tests, failures=0, errors=0, skipped=0
frontend full-suite
  PASS — 29 tests
npm run verify
  PASS — contract/OpenAPI/Compose config/backend/frontend/data/recommender Gate
```

### 7.2 후속 점수와 잔여 NO-GO

| 항목 | 후속 점수 | 변화 |
| --- | ---: | --- |
| 계약·추적성 | 20/20 | 공통 C0+C1 권위와 AC mapping Gate로 +3 |
| Backend 기능 정확성 | 20/20 | 변화 없음 |
| 원자성·멱등성·동시성·불변식 | 20/20 | 4개 mutation checkpoint와 runtime outbox worker 증거로 +5 |
| Frontend·실제 E2E | 20/20 | 변화 없음 |
| 보안·장애 격리 | 10/10 | C1 API safe-log capture로 +1 |
| 독립 재현성·운영 문서 | 3/10 | Runbook 경로 수정으로 +1; revision 미고정 |
| **합계** | **93/100** | **점수 기준은 넘었지만 hard Gate 때문에 NO-GO** |

남은 차단사항은 다음 하나다.

1. `BH-C1-001` **BLOCKER**: 구현·계약이 여전히 untracked라 새 checkout 재현이 불가능하다.

기존 자동 증거 GAP 8개(`AC-C1-005`, `009`, `030`, `031`, `033`, `040`, `047`, `056`)는
2026-08-30 `C1ExplicitGapPostgresAcceptanceTest`의 서로 다른 PostgreSQL/API 실행 증거로 닫았다.
또한 C1 item direct route는 첫 page에 없더라도 cursor를 끝까지 순회해 유효 owner resource를 찾도록
보완했고 두 번째 page 단위·브라우저 회귀를 추가했다.

따라서 계약 권위와 구현 증거 충돌은 해소됐지만 `BH-C1-001`이 닫히고 새 revision에서 전체 blind handoff를
재현하기 전까지 최종 판정은 **NO-GO**다.
