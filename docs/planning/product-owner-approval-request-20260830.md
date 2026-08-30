# FEELM standalone 제품 권장안 일괄 승인 기록 — 2026-08-30

> 상태: `RECORDED_LOCAL_PRODUCT_APPROVAL`  
> 승인 근거: 사용자가 C2B~C5를 별개 개인 프로젝트의 localhost 기능으로 계속 구현하라고 명시한 지시  
> 효력: 아래 token과 machine-validated 계약 범위의 local-MVP 구현 권위. commit·push·MR·배포·운영 credential 주입은 포함하지 않는다.

이 문서는 더 이상 승인 요청이나 pending marker가 아니다. 사용자의 연속된 로컬 개발 지시를 제품 승인으로
기록하며, C2B·C3·C4·C5의 승인된 범위는 `APPROVED_LOCAL_MVP`다. 구현 파일과 자동화 검증 경로가
존재하더라도 revision 고정, clean-checkout 재현, 실제 Compose local-MVP E2E 성공은 별도 완료 Gate로 남는다.

## 승인된 권장안

### C2B 개인 추천·발견

| 결정 | 권장 token | 구현 의미 |
| --- | --- | --- |
| `DN-C2B-001` | `KEEP_PUBLIC_ALPHA0_SHADOW_K10` | 공개 순위는 popularity alpha 0, K10 alpha 0.2는 shadow evidence만 수집 |
| `DN-C2B-002` | `BASELINE_THREE_CUMULATIVE_LOAD_MORE_RATED_OR_EXPLICIT_DISMISS` | **2026-08-30 승인됨**: 최초 3편, 추가마다 최대 3편 누적, 평가 완료 또는 명시적 관심 없음이면 목록·향후 제외 |
| `DN-C2B-003` | `MAX_ONE_FAITHFUL_REASON` | 실제 rank contribution이 있을 때만 이유 최대 1개, copy 승인 전 숨김 |
| `DN-C2B-004` | `STAR_DISABLED_FAIL_CLOSED` | 예상 별점은 `NOT_COMPUTED`, 값·badge·0점 placeholder 없음 |
| `DN-C2B-005` | `EXACT_STAGE_ONLY_C1_EVENT_AMENDMENT` | 관측된 exact action chain만 기록, C1 click event 계약 보완 필수, 만족도·utility 추론 금지 |
| `DN-C2B-006` | `NO_STALE_VERSIONED_RETENTION_CANDIDATE` | C0 Catalog lock/version·C1 final-check 보완 필수, stale 성공 금지, production SLA 아님 |

### C3 Party·OTT 비교

| 결정 | 권장 token | 구현 의미 |
| --- | --- | --- |
| `DN-C3-001` | `CATALOG_POPULARITY_KR_FLATRATE_V1` | provider coverage와 C0 popularity만 쓰는 결정적 local baseline; Party public champion은 계속 disabled |
| `DN-C3-002` | `PARTY_CREATE_INVITE_ACCEPT_MAX4` | owner 생성, allowlist fake actor 초대·수락, owner 포함 최대 4명 |
| `DN-C3-003` | `LOOPBACK_ALLOWLIST_FAKE_ACTOR` | loopback의 exact fixture actor만 허용하고 body actor·실제 identity lookup 금지 |
| `DN-C3-004` | `KR_FLATRATE_COMPLETE_FIXTURE_FULL_LIST` | 같은 COMPLETE fixture의 KR 정액제 2~4개, overlap과 실제 영화 전체 stable cursor |
| `DN-C3-005` | `DEFERRED` | Rating·노출·상세·click·taste analysis와 임의 weighting은 local MVP 입력·응답에서 제외 |

### C4 회원·인증·온보딩

| 결정 | 권장 token | 구현 의미 |
| --- | --- | --- |
| `DN-C4A-001` | `BEARER_JWT_ROTATING_REFRESH_CURRENT_LOGOUT` | 10분 RS256 access JWT, 회전 opaque refresh cookie, 현재 session logout |
| `DN-C4A-002` | `MINIMAL_FIELDS_GLOBAL_NICKNAME` | email/password/nickname, password 15~128자, 정규화 nickname 전역 unique |
| `DN-C4A-003` | `VERIFY_REQUIRED_MAILPIT_LOCAL_PROD_DEFERRED` | email 인증 필수, local Mailpit만; 운영 발송 provider·credential은 보류 |
| `DN-C4A-004` | `OPTIONAL_UP_TO_10_WITH_SKIP` | 0개 skip 또는 1~10개 선택, K10은 권장일 뿐 강제 아님, 재수행은 새 revision의 versioned replace |
| `DN-C4A-005` | `KEEP_ALL_SOCIAL_DISABLED` | Google·Kakao·Naver route/button 없음, 자동 email merge 금지 |

### C5 리포트·프로필·공유

| 결정 | 권장 token | 구현 의미 |
| --- | --- | --- |
| `DN-C5-001` | `C5_REPORT=CALENDAR_HALF_KST_IMMUTABLE_REVISION_V1` | KST 반기 immutable revision, 사실 지표와 기간 내 실제 영화 전체 목록 |
| `DN-C5-002` | `C5_EXPORT=ACCESSIBLE_PDF_ASYNC_24H_V1` | 접근 가능한 server-rendered PDF, 비동기, artifact 24시간 |
| `DN-C5-003` | `C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1` | 기본 PRIVATE, profile/Film/Popcorn resource별 opt-in·전체 목록 pagination; taste compare는 opt-in 값만 저장하고 계산·화면·공유는 계속 disabled |
| `DN-C5-004` | `C5_SHARE=IMMUTABLE_REPORT_FRAGMENT_EXCHANGE_1CALMONTH_V1` | immutable report만 256-bit hash-only grant로 공유, calendar 1개월 |
| `DN-C5-005` | `C5_NOTIFICATION=IN_APP_PROVIDERLESS_OPT_IN_V1` | provider 없는 in-app 감상확인 알림만 opt-in, 기본 OFF |
| `DN-C5-006` | `C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED` | recovery/change/delete는 C4 구현·검증 뒤 별도 결정, 현재 구현 0건 |

## 함께 승인해야 하는 추천 교차 Gate

| 결정 | 권장 token | 연결 의미 |
| --- | --- | --- |
| `REC-PD-001` | `HIDE_NOT_COMPUTED` | `DN-C2B-004`와 동일하게 예상 별점 숫자를 숨김 |
| `REC-PD-003` | `OPTIONAL_UP_TO_10_WITH_SKIP` | `DN-C4A-004`와 같은 maximum/minimum/skip/versioned rerun 의미 |
| `REC-PD-005` | `KEEP_PARTY_PUBLIC_DISABLED` | `DN-C3-001`과 같이 Average는 offline 기준선, public Party 추천 champion은 null |
| `REC-PD-007` | `SHOW_MAX_ONE_FAITHFUL_REASON` | `DN-C2B-003`과 같이 실제 rank contribution reason만 최대 1개 |

각 제품 패킷에 적힌 허용 손실, rollback trigger, 재검토 evidence/조건, retention 값은 변경 없이 함께
승인했다. 필요한 C0/C1 amendment와 slice 계약은 machine validation을 통과했으며 승인된 local baseline은
구현 권위를 얻었다. blocked extension은 validator 통과 여부와 무관하게 계속 차단한다. `DN-C5-006`은 선택 자체가
명시적 `DEFER`이므로 계약·구현 권위가 생기지 않는다.

## C6 local experiment 승인 추기

> 상태: `RECORDED_LOCAL_EXPERIMENT_APPROVAL`  
> 근거: 사용자가 예상 별점·만족도 추론·취향 진단을 로컬에서 실험해 나중 판단 근거로 사용하자고 명시한 지시

C6에는 `APPROVED_LOCAL_EXPERIMENT`만 부여한다. `displayEligible=false`, 일반 navigation 미노출,
local/DEV 이중 flag, 자기보고 만족도가 아님을 드러내는 문구를 유지한 조건에서만 구현한다.
이 추기는 C2B의 `DN-C2B-004=STAR_DISABLED_FAIL_CLOSED`를 변경하지 않으며, 제품 노출은 C1
paired-scale·실사용자 이해도/만족도 evidence와 별도 승인 전까지 차단한다.

## 승인 후에도 유지되는 금지선

- MovieLens offline 개선을 실제 사용자 만족도 개선이라고 표현하지 않는다.
- 제품 예상 별점, 취향 진단, 추천 만족도는 근거가 생기기 전 `NOT_COMPUTED`다. C6 로컬 실험 결과는 예외적 판단 자료일 뿐 제품 값이 아니다.
- Party public recommendation은 champion evidence 전 구현하지 않는다.
- 운영 mail/OAuth key, 실제 sender domain, 배포 환경은 별도 승인이 필요하다.
- commit·push·MR·배포는 이 제품 승인과 별도다.
- production SLA·분산 scale-out 효과는 multi-host 실측 전 주장하지 않는다.

## 기록된 승인 문구

아래 문장은 사용자가 부여한 local 제품 승인 범위를 정규화한 기록이다.

```text
FEELM standalone C2B~C5 22개 권장안과 REC-PD-001·003·005·007 교차 Gate를 전체 승인한다. 패킷의 수치·허용 손실·rollback·재검토·retention을 그대로 적용하라. 계약 전체가 machine validation을 통과한 slice/task만 로컬 구현하고, C5-006은 DEFER하라. commit·push·MR·배포·운영 credential 주입은 별도 승인 전 금지한다.
```

이 승인 기록은 local 제품 의미만 고정한다. 새로운 production 의미나 blocked extension을 열려면
`DN-*` ID와 대체 token 또는 `DEFER` 해제를 명시한 별도 사용자 결정이 필요하다.
