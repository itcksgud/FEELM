# C2B 상태 모델

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`

## SCN-C2B-001 — Ranking policy

```text
INPUT_READY
  ├─ K<10 ───────────────→ POPULARITY_FALLBACK
  └─ K>=10
       ├─ decision/artifact 미승인 → PERSONALIZATION_CANDIDATE_BLOCKED → POPULARITY_FALLBACK
       └─ 향후 승인된 exact policy → PERSONALIZATION_APPLIED
```

현재 도달 가능한 proposed public 결과는 `POPULARITY_FALLBACK`뿐이다. K10이라는 이유만으로
`PERSONALIZATION_APPLIED`로 전이하지 않는다.

## SCN-C2B-002 — 누적 delivery collection

```text
INITIAL_REQUESTED
  ├─ Catalog singleton FOR SHARE → actor eligibility FOR SHARE
  │    + mapping/catalog/C1 typed version double-read equal + every item current valid
  │    + locks held through commit → REPLAY_PREPARED
  ├─ existing collection → RETURN_ACTIVE_COLLECTION(no new page)
  ├─ Top500 scan/backfill 결과 3 → PREPARED_COMPLETE(3)
  ├─ Top500 소진 결과 1..2 → PREPARED_PARTIAL
  ├─ 0 eligible → EMPTY
  └─ timeout/stale/config/DB → FAILED (503, no stale success)

PREPARED_*
  ├─ valid exposure acknowledgement → EXPOSED_PARTIAL | EXPOSED_ALL
  └─ version drift → STALE (409, no exposure write)
```

```text
ACTIVE_COLLECTION(revision N, nextCursor)
  ├─ append(expected N, valid cursor)
  │    ├─ 3 new unique → APPEND_COMPLETE + revision N+1
  │    ├─ Top500 exhausted 1..2 → APPEND_PARTIAL + revision N+1
  │    └─ Top500 exhausted 0 → APPEND_EMPTY + hasMore=false
  ├─ stale cursor/revision/version → 409 REFRESH_REQUIRED (collection unchanged)
  ├─ Rating commit(movie) → matching ACTIVE item(s) COMPLETED_RATED
  ├─ explicit 관심 없음(item) → DISMISSED_NOT_INTERESTED
  └─ Catalog invalid(item) → RETIRED_CATALOG

ACTIVE item
  ├─ load-more/refresh/re-entry → ACTIVE (same sequence position)
  ├─ watch/view only → ACTIVE
  ├─ Rating commit → COMPLETED_RATED (terminal for this collection)
  ├─ explicit 관심 없음 → DISMISSED_NOT_INTERESTED (terminal)
  └─ Catalog invalid → RETIRED_CATALOG (system safety)
```

append는 기존 ACTIVE item을 교체·재정렬·삭제하지 않는다. `COMPLETED_RATED`와
`DISMISSED_NOT_INTERESTED`만 user-driven 목록 종료이며 서로 다른 사건이다. Rating 삭제는 종료 item을 자동
복원하지 않고, 감상 완료만으로는 종료하지 않는다. Catalog 안전 퇴역은 사용자 관심 판단으로 기록하지 않는다.

C1 Viewing/Rating mutation은 같은 actor eligibility row를 `FOR UPDATE`하고 mutation+version increment를 한
transaction으로 commit한다. Rating commit은 exact movie item 종료도 같은 선형화 경계에서 수행한다. C2B append와
C1 Rating 중 먼저 commit한 쪽이 선형화되지만 최종 상태는 동일하며 unrelated ACTIVE item은 유지된다. Viewing-only
version 변화는 새 candidate exclusion에만 반영한다. lock/version protocol unavailable은 새 append 503이고 기존
collection을 삭제하지 않는다.
C0 activation/UI_READY mutation도 Catalog singleton을 `FOR UPDATE`하고 mutation+version increment를 같은
transaction에 commit한다. C2B final check는 Catalog shared lock을 먼저 얻고 actor shared lock을 얻어 delivery/exposure
commit까지 유지한다. C0-first는 새 state, C2B-first는 현재 result 뒤 다음 요청 stale로 선형화한다.

## SCN-C2B-003 — Exposure item funnel

```text
EXPOSED
  → DETAIL_OPENED
  → OTT_OPTION_OPENED
  → WATCH_CONFIRMED
  → RATED
```

중간 상태를 건너뛸 수 있지만 뒤 상태는 exact resource chain이 있어야 한다. 아무 사건도 없으면
`EXPOSED` 그대로이며 NEGATIVE로 전이하지 않는다.

## SCN-C2B-004 — Attribution

```text
explicit recommendationItemId + same owner/movie
  ├─ DETAIL_OPENED → DIRECT_ACTION
  └─ OTT_OPTION_OPENED + unique C1 click behaviorEventId
       ├─ CREATED + WatchIntent + ViewingRecord → DIRECT_WATCH
       ├─ CREATED + WatchIntent + Rating → DIRECT_RATING
       └─ ACTIVE_REUSED | ALREADY_WATCHED → DIRECT_CLICK_ONLY

explicit link 없음/불일치 → UNATTRIBUTED
```

현 C1 응답에는 current click behaviorEventId가 없어 OTT 분기는 `BLOCKED_C1_CLICK_CONTRACT`다.
ALREADY_WATCHED는 WatchIntent null이며 과거 Viewing/Rating을 새 추천 결과로 복제하지 않는다.

## SCN-C2B-005 — Composition

```text
현재: BASELINE_THREE
REC-EV-013 v1 complete → selected:null / relevance Gate FAIL → BASELINE_THREE 유지
새 evidence version + validator PASS + product decision
  → future version에서만 TWO_PERSONAL_ONE_DISCOVERY 재검토
```

evidence artifact 출현만으로 전이하지 않는다.

## SCN-C2B-006 — Failure/isolation

```text
C1 Rating/Watch transaction COMMITTED
  → separate C2B action transaction
  → outbox/projection/recommendation failure
  → C1 state remains COMMITTED

cross-owner resource → 404
Top500 소진 뒤 최종 1..2편 → PARTIAL
DB/recommender unavailable → 503
```

### Projector ordering

```text
source event received
  └─ immutable inbox eventId claim
       ├─ action exists → ledger unique FK → item row lock → projection
       ├─ action absent → PENDING_ACTION
  │    └─ later CREATED action/reconcile → exact chain deterministic backfill
       ├─ duplicate eventId + same payload → NOOP
       └─ duplicate eventId + payload drift → DEAD_LETTER

Rating revision
  ├─ higher → apply ACTIVE or DELETED tombstone
  ├─ equal + same → NOOP
  ├─ equal + different → DEAD_LETTER
  └─ lower → STALE_NOOP
```

late reconcile은 `(stageRank ASC, sourceRevision ASC NULLS FIRST, server occurredAt ASC, sourceEventId ASC)`로
inbox를 읽는다. periodic replay와 action-commit trigger가 같은 결과를 내며 ACTIVE_REUSED/ALREADY_WATCHED 또는
unrelated historical WatchIntent를 연결하지 않는다.

### 반복 action과 singular projection

```text
item action 0..N
  → each exact action/event ledger 보존
  → winner = max(stageRank), then min(server occurredAt), then min(actionEventId)
  → projection FK는 winner CREATED chain 하나에서만 갱신
  → equal/lower later action = ledger only, projection winner unchanged
```

DETAIL reference는 DETAIL action의 earliest server time/actionEventId를 별도 보존한다. ACTIVE_REUSED와
ALREADY_WATCHED는 OTT stage까지만 경쟁하며 watch/viewing/rating FK를 갖지 않는다.

### Exposure dual-idempotency

```text
sorted advisory lock(HEADER key, BODY exposureBatchId)
  → header IDEMPOTENCY_RECORD claim/check
  → body exposure batch canonical claim/check
  → exposure batch/items + delivery link + canonical domain result one REQUIRES_NEW transaction
  → original wire 201/replayed=false; replay wire 200/replayed=true; domain fields identical
  → crash = all rollback; concurrent same body = same replay or 409 drift
```
