# C5 local navigation

```text
/me/reports ──> /me/reports/{reportId} ──> /me/reports/{reportId}/export
                                   └────> /me/reports/{reportId}/share
/me/privacy ── exact capability ──> /people/{publicProfileId}
/me/notifications
/#/shared-report?token=... ── fragment remove/exchange ──> /shared-report
```

실제 구현에서는 secret을 query/path에 넣지 않는다. 위 표기의 token은 fragment payload 개념을 나타낼 뿐
서버 request URL 형식이 아니다. 공개 discovery/search navigation과 taste comparison link는 없다.

