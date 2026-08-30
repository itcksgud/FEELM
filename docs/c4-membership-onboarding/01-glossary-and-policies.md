# C4A 용어와 공통 정책

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`

## 1. 용어

| 용어 | 정의 |
| --- | --- |
| Service User | FEELM에 가입한 사용자. MovieLens user와 ID 공간·의미가 완전히 다르다. |
| Membership | email credential로 생성되고 verification을 거쳐 protected API actor가 되는 계정 상태 |
| Email Credential | normalized email key와 password hash metadata. raw password를 보관하지 않는다. |
| Email Signup Public Flow | 응답으로 주는 stable `signupId`의 24시간 actual/decoy 상태. challenge version과 분리되며 kind·identity는 public에 노출하지 않는다. |
| Email Verification Challenge | 일회용 secret의 hash·expiry·attempt/delivery metadata. raw secret의 저장소가 아니다. |
| Verification Delivery Material | commit 후 메일 worker가 같은 single-use link를 만들기 위한 TTL-bound AES-256-GCM ciphertext·nonce·key version. safe outbox에는 materialId만 있고 terminal/expiry에 삭제한다. |
| Authentication Session | DN-C4A-001의 local bearer+refresh 방식에서 refresh family·current generation·revoke 상태를 보관하는 record |
| Refresh Token Lineage | raw token이 아닌 generation별 hash·ROTATED/reuse·replacement 관계. previous-token replay와 concurrency 판정 source |
| Nickname | 서비스 내 표시·검색용 이름. local profile은 trim/NFKC/casefold·전역 unique·30일 변경 cooldown을 사용한다. |
| Onboarding Journey | 최초 취향 입력의 현재 상태와 revision. skip과 submitted completion을 구분한다. |
| Onboarding Preference | 영화에 대한 `LIKE` 또는 `DISLIKE`. 감상 후 C1 Rating이 아니다. |
| OTT Subscription Set | 사용자가 KR provider 중 구독 중이라고 명시한 전체 set과 revision |
| Social Provider Capability | GOOGLE/KAKAO/NAVER adapter가 `DISABLED` 또는 승인 뒤 `AVAILABLE`인지 나타내는 내부 상태 |
| Idempotency Key | 같은 actor/anonymous signup scope·operation·key·canonical body의 최초 결과를 replay하는 불투명 키 |
| Public Auth Idempotency Record | C1 actor ledger와 분리된 signup/verify/resend canonical result ledger. current/previous identity HMAC 또는 signupId는 별도 scope alias row로 같은 record를 가리키며 keyed request HMAC를 저장한다. |
| Pending Signup Recovery Attempt | expired REAL flow recovery admission을 DB 시각·prior flow에 결합해 concurrent loser와 후속 요청을 구분하는 내부 record |

## 2. local 채택과 blocked extension 분리

| 구분 | local profile 구현 값 | blocked extension |
| --- | --- | --- |
| Identity | email signup/login/logout, bearer+refresh, nickname 2..20·전역 unique | password/account lifecycle, production token/key/cookie activation |
| Email | verification 필수, TTL/retry, local Mailpit, raw secret 저장 금지 | production provider/sender/credential |
| Onboarding | LIKE/DISLIKE는 Rating과 분리, 0개 skip 또는 1..10 submit | restart/rerun public operation |
| OTT | KR provider set, 미구독 영화도 추천에서 제외하지 않음 | 없음. 단 protected transport는 DN-C4A-001 의존 |
| Social | GOOGLE/KAKAO/NAVER는 요구 범위 | OAuth flow, key, redirect, linking; 모두 BLOCKED |

과거 표의 `PROPOSED` 표시는 결정 출처를 보존하는 provenance다. 이 문서에 채택된 값은 local 구현
상수이며 production activation 값이 아니다.

## 3. 식별자·시간·revision

- service resource ID는 UUID 문자열이고 MovieLens ID를 API·DB FK로 사용하지 않는다.
- instant는 ISO 8601 UTC다.
- mutation은 8~128자 printable ASCII `Idempotency-Key`를 사용한다.
- update/complete/logout 중 기존 resource를 바꾸는 요청은 `expectedRevision` 또는
  `X-Expected-Revision`으로 stale write를 거부한다.
- email은 응답·로그에서 masked representation만 사용한다. normalized email과 nickname 비교 규칙은
  local schema에서 version과 함께 고정한다.

## 4. 비밀과 credential

- password는 request 처리 중에만 raw로 존재하고 검증된 password hash와 algorithm/version metadata만
  저장한다. reversible encryption이나 평문 fallback은 금지한다.
- verification secret은 challenge 비교용 SHA-256 hash만 둔다. post-commit 전달을 위한 별도 encrypted
  single-use material은 challenge TTL까지만 허용하고 safe outbox/log/trace에는 raw/ciphertext를 넣지 않는다.
  refresh token은 raw 저장·로그·metric label·event payload가 금지이며 비교는 hash로 수행한다.
- access token, OAuth authorization code, provider response body, SMTP/API key도 로그·fixture에 넣지 않는다.
- 운영 secret은 environment secret으로만 주입한다. `.env.example`에는 변수 이름과 설명만 둔다.
- test fake bearer 문자열은 test decoder 전용 label이며 운영 암호가 아니다.

## 5. 인증 전달 local 경계 — DN-C4A-001

현재 C1은 `Authorization: Bearer`를 요구한다. C4A local profile은 호환 방식으로 access bearer와 회전
refresh cookie를 채택한다. production key/cookie 활성화는 별도 승인이다.

| 항목 | Proposed | 대안/영향 |
| --- | --- | --- |
| access | short-lived signed JWT Bearer | session cookie 선택 시 C1·frontend 공통 변경 필요 |
| refresh | opaque rotating token + readable signed CSRF cookie, Secure/SameSite=Lax; refresh는 HttpOnly | refresh와 모든 logout은 exact Origin이 필요; active-session logout만 double-submit header가 필요하고 두 cookie 모두 없는 retry는 CSRF/idempotency 없이 204 clear |
| logout | refresh session current family revoke + refresh/CSRF cookie clear | access JWT는 v1에서 최대 10분 exp까지 유효 가능; all-session은 별도 |
| persistence | access raw 미저장, session+generation별 refresh/CSRF hash lineage | family active/absolute lifetime과 family terminal timestamp+30일까지 보존한 뒤 token hash와 AUTH_SESSION을 같은 cleanup에서 삭제 |

production HTTPS cookie는 `__Host-feelm_refresh`/`__Host-feelm_csrf`다. `http://localhost` local profile은
Secure cookie를 가장하지 않고 충돌하지 않는 `feelm_local_refresh`/`feelm_local_csrf`를 사용한다. local cookie는
loopback host에서만 발급하며 Path=/, no Domain, SameSite=Lax이고 refresh만 HttpOnly다. production profile에서
local 이름을 허용하거나 두 profile 이름을 동시에 발급하지 않는다. clear response도 선택된 profile의 발급
attributes와 같은 name/Path/Domain/SameSite/Secure/HttpOnly에 `Max-Age=0`과 epoch Expires를 더한다.

## 6. Onboarding과 Rating 분리

| 입력 | source of truth | 허용 사용 | 금지 |
| --- | --- | --- | --- |
| Onboarding LIKE/DISLIKE | `ONBOARDING_PREFERENCE` | versioned onboarding feature/fold-in 실험 입력 | C1 Rating·Frame·Popcorn·Rating aggregate 생성 |
| C1 Rating 1~5 | `RATING` | Film·Popcorn·취향 aggregate와 승인된 추천 입력 | onboarding preference로 역쓰기 |
| 미선택/skip | preference row 없음 + journey status | fallback 선택 | DISLIKE·0점으로 해석 |

REC-EV-003B sampled 평가는 K1~K20 ranking alpha가 모두 0이었다. 최신 REC-EV-011 full-catalog는
K10 alpha 0.2를 선택했고 evaluation paired NDCG CI `[0.000253,0.002783]`가 0 위였지만 effect가 작다.
따라서 K10은 후속 offline ranking 후보일 뿐 champion·expected-star·HIGH confidence·온보딩 UX
승인으로 표현하지 않는다.

## 7. OTT set 의미

- `NOT_CONFIGURED`: 사용자가 아직 답하지 않았다.
- `CONFIGURED` + empty set: 구독 중인 provider가 없다고 명시했다.
- `CONFIGURED` + non-empty set: 명시한 provider들을 구독 중이다.
- `SKIPPED`: OTT 단계에서 나중에 설정하기를 선택했다.
- 어느 상태도 영화 추천 후보 제외 조건이 아니다. C0 OTT 표시·정렬의 `isSubscribed` 계산에만
  영향을 줄 수 있다.

## 8. 오류·열거 공격 정책

- 오류는 `code`, 안전한 `message`, `traceId`, `fieldErrors[]`를 가진다.
- protected token 없음·무효는 401이다. pending membership은 login에서 403 후보지만 상세 문구가
  email 존재 여부를 불필요하게 노출하지 않아야 한다.
- duplicate email/nickname의 public 응답 의미는 DN-C4A-002/003 승인 대상이다.
- verification resend와 login은 IP·account scope rate limit이 필요하나 수치는 DN-C4A-003 전
  proposed다. Public auth의 같은 idempotency key/body replay는 Redis quota를 소비하지 않고 먼저
  replay하며, idempotency miss에서만 Redis admission을 거친 뒤 PostgreSQL domain transition을 수행한다.
- public auth rate limit은 모든 instance가 공유하는 Redis server UTC clock과 atomic counter+TTL authority를
  사용한다. local-memory fallback은 금지하고 authority 장애는 mutation 전 503 fail-closed다.
- current/previous HMAC key rotation 중 Redis Function은 두 version의 같은 logical-window counter를 원자 조회해
  **합산** 판정하고 허용된 miss에서 current key만 증가시킨다. key version별로 별 quota를 주지 않는다.
  previous key는 최대 quota window와 pre-signup idempotency retention이 모두 지난 뒤 retire한다.
- 최초 signup mail과 resend는 같은 `MAIL_IDENTITY=5/h+10/d`, `MAIL_IP=20/h+100/d`를 공유한다.
  REAL/DECOY idempotency miss 모두 같은 aggregate quota를 소비한다.
- 그 밖의 rate scope와 한도는 `VERIFY_IP=30/h`,
  `LOGIN_IDENTITY=5 failures/15m exponential 30s..15m`,
  `LOGIN_IP=50/15m then 15m throttle`의 structured policy다. key에는 raw email/IP를 넣지 않고
  server-keyed identity projection과 coarse IP projection만 쓴다.
- challenge당 wrong-attempt 5회와 `failed_attempt_count`는 PostgreSQL `EMAIL_SIGNUP_PUBLIC_FLOW`가
  단일 source of truth이다. Redis는 IP/identity abuse admission만 담당하며 DB rollback을 domain
  attempt로 바꾸지 않는다. Redis admission이 이미 소비된 뒤 DB가 실패하면 quota는
  보수적으로 유지할 수 있지만 flow attempt/status는 변하지 않는다.
- verification link origin은 request Host/Forwarded 계열을 신뢰하지 않고 versioned server config의 exact
  HTTPS frontend origin만 사용한다. missing/invalid config는 dispatch/readiness fail-closed다.
- anonymous signup은 C1의 `actor_user_id` idempotency PK에 가짜 user를 넣지 않는다. pre-signup current/previous
  identity HMAC scope 또는 stable signupId scope를 가진 `PUBLIC_AUTH_IDEMPOTENCY_RECORD`를 사용하고,
  password/verificationSecret을 포함한 body equality는 versioned keyed HMAC-SHA-256으로만 비교한다.
  plain body SHA-256은 offline password oracle가 되므로 금지한다.
