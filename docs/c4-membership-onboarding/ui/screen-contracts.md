# C4A React Screen Contract

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS`  
> 공통: React, keyboard/screen-reader 접근성, 320px 이상 responsive, secret persistence 금지

## 공통 상태

| 상태 | 표시·행동 |
| --- | --- |
| loading | `aria-busy=true`, 중복 submit 방지, 기존 non-secret 입력 유지 |
| validation 400 | field와 연결된 message, 첫 오류 focus, password/secret value echo 금지 |
| unauthorized 401 | 안전한 login 이동; access/refresh 상태 정리; email/password/verification input 저장 금지 |
| forbidden 403 | pending verification 또는 Origin/CSRF 실패의 안정 safe code 기반 다음 행동; session/account 존재 상세 과노출 금지 |
| conflict 409 | 최신 revision/state 재조회 후 사용자가 재시도; 무조건 자동 overwrite 금지 |
| rate limit 429 | Retry-After 기반 재시도 가능 시각; countdown이 유일한 접근 수단이 아니어야 함 |
| unavailable 503 | 상태 저장 성공 여부를 명확히 하고 retry; mail 실패를 account rollback처럼 표시하지 않음 |
| offline/retry | mutation key는 같은 의도 재시도에 유지, body가 바뀌면 새 key |

## `SCR-C4A-001` — Email signup

- Gate: `DN-C4A-002`, `DN-C4A-003`
- Operation: `createEmailSignup`
- email, password, nickname label/input과 회원가입 submit을 제공한다.
- 승인된 nickname/password constraint만 도움말로 표시하며 proposed 수치를 확정 문구로 하드코딩하지 않는다.
- submit 성공 시 password를 즉시 메모리에서 버리고 stable `signupId`, masked email만 verification 화면의
  memory/navigation state에 넘긴다. `signupId`는 EMAIL_SIGNUP_PUBLIC_FLOW handle이며 challenge ID가 아니다.
  신규 또는 prior REAL flow가 EXPIRED인 PENDING same-password recovery면 actual, 만료 전/duplicate/unknown/password mismatch면 persisted decoy UUID라서
  UI가 어느 쪽인지 구분하지 않는다. URL/storage/analytics에는 넣지 않는다.
- duplicate email은 같은 202·masked email·`QUEUED`·expiry/resend 형태를 사용한다. nickname conflict처럼
  별도 public constraint가 승인된 경우에만 generic 409를 사용하되 충돌 주체를 노출하지 않는다.
- social 버튼은 provider capability가 DISABLED인 동안 렌더링하지 않는다.
- 접근성: label, password manager autocomplete (`new-password`), error `aria-describedby`.

## `SCR-C4A-002` — Email verification

- Gate: `DN-C4A-003`
- Operations: `verifySignupEmail`, `resendSignupEmailVerification`
- masked email, verification input, verify, resend 가능 시각을 표시한다.
- email 링크는 raw secret을 HTTPS **fragment**에만 둔다. 화면 entry script는 첫 network/third-party
  resource보다 먼저 fragment를 component memory로 읽고 `history.replaceState`로 제거한다.
- `Referrer-Policy: no-referrer`를 적용하고 raw secret을 query/path, analytics, local/session storage,
  navigation state, console/error telemetry에 넣지 않는다. verify POST body 외 전송을 금지한다.
- resend 성공은 새 challenge 때문에 이전 email secret이 무효일 수 있음을 안내한다.
- resend 뒤에도 signupId는 바뀌지 않는다. response revision/expiry/cooldown만 교체하며 internal current
  challenge ID/version을 받거나 저장하지 않는다.
- verify 성공은 token을 자동 발급하지 않고 email login 화면으로 이동한다.
- actual/decoy 공통 `VERIFICATION_INVALID`, `SIGNUP_FLOW_INVALID_OR_EXPIRED`,
  `VERIFICATION_ATTEMPTS_EXHAUSTED`, `AUTH_FLOW_THROTTLED`, `AUTH_DEPENDENCY_UNAVAILABLE` code를 사용한다.
  24시간 flow 만료는 signup으로 보내고 30일 purge 전 same-password re-signup recovery를 generic copy로만 안내한다.
- local Mailpit 링크는 development build에서만 보일 수 있고 production UI에는 없다.

## `SCR-C4A-003` — Email login

- Gate: `DN-C4A-001`, `DN-C4A-003`
- Operation: `loginWithEmail`
- email/password와 login submit을 제공한다. raw password는 component state 밖에 저장하지 않는다.
- invalid credential은 email 존재/비밀번호 중 무엇이 틀렸는지 구분하지 않는다.
- pending verification 응답 UX와 resend 진입은 DN-C4A-003 승인 문구를 따른다.
- 성공 시 access JWT는 React memory에만 둔다. production HTTPS는 `__Host-feelm_refresh`/`__Host-feelm_csrf`,
  loopback local HTTP는 `feelm_local_refresh`/`feelm_local_csrf`를 함께 받고 refresh 때 함께 회전한다.
  profile 이름을 섞거나 local 이름을 production에서 허용하지 않는다.
- social 버튼·redirect는 DN-C4A-005 전 없다.

## `SCR-C4A-004` — Movie LIKE/DISLIKE onboarding

- Gate: `DN-C4A-004`; protected auth는 `DN-C4A-001`
- Operations: `listOnboardingMovies`, `replaceOnboardingPreferences`, `completeOnboarding`
- `restartOnboarding`과 재수행 CTA는 local profile에서 `BLOCKED`다.
- 실제 영화 poster/title 목록을 표시하고 각 영화에 명시적 `좋아요`, `싫어요`, `미선택` 상태를 제공한다.
- 목업의 원형 취향 공간을 canonical 시각 입력으로 사용한다. 포스터 중심과 취향 공간 중심 사이의 거리가
  원의 반지름 이하이면 `LIKE`, 반지름보다 크면 `DISLIKE`다. 고정 pixel 수치가 아니라 실제 렌더링된
  원의 중심·반지름으로 판정하므로 responsive 크기에서도 의미가 같다.
- 아직 배치하지 않았거나 ⊖로 공간에서 뺀 영화는 `미선택`이며 preference row를 만들지 않는다.
  원 경계 위는 `LIKE`로 판정한다. API에는 거리나 좌표를 보내지 않고 최종 `LIKE/DISLIKE`만 보낸다.
- drag는 touch/pointer 입력을 지원하며, 같은 영화를 `좋아요`, `싫어요`, `미선택`으로 배치하는 명시 버튼을
  keyboard·screen reader 대안으로 함께 제공한다. 색만으로 상태를 구분하지 않는다.
- progress는 `현재 선택 수 / 10`으로 표시한다. K5/K10은 local에서도
  “성능이 보장되는 수”라고 설명하지 않는다.
- skip은 항상 별도 버튼과 확인 문구로 제공하며 미선택을 DISLIKE로 제출하지 않는다.
- empty: UI_READY 후보가 0개면 빈 상태·skip·retry를 제공하고 자동 완료/싫어요를 만들지 않는다.
- 409 minimum-not-met이면 server의 required count를 표시하고 현재 preferences를 유지한다.

## `SCR-C4A-005` — KR OTT subscription set

- Operation: existing C0 `listOttProviders`, C4A `getMyOttSubscriptions`, `replaceMyOttSubscriptions`
- provider logo/name과 multi-select, `구독 없음`(CONFIGURED empty), `나중에`(SKIPPED)를 구분한다.
- 한 개도 선택하지 않은 화면 상태만으로 CONFIGURED empty를 저장하지 않고 사용자의 명시 submit을 받는다.
- 미구독 provider 영화도 추천에서 제외되지 않는다는 설명을 과장 없이 표시할 수 있다.
- empty provider catalog는 200 empty·retry/skip이며 stale providerId 404/409 시 목록을 다시 읽는다.

## `SCR-C4A-006` — Onboarding completion

- Gate: `DN-C4A-004`
- Operation: `getMyMembership`
- COMPLETED와 SKIPPED를 다른 문구로 표시한다.
- recommendationProjection이 PENDING/FAILED여도 가입·입력 저장 성공을 실패로 되돌리지 않는다.
- READY를 “개인 추천 성능 향상” 또는 “예상 별점 정확”으로 표시하지 않는다.
- 다음 Catalog/home 경로와 나중에 설정 가능한 OTT/profile 진입을 제공한다.

## `SCR-C4A-007` — My membership·nickname·logout

- Gate: `DN-C4A-001`, `DN-C4A-002`
- Operations: `getMyMembership`, `updateMyNickname`, `logoutCurrentSession`
- masked email, nickname, onboarding 상태를 표시하고 nickname 변경·logout을 제공한다.
- stale nickname revision은 최신 profile을 다시 읽고 사용자 입력을 덮어쓰지 않는다.
- active-session logout은 bearer-only 호출이 아니다. browser가 current refresh/CSRF cookie를 보내고 client는 exact
  Origin 요청에서 CSRF cookie 값을 `X-CSRF-Token`으로 보내며, 403이면 session 상태를 추측하지 않고
  generic 재로그인 recovery를 제공한다.
- logout 성공 후 access memory와 protected cache/query를 즉시 제거하고 profile별 exact attributes의 두
  cookie clear를 기대한다. 두 auth cookie가 모두 없는 retry도 exact Origin은 필수이고 CSRF/idempotency 없이
  204 clear이며 DB/audit mutation이 없다. 하나만 남으면 403, valid CSRF+invalid session은 401이다. current-session만
  revoke되며, 이미 복제된 access JWT는 v1 후보에서 최대 10분 exp까지
  유효할 수 있다는 backend contract를 즉시 폐기라고 과장하지 않는다.
- password reset/change/delete와 social link/unlink는 C4A 화면에 넣지 않는다.

## Responsive·접근성 Gate

- 320/768/1280px에서 horizontal content loss 없이 동작한다.
- poster grid는 DOM/reading order와 시각 순서가 같고 선택 상태가 색만으로 표현되지 않는다.
- 모든 dialog는 focus trap, Escape/취소, focus return을 가진다.
- submit 결과는 `role=status`, field 오류는 `role=alert` 또는 연결된 live region으로 알린다.
- motion/drag는 `prefers-reduced-motion`과 button alternative를 제공한다.
