# C4A 제품·보안 결정 패킷

> 계약 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`  
> 결정 상태: `APPROVED_LOCAL_PROFILE_5_OF_5`  
> 승인 결과: `DN-C4A-001`~`005`의 보수 권장 token을 local profile에 채택  
> `LOCAL_PROFILE_IMPLEMENTATION_AUTHORITY: YES` — 13개 operation·7개 screen 계약만
> `PRODUCTION_ACTIVATION_AUTHORITY: NO` — 배포·실제 mail·OAuth·lifecycle 권위 없음; main OpenAPI에는 local 13개 operation만 병합

이 문서는 이미 확정된 요구사항을 바꾸지 않고, 제품 소유자가 C4A의 다섯 P0 결정을 한 번에
비교할 수 있게 수치화한 보수 기본값을 local profile에 채택한다. production 상수나 배포 권위는 아니며,
OpenAPI fragment, 상태 머신, ERD, AC, task DAG, trace를 같은 변경에서 정렬한다.

## 0. 한눈에 보는 권장안

| 결정 | local 채택 선택 | 제품 영향 | production 경계 |
| --- | --- | --- | --- |
| `DN-C4A-001` | 10분 access JWT Bearer + 매회 회전하는 opaque HttpOnly refresh cookie, 현재 session logout | C1 Bearer를 유지하면서 브라우저 장기 secret의 JavaScript 노출을 줄인다. | production token/key/cookie activation 차단 |
| `DN-C4A-002` | email/password/nickname만, password 15~128자, nickname NFKC+casefold 전역 unique 2~20자 | 가입 필드를 최소화하고 Party nickname 식별을 안정화한다. | password/account lifecycle 차단 |
| `DN-C4A-003` | 이메일 인증 필수, 256-bit 일회용 token hash만 저장, TTL 10분, Mailpit local only | 오입력 계정과 무단 email 사용을 줄이지만 가입 단계가 하나 늘어난다. | production mail 차단 |
| `DN-C4A-004` | 0개 skip 또는 1~10개 선택 완료, K10은 권장 목표 | 강제 K10 이탈 위험 없이 evidence 입력을 받을 수 있다. | restart/rerun operation 차단 |
| `DN-C4A-005` | Google/Kakao/Naver 모두 disabled, 기존 인증 계정의 명시적 linking만 후보 | email claim 자동 병합으로 인한 계정 탈취를 막는다. | social route/button 없음 |

다섯 보수 결정은 local profile에서 함께 채택했다. `DN-C4A-004`는 같은 제품 의미를 가진
`REC-PD-003=OPTIONAL_UP_TO_10_WITH_SKIP`과 local 계약에서 동일하게 고정한다.

---

## DN-C4A-001 — 인증 전달·session 수명·logout

**권장 기본값:** `ACCESS_TTL=10m`, `REFRESH_IDLE_TTL=7d`, `REFRESH_ABSOLUTE_TTL=30d`,
`REFRESH_ROTATION=EVERY_USE`, `LOGOUT_SCOPE=CURRENT_SESSION`,
`CSRF=ORIGIN_PLUS_SIGNED_DOUBLE_SUBMIT`, `ROTATED_REFRESH_RACE_GRACE=5s`,
`COOKIE_MUTATION_AUTH=REFRESH_PLUS_ORIGIN_PLUS_CSRF`,
`JWT_AFTER_LOGOUT=VALID_UNTIL_EXP_MAX_10m`, `REFRESH_RACE_CLOCK=POSTGRES_CLOCK_TIMESTAMP`,
`LINEAGE_RETENTION_AFTER_TERMINAL=30d`, `COOKIELESS_LOGOUT_RETRY=204_CLEAR_NO_MUTATION` (exact allowed Origin은 필수이고 CSRF/idempotency만 생략).

### 구체 계약 후보

| 항목 | 권장값 | 비교 근거 |
| --- | --- | --- |
| access | RS256 allowlist, required `alg/kid/typ`, exact versioned iss/aud, required `sub/sid/jti/iat/nbf/exp`, UUID ids, 30초 leeway, TTL 10분의 signed JWT | unknown kid/alg/claim/time은 401이고 stale key registry는 non-test readiness를 fail-closed한다. |
| access client storage | React memory only, 새로고침 후 refresh | localStorage/sessionStorage의 장기 탈취면을 만들지 않는다. |
| refresh | CSPRNG 256-bit opaque token, DB에는 SHA-256 hash와 family/session metadata만 저장 | token 자체는 고엔트로피라 빠른 hash로 lookup 가능하며 raw token은 복구할 수 없다. |
| refresh cookie | `__Host-feelm_refresh`; `Secure; HttpOnly; SameSite=Lax; Path=/`; `Domain` 없음 | host scope와 JavaScript 접근 차단. 개발 HTTP에서는 local profile만 별도 non-production 이름을 쓴다. |
| CSRF cookie/header | JavaScript-readable `__Host-feelm_csrf`; `Secure; SameSite=Lax; Path=/`; `Domain` 없음 + `X-CSRF-Token` | signed value를 refresh family·generation에 결합한다. login에서 두 cookie를 함께 발급하고 refresh 성공 때 함께 회전한다. |
| local HTTP cookie | loopback-only `feelm_local_refresh`(HttpOnly)/`feelm_local_csrf`, Secure 없음, SameSite=Lax, Path=/, Domain 없음 | `__Host-`+Secure를 HTTP localhost에서 거짓 설정하거나 production 이름과 충돌시키지 않는다. production에서는 local 이름을 거부한다. |
| refresh 수명 | last use 기준 7일 idle, 최초 login 기준 30일 absolute 중 먼저 도달 | 매일 쓰는 사용자는 유지하되 분실 browser의 무기한 session을 금지한다. |
| rotation lineage | refresh 성공마다 prior `ACTIVE→ROTATED`, generation+1 token row를 같은 transaction에서 만들고 replacement 관계를 family terminal 시각+30일까지 보존한 뒤 token hash와 session을 같은 cleanup으로 삭제 | active/absolute 최대 30일과 종료 후 보안 보존 30일을 혼동하지 않고 raw token hash를 무기한 보존하지 않는다. |
| concurrent/replay | PostgreSQL primary `clock_timestamp()` 기준 직전 token이 회전 후 5초 이내 재사용되면 409·Set-Cookie 없음·family 유지; 5초 초과 또는 더 오래된 generation 재사용은 family 전체 revoke | app/Redis/client clock skew가 보안 경계를 바꾸지 않는다. loser에게 winner token을 재전달하지 않는다. |
| CSRF | refresh/logout에 exact Origin allowlist + session/generation-bound signed double-submit cookie/header | SameSite=Lax만 단독 방어로 보지 않는다. absent/null Origin과 cookie/header 불일치는 mutation 전에 403이다. |
| CORS | credential 요청은 exact origin만, wildcard 금지 | cookie가 포함된 cross-origin 요청 범위를 제한한다. |
| logout | 모든 branch가 exact Origin 필수. active pair는 signed CSRF로 family revoke, cookie-less retry만 CSRF/idempotency 없이 204·DB/audit mutation 0. partial pair는 403, valid CSRF+invalid session은 401, 같은 key 성공 replay만 204 | 모든 204는 선택 profile 두 cookie를 각각 별도 Set-Cookie로 exact clear한다. |
| cookie clear | production은 발급과 같은 `__Host-` name, Path=/, no Domain, SameSite=Lax, Secure, refresh만 HttpOnly + Max-Age=0 + epoch Expires; local은 local name과 같은 attributes(Secure 없음) + Max-Age=0 + epoch Expires | 잘못된 Path/name/Secure로 stale cookie가 남는 logout을 막는다. |
| JWT key | production asymmetric signing key, `kid`로 rotation; 새/이전 public key 48시간 overlap | verifier 무중단 회전을 허용하며 private key를 consumer에 배포하지 않는다. |

### 반대안 손실

- cookie-only server session은 CSRF surface와 C1/frontend 인증 경계를 동시에 migration해야 한다.
- 30일 access token처럼 refresh가 없는 방식은 탈취 후 즉시 revoke가 어렵고 C1 호출마다 긴 secret을
  브라우저 저장소에 두게 된다.
- 기본 all-session logout은 공용/분실 기기 대응에는 강하지만 정상적인 다른 기기 작업도 끊는다.
  필요하면 이후 별도 “모든 기기에서 로그아웃” mutation으로 추가한다.

### 보안 영향

- refresh와 complete-cookie-pair logout은 `Origin`과 signed double-submit을 모두 검증한다. cookie-less logout도
  exact Origin은 필수이고 CSRF/idempotency만 생략한다.
- JWT는 algorithm allowlist, issuer/audience/time/jti/session 검증이 필요하고 email/nickname을 claim에
  넣지 않는다.
- refresh winner의 prior rotation·replacement row·새 generation·session current generation 반영과 replay
  family revoke는 각각 한 transaction이어야 한다.
- logout은 refresh family의 새 발급을 즉시 막지만 이미 발급된 stateless access JWT를 즉시 무효화한다고
  주장하지 않는다. access에는 `sid`와 최대 10분 `exp`가 있고 v1 protected API는 매 요청 session DB
  introspection을 하지 않는다.
- access·refresh·CSRF raw value, Authorization/Cookie header는 log·event·metric에 없다.

### UX 영향

- active browser는 최대 7일 idle까지 자동 갱신되고, 30일이 되면 다시 로그인한다.
- access 10분 만료가 API error로 보이지 않도록 한 번의 refresh single-flight 후 원 요청을 재시도한다.
- refresh 실패 또는 replay family revoke는 auth state를 지우고 generic 재로그인 안내를 제공한다.

### Rollback

1. refresh anomaly 시 발급 feature를 끄고 모든 refresh family를 revoke한다.
2. logout/replay 직전 이미 복제된 access JWT는 최대 10분 뒤 자연 만료하므로 강제 전면 key 교체 없이
   bounded rollback이 가능하다. 즉시 폐기가 제품 필수가 되면 별도 denylist/session introspection 결정을 연다.
3. frontend는 C1 bearer 호출을 유지한 채 재로그인으로 복구한다.
4. TTL 변경은 새 session부터 적용하고 기존 session 연장은 금지한다.

### 운영 credential 경계

- secret store 전용 이름: `AUTH_JWT_SIGNING_PRIVATE_KEY`, `AUTH_JWT_SIGNING_KEY_ID`.
- public verification material은 secret이 아니지만 versioned config로 배포한다.
- 실제 key, JWT, cookie, allowed production origin 값은 이 패킷·fixture·`.env.example`에 넣지 않는다.

**local 채택:** `BEARER_JWT_ROTATING_REFRESH_CURRENT_LOGOUT`; production activation은 별도 승인.

---

## DN-C4A-002 — 가입 필드·password·nickname

**권장 기본값:** `SIGNUP_FIELDS=email,password,nickname`, `PASSWORD_LENGTH=15..128`,
`NICKNAME_LENGTH=2..20`, `NICKNAME_NORMALIZATION=TRIM_NFKC_CASEFOLD`,
`NICKNAME_UNIQUENESS=GLOBAL`, `NICKNAME_CHANGE_COOLDOWN=30d`.

### 구체 계약 후보

| 항목 | 권장값 | 비교 근거 |
| --- | --- | --- |
| signup field | email, password, nickname만 필수; legal name/성별/생년월일 없음 | 현재 서비스 기능에 필요하지 않은 개인정보를 수집하지 않는다. |
| password length | Unicode code point 15~128, 공백·붙여넣기·password manager 허용 | single-factor password의 길이를 확보하고 긴 passphrase를 막지 않는다. |
| password rule | 대/소문자·숫자·특수문자 조합 강제 없음, common/breached blocklist 적용 | 예측 가능한 조합 충족보다 길이·blocklist·rate limit에 집중한다. |
| password hash | Argon2id `m=19456 KiB, t=2, p=1` 이상을 hard security floor로 두고 배포 환경 p95 500ms 이하로 benchmark | p95 500ms는 용량 목표이지 parameter 하향 근거가 아니다. floor와 목표를 동시에 만족하지 못하면 non-test auth readiness/startup을 fail-closed하고 자원·동시성·인스턴스 수를 조정한다. unique salt와 algorithm/version을 hash에 포함한다. |
| nickname display | trim 전 원문은 저장하지 않고 trim된 display만 저장 | 의도치 않은 양끝 공백으로 다른 이름처럼 보이는 문제를 막는다. |
| nickname key | trim → Unicode NFKC → default casefold | 전각/호환문자와 영문 대소문자 collision을 하나의 비교 key로 만든다. |
| nickname 문자 | Unicode Letter/Number와 `_`만, whitespace/control/format/bidi override 금지 | Party exact invite에서 시각적 혼동·invisible 이름을 줄인다. |
| nickname 길이 | 정규화 전후 모두 2~20 Unicode code point | 모바일 표시와 입력 가능성을 균형화한다. UTF-16 unit/byte로 세지 않는다. |
| unique scope | active/pending 모든 service account에 global unique | C3 nickname exact invite가 여러 사용자를 반환하지 않게 한다. |
| reserved | 운영자 사칭·금칙어의 versioned blocklist | validation rule을 code에 하드코딩하지 않고 변경 이력을 둔다. |
| 변경 | 30일 cooldown, collision은 409; 현재 revision 유지 | 초대 식별자 churn과 사칭을 줄인다. 제품 support 없이 강제 rename은 하지 않는다. |

email 비교 key는 ASCII whitespace trim, Unicode NFC, IDNA domain lowercase, 전체 case-insensitive
product key로 제안한다. Gmail dot/plus 제거처럼 provider별 alias 추측은 하지 않는다. display response는
항상 masked email이며 normalized key는 API/log에 없다.

### 반대안 손실

- nickname 중복을 허용하면 C3 exact invite에 별도 discriminator나 검색 노출이 필요해 privacy 비용이
  커진다.
- 8~10자 password minimum은 입력이 쉽지만 single-factor 방어 여유가 작다.
- legal name을 받으면 고객지원에 쓸 수 있으나 현재 verified identity 용도가 없고 개인정보 보유만 늘어난다.
- 30일 cooldown은 사용자의 오타 수정이 불편하다. 반대로 제한 없음은 초대 혼동과 사칭 대응을 어렵게 한다.

### 보안 영향

- normalized nickname collision은 unique index와 transaction으로 한 winner만 허용한다.
- password는 raw 저장·log·analytics·outbox 금지이며 reversible encryption fallback도 없다.
- blocklist 판정은 원문 password를 외부 telemetry로 보내지 않는 local 또는 privacy-preserving adapter만 쓴다.

### UX 영향

- 가입 form은 세 필드뿐이고 password paste/autofill/show-toggle을 제공한다.
- nickname validation은 submit 뒤 한꺼번에 숨기지 않고 입력 단계에서 허용 문자와 2~20자를 설명한다.
- normalization collision은 어떤 account와 충돌했는지 노출하지 않는 “사용할 수 없는 닉네임” 409다.

### Rollback

1. normalization rule 변경은 `nickname_normalization_version`을 올리고 dual-read collision audit 후 migration한다.
2. 30일 cooldown은 config로 7일/0일로 줄일 수 있지만 이미 허용한 rename을 취소하지 않는다.
3. 길이 maximum을 줄일 때 기존 nickname은 grandfather하고 신규/변경에만 적용한다.

### 운영 credential 경계

- nickname·email normalization에는 외부 credential이 없다.
- optional breached-password service를 도입할 경우 별도 privacy/credential ADR 전 network call을 금지한다.
- password hash pepper를 추가한다면 값은 `AUTH_PASSWORD_PEPPER` secret store에만 두며 본 패킷에는 없다.

**local 채택:** `MINIMAL_FIELDS_GLOBAL_NICKNAME`; production 정책 변경은 별도 승인.

---

## DN-C4A-003 — 이메일 인증·중복·rate limit·메일 adapter

**권장 기본값:** `EMAIL_VERIFICATION=REQUIRED`, `VERIFICATION_TOKEN_ENTROPY=256bit`,
`VERIFICATION_TTL=10m`, `VERIFICATION_ATTEMPTS=5`, `RESEND_COOLDOWN=60s`,
`SHARED_MAIL_IDENTITY_LIMIT=5/h+10/d`, `SHARED_MAIL_IP_LIMIT=20/h+100/d`, `LOCAL_MAIL=MAILPIT_NO_AUTH`,
`PRODUCTION_MAIL=DEFERRED_CREDENTIAL_GATE`, `GENERIC_SIGNUP_HANDLE=PERSISTED_DECOY_UUID`,
`SIGNUP_RESPONSE_FLOOR=CALIBRATED_SUPPORTED_PHC_P99_PLUS_25MS`, `SIGNUP_RESPONSE_JITTER=0..75ms`,
`VERIFICATION_DELIVERY=HTTPS_FRAGMENT_THEN_POST_BODY`,
`VERIFICATION_LINK_ORIGIN=PINNED_CONFIG_ONLY`, `RATE_LIMIT_AUTHORITY=SHARED_ATOMIC_FAIL_CLOSED`,
`SIGNUP_FLOW_TTL=24h_NO_EXTENSION`, `PENDING_ACCOUNT_PURGE=30d`,
`PENDING_RESIGNUP_RECOVERY=PASSWORD_VERIFIED_NEW_REAL_FLOW`,
`PUBLIC_FLOW_MODEL=STABLE_SIGNUP_ID_VERSIONED_CURRENT_CHALLENGE`,
`DELIVERY_MATERIAL=AES256_GCM_SINGLE_USE_VERSIONED_KEY`,
`PUBLIC_AUTH_IDEMPOTENCY=SEPARATE_LEDGER_KEYED_REQUEST_HMAC`,
`RECOVERY_LINEARIZATION=PERSISTED_ADMISSION_PRIOR_FLOW`,
`HMAC_ROTATION_QUOTA=ATOMIC_CURRENT_PREVIOUS_AGGREGATE`.

### 구체 계약 후보

| 항목 | 권장값 | 비교 근거 |
| --- | --- | --- |
| activation | email 확인 전 `PENDING_EMAIL_VERIFICATION`, 성공 후 `ACTIVE` | 잘못 입력하거나 소유하지 않은 email로 protected actor가 되는 것을 막는다. |
| token delivery | CSPRNG 32-byte(256-bit) URL-safe single-use token을 HTTPS URL **fragment**에만 두고 React가 즉시 memory로 읽은 뒤 `history.replaceState`로 제거하여 POST body로 제출 | query는 server/access log·Referer에 노출될 수 있다. fragment는 HTTP request에 전송되지 않으며 제거 전 network/third-party resource를 금지한다. |
| link origin | versioned server config의 승인된 HTTPS `VERIFICATION_FRONTEND_ORIGIN` 한 값으로만 link를 생성하고 request `Host`·`Forwarded`·`X-Forwarded-Host`는 사용하지 않음 | Host-header poisoning으로 공격자 origin에 fragment secret을 전달하지 않는다. config가 없거나 HTTPS exact-origin validation에 실패하면 dispatch/readiness를 fail-closed한다. 실제 production origin 값은 secret store/config에만 둔다. |
| storage/dispatch | challenge에는 SHA-256 hash만, 별도 single-use delivery material에는 AES-256-GCM ciphertext/nonce/key version만 두고 safe outbox는 materialId만 참조. worker lease가 commit 후 메모리 복호화 | raw recipient/link는 DB/outbox/cache/log/trace/test artifact에 없고 worker memory와 TLS provider request wire에만 일시 허용한다. adapter 관측성은 redact한다. provider 수락 전 crash는 같은 material retry, 수락 후 삭제 전 crash는 같은 link 중복만 허용한다. terminal/expiry에는 ciphertext/nonce를 삭제한다. |
| TTL | 10분 | 노출 창을 짧게 하면서 일반적인 메일 지연·전환 시간을 허용한다. 5분은 지연에 취약하고 30분은 창이 길다. |
| wrong attempts | PostgreSQL public-flow `failed_attempt_count` 5회 후 `EXHAUSTED`; Redis는 coarse IP 30회/시간만 | attempt source를 이중화하지 않는다. resend는 budget을 소비하고 actual/decoy count를 0으로 reset한다. |
| signup+resend shared mail | 같은 normalized email 60초 resend cooldown, 최초 발송과 resend 합산 5회/시간·10회/일 | initial signup으로 별 quota를 얻는 우회를 막고 중복 click과 mail bombing을 제한한다. |
| shared mail IP | 최초 발송과 resend 합산 20회/시간·100회/일 | 여러 대상 mail bombing을 제한하되 개발 shared network를 완전히 잠그지 않는다. |
| signup generic 202 | 신규는 REAL, duplicate ACTIVE/unknown/PENDING password 불일치는 persisted decoy `EMAIL_SIGNUP_PUBLIC_FLOW` stable UUID를 생성+24시간 보존; 모두 same masked email·QUEUED·now+10m·now+60s·revision 1 | decoy는 account/challenge/secret/mail이 없고, handle kind는 public에 없다. resend는 signupId를 유지하고 current challenge version/revision만 바꾼다. |
| PENDING recovery/purge | 이전 REAL flow가 EXPIRED이고 `pending_purge_at` 전 같은 password일 때만 새 REAL flow. nickname/OTT set/purgeAt을 보존하며 concurrent unique winner만 mail을 만들고 같은 race loser만 winner result replay | 만료 전·wrong password·후속 요청은 DECOY다. |
| public idempotency | C1 actor ledger와 분리한 canonical record+`(scopeKind,scopeValue,operation,key)` unique alias ledger. signup은 current/previous identity HMAC alias를 모두 같은 record에 쓰고 verify/resend는 signupId alias이며 canonical secret body는 versioned keyed HMAC-SHA-256만 저장 | old/new deployment가 공유 alias로 같은 winner를 보고 가짜 actor·plain password digest offline oracle를 만들지 않으며 terminal replay를 flow cleanup과 분리한다. |
| recovery linearization | expired prior flow를 처음 본 request만 DB admittedAt attempt를 만들고 account lock 뒤 winner WON/new flow 또는 같은-prior lock loser LOST_REPLAY로 commit | winner commit 뒤 OPEN flow를 먼저 본 후속은 attempt 없이 DECOY라 race loser와 구분된다. |
| signup/login timing | current/dummy와 모든 supported stored PHC workload를 startup benchmark해 `commonFloor=max(p99)+25ms`, CSPRNG 0~75ms jitter | missing/stale/unsupported calibration은 readiness fail-closed이고 unknown/wrong은 401 `INVALID_CREDENTIALS` 동일 body다. |
| login failure | account key 5회/15분부터 30초 exponential delay, 최대 15분; hard lock 없음 | guessing을 늦추되 attacker가 영구 account DoS를 만들지 못하게 한다. |
| login IP | 50회/15분 뒤 15분 throttle; 성공 시 account failure counter reset | credential stuffing을 억제하고 NAT 사용자를 고려한다. |
| local adapter | loopback Mailpit SMTP 1025/UI 8025, auth/TLS/API key 없음, 외부 relay/forward 금지 | 실제 발송 없이 message·link·masking·retry를 관찰한다. |
| production adapter | provider·sender domain·credential·bounce/complaint 정책 승인 전 `DISABLED` | local capture 성공을 실제 전달 완료로 과장하지 않는다. |

rate limit key에는 raw email을 쓰지 않고 versioned HMAC identity와 coarse IP key를 사용한다. rotation 중 current/previous
logical-window counter를 한 Redis Function에서 합산 판정하고 허용 시 current만 증가시킨다. previous key는 최대 window와
pre-signup idempotency retention이 모두 지난 뒤 retire하며 세 번째 version을 겹치지 않는다. 최초 signup mail과 resend는
같은 `MAIL_IDENTITY=5/h+10/d`, `MAIL_IP=20/h+100/d` aggregate quota를 공유한다. trusted proxy CIDR의
immediate peer만 하나의 configured canonical forwarded chain을 사용할 수 있고 나머지는 socket peer IP만 쓴다.
IPv4 /24, IPv6 /56 projection이며 malformed/ambiguous chain과 missing HMAC key version은 fail-closed한다.
429는 `Retry-After`와 generic message를 반환하고 log에는 safe reason/count bucket만 남긴다.
모든 instance는 하나의 shared Redis authority를 사용하고 server-side UTC `TIME`으로 fixed-window bucket을
결정한다. counter 증분·limit 판정·TTL 설정은 Lua/Redis Function 한 번으로 atomic하게 수행하며 local-memory
counter fallback은 금지한다. Redis unavailable, clock/protocol mismatch, partial write는 signup/login/verify/resend를
503으로 fail-closed하고 identity·challenge·session mutation을 만들지 않는다.
서비스 전체 emergency counter도 같은 atomic authority를 사용하되 limit은 capacity test에서 승인한 versioned
정수여야 하며 non-test에서 값이 없으면 readiness를 실패한다. 이를 verification wrong-attempt authority로 쓰지 않는다.

structured rate policy는 다음과 같다.

| operation | identity/flow scope | coarse IP scope |
| --- | --- | --- |
| signup+resend shared mail | `5/h + 10/d` | `20/h + 100/d` |
| verify | PostgreSQL public-flow wrong attempt `5` (Redis에 복제하지 않음) | `30/h` |
| login | failure `5/15m`부터 30초 exponential delay, 최대 15분 | `50/15m` 뒤 15분 throttle |
| refresh | family `30/1m` | `120/15m` |

public exact mapping은 actual/decoy에 동일하다: malformed 400 `VALIDATION_ERROR`; wrong secret 400
`VERIFICATION_INVALID`; unknown/terminal/24h-expired flow 400 `SIGNUP_FLOW_INVALID_OR_EXPIRED`; attempt exhausted
429 `VERIFICATION_ATTEMPTS_EXHAUSTED`; cooldown/rate 429 `AUTH_FLOW_THROTTLED`; idempotency body conflict
409 `IDEMPOTENCY_KEY_REUSED`; concurrent state conflict 409 `AUTH_STATE_CONFLICT`;
dependency failure 503 `AUTH_DEPENDENCY_UNAVAILABLE`; correct current REAL challenge만 200; allowed resend는 202.

### 반대안 손실

- 가입 즉시 ACTIVE는 funnel이 짧지만 email 소유 증명과 password recovery 기반을 잃는다.
- 6자리 code는 모바일 입력이 쉽지만 256-bit link보다 rate limit 의존성이 크다.
- 운영 SMTP를 지금 고르면 실제 credential, sender domain, deliverability, bounce 처리가 없는 상태에서
  “이메일 인증 완료”를 허위로 주장하게 된다.

### 보안 영향

- verify 성공은 challenge `CONSUMED`와 membership `ACTIVE`를 한 transaction으로 바꾼다.
- resend는 기존 ACTIVE challenge를 `SUPERSEDED`하고 검증 가능한 challenge를 최대 하나로 유지한다.
- late resend expiry는 `min(PostgreSQL now+10m, public_flow_expires_at)`이고 actual/decoy attempt를 0으로 reset한다.
- 모든 public response timing/shape는 email 존재 여부를 과도하게 구분하지 않는다.
- signup 응답의 actual/decoy UUID는 화면 memory/navigation state에만 두며 analytics·URL·storage에 넣지
  않는다. raw email/token과 함께 timing metric label로 쓰지 않는다.
- actual/decoy public-flow row는 stable handle, keyed identity projection, kind, expiry/cooldown/attempt/revision,
  nullable current_challenge_id만 저장한다. decoy에는 userId·challenge·secret hash·mail outbox가 없고 kind는
  API·error·log에 없다. flow는 생성 24시간 뒤 삭제하며 PENDING account는 30일 recovery/purge 정책을 따른다.
- Mailpit UI/API는 loopback에만 bind하며 production profile에서 시작하지 않는다.

### UX 영향

- 가입 뒤 “10분 안에 메일 확인”과 60초 resend countdown을 표시한다.
- 만료·소진 시 account 생성부터 반복시키지 않고 rate limit 안에서 새 challenge를 발급한다.
- 실제 provider 전에는 local 개발에서만 동작하며 외부 사용자는 가입 완료 flow를 사용할 수 없다.

### Rollback

1. delivery 장애 시 signup을 `PENDING`으로 보존하고 인증을 우회해 ACTIVE로 승격하지 않는다.
2. adapter를 `DISABLED`로 전환하고 resend를 503 retryable로 닫는다.
3. TTL/rate 수치는 새 challenge부터 변경하고 기존 expiry를 연장하지 않는다.
4. provider 교체 시 challenge/domain event 계약은 유지하고 adapter만 교체한다.

### 운영 credential 경계

- local Mailpit에는 username/password/API key를 설정하지 않는다.
- production 후보 변수 이름만 예약한다: `MAIL_PROVIDER`, `MAIL_API_KEY`, `MAIL_FROM_ADDRESS`.
- 실제 production provider 이름 선택, API key, sender 주소·도메인, webhook signing secret은
  승인/secret store 전 없다.

**local 채택:** `VERIFY_REQUIRED_MAILPIT_LOCAL_PROD_DEFERRED`; production mail은 `BLOCKED`.

---

## DN-C4A-004 — 온보딩 입력 수·skip·재수행

**권장 기본값:** `ONBOARDING_MAX=10`, `SUBMITTED_MIN=1`, `SKIP_AT_ZERO=true`,
`K10=RECOMMENDED_NOT_REQUIRED`, `RERUN=BLOCKED_LOCAL_PROFILE`,
`K10_FULL_CATALOG_ALPHA=0.2`.

### evidence와 구체 계약 후보

| 경로 | 최소 조작 lower bound | MovieLens 예상 별점 evidence | 제품 의미 |
| --- | ---: | --- | --- |
| skip | 1 | K0 fallback | 0개 preference, DISLIKE 아님 |
| 1~9개 완료 | 선택 수 + 완료 1회 | REC-EV-011 K5 full-catalog blend `alpha=0.1`, paired NDCG delta CI `[0.000016, 0.002202]`로 0 경계에 매우 가까움 | preference 저장, 숫자 예상 별점 Gate 아님 |
| 10개 완료 | 11 | REC-EV-011 K10 full-catalog blend `alpha=0.2`; popularity NDCG `0.004723`, blend `0.006154`, delta CI `[0.000253, 0.002783]` | 권장 입력 목표 후보지만 강제 minimum·실제 UX 개선 근거 아님 |

- C0 `UI_READY` 실제 영화만 versioned selection policy로 최대 10개 제시한다.
- 사용자는 LIKE/DISLIKE를 1개 이상 고르면 어느 시점에도 `SUBMITTED` 완료할 수 있다.
- 0개는 명시적 `SKIPPED`; 미선택 movie를 DISLIKE로 만들지 않는다.
- save는 현재 active set 전체를 `replace`하고 append/merge하지 않는다.
- local profile은 재수행 operation을 노출하지 않는다. 별도 승인 시에만 새 journey revision과
  selection policy version, 이전 preference `SUPERSEDED` 모델을 후보로 재검토한다.
- K10 미만이면 예상 별점 숫자는 별도 `REC-PD-001` Gate에 따라 계속 숨긴다.

### 반대안 손실

- K10 강제는 모든 완료 사용자에게 첫 3% 별점 data Gate를 확보하지만 skip보다 최소 10회,
  K5보다 5회 더 조작시키고 실제 signup 이탈 근거가 없다.
- K5 강제는 부담이 작지만 REC-EV-011 개선 CI 하한이 0에 매우 가까우며 실제 가입 이탈·만족도
  evidence가 없다. K10 역시 작은 offline MovieLens ranking 개선일 뿐 강제 입력의 제품 효용을 입증하지 않는다.
- 재수행 append는 입력을 보존해 보이지만 상충 LIKE/DISLIKE와 현재 취향의 source of truth가 모호해진다.

### 보안 영향

- onboarding은 인증된 actor의 resource이고 body userId를 받지 않는다.
- maximum 10과 idempotent replace로 payload/row 증폭을 제한한다.
- 이전 revision은 audit용이며 public active preference response에 섞지 않는다.

### UX 영향

- 첫 화면부터 “나중에”를 제공하고 `선택 수 / 10`은 목표이지 필수 진행도로 표현하지 않는다.
- 1개 이후 완료 CTA를 활성화하고 K10까지 선택할 수 있음을 안내한다.
- 재수행 CTA는 local 화면에 없다. 별도 승인 뒤에만 기존 선택 대체 확인 UX를 검토한다.

### Rollback

1. maximum/selection policy 변경은 새 journey version부터 적용하고 과거 완료를 무효화하지 않는다.
2. 향후 rerun extension 장애 시 기능 flag를 끄고 기존 active revision을 유지한다.
3. optional completion이 품질을 해치면 강제 K로 즉시 바꾸지 않고 K cohort별 실제 완료율/추천 지표를
   먼저 수집해 새 제품 결정을 연다.

### 운영 credential 경계

- 온보딩 자체에는 외부 credential이 없다.
- TMDB/Catalog은 기존 C0 projection만 읽으며 frontend가 TMDB key를 받지 않는다.
- selection/recommendation artifact는 versioned internal evidence이고 사용자 secret이 아니다.

**local 채택:** `OPTIONAL_UP_TO_10_WITH_SKIP`; restart는 local MVP에서 `BLOCKED`.

---

## DN-C4A-005 — Google·Kakao·Naver OAuth와 계정 연결

**권장 기본값:** `SOCIAL_GOOGLE=DISABLED`, `SOCIAL_KAKAO=DISABLED`,
`SOCIAL_NAVER=DISABLED`, `AUTO_MERGE_BY_EMAIL=FORBIDDEN`,
`LINKING=AUTHENTICATED_EXPLICIT`, `RECENT_REAUTH_MAX_AGE=10m`, `OAUTH_STATE_TTL=10m`,
`PKCE=S256`, `SOCIAL_IDENTITY_KEY=PROVIDER_ISSUER_SUBJECT_HMAC`,
`LINK_TX_TTL=10m`, `MIX_UP_DEFENSE=SERVER_ISSUER_BINDING`.

### activation Gate와 연결 규칙

세 provider는 아래 조건을 모두 충족할 때만 provider별로 `AVAILABLE` 승격 후보가 된다.

1. provider app·client ID/secret이 운영 secret store에 주입된다.
2. 환경별 redirect URI가 exact allowlist로 승인되고 wildcard/prefix match가 없다.
3. authorization code + PKCE S256, state, nonce, issuer/audience 검증 contract와 test가 있다.
4. provider `(provider, issuer, subjectHmac)` unique identity와 revoke/unlink audit가 있다. raw subject는
   저장하지 않고 server keyed HMAC projection만 사용한다.
5. public start/callback UI/API, privacy notice, failure recovery가 별도 승인된다.

v1 권장 범위는 email/password로 로그인한 ACTIVE 사용자가 최근 10분 안에 password 재인증한 뒤
설정 화면에서 명시적으로 provider를 link하는 것뿐이다. provider email claim이 verified여도 기존
service account와 **자동 병합하지 않는다**. 동일 email은 후보 안내조차 public callback에서 노출하지
않고 이미 인증된 actor의 명시적 linking transaction에서만 처리한다.

| 항목 | 권장값 | 이유 |
| --- | --- | --- |
| social signup | disabled | email/password/nickname v1 범위를 유지한다. |
| social login | linked identity가 생기기 전 disabled | email claim만으로 identity를 추측하지 않는다. |
| explicit link | recent password reauth ≤10분 + OAuth 성공 | account session 탈취만으로 새 provider를 붙이기 어렵게 한다. |
| transaction | authenticated user/session/provider/expected issuer/exact redirect URI에 결합, TTL 10분, single use | callback parameter가 actor/provider/token endpoint를 다시 선택하지 못하게 한다. |
| state/nonce | 각 256-bit random, hash/session binding, TTL 10분, single use | login CSRF와 ID token replay를 제한한다. |
| PKCE | S256 challenge; verifier는 transaction 동안만 암호화 저장 후 성공/실패 때 폐기 | authorization code interception 방어를 보강하며 verifier raw log를 금지한다. |
| redirect | exact HTTPS URI allowlist, local은 별도 exact loopback | open redirect/prefix confusion을 막는다. |
| mix-up | start 시 server가 고정한 provider·issuer·authorization/token endpoint만 사용하고 callback issuer가 다르면 전체 rollback | 공격자가 callback parameter로 다른 issuer/token endpoint를 선택하는 mix-up을 막는다. |
| provider key | `(provider, issuer, subjectHmac)` global unique; `subjectHmac=HMAC(subject)`이고 raw subject 저장·로그 금지 | email 변경·provider별 claim과 service identity를 분리하고 유출 시 raw provider ID를 줄인다. |
| email claim | display/notification 후보만, merge key 금지 | provider email 검증 의미 차이로 인한 account takeover를 막는다. |

### 반대안 손실

- email claim 자동 병합은 가입 friction이 작지만 잘못된 issuer/claim 신뢰나 provider account 탈취가
  기존 FEELM 계정 탈취로 번질 수 있다.
- social signup까지 동시에 열면 recovery·nickname collision·약관·unlink 후 login 수단을 함께
  결정해야 해 v1 범위가 커진다.
- 계속 disabled이면 요구된 세 provider의 편의성을 당장 제공하지 못한다.

### 보안 영향

- OAuth authorization code, access/refresh token, provider response body, client secret은 저장 최소화하고
  log/event/fixture에 넣지 않는다.
- callback error는 provider account/email 존재를 노출하지 않는 generic code를 쓴다.
- linking은 현재 service session, recent reauth, OAuth transaction을 한 actor에게 결합하고 conflict 시
  어느 기존 account에 연결됐는지 숨긴다.
- unique identity 충돌은 identity row·audit·session을 부분 생성하지 않고 transaction 전체 rollback한다.
  OAuth `state`/`nonce`/code/PKCE verifier는 어떤 실패에서도 재사용할 수 없다.

### UX 영향

- local profile에서는 로그인 화면에 Google/Kakao/Naver 버튼이나 disabled teaser를 노출하지 않는다.
- 승인 뒤에도 최초 연결은 설정 화면의 명시적 action이며 자동 popup/redirect가 아니다.
- 연결 충돌은 “이 로그인 수단을 연결할 수 없음”으로 복구하고 기존 email login을 유지한다.

### Rollback

1. provider별 capability를 즉시 `DISABLED`로 바꾸고 새 start/callback을 404로 닫는다.
2. 해당 provider로 발급된 service session family를 revoke하되 email credential은 삭제하지 않는다.
3. identity row는 audit 보존하고 자동 unlink/delete하지 않는다.
4. provider 장애는 다른 provider나 email login을 막지 않는다.

### 운영 credential 경계

- 예약 이름: `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET`,
  `OAUTH_GOOGLE_REDIRECT_URI`; Kakao/Naver도 동일 prefix 구조를 쓴다.
- 실제 client ID/secret, redirect URI, provider tenant/app ID는 문서·fixture·`.env.example` 값으로 넣지 않는다.
- credential이 존재한다는 사실만으로 capability를 켜지 않으며 activation Gate 승인 record가 필요하다.

**local 채택:** `KEEP_ALL_SOCIAL_DISABLED`; OAuth 활성화는 별도 제품·보안 승인.

---

## 6. 운영 credential matrix

| Capability | local | production | Git/document 허용 |
| --- | --- | --- | --- |
| JWT signing | test-only ephemeral/fake decoder | secret store private key + key ID | 변수 이름·검증 절차만 |
| refresh/challenge | CSPRNG raw는 response/cookie/mail로만 전달, hash 저장 | 동일 | raw 값 금지 |
| Mail | Mailpit no auth, loopback only | provider deferred/disabled | host/port와 변수 이름만 |
| OAuth | provider capability disabled | provider별 승인 뒤 secret store | provider 이름과 변수 이름만 |
| redirect | local exact URI도 구현 승인 뒤 | exact HTTPS allowlist | 실제 URI 값 금지 |

운영 secret이 없을 때 fail-fast하는 것은 adapter startup 또는 capability activation이며, Catalog/C1 Rating
같은 무관 기능은 계속 동작해야 한다.

## 7. Credential 없는 Mailpit local 검증 절차 — 계획만

현재 backend mail adapter와 Compose service는 없으므로 아래 절차는 **실행 완료 증거가 아니다**.
local adapter 선행 task가 구현된 뒤 별도 local profile에서만 수행한다.

1. 보안 수정이 포함된 검토된 pin을 사용한다. 현재 검토 후보는 `axllent/mailpit:v1.30.4`이며 실제
   도입 시 image digest까지 고정한다. `latest`는 사용하지 않는다.
2. Compose를 바꾸지 않고 임시 container를 loopback에만 노출한다.

   ```powershell
   docker run --rm --name feelm-mailpit-local `
     -p 127.0.0.1:1025:1025 -p 127.0.0.1:8025:8025 `
     axllent/mailpit:v1.30.4
   ```

3. local mail profile은 `host=127.0.0.1`, `port=1025`, `auth=none`, `tls=none`만 사용한다. 실제 email
   주소·API key·password를 넣지 않고 `.test` recipient를 사용한다.
4. signup fixture를 한 번 실행하고 Mailpit UI/API에서 다음만 확인한다.
   - message 1건, masked/test recipient, expiry copy 10분
   - link의 raw token은 HTTPS fragment에만 있고 query·DB·log·outbox에는 없으며, E2E browser는
     fragment를 memory로 읽고 `history.replaceState`로 제거한 뒤 어떤 network/third-party resource보다
     먼저 POST body로 제출
   - resend 전 60초는 429, 허용 뒤 old challenge `SUPERSEDED`, new message 1건
   - expired/wrong/consumed replay는 membership을 ACTIVE로 바꾸지 않음
5. Mailpit SMTP 중단과 chaos/error 후보로 delivery failure를 만들고 signup transaction rollback 또는
   `PENDING + DELIVERY_FAILED_RETRYABLE` 계약을 확인한다. 실제 forwarding/release는 금지한다.
6. 종료는 다른 terminal에서 `docker stop feelm-mailpit-local`로 수행하며 DB fixture를 삭제하지 않는다.

Mailpit 기본 SMTP/UI port와 no-auth local SMTP 동작은 공식 문서 기준이지만, host 외부 공개는 금지한다.

## 8. production/extension 재검토 응답 형식

production activation 또는 blocked extension은 아래 형식으로 별도 승인·변경·보류한다.

```text
DN-C4A-00x: <권장 선택 token 또는 DEFER>
수치 변경: <없음 또는 field=value>
허용 손실: <보안/UX trade-off>
rollback trigger: <측정 가능한 조건>
재검토 조건: <evidence 또는 날짜>
```

local profile은 `승인 현황: 5/5`다. main OpenAPI에는 local 13개 operation만 병합했다. production
activation, 실제 mail/OAuth, restart와 password/account lifecycle은 계속 `BLOCKED`다.

## 9. 비교 근거

- [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html): single-factor password 길이,
  composition rule 금지, blocklist·rate limit 근거
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html):
  password·authentication response·reauthentication 경계
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html):
  Argon2id와 work-factor benchmark 기준
- [OWASP Forgot Password Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html):
  존재 여부 비노출·유사 응답시간·일회성 만료 token·rate limit 경계
- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html):
  server-side expiry/revoke, Secure·HttpOnly·SameSite, browser storage 경계
- [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html):
  SameSite를 단독 방어로 보지 않는 이유와 token/Origin 검증
- [RFC 9700 OAuth 2.0 Security Best Current Practice](https://datatracker.ietf.org/doc/html/rfc9700):
  exact redirect URI matching, PKCE S256, refresh rotation/replay detection 기준
- [MDN secure cookie configuration](https://developer.mozilla.org/en-US/docs/Web/Security/Practical_implementation_guides/Cookies):
  Secure·HttpOnly·SameSite·host prefix 경계
- [Mailpit configuration](https://mailpit.axllent.org/docs/configuration/): local SMTP 1025/UI 8025 기준
- [REC-PD-003 제품 결정 패킷](../recommendation/product-decision-packet.md): K5/K10/skip 근거와 C4A 연결
- [REC-EV-011 full-catalog blend evidence](../recommendation/evidence/REC-EV-011-cold-foldin-full-catalog.md):
  K10 `alpha=0.2`와 작은 offline ranking delta의 현재 근거

외부 보안 지침은 권장값의 비교 근거일 뿐 FEELM 제품 승인을 대신하지 않는다.
