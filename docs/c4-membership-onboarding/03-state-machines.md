# C4A 상태 기계

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`

## 1. Membership — DN-C4A-003

```text
ABSENT
  └─ createEmailSignup [PROPOSED]
       → PENDING_EMAIL_VERIFICATION
          ├─ valid challenge consume → ACTIVE
          ├─ expired challenge → PENDING_EMAIL_VERIFICATION (resend 가능)
          └─ delivery failure → PENDING_EMAIL_VERIFICATION (deliveryStatus만 실패)

PENDING_EMAIL_VERIFICATION
  ├─ flow 만료 전 resend → 같은 signupId + 새 current challenge version
  ├─ flow 24h 만료 뒤, createdAt+30d 전 re-signup + 같은 password
  │    → 새 REAL signupId + 새 current challenge (pendingPurgeAt 연장 없음)
  ├─ re-signup password 불일치 → DECOY signupId (account 변화 없음)
  └─ pendingPurgeAt 도달 + verification race loser → account/credential/profile purge
```

- ACTIVE 전 protected actor가 될 수 없다.
- challenge expiry는 membership 삭제나 DISABLED를 뜻하지 않는다.
- createEmailSignup은 존재 여부와 무관하게 generic 202를 반환한다. 신규면 실제 signup handle,
  duplicate/unknown이면 같은 형식·expiry/resend 값을 가진 persisted decoy signupId다. 동일 Idempotency-Key
  replay만 최초 safe response를 그대로 받는다.
- 실제/decoy 모두 masked email·`QUEUED`·response time+10분 expiry·+60초 resend·revision 1을
  반환한다. 신규 경로는 actual Argon2id/transaction/async enqueue, duplicate/unknown 경로는 dummy
  Argon2id+queue-equivalent work를 거친 뒤 supported PHC workloads의 calibrated common floor+0~75ms
  CSPRNG jitter를 적용한다. actual/decoy handle은
  모두 24시간 public-flow row로 저장해 follow-up verify/resend의 cooldown·attempt·revision을 동일하게
  처리한다. decoy에는 userId·secret·mail outbox가 없고 public 응답 timing이 존재 비노출을 완전히
  보장한다고 주장하지 않는다.
- `signupId`는 `EMAIL_SIGNUP_PUBLIC_FLOW`의 stable handle이고 challenge ID가 아니다. REAL flow만 nullable
  `current_challenge_id`로 versioned challenge를 가리킨다. resend는 handle을 바꾸지 않고 challenge version과
  flow revision만 증가시킨다. 24시간 flow expiry는 membership 삭제가 아니며, 30일 purge 전 password를
  다시 증명한 re-signup recovery가 새 REAL flow를 만든다.
- recovery 후보는 DB `admitted_at`과 prior expired signupId를 가진 `PENDING_SIGNUP_RECOVERY_ATTEMPT`를 먼저
  만들고 account lock 뒤 재검증한다. winner flow는 attempt/prior flow를 참조한다. lock 대기 loser는 자신의
  admittedAt 뒤 같은 prior flow에서 생성된 winner에만 `LOST_REPLAY`로 연결한다. 요청 시작 시 새 OPEN flow가
  이미 보인 후속 요청은 attempt 없이 DECOY여서 race loser와 결정론적으로 구분된다.

## 2. Email Verification Challenge — DN-C4A-003

```text
REQUESTED
  └─ ephemeral secret generated + hash persisted → ACTIVE
       ├─ correct secret → CONSUMED
       ├─ now >= expiresAt → EXPIRED
       ├─ resend creates new challenge → SUPERSEDED
       └─ failed attempt limit → EXHAUSTED [PROPOSED]
```

동시에 검증 가능한 ACTIVE real challenge는 membership당 최대 하나다. challenge는 resend마다 새 row/version이며
과거 row를 덮어쓰지 않는다. decoy public flow에는 challenge row/secret이 없고 모든 submitted secret은
public-flow wrong-attempt transition만 수행한다. actual/decoy 모두 public-flow row lock 아래 verify/resend를
직렬화해 concurrent consume/resend 한 transition만 winner가 된다. raw secret은 transition 입력이며 state
column, safe outbox, audit, log에 없다. wrong attempt의 단일 authority는 PostgreSQL public-flow
`failed_attempt_count`이며 Redis는 coarse IP/global abuse만 담당한다.

signup/resend transaction은 raw secret을 메모리에서 한 번 만들고 challenge에는 SHA-256 hash만,
`VERIFICATION_DELIVERY_MATERIAL`에는 AES-256-GCM ciphertext/nonce/key version만 저장한다. safe outbox에는
materialId/challenge pseudonym만 있다. post-commit worker는 lease 후 메모리 복호화로 pinned-origin fragment
link를 만든다. provider 수락 전 crash는 같은 material/challenge를 retry하고, 수락 뒤 ciphertext 삭제 전 crash는
같은 single-use link가 중복 발송될 수 있을 뿐 새 secret/challenge를 만들지 않는다. provider 수락 또는 challenge
terminal/superseded/expired 시 ciphertext와 nonce를 지우며 challenge expiry를 넘기지 않는다.
DB/outbox/cache/log/trace/test artifact에는 raw recipient/secret/link가 없다. provider 전달 자체는 recipient와
single-use link를 worker memory와 TLS provider request wire에 일시적으로 필요로 하며, adapter는 request/response
body·header·exception을 관측성으로 복제하지 않는다.

public mapping은 handle kind와 무관하게 고정한다: malformed 400 `VALIDATION_ERROR`, wrong 400
`VERIFICATION_INVALID`, unknown/terminal/24h-expired 400 `SIGNUP_FLOW_INVALID_OR_EXPIRED`, attempt exhausted
429 `VERIFICATION_ATTEMPTS_EXHAUSTED`, cooldown/global limit 429 `AUTH_FLOW_THROTTLED`, idempotency body conflict
409 `IDEMPOTENCY_KEY_REUSED`, concurrent state conflict 409 `AUTH_STATE_CONFLICT`, dependency failure 503
`AUTH_DEPENDENCY_UNAVAILABLE`; correct current REAL challenge만 200이며 허용된
actual/decoy resend는 202다.

signup/verify/resend의 idempotency는 C1 actor ledger가 아니라 `PUBLIC_AUTH_IDEMPOTENCY_RECORD`다. signup은
current/previous identity HMAC scope, verify/resend는 stable signupId scope를 쓰며 derived scope+key advisory lock을
정렬 획득한다. request equality는 keyed/versioned HMAC만 사용하고, record/domain/safe response는 같은 transaction이다.
terminal success record는 flow cleanup FK cascade와 분리해 terminal+24h replay를 유지한다.

메일 링크는 request `Host`, `Forwarded`, `X-Forwarded-Host`를 사용하지 않고 versioned server config의
승인된 exact HTTPS `VERIFICATION_FRONTEND_ORIGIN`으로 만든다. config가 없거나 invalid면 dispatch와 readiness를
fail-closed한다. 그 URL **fragment**에 signupId/raw secret을 넣고 query에는 넣지 않는다. React verify 화면은
third-party/network 요청 전에 fragment를 읽고 `history.replaceState`로 제거한 뒤 POST body에만 전송한다.
`Referrer-Policy: no-referrer`를 사용하고 fragment/error telemetry를 기록하지 않는다.

## 3. Authentication Session — DN-C4A-001 proposed

```text
AUTH_SESSION.ACTIVE + REFRESH_TOKEN.ACTIVE(generation=N)
  ├─ valid Origin + matching signed CSRF + refresh lock winner
  │    → old token ROTATED(replacement=N+1)
  │    → new token ACTIVE(N+1), new CSRF, session generation=N+1
  ├─ old token reused within PostgreSQL clock rotatedAt+5s
  │    → 409 REFRESH_RACE_RETRY_NEW_COOKIE, family stays ACTIVE, no Set-Cookie
  ├─ old token reused after 5s or generation < N-1
  │    → session REVOKED_FAMILY, all family tokens REVOKED/REUSED
  ├─ exact Origin + valid refresh+CSRF logout → session REVOKED, family tokens REVOKED, cookies cleared
  ├─ exact Origin + both cookies absent → 204 exact clear only
  ├─ partial cookie pair → 403 CSRF_FORBIDDEN
  ├─ exact Origin + valid CSRF + invalid session → 401 AUTH_SESSION_INVALID
  └─ idle/absolute expiry → EXPIRED
```

- login/refresh/logout 모두 exact Origin allowlist를 사용한다. refresh/active logout은 선택 profile CSRF cookie와
  `X-CSRF-Token` equality,
  session/generation-bound signature를 검증한다. 실패는 session 존재 여부를 숨기는 403이다.
- concurrent refresh는 session+presented token row lock으로 한 winner만 replacement를 만든다. 서버는 raw
  replacement를 저장하지 않으므로 loser에게 같은 token을 replay하지 않는다.
- `rotated_at`과 race 비교 now는 같은 PostgreSQL primary의 `clock_timestamp()`로만 기록·판정한다. app,
  Redis, client clock은 이 5초 경계의 authority가 아니다. terminal family lineage/token hash/session row는
  terminalAt+30일 cleanup에서 함께 삭제한다.
- logout은 cookie 유무보다 먼저 exact allowed Origin을 요구한다. active refresh가 있으면 signed CSRF 뒤 family를
  즉시 폐기한다. 두 auth cookie가 모두 없는 retry는 Origin 검증 뒤 204와 exact clear header만 반환하고
  CSRF/idempotency/session/audit를 변경하지 않는다. 하나만 있으면 403이다. valid CSRF지만 refresh가
  unknown/expired/revoked이면 401이며 같은 key의 성공 replay만 204를 재생한다. 이미 발급된 bearer access JWT는 최대 10분
  `exp`까지 유효할 수 있다. UI는 memory token을 즉시 지우며 “access 즉시 서버 폐기”를 주장하지 않는다.
- 현재 상태 기계는 bearer access + opaque refresh 비교안일 뿐 APPROVED가 아니다.
- access JWT는 RS256 allowlist, required `alg/kid/typ`, exact iss/aud, required
  `sub/sid/jti/iat/nbf/exp`, 30초 leeway를 모두 검증한다. current+previous key overlap은 최대 48시간이고
  unknown/missing kid 또는 stale key registry는 401/readiness fail-closed다.

## 4. Onboarding Journey — DN-C4A-004

```text
NOT_STARTED
  ├─ list/save preference → IN_PROGRESS
  ├─ complete(SKIPPED, expectedPreferenceCount=0 and locked count=0) → SKIPPED
  └─ complete(SUBMITTED, count >= approved minimum) → COMPLETED

IN_PROGRESS
  ├─ replace preferences → IN_PROGRESS (revision + 1)
  ├─ complete(SKIPPED) only when locked active count=0 → SKIPPED
  └─ complete(SUBMITTED, valid count) → COMPLETED

COMPLETED or SKIPPED
  └─ restart [PROPOSED] → new IN_PROGRESS journey; prior preferences SUPERSEDED
```

- `SKIPPED`는 DISLIKE set이 아니다.
- minimum/max와 restart 허용은 DN-C4A-004 전 transition Gate다.
- recommender projection status는 journey state와 별도다.

## 5. Preference lifecycle

```text
ACTIVE(current journey, movie, LIKE|DISLIKE)
  ├─ full-set replace에서 값 변경 → ACTIVE(new revision value)
  ├─ full-set replace에서 누락 → SUPERSEDED
  └─ journey restart → SUPERSEDED
```

C1 Rating으로 promote하거나 Rating 삭제와 함께 지우는 transition은 없다.

## 6. OTT Subscription Set

```text
NOT_CONFIGURED
  ├─ replace([] or providerIds) → CONFIGURED
  └─ skip → SKIPPED

CONFIGURED or SKIPPED
  └─ replace([] or providerIds) → CONFIGURED (revision + 1)
```

empty CONFIGURED set과 SKIPPED/NOT_CONFIGURED는 서로 다른 상태다. USER_ACCOUNT signup과 같은 transaction에서
NOT_CONFIGURED set을 정확히 하나 만들며, SKIPPED request/response는 providerIds가 JSON Schema상 empty다.

## 7. Social Provider Capability — DN-C4A-005

```text
DISABLED
  └─ decision + credentials + redirect allowlist + contract tests → AVAILABLE
AVAILABLE
  └─ credential/health/decision rollback → DISABLED
```

C4A 초안 상태는 GOOGLE/KAKAO/NAVER 모두 `DISABLED`다. DISABLED 동안 public OAuth operation과
연결된 UI action은 존재하지 않는다.

향후 explicit linking이 승인되어도 다음 transaction Gate를 먼저 통과한다.

```text
ABSENT
  └─ ACTIVE user + current session + recent password reauth
       → LINK_TX.PENDING(provider, expectedIssuer, exactRedirect, state, PKCE-S256, nonce)
          ├─ state/nonce/issuer/audience/PKCE success + unique issuer/subject
          │    → LINK_TX.CONSUMED + SOCIAL_IDENTITY.LINKED (same DB transaction)
          ├─ provider/issuer mismatch → FAILED_MIX_UP
          ├─ unique identity conflict → FAILED_CONFLICT (owner 숨김)
          └─ now >= 10m → EXPIRED
```

callback 입력은 issuer/token endpoint/redirect URI를 선택하지 못하고 transaction의 server allowlist 값만
사용한다. verified email claim도 account merge key가 아니다.
