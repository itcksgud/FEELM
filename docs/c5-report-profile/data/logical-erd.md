# C5 local logical ERD

> 상태: `APPROVED_LOCAL_MVP_PROFILE`

```text
C4 USER_ACCOUNT 1 ── N TASTE_REPORT_REVISION 1 ── N TASTE_REPORT_PERIOD_ITEM
                         │ 1
                         ├── N REPORT_EXPORT_JOB 1 ── 0..1 REPORT_EXPORT_ARTIFACT
                         └── N REPORT_SHARE_GRANT 1 ── N REPORT_SHARE_VIEWER_SESSION

C4 USER_ACCOUNT 1 ── 3 USER_PRIVACY_SETTING
C4 USER_ACCOUNT 1 ── 1 USER_NOTIFICATION_SETTING
C4 USER_ACCOUNT 1 ── N IN_APP_NOTIFICATION

TASTE_REPORT_PERIOD_ITEM ── snapshot reference ──> C1 VIEWING_RECORD/RATING + C0 MOVIE
IN_APP_NOTIFICATION ── source reference ──> C1 WATCH_INTENT revision
```

C5는 credential/session, Rating, Frame, Popcorn, movie 사본의 원천을 만들지 않는다. report item은 immutable
snapshot이며 source ID/version과 표시 snapshot만 보관한다. public Film/Popcorn은 C1 active source를 직접
권한 필터 뒤 읽는다.

