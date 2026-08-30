# C4A Data Dictionary

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`

## 1. Enum

| Type | Values | 의미/승인 상태 |
| --- | --- | --- |
| `MembershipStatus` | `PENDING_EMAIL_VERIFICATION`, `ACTIVE` | verification 필수 여부는 DN-C4A-003 proposed |
| `EmailSignupFlowKind` | `REAL`, `DECOY` | internal only; API·error·log 미노출 |
| `EmailSignupFlowStatus` | `OPEN`, `VERIFIED`, `EXHAUSTED`, `EXPIRED` | stable signupId public-flow lifecycle |
| `EmailChallengeStatus` | `REQUESTED`, `ACTIVE`, `CONSUMED`, `EXPIRED`, `SUPERSEDED`, `EXHAUSTED` | EXHAUSTED limit은 DN-C4A-003 proposed |
| `DeliveryStatus` | `QUEUED`, `SENT`, `FAILED_RETRY_AVAILABLE` | provider 독립 상태 |
| `AuthSessionStatus` | `ACTIVE`, `REVOKED`, `REVOKED_FAMILY`, `EXPIRED` | DN-C4A-001 proposed |
| `RefreshTokenStatus` | `ACTIVE`, `ROTATED`, `REVOKED`, `REUSED`, `EXPIRED` | lineage/replay 판정; raw token 아님 |
| `OnboardingStatus` | `NOT_STARTED`, `IN_PROGRESS`, `COMPLETED`, `SKIPPED` | local count는 0 skip 또는 1..10 submit; rerun은 blocked |
| `OnboardingPreferenceValue` | `LIKE`, `DISLIKE` | C1 Rating과 별개 |
| `PreferenceRecordStatus` | `ACTIVE`, `SUPERSEDED` | full-set replace history; rerun public operation은 blocked |
| `RecommendationProjectionStatus` | `NOT_REQUESTED`, `PENDING`, `READY`, `FAILED` | onboarding completion과 분리 |
| `OttSelectionStatus` | `NOT_CONFIGURED`, `CONFIGURED`, `SKIPPED` | empty CONFIGURED와 skip 구분 |
| `SocialProvider` | `GOOGLE`, `KAKAO`, `NAVER` | 범위 확정, adapter는 DN-C4A-005 전 DISABLED |
| `SocialCapabilityStatus` | `DISABLED`, `AVAILABLE` | C4A 현재 모두 DISABLED |
| `SocialIdentityStatus` | `LINKED`, `UNLINKED` | DN-C4A-005 예약, 현재 row 0건 |
| `SocialLinkTransactionStatus` | `PENDING`, `CONSUMED`, `FAILED`, `EXPIRED` | DN-C4A-005 예약, 현재 row 0건 |
| `VerificationDeliveryMaterialStatus` | `READY`, `LEASED`, `ACCEPTED`, `DELETED` | encrypted single-use mail dispatch lifecycle |

## 2. USER_ACCOUNT

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `user_id` | UUID | N | service user 내부 PK; API/log에 직접 노출하지 않음 |
| `membership_status` | enum | N | proposed pending→active |
| `verified_at` | timestamptz | Y | ACTIVE일 때 non-null proposed |
| `pending_purge_at` | timestamptz | Y | PENDING 생성 시 `created_at+30d`; re-signup으로 연장 금지, activation 시 null |
| `created_at`, `updated_at` | timestamptz | N | UTC |
| `revision` | bigint | N | 1부터 증가 |

## 3. EMAIL_CREDENTIAL

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `user_id` | UUID | N | USER_ACCOUNT 1:1 |
| `email_normalized` | varchar(320) | N | normalization/unique는 DN-C4A-003; log·response는 masked |
| `password_hash` | text | N | password hash PHC string 등 검증 library output만 |
| `password_hash_version` | varchar | N | algorithm/parameter migration 식별 |
| `password_changed_at` | timestamptz | N | password reset/change는 C4A scope 밖 |

금지 column: `password`, `password_encrypted`, password hint.

## 4. USER_PROFILE

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `user_id` | UUID | N | USER_ACCOUNT 1:1 |
| `nickname_display` | varchar | N | 사용자 표시값; constraints DN-C4A-002 |
| `nickname_normalized` | varchar | N | comparison key proposed; unique scope DN-C4A-002 |
| `nickname_normalization_version` | varchar | N | normalization algorithm/version migration provenance |
| `revision` | bigint | N | stale nickname update 방지 |
| `updated_at` | timestamptz | N | UTC |

## 5. EMAIL_SIGNUP_PUBLIC_FLOW — DN-C4A-003 proposed

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `signup_id` | UUID | N | API의 stable opaque signupId, global unique |
| `user_id` | UUID | Y | REAL PENDING owner; DECOY는 null |
| `flow_kind` | enum | N | REAL/DECOY internal discriminator, public 노출 금지 |
| `identity_hmac` | text | N | normalized email의 server-keyed projection; raw email 금지 |
| `identity_hmac_key_version` | varchar | N | current/previous HMAC key dual-read와 rewrite provenance |
| `current_challenge_id` | UUID | Y | REAL의 current version FK; DECOY는 null, API 미노출 |
| `recovery_attempt_id` | UUID | Y | recovery winner attempt; ordinary/DECOY flow는 null |
| `recovery_from_signup_id` | UUID | Y | recovery가 대체한 expired REAL flow; attempt와 함께 null/non-null |
| `status` | enum | N | OPEN/VERIFIED/EXHAUSTED/EXPIRED |
| `public_delivery_status` | enum | N | actual/decoy 공통 QUEUED 등 safe state |
| `verification_expires_at` | timestamptz | N | current REAL challenge 또는 DECOY mirror expiry |
| `failed_attempt_count` | integer | N | actual/decoy 동일 transition; 0 이상 |
| `resend_available_at` | timestamptz | N | cooldown DN-C4A-003 |
| `revision` | bigint | N | verify/resend race guard; 1부터 증가 |
| `created_at` | timestamptz | N | public flow 시작, DB UTC |
| `public_flow_expires_at` | timestamptz | N | `created_at+24h`, resend로 연장 금지 |

DECOY row는 `user_id`, `current_challenge_id`가 null이고 mail outbox를 만들지 않는다. REAL/DECOY 모두
stable signupId, cooldown, failed attempt, expiry, revision을 같은 row lock과 public code mapping으로 처리한다.
flow 삭제는 PENDING account 삭제와 다르며 30일 purge 전 password-verified re-signup recovery를 허용한다.
CHECK는 REAL owner non-null과 OPEN REAL current pointer non-null을 강제한다. challenge의
`(signup_id,challenge_id,user_id)` UNIQUE를 참조하는 deferred composite FK 및 deferred constraint trigger가
COMMIT 시 current challenge ACTIVE/동일 owner와 terminal transition 일관성을 검증한다.

## 5.1 EMAIL_VERIFICATION_CHALLENGE

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `challenge_id` | UUID | N | internal challenge PK; API 미노출 |
| `signup_id` | UUID | N | EMAIL_SIGNUP_PUBLIC_FLOW FK; REAL flow만 challenge 보유 |
| `user_id` | UUID | N | PENDING membership owner |
| `challenge_version` | integer | N | `(signup_id,challenge_version)` unique, resend마다 +1 |
| `secret_hash` | text | N | ACTIVE 전 생성; raw secret column 금지 |
| `status` | enum | N | state machine 준수 |
| `delivery_status` | enum | N | mail call 결과 |
| `expires_at` | timestamptz | Y | TTL DN-C4A-003 |
| `consumed_at`, `superseded_at` | timestamptz | Y | terminal metadata |
| `created_at` | timestamptz | N | DB UTC; version provenance |

DECOY challenge row는 만들지 않는다. attempt/cooldown/revision mirror는 EMAIL_SIGNUP_PUBLIC_FLOW에 있다.
REAL resend는 과거 challenge를 SUPERSEDED하고 새 row를 insert한 뒤 public flow `current_challenge_id`를 바꾼다.
wrong verification attempt authority는 이 flow의 `failed_attempt_count` 하나뿐이다. Redis는 coarse IP/global abuse만
담당하고 challenge attempt를 복제하지 않는다. 처리 순서는 PostgreSQL idempotency replay 판정 뒤 Redis admission,
그 뒤 flow/challenge row lock과 attempt mutation이다.

## 5.2 VERIFICATION_DELIVERY_MATERIAL

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `material_id` | UUID | N | safe outbox가 참조하는 pseudonym |
| `challenge_id` | UUID | N | challenge당 최대 1, API 미노출 |
| `secret_ciphertext` | bytea | Y | AES-256-GCM envelope ciphertext; DELETED이면 null |
| `nonce` | bytea | Y | ciphertext와 함께 삭제; 재사용 금지 |
| `delivery_key_version` | varchar | N | secret-store key version; missing/unknown이면 readiness/dispatch fail-closed |
| `status` | enum | N | READY/LEASED/ACCEPTED/DELETED |
| `lease_owner`, `lease_expires_at` | varchar/timestamptz | Y | post-commit worker retry lease; owner는 instance pseudonym |
| `provider_accepted_at`, `deleted_at`, `expires_at` | timestamptz | Y/N | ciphertext 삭제와 challenge TTL 상한 |

challenge row에는 SHA-256 hash만 둔다. 가입/재전송 transaction이 raw secret을 메모리에서 만들고 hash와
AES-256-GCM ciphertext/nonce/key version을 함께 commit하며 `DOMAIN_OUTBOX`에는 materialId와 challenge pseudonym만
쓴다. worker는 lease 뒤 메모리에서만 복호화하고 pinned frontend origin fragment를 만든다. provider 수락 전 crash는
같은 material을 retry하고, 수락 후 삭제 전 crash는 같은 single-use link의 at-least-once 중복만 허용한다. 수락 또는
challenge CONSUMED/SUPERSEDED/EXPIRED 시 ciphertext/nonce를 삭제하고 challenge expiry를 넘겨 보관하지 않는다.
raw recipient와 link는 DB/outbox/cache/log/trace/test artifact에 저장하지 않는다. worker는 owner join 뒤 process
memory와 TLS provider request wire에서만 recipient/link를 사용하고 adapter request/response/error를 redact한다.

## 5.3 PUBLIC_AUTH_IDEMPOTENCY_RECORD — C4A physical ledger

C1 `IDEMPOTENCY_RECORD(actor_user_id,...)`는 ACTIVE actor mutation에만 재사용한다. signup 전 identity나
signupId를 가짜 actor_user_id로 넣지 않는다.

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `record_id` | uuid | N | canonical record PK; public 미노출 |
| `operation_code` | enum | N | `CREATE_SIGNUP`, `VERIFY_EMAIL`, `RESEND_EMAIL` |
| `idempotency_key` | varchar(128) | N | printable ASCII 8..128 |
| `response_status` | smallint | N | persisted safe success/error replay status |
| `safe_response_body` | jsonb | N | password/secret/internal kind 없는 exact response |
| `resource_signup_id` | uuid | Y | CREATE_SIGNUP 최초 REAL/DECOY stable handle 또는 terminal verify resource |
| `created_at`, `expires_at` | timestamptz | N | operation별 retention |

`PUBLIC_AUTH_IDEMPOTENCY_SCOPE_ALIAS`는 `scope_kind`, `scope_value`, `scope_hmac_key_version`,
`operation_code`, `idempotency_key`, `record_id FK`를 가진다. `(scope_kind,scope_value,operation_code,idempotency_key)`가
unique다. signup은 current/previous identity projection과 idempotency key로 만든 advisory-lock key를 정렬 획득하고
모든 alias를 조회한다. miss는 current와 previous alias를 같은 canonical record에 원자 insert하므로 old/new deployment
어느 쪽 winner도 공유 version alias를 통해 같은 record를 본다. verify/resend는 stable
signupId 하나를 잠근다. `PUBLIC_AUTH_IDEMPOTENCY_REQUEST_HMAC_ALIAS(record_id,request_hmac_key_version,
request_hmac)`는 record/key-version unique이며 miss 때 current/previous canonical-body HMAC alias를 모두 insert한다.
old/new deployment는 공유 digest version을 constant-time 비교한다. password/verificationSecret을 포함한 body의
unkeyed digest는 offline oracle이므로 금지한다.
live record가 참조하는 identity/digest key version은 expiry 전에 retire하지 않는다. record/domain/safe outbox/result는
같은 transaction이며 Redis admission은 miss 뒤에만 실행한다. signup/resend/wrong verify는 flow expiry까지,
verify success는 flow FK cascade와 독립적으로 terminal+24h 보존한다.

## 5.4 PENDING_SIGNUP_RECOVERY_ATTEMPT

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `recovery_attempt_id` | UUID | N | internal correlation PK; public 미노출 |
| `user_id` | UUID | N | locked PENDING account |
| `prior_expired_signup_id` | UUID | N | request 시작 시 관측한 expired REAL flow |
| `public_idempotency_scope_value`, `idempotency_key` | varchar | N | request ledger correlation; secret 없음 |
| `admitted_at` | timestamptz | N | account lock 전 PostgreSQL `clock_timestamp()` |
| `status` | enum | N | `STARTED`, `WON`, `LOST_REPLAY`, `FAILED` |
| `winner_attempt_id`, `winner_signup_id` | UUID | Y | WON이면 self/new flow, LOST_REPLAY면 linked winner |
| `linearized_at` | timestamptz | Y | account lock 아래 결정한 DB 시각 |

request 시작 시 expired prior flow를 본 idempotency miss만 attempt를 만든다. account lock 뒤에도 새 flow가 없으면
WON과 새 flow의 `recovery_attempt_id/recovery_from_signup_id`를 같은 transaction에 commit한다. lock 대기 중
자신의 admittedAt 뒤 같은 prior flow에서 winner가 생긴 경우만 LOST_REPLAY로 연결해 winner safe result를 자신의
idempotency record에 저장한다. 시작 시 이미 OPEN REAL flow를 본 후속 request는 attempt를 만들지 않고 DECOY다.

## 6. AUTH_SESSION — DN-C4A-001 proposed

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `session_id` | UUID | N | access subject의 session claim 후보 |
| `user_id` | UUID | N | ACTIVE membership |
| `token_family_id` | UUID | N | rotation replay family revoke 후보 |
| `current_generation` | bigint | N | monotonic, 1부터 시작 |
| `status` | enum | N | proposed lifecycle |
| `idle_expires_at`, `absolute_expires_at` | timestamptz | N | 7d idle/30d absolute 후보 |
| `revoked_at`, `revocation_reason` | timestamptz/varchar | Y | LOGOUT/REPLAY/EXPIRED safe code |
| `revision` | bigint | N | concurrent refresh/logout guard |

session-cookie 방식이 승인되면 이 table을 그대로 사용한다고 가정하지 않고 다시 검토한다.

## 6.1 AUTH_REFRESH_TOKEN — DN-C4A-001 proposed

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `refresh_record_id` | UUID | N | lineage PK |
| `session_id` | UUID | N | AUTH_SESSION FK |
| `token_hash` | text | N | raw refresh 금지, global unique |
| `csrf_nonce_hash` | text | N | signed CSRF cookie/header nonce hash; raw CSRF 저장 금지 |
| `generation` | bigint | N | `(session_id,generation)` unique |
| `status` | enum | N | ACTIVE/ROTATED/REVOKED/REUSED/EXPIRED |
| `replacement_record_id` | UUID | Y | 성공 rotation의 다음 generation FK |
| `issued_at`, `expires_at` | timestamptz | N | token lifecycle |
| `rotated_at`, `first_race_at`, `reused_at`, `revoked_at` | timestamptz | Y | race/replay audit |
| `family_terminal_at` | timestamptz | Y | family 종료 시각; 종료+30d lineage/token hash/session cleanup 기준 |

- session당 ACTIVE row는 최대 하나다.
- refresh 성공 시 raw replacement와 raw CSRF를 먼저 CSPRNG로 만들고 hash만 transaction에 저장한다.
- `rotated_at`과 비교 now는 row lock 중 PostgreSQL primary `clock_timestamp()`로만 판정한다.
- 바로 이전 ROTATED token의 `rotated_at+5s` 이내 reuse는 concurrent response race로 409이며 raw
  replacement를 서버가 재현하지 않는다. 5초 뒤 reuse 또는 더 오래된 generation은 family revoke다.
- family 종료 후 정확히 30일 보존한 뒤 AUTH_REFRESH_TOKEN 전체와 AUTH_SESSION을 같은 cleanup으로 삭제한다.

## 7. ONBOARDING_JOURNEY

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `journey_id` | UUID | N | versioned journey PK |
| `user_id` | UUID | N | current journey partial unique |
| `status` | enum | N | state machine |
| `catalog_version` | varchar | Y | movie 목록을 본 Catalog version |
| `selection_policy_version` | varchar | Y | DN-C4A-004 승인된 후보 정책 |
| `preference_count`, `like_count`, `dislike_count` | integer | N | 각각 0..10, `preference_count=like_count+dislike_count=locked ACTIVE row count`; SKIPPED/NOT_STARTED는 모두 0 |
| `recommendation_projection_status` | enum | N | domain complete와 분리 |
| `completed_at`, `skipped_at`, `superseded_at` | timestamptz | Y | terminal/version metadata |
| `revision` | bigint | N | local full-set replace/complete race guard; rerun extension 예약 |

## 8. ONBOARDING_PREFERENCE

| Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `journey_id` | UUID | N | owner journey |
| `movie_id` | UUID | N | C0 active UI_READY movie FK/read validation |
| `preference` | enum | N | LIKE/DISLIKE만 |
| `record_status` | enum | N | ACTIVE/SUPERSEDED |
| `catalog_version`, `selection_policy_version` | varchar | N | input provenance |
| `created_at`, `superseded_at` | timestamptz | Y | audit metadata |
| `revision` | bigint | N | current row revision |

Unique 후보: active `(journey_id,movie_id)`. journey active preference는 최대 10개 check/locked count로 제한한다.
Rating value, frameId, popcornId column은 금지한다.

## 9. OTT_SUBSCRIPTION_SET / USER_OTT_SUBSCRIPTION

| Entity.Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `OTT_SUBSCRIPTION_SET.user_id` | UUID | N | USER_ACCOUNT 1:1 |
| `.selection_status` | enum | N | NOT_CONFIGURED/CONFIGURED/SKIPPED |
| `.region` | char(2) | N | C4A `KR` |
| `.revision` | bigint | N | full-set replace guard |
| `USER_OTT_SUBSCRIPTION.user_id` | UUID | N | set owner |
| `.provider_id` | UUID | N | C0 OTT_PROVIDER reference, `(user_id,provider_id)` unique |
| `.selected_at` | timestamptz | N | UTC |

`selection_status != CONFIGURED`이면 active child row는 0개다. CONFIGURED는 child 0개도 허용한다.
모든 USER_ACCOUNT는 signup transaction에서 NOT_CONFIGURED set을 정확히 하나 가진다. migration은 기존 account의
누락 set을 backfill한 뒤 1:1 FK/PK를 enforce한다.

## 10. idempotency·outbox payload

ACTIVE actor mutation/logout만 C1 공통 `IDEMPOTENCY_RECORD`를 재사용한다. anonymous signup/verify/resend는
5.3의 C4A `PUBLIC_AUTH_IDEMPOTENCY_RECORD`를 쓴다. 둘 다 canonical validation/scope 뒤 PostgreSQL
idempotency lookup/replay-or-409를 Redis admission보다 먼저 수행한다. miss만 Redis를 소비하고 domain transition과
result를 한 transaction에 둔다. signup/resend/wrong-verify public record는 public flow expiry까지 유지하고
verify success public record와 active logout actor record는 terminal result+24h 유지한다. terminal public record는
flow cleanup FK cascade와 분리한다. cookie-less logout은 record를 만들지 않고 Redis 429/503도 result로 저장하지 않는다. payload allowlist에는 resource
pseudonym, state transition, schemaVersion만 두고 email/nickname/password/token/verification input,
OAuth body를 넣지 않는다. login/refresh token issuance replay 방식은 DN-C4A-001에서 별도로 정한다.

## 11. SOCIAL_IDENTITY / SOCIAL_LINK_TRANSACTION — DN-C4A-005 예약

두 entity는 문서 예약이며 capability가 DISABLED인 현재 migration/table/row가 없다.

| Entity.Column | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `SOCIAL_IDENTITY.social_identity_id` | UUID | N | reserved PK |
| `.user_id` | UUID | N | explicit linking을 시작한 ACTIVE user |
| `.provider` | enum | N | GOOGLE/KAKAO/NAVER |
| `.issuer_normalized` | varchar | N | server allowlist exact issuer |
| `.subject_hmac` | text | N | raw subject 대신 keyed projection; `(provider,issuer,subject_hmac)` unique |
| `.status`, `.linked_at`, `.unlinked_at`, `.revision` | enum/time/bigint | N/Y | versioned lifecycle |
| `SOCIAL_LINK_TRANSACTION.link_transaction_id` | UUID | N | opaque transaction PK |
| `.user_id`, `.session_id` | UUID | N | authenticated actor/current session binding |
| `.provider`, `.expected_issuer` | enum/varchar | N | mix-up 방지 server selection |
| `.exact_redirect_uri_hash` | text | N | wildcard/prefix match 금지 |
| `.state_hash`, `.nonce_hash` | text | N | raw state/nonce 저장 금지 |
| `.pkce_verifier_ciphertext` | text | N | secret-store key로 encrypted, TTL 10분, terminal 즉시 삭제 |
| `.status`, `.expires_at`, `.revision` | enum/time/bigint | N | single-use/race guard |

authorization code, provider access/refresh token, raw userinfo/ID token, raw subject/email claim은 column이 아니다.
