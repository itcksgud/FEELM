# C4A Logical ERD

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`  
> C0 `MOVIE_IDENTITY`·`OTT_PROVIDER`, C1 actor `IDEMPOTENCY_RECORD`·`DOMAIN_OUTBOX`는 재정의하지 않는다.
> 익명 public auth는 actor ledger와 분리된 `PUBLIC_AUTH_IDEMPOTENCY_RECORD`를 C4A가 소유한다.

```mermaid
erDiagram
    USER_ACCOUNT ||--|| EMAIL_CREDENTIAL : authenticates_by
    USER_ACCOUNT ||--|| USER_PROFILE : displays_as
    USER_ACCOUNT o|--o{ EMAIL_SIGNUP_PUBLIC_FLOW : real_flow_owns
    EMAIL_SIGNUP_PUBLIC_FLOW o|--o{ EMAIL_VERIFICATION_CHALLENGE : versions
    EMAIL_VERIFICATION_CHALLENGE ||--o| VERIFICATION_DELIVERY_MATERIAL : dispatches_with
    EMAIL_SIGNUP_PUBLIC_FLOW o|--o{ PUBLIC_AUTH_IDEMPOTENCY_RECORD : safe_result_scope
    PUBLIC_AUTH_IDEMPOTENCY_RECORD ||--|{ PUBLIC_AUTH_IDEMPOTENCY_SCOPE_ALIAS : reachable_by
    PUBLIC_AUTH_IDEMPOTENCY_RECORD ||--|{ PUBLIC_AUTH_IDEMPOTENCY_REQUEST_HMAC_ALIAS : body_equals_by
    USER_ACCOUNT ||--o{ PENDING_SIGNUP_RECOVERY_ATTEMPT : recovery_admissions
    USER_ACCOUNT ||--o{ AUTH_SESSION : proposed_has
    AUTH_SESSION ||--o{ AUTH_REFRESH_TOKEN : rotates
    USER_ACCOUNT ||--o{ ONBOARDING_JOURNEY : performs
    ONBOARDING_JOURNEY ||--o{ ONBOARDING_PREFERENCE : contains
    MOVIE_IDENTITY ||--o{ ONBOARDING_PREFERENCE : selected_movie
    USER_ACCOUNT ||--|| OTT_SUBSCRIPTION_SET : configures
    OTT_SUBSCRIPTION_SET ||--o{ USER_OTT_SUBSCRIPTION : contains
    OTT_PROVIDER ||--o{ USER_OTT_SUBSCRIPTION : references
    USER_ACCOUNT ||--o{ SOCIAL_IDENTITY : reserved_links
    USER_ACCOUNT ||--o{ SOCIAL_LINK_TRANSACTION : reserved_starts

    USER_ACCOUNT {
      uuid user_id PK
      enum membership_status
      timestamptz verified_at
      timestamptz pending_purge_at
      bigint revision
    }
    EMAIL_CREDENTIAL {
      uuid user_id PK_FK
      string email_normalized UK_PROPOSED
      string password_hash
      string password_hash_version
    }
    USER_PROFILE {
      uuid user_id PK_FK
      string nickname_display
      string nickname_normalized UK_PROPOSED
      string nickname_normalization_version
      bigint revision
    }
    EMAIL_SIGNUP_PUBLIC_FLOW {
      uuid signup_id PK_PUBLIC_STABLE
      uuid user_id FK_NULL_FOR_DECOY
      enum flow_kind INTERNAL
      string identity_hmac
      string identity_hmac_key_version
      enum status
      uuid current_challenge_id FK_NULL_FOR_DECOY
      uuid recovery_attempt_id FK_NULL
      uuid recovery_from_signup_id FK_NULL
      timestamptz verification_expires_at
      int failed_attempt_count PROPOSED
      timestamptz resend_available_at
      timestamptz public_flow_expires_at
      bigint revision
    }
    EMAIL_VERIFICATION_CHALLENGE {
      uuid challenge_id PK
      uuid signup_id FK
      uuid user_id FK
      int challenge_version
      string secret_hash
      enum status
      enum delivery_status
      timestamptz expires_at PROPOSED
    }
    VERIFICATION_DELIVERY_MATERIAL {
      uuid material_id PK
      uuid challenge_id UK_FK
      bytes secret_ciphertext
      bytes nonce
      string delivery_key_version
      enum status
      timestamptz lease_expires_at
      timestamptz deleted_at
    }
    PUBLIC_AUTH_IDEMPOTENCY_RECORD {
      uuid record_id PK
      enum operation_code
      string idempotency_key
      int response_status
      jsonb safe_response_body
      uuid resource_signup_id
      timestamptz expires_at
    }
    PUBLIC_AUTH_IDEMPOTENCY_SCOPE_ALIAS {
      enum scope_kind UK
      string scope_value UK
      enum operation_code UK
      string idempotency_key UK
      string scope_hmac_key_version
      uuid record_id FK
    }
    PUBLIC_AUTH_IDEMPOTENCY_REQUEST_HMAC_ALIAS {
      uuid record_id UK_FK
      string request_hmac_key_version UK
      string request_hmac
    }
    PENDING_SIGNUP_RECOVERY_ATTEMPT {
      uuid recovery_attempt_id PK
      uuid user_id FK
      uuid prior_expired_signup_id FK
      uuid winner_signup_id FK_NULL
      uuid winner_attempt_id FK_NULL
      enum status
      timestamptz admitted_at
      timestamptz linearized_at
    }
    AUTH_SESSION {
      uuid session_id PK_PROPOSED
      uuid user_id FK
      uuid token_family_id UK
      bigint current_generation
      enum status
      timestamptz idle_expires_at PROPOSED
      timestamptz absolute_expires_at PROPOSED
      timestamptz revoked_at
      bigint revision
    }
    AUTH_REFRESH_TOKEN {
      uuid refresh_record_id PK_PROPOSED
      uuid session_id FK
      string token_hash UK
      string csrf_nonce_hash
      bigint generation
      enum status
      uuid replacement_record_id FK_NULL
      timestamptz rotated_at
      timestamptz first_race_at
      timestamptz reused_at
    }
    ONBOARDING_JOURNEY {
      uuid journey_id PK
      uuid user_id FK
      enum status
      string selection_policy_version
      string catalog_version
      bigint revision
    }
    ONBOARDING_PREFERENCE {
      uuid journey_id FK
      uuid movie_id FK
      enum preference
      bigint revision
    }
    OTT_SUBSCRIPTION_SET {
      uuid user_id PK_FK
      enum selection_status
      string region
      bigint revision
    }
    USER_OTT_SUBSCRIPTION {
      uuid user_id FK
      uuid provider_id FK
      timestamptz selected_at
    }
    SOCIAL_IDENTITY {
      uuid social_identity_id PK_RESERVED
      uuid user_id FK
      enum provider
      string issuer_normalized
      string subject_hmac
      enum status
      bigint revision
    }
    SOCIAL_LINK_TRANSACTION {
      uuid link_transaction_id PK_RESERVED
      uuid user_id FK
      uuid session_id FK
      enum provider
      string expected_issuer
      string exact_redirect_uri_hash
      string state_hash
      string nonce_hash
      string pkce_verifier_ciphertext
      enum status
      timestamptz expires_at
    }
```

## 1. Cardinality·unique 후보

| Invariant | 상태 |
| --- | --- |
| USER_ACCOUNT 1 : 1 EMAIL_CREDENTIAL / USER_PROFILE | email membership 범위에서 확정 |
| email normalized unique | `PROPOSED: DN-C4A-003` |
| nickname normalized global unique | `PROPOSED: DN-C4A-002` |
| 반환 stable signupId마다 `EMAIL_SIGNUP_PUBLIC_FLOW` actual/decoy row 정확히 1 | 생성+24시간, resend 연장 금지; cooldown·attempt·revision 재현; kind 미노출 |
| REAL flow `current_challenge_id`만 current ACTIVE version을 가리킴 | `(signup_id,challenge_version)` unique; 과거 row SUPERSEDED, DECOY challenge row 0 |
| user당 검증 가능한 ACTIVE challenge 최대 1 | 안전 invariant; TTL/attempt는 DN-C4A-003 |
| flow/challenge/user composite ownership | challenge `(signup_id,challenge_id,user_id)` UNIQUE + flow deferred composite FK. DECOY owner/current null, REAL owner 항상 non-null, OPEN REAL current non-null. deferred constraint trigger가 COMMIT 때 current status ACTIVE와 terminal transition을 검증 |
| delivery material challenge당 최대 1 | challenge hash와 분리된 AES-256-GCM ciphertext; safe outbox는 material id만 참조 |
| public auth idempotency physical key | canonical record는 `record_id` PK. alias `(scope_kind,scope_value,operation_code,idempotency_key)` unique가 record를 참조하며 PRE_SIGNUP current/previous alias를 둘 다 insert해 old/new deployment가 같은 winner를 조회함 |
| public idempotency request equality | keyed HMAC-SHA-256 + digest key version. password/verificationSecret canonical body의 plain hash 금지; live scope/digest key retire 금지 |
| public request digest aliases | record당 current/previous keyed canonical-body HMAC alias. shared version으로 old/new deployment가 constant-time equality 검증 |
| recovery attempt lineage | winner flow는 `(recovery_attempt_id,recovery_from_signup_id)`를 갖고, loser는 같은 prior flow winner attempt/result에만 연결. 후속 요청은 attempt 0 |
| `AUTH_SESSION.token_family_id` unique | `PROPOSED: DN-C4A-001`; current session logout/family revoke scope |
| `AUTH_REFRESH_TOKEN.token_hash` unique | raw token 없음; ACTIVE/ROTATED record를 family lifetime 동안 유지해 reuse 탐지 |
| session당 ACTIVE refresh generation 최대 1 | row lock + partial unique; concurrent refresh 한 winner |
| `(session_id,generation)` unique | replacement lineage와 previous-token 판정 재현 |
| user당 current onboarding journey 최대 1 | `superseded_at IS NULL` partial unique; rerun version은 blocked extension 예약 |
| `(journey_id,movie_id)` preference 최대 1 | 확정 |
| journey active preference/count | active row 최대 10; `preference_count=like_count+dislike_count=locked active row count`, SKIPPED/NOT_STARTED는 0 |
| user당 OTT_SUBSCRIPTION_SET 정확히 1 | signup transaction에서 NOT_CONFIGURED로 생성하고 legacy row는 migration에서 backfill |
| `(user_id,provider_id)` subscription 최대 1 | 확정 |

## 2. Transaction boundary

| Mutation | 같은 transaction |
| --- | --- |
| signup | sorted current/previous pre-signup scope advisory lock 뒤 PUBLIC_AUTH_IDEMPOTENCY_RECORD miss, USER_ACCOUNT, EMAIL_CREDENTIAL hash, USER_PROFILE, NOT_CONFIGURED OTT_SUBSCRIPTION_SET, REAL stable public flow, version 1 challenge/hash/current_challenge_id, encrypted delivery material, keyed request HMAC result, safe outbox reference |
| duplicate/unknown signup | DECOY stable public-flow row·PUBLIC_AUTH_IDEMPOTENCY_RECORD를 원자 저장하고 account/challenge/secret/mail은 만들지 않음; same-parameter dummy Argon2id+queue-equivalent work 뒤 같은 public shape 반환 |
| PENDING re-signup recovery | request-start observation에서 expired prior flow를 본 경우 recovery attempt/admittedAt을 만들고 account lock 후 재검증. winner attempt+새 REAL flow가 같은 prior를 연결; lock-wait loser만 winner attempt/result에 LOST_REPLAY 연결. 이미 new OPEN flow를 본 후속은 attempt 없이 DECOY |
| verify/resend | stable signupId scope PUBLIC_AUTH_IDEMPOTENCY_RECORD lookup 뒤 actual/decoy flow row lock과 attempt/revision/current_challenge_id 판정. REAL verify는 current challenge consume+membership ACTIVE+verifiedAt+pending_purge_at null; record/safe audit/outbox와 같은 transaction, token/session은 생성하지 않음 |
| login | AUTH_SESSION + generation 1 AUTH_REFRESH_TOKEN + CSRF nonce hash를 원자 생성; raw access/refresh/CSRF는 response/cookie 전달 뒤 저장 금지 |
| refresh winner | session과 presented token row lock, prior ACTIVE→ROTATED, replacement ACTIVE insert, generation/idle expiry 증가를 한 transaction으로 반영 |
| previous-token race ≤5s | PostgreSQL `clock_timestamp()` 기준 바로 이전 ROTATED generation이면 409만 반환하고 replacement raw token을 재발급하거나 family를 즉시 revoke하지 않음 |
| previous-token reuse >5s/older generation | AUTH_SESSION→REVOKED_FAMILY, family의 ACTIVE/ROTATED token→REVOKED, audit를 한 transaction으로 반영 |
| nickname | normalized unique claim, display nickname, profile revision, idempotency |
| preferences replace | journey lock/revision, old active rows supersede, new set, idempotency, projection outbox |
| complete/skip | journey state/revision, idempotency, projection outbox |
| subscriptions replace | set lock/revision, child delete+insert, idempotency |
| logout active | refresh+exact Origin+signed CSRF로 찾은 AUTH_SESSION→REVOKED와 family token revoke, safe audit; cookie clear는 exact profile response headers |
| logout cookie-less retry | exact allowed Origin은 필수. 두 auth cookie가 모두 없으면 CSRF/idempotency 없이 204+exact clear만, DB/audit mutation 0; 한 cookie만 있으면 403 |

메일 provider와 recommender 호출은 DB transaction 밖이다. 메일 worker는 commit된 outbox reference와 lease로
encrypted delivery material을 claim하고 secret-store의 versioned key로 메모리에서만 복호화한다. provider 수락 전
crash는 같은 material/challenge를 retry하고, 수락 후 삭제 전 crash는 같은 single-use link의 중복 발송만 허용한다.
새 secret/challenge를 만들지 않으며 provider 수락 또는 challenge terminal/superseded/expired에 ciphertext와 nonce를
삭제한다. 외부 실패는 domain rollback을 가장하지 않고
delivery/projection 상태로 분리한다. auth rate-limit authority는 모든 instance가 공유하는 Redis이며
server UTC clock+atomic counter/TTL operation을 사용한다. unavailable이면 local fallback 없이 public auth mutation을
503으로 닫는다. HMAC rotation 중 Redis Function은 current/previous projection counter를 한 번에 합산 판정하고
허용 시 current만 increment한다. signup 최초 mail과 resend는 같은 identity/IP mail quota를 공유한다.

## 3. 금지 관계

- `ONBOARDING_PREFERENCE`에서 C1 `RATING`, `FRAME`, `POPCORN`으로 insert하는 FK·trigger가 없다.
- MovieLens user ID column이 없다.
- C4A가 C0 `MOVIE_IDENTITY`·`OTT_PROVIDER` row를 복제하거나 삭제하지 않는다.
- raw password, plaintext verification secret, access/refresh token, OAuth code/provider token column이 없다.
- verification secret은 challenge에는 SHA-256 hash만, delivery material에는 TTL-bound AES-256-GCM ciphertext/nonce/key version만 있다. outbox에는 둘 다 없다.
- public idempotency에는 secret-bearing canonical body의 plain SHA-256이 없고 keyed request HMAC만 있다.
- raw recipient/single-use link는 DB/outbox/cache/log/trace/test artifact에 없다. worker memory와 TLS provider request wire만 전달 목적의 일시적 예외다.

## 4. Access JWT logout 의미 — DN-C4A-001

- access JWT는 RS256 allowlist와 required header `alg,kid,typ`, exact versioned issuer/audience, required claim
  `iss,aud,sub,sid,jti,iat,nbf,exp`, 30초 leeway, `exp<=issuedAt+10m`를 가진다. sub/sid/jti는 UUID이고 PII가 없다.
- resource server는 unknown/missing kid, disallowed alg, bad iss/aud/time/claim을 401로 fail-closed하며 current+previous
  public key의 최대 48시간 overlap registry가 missing/stale/ambiguous하면 non-test readiness를 실패한다.
- v1 후보는 C1 bearer consumer에 매 요청 session introspection/denylist를 추가하지 않는다. 따라서 logout은
  refresh family를 즉시 revoke하지만 이미 탈취된 access JWT를 암호학적으로 회수하지 못하고 최대 10분
  `exp`까지 유효할 수 있다.
- AUTH_REFRESH_TOKEN lineage hash/metadata는 family active/absolute lifetime과 종료 후 30일 보안 보존 기간만
  유지한 뒤 AUTH_SESSION과 같은 cleanup으로 삭제한다. "영구 보존"을 요구하지 않는다.
- React는 logout 204에서 memory access token과 protected query cache를 즉시 지운다.
- 즉시 access revoke가 제품 요구가 되면 C1 포함 모든 resource server의 online session check/denylist를
  별도 migration한 뒤 의미를 바꾼다. 현재 문서에서 즉시 revoke라고 주장하지 않는다.

## 5. Social identity·link transaction 예약 — DN-C4A-005

위 두 entity는 **예약 schema이며 migration 대상이 아니다**. GOOGLE/KAKAO/NAVER capability는 계속
`DISABLED`이고 public OAuth path/UI는 없다. 향후 명시적 linking 승인 시 다음 불변식을 동시에 적용한다.

- identity unique: `(provider, issuer_normalized, subject_hmac)`; email claim은 key/자동 병합에 사용하지 않음
- transaction은 ACTIVE user + current session + provider + server allowlisted issuer/token endpoint + exact
  redirect URI에 결합
- state/nonce는 hash-only, PKCE S256 verifier는 10분 TTL encrypted transient value이고 terminal 즉시 삭제
- callback provider/issuer가 transaction과 다르면 mix-up으로 실패하며 callback 입력이 token endpoint를
  선택하지 못함
- provider exchange는 DB transaction 밖에서 수행하고 검증된 issuer+subject 결과와 terminal link
  transaction+SOCIAL_IDENTITY insert만 한 DB transaction으로 commit
- unique conflict는 어느 account와 연결됐는지 숨기는 409이며 partial identity가 없음
