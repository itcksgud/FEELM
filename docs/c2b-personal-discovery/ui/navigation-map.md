# C2B navigation map

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`

```text
Authenticated Home
  └─ SCR-C2B-001 retained recommendation collection
       ├─ 추가 추천 → POST append(max 3) → existing cards + appended cards
       ├─ 관심 없음 → POST explicit dismissal → that card only removed
       ├─ render commit → POST exposure acknowledgement
       └─ select card → Existing C0 Movie Detail / SCR-C2B-002
            ├─ DETAIL_OPENED action (best-effort, exact item)
            └─ OTT option → Existing C1 createWatchIntent
                 ├─ current contract → exact attribution BLOCKED
                 ├─ amended success → OTT_OPTION_OPENED action with current C1 behaviorEventId
                 ├─ external OTT destination
                 └─ C1 confirmation → ViewingRecord → Rating / SCR-C2B-003
```

## Route proposal

- 새 독립 상세 route를 만들지 않는다. C0 `/movies/:movieId`를 재사용한다.
- 홈 route의 실제 위치는 public product navigation 승인 전 정하지 않는다.
- 별도 route/query 없이 같은 홈 section의 `추가 추천` 버튼이 opaque cursor로 최대 3편을 누적한다.
- 새로고침·재진입 GET은 server-side active collection 전체를 복구한다. React state만으로 보존하지 않는다.
- recommendationItemId는 URL/query string에 넣지 않고 in-memory navigation context 또는 서버가 발급한
  opaque action context로 전달한다. 새로고침으로 사라지면 last-touch 추측 없이 UNATTRIBUTED다.
- 로그인되지 않은 사용자는 C2B operation을 호출하지 않고 auth 진입으로 보낸다.
- cross-owner 404는 not-found UI이며 다른 사용자의 delivery 존재를 설명하지 않는다.

## 실패 격리

- 추천 503: Catalog/search/Film/Rating navigation은 정상 유지.
- exposure ack 실패: card navigation은 가능하나 attributed action은 만들지 않음.
- action 실패: 상세/OTT/C1 mutation 성공을 되돌리지 않음.
- OTT click/WatchIntent는 먼저 독립 commit하고, 이후 별도 C2B action transaction 실패는 best-effort attribution 실패로만 처리.
- stale append 409: 홈에서 collection을 refresh하고 기존 active item을 유지한 채 append만 다시 시도한다.
- Rating commit: 해당 movie item만 `COMPLETED_RATED`로 제거. 감상 확인만으로 제거하지 않음.
