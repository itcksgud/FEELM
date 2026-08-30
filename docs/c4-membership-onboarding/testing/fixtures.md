# C4A 고정 Fixture

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`  
> 고정 시계: `2026-08-29T15:00:00Z`  
> 모든 address/token/hash label은 `.invalid`·test decoder 전용이며 운영 secret이 아니다.

## 1. Membership·profile

| Fixture | 값 | 상태 |
| --- | --- | --- |
| `USER-C4A-ACTIVE` | `018f6826-4da1-7c38-a846-8f794cd8b0cf` | C1 owner와 같은 service user, ACTIVE |
| `USER-C4A-PENDING` | `d68e9c58-d2e7-48a3-96a2-f62705bcd5f8` | PENDING_EMAIL_VERIFICATION |
| `USER-C4A-OTHER` | `5f93a51d-a6f1-41dc-8d86-6b570d53bd82` | C1 other와 같은 service user |
| pending stable signupId | `cf7c8139-d01b-4af0-944d-02eb8596b334` | EMAIL_SIGNUP_PUBLIC_FLOW actual ID; challenge ID와 다름 |
| persisted decoy duplicate signupId | `b477fe2d-9130-43cc-932a-6facb34340c4` | 24시간 public-flow state만 저장하는 generic 202 UUID; account 존재 의미 없음 |
| active email | `member-c4a@example.invalid` | fixture-only address; response는 `m***@example.invalid` |
| pending email | `pending-c4a@example.invalid` | fixture-only address; log 출력 금지 |
| active nickname | `필름수집가` | profile revision 2 |
| pending nickname | `첫관객` | profile revision 1 |
| collision nickname | `  필름수집가  ` | DN-C4A-002 proposed normalization conflict fixture |
| pending purge at | `2026-09-28T15:00:00Z` | account created+30d; re-signup으로 연장 금지 |

password 원문은 fixture 파일에 없다. test password encoder는 `PASSWORD-INPUT-REF-ACTIVE`라는
in-memory reference를 받아 `test-password-hash-v1`로 비교하며 input을 출력하지 않는다.

## 2. Verification·mail

| Fixture | 값 | 의미 |
| --- | --- | --- |
| `CHALLENGE-ACTIVE` | `7162df77-02a0-4a0e-b94b-d58e75afeccb` | pending user ACTIVE challenge |
| `CHALLENGE-OLD` | `c392605b-884f-4daf-a049-a854d64477ea` | resend 뒤 SUPERSEDED |
| current challenge pointer | `CHALLENGE-ACTIVE`, version `2` | public flow 내부 FK; API/log 미노출 |
| secret hash | `test-verification-hash-v1` | verifier fake hash output, raw secret 아님 |
| Mailpit message ref | `mailpit://message/C4A-PENDING-LATEST` | test가 Mailpit API로 최신 body를 읽는 reference; secret value 기록 금지 |
| proposed expiry | `2026-08-29T15:10:00Z` | DN-C4A-003의 10분 비교안, 승인 상수 아님 |
| proposed resend | `2026-08-29T15:01:00Z` | DN-C4A-003의 60초 비교안 |

신규와 duplicate signup fixture는 모두 `emailMasked=m***@example.invalid`, `deliveryStatus=QUEUED`,
`verificationExpiresAt=2026-08-29T15:10:00Z`, `resendAvailableAt=2026-08-29T15:01:00Z`,
`revision=1`인 같은 public shape다. 신규는 actual signupId, duplicate/unknown은 위 persisted decoy UUID다.
두 경로 모두 dummy/actual과 모든 supported PHC workload의 calibrated `max(p99)+25ms` floor + 고정 test jitter bucket에서
응답하며 timing 측정 결과에 raw email을 label로 쓰지 않는다.

actual/decoy EMAIL_SIGNUP_PUBLIC_FLOW row는 모두 `2026-08-30T15:00:00Z`까지 존재하고 resend로 연장되지
않으며 같은 resend/attempt/revision transition을 수행한다. decoy row는 `user_id=null`,
`current_challenge_id=null`, challenge/secret/mail outbox 0건이다. actual resend는 같은 signupId에서 old
challenge v1을 SUPERSEDED하고 current_challenge_id를 v2로 바꾼다. hostile Host 계열
fixture와 무관하게 local pinned verification origin만 message link에 사용한다.

E2E test는 Mailpit message의 HTTPS **fragment**에서 verification input을 component memory로 옮기고
첫 network/third-party resource 전에 `history.replaceState`로 fragment를 제거한다. POST body로 제출한 뒤
즉시 폐기하며 query, screenshot, trace, console, assertion error에 그 값을 포함하지 않는다.

## 3. Fake auth — DN-C4A-001 proposed

| Fixture | Label | 의미 |
| --- | --- | --- |
| active bearer | `test-c4a-active-token` | fake decoder가 USER-C4A-ACTIVE로 해석 |
| pending bearer | `test-c4a-pending-token` | protected API에서 403/401 Gate |
| other bearer | `test-c4a-other-token` | 소유권 test |
| invalid bearer | `test-c4a-invalid-token` | 401 |
| session/family | `74e7b61d-b0fd-43d5-ae39-97706b9723b3` / `c4a-refresh-family-01` | proposed ACTIVE auth session revision 2 |
| generation 1 refresh hash | `test-refresh-hash-v1` | ROTATED; raw refresh token 아님 |
| generation 1 CSRF nonce hash | `test-csrf-nonce-hash-v1` | refresh와 함께 ROTATED; raw CSRF 아님 |
| generation 2 refresh hash | `test-refresh-hash-v2` | ACTIVE current generation; raw refresh token 아님 |
| generation 2 CSRF nonce hash | `test-csrf-nonce-hash-v2` | ACTIVE current generation; raw CSRF 아님 |
| generation 1 rotatedAt | `2026-08-29T14:59:58Z` | 고정 시계 기준 2초 전, 5초 race grace 내부 |

실제 JWT secret, access/refresh token 또는 signing key는 없다.

cookie profile fixture는 production HTTPS의 `__Host-feelm_refresh`/`__Host-feelm_csrf`와 local loopback
HTTP의 `feelm_local_refresh`/`feelm_local_csrf`를 분리한다. clear는 발급과 같은 name, Path=/, no Domain,
SameSite=Lax, Secure/HttpOnly 속성에 `Max-Age=0`과 epoch Expires를 추가한다.

## 4. Onboarding movies·preferences

| Fixture | UUID | 상태 |
| --- | --- | --- |
| `MOV-C4A-01` | `6b226903-0ca4-4f5a-9bf0-50d6cedd224c` | C0 UI_READY, LIKE |
| `MOV-C4A-02` | `19406c31-213f-4fe1-93f6-109f8570ec20` | C0 UI_READY, DISLIKE |
| `MOV-C4A-03` | `1958ba3a-3d8c-4a4f-8845-124c0b12373e` | C0 UI_READY, 미선택 |
| `MOV-C4A-HIDDEN` | `e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490` | UI_READY가 아니어서 목록/저장 거부 |
| `JOURNEY-IN-PROGRESS` | `597d6936-dc37-45fa-b20e-a74a9a8b6022` | preference 2, revision 3 |
| `JOURNEY-SKIPPED` | `ed2e8d08-c4b1-4108-86d8-73ef9b94a762` | preference 0, SKIPPED |

- `catalogVersion=catalog-fixture-v1`
- `selectionPolicyVersion=onboarding-proposed-v1` — DN-C4A-004 전 제품 승인값 아님
- K5 set은 UI 부담 비교 fixture이며 REC-EV-011 full-catalog 후보 `alpha=0.1`, paired NDCG delta
  CI `[0.000016, 0.002202]`의 0 경계 근처 결과를 연결한다.
- K10 set은 REC-EV-003B data-only와 REC-EV-011 full-catalog `alpha=0.2`, paired NDCG delta
  CI `[0.000253, 0.002783]`를 연결한다. 어느 것도 실제 onboarding 만족·champion fixture가 아니다.

## 5. OTT subscription

| Fixture | UUID/value | 의미 |
| --- | --- | --- |
| Netflix provider | `d392a4d5-0428-4e06-aa41-aef899c06842` | C0 active KR provider |
| second provider | `780fef88-87c8-43ef-a4fe-3e0931f9140d` | C0 active KR provider fixture |
| retired provider | `14f86cf3-21f2-4767-97bd-d79244302fc0` | replace 거부 |
| set revision | `2` | ACTIVE user CONFIGURED Netflix only |
| explicit empty | `CONFIGURED + []` | 구독 없음 명시 |
| unanswered | `NOT_CONFIGURED + []` | 아직 응답 안 함 |
| skipped | `SKIPPED + []` | 나중에 설정 |

## 6. Idempotency·race·failure

| Fixture | 기대 |
| --- | --- |
| `c4a-signup-0001` same canonical body | 최초 pending result replay, account/challenge 추가 0 |
| `c4a-signup-0001` changed nickname | 409 IDEMPOTENCY_KEY_REUSED |
| concurrent duplicate signup | 승인 unique policy에 따른 단일 account winner; partial credential 0 |
| duplicate/unknown signup | persisted decoy signupId와 신규와 동일한 generic 202 shape; public-flow 외 account/secret/mail 추가 0; follow-up cooldown/attempt/revision 재현 |
| flow expiry then PENDING same-password re-signup at +25h | old flow invalid; 새 stable REAL signupId+challenge v1+mail, pending purge at 불변 |
| flow expiry then PENDING wrong-password re-signup | 새 DECOY flow만; account/challenge/mail 변화 0 |
| PENDING purge at +30d race | verification 또는 purge 한 winner; purge winner면 account/credential/profile/flow 제거 후 새 signup 가능 |
| concurrent PENDING recovery | 두 request가 prior expired flow를 본 admittedAt attempt를 갖고 winner WON/new flow, lock-wait loser LOST_REPLAY/winner link; commit 뒤 follow-up은 attempt 0 DECOY |
| exact verify public mapping | malformed 400 VALIDATION_ERROR; wrong 400 VERIFICATION_INVALID; unknown/terminal/expired 400 SIGNUP_FLOW_INVALID_OR_EXPIRED; exhausted 429 VERIFICATION_ATTEMPTS_EXHAUSTED; throttle 429 AUTH_FLOW_THROTTLED; idempotency 409 IDEMPOTENCY_KEY_REUSED; state race 409 AUTH_STATE_CONFLICT; dependency 503 AUTH_DEPENDENCY_UNAVAILABLE |
| unknown login vs wrong password | supported PHC calibrated common floor+fixed jitter, 401 INVALID_CREDENTIALS same body; stale/unsupported calibration readiness fail |
| hostile Host/Forwarded mail dispatch | pinned exact HTTPS frontend origin만 사용; config invalid면 503/readiness fail |
| HMAC rotation quota boundary | current/previous logical-window count 합산 후 current만 증가; initial signup+resend가 같은 MAIL_IDENTITY/MAIL_IP quota, third version activation 금지 |
| shared rate-limit authority down | signup/login/verify/resend 503, local fallback·identity/challenge/session mutation 0 |
| simultaneous generation 1 refresh | PostgreSQL clock_timestamp 기준 한 winner만 generation 2 생성; loser는 5초 이내 409·Set-Cookie 0·family ACTIVE |
| generation 1 reuse at +6s | family 전체 REVOKED, 새 access/refresh/CSRF 0 |
| generation 0 reuse | grace와 무관하게 family 전체 REVOKED, 새 access/refresh/CSRF 0 |
| login/refresh/logout missing Origin 또는 active CSRF mismatch | 403, token/session/idempotency/audit mutation 0 |
| current-session logout | exact Origin+CSRF 뒤 family REVOKED·profile exact 두 cookie clear·UI access/cache clear; 사전 복제 access는 최대 10분 exp |
| cookie-less logout retry | exact Origin은 필수, CSRF/idempotency 없이 204+두 exact clear, DB/audit mutation 0 |
| one-cookie logout | exact Origin이어도 403 CSRF_FORBIDDEN; session/audit mutation 0 |
| valid CSRF + unknown/expired/revoked refresh logout | 401 AUTH_SESSION_INVALID; same-key stored success replay만 204 exact clear |
| terminal refresh lineage +30d | AUTH_REFRESH_TOKEN token hashes와 AUTH_SESSION을 같은 cleanup에서 삭제 |
| verification dispatch crash matrix | challenge hash-only, AES-256-GCM material/key version, outbox materialId only; raw recipient/link은 worker memory+TLS provider wire 일시 예외이고 persistent/log artifact 0; pre-accept retry same material, post-accept duplicate same link, terminal ciphertext deletion |
| public auth idempotency | canonical record PK + PRE_SIGNUP_IDENTITY current/previous alias unique rows/SIGNUP_FLOW alias, sorted lock, versioned keyed request HMAC; old/new deployment same winner, plain password/verification digest 0, verify terminal replay survives flow cleanup |
| late resend at flow expiry-3m | expiry=min(now+10m,flow expiry), actual/decoy failed attempts reset 0 and mirror revision |
| duplicate movieId or 11 preferences | JSON x-unique-by movieId/maxItems 10으로 400, DB mutation 0 |
| onboarding SKIPPED with expectedPreferenceCount 1 | JSON if/then 400; locked DB nonzero skip도 conflict |
| OTT SKIPPED with providerIds | JSON if/then 400; signup creates exactly one NOT_CONFIGURED set |
| `c4a-pref-replace-0001` retry | preference/journey revision 추가 0 |
| stale journey revision 2 vs current 3 | 409 REVISION_CONFLICT |
| concurrent subscription revision 2 | 한 요청만 revision 3, loser 409 |
| `MAILPIT-DOWN` | account 상태와 delivery failure 명시, raw secret response 0 |
| `POSTGRES-DOWN` | 503, partial account/preference/subscription/session 0 |
| `RECOMMENDER-DOWN` | onboarding/OTT write 성공, projection FAILED/PENDING |
| `SOCIAL-CAPABILITY-DISABLED` | social route/button/credential load 0 |

## 7. 향후 social linking 예약 fixture — operation은 없음

| Fixture | safe label | 기대 |
| --- | --- | --- |
| provider/issuer | `GOOGLE` / `ISSUER-ALLOWLIST-GOOGLE` | server가 start transaction에 고정; callback 입력이 바꾸지 못함 |
| subject key | `test-provider-subject-hmac-v1` | raw provider subject가 아닌 HMAC output |
| transaction | `SOCIAL-LINK-TX-01`, 10분 | authenticated user/session, exact redirect label, provider, issuer에 결합 |
| state/nonce | `test-state-hash-v1` / `test-nonce-hash-v1` | 각 raw 값이 아닌 hash, single use |
| PKCE | `test-pkce-verifier-ciphertext-v1` / `test-s256-challenge-v1` | verifier는 transaction 동안만 암호화 보존 후 폐기 |
| mix-up callback | `ISSUER-OTHER` | callback 전에 전체 rollback, identity/session/audit 0 |
| identity conflict | existing `(GOOGLE, ISSUER-ALLOWLIST-GOOGLE, test-provider-subject-hmac-v1)` | generic conflict, 어느 account인지 비노출, partial link 0 |

Google/Kakao/Naver capability는 계속 `DISABLED`이고 이 fixture는 public route/API/UI 구현 권위가 아니다.
