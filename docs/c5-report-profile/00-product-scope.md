# C5 제품 범위와 고정 경계

> 상태: `APPROVED_LOCAL_MVP_PROFILE`  
> 구현 권위: `LOCAL_MVP_ONLY`

## 1. 승인된 local 목표

C5는 다음 후순위 제품 흐름을 정규화한다.

- 사용자가 사실 기반 반기 리포트를 조회하고 local PDF를 다운로드
- 사용자가 명시적으로 만든 loopback 공유 capability로 비회원이 특정 report revision만 조회
- privacy 설정 뒤 정확한 capability target으로 공개 nickname·전체 Film·전체 Popcorn Bucket 조회
- provider 없는 in-app `WATCH_CONFIRMATION_DUE` 알림 설정·목록·상태 변경

취향 비교와 계정 복구·비밀번호 변경·회원 탈퇴는 local 범위에도 포함하지 않는다.

## 2. 요구사항으로 확정된 의미

| ID | 확정 의미 | 근거 |
| --- | --- | --- |
| `FIX-C5-001` | 취향 리포트 주기는 월간이 아니라 반기다. | FR-23, D-03, C-03, Q-03 |
| `FIX-C5-002` | 리포트는 감상 데이터와 취향 변화를 분석한다. | FR-23 |
| `FIX-C5-003` | 외부 공유 링크는 서버에 저장하며 앱 밖 비회원 사용자를 위한 공개 capability다. | D-11, Q-11 |
| `FIX-C5-004` | 공유 링크는 생성 후 1개월에 만료한다. | D-11, Q-11 |
| `FIX-C5-005` | 사용자는 리포트를 휴대폰에 다운로드할 수 있다. | D-11, Q-11 |
| `FIX-C5-006` | 공개 프로필·Film·Popcorn·취향 비교는 사용자 privacy 설정이 먼저 정의돼야 한다. | FR-25, D-12, Q-11 |
| `FIX-C5-007` | 프로필·알림 설정과 계정 복구·변경·탈퇴는 요구 범위에 포함한다. | C-07, D-14 |
| `FIX-C5-008` | Film은 C1 전체 active Frame 모음이고 Popcorn은 Frame과 1:1이다. 공개/리포트가 이 원천 의미를 바꾸지 않는다. | D-02, Q-02, C1 승인 계약 |

## 3. 고정 안전 경계

| ID | 구현 전에도 바꿀 수 없는 경계 |
| --- | --- |
| `SAFE-C5-001` | public share raw token은 최초 전달 capability이며 서버에는 hash만 저장한다. raw token을 DB·log·trace·metric·analytics에 저장하지 않는다. |
| `SAFE-C5-002` | share grant는 `expiresAt`과 명시적 revoke 상태를 가지며 만료·revoke 후 fail-closed다. URL 삭제만으로 revoke를 대신하지 않는다. |
| `SAFE-C5-003` | share token이나 공개 응답에 raw email, internal userId, provider subject, auth/session identifier를 넣지 않는다. 표시 nickname·report field도 privacy/share 결정 전 허용하지 않는다. |
| `SAFE-C5-004` | secret-bearing share entry/response는 shared cache에 저장하지 않고 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`를 적용한다. third-party resource로 token이 전달되지 않아야 한다. |
| `SAFE-C5-005` | public share 조회는 token possession만으로 account login·profile·Film·Popcorn 전체 권한을 얻지 않는다. grant에 승인된 report projection만 읽는다. |
| `SAFE-C5-006` | privacy가 허용하지 않은 profile/Film/Popcorn/taste comparison은 존재 여부를 과도하게 구분하지 않는 fail-closed 결과다. |
| `SAFE-C5-007` | 알림은 C1 WatchIntent·ViewingRecord나 C2B exposure/action을 복제 원천으로 만들지 않는다. 사건 없음·클릭 없음은 부정 선호가 아니다. |
| `SAFE-C5-008` | 계정 복구·변경·삭제는 C4 인증 경계가 승인되기 전 구현하지 않으며 비밀번호·복구 secret raw 저장·로그를 금지한다. |

share URL transport는 아직 결정되지 않았다. 어떤 방식을 선택하더라도 access/CDN/application log와
Referer에 raw token을 남겨서는 안 된다. 보수적 후보는 C4 email verification처럼 fragment에서 읽어
즉시 제거한 뒤 교환하는 방식이지만, 이것은 `DN-C5-004` 승인 전 API 계약이 아니다.

## 4. local MVP 명시적 제외

- 예상 별점·만족도·취향 진단·AI 요약·“취향이 좋아졌다” 같은 해석 문구
- 월간 report 또는 rolling 6개월을 반기로 부르는 동작
- 자동 재생성; owner가 명시적으로 immutable revision을 만들 때만 snapshot
- 외부 object storage·CDN·KMS·public renderer
- taste comparison과 검색/discovery 기반 공개 profile
- 대표 Frame 몇 개만 반환하고 Film 전체라고 표시하는 응답
- external notification channel, 마케팅 동의, push/email/SMS/webhook adapter
- password reset/change, account delete, 삭제 유예·복구·보존 정책
- C2B 예상 별점·개인화·발견 성과를 report metric으로 사용하는 일
- main OpenAPI·공통 ERD·React public route·backend/worker/Compose 변경

## 5. 결정 완료 후에도 지켜야 할 Source of Truth

- 리포트 입력은 source record를 복제해 수정하지 않고 snapshot provenance와 source version을 남긴다.
- Rating 삭제는 C1 승인 transaction 결과를 따르며 C5가 Frame·Popcorn을 되살리지 않는다.
- profile actor/nickname/auth는 승인된 C4 경계를 참조하고 별도 사용자 table을 만들지 않는다.
- 추천 관련 지표를 채택하려면 당시 승인된 C2B delivery/exposure/outcome semantics와 model/policy version을
  사용한다. offline evidence를 실제 사용자 만족으로 쓰지 않는다.
