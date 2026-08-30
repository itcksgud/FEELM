# C4·C5 local-only MVP 잔여 Slice 감사

> 감사일: 2026-08-30  
> 판정: `CONDITIONAL LOCAL GO`  
> 권위: 현재 사용자는 **외부 자격증명 없이 localhost에서 검증하는 구현**만 허용했다.  
> 비권위: production activation, 배포, 실제 이메일 발송, OAuth 연결, 외부 공개 URL,
> 운영 object storage, 민감정보 공개는 승인되지 않았다.

## 1. 판정과 현재 연결 상태

현재 main OpenAPI와 runtime에는 C0·C1·C2B만 연결되어 있다. `docs/api/openapi.yaml`에는 C4/C5
operation이 없고, backend에는 C4/C5 package·controller·migration이 없으며 frontend에도 C4/C5 API client,
route, 제품 화면이 없다. 따라서 아래는 “이미 구현됨” 판정이 아니라 **계약 승격부터 시작할 수 있는 local-only
구현 범위**다.

재사용 가능한 현재 자산은 다음과 같다.

| 자산 | C4 연결 | C5 연결 |
| --- | --- | --- |
| C0 Catalog·OTT provider·UI_READY movie | 온보딩 실제 영화와 KR OTT set | 리포트 영화 metadata, 공개 목록 |
| C1 ViewingRecord·Rating·Frame·Popcorn·Taste aggregate | onboarding preference와 Rating을 분리하는 불변식 | 반기 factual report와 전체 Film/Popcorn의 유일 원천 |
| C1 required-auth fake actor | 테스트 fixture로만 유지; C4 session/JWT actor로 대체할 경계 | C4 local actor가 준비된 뒤 owner authorization에 사용 |
| C2B local popularity baseline | 직접 의존하지 않음 | 만족·예상 별점·취향 진단 지표로 사용 금지 |

기존 C4 문서는 `0/5`, C5 문서는 `0/6`이고 구현 권위가 없다고 적혀 있다. 현재 지시는 이 문서에 적힌
**보수적 local profile**에 한해서만 별도 권위를 준다. 기존 문서의 production 권위나 제품 결정을 암묵적으로
바꾸지 않는다. 실제 구현 전에는 별도 변경에서 C4/C5 계약, main OpenAPI, trace와 task 상태를 이 범위에 맞게
승격해야 한다.

## 2. 공통 local fake 경계

- 외부에서 접근 가능한 listener를 만들지 않는다. frontend `http://localhost:5173`, API
  `http://localhost:8080`, Mailpit SMTP/UI `127.0.0.1:1025/8025`만 허용한다.
- Mailpit은 no-auth capture adapter다. relay·forward·실제 수신자 전달을 끄고 local UI에만 표시한다.
- JWT signing, delivery encryption, identity/request HMAC key는 local profile에서 매 실행 생성하거나
  `.gitignore`된 local secret file로 주입한다. 저장소·fixture·로그에 raw key/token/password를 넣지 않는다.
- production profile은 mail/OAuth/signing/storage credential과 exact HTTPS origin이 없으면 readiness를
  fail-closed한다. local adapter로 production 성공을 가장하지 않는다.
- OAuth provider capability는 GOOGLE/KAKAO/NAVER 모두 `DISABLED`; start/callback/link route와 버튼은 없다.
- C5 export는 local encrypted/temp filesystem에서만 24시간 이내 삭제한다. 외부 object storage 호출은 없다.
- C5 share는 loopback에서만 작동하고 localhost 밖 base URL을 만들지 않는다. raw token은 fragment→body
  교환 전용이며 DB/log/trace/metric에는 hash만 둔다.
- email, nickname, internal user ID, raw report/share/auth token을 로그 label, URL path/query, filename,
  analytics에 넣지 않는다.
- 모든 local-only 기능은 한 개의 kill switch로 비활성화할 수 있어야 하며 production 기본값은 `OFF`다.

## 3. C4 회원·온보딩

### 3.1 Local 구현 허용 operation

| 묶음 | operationId | local 의미 |
| --- | --- | --- |
| 가입 | `createEmailSignup` | email/password/nickname으로 PENDING flow 생성; generic actual/decoy 202 |
| 이메일 확인 | `verifySignupEmail`, `resendSignupEmailVerification` | Mailpit fragment secret을 POST body로 확인; stable flow+versioned challenge |
| 세션 | `loginWithEmail`, `refreshAuthentication`, `logoutCurrentSession` | local cookie pair, memory access JWT, rotation lineage와 current-session logout |
| 회원 | `getMyMembership`, `updateMyNickname` | masked email, nickname, revision 기반 변경 |
| 온보딩 | `listOnboardingMovies`, `replaceOnboardingPreferences`, `completeOnboarding` | UI_READY 실제 영화, LIKE/DISLIKE full replace, 0개 skip 또는 1~10개 submit |
| OTT | `getMyOttSubscriptions`, `replaceMyOttSubscriptions` | KR provider의 NOT_CONFIGURED/CONFIGURED/SKIPPED 구분 |

`restartOnboarding`은 기존 fragment에서 명시적으로 `BLOCKED`이므로 제외한다. Social OAuth operation은
만들지 않는다. password reset/change, account delete도 C4A 현 범위에 없으므로 제외한다.

### 3.2 Entity

Local migration에 허용되는 entity는 아래로 제한한다.

- membership/profile: `USER_ACCOUNT`, `EMAIL_CREDENTIAL`, `USER_PROFILE`
- public verification: `EMAIL_SIGNUP_PUBLIC_FLOW`, `EMAIL_VERIFICATION_CHALLENGE`,
  `VERIFICATION_DELIVERY_MATERIAL`, `PENDING_SIGNUP_RECOVERY_ATTEMPT`, `DOMAIN_OUTBOX`
- anonymous idempotency: `PUBLIC_AUTH_IDEMPOTENCY_RECORD`, `PUBLIC_AUTH_IDEMPOTENCY_SCOPE_ALIAS`,
  `PUBLIC_AUTH_IDEMPOTENCY_REQUEST_HMAC_ALIAS`
- authenticated session: `AUTH_SESSION`, `AUTH_REFRESH_TOKEN`, 기존 actor-scoped `IDEMPOTENCY_RECORD`
- onboarding: `ONBOARDING_JOURNEY`, `ONBOARDING_PREFERENCE`
- OTT: `OTT_SUBSCRIPTION_SET`, `USER_OTT_SUBSCRIPTION`; provider 원천은 C0 `OTT_PROVIDER`

`SOCIAL_IDENTITY`, `SOCIAL_LINK_TRANSACTION`은 runtime table/row를 만들지 않는다. schema 예약이 필요해도
별도 OAuth 승인 전 migration하지 않는다.

### 3.3 Screen

`SCR-C4A-001` email signup, `002` email verification, `003` email login, `004` LIKE/DISLIKE onboarding,
`005` KR OTT set, `006` onboarding completion, `007` membership·nickname·logout을 구현할 수 있다.
Social button, password recovery/change/delete entry는 렌더링하지 않는다. 개발 전용 Mailpit 열기 링크는
local build에서만 제공하고 production bundle에는 포함하지 않는다.

### 3.4 Acceptance 범위

- local positive/security target: `AC-C4A-001..033`, `AC-C4A-035..044`, `AC-C4A-046..066`,
  `AC-C4A-068..085`
- 계속 차단: `AC-C4A-034` restart, `AC-C4A-045` social route/UI, `AC-C4A-067` OAuth link transaction
- `AC-C4A-007`, `061`, `073`, `081`, `082`의 production branch는 **기능 활성화 테스트가 아니라**
  credential/origin/key가 없을 때 production readiness가 실패하는 negative test로만 실행한다.
- 필수 E2E는 signup→Mailpit fragment 제거→verify→login→LIKE/DISLIKE 또는 skip→OTT set→logout이며
  raw secret artifact가 0건이어야 한다.

### 3.5 구현 순서

1. local-only decision record와 main OpenAPI/trace/task 승격; social/restart/production은 차단 상태 유지
2. DB constraint, Argon2 floor, public/actor idempotency, rate-limit Redis, safe outbox 기반
3. Mailpit capture와 encrypted delivery material의 crash/retry/삭제 구현
4. signup/verify/resend와 실제·decoy oracle/race/expired recovery 테스트
5. login/JWT/resource-server/refresh/logout/cookie·Origin·CSRF 구현과 C1 actor 호환
6. membership/nickname, onboarding, OTT set backend와 C1 Rating 분리 검증
7. 7개 React screen, secret-memory boundary, accessibility, local E2E
8. production profile fail-closed·secret scan·concurrency/rollback 감사

### 3.6 절대 필요한 사용자·외부 입력

**현재 local MVP를 시작하는 데 추가 API key나 실제 이메일 계정은 필요 없다.** repo 기본 local origin과
Mailpit을 쓸 수 있다. 포트가 충돌할 때만 사용자가 대체 loopback port를 정하면 된다.

다음은 local 범위를 넘을 때까지 요청하지도 사용하지도 않는다.

- 실제 메일: sender domain 검증, SMTP/provider credential, bounce/complaint·suppression 정책
- OAuth: provider별 client ID/secret, exact redirect URI, issuer/endpoints, 운영 link/unlink 정책
- 배포: HTTPS public origin, reverse-proxy trusted hop, KMS/JWT key registry와 rotation, secret store
- 제품 승격: access/refresh TTL·cookie profile·nickname 정책·verification/rate 수치·onboarding K/rerun에 대한
  명시적 production 승인

## 4. C5 리포트·프로필

### 4.1 Local 구현 권위와 단계

C5는 아직 OpenAPI/ERD/AC가 전혀 없으므로 바로 코드부터 만들 수 없다. 현재 지시는 아래 planning ID와
보수적 packet token을 **local contract로 먼저 작성할 권위**만 준다.

- local 채택 후보: `C5_REPORT=CALENDAR_HALF_KST_IMMUTABLE_REVISION_V1`,
  `C5_EXPORT=ACCESSIBLE_PDF_ASYNC_24H_V1`, `C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1`,
  `C5_SHARE=IMMUTABLE_REPORT_FRAGMENT_EXCHANGE_1CALMONTH_V1`,
  `C5_NOTIFICATION=IN_APP_PROVIDERLESS_OPT_IN_V1`
- 계속 DEFER: `C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED`
- 항상 disabled: taste compare, satisfaction, taste diagnosis, expected star, external notification

### 4.2 Planning operation

아래 이름은 이 감사의 local 계약 후보이며 아직 main OpenAPI operation이 아니다.

| 묶음 | operationId | local endpoint 의미 |
| --- | --- | --- |
| report | `listMyTasteReports`, `getMyTasteReport`, `createMyTasteReportRevision` | owner 반기 목록·상세·immutable 재생성 |
| export | `createMyTasteReportExport`, `getMyTasteReportExport`, `downloadMyTasteReportExport` | async accessible PDF, local artifact 24h |
| privacy | `getMyPrivacySettings`, `replaceMyPrivacySettings` | PRIVATE 기본, resource별 explicit opt-in |
| public read | `getPublicUserProfile`, `listPublicUserFilmFrames`, `listPublicUserPopcorns` | capability target의 허용 resource 전체 stable pagination |
| share owner | `createMyTasteReportShare`, `revokeMyTasteReportShare` | raw-once/hash-only local grant |
| share viewer | `exchangeTasteReportShare`, `getSharedTasteReport` | fragment secret 교환 뒤 15분 report-only viewer session |
| notification | `getMyNotificationSettings`, `replaceMyNotificationSettings`, `listMyNotifications`, `updateMyNotificationState` | in-app WATCH_CONFIRMATION_DUE, default OFF |

public read/share는 loopback local E2E에서만 활성화한다. taste comparison operation, external email/push,
password/account lifecycle operation은 만들지 않는다.

### 4.3 Planning entity

- report: `TASTE_REPORT_REVISION`, `TASTE_REPORT_PERIOD_ITEM`, `REPORT_SOURCE_SNAPSHOT`
- export: `REPORT_EXPORT_JOB`, `REPORT_EXPORT_ARTIFACT`
- privacy: `USER_PRIVACY_SETTING`
- share: `REPORT_SHARE_GRANT`, `REPORT_SHARE_VIEWER_SESSION`
- notification: `USER_NOTIFICATION_SETTING`, `IN_APP_NOTIFICATION`, `NOTIFICATION_SOURCE_INBOX`
- 재사용만: C4 `USER_ACCOUNT`/`USER_PROFILE`, C1 `VIEWING_RECORD`/`RATING`/`FRAME`/`POPCORN`/
  `TASTE_AGGREGATE`, C0 movie identity

C5가 두 번째 Rating, Film, Popcorn, user credential/session 원천을 만들면 안 된다.

### 4.4 Planning screen

| ID | 화면 |
| --- | --- |
| `SCR-LOCAL-C5-001` | 반기 report 목록·EMPTY 상태 |
| `SCR-LOCAL-C5-002` | factual metric과 실제 period 영화 전체 report 상세 |
| `SCR-LOCAL-C5-003` | PDF job 상태·owner download |
| `SCR-LOCAL-C5-004` | resource별 privacy 설정; PRIVATE 기본 |
| `SCR-LOCAL-C5-005` | 허용된 공개 nickname·전체 Film·전체 Popcorn |
| `SCR-LOCAL-C5-006` | owner share 생성·raw-once copy·revoke |
| `SCR-LOCAL-C5-007` | fragment 제거 뒤 report-only shared viewer |
| `SCR-LOCAL-C5-008` | in-app 알림 opt-in·목록·read/dismiss |

모든 화면에는 local-only 표식을 두되 raw email/token/internal ID는 표시하지 않는다. taste compare, “취향 향상”,
추천 만족, 예상 별점 문구는 없다.

### 4.5 Planning AC

이 ID는 기존 C5 Acceptance가 없어서 만든 planning ID다. 별도 C5 계약에 옮기기 전 PASS를 주장하지 않는다.

| AC | 필수 local 검증 |
| --- | --- |
| `AC-LOCAL-C5-001..003` | C4 ACTIVE owner/ownership, Asia/Seoul calendar half+72h, 0건은 EMPTY_NO_ACTIVITY |
| `AC-LOCAL-C5-004..008` | factual allowlist만, 전체 periodItems stable pagination, immutable revision/provenance, Rating 삭제 비복원, list/detail 일관성 |
| `AC-LOCAL-C5-009..012` | owner-only async PDF, text/accessibility+전체 목록, local 24h cleanup, filename/log PII 0건 |
| `AC-LOCAL-C5-013..017` | resource별 PRIVATE 기본, 비공개/없는 target 동일 결과, 전체 Film/Popcorn 불변식, revoke, taste compare 미구현 |
| `AC-LOCAL-C5-018..022` | raw-once/hash-only grant, fragment 선제 제거, no-store/no-referrer, 1개월 expiry/revoke, report-only viewer·oracle 방지 |
| `AC-LOCAL-C5-023..026` | notification OFF 기본·in-app만, source revision dedupe, WatchIntent 해결 시 expiry, 30d/terminal 7d retention |
| `AC-LOCAL-C5-027..030` | external adapter 호출 0건, account lifecycle route 0건, secret scan, loopback/kill-switch/production fail-closed |

### 4.6 구현 순서

1. C4 local actor/session E2E 완료
2. C5 5개 local token과 lifecycle DEFER를 decision record에 분리 기록
3. C5 scope/rules/state/OpenAPI/ERD/AC/trace/task를 동시에 작성하고 독립 privacy/security 감사
4. report revision/source snapshot과 owner list/detail
5. privacy PRIVATE 기본과 in-app providerless notification
6. local PDF job/temp artifact와 cleanup
7. loopback share fragment exchange·viewer session·revoke/expiry
8. 8개 React screen과 cross-owner/oracle/retention E2E
9. production flag가 OFF이고 외부 network/credential 참조가 0건인지 최종 감사

### 4.7 절대 필요한 사용자·외부 입력

**현재 local C5 계약·구현에는 외부 API key가 필요 없다.** C4 local actor와 기존 C0/C1 fixture가 선행
입력이다. PDF는 bundled/local font와 temp storage, 알림은 DB projection, share는 loopback만 사용한다.

다음 입력은 production/외부 공개 전까지 필요하지 않으며 현재 권위로 수집·사용하지 않는다.

- 외부 share: public HTTPS base URL, CDN/reverse-proxy log redaction, abuse IP trust boundary
- export: 운영 object storage credential, encryption/KMS, renderer sandbox/font license·patch 운영
- notification: email/push/SMS provider credential, consent·bounce·delivery 정책
- privacy/public profile: 실제 공개 정책과 discovery/block/moderation 결정
- account lifecycle: C4 recovery/change/delete, recent reauth, revoke, grace/retention 법적 결정
- 제품 승격: C5 `DN-C5-001..005`의 production 승인과 `DN-C5-006` 재개 결정

## 5. 최종 Gate

Local MVP 완료는 다음을 모두 만족할 때만 주장한다.

1. C4/C5 operation이 main OpenAPI와 generated client에 들어가기 전에 local-only authority와 production
   `OFF`가 machine-readable하게 검증된다.
2. production profile은 mail/OAuth/storage/public origin credential 부재 시 startup 또는 해당 capability를
   fail-closed한다.
3. secret scan, cross-owner, idempotency/race, rollback, expiry/cleanup, browser fragment 제거 E2E가 통과한다.
4. 실제 메일/OAuth/외부 share/deploy 호출 증거가 0건이다.
5. C5 report는 사실 집계와 실제 전체 목록만 제공하고 개인화/XAI/예상 별점/만족/취향 진단을 계산하지 않는다.

이 Gate를 통과해도 판정은 `LOCAL MVP`이며 production readiness나 사용자 데이터 공개 승인이 아니다.
