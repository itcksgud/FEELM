# C2A 내부 추천 독립 blind handoff 감사

> 감사일: 2026-08-30  
> 감사 범위: 승인 계약 → artifact/candidate → FastAPI → Spring 내부 adapter·Rating input → exposure DB → Compose  
> 감사 방식: 공개 기능 설명을 전제하지 않고 계약·소스·테스트 XML·실행 container·읽기 전용 DB 집계를 교차 검증  
> 판정: **92/100, NO-GO HANDOFF — 현재 workspace의 C2A local 내부 slice PASS / revision·운영 Gate 잔존**

## 1. 결론

C2A의 승인된 내부 Popularity-only 경로는 현재 workspace에서 동작한다. V100 ACTIVE Catalog의
UI_READY 7편과 candidate store가 같은 service UUID 집합을 사용하고, 완전한 artifact set을 로드한
FastAPI가 실제 rank에서 7편을 결정적으로 반환했다. Spring은 C1 active integer Rating snapshot만
canonical inputVersion으로 만들고, candidate/Catalog를 호출 전후 검증하며, caller가 실제 선택한 노출
3건만 typed V5 snapshot으로 별도 transaction에 저장한다. outbox projection, 노출 원자성·소유권·멱등성,
FastAPI/Spring의 strict failure가 PostgreSQL·HTTP 자동 테스트로 고정돼 있다.

다만 이것은 공개 추천 기능이나 production 준비 완료가 아니다.

1. `docs/api/openapi.yaml`과 React에는 C2 public operation/screen이 없고, 실제 Spring runtime에서
   `InternalRecommendationService`를 호출하는 public controller도 없다. 이는 승인된 경계이며 감사에서
   임의 endpoint를 추가하지 않았다.
2. 실행 rank는 모든 K에서 alpha `0`인 `BAYESIAN_POPULARITY_ONLY`다. ALS item factor와 calibration은
   checksum/readiness에 로드되지만 이 rank의 개인화 점수에는 사용되지 않는다. REC-EV-007의 Fold-in
   core benchmark는 별도 경로의 성능 증거이며 Compose rank가 Fold-in을 실행했다는 뜻이 아니다.
3. 운영 service credential·회전과 production topology SLA는 승인되지 않았다. local fake Gate 밖에서는
   Spring/FastAPI가 닫히며, 공개 활성화는 `TASK-C2-012`와 관련 decision 승인 전 금지다.
4. `HEAD=origin/main=e2768cd`, tracked file 10개, untracked file 440개다. 새 checkout은 현재 C2A 구현과
   증거를 재현할 수 없으므로 점수와 무관하게 독립 handoff는 NO-GO다.

## 2. 점수

| 항목 | 점수 | 독립 근거 |
| --- | ---: | --- |
| 계약·추적성 | 15/15 | C2 validator: 40 rules, 8 decisions, 37 AC, 12 tasks, 11 requirements, 내부 3 operation. public registry는 C2A를 명시적으로 비공개로 격리 |
| Artifact·FastAPI serving | 20/20 | checksum/family/head binding, atomic reload, service UUID quarantine, actual readiness/rank, V100 drift negative test |
| Spring input·client·exposure DB | 18/20 | active integer Rating canonical projection, event dedup/retry, strict JDK client, caller-selected typed exposure와 rollback/ownership PASS. Compose에는 실제 client를 부르는 승인 caller가 없음 |
| 보안·fail-closed | 15/15 | auth 없음 401, forbidden 403, mismatch 422, mode/config/timeout/invalid response typed failure, read-only mount, secret pattern 0, npm high 이상 0 |
| 자동 검증·Compose | 15/15 | backend 63, frontend 26, data pipeline 8, recommender 63 전부 PASS/skip 0. bounded build 1회와 stable no-build 3회 연속 PASS |
| 근거·성능 주장 경계 | 8/10 | REC-EV-007 조건·manifest·의존성 재현. 750ms는 local-loopback 후보이며 production SLA가 아니고 실제 Compose rank는 alpha 0 |
| 독립 revision 재현성 | 1/5 | 한 명령 Runbook과 비파괴 Compose probe는 있으나 구현 대부분이 untracked라 clean checkout 불가 |
| **합계** | **92/100** | local 내부 slice 통과; revision hard Gate 때문에 handoff NO-GO |

## 3. Fatal / Major / Minor

| ID | 심각도 | 판정 | 완료 조건 |
| --- | --- | --- | --- |
| `BH-C2A-001` | **MAJOR / HARD GATE** | 구현·계약·evidence 대부분이 untracked다. 현재 PC의 PASS를 새 checkout 재현으로 확대할 수 없음 | 사용자 승인 revision으로 고정한 뒤 새 worktree에서 `npm ci`, `npm run verify`, bounded Compose probe 재실행 |
| `BH-C2A-002` | **MAJOR — production boundary, not local defect** | operational service auth·rotation과 production topology timeout/SLA가 없다. fake mode 밖은 의도적으로 fail-closed | `DN-C2-004/005` 승인, 비밀 저장소 기반 credential, 운영 topology 재측정 전까지 배포·공개 활성화 금지 |
| `BH-C2A-003` | **MINOR / evidence boundary** | Compose는 실제 FastAPI rank와 Spring container의 TCP/env/read-only mount를 검증하지만, 승인 caller가 없어 container 내부 JDK client 호출은 하지 않음 | C2 public/internal caller가 별도 승인된 뒤 그 endpoint의 success/failure Compose E2E 추가. 현재는 Testcontainers/JDK HTTP 증거를 별도 유지 |
| `BH-C2A-004` | **MINOR / scale boundary** | Compose fixture는 UI_READY 7편이다. UUID/version wiring 증거이지 full Catalog coverage·처리량 증거가 아님 | production-like Catalog/candidate volume에서 coverage, reload, p95와 resource Gate 재검증 |

**Fatal은 0건**이다. 현재 승인된 C2A local 내부 경로에서 데이터 손상, 비밀 노출, public 의미 활성화,
fail-open fallback은 발견하지 못했다.

## 4. 경계별 감사

### 4.1 계약과 공개 API 부재

- C2 contract fragment의 operation은 liveness, readiness, internal rank 3개뿐이다.
- canonical public OpenAPI는 C0+C1 18 operation을 유지한다. C2 public controller/React route를 추가하지 않았다.
- expected star는 모든 반환 항목에서 `NOT_COMPUTED`, value null, displayEligible false,
  confidence `NOT_EVALUATED`다.
- reason·exploration·party·outcome attribution은 evidence 또는 후속 task일 뿐 현재 public 제품 의미가 아니다.

### 4.2 Artifact와 실제 rank

- artifact manifest는 Bias, ALS item factor, calibration v2, service mapping 네 payload의 version/checksum,
  compatibility family, rating scale/head binding을 검증한다.
- reload 실패는 기존 ready artifact set을 유지한다. readiness는 네 payload와 serving dry-run 5개가 모두
  PASS여야 200이다.
- local candidate active pointer는 immutable payload checksum, canonical bytes, catalogVersion,
  mapping checksum, sorted unique service UUID를 다시 검증한다.
- V100 SQL parser test가 Catalog version, 안정 movie UUID 8개, UI_READY 7개를 local fixture와 비교한다.
  Compose는 DB UI_READY count와 candidate/rank 7개를 다시 교차 비교한다.
- 실제 rank의 policy는 `BAYESIAN_POPULARITY_ONLY`, alpha 0이다. loaded factor의 readiness와 factor Fold-in
  benchmark를 현재 serving rank의 개인화 사용으로 오해하지 않는다.

### 4.3 Rating input과 C1 격리

- consumer는 `RATING_CREATED/UPDATED/DELETED` allowlist만 처리한다. actor는 behavior event FK에서 찾고
  payload의 임의 사용자 식별자를 신뢰하지 않는다.
- `rating.logical_status=ACTIVE`, integer 1~5만 movie UUID 순으로 canonicalize한다. deleted, unrated,
  onboarding, watched=false는 포함하지 않는다.
- eventId application PK와 actor advisory lock으로 중복·경쟁을 직렬화한다. consumer 실패는 outbox
  savepoint/재시도로 격리되어 이미 commit된 C1 Rating을 되돌리지 않는다.
- Compose V102는 local/compose profile에서만 deterministic Rating event를 추가한다. 운영 migration은
  자동 seed하지 않는다.

### 4.4 Spring adapter와 failure 의미

- 요청은 requestId, candidate version/service UUID, inputVersion과 active Rating tuple만 전송한다.
  user UUID, email, 사용자 bearer, raw behavior는 FastAPI body에 없다.
- 호출 전후 active Catalog version·UI_READY를 확인하고, response root/snapshot/item/reason/issue 필드를
  exact allowlist로 파싱한다. checksum·version·rank·UUID·alpha·expected-star 불일치는 성공으로 쓰지 않는다.
- config Gate, timeout, connection, 401, 403, 503, 기타 reject, malformed response는 typed 내부 실패다.
  이전 성공 body나 Popularity fallback으로 조용히 대체하지 않는다.
- Compose에서는 backend container에서 recommender TCP가 열렸는지, 필수 env **key**가 있는지,
  candidate mount가 read-only인지 확인한다. 환경 값과 token은 출력하지 않는다.

### 4.5 노출 persistence

- caller가 결과 중 실제 표시 대상으로 선택한 typed item만 V5 batch/item에 저장한다. 감사 test는 20개
  결과 중 3개만 저장되는지 검증한다. raw FastAPI JsonNode와 전체 candidate는 저장하지 않는다.
- 같은 exposureBatchId+동일 canonical payload는 replay하고, 다른 payload 재사용은 거부한다. 다른 batch는
  같은 영화를 새 recommendationItemId로 보존한다.
- batch insert부터 item 전체가 `REQUIRES_NEW` transaction이며 중간 실패 시 0건으로 rollback된다.
- actor 조건 없는 owned read는 제공하지 않는다. click/Rating이 없다는 이유로 negative/outcome row를
  만들지 않으며 현재 schema에도 임의 outcome record가 없다.

## 5. 실행 증거

### 5.1 전체 Gate

```text
npm run verify = PASS
backend: 18 suites, 63 tests, failures 0, errors 0, skipped 0
frontend: 3 files, 26 tests PASS; OpenAPI type generation + TypeScript + Vite build PASS
data-pipeline: 8/8 PASS
recommender: 63/63 PASS
REC-EV-007 validator: PASS
root/frontend/e2e npm audit --audit-level=high: vulnerabilities 0/0/0
secret pattern file matches (tracked/untracked source, generated/secret dirs excluded): 0
```

공통 validator는 suffix task ID(`TASK-REC-EV-003B`)를 numeric-only regex 때문에 무시하던 결함이
감사 중 발견됐다. `\d{3}[A-Z]?`로 보정한 뒤 새 `TASK-REC-EV-011` dependency와 전체 계약 검증이
통과했다.

### 5.2 Compose

```text
bounded -Build: 1/1 PASS
stable no-Build repeat: 3/3 PASS
healthy services: 4
artifact checks / fail-closed HTTP checks: 5 / 3
UI_READY / candidate / ranked: 7 / 7 / 7
required Flyway versions: 8 (V1,V2,V3,V4,V5,V100,V101,V102)
Rating snapshot / processed event application: 1 / 5
exposure schema tables: 2
ranking alpha: 0
expected star: NOT_COMPUTED
```

Windows PowerShell 5에서 `docker compose up --build --wait`가 무기한 남는 문제를 제거했다. build native
process는 기본 300초, health poll은 120초로 제한한다. PowerShell 5의 null ExitCode 문제는 native
Handle을 Wait 전에 materialize해 성공/실패를 정확히 판정한다. Docker CLI 부재 negative 실행은
exit 2와 `DOCKER_CLI_UNAVAILABLE` safe code만 출력했다.

## 6. 재판정 Gate

1. 사용자가 검토 가능한 revision 고정을 승인한다.
2. 새 worktree/checkout에서 `npm ci`부터 전체 Gate와 bounded Compose를 재현한다.
3. C2B/public caller는 제품 결정·public contract 승인 뒤 별도 vertical로 구현한다.
4. 운영 auth와 production-like topology에서 timeout·readiness·reload·candidate scale을 재검증한다.
5. expected-star, reason, exploration, attribution을 현재 C2A PASS에 포함하지 않는다.

이번 감사는 commit, push, 배포, volume 삭제, DB 초기화, public 기능 활성화를 수행하지 않았다.

