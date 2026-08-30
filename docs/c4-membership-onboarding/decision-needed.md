# C4A 승인 필요 결정

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`  
> 결정 상태: `APPROVED_LOCAL_PROFILE_5_OF_5` — [제품·보안 결정 패킷](./product-decision-packet.md)  
> local profile 구현 및 main OpenAPI 13-operation 병합 권위: `YES`; production 권위: `NO`  
> Local profile 승인 현황: `5/5`  
> 원칙: 권장 token은 local profile에만 확정되며 production 승인이 아니다.

## Decision matrix

| ID | 주제 | 확정 근거 | Proposed 초기안 | 승인해야 할 선택 | 상태 | 차단 범위 |
| --- | --- | --- | --- | --- | --- | --- |
| `DN-C4A-001` | 인증 전달·수명·logout | C1 protected API는 Bearer를 소비한다. FR-01은 login/logout만 확정했다. | RS256 exact iss/aud/kid/claim의 access JWT 10분 + rotating refresh lineage. 5초 race는 PostgreSQL clock, family terminal+30일 cleanup. 모든 logout exact Origin 필수; cookie-less만 CSRF/idempotency 없는 204 clear. invalid session은 401. production/local cookie pair와 별도 multi Set-Cookie를 강제한다. | bearer+refresh 또는 공통 migration; TTL; key rotation; race grace/clock; lineage retention; access revoke 의미; retry/cookie profile; current/all-session logout; CSRF 기준 | `APPROVED_LOCAL_PROFILE_2026-08-30` | production token/key/cookie activation은 별도 승인 |
| `DN-C4A-002` | 가입 필드·닉네임 | 이메일·비밀번호·닉네임 범위는 API 후보/C-07에 있다. | 법적 이름 없이 email/password/nickname만 받고, password 15..128, nickname 2..20·trim/NFKC/casefold·전역 unique·30일 cooldown을 local profile에 사용한다. | production 표시/정책 변경은 별도 승인 | `APPROVED_LOCAL_PROFILE_2026-08-30` | production profile activation은 별도 승인 |
| `DN-C4A-003` | 이메일 인증·중복·rate limit | 운영 credential은 없으며 Mailpit local capture만 가능하다. | stable public flow, versioned hash-only challenge, encrypted material+safe outbox, 별도 public idempotency, persisted recovery, atomic shared rate limit을 local profile에 사용한다. | production provider/sender/credential은 별도 승인 | `APPROVED_LOCAL_PROFILE_2026-08-30` | production mail adapter는 `BLOCKED` |
| `DN-C4A-004` | 온보딩 수량·후보·재수행 | LIKE/DISLIKE 분리와 skip은 확정. REC-EV-011 K10은 작은 offline 후보뿐이다. | local은 최대 10개, 1개부터 `SUBMITTED`, K10 권장, 0개 `SKIPPED`; K10을 champion/expected-star로 표현하지 않는다. | rerun은 local MVP에서 제외 | `APPROVED_LOCAL_PROFILE_2026-08-30` | `restartOnboarding`은 `BLOCKED` |
| `DN-C4A-005` | Social OAuth·계정 연결 | GOOGLE/KAKAO/NAVER 범위만 있고 운영 key·redirect가 없다. | 세 provider `DISABLED`, public operation/UI/credential/runtime identity row 0건, email claim 자동 병합 금지를 local profile에 채택한다. | OAuth 활성화는 새 제품·보안 승인 필요 | `APPROVED_LOCAL_PROFILE_2026-08-30` | social public API/UI와 credential은 `BLOCKED` |

## local 승인 규칙

1. 다섯 보수 token은 local profile에서만 채택한다.
2. operation·AC·task·trace는 local 구현과 blocked extension을 정확히 분리한다.
3. main OpenAPI에는 승인된 local 13개 operation만 병합하며 production 승격은 별도 작업이다.
4. production mail/OAuth/deploy는 credential 존재만으로 활성화하지 않는다.
5. C1 required bearer와 frontend client migration은 C4 runtime 구현에서 검증한다.

## local profile에서 허용되는 것

- 13개 operation, 7개 screen, local Mailpit와 security/race test 구현
- password/token hashing library benchmark와 fail-closed readiness
- Google/Kakao/Naver capability를 `DISABLED`로 검증하는 negative contract

local 13개 operation의 main OpenAPI·frontend 변경은 후속 승인으로 수행한다. production email/OAuth 연결은
local 구현 이후에도 별도 승인 전 허용하지 않는다.
