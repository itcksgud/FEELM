# C4A Email Membership·Onboarding 계약 세트

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`  
> local profile 구현 권위: `YES` — 계약의 13개 operation과 7개 screen만  
> main OpenAPI local 13-operation merge: `YES`; production/OAuth/restart/password lifecycle 권위: `NO`; local 결정 `5/5`  
> 기준일: 2026-08-29

## 1. 목적

FR-01~03과 추가 합의된 닉네임·소셜 로그인 범위를 다음 수직 구현자가 대화 이력 없이 검토할 수
있게 정규화한다. 이 디렉터리의 승인된 13개 operation은 `docs/api/openapi.yaml`에 병합된
local-only extension 계약이다.

## 2. 권위와 승격 조건

- 요구사항 출처: `docs/requirements/00-source.md`, `03-api-candidates.md`,
  `04-open-questions.md`, `05-wireframe-decisions.md`
- 기존 인증 소비자 경계: C1 protected API의 required `bearerAuth`
- 온보딩 품질 근거: REC-EV-003B/011. full-catalog K10 alpha 0.2는 작은 offline ranking 후보지만
  champion·expected-star·사용자 입력 부담 승인이 아니다.
- C4A 문서는 모두 `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`이며 공통 계약보다 우선하지 않는다.
- `DN-C4A-001`~`005`의 보수 권장 token은 local profile에만 채택됐다. main OpenAPI에는 승인된
  13개 local operation만 병합됐고 production activation은 별도 승인이 필요하다.

## 3. 문서 지도

| 계약 | 파일 |
| --- | --- |
| 범위·시나리오 | `00-product-scope.md` |
| 용어·공통 정책 | `01-glossary-and-policies.md` |
| 업무 규칙 | `02-business-rules.md` |
| 상태 전이 | `03-state-machines.md` |
| P0 decision matrix | `decision-needed.md` |
| 제품·보안 결정 패킷 | `product-decision-packet.md` |
| API 초안 | `api/openapi.fragment.yaml` |
| ERD·데이터 사전 | `data/logical-erd.md`, `data/data-dictionary.md` |
| 화면·탐색 | `ui/screen-contracts.md`, `ui/navigation-map.md` |
| fixture·acceptance | `testing/fixtures.md`, `testing/acceptance-tests.md` |
| 구현 DAG | `tasks/implementation-backlog.yaml` |
| 추적성 | `traceability/requirements.csv` |
| 독립 검증 | `validate_contract.py` |
| 결정 패킷 검증 | `validate_product_decision_packet.py` |

## 4. 검증

```powershell
py -3.12 docs/c4-membership-onboarding/validate_contract.py
py -3.12 docs/c4-membership-onboarding/validate_product_decision_packet.py
npx redocly lint docs/c4-membership-onboarding/api/openapi.fragment.yaml
```

validator는 local 결정 ID 누락, 허용되지 않은 operation/task, social endpoint의 조기 노출, raw secret
fixture, 추적되지 않은 API·AC·screen을 실패시킨다.

익명 signup/verify/resend는 C1 actor idempotency row에 가짜 user를 넣지 않고 별도
canonical `PUBLIC_AUTH_IDEMPOTENCY_RECORD`와 current/previous `PUBLIC_AUTH_IDEMPOTENCY_SCOPE_ALIAS`를 사용한다.
validator는 keyed request HMAC/key rotation, persisted recovery
linearization, rotated-key aggregate quota, operation별 exact error code, local/production cookie 설명,
onboarding count schema와 AC077~085 final Gate도 검사한다.

결정 패킷 validator는 권장 수치·보안/UX/rollback/credential 경계 누락, local `5/5` drift,
production 권위 혼동, raw credential/redirect 노출을 실패시킨다.

## 5. 명시적 비밀 경계

- 로컬 이메일은 자격증명 없는 Mailpit adapter 제안만 둔다.
- 운영 SMTP/API credential, OAuth client secret, 실제 redirect URI는 문서·fixture에 넣지 않는다.
- password, verification secret, refresh token은 raw 저장·로그 금지다. 실제 mail provider 전달 중 recipient와
  single-use link는 worker memory/TLS wire에만 일시 허용하고 DB/outbox/cache/log/trace/test artifact에는 남기지 않는다.
- 문서의 token 문자열은 test fake decoder만 인식하는 비밀이 아닌 fixture label이다.
