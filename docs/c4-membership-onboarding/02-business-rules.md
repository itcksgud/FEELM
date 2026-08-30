# C4A 업무 규칙

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`  
> 표의 `PROPOSED`는 원래 DN provenance다. 보수 token은 local profile에서 채택됐으며,
> `BR-C4A-046` restart와 social/OAuth activation, production mail·password/account lifecycle만 차단한다.

## 1. 접근·actor

| ID | 상태 | 규칙 |
| --- | --- | --- |
| `BR-C4A-001` | 확정 경계 | public operation은 signup·verify·resend·email login뿐이다. protected operation actor는 검증된 인증 subject에서만 결정한다. |
| `BR-C4A-002` | `PROPOSED: DN-C4A-001` | protected resource/C1 호출은 access Bearer subject를 사용한다. login은 cookie 발급 전 exact Origin을 검증한다. refresh와 complete-cookie-pair logout은 refresh cookie session actor+exact Origin+signed double-submit CSRF를 검증한다. cookie-less logout도 exact Origin은 필수이고 CSRF만 생략한다. absent/null/mismatch Origin은 mutation 전 403이다. |
| `BR-C4A-003` | 확정 경계 | request body/path의 userId·email을 protected actor identity로 신뢰하지 않는다. 본인 resource는 `/me`를 사용한다. |
| `BR-C4A-004` | 확정 경계 | PENDING_EMAIL_VERIFICATION membership은 verification/resend 외 protected API를 호출할 수 없다. |
| `BR-C4A-005` | 확정 경계 | 다른 사용자의 membership/onboarding/subscription UUID는 존재 여부를 숨기는 404다. |

## 2. 비밀·이메일

| ID | 상태 | 규칙 |
| --- | --- | --- |
| `BR-C4A-010` | 확정 안전 규칙 | password raw 저장·로그를 금지하고 검증된 password hash와 algorithm/version metadata만 저장한다. Argon2id 승인 minimum은 hard security floor이고 p95 500ms는 capacity target이다. 둘을 충족하지 못하면 floor를 낮추지 않고 non-test auth readiness/startup을 fail-closed한다. |
| `BR-C4A-011` | 확정 안전 규칙 | verification secret은 challenge에 SHA-256 hash만 저장한다. post-commit mail 전달을 위한 별도 single-use material만 AES-256-GCM ciphertext/nonce/key version으로 challenge TTL까지 보관하고 raw secret·ciphertext는 safe outbox/log/trace에 두지 않는다. refresh token·CSRF nonce는 hash/서명 검증 정보만 저장한다. |
| `BR-C4A-012` | 확정 안전 규칙 | local mail adapter는 credential 없는 Mailpit를 사용한다. production adapter는 credential·sender·provider가 승인·주입되지 않으면 시작에 실패한다. |
| `BR-C4A-013` | `PROPOSED: DN-C4A-003` | signup은 `EMAIL_SIGNUP_PUBLIC_FLOW`의 stable `signupId`를 만든다. 신규 account만 즉시 REAL이며, PENDING recovery는 이전 REAL flow가 24시간 논리 TTL을 지나 EXPIRED가 되고 `pending_purge_at` 전에 같은 password를 증명했을 때만 새 REAL flow를 만든다. 만료 전 re-signup, duplicate ACTIVE, PENDING password 불일치, unknown recovery는 DECOY flow이며 account·challenge secret·mail을 만들지 않는다. Recovery request의 nickname은 문법만 검증하고 기존 profile nickname을 보존하며 unique claim/update에 사용하지 않는다. |
| `BR-C4A-014` | `PROPOSED: DN-C4A-003` | challenge wrong-attempt 5회와 EXHAUSTED는 PostgreSQL public-flow row lock/transaction의 `failed_attempt_count`가 단일 authority다. Redis는 identity/coarse-IP/global emergency abuse admission만 server UTC clock+atomic counter/TTL로 수행하고 local fallback 없이 장애 시 domain mutation 전 503이다. global limit은 승인된 capacity-derived versioned 정수여야 하고 non-test에서 누락되면 readiness를 실패한다. raw secret은 pinned HTTPS frontend origin fragment로만 전달하고 network/Referer/log 전에 제거한 뒤 verify POST body에만 넣는다. |
| `BR-C4A-015` | 확정 안전 규칙 | resend는 stable signupId를 유지한 채 REAL flow의 이전 ACTIVE challenge를 SUPERSEDED하고 새 version challenge를 만든 뒤 `current_challenge_id`를 바꾼다. 새 actual challenge와 DECOY mirror의 expiry는 `min(PostgreSQL now+10m, public_flow_expires_at)`이고, 두 kind 모두 `failed_attempt_count=0`·status OPEN으로 reset하며 cooldown/revision을 증가시킨다. account당 OPEN REAL flow와 ACTIVE challenge는 각각 최대 1개다. |
| `BR-C4A-016` | `PROPOSED: DN-C4A-003` | actual/decoy에 동일한 public mapping을 적용한다. malformed body는 400 `VALIDATION_ERROR`; wrong secret은 400 `VERIFICATION_INVALID`; unknown/terminal/24h-expired flow는 400 `SIGNUP_FLOW_INVALID_OR_EXPIRED`; 5회 소진은 429 `VERIFICATION_ATTEMPTS_EXHAUSTED`; cooldown/분산 limit은 429 `AUTH_FLOW_THROTTLED`; idempotency body conflict는 409 `IDEMPOTENCY_KEY_REUSED`, concurrent state conflict는 409 `AUTH_STATE_CONFLICT`; Redis/DB/mail dispatch authority 장애는 mutation 전 503 `AUTH_DEPENDENCY_UNAVAILABLE`다. correct current REAL challenge만 200이고 resend 허용 actual/decoy는 모두 202다. OpenAPI operation별 ErrorResponse.code enum/const가 이 mapping을 기계적으로 제한한다. |
| `BR-C4A-017` | `PROPOSED: DN-C4A-003` | public flow TTL은 생성 시각부터 24시간이며 resend로 연장하지 않는다. PENDING recovery는 기존 REAL flow의 논리 expiry 후에만 가능하다. idempotency miss가 recovery 후보를 관측하면 PostgreSQL transaction 시작 시 `PENDING_SIGNUP_RECOVERY_ATTEMPT(admitted_at, prior_expired_signup_id)`를 만들고 account row를 잠근 뒤 eligibility를 재검증한다. winner는 새 REAL flow에 `recovery_attempt_id`와 `recovery_from_signup_id`를 기록하고 attempt를 WON으로 선형화한다. lock을 기다린 attempt는 자신의 admittedAt 뒤 같은 prior flow에서 생성된 winner를 볼 때만 LOST_REPLAY로 연결해 winner의 safe 202를 저장·재생한다. 요청 시작 시 이미 새 OPEN REAL flow가 보인 후속 요청은 attempt를 만들지 않고 DECOY다. PENDING account·credential·profile은 `pending_purge_at=created_at+30d`에 verification과 단일 winner로 purge하고 re-signup은 purge 시각을 연장하지 않는다. |
| `BR-C4A-018` | 확정 안전 규칙 | signupId는 public flow 수명 동안 stable하다. CHECK는 DECOY의 user/current pointer가 모두 null, REAL owner는 항상 non-null, OPEN REAL current pointer는 non-null임을 강제한다. user당 OPEN REAL flow와 ACTIVE challenge partial unique, challenge `(signup_id,challenge_id,user_id)` UNIQUE, flow `(signup_id,current_challenge_id,user_id)` deferred composite FK를 둔다. COMMIT 시 deferred constraint trigger가 OPEN REAL pointer의 challenge status=ACTIVE, 같은 flow/user, terminal flow와 challenge transition 일관성을 검증한다. DECOY challenge row는 0개다. |
| `BR-C4A-019` | `PROPOSED: DN-C4A-001` | login/refresh는 raw token을 idempotency response에 저장하지 않는다. refresh는 session+token row lock 아래 한 winner만 새 refresh/CSRF generation을 만들고 loser는 token 재발급 없이 409다. |

## 3. 멱등성·transaction·race

| ID | 상태 | 규칙 |
| --- | --- | --- |
| `BR-C4A-020` | 확정 안전 규칙 | login·refresh token issuance를 제외한 domain resource mutation은 Idempotency-Key가 필수다. ACTIVE actor mutation/logout은 C1 `IDEMPOTENCY_RECORD`를 사용하지만 anonymous signup/verify/resend는 actor ledger에 가짜 user를 넣지 않고 별도 `PUBLIC_AUTH_IDEMPOTENCY_RECORD`를 쓴다. 처리 순서는 canonical validation/scope 산출 → PostgreSQL lookup/replay-or-409 → miss에만 Redis abuse admission → domain row lock/transition+idempotency result 같은 transaction이다. replay는 Redis/attempt/cooldown을 소비하지 않는다. raw token은 result에 저장하지 않는다. |
| `BR-C4A-021` | 확정 안전 규칙 | public idempotency scope는 signup의 current/previous `IDENTITY_HMAC` projection인 `PRE_SIGNUP_IDENTITY`와 verify/resend의 stable UUID `SIGNUP_FLOW`뿐이다. canonical `PUBLIC_AUTH_IDEMPOTENCY_RECORD(record_id PK)`, unique `PUBLIC_AUTH_IDEMPOTENCY_SCOPE_ALIAS`, `PUBLIC_AUTH_IDEMPOTENCY_REQUEST_HMAC_ALIAS(record_id,request_hmac_key_version UNIQUE)`를 분리한다. current/previous derived scope lock을 정렬 획득하고 모든 alias를 lookup한 뒤 miss이면 모든 accepted scope alias와 canonical body의 current/previous **keyed HMAC-SHA-256** alias를 같은 record에 원자 insert한다. old/new deployment는 공유 scope와 request-key version으로 같은 winner와 body equality를 검증한다. raw/plain SHA-256 secret digest는 offline password oracle이므로 금지한다. live alias/record가 참조하는 scope/digest key는 expiry 전 retire하지 않으며 drain 전 세 번째 active version을 도입하지 않는다. |
| `BR-C4A-022` | 확정 안전 규칙 | public idempotency miss는 record, REAL/DECOY flow 또는 verify/resend transition, recovery attempt, safe response, audit/outbox를 하나의 PostgreSQL transaction에 commit/rollback한다. signup record의 `resource_signup_id`가 최초 stable handle이고 retry는 flow 삭제와 독립된 terminal response를 재생한다. signup/resend/wrong-verify record는 flow expiry까지, verify success는 terminal+24h 보존한다. cleanup은 expired record를 먼저 또는 flow와 함께 지우되 terminal+24h record를 flow FK cascade로 조기 삭제하지 않는다. mail/OAuth 외부 호출은 DB transaction 안에서 하지 않는다. |
| `BR-C4A-023` | 확정 안전 규칙 | unique email/nickname, active challenge, preference `(journey,movie)`, subscription `(user,provider)` race는 DB constraint/lock으로 단일 winner를 만든다. |
| `BR-C4A-024` | 확정 안전 규칙 | expectedRevision 불일치는 409 `REVISION_CONFLICT`이며 최신 상태를 다시 읽기 전 변경하지 않는다. |
| `BR-C4A-025` | 확정 안전 규칙 | DB failure는 partial account/preference/subscription/session을 남기지 않고 503이다. external delivery failure는 committed identity와 분리된 명시 상태로 재시도한다. |
| `BR-C4A-026` | 확정 안전 규칙 | signup/resend transaction은 challenge hash, AES-256-GCM delivery material, materialId만 가진 safe outbox를 원자 commit한다. worker는 challenge owner join으로 수신자를 조회하고 versioned secret-store key와 lease로 post-commit 메모리 복호화한다. DB/outbox/cache/log/trace/test artifact에는 raw recipient/secret/link가 0건이어야 한다. 실제 전달을 위해 worker process memory와 TLS provider request wire에는 recipient와 동일 single-use link가 최소 시간 존재할 수 있으나 provider adapter는 body/header/error를 log·metric·trace에 남기지 않는다. live material이 참조하는 key version은 retire할 수 없고 missing/unknown version은 readiness/dispatch를 fail-closed한다. |
| `BR-C4A-027` | 확정 안전 규칙 | provider 수락 전 worker crash는 같은 material/challenge를 retry하고, 수락 후 ciphertext 삭제 전 crash는 같은 single-use link의 at-least-once 중복만 허용한다. 새 secret/challenge를 발급하지 않으며 수락 또는 challenge terminal/superseded/expired에 ciphertext/nonce를 삭제한다. |
| `BR-C4A-028` | 확정 안전 규칙 | idempotency retention은 signup/resend/wrong verify가 flow expiry까지, verify success와 active logout success가 terminal+24h다. replay는 Redis/attempt/cooldown/mail/audit를 소비하지 않고 cookie-less logout과 Redis 429/503은 record를 만들지 않는다. |
| `BR-C4A-029` | 확정 안전 규칙 | network/identity projection은 trusted proxy CIDR의 immediate peer만 하나의 configured canonical forwarded chain을 허용하고 그 외에는 socket peer IP만 사용한다. malformed/ambiguous chain을 거부하며 IPv4 /24·IPv6 /56와 normalized identity의 HMAC key version을 쓴다. rotation 동안 Redis Function은 current/previous projection의 같은 logical window counter를 한 번에 읽어 합산 판정하고 허용 시 current counter만 increment+TTL한다. 두 version key를 따로 허용량으로 취급하지 않는다. previous key는 최대 quota window와 pre-signup idempotency retention이 모두 지난 뒤 retire하며 그 전 세 번째 version을 활성화하지 않는다. signup 최초 delivery와 resend는 같은 `MAIL_IDENTITY` 5/h+10/d 및 `MAIL_IP` 20/h+100/d aggregate quota를 공유하고 REAL/DECOY 모두 miss에서 소비한다. missing key/protocol은 readiness/요청 503이다. |

## 4. Membership·nickname·auth

| ID | 상태 | 규칙 |
| --- | --- | --- |
| `BR-C4A-030` | `PROPOSED: DN-C4A-002/003` | signup request 필드는 email/password/nickname만 사용하고 email/nickname 승인 normalization으로 unique를 판정한다. legal name은 proposed 범위에 없다. |
| `BR-C4A-031` | 확정 안전 규칙 | signup response는 password/hash, raw challenge, internal userId를 포함하지 않으며 email은 masked다. account 존재 여부와 무관하게 signupId·now+10m expiry·now+60s resend·QUEUED를 같은 shape으로 반환한다. |
| `BR-C4A-032` | `PROPOSED: DN-C4A-003` | ACTIVE email credential과 password hash 검증 성공만 login할 수 있다. unknown email은 현재 production Argon2id와 동일 parameter의 dummy PHC를 verify하고 wrong은 stored PHC를 verify한다. Non-test startup은 current hash/dummy verify와 모든 supported stored PHC verify workload를 benchmark하여 `commonFloor=max(p99)+25ms`를 계산하고 calibration이 누락·stale·unsupported이면 readiness를 fail-closed한다. Signup/invalid login은 같은 calibrated floor+0~75ms CSPRNG jitter를 사용하고 unknown/wrong은 401 `INVALID_CREDENTIALS` 같은 body다. Security floor를 timing 목표 때문에 낮추지 않는다. |
| `BR-C4A-033` | `PROPOSED: DN-C4A-001` | successful refresh는 prior ACTIVE→ROTATED와 replacement ACTIVE를 원자 반영한다. `rotated_at`과 race 판정 now는 row lock을 보유한 PostgreSQL primary의 `clock_timestamp()`만 사용하며 app/Redis/client clock을 쓰지 않는다. 바로 이전 token의 DB-clock 5초 이내 reuse는 benign race 409, 이후/더 오래된 generation reuse는 family revoke이며 raw replacement를 replay하지 않는다. family terminal 시각 뒤 30일까지만 lineage를 보존하고 같은 cleanup에서 token hash와 session row를 삭제한다. |
| `BR-C4A-034` | `PROPOSED: DN-C4A-001` | 모든 logout은 cookie 유무와 무관하게 exact allowed Origin을 먼저 검증한다. active refresh session mutation은 valid refresh+signed CSRF로 current family를 한 번 revoke하고 Idempotency-Key를 요구한다. 두 auth cookie가 모두 없는 retry만 CSRF/idempotency 없이 204 exact clear를 반환하고 DB/audit를 변경하지 않는다. cookie가 하나라도 있으면 signed CSRF를 생략할 수 없다. clear는 선택 profile의 발급 attributes+`Max-Age=0`+epoch Expires다. |
| `BR-C4A-035` | `PROPOSED: DN-C4A-002` | nickname 변경은 승인 normalization/unique/cooldown과 expectedRevision을 적용하고 실패 시 기존 profile을 유지한다. |
| `BR-C4A-036` | `PROPOSED: DN-C4A-001` | access JWT는 RS256만, required header `alg/kid/typ`, exact versioned iss/aud, required claims `sub/sid/jti/iat/nbf/exp`, UUID identifiers, 30초 leeway, 10분 max lifetime을 강제하고 PII를 넣지 않는다. unknown/missing kid·bad alg/claim/time은 401이다. |
| `BR-C4A-037` | `PROPOSED: DN-C4A-001` | signing key registry는 current+previous key를 최대 48시간 overlap하며 missing/stale/ambiguous registry는 non-test issuer/resource-server readiness를 fail-closed한다. production private key는 secret store에만 있고 test fake decoder는 explicit test profile에만 있다. |
| `BR-C4A-038` | `PROPOSED: DN-C4A-001` | refresh abuse limit은 family 30/1m, coarse IP 120/15m이고 Redis unavailable이면 session/token mutation 전 503이다. verify wrong-attempt 5회는 PostgreSQL만, Redis verify는 coarse IP 30/h만 센다. |
| `BR-C4A-039` | `PROPOSED: DN-C4A-001` | login/refresh/logout의 issue/rotate/clear는 선택 profile의 두 개 별도 `Set-Cookie`이며 comma folding을 금지한다. logout은 partial pair 403, valid CSRF+unknown/expired/revoked session 401, same-key stored success만 204 replay한다. |

## 5. Onboarding

| ID | 상태 | 규칙 |
| --- | --- | --- |
| `BR-C4A-040` | 확정 | OnboardingPreference는 `LIKE|DISLIKE`만 저장하고 C1 Rating/Frame/Popcorn/Rating aggregate를 만들지 않는다. |
| `BR-C4A-041` | 확정 | 미선택 영화와 skip은 preference row가 없으며 DISLIKE·0점·관심없음으로 해석하지 않는다. |
| `BR-C4A-042` | `PROPOSED: DN-C4A-004` | onboarding movie 목록은 C0 active `UI_READY` movie만 versioned selection policy로 반환한다. local selection policy는 versioned 보수 baseline을 쓰며 K/장르·인기·다양성의 개인화·production 주장은 하지 않는다. |
| `BR-C4A-043` | 확정 안전 규칙 | preference PUT은 현재 journey의 전체 set replace이며 JSON-level `movieId` uniqueness·`maxItems=10`과 DB unique/count maximum을 모두 강제하고 catalogVersion·selectionPolicyVersion·revision을 snapshot한다. maximum 초과는 기존 set을 바꾸지 않는 400이다. |
| `BR-C4A-044` | `PROPOSED: DN-C4A-004` | `SUBMITTED` complete는 JSON expectedPreferenceCount와 locked DB active count가 같고 승인 minimum 이상·maximum 10 이하를 요구한다. `SKIPPED`/`NOT_STARTED` response count는 모두 0, `COMPLETED`는 1..10이며 모든 response에서 `preferenceCount=likeCount+dislikeCount=locked active count`다. `SKIPPED`는 JSON if/then과 locked DB count 모두 0일 때만 허용한다. |
| `BR-C4A-045` | 확정 | complete response는 onboarding state만 확정하며 추천 candidate/fold-in/예상 별점 계산 완료를 동기 성공으로 주장하지 않는다. |
| `BR-C4A-046` | `PROPOSED: DN-C4A-004` | rerun은 승인될 경우 새 journey revision/version으로 이전 active preferences를 supersede한다. append/merge를 암묵적으로 하지 않는다. |
| `BR-C4A-047` | 확정 | recommender 장애는 preference 저장·skip·OTT set 저장을 막지 않는다. 후속 projection은 outbox status로 분리한다. |
| `BR-C4A-048` | 근거 경계 | REC-EV-011 full-catalog에서 K10 alpha 0.2의 paired NDCG CI `[0.000253,0.002783]`가 0 위인 작은 offline 후보가 됐다. champion·expected-star·public UI 승인이 아니며 HIGH confidence로 표시하지 않는다. |

## 6. OTT subscription set

| ID | 상태 | 규칙 |
| --- | --- | --- |
| `BR-C4A-050` | 확정 | provider source는 C0 `/api/v1/ott-providers`의 active KR provider이며 C4A가 provider를 복제하지 않는다. |
| `BR-C4A-051` | 확정 | PUT은 내 현재 provider set 전체 교체다. 빈 배열은 `CONFIGURED`+명시적 0개이고 skip은 `SKIPPED`, 무응답은 `NOT_CONFIGURED`다. |
| `BR-C4A-052` | 확정 | 같은 providerId 중복·KR에서 선택 불가 provider는 400/404이며 기존 set을 부분 변경하지 않는다. |
| `BR-C4A-053` | 확정 | subscription은 C0 OTT offer의 표시·정렬에 사용할 수 있지만 미구독 provider 영화도 추천 candidate에서 제외하지 않는다. |
| `BR-C4A-054` | 확정 안전 규칙 | USER_ACCOUNT signup transaction은 NOT_CONFIGURED OTT_SUBSCRIPTION_SET을 정확히 하나 만들고 rollback도 함께 한다. SKIPPED request/response는 JSON if/then으로 providerIds 0개를 강제하며 CONFIGURED empty와 구분한다. |

## 7. Social capability

| ID | 상태 | 규칙 |
| --- | --- | --- |
| `BR-C4A-060` | 확정 범위 | SocialProvider enum은 GOOGLE/KAKAO/NAVER다. |
| `BR-C4A-061` | `BLOCKED: DN-C4A-005` | provider별 credential/exact redirect와 explicit authenticated linking 승인 전 capability는 DISABLED다. 향후 transaction은 user/session/provider/expected issuer에 결합하고 state·nonce·PKCE S256을 검증한다. |
| `BR-C4A-062` | `BLOCKED: DN-C4A-005` | DISABLED provider의 public start/callback/exchange path와 로그인 버튼을 노출하지 않는다. |
| `BR-C4A-063` | 확정 안전 규칙 | provider access/refresh token, authorization code, raw userinfo body/subject/email claim은 DB·log·fixture에 저장하지 않는다. `(provider,issuer,subjectHmac)`만 identity key이고 callback이 issuer/token endpoint를 선택하지 못한다. |

## 8. 오류·관측성

| ID | 상태 | 규칙 |
| --- | --- | --- |
| `BR-C4A-070` | 확정 | field validation은 400, auth 없음/무효는 401, 권한 부족은 403, 숨긴 소유권은 404, stale/idempotency/state conflict는 409, throttle은 429, DB unavailable은 503이다. Public auth의 각 status는 OpenAPI operation별 `ErrorResponse.code` enum/const schema를 사용한다. generic 자유 문자열 code로 exact actual/decoy/logout mapping을 약화하지 않는다. |
| `BR-C4A-071` | 확정 | 오류는 traceId를 가지며 email·nickname 전체값, password, token, verification input, provider body를 message에 포함하지 않는다. |
| `BR-C4A-072` | 확정 | empty onboarding movies/provider set은 200과 빈 collection/state를 반환하고 missing을 dislike나 subscription 없음 확정으로 바꾸지 않는다. |
| `BR-C4A-073` | 확정 | safe audit는 pseudonymous actor/signup scope, event type, resource pseudonym, occurredAt, traceId만 기록하고 credential input을 포함하지 않는다. |
