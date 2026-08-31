# FEELM LLM 자율개발 준비도와 계약 문서 계획

> 상태: `APPROVED` — 문서 작성 순서와 평가 방법의 기준  
> 최초 평가일: 2026-08-29  
> 최초 기준선: **NOT_READY (12/40, 30%)**  
> 현재 판정: **전체 프로젝트는 범위별 판정 / C0 blind handoff revision 대기 / Recommendation vNext 오프라인 구현 GO**

## 1. 목표

목표는 새 LLM 작업이 이전 대화 이력 없이 저장소만 읽고 다음을 반복할 수 있는 상태다.

1. 다음 구현 작업을 의존성 순서로 선택한다.
2. 요구사항과 화면·API·DB·추천 계약을 추적한다.
3. 정상·빈 상태·오류·권한 상태를 포함해 구현한다.
4. 로컬 환경을 구성하고 자동 테스트로 검증한다.
5. 계약과 구현이 달라지면 문서·테스트를 함께 갱신한다.
6. 결과와 남은 위험을 재현 가능한 명령으로 인계한다.

LLM이 제품 책임자를 대체하는 것이 목표는 아니다. 다음은 사람 또는 명시적으로 승인된 정책이 제공해야 한다.

- 기능 범위와 사용자 경험을 바꾸는 제품 결정
- 이메일·OAuth·배포·도메인 등 외부 계정과 자격증명
- 데이터·이미지·OTT 정보의 상업 사용 승인
- 추천 정확도와 탐험성 사이의 최종 허용 손실
- 실제 사용자 만족도와 운영 출시 승인

## 2. 판정 등급

| 등급 | 점수 | 조건 |
| --- | ---: | --- |
| `READY` | 34~40 | 모든 필수 Gate 통과. 독립 LLM blind handoff 3개 성공 |
| `CONDITIONALLY_READY` | 28~40 | 범위가 닫힌 수직 기능 구현은 가능하지만 하나 이상의 필수 Gate가 남음 |
| `NOT_READY` | 0~27 | 구현 의미를 LLM이 추측하거나 실행·검증할 수 없는 필수 계약이 있음 |

점수와 별개로 필수 Gate 하나라도 실패하면 `READY`가 될 수 없다.

## 3. 현재 준비도 평가

각 항목은 0~4점이다.

| 항목 | 현재 | 근거 | READY 조건 |
| --- | ---: | --- | --- |
| 제품 범위·우선순위 | 2 | 1·2차 MVP와 계획은 있으나 원문 상태가 `검토 중`이고 범위가 큼 | P0 vertical slices와 out-of-scope가 승인됨 |
| 업무 규칙·상태·불변식 | 1 | 결정 기록은 있으나 미정·추가 결정 표현이 다수 | P0 상태 전이·삭제·멱등성·권한 규칙 확정 |
| 화면·내비게이션 계약 | 1 | 20MB 목업은 저장소 밖에 있고 충돌 기록만 있음 | 화면 ID별 데이터·행동·상태·이동 계약 |
| API 계약 | 1 | endpoint 후보만 있고 OpenAPI 없음 | lint 가능한 OpenAPI와 예시·오류 모델 |
| DB·데이터 계약 | 1 | 엔티티 후보와 데이터 감사만 있음 | ERD, dictionary, 제약, provenance, migration 정책 |
| 추천·평가 계약 | 3 | 데이터 감사와 평가 프로토콜이 상세함 | 서빙 입력·출력·fallback·버전 계약 추가 |
| 아키텍처·ADR | 1 | 기술 방향과 계획만 있음 | 경계·호출 흐름·장애 정책과 결정 ADR |
| Acceptance·추적성 | 1 | 테스트 전략만 있고 실행 가능한 AC가 없음 | REQ→SCREEN→API→ENTITY→AC→TEST 전수 연결 |
| 환경·실행 Runbook | 1 | TMDB env 예시와 감사 스크립트만 있음 | 한 명령 bootstrap, fixture, 모든 검증 명령 |
| Agent 규칙·작업 그래프 | 0 | 평가 시점에 AGENTS와 실행 backlog가 없었음 | agent contract와 의존성 있는 소형 작업 목록 |
| **합계** | **12/40** | **현재는 문서 해석만으로 구현 의미가 달라질 가능성이 큼** | **34점 이상 + 모든 Gate + blind handoff** |

`AGENTS.md`를 이번에 추가했지만 최초 점수는 평가 시점의 기준선으로 보존한다. 계약 문서가 실제로 채워지고 blind handoff를 통과할 때만 재평가한다.

### 3.1 C0 Catalog 계약 작성 후 재평가

> 재평가일: 2026-08-29  
> 판정: **CONDITIONALLY_READY (35/40, 87.5%)**  
> 허용 범위: `TASK-CAT-002`부터 C0 Catalog 구현 시작  
> 비허용 범위: 전체 FEELM 무인 구현, 평가·Film·개인 추천·Party 의미 추측

| 항목 | 점수 | 재평가 근거 |
| --- | ---: | --- |
| 제품 범위·우선순위 | 4 | C0 여정, in/out, 공개 범위와 품질 목표 승인 |
| 업무 규칙·상태·불변식 | 4 | `BR-CAT-*`, Catalog/OTT/UI 상태와 경계값 승인 |
| 화면·내비게이션 계약 | 3 | 5개 화면의 상태·행동·이동은 닫혔으나 20MB 시각 원본은 저장소 밖에 있음 |
| API 계약 | 4 | 7 operations, 오류·pagination·auth·example을 포함한 OpenAPI lint 통과 |
| DB·데이터 계약 | 4 | 논리 ERD, data dictionary, import·quality·publish 계약 작성 |
| 추천·평가 계약 | 3 | C0 유사 영화의 version·결정성·fallback은 닫혔으나 개인 추천은 C2 대상 |
| 아키텍처·ADR | 4 | runtime 경계와 4개 선택 ADR 작성 |
| Acceptance·추적성 | 4 | 50 AC, 9 trace rows, 7 operations 전수 참조 자동 검증 |
| 환경·실행 Runbook | 2 | 계약·mock은 재현되지만 Spring/PostgreSQL/React 제품 환경은 아직 없음 |
| Agent 규칙·작업 그래프 | 3 | 규칙과 13개 의존성 task가 있으나 실제 구현에서 DoD 준수를 아직 증명하지 않음 |
| **합계** | **35/40** | **문서만으로 첫 구현 task를 시작할 수 있으나 무인 완주 증거는 없음** |

Gate 판정:

- `G1 결정 폐쇄`: 통과. 승인 계약의 미해결 marker 검사가 통과했다.
- `G2 전수 추적`: C0 범위 통과. operation·screen·AC·task 참조 검사가 통과했다.
- `G3 실행 가능 계약`: 부분 통과. OpenAPI lint와 mock 요청 검증은 통과했지만 DB migration과 fixture 실행은 구현 전이다.
- `G4 재현 가능한 개발 환경`: 실패. `TASK-CAT-002`와 `TASK-CAT-003` 산출물이 아직 없다.
- `G5 독립 LLM 검증`: 실패. 새 대화·새 worktree blind handoff를 아직 실시하지 않았다.

따라서 점수만으로 `READY`라 부르지 않는다. 현재 문서는 LLM이 **C0 구현을 시작하기 위한
충분한 입력**이고, LLM이 프로젝트를 **혼자 끝낼 수 있다는 증거**는 아니다.

### 3.2 재현한 검증 결과

```text
npm run contracts:check
  PASS — 7 operations, 5 screens, 50 acceptance tests, 9 trace rows, 13 tasks

npm run openapi:lint
  PASS — OpenAPI description valid, warning 0

npm run openapi:mock
  PASS — GET /api/v1/movies?query=... -> 200
  PASS — GET /api/v1/catalog/genres -> 200
  PASS — GET /api/v1/movies?limit=999 -> 422 request validation

npm audit
  PASS — vulnerabilities 0
```

다음 재평가는 `TASK-CAT-002`와 `TASK-CAT-003` 완료 후 G3·G4를 확인하고, C0 구현 완료 후
`TASK-CAT-013`에서 blind handoff 시험 A를 수행한다.

### 3.3 C0 구현 후 blind handoff 시험 A

> 감사일: 2026-08-29  
> 판정: **84/100 FAIL — C0 기능 PASS, 새 checkout 인계 NO-GO**

Spring·PostgreSQL·React·수집 pipeline·원자 게시·E2E·87,585편 성능 Gate는 현재 workspace에서
통과했다. 그러나 구현 전체가 아직 commit되지 않아 새 checkout/worktree에는 실행 파일이 없고,
감사 당시 기본 backend Gate가 importer 실DB 테스트를 skip했다. 따라서 G3의 기능 증거는 확보했지만
G4·G5를 통과했다고 선언하지 않는다.

상세 점수와 조치는 [C0 blind handoff 결과](./c0-blind-handoff-20260829.md)를 기준으로 한다.
Testcontainers 보완 뒤 backend 21/21, skip 0을 재검증했다. 사용자 승인으로 revision이 생긴 뒤
새 worktree blind 재검증을 통과해야 `TASK-CAT-013=DONE`과 C0 slice GO로 바꾼다.

### 3.4 Recommendation vNext 오프라인 구현 GO

> 재평가일: 2026-08-30
>
> 판정: **GO — `TASK-REC-EV-019A`, `TASK-REC-EV-019B` 독립 구현 가능**
>
> 비포함: 현재 C2 popularity-only 교체, 개인화 champion, 예상 별점 public 노출

추천 입력의 핵심 충돌을 `K_b` binary 온보딩과 `K_r` 활성 Rating으로 분리했고, user split·candidate·
기준 모델·통계 Gate·fallback·task graph를 실행 계약으로 승인했다. 실제 MovieLens Test 3,200,021행을
사용한 `REC-EV-019P v2`는 약한 30% Test false-GO를 폐기하고 K10·미래 10개·positive 3개·
candidate-positive를 모두 적용했다. split을 `40/10/10/40`으로 교정해 strict K10 eligible
5,476명으로 최소 5,000명 Gate를 통과했으며, 019C는 최종 TMDB identity 적용 뒤 Gate를 재확인한다.

단일 기준은 [Recommendation vNext 구현 준비도](../recommendation/vnext-implementation-readiness.md)다.
다음 명령이 PASS와 `decision=GO`를 반환해야 한다.

```powershell
npm run recommendation:vnext:readiness:check
```

이 GO는 LLM이 실험 구현을 시작하기 위한 계약 완결성 판정이다. 모델 채택은 REC-EV-019 이후 실제
full-catalog 결과가 SESOI·segment non-inferiority·Holm 보정 Gate를 통과할 때만 별도로 승인한다.

## 4. 필요한 계약 문서 세트

### D0 — 기준과 결정

| 산출물 | 현재 자료 | 완료 조건 |
| --- | --- | --- |
| `AGENTS.md` | 이번에 생성 | 권한·출처 계층·DoD·비밀·검증 규칙이 명시됨 |
| `docs/spec/00-product-scope.md` | 요구사항 원문, 독립 계획 | P0/P1/out-of-scope, actor, 핵심 사용자 여정 승인 |
| `docs/decisions/decision-log.md` | wireframe decisions, open questions | 모든 P0 질문이 `DECIDED` 또는 명시적 제외 |
| `docs/spec/01-glossary-and-policies.md` | 요구사항 용어 | 코드·UI·데이터에서 같은 용어와 값 사용 |

### D1 — 제품 행동 계약

| 산출물 | 반드시 포함할 내용 |
| --- | --- |
| `docs/spec/02-business-rules.md` | 평가 수정·삭제, Frame·Popcorn, 취향 집계, 공개·공유, 파티 권한, 추천 구성 |
| `docs/spec/03-state-machines.md` | 계정, 온보딩, WatchIntent, Rating, PartyInvitation, ReportShare 상태 전이 |
| `docs/ui/screen-contracts.md` | 화면 ID, 진입 조건, 표시 데이터, 사용자 행동, 로딩·빈 상태·오류, 다음 화면 |
| `docs/ui/navigation-map.md` | 인증·온보딩·메인·상세·평가·파티·리포트 이동과 guard |

20MB 목업 HTML은 디자인 참고 원본이다. 그대로 구현 계약으로 쓰지 않고 화면 목록과 상태를 텍스트 계약으로 추출한다. 최종 목업의 hash와 원본 경로를 기록하고, 필요한 대표 이미지만 별도 보관한다.

### D2 — 실행 가능한 시스템 계약

| 산출물 | 반드시 포함할 내용 |
| --- | --- |
| `docs/api/openapi.yaml` | 인증, pagination, 오류, idempotency, 예시, 모든 P0 operation |
| `docs/data/logical-erd.md` | 엔티티·관계·소유권·파생 데이터·불변식 |
| `docs/data/data-dictionary.md` | 필드 타입·nullable·단위·enum·출처·보존·PII |
| `docs/data/catalog-ingestion-contract.md` | MovieLens/TMDB ID 검증, locale fallback, UI/OTT eligibility, snapshot |
| `docs/recommendation/serving-contract.md` | 입력, candidate, score, 예상 별점, 신뢰 상태, reason, fallback, version |
| `docs/architecture/system-design.md` | Spring/FastAPI/Spark/PostgreSQL/Redis 경계와 동기·비동기 호출 |
| `docs/architecture/adr/*.md` | 선택 이유, 대안, 결과, 재검토 조건 |

OpenAPI와 ERD는 독립적으로 작성하지 않는다. 한 use case마다 화면 응답 → API → 저장 원천 순서로 동시에 확정한다.

### D3 — 검증과 실행 계약

| 산출물 | 반드시 포함할 내용 |
| --- | --- |
| `docs/testing/acceptance-tests.md` | Given/When/Then, 정상·빈 상태·오류·권한·외부 장애 |
| `docs/traceability/requirements.csv` | Requirement, Screen, OperationId, Entity, AC, Test, Status |
| `docs/runbook/local-development.md` | prerequisites, env, bootstrap, seed, start/stop, test/lint, troubleshooting |
| `docs/tasks/implementation-backlog.yaml` | 작은 수직 작업, 의존성, 입력 문서, 산출물, 검증 명령, DoD |
| `docs/testing/fixtures.md` | 고정 사용자·영화·파티·권한·추천 장애 시나리오 |

## 5. 작성 순서

```text
D0 범위·결정·용어
  → D1 업무 규칙·상태·화면
    → D2 OpenAPI·ERD·추천·아키텍처
      → D3 Acceptance·추적성·Runbook·Backlog
        → 독립 LLM blind handoff
          → 실패를 문서 누락으로 환원
            → 재평가
```

API부터 작성하면 미정인 업무 규칙을 Schema에 숨기게 된다. 반대로 모든 후순위 기능을 먼저 확정하면 개발이 시작되지 않는다. P0 첫 수직 기능인 `영화 카탈로그 조회·검색·상세·OTT`를 기준으로 D0~D3 한 묶음을 완성하고, 같은 형식을 평가·필름, 추천, 파티 순서로 확장한다.

## 6. 필수 READY Gate

### G1. 결정 폐쇄

- P0 기능에 `미정`, `검토`, `예정`인 제품 의미가 없다.
- 보류 사항은 해당 기능의 범위 밖으로 명시되어 있다.
- 되돌리기 어려운 선택은 ADR 또는 decision log에 있다.

### G2. 전수 추적

- 모든 P0 Requirement가 하나 이상의 Screen 또는 internal use case에 연결된다.
- 모든 사용자 행동이 OpenAPI operation과 권한 규칙에 연결된다.
- 모든 API 쓰기가 entity와 transaction boundary에 연결된다.
- 모든 acceptance criterion이 자동화할 test ID에 연결된다.

### G3. 실행 가능 계약

- OpenAPI lint가 통과하고 example response로 Mock Server를 실행할 수 있다.
- ERD가 렌더링되고 Schema 제약이 업무 불변식을 표현한다.
- 추천 서비스 장애 상태와 fallback 응답이 계약에 있다.
- 외부 API 없이도 fixture와 adapter fake로 핵심 E2E를 실행할 수 있다.

### G4. 재현 가능한 개발 환경

- 새 checkout에서 문서 한 개만 따라 bootstrap할 수 있다.
- 실제 secret 없이 local profile을 실행할 수 있다.
- test, lint, migration, API contract 검증 명령이 문서와 CI에서 동일하다.
- 대용량 원본 없이도 작은 고정 fixture로 개발할 수 있다.

### G5. 독립 LLM 검증

- 새 대화와 새 worktree에서 이전 대화 내용을 전달하지 않는다.
- LLM에는 저장소와 하나의 backlog task ID만 제공한다.
- 세 수직 기능을 각각 독립적으로 구현·검증한다.
- 질문 없이 진행했다는 사실보다, 제품 의미를 발명하지 않았다는 것을 우선 평가한다.

## 7. Blind handoff 시험

### 시험 A — Catalog

`영화 검색 → 상세 → 한국어/영어 fallback → KR OTT 목록`을 구현한다.

- stale TMDB ID와 TV type mismatch fixture 포함
- OTT `flatrate`와 rent/buy 분리
- TMDB 장애 시 기존 catalog 조회 가능

### 시험 B — Rating·Film

`감상 확인 → 지연 평가 → Rating 생성·수정·삭제 → Frame·Popcorn·취향 갱신`을 구현한다.

- 중복 요청과 transaction rollback 포함
- 상태 전이와 집계 불변식 검증
- 삭제 정책을 코드가 임의로 만들지 않았는지 확인

### 시험 C — Recommendation

`개인 2 + 발견 1 → 발견 없음이면 개인 3 → 예상 별점·신뢰·이유 → 장애 fallback`을 구현한다.

- 같은 입력·modelVersion의 결정성
- 평가 부족 사용자 상태
- 이미 평가한 영화와 UI 미준비 영화 필터

## 8. 시험 채점표

| 항목 | 배점 | 실패 예 |
| --- | ---: | --- |
| 계약 준수 | 20 | 문서에 없는 상태·필드·정책 발명 |
| 추적성 | 15 | 코드가 Requirement·AC·Test와 연결되지 않음 |
| 기능 정확성 | 20 | 정상 흐름 또는 핵심 불변식 실패 |
| 오류·권한·빈 상태 | 15 | 외부 장애가 전체 기능을 중단 |
| 자동 검증 | 15 | 테스트가 없거나 명령이 재현되지 않음 |
| 변경 범위·유지보수성 | 10 | 계약을 우회하거나 다른 수직 기능까지 변경 |
| 보안·비밀 | 5 | token, PII, private data 노출 |

각 시험은 85점 이상이어야 통과한다. 세 시험이 모두 통과하고 재실행 시 같은 결과가 나와야 `READY`로 판정한다.

## 9. 현재 차단 요인과 다음 작업

현재 가장 큰 차단 요인은 기술 조사가 아니라 계약 부재다.

1. 요구사항 원문이 `검토 중`이고 P0 의미 결정이 남아 있다.
2. 최종 20MB 목업이 저장소 밖에 있으며 화면별 계약으로 정규화되지 않았다.
3. OpenAPI와 ERD가 없다.
4. 추천 vNext 서빙 경계는 작성됐으며 제품 API 승격은 REC-EV Gate 이후 별도 승인한다.
5. acceptance criteria, fixture, 실행 명령, task graph가 없다.

다음 작업은 **Catalog 수직 기능 계약 세트**다.

```text
Scope/decision
→ Catalog screen contracts
→ Catalog OpenAPI
→ Movie/Provider ERD와 data dictionary
→ TMDB adapter·fallback acceptance tests
→ Catalog backlog tasks
→ blind handoff 시험 A
```

Catalog 시험을 통과하기 전에는 전체 백엔드나 추천 시스템을 한 번에 생성하지 않는다. 첫 시험에서 드러난 문서 형식을 고친 뒤 Rating·Film과 Recommendation에 복제한다.
