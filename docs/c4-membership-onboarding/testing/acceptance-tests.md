# C4A Acceptance Test

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`  
> Fixture: `testing/fixtures.md`  
> LOCAL IMPLEMENTATION AC: `AC-C4A-001..033`, `AC-C4A-035..044`, `AC-C4A-046..066`, `AC-C4A-068..085`  
> PRODUCTION NEGATIVE BRANCH AC: `AC-C4A-007`, `AC-C4A-061`, `AC-C4A-073`, `AC-C4A-081`, `AC-C4A-082`  
> BLOCKED EXTENSION AC: `AC-C4A-034`, `AC-C4A-045`, `AC-C4A-067`  
> 기존 `PROPOSED` 표기는 원래 decision provenance다. 위 authority manifest가 local 구현 상태의 권위다.

## 1. Signup·email verification

| ID | 상태 | Given / When / Then |
| --- | --- | --- |
| `AC-C4A-001` | `PROPOSED DN-C4A-002/003` | Given 새 email/nickname, When createEmailSignup, Then generic 202 PENDING·opaque signupId·masked email·QUEUED·expiry/resend·revision을 반환하고 password/raw secret은 없다. |
| `AC-C4A-002` | 안전 Gate | Given non-test auth startup, When Argon2id benchmark, Then `m=19456 KiB,t=2,p=1` hard floor 아래로 낮추지 않고 p95 500ms를 넘으면 auth readiness/startup을 fail-closed하며 raw password/reversible column은 0건이다. |
| `AC-C4A-003` | 안전 Gate | Given 같은 signup key/body, When retry, Then 신규면 최초 actual signupId, duplicate/unknown이면 최초 persisted decoy signupId를 replay하고 public-flow/idempotency 외 account/secret/mail/audit를 추가하지 않는다. |
| `AC-C4A-004` | 안전 Gate | Given 사용한 signup key, When nickname/body를 바꿔 재사용, Then 409 IDEMPOTENCY_KEY_REUSED이고 상태가 변하지 않는다. |
| `AC-C4A-005` | `PROPOSED DN-C4A-002/003` | Given concurrent duplicate email/nickname, When signup, Then 승인 unique/열거 정책의 단일 winner와 일관된 응답이며 partial credential이 없다. |
| `AC-C4A-006` | 안전 Gate | Given local profile와 hostile Host/Forwarded/X-Forwarded-Host, When signup mail dispatch, Then request header와 무관하게 pinned exact HTTPS frontend origin의 fragment link만 Mailpit에 전달되고 query/application log에 recipient/secret이 없다. origin config missing/invalid면 dispatch/readiness가 fail-closed다. |
| `AC-C4A-007` | 안전 Gate | Given production profile와 provider credential 없음, When start/dispatch, Then fail-closed이고 local Mailpit fallback으로 운영 발송을 가장하지 않는다. |
| `AC-C4A-008` | `PROPOSED DN-C4A-003` | Given ACTIVE challenge input, When verify, Then challenge CONSUMED·membership ACTIVE·nextAction LOGIN이며 token을 자동 발급하지 않는다. |
| `AC-C4A-009` | 안전 Gate | Given challenge 생성/검증, When DB/log/trace/HTTP request target 검사, Then raw verification secret은 0건이고 DB에는 hash만 있으며 URL query/path에는 없다. |
| `AC-C4A-010` | `PROPOSED DN-C4A-003` | Given wrong/expired/exhausted input, When verify, Then 활성화되지 않고 승인 code/attempt/rate-limit과 안전 message를 반환한다. |
| `AC-C4A-011` | `PROPOSED DN-C4A-003` | Given actual/decoy handle과 resend 허용 시점 또는 동시 verify/resend, When row-lock race, Then 한 transition만 winner이고 actual은 이전 challenge SUPERSEDED·새 ACTIVE 최대 1개, decoy는 같은 cooldown/attempt/revision 상태만 갱신하며 old actual input은 실패하고 public shape/status/timing은 kind를 드러내지 않는다. |
| `AC-C4A-012` | 안전 Gate | Given signup/verify transaction failure injection, When 요청, Then account·credential·profile·challenge·idempotency/audit가 모두 rollback된다. |

## 2. Login·profile·logout

| ID | 상태 | Given / When / Then |
| --- | --- | --- |
| `AC-C4A-013` | `PROPOSED DN-C4A-001/003` | Given ACTIVE credential, When exact allowed Origin login, Then memory-only access와 선택 profile의 refresh HttpOnly/signed CSRF cookie를 발급한다. production HTTPS는 `__Host-` 이름, loopback local HTTP는 별도 `feelm_local_` 이름이고 섞지 않는다. absent/null/mismatch Origin은 credential 검증·session·cookie·audit mutation 전 403이다. |
| `AC-C4A-014` | 안전 Gate | Given unknown email 또는 wrong password, When login, Then unknown은 current production dummy PHC, wrong은 stored PHC를 verify하고 둘 다 모든 supported PHC workload의 calibrated `max(p99)+25ms` common floor+0~75ms CSPRNG jitter, 401 INVALID_CREDENTIALS 동일 body다. 누락·stale·unsupported calibration은 readiness 실패다. |
| `AC-C4A-015` | `PROPOSED DN-C4A-003` | Given pending membership, When login, Then 승인된 403/verification next action이며 protected actor가 되지 않는다. |
| `AC-C4A-016` | `PROPOSED DN-C4A-001` | Given login access artifact, When C1 protected endpoint 호출, Then 승인된 공통 boundary에서 같은 service actor로 인증된다. |
| `AC-C4A-017` | `PROPOSED DN-C4A-001` | Given refresh lineage, When old refresh를 재사용, Then 5초 race/older replay 규칙에 따라 409 또는 family revoke되고 loser에게 새 cookie/access가 전달되지 않는다. |
| `AC-C4A-018` | `PROPOSED DN-C4A-001` | Given current refresh+CSRF session, When exact Origin logout, Then current family가 한 번 revoke되고 profile별 exact attributes의 두 cookie와 protected cache가 제거된다. 둘 다 없는 retry도 exact Origin은 필수지만 CSRF/idempotency 없이 204 clear·DB/audit mutation 0이고 partial pair는 403, valid CSRF+invalid session은 401이다. |
| `AC-C4A-019` | 안전 Gate | Given token 없음/invalid/pending actor, When `/me`·onboarding·subscription 호출, Then 401/403이고 개인 데이터가 없다. |
| `AC-C4A-020` | `PROPOSED DN-C4A-002` | Given normalized nickname collision, When update, Then 409과 current revision이며 기존 profile을 유지한다. |
| `AC-C4A-021` | 안전 Gate | Given stale profile revision, When nickname update, Then 409 REVISION_CONFLICT이며 overwrite하지 않는다. |
| `AC-C4A-022` | 안전 Gate | Given auth/profile 오류, When logs·metrics 검사, Then email/nickname 전체값, password, access/refresh token, session hash가 message/label에 없다. |

## 3. Onboarding

| ID | 상태 | Given / When / Then |
| --- | --- | --- |
| `AC-C4A-023` | `PROPOSED DN-C4A-004` | Given Catalog fixtures, When listOnboardingMovies, Then UI_READY 실제 영화만 versioned policy·targetCount와 반환한다. |
| `AC-C4A-024` | 안전 Gate | Given MOV-C4A-HIDDEN, When preference replace, Then 404/validation failure이고 기존 set을 유지한다. |
| `AC-C4A-025` | 안전 Gate | Given LIKE/DISLIKE set, When replace, Then current journey 전체 set·count·revision이 원자 교체된다. |
| `AC-C4A-026` | 안전 Gate | Given request에 duplicate movieId/지원하지 않는 value, When replace, Then 400이고 부분 row가 없다. |
| `AC-C4A-027` | 핵심 분리 | Given onboarding LIKE/DISLIKE 저장·complete, When C1 DB/API 검사, Then Rating·ViewingRecord·Frame·Popcorn·Rating aggregate 생성은 0건이다. |
| `AC-C4A-028` | 확정 | Given preference 0개, When complete(SKIPPED), Then journey SKIPPED이고 DISLIKE row·개인 추천 개선 claim이 없다. |
| `AC-C4A-029` | `PROPOSED DN-C4A-004` | Given K가 승인 SUBMITTED minimum 미만, When complete(SUBMITTED), Then 409 MINIMUM_NOT_MET과 required count이며 preference를 유지한다. |
| `AC-C4A-030` | `PROPOSED DN-C4A-004` | Given 승인 maximum 초과 set, When replace/complete, Then 400/409이고 current set을 유지한다. |
| `AC-C4A-031` | 안전 Gate | Given valid completion, When response, Then journey 상태만 완료되고 recommendationProjection READY를 동기 success 조건으로 요구하지 않는다. |
| `AC-C4A-032` | 안전 Gate | Given recommender down, When preference/complete, Then write 성공·outbox pending/failed이며 C1/C0 핵심 API는 사용 가능하다. |
| `AC-C4A-033` | 안전 Gate | Given current journey revision 3, When stale revision 2 mutation, Then 409이고 preference/count/outbox가 변하지 않는다. |
| `AC-C4A-034` | `PROPOSED DN-C4A-004` | Given COMPLETED/SKIPPED journey, When restart, Then 승인된 경우 새 version이고 prior preferences는 SUPERSEDED; 승인 전 operation은 BLOCKED다. |
| `AC-C4A-035` | 안전 Gate | Given onboarding candidate empty/503, When 화면/API, Then empty/retry/skip을 제공하고 미선택을 DISLIKE로 만들지 않는다. |
| `AC-C4A-036` | 근거 Gate | Given K5/K10 completion fixture, When UI/response 설명 검사, Then REC-EV-011 K10 `alpha=0.2`의 작은 offline ranking 후보를 인용하되 actual UX·champion·expected-star·HIGH confidence를 주장하지 않는다. |

## 4. OTT subscription

| ID | 상태 | Given / When / Then |
| --- | --- | --- |
| `AC-C4A-037` | 확정 | Given C0 providers, When 목록 조회, Then active KR provider와 Catalog version을 사용하고 C4A provider 복제 row가 없다. |
| `AC-C4A-038` | 확정 | Given Netflix+second provider, When replace CONFIGURED, Then child set과 revision이 원자 교체된다. |
| `AC-C4A-039` | 확정 | Given CONFIGURED empty submit, When 조회, Then `CONFIGURED+[]`로 NOT_CONFIGURED/SKIPPED와 구분된다. |
| `AC-C4A-040` | 확정 | Given skip submit, When 조회, Then `SKIPPED+[]`이며 provider를 구독하지 않는다는 확정으로 해석하지 않는다. |
| `AC-C4A-041` | 안전 Gate | Given duplicate/retired/non-KR provider, When replace, Then 400/404이고 기존 child set·revision을 유지한다. |
| `AC-C4A-042` | 안전 Gate | Given concurrent same revision replace, When race, Then 한 winner만 commit하고 loser는 409다. |
| `AC-C4A-043` | 확정 | Given 미구독 provider에만 있는 영화, When 추천 후보 검사, Then subscription set 때문에 제외되지 않는다. |

## 5. Social·security·UI

| ID | 상태 | Given / When / Then |
| --- | --- | --- |
| `AC-C4A-044` | 범위 | Given provider registry, When 조회, Then GOOGLE/KAKAO/NAVER가 DISABLED로 존재한다. |
| `AC-C4A-045` | `BLOCKED DN-C4A-005` | Given decisions/credentials 미승인, When route/UI 검사, Then social start/callback/exchange/link operation과 버튼이 없고 예약 data model도 runtime credential을 읽지 않는다. |
| `AC-C4A-046` | 안전 Gate | Given secret scan, When source/docs/fixture/log 검사, Then password·verification·access/refresh/OAuth/SMTP raw secret이 0건이다. |
| `AC-C4A-047` | 안전 Gate | Given POSTGRES-DOWN, When 각 mutation, Then 503과 partial account/preference/subscription/session 0건이다. |
| `AC-C4A-048` | 안전 Gate | Given 여러 app instance의 동시 login/signup/verify/resend, When shared Redis server-clock fixed-window limit 경계, Then atomic counter+TTL로 정확히 한 global 판정과 429 Retry-After를 반환하며 local-memory fallback·입력값 노출이 없다. Redis unavailable/protocol mismatch면 503이고 identity/challenge/session mutation은 0건이다. |
| `AC-C4A-049` | 안전 Gate | Given OTHER actor/resource, When owner가 접근, Then 404이며 타 email/nickname/state가 없다. |
| `AC-C4A-050` | UI | Given signup 400/409/429/503, When SCR-C4A-001, Then field focus·retry·중복 submit 방지와 secret echo 금지를 만족한다. |
| `AC-C4A-051` | UI | Given verification fragment/expired/resend/error, When SCR-C4A-002, Then 첫 network 전에 fragment를 memory로 읽고 history에서 제거하며 masked email·retry timing을 접근 가능하게 표시하고 raw secret을 query/storage/trace에 남기지 않는다. |
| `AC-C4A-052` | UI | Given invalid/pending/login success, When SCR-C4A-003, Then generic credential error·승인 next action·protected navigation이 일치한다. |
| `AC-C4A-053` | UI | Given movie onboarding, When keyboard/touch, Then LIKE/DISLIKE/misselected 상태와 skip을 색·drag에만 의존하지 않고 조작한다. |
| `AC-C4A-054` | UI | Given 원형 취향 공간, When 포스터 중심을 drag로 배치, Then 중심 거리가 반지름 이하이면 LIKE, 초과이면 DISLIKE이고 경계·화면 크기와 무관하게 실제 DOM geometry로 판정한다. ⊖ 또는 미선택은 row를 만들지 않으며 keyboard·screen reader용 명시 버튼도 같은 최종 enum을 만든다. |
| `AC-C4A-055` | UI | Given OTT states, When SCR-C4A-005, Then explicit empty, skipped, not configured를 다른 control/copy로 표시한다. |
| `AC-C4A-056` | UI | Given projection PENDING/FAILED, When SCR-C4A-006, Then 저장 완료와 추천 후속 상태를 분리하고 Catalog 진입을 막지 않는다. |
| `AC-C4A-057` | UI | Given profile conflict/logout, When SCR-C4A-007, Then stale overwrite 금지, Origin+CSRF current-session logout, access memory·protected query clear를 수행한다. |
| `AC-C4A-058` | UI | Given 320/768/1280px·keyboard·screen reader·reduced motion, When 7 screens 검사, Then content/action loss 없이 사용 가능하다. |
| `AC-C4A-059` | UI | Given each protected route direct load/reload, When valid/invalid auth, Then 상태 복원 또는 login 이동이 결정 경계와 일치한다. |
| `AC-C4A-060` | E2E | Given 독립 fixture users, When signup→Mailpit fragment verify→login→LIKE/DISLIKE 또는 skip→OTT set→Origin+CSRF logout, Then raw secret artifact 없이 전체 상태가 계약과 일치한다. |

## 6. Fatal 보안·근거 보완 Gate

| ID | 상태 | Given / When / Then |
| --- | --- | --- |
| `AC-C4A-061` | `PROPOSED DN-C4A-001` | Given login/refresh/logout, When cookie 검사 또는 Origin/CSRF 누락·불일치 요청, Then 모두 exact Origin이 필수이고 login/refresh/logout 성공은 선택 profile의 두 `Set-Cookie`를 comma folding 없이 각각 issue/rotate/clear한다. active pair만 CSRF가 필수이며 cookie-less retry 예외는 AC-C4A-018만 따른다. |
| `AC-C4A-062` | `PROPOSED DN-C4A-001` | Given 같은 ACTIVE refresh generation의 동시 요청, When PostgreSQL primary `clock_timestamp()`와 row lock으로 race, Then 정확히 한 요청만 next generation을 commit하고 loser는 DB-clock 5초 이내 409·Set-Cookie/access body 0이며 family는 ACTIVE다. |
| `AC-C4A-063` | `PROPOSED DN-C4A-001` | Given ROTATED token을 rotatedAt+5초 초과 또는 current보다 2세대 이상 오래된 시점에 재사용, When refresh, Then lineage를 보존한 채 family 전체가 원자 revoke되고 이후 어떤 generation도 새 access/refresh/CSRF를 발급하지 않는다. |
| `AC-C4A-064` | `PROPOSED DN-C4A-001` | Given current session과 사전 복제 access JWT, When Origin+CSRF logout, Then refresh family와 두 cookie/UI auth state는 즉시 폐기되지만 복제 access는 server denylist 없는 v1에서 기존 exp 최대 10분까지만 유효하고 그 이후 401이다. |
| `AC-C4A-065` | `PROPOSED DN-C4A-003` | Given hostile Host/Forwarded/X-Forwarded-Host와 Mailpit verification link, When 생성·browser 진입, Then link origin은 pinned server config exact HTTPS origin이고 raw secret은 fragment에만 있으며 entry script가 첫 network/third-party resource 전에 memory로 읽고 `history.replaceState`로 제거하며 no-referrer 상태에서 verify POST body에만 전송한다. |
| `AC-C4A-066` | `PROPOSED DN-C4A-003` | Given 신규와 duplicate/unknown email, When signup 뒤 actual/decoy stable signupId로 verify·resend·retry, Then 둘 다 생성+24시간(연장 없음) public flow의 cooldown·attempt·expiry·revision과 같은 public status/shape/timing을 재현한다. REAL만 versioned current challenge를 갖고 DECOY는 account/challenge/secret/mail 0건이다. |
| `AC-C4A-067` | `BLOCKED DN-C4A-005` | Given social capability disabled와 향후 explicit-link contract fixture, When contract/security 검사, Then public route는 없고 예약 flow는 recent reauth, provider+issuer+subjectHmac unique, state/nonce single-use, PKCE S256, exact redirect, server issuer binding을 요구하며 mix-up/conflict 시 identity/session/audit가 전체 rollback되고 email claim 자동 병합이 없다. |
| `AC-C4A-068` | 근거 Gate | Given REC-EV-011 artifact, When C4 문서·UI copy 검사, Then K10 full-catalog `alpha=0.2`, paired NDCG delta CI `[0.000253, 0.002783]`를 정확히 인용하고 강제 K10·actual 만족·champion·expected-star 승인으로 승격하지 않는다. |
| `AC-C4A-069` | `PROPOSED DN-C4A-003` | Given stable REAL signupId, When resend twice, Then signupId는 불변이고 challenge version은 1→2→3, 각 prior는 SUPERSEDED, flow current_challenge_id는 최신만 가리키며 internal challenge ID/version은 HTTP/log에 없다. DECOY는 challenge row 0이고 같은 revision/expiry public response다. |
| `AC-C4A-070` | `PROPOSED DN-C4A-003` | Given public flow 생성+24h 뒤 PENDING account created+30d 전, When same password re-signup, Then old handle은 400 SIGNUP_FLOW_INVALID_OR_EXPIRED이고 새 REAL flow/challenge/mail을 생성하되 pending_purge_at은 연장하지 않는다. wrong password는 DECOY만 만든다. |
| `AC-C4A-071` | `PROPOSED DN-C4A-003` | Given pending_purge_at 도달, When verification과 purge가 race, Then DB lock/constraint로 한 winner이며 purge winner는 account/credential/profile/flow를 전부 제거해 새 signup을 허용하고 partial orphan이 없다. |
| `AC-C4A-072` | `PROPOSED DN-C4A-003` | Given actual/decoy/unknown handle matrix, When signup/verify/resend/login/refresh/logout, Then exact public status/code mapping이 operation별 OpenAPI ErrorResponse.code enum/const와 일치하고 generic 자유 문자열 code·handle kind·account 존재는 schema/timing에 없다. |
| `AC-C4A-073` | `PROPOSED DN-C4A-001` | Given production HTTPS와 local loopback HTTP, When login/logout, Then production은 `__Host-` Secure cookie, local은 `feelm_local_` non-Secure cookie만 사용하고 clear가 같은 attributes+Max-Age=0+epoch Expires를 재현하며 mixed profile cookie를 거부한다. |
| `AC-C4A-074` | `PROPOSED DN-C4A-001` | Given family terminal at T, When cleanup T+30d 전/후, Then 전에는 replay audit lineage가 있고 후에는 family AUTH_REFRESH_TOKEN hash rows와 AUTH_SESSION이 함께 삭제되며 active/absolute 30d와 혼동하지 않는다. |
| `AC-C4A-075` | `PROPOSED DN-C4A-003` | Given signup/verify/resend/login 각 structured scope 경계, When shared Redis server-time atomic policy를 실행, Then identity/flow/IP별 exact limits와 Retry-After가 일치하고 raw identity key/local fallback이 없으며 authority 장애는 mutation 전 503이다. |
| `AC-C4A-076` | `PROPOSED DN-C4A-001` | Given refresh token rotatedAt와 boundary 요청, When app/client/Redis clock을 왜곡해도, Then PostgreSQL primary clock_timestamp만 5초 race와 terminalAt을 판정하고 결과가 바뀌지 않는다. |
| `AC-C4A-077` | `PROPOSED DN-C4A-003` | Given signup/resend mail dispatch, When DB/outbox/worker/provider crash·key rotation 지점을 각각 주입, Then challenge에는 SHA-256 hash만, delivery material에는 AES-256-GCM ciphertext/nonce/key version만, outbox에는 materialId만 있다. raw recipient/secret/link는 DB/outbox/cache/log/trace/metric/test artifact 0건이고, owner join 뒤 worker memory와 TLS provider request wire에만 전달 중 일시 존재하며 adapter 관측성은 redact한다. provider 수락 전에는 같은 material을 retry하고 수락 뒤 삭제 전에는 같은 single-use link만 중복 가능하며 새 challenge는 없다. terminal/expiry에 ciphertext/nonce가 삭제되고 live material key retire 또는 missing version은 readiness/dispatch 실패다. |
| `AC-C4A-078` | `PROPOSED DN-C4A-003` | Given REAL/DECOY flow와 concurrent verify/resend, When CHECK/partial unique/composite FK/deferred trigger를 각각 위반, Then DECOY owner/current null, REAL owner non-null, OPEN REAL current ACTIVE same-owner challenge와 user당 ACTIVE 하나만 commit된다. transition은 한 winner이고 wrong attempt는 PostgreSQL flow count만 변경하며 Redis는 IP/global abuse만 담당한다. |
| `AC-C4A-079` | `PROPOSED DN-C4A-003` | Given flow expiry 직전 resend, When accepted, Then 새 challenge expiry는 `min(DB now+10m, public_flow_expires_at)`이고 actual/decoy 모두 attempt를 0으로 reset하며 expiry/revision mirror가 같다. |
| `AC-C4A-080` | `PROPOSED DN-C4A-003` | Given expired REAL flow의 PENDING account, When same-password recovery가 동시에 실행, Then 각 idempotency miss는 account lock 전 DB admittedAt/priorFlow의 recovery attempt를 만들고 DB winner만 attempt WON+새 REAL flow/material/mail을 commit한다. lock-wait loser는 같은 prior flow winner에 LOST_REPLAY로 연결해 safe result를 저장하며, winner commit 뒤 시작해 OPEN flow를 먼저 본 후속 요청은 attempt 0·DECOY다. 기존 nickname/OTT set/pending_purge_at은 보존된다. |
| `AC-C4A-081` | `PROPOSED DN-C4A-001` | Given production/local cookie profiles와 invalid/absent/partial cookies, When login/refresh/logout, Then OpenAPI security alternatives가 runtime pair와 일치하고 mixed pair는 거부된다. logout exact Origin 누락은 모든 branch 403, partial pair 403, valid CSRF+invalid session 401, same-key successful replay만 204이며 issue/rotate/clear는 정확히 두 개의 별도 Set-Cookie다. |
| `AC-C4A-082` | `PROPOSED DN-C4A-001/003` | Given supported Argon2 PHCs와 JWT key rotation, When calibration/token 검증, Then common floor는 모든 supported workload p99+25ms 이상이고 JWT는 RS256, required header/claims, exact iss/aud, 30s leeway, UUID ids, 10m max를 강제한다. unknown kid/alg/claim/time 또는 stale registry는 401/readiness fail-closed다. |
| `AC-C4A-083` | `PROPOSED DN-C4A-001/003` | Given trusted/untrusted proxy, current/previous HMAC keys와 Redis/DB failure, When signup/verify/resend/login/refresh, Then socket peer 또는 단일 canonical forwarded chain으로 IPv4 /24·IPv6 /56 projection을 만들고 한 Redis Function이 version counter를 합산 판정 후 current만 증가시킨다. initial signup+resend REAL/DECOY miss는 같은 MAIL_IDENTITY 5/h+10/d·MAIL_IP 20/h+100/d를 공유하고 refresh 30/min family·120/15m IP, verify 30/h IP를 atomic 적용한다. previous는 max window+public idem retention 전 retire하지 않고 Redis failure는 DB mutation 전 503이다. |
| `AC-C4A-084` | `PROPOSED DN-C4A-004` | Given duplicate movieId, 11 preferences, SKIPPED onboarding/OTT, and account signup, When JSON/domain/DB 검사, Then `x-unique-by: movieId`와 `maxItems:10`이 duplicate/overflow를 400으로 막는다. OnboardingState/Summary if/then은 SKIPPED/NOT_STARTED count 0, COMPLETED 1..10이고 domain은 preferenceCount=like+dislike=locked active count를 재검증한다. OTT SKIPPED providerIds는 0이며 signup은 NOT_CONFIGURED OTT set을 정확히 하나 만들고 rollback도 함께 한다. |
| `AC-C4A-085` | 안전 Gate | Given operation별 Idempotency-Key와 old/new HMAC-key deployment, When public signup/verify/resend와 actor mutation replay·flow cleanup·terminal+24h 경계를 검사, Then anonymous operation은 C1 actor ledger가 아닌 canonical record+current/previous scope alias+current/previous request-HMAC alias를 사용한다. sorted advisory lock 뒤 모든 alias를 같은 record에 insert해 양 deployment가 공유 version으로 같은 winner/body equality를 constant-time 검증하고 plain password digest가 0건이다. record/domain/recovery/safe response는 원자 commit하며 signup/resend/wrong-verify는 flow expiry까지, verify success/logout success는 terminal+24h 유지된다. replay는 attempt/rate/mail/audit를 소비하지 않고 cookie-less logout/Redis 429·503은 record가 없다. |
