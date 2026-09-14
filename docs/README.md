# FEELM 문서 지도

현재 C2B~C5 local-MVP 승인 기록은
[`planning/product-owner-approval-request-20260830.md`](planning/product-owner-approval-request-20260830.md)를 본다.

추천 연구의 현재 결론은 [현재 연구](recommendation/active-experiment.md)에서 시작한다.
작업 폴더는 [폴더·브랜치 지도](workspace-map.md), 문서 작성은 [짧은 작성 규칙](ai-workflow/writing.md)을 본다. [AI 작업 방식·측정 결과](ai-workflow/README.md)도 참고할 수 있다.

제품 구현 작업은 이 문서에서 해당 영역을 고른다. 공개 제품 계약의 권위는
[`approved-slices.json`](./spec/approved-slices.json), 전체 완료 상태와 남은 Gate는
[`project-completion-gates.yaml`](./planning/project-completion-gates.yaml)을 기준으로 한다.
요구사항 원문과 목업은 근거·참고 자료다.

## 제품 구현의 기준 문서

아래는 계약 지도다. 해당 기능의 계약 연결을 확인하되 모든 영역을 매번 전부 읽지 않는다.

1. [저장소 작업 규칙](../AGENTS.md)
2. [제품 범위](./spec/00-product-scope.md), [용어·정책](./spec/01-glossary-and-policies.md)
3. [업무 규칙](./spec/02-business-rules.md), [상태 모델](./spec/03-state-machines.md)
4. [화면 계약](./ui/screen-contracts.md), [내비게이션](./ui/navigation-map.md)
5. [OpenAPI](./api/openapi.yaml), [논리 ERD](./data/logical-erd.md), [데이터 사전](./data/data-dictionary.md)
6. [시스템 설계](./architecture/system-design.md), [ADR](./architecture/adr/ADR-0001-local-catalog-read-model.md)
7. [Acceptance](./testing/acceptance-tests.md), [추적성](./traceability/requirements.csv)
8. [로컬 Runbook](./runbook/local-development.md), [구현 백로그](./tasks/implementation-backlog.yaml)

## 상태

| 범위 | 계약 상태 | 구현 상태 | 다음 작업 |
| --- | --- | --- | --- |
| C0 Catalog | `APPROVED` | Spring·PostgreSQL·React·수집·게시·E2E·성능 Gate 완료 | revision 고정 뒤 blind handoff 재검증 |
| 평가·Film | `APPROVED` | 11 API·PostgreSQL transaction·React 8화면·Playwright 5여정, rollback·outbox worker 완료 | revision 고정 뒤 blind handoff 재검증 |
| 개인·발견 추천 | C2A internal·C2B local baseline 승인 | C2B 누적 추천/제외 흐름 실제 E2E, K10 Fold-in offline 후보; 제품 예상 별점은 `NOT_COMPUTED` | revision 재현·사용자 evidence·production 재승인 |
| Party·OTT 비교 | C3 `APPROVED_LOCAL_MVP` | 2~4명 local Party와 KR OTT 실제 영화 전체 목록·비교 구현/E2E 완료 | Party public champion·production auth/invitation 보류 |
| 회원·온보딩 | C4 `APPROVED_LOCAL_MVP` | Mailpit 이메일 인증, login/refresh/logout, profile/onboarding/OTT 구독 구현/E2E 완료 | OAuth·recovery·production email/origin/key 보류 |
| 리포트·프로필·공유·알림·설정 | C5 `APPROVED_LOCAL_MVP` | factual report/PDF, privacy opt-in profile, fragment share/revoke, in-app notification 구현/E2E 완료 | account lifecycle·production provider/storage 보류 |
| 추천 해석 실험 | C6 `APPROVED_LOCAL_EXPERIMENT` | local-only 예상 별점·개인 기준 기대 효용·취향 관측 근거; 모두 `displayEligible=false` | paired scale·실사용자 검증 후 제품 채택 판단 |

## 자동 검증

```powershell
npm ci
npm run verify
npm run recommendation:evidence:check
npm run verify:e2e
```

모든 명령과 환경 변수는 [로컬 Runbook](./runbook/local-development.md) 한 곳에서 관리한다.

추천 연구: [현재 결론](recommendation/active-experiment.md) → [필요한 연구 절차](recommendation/README.md).
과거 REC002~019의 수치는 당시 연구 이력이며 [이전 기록 지도](recommendation/record-system-history-20260912.md)에서 찾는다.
현재 연구의 선정안으로 승인 제품 정책을 자동 변경하지 않는다.
