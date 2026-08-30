# C1 Rating·Film 승인 계약

> 상태: `APPROVED` — C1 구현 입력으로 사용 가능  
> Canonical public slice registry: `docs/spec/approved-slices.json`  
> 작성일: 2026-08-29  
> 기준 요구사항: `docs/requirements/00-source.md`, FR-04~FR-09, FR-17~FR-19, NFR-05~NFR-07

이 디렉터리는 C0 Catalog 기반 위에 C1을 합성할 수 있도록 모은 승인 extension 계약이다.
`docs/spec/approved-slices.json`이 C1을 공개 제품 승인 Slice로 등록하며, P0 제품 결정은
`decision-needed.md`에 승인 기록으로 남겼고 P1은 현재 기능을 확장하지 않는 안전 경계로 격리했다.
경로 이름 `c1-draft`는 기존 검토 링크의 안정성을 위해 유지하며 문서 상태를 뜻하지 않는다.

## 문서 지도

| 문서 | 역할 |
| --- | --- |
| `00-product-scope.md` | C1 사용자 여정, 포함·제외 범위, 완료 Gate |
| `01-glossary-and-policies.md` | Rating·Viewing·Film·Popcorn 용어와 공통 정책 |
| `02-business-rules.md` | 인가·멱등성·트랜잭션·집계 불변식 |
| `03-state-machines.md` | WatchIntent, ViewingRecord, Rating 상태 전이 |
| `decision-needed.md` | 승인된 P0 결정 기록과 후속 P1 경계 |
| `ui/` | 화면 상태와 내비게이션 |
| `api/openapi.fragment.yaml` | 승인된 C1 OpenAPI 3.1 계약 조각 |
| `data/` | C1 논리 ERD와 데이터 사전 조각 |
| `testing/` | 고정 fixture와 acceptance criteria |
| `traceability/requirements.csv` | REQ→SCREEN→API→ENTITY→AC→TASK 연결 |
| `tasks/implementation-backlog.yaml` | 승인된 의존 순서로 실행할 작은 작업 그래프 |

## 출처와 시각 참고

- 요구사항 원문: `docs/requirements/00-source.md`
- 팀 결정 기록: `docs/requirements/05-wireframe-decisions.md`
- C0 계약 형식: `docs/spec/`, `docs/api/openapi.yaml`, `docs/testing/`
- 시각 참고 원본: `C:\Users\kingc\Downloads\FEELM UI Mockups Final FOR REAL.html`
- 시각 원본 SHA-256: `c438d2da2b53c45c1bbc577799c40e416249c753cf6eaf1c1b281be90622afbf`
- 참고 화면: `1a ⑥-3`, `⑥-4`, `⑥-5`, `⑥-6`, `⑨`, `⑪`, `⑪-3`, `⑪-4`

시각 원본의 `4.0/5.0`, 한줄 감상, 월간 증가량 같은 표현은 확정 요구사항으로 승격하지 않는다.
특히 한줄평은 FR-26의 2차 MVP이므로 C1 쓰기 범위에서 제외한다.

## 구현 상태와 순서

1. 공통 OpenAPI 병합, required auth, DB·idempotency·outbox 기반을 완료했다.
2. WatchIntent→ViewingRecord→Rating→Film·Frame·Popcorn·Taste 수직 경로와 React 8개 화면을 완료했다.
3. backend 전체 72/72, React 29/29, 실제 Compose Playwright C0+C1 10/10을 통과했다.
4. 다음 task는 대화 이력 없는 `TASK-C1-011` blind handoff다.

현재 `TASK-C1-001`~`TASK-C1-010`은 `DONE`, `TASK-C1-011`만 `READY`다. C1 11개 operation은
공통 OpenAPI에 병합됐고 이 디렉터리는 승인된 의미·Acceptance·추적성 기준으로 계속 사용한다.

```powershell
npm run c1:contracts:check
npm exec redocly lint docs/c1-draft/api/openapi.fragment.yaml
```

첫 명령은 operation·screen·rule·AC·trace·task 참조와 backlog dependency 상태를 함께 검증한다.
