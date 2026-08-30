# C4A Email Membership·Onboarding 제품 범위

> 상태: `APPROVED_LOCAL_PROFILE_WITH_BLOCKED_PRODUCTION_EXTENSIONS` — 13개 local operation만 구현 허용

## 1. 목표

```text
email signup → email verification → login → protected membership
→ LIKE/DISLIKE onboarding 또는 skip
→ KR OTT subscription set 저장
→ onboarding 완료 상태
```

이 slice는 React 회원 흐름과 최초 취향 입력을 정의한다. OnboardingPreference는 C1 Rating과 다른
source of truth이며, 입력이 있다고 추천 순위가 개선됐다고 주장하지 않는다.

## 2. Actor

| Actor | 범위 |
| --- | --- |
| 방문자 | 이메일 signup·verify·login; Catalog 공개 화면 유지 |
| pending member | 이메일 verification/resend만 가능; protected 개인 API 불가 |
| active member | 본인 profile·onboarding·OTT subscription·logout |
| local mail adapter | credential 없이 Mailpit로 개발 메일 전달 |
| production mail adapter | provider credential 승인·주입 전 `BLOCKED` |
| social provider adapter | GOOGLE/KAKAO/NAVER 범위이나 DN-C4A-005와 credential 전 `BLOCKED` |

## 3. 포함 범위

| Capability | 근거 | C4A 초안 결과 |
| --- | --- | --- |
| 이메일 가입·인증 | FR-01, 사용자 지시 | stable actual/decoy public flow, versioned hash-only challenge, encrypted single-use post-commit delivery material, expired-flow recovery/30d purge, verification activation |
| 이메일 login/logout | FR-01 | 승인된 인증 전달 방식으로 protected actor 생성·session 폐기 |
| nickname·내 정보 | C-07/D-14 | 가입 nickname, 내 profile 조회·변경 후보 |
| 영화 온보딩 | FR-02, D-04/Q-04 | C0 UI_READY movie, LIKE/DISLIKE full-set preference, skip |
| OTT 설정 | FR-03, D-10/Q-10 | C0 KR provider를 참조하는 내 subscription set; 미구독 영화 제외 금지 |
| 추천 경계 | REC-EV-003B/011 | K10 별점 data-only와 full-catalog alpha 0.2의 작은 offline ranking 후보를 UX 결정과 분리; champion 주장 금지 |
| 로컬 이메일 | secrets rule, 사용자 지시 | Mailpit keyless adapter, 운영 provider는 별도 Gate |
| 소셜 범위 예약 | D-07/Q-07 | provider enum·task만 유지; public API/UI/adapter activation 차단 |

## 4. 명시적 제외

- 이 문서 작업에서 main OpenAPI·backend·frontend·migration·Compose를 함께 변경하는 것
- password reset/change, account deletion, notification·privacy 설정
- production SMTP/email API 연결과 credential 발급·저장
- Google/Kakao/Naver OAuth callback·exchange·계정 연결 구현
- 온보딩 LIKE/DISLIKE를 C1 Rating, Frame, Popcorn, Rating aggregate로 저장
- K 입력만으로 예상 별점 숫자·HIGH confidence 또는 개인 추천 순위 개선을 표시
- MovieLens user와 service user의 identity 연결
- 구독하지 않은 OTT 영화의 추천 후보 제외
- social/email 계정 복구·병합의 암묵적 처리

## 5. 화면

| ID | 역할 | local profile 권위 |
| --- | --- | --- |
| `SCR-C4A-001` | 이메일 회원가입 | DN-C4A-002/003 |
| `SCR-C4A-002` | 이메일 인증·재전송 | DN-C4A-003 |
| `SCR-C4A-003` | 이메일 로그인 | DN-C4A-001/003 |
| `SCR-C4A-004` | 영화 LIKE/DISLIKE 입력·skip | DN-C4A-004 |
| `SCR-C4A-005` | KR OTT 구독 set | 없음; protected auth는 DN-C4A-001 |
| `SCR-C4A-006` | 완료/skip 결과와 다음 경로 | DN-C4A-004 |
| `SCR-C4A-007` | 내 nickname·logout | DN-C4A-001/002 |

Social 버튼은 local profile에 포함되지 않는다. provider capability가 `AVAILABLE`인 별도 계약이
승인되기 전 disabled teaser도 실제 연결처럼 노출하지 않는다.

## 6. 핵심 시나리오

| ID | 시나리오 |
| --- | --- |
| `SCN-C4A-001` | 방문자 signup → Mailpit/adapter 전달 → verify → ACTIVE |
| `SCN-C4A-002` | ACTIVE email login → protected `/me` → current-session logout |
| `SCN-C4A-003` | onboarding movies → LIKE/DISLIKE 저장 → 승인 minimum 충족 → complete |
| `SCN-C4A-004` | onboarding에서 즉시 skip → fallback 가능 상태 → Catalog 진입 |
| `SCN-C4A-005` | KR provider 목록 → empty/non-empty subscription set replace |
| `SCN-C4A-006` | duplicate/retry/race·mail/DB crash에서 raw secret/partial state 없음; public-auth 전용 keyed-HMAC idempotency, persisted recovery attempt, aggregate rotated-key quota, delivery material key/deletion, 24h expired-flow recovery, 30d purge single-winner, exact-Origin cookie-less logout 204 clear |
| `SCN-C4A-007` | social capability DISABLED → public social API/UI 없음 |

## 7. 품질 Gate

| Gate | 기대 |
| --- | --- |
| 결정 권위 | 보수 token 5/5는 13개 operation·7개 screen의 local 구현에만 권위를 주며 production/main 승격 권위는 주지 않음 |
| 비밀 | password·verification secret·refresh/access token raw 저장·로그 0건; provider 전달의 recipient/link는 worker memory·TLS wire에만 일시 허용 |
| 인증 | protected actor는 승인된 transport의 검증된 subject에서만 결정 |
| 소유권 | `/me`, onboarding, subscription은 actor 본인만 접근 |
| 분리 | OnboardingPreference row와 C1 Rating/Frame/Popcorn 생성 연결 0건 |
| 멱등성 | signup·verify/resend는 C1 actor ledger와 분리된 physical public ledger+keyed request HMAC, 나머지 mutation은 actor ledger로 retry 중복 효과 없음 |
| 원자성 | 상태·idempotency·audit/outbox 중 하나 실패 시 domain 변경 rollback |
| 장애 | mail 실패는 raw secret 노출 없이 resend 가능한 명시 상태; DB 실패는 partial account 없음 |
| 추천 정직성 | REC-EV-011 K10 alpha 0.2·작은 NDCG CI를 champion·expected-star·HIGH confidence 주장으로 바꾸지 않음 |

## 8. local 구현 완료와 별도 production 승격 조건

- local 구현은 DN-C4A-001~005의 채택 token과 13개 operation만 사용
- fragment에서 local transport/TTL/constraints와 차단 extension을 분리
- ERD unique/check/index와 API error가 decision과 일치
- 모든 trace row가 operation/entity/AC/task/test를 연결
- local Mailpit test는 credential 없이 통과, production adapter test는 secret fixture 없이 contract-test 가능
- C1 bearer 호환 또는 승인된 migration 완료
- blind handoff와 보안 검토는 local 완료 증거일 뿐 common registry/main OpenAPI 병합은 별도 승인
