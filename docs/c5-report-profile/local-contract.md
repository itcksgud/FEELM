# C5 local MVP 실행 계약

> 상태: `APPROVED_LOCAL_MVP_PROFILE`  
> 권위: localhost·C4 local actor·fixture data에 한정  
> production/external provider/public discovery: `BLOCKED`

## 1. API inventory

| 영역 | operationId | 의미 |
| --- | --- | --- |
| report | `listMyTasteReports` | owner의 calendar-half report revision 목록 |
| report | `getMyTasteReport` | owner의 immutable factual report와 실제 period 영화 전체 page |
| report | `createMyTasteReportRevision` | C1 source를 현재 시점에 snapshot한 새 revision 생성 |
| export | `createMyTasteReportExport` | report revision의 local PDF job 생성 |
| export | `getMyTasteReportExport` | owner의 job/expiry 상태 조회 |
| export | `downloadMyTasteReportExport` | READY·미만료 local PDF 다운로드 |
| privacy | `getMyPrivacySettings` | PROFILE/FILM/POPCORN의 PRIVATE/PUBLIC 상태 조회 |
| privacy | `replaceMyPrivacySettings` | resource별 전체 replace와 revision CAS |
| public | `getPublicUserProfile` | PROFILE opt-in target의 nickname만 조회 |
| public | `listPublicUserFilmFrames` | FILM opt-in target의 C1 active Frame 전체 cursor page |
| public | `listPublicUserPopcorns` | POPCORN opt-in target의 C1 active Popcorn 전체 cursor page |
| share | `createMyTasteReportShare` | 특정 immutable report revision의 raw-once grant 생성 |
| share | `revokeMyTasteReportShare` | owner가 grant와 viewer session을 즉시 revoke |
| share | `exchangeTasteReportShare` | fragment에서 제거한 raw secret을 body로 교환 |
| share | `getSharedTasteReport` | report-only viewer session으로 허용 projection 조회 |
| notification | `getMyNotificationSettings` | in-app category opt-in 조회 |
| notification | `replaceMyNotificationSettings` | WATCH_CONFIRMATION_DUE opt-in 전체 replace |
| notification | `listMyNotifications` | actor의 in-app projection page |
| notification | `updateMyNotificationState` | unread→read 또는 active→dismissed 전이 |

모든 owner operation은 C4 local session actor를 사용한다. public target은 내부 user UUID가 아니라 별도
무작위 `publicProfileId`다. share raw secret은 URL query/path가 아닌 fragment→POST body 교환에만 사용한다.

## 2. Entity ownership

| entity | 핵심 field·불변식 |
| --- | --- |
| `TASTE_REPORT_REVISION` | owner, periodStart/End(KST), revision, status, sourceWatermark, immutable payload |
| `TASTE_REPORT_PERIOD_ITEM` | report revision, C1 viewing/rating snapshot, actual movie identity; 삭제 후 복원 금지 |
| `REPORT_EXPORT_JOB` | owner, report revision, PENDING/READY/FAILED/EXPIRED, attempt≤3 |
| `REPORT_EXPORT_ARTIFACT` | local opaque path, sha256, media type PDF, expiresAt≤createdAt+24h |
| `USER_PRIVACY_SETTING` | owner, resource PROFILE/FILM/POPCORN, PRIVATE/PUBLIC, revision; default PRIVATE |
| `REPORT_SHARE_GRANT` | owner, report revision, token hash, ACTIVE/REVOKED/EXPIRED, one-month expiry |
| `REPORT_SHARE_VIEWER_SESSION` | grant, session hash, 15-minute expiry; report-only authority |
| `USER_NOTIFICATION_SETTING` | owner, WATCH_CONFIRMATION_DUE enabled=false default, revision |
| `IN_APP_NOTIFICATION` | owner, source watchIntent revision unique, UNREAD/READ/DISMISSED, expiry |

C5는 C4 user/session, C1 ViewingRecord/Rating/Frame/Popcorn/WatchIntent, C0 movie를 재사용하며 두 번째
credential·Rating·Film·Popcorn 원천을 만들지 않는다.

## 3. Business rules

1. report period는 `Asia/Seoul` calendar half(`01-01..06-30`, `07-01..12-31`)이고 period 종료+72시간
   뒤 생성할 수 있다. 활동 0건은 `EMPTY_NO_ACTIVITY`이며 실패가 아니다.
2. local MVP report는 감상 수, 평가 수, 평가의 산술 평균(소수 둘째 자리 반올림), 실제 period item 전체만
   포함한다. 장르·국가·감독·flavor breakdown은 제품 결정의 factual allowlist 안에 있지만, immutable
   catalog snapshot 계약을 별도로 확정하기 전까지 이 local slice에서는 명시적으로 보류한다. 추천 점수·
   예상 별점·만족도·취향 향상/진단은 포함하지 않는다.
3. create revision은 source watermark와 snapshot provenance를 저장한다. 기존 revision을 수정하지 않는다.
4. PDF는 local worker가 deterministic하게 만들고 artifact는 최대 24시간 보존한다. raw email/internal ID를
   filename·PDF metadata·log에 넣지 않는다.
5. privacy 기본값은 모든 resource `PRIVATE`다. PROFILE/FILM/POPCORN은 독립 opt-in이며 존재하지 않거나
   private인 target은 같은 404 payload를 반환한다. 공개 목록도 대표 subset이 아니라 전체 stable page다.
6. share secret은 256-bit 이상, 최초 응답 한 번만 raw로 반환하고 hash만 저장한다. active grant는 owner당
   최대 3개, 만료는 생성 시점의 KST local date 기준 한 calendar month다.
7. browser는 fragment를 history에서 먼저 제거한 뒤 raw secret을 POST body로 교환한다. exchange/read는
   `Cache-Control: no-store`, `Referrer-Policy: no-referrer`이며 third-party resource를 렌더링하지 않는다.
8. viewer session은 15분, 해당 report revision만 읽는다. revoke/expiry 뒤 grant와 session은 즉시 실패한다.
9. notification은 `WATCH_CONFIRMATION_DUE` in-app만, 기본 OFF다. 같은 source revision은 한 건이며 확인 완료
   시 더 이상 active가 아니다. unread 30일, terminal 7일 뒤 삭제한다.
10. `C5_LOCAL_ENABLED=false` 또는 non-loopback/public origin이면 public/share/export capability를 등록하지
    않는다. production에서 storage/provider/signing 조건 없이 local adapter로 성공을 가장하지 않는다.

## 4. State machines

```text
Report: NONE --owner create--> READY | EMPTY_NO_ACTIVITY
        READY/EMPTY --owner create new revision--> SUPERSEDED + new immutable revision

Export: PENDING --> READY --> EXPIRED
                 \-> FAILED(after max 3 attempts)

Share: ACTIVE --> REVOKED
              \-> EXPIRED
Viewer session: ACTIVE --> REVOKED | EXPIRED

Notification: UNREAD --> READ --> DISMISSED
              UNREAD ----------> DISMISSED
```

## 5. Screen contract

| ID | route | 핵심 상태 |
| --- | --- | --- |
| `SCR-C5-001` | `/me/reports` | period/revision 목록, EMPTY, 생성 |
| `SCR-C5-002` | `/me/reports/{reportId}` | factual metrics, 실제 period movie 전체, 다음 page |
| `SCR-C5-003` | `/me/reports/{reportId}/export` | PENDING/READY/FAILED/EXPIRED, download |
| `SCR-C5-004` | `/me/privacy` | resource별 PRIVATE/PUBLIC와 명시적 경고 |
| `SCR-C5-005` | `/people/{publicProfileId}` | 허용 nickname과 Film/Popcorn 전체 navigation |
| `SCR-C5-006` | `/me/reports/{reportId}/share` | raw-once copy, active grant, revoke |
| `SCR-C5-007` | `/shared-report` | fragment 제거→exchange→report-only view |
| `SCR-C5-008` | `/me/notifications` | opt-in, unread/read/dismissed, empty/error |

## 6. Acceptance Gate

| ID | 검증 |
| --- | --- |
| `AC-C5-001` | KST calendar-half, +72h, 0건 EMPTY가 결정적이다. |
| `AC-C5-002` | report에는 factual allowlist와 실제 전체 period item만 있고 금지 추정 field가 없다. |
| `AC-C5-003` | 새 revision이 과거 payload를 바꾸지 않고 source provenance를 보존한다. |
| `AC-C5-004` | Rating 삭제가 C1 source/Film/Popcorn을 되살리지 않는다. |
| `AC-C5-005` | PDF job owner 격리, text 추출 가능, 전체 목록, 24h expiry/cleanup을 만족한다. |
| `AC-C5-006` | PDF filename/metadata/log의 email/internal ID/raw token이 0건이다. |
| `AC-C5-007` | privacy row가 없어도 PROFILE/FILM/POPCORN은 PRIVATE다. |
| `AC-C5-008` | private/unknown/cross-owner target은 같은 404이고 revoke는 60초 안에 반영된다. |
| `AC-C5-009` | public Film/Popcorn traversal은 C1 active 전체와 count가 같고 중복/누락이 없다. |
| `AC-C5-010` | raw share token은 최초 response 외 DB/log/trace/artifact에 없고 hash replay만 검증한다. |
| `AC-C5-011` | fragment 선제 제거, no-store/no-referrer, 15분 viewer, one-month grant expiry가 적용된다. |
| `AC-C5-012` | revoke/expiry/cross-report viewer는 동일한 fail-closed 결과다. |
| `AC-C5-013` | notification OFF에서는 row 0, ON에서 source revision당 1건이다. |
| `AC-C5-014` | watch confirmation 완료·dismiss·retention 전이가 source를 수정하지 않는다. |
| `AC-C5-015` | external mail/push/SMS/object storage/network adapter 호출이 0건이다. |
| `AC-C5-016` | account lifecycle와 taste compare route/UI/table이 없다. |
| `AC-C5-017` | owner/public/share cursor tamper와 cross-context reuse가 실패한다. |
| `AC-C5-018` | 320px, keyboard, semantic status/error, loading/empty를 모두 구분한다. |
| `AC-C5-019` | local kill switch OFF와 production profile에서 capability가 fail-closed한다. |
| `AC-C5-020` | secret scan, cross-owner integration, Compose browser E2E가 통과한다. |

## 7. Required E2E

```text
C4 local login
→ factual half-year report revision 생성/조회
→ PDF job/download
→ privacy PRIVATE 차단 및 PROFILE/FILM/POPCORN opt-in 전체 조회
→ share raw-once 생성, fragment 제거·교환·조회·revoke
→ notification opt-in/read/dismiss
→ logout 뒤 owner route 401
```

이 흐름이 통과해도 판정은 `LOCAL MVP`이며 production readiness가 아니다.
