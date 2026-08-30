# C5 제품 결정 패킷

> 상태: `APPROVED_LOCAL_MVP_PROFILE`  
> local 결정: `5/6`; account lifecycle `DEFERRED`  
> local 구현 권위: `YES`  
> production 결정·외부 활성화: `0/6`, `NO`

이 패킷의 보수적 token은 현재 사용자의 local-only 완성 지시에 따라 localhost 구현 profile로 선택됐다.
이는 production 배포, 외부 provider/public URL, account lifecycle 권위를 부여하지 않는다. 각 token의
production 수치·운영 정책은 계속 재승인이 필요하며 `DEFER`인 범위는 local에서도 제외한다.

모든 결정에 공통으로 다음 해석을 고정한다.

- `SATISFACTION=NOT_COMPUTED`: 클릭·평점·감상 건수를 추천 만족으로 해석하지 않는다.
- `TASTE_DIAGNOSIS=NOT_COMPUTED`: 장르·flavor 집계를 성격·취향의 우열이나 향상으로 진단하지 않는다.
- `EXPECTED_STAR=NOT_COMPUTED`: 예상 별점을 계산·표시·공유하지 않는다.
- C2B offline evidence는 실제 사용자 성과나 public champion의 근거가 아니다.
- 영화 목록은 허용된 범위의 실제 영화 **전체 목록**이다. 대표 영화나 상위 몇 편을 전체 Film이라고 부르지
  않고, 응답 크기는 stable pagination으로 해결한다.

## 한 번에 선택하는 권장값

| 결정 | 보수적 권장 token | 승인 시 의미 | DEFER 경계 |
| --- | --- | --- | --- |
| `DN-C5-001` | `C5_REPORT=CALENDAR_HALF_KST_IMMUTABLE_REVISION_V1` | 사실 집계와 period 전체 영화 목록만 가진 반기 snapshot | report projection·생성·조회 전체 제외 |
| `DN-C5-002` | `C5_EXPORT=ACCESSIBLE_PDF_ASYNC_24H_V1` | 승인 report revision의 접근 가능한 PDF를 24시간 보관 | export job·renderer·artifact storage 전체 제외 |
| `DN-C5-003` | `C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1` | resource별 PRIVATE 기본값과 명시적 public opt-in | public profile·Film·Popcorn·compare 전체 제외 |
| `DN-C5-004` | `C5_SHARE=IMMUTABLE_REPORT_FRAGMENT_EXCHANGE_1CALMONTH_V1` | 특정 report revision만 읽는 hash-only 외부 grant | grant·public exchange/read·shared card 전체 제외 |
| `DN-C5-005` | `C5_NOTIFICATION=IN_APP_PROVIDERLESS_OPT_IN_V1` | 외부 provider 없는 opt-in in-app projection만 허용 | notification setting/projection/UI 전체 제외 |
| `DN-C5-006` | `C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED` | recovery/change/delete를 C4 승인 뒤 다시 결정 | recovery·change·delete API/data/UI 전체 제외 |

---

## `DN-C5-001` — 반기 report 의미·생성

**권장 선택:** `C5_REPORT=CALENDAR_HALF_KST_IMMUTABLE_REVISION_V1`

| 항목 | 권장값 |
| --- | --- |
| 기간 | `REPORT_TIMEZONE=Asia/Seoul`; H1 `[01-01T00:00, 07-01T00:00)`, H2 `[07-01T00:00, 다음 해 01-01T00:00)` |
| watermark | period 종료 `+72h`; 그 시각까지 commit된 승인 C1 source만 최초 revision에 포함 |
| 최초 생성 | owner가 종료+72h 뒤 처음 조회를 요청할 때 생성; rating 0건도 `EMPTY_NO_ACTIVITY` revision 생성 |
| 상태 | `EMPTY_NO_ACTIVITY`, `READY`, `SUPERSEDED`; 생성 실패는 공개 revision 없이 재시도 가능 |
| factual metric allowlist | 감상 건수, active rating 건수, rating 값별 건수, 산술 평균과 분포, 영화·장르·국가·감독·8 flavor별 단순 count/average. 분모와 missing count를 함께 표시. 이는 허용 목록이며 모든 항목의 동시 구현 의무가 아니다. local MVP는 감상/평가 건수·산술 평균·전체 period item만 구현하고 dimension breakdown은 immutable catalog snapshot 계약 전까지 보류한다. |
| 영화 목록 | period 조건에 해당하는 C1 source의 실제 영화 전체 `periodItems`; `watchedAt ASC, movieId ASC` stable order. 일부 대표작으로 대체하지 않음 |
| 금지 | `SATISFACTION=NOT_COMPUTED`, `TASTE_DIAGNOSIS=NOT_COMPUTED`, `EXPECTED_STAR=NOT_COMPUTED`, 추천 성공·취향 향상 문구 |
| late update/delete | 기존 revision을 덮지 않고 owner가 재생성을 요청할 때 새 immutable revision 생성; 하루 최대 1회, period당 최대 5개 revision |
| retention | current revision은 account lifecycle까지, `SUPERSEDED` revision은 supersede 시점부터 `400d`; 연결 export/share가 있으면 그 grant 만료까지 더 긴 쪽 |
| source-of-truth | C1 ViewingRecord, active Rating, Frame/Popcorn과 당시 C0 catalog identity; C5는 Rating·Film 원천을 복제·수정하지 않음 |
| rollback | 새 revision 생성만 feature flag로 중단. 이미 생성한 immutable revision은 재작성하지 않고 retention에 따라 읽기 전용 유지; 계산 version만 다음 revision에서 변경 |

`EMPTY_NO_ACTIVITY`는 “취향이 없다”가 아니라 해당 기간의 허용 source row가 0개라는 뜻이다. 1건뿐인
경우에도 사실 row는 표시할 수 있지만 변화 추세·우열·진단을 만들지 않는다. Rating 삭제 후 재생성된 revision은
삭제된 값을 되살리지 않으며 revision별 source watermark와 calculation version을 보존한다.

**반대안:** live 계산과 rolling 6개월은 최신성은 높지만 동일 link/download의 재현성과 “상·하반기” 요구를
깨뜨린다. 기간 중 자동 생성은 완결되지 않은 반기를 완성본처럼 보이게 한다.

**DEFER:** `DN-C5-001: DEFER`이면 report projection, metric, visualization, generator, read route를 만들지
않는다. C1 source는 그대로 남으며 C5 snapshot을 선생성하지 않는다.

---

## `DN-C5-002` — 다운로드·renderer·retention

**권장 선택:** `C5_EXPORT=ACCESSIBLE_PDF_ASYNC_24H_V1`

| 항목 | 권장값 |
| --- | --- |
| format | text layer·tagged heading·대체텍스트·한국어 font embedding을 갖춘 `PDF` 1종; PNG/HTML 제외 |
| rendering | server-side deterministic renderer, renderer/font/template version과 output checksum 기록 |
| 상태 | `QUEUED`, `RENDERING`, `READY`, `FAILED_RETRYABLE`, `FAILED_FINAL`, `EXPIRED` |
| 실행 | async; 동일 owner+reportRevision+renderVersion은 한 active job만. 실패는 `1m/5m/15m` 최대 3회 재시도 |
| 크기/페이지 | A4 portrait, 전체 periodItems는 다음 페이지로 계속하며 임의 절단·대표 영화 치환 금지 |
| storage | `OBJECT_STORAGE=SERVICE_MANAGED_ENCRYPTED_AT_REST`; owner authorization 뒤에만 다운로드, shared/public cache 금지 |
| retention | READY artifact 생성부터 `24h`; EXPIRED 또는 account delete 시작 시 즉시 read 차단하고 최대 `1h` 안에 object 삭제 |
| source-of-truth | 승인된 DN-C5-001 immutable report revision. export가 metric이나 영화 목록을 재계산하지 않음 |
| rollback | 신규 job 접수를 중단하고 active job을 취소. artifact는 기존 24h보다 연장하지 않고 삭제하며 report revision은 유지 |

artifact와 filename에는 raw email, internal userId, share token, auth/session ID를 넣지 않는다. 모바일 저장은
owner가 내려받은 뒤의 device lifecycle이고 서버 artifact retention과 구분한다. renderer sandbox·CVE patch가
준비되지 않으면 job은 fail-closed한다.

**반대안:** PNG는 갤러리 공유에는 쉽지만 긴 전체 목록과 접근성을 훼손한다. client rendering은 운영 worker를
줄이지만 기기별 font/layout 차이와 민감 데이터 처리면을 키운다.

**DEFER:** DN-C5-001이 승인되지 않았거나 `DN-C5-002: DEFER`이면 export API/worker/storage/UI를 만들지
않고 report를 파일로 위장하지 않는다.

---

## `DN-C5-003` — privacy·공개 profile·전체 Film/Popcorn·taste compare

**권장 선택:** `C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1`

| 항목 | 권장값 |
| --- | --- |
| 기본값 | 모든 계정과 모든 resource가 `PRIVATE`; 가입·migration이 공개를 암묵 선택하지 않음 |
| resource | `PROFILE`, `FILM`, `POPCORN`, `TASTE_COMPARE`를 독립 설정. 한 resource 공개가 다른 resource 권한을 열지 않음 |
| profile allowlist | C4 승인 actor의 display nickname만 허용. email, internal userId, auth/social/provider identifier, report 존재 여부 제외 |
| Film | opt-in 시 C1 전체 active Frame 목록을 `watchedAt DESC, frameId DESC`로 stable pagination. 대표 영화만 반환하지 않음 |
| Popcorn | opt-in 시 C1 전체 active Popcorn 목록과 전체 count. Frame과 1:1 불변식 유지 |
| compare | v1 권장값 `TASTE_COMPARE=DISABLED_NOT_EVIDENCED`; 공개 opt-in만 저장 가능하고 계산·화면·공유는 별도 evidence 전 차단 |
| 접근 결과 | 비공개·차단·존재하지 않는 target을 public caller에게 동일 `NOT_FOUND` 의미로 처리. raw UUID를 error/log route에 출력하지 않음 |
| cache | authorization은 source setting을 매 요청 확인. public projection TTL 최대 `30s`, privacy 축소/revoke는 cache purge 포함 `60s` 이내 fail-closed |
| retention | current setting은 account lifecycle까지; privacy change safe audit은 resource/action만 `180d`, viewer/target raw pair·열람 이력은 저장하지 않음 |
| source-of-truth | setting은 승인 후 C5 resource privacy, nickname/auth는 C4, 전체 Film/Popcorn은 C1 |
| rollback | 모든 public resource를 강제로 PRIVATE 처리하고 60초 안에 cache/projection purge. C1 Frame/Popcorn source는 삭제·변경하지 않음 |

PUBLIC은 검색 노출을 뜻하지 않는다. v1 권장은 `PUBLIC_PROFILE_DISCOVERY=DISABLED`이며 정확한 capability URL을
가진 caller에게만 resource별 authorization을 평가한다. 영화 목록을 잘라서 “대표 Film”으로 바꾸는 대안은
허용하지 않는다.

**반대안:** master public toggle은 단순하지만 nickname 공개가 Film·Popcorn 공개로 확장되는 과도한 권한을
만든다. MEMBERS 단계는 actor·block·검색 정책을 추가하므로 v1 권장안에서 제외한다.

**DEFER:** C4 actor가 승인되지 않았거나 `DN-C5-003: DEFER`이면 privacy row를 미리 만들지 않고 public
profile/Film/Popcorn/compare route·projection·navigation 전체를 만들지 않는다.

---

## `DN-C5-004` — 외부 share grant

**권장 선택:** `C5_SHARE=IMMUTABLE_REPORT_FRAGMENT_EXCHANGE_1CALMONTH_V1`

| 항목 | 권장값 |
| --- | --- |
| 대상 | owner가 선택한 DN-C5-001 immutable report revision 하나. live profile/Film/Popcorn 권한을 상속하지 않음 |
| secret | CSPRNG 256-bit URL-safe raw capability를 최초 1회만 전달; 서버는 SHA-256 hash와 key version만 저장. raw 값은 DB/log/trace/metric/analytics 금지 |
| transport | URL fragment에서 entry script가 network/third-party resource 전에 memory로 읽고 `history.replaceState`로 제거한 뒤 same-origin body로 1회 교환 |
| viewer grant | 교환 성공 후 report projection만 읽는 15분 opaque HttpOnly viewer session; account login/refresh credential이 아님 |
| 만료 | `Asia/Seoul` createdAt에 calendar `plusMonths(1)`; 말일은 다음 달 유효 말일로 clamp. DB absolute instant가 권위 |
| 상태 | `ACTIVE`, `REVOKED`, `EXPIRED`; ACTIVE grant는 report revision당 최대 3개, rotate는 새 grant 생성+기존 즉시 revoke |
| field allowlist | report period, factual metric allowlist, 전체 periodItems와 catalog display metadata. nickname/profile/전체 Film/Popcorn, email/user ID, satisfaction·taste diagnosis·expected star 제외 |
| privacy/delete | owner revoke·account delete 시작 즉시 exchange/read 차단. 이후 privacy 축소도 신규 exchange를 차단하며 이미 발급한 viewer session을 즉시 무효화 |
| HTTP 안전 | secret entry/exchange/read 모두 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`; CDN/shared cache·third-party resource 금지 |
| abuse | coarse trusted-client IP 기준 실패 10회/10분, 전체 조회 60회/분. 초과는 429; token 존재 여부가 status/timing/body로 드러나지 않음 |
| telemetry/retention | raw IP/User-Agent 저장 금지, grant별 aggregate view count만 current lifecycle에 보존. terminal grant hash/metadata는 `30d`, safe revoke audit은 `180d` 후 삭제 |
| source-of-truth | DN-C5-001 report revision과 C5 share grant. C5 share가 report/source Rating을 수정하지 않음 |
| rollback | 신규 생성·교환을 즉시 끄고 모든 ACTIVE grant/viewer session revoke, cache purge. raw token 복구·재발급 없이 owner report는 PRIVATE 유지 |

fragment 교환 entry는 token 제거 전 analytics, font CDN, image CDN을 포함한 제3자 request를 만들지 않는다.
public viewer가 grant를 가졌다는 이유로 profile, Film, Popcorn Bucket이나 다른 report revision을 탐색할 수 없다.

**반대안:** path/query token은 구현이 단순하지만 access log와 Referer 노출면을 키운다. live share는 같은 URL의
내용을 바꾸며, CDN cache는 revoke·expiry·삭제의 즉시성을 약화한다.

**DEFER:** DN-C5-001/003이 승인되지 않았거나 `DN-C5-004: DEFER`이면 share grant, raw token, public
entry/exchange/read, shared card를 만들지 않는다.

---

## `DN-C5-005` — notification channels·defaults

**권장 선택:** `C5_NOTIFICATION=IN_APP_PROVIDERLESS_OPT_IN_V1`

| 항목 | 권장값 |
| --- | --- |
| channel/provider | `IN_APP`만, `EXTERNAL_NOTIFICATION_PROVIDER=NONE`; email/push/SMS/webhook/marketing은 `DISABLED` |
| category | `WATCH_CONFIRMATION_DUE` 하나만 허용. C2B 추천·마케팅·취향 진단 알림 제외 |
| default/consent | `OFF`; ACTIVE C4 actor가 명시 opt-in한 뒤에만 projection 생성. opt-in 시각·policy version만 safe audit |
| source event | C1 WatchIntent의 현재 pending/due 상태. notification이 WatchIntent·ViewingRecord·Rating을 복제 원천으로 만들지 않음 |
| 상태 | `UNREAD`, `READ`, `DISMISSED`, `EXPIRED`; source가 resolved/expired이면 projection도 최대 60초 안에 EXPIRED |
| dedupe/retry | source event/revision당 한 projection. projector failure는 `1m/5m/15m/1h/6h` 최대 5회 후 safe dead-letter; C1 transaction을 rollback하지 않음 |
| quiet hours | in-app에는 적용하지 않음. external channel이 없으므로 timezone delivery schedule도 없음 |
| retention | UNREAD 최대 `30d`; READ/DISMISSED/EXPIRED는 terminal 시점부터 `7d`; safe processing audit `30d` |
| source-of-truth | WatchIntent는 C1, actor/setting은 승인된 C4/C5 setting, notification row는 파생 projection |
| rollback | opt-in과 신규 projector를 끄고 UI를 숨긴 뒤 projection을 7일 안에 삭제. C1 source와 사용자 Rating에는 영향 없음 |

앱을 열지 않은 사용자는 이 선택으로 외부 알림을 받지 않는다. provider credential, 발송 성공률, bounce,
delivery receipt를 구현하거나 주장하지 않는다. 보안·계정 알림도 C4에서 별도 승인하지 않는 한 이 category에
끼워 넣지 않는다.

**반대안:** external email/push는 앱 밖 도달을 늘리지만 provider credential·동의·bounce·비용·삭제 계약이
필요하다. 모두 ON 기본값은 명시적 동의와 최소 공개 원칙을 위반한다.

**DEFER:** C4 actor가 승인되지 않았거나 `DN-C5-005: DEFER`이면 setting, projection, badge, notification
UI를 만들지 않는다. external provider는 선택과 무관하게 별도 운영 승인 전 사용하지 않는다.

---

## `DN-C5-006` — recovery·password change·account delete

**권장 선택:** `C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED`

| 항목 | 권장값 |
| --- | --- |
| 현재 상태 | recovery, password change, account delete 모두 `BLOCKED_BY_C4` |
| secret/session | 새 recovery secret·credential·session mutation `0건`; 기존 C4 draft를 C5가 복제하지 않음 |
| retention | `NOT_APPLICABLE_WHILE_DEFERRED`; C5가 임의 grace/legal/audit retention을 만들지 않음 |
| source-of-truth | 향후 승인된 C4 auth/session/account lifecycle과 C1/C2/C5 데이터 보존 행렬 |
| 재개 Gate | C4 인증 전달·session·email verification 승인, recent reauth 기준, delete grace, session revoke, source별 삭제/익명화/법적 보존, share/export revoke가 함께 승인됨 |
| rollback | 현재 구현이 없으므로 migration 없음. 장래 승인 시에도 단계별 feature flag+active share/export 선 revoke+복구 가능한 migration을 별도 결정 |

generic recovery response, one-time secret, password 변경 후 session revoke, account delete grace는 모두 장래
비교 후보일 뿐 이 결정에서 숫자나 API로 확정하지 않는다. C4 승인 전에 C5가 password/account row를 직접
변경하거나 raw recovery secret을 저장·로그하지 않는다.

**반대안:** C4보다 먼저 recovery/change/delete를 구현하면 actor, credential rotation, session revoke,
idempotency, enumeration 방어와 삭제 authority가 이중화된다. account row만 먼저 지우는 hard delete는 C1
불변식과 share/export revoke를 깨뜨릴 수 있다.

**DEFER:** 이 권장 token 자체가 명시적 DEFER다. 제품 소유자가 선택해도 구현 승인이 아니라 C4 dependency와
데이터 lifecycle 결정을 충족할 때까지 범위를 닫아 두는 승인이다.

---

## 선택 기록

local profile은 아래 exact 선택을 사용한다.

```text
DN-C5-001: C5_REPORT=CALENDAR_HALF_KST_IMMUTABLE_REVISION_V1
DN-C5-002: C5_EXPORT=ACCESSIBLE_PDF_ASYNC_24H_V1
DN-C5-003: C5_PRIVACY=PRIVATE_RESOURCE_OPT_IN_V1
DN-C5-004: C5_SHARE=IMMUTABLE_REPORT_FRAGMENT_EXCHANGE_1CALMONTH_V1
DN-C5-005: C5_NOTIFICATION=IN_APP_PROVIDERLESS_OPT_IN_V1
DN-C5-006: C5_ACCOUNT_LIFECYCLE=DEFER_UNTIL_C4_APPROVED
```

결정자: standalone project owner instruction. 결정일: 2026-08-30. 허용 노출: loopback local only.
production에서는 별도 결정자·운영 source·rollback·재검토 조건 승인 전 모두 fail-closed다.
