# C2B 논리 ERD

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`  
> 물리 migration: 없음. 승인 전 구현 금지.

```text
USER_ACCOUNT 1 ── N RECOMMENDATION_DELIVERY_BATCH
USER_ACCOUNT 1 ── 1 USER_RECOMMENDATION_ELIGIBILITY_VERSION
CATALOG_DISCOVERY_ELIGIBILITY_VERSION 1 ── N RECOMMENDATION_DELIVERY_BATCH
CATALOG_VERSION 1 ── N RECOMMENDATION_DELIVERY_BATCH
RECOMMENDATION_DELIVERY_BATCH 1 ── 0..500 RECOMMENDATION_DELIVERY_ITEM
RECOMMENDATION_DELIVERY_BATCH 1 ── N RECOMMENDATION_DELIVERY_APPEND
RECOMMENDATION_DELIVERY_APPEND 1 ── 0..3 RECOMMENDATION_DELIVERY_ISSUE
RECOMMENDATION_DELIVERY_ITEM 1 ── 0..1 RECOMMENDATION_ITEM_DISMISSAL
MOVIE_IDENTITY 1 ── N RECOMMENDATION_DELIVERY_ITEM

RECOMMENDATION_DELIVERY_ITEM 1 ── 0..1 RECOMMENDATION_EXPOSURE_ITEM
RECOMMENDATION_EXPOSURE_BATCH 1 ── 1..3 RECOMMENDATION_EXPOSURE_ITEM
RECOMMENDATION_EXPOSURE_ITEM 1 ── N RECOMMENDATION_ACTION
RECOMMENDATION_EXPOSURE_BATCH 1 ── 1..N IDEMPOTENCY_RECORD (original/replay header keys reference safe result)
RECOMMENDATION_ACTION 1 ── 1..N IDEMPOTENCY_RECORD (original/replay header keys reference safe result)

USER_BEHAVIOR_EVENT 1 ── 0..1 RECOMMENDATION_ACTION (C1 OTT only; event unique)
RECOMMENDATION_ACTION 1 ── 0..1 WATCH_INTENT (CREATED OTT only)
WATCH_INTENT 1 ── 0..1 VIEWING_RECORD
VIEWING_RECORD 1 ── 0..1 RATING

RECOMMENDATION_EXPOSURE_ITEM 1 ── 0..1 RECOMMENDATION_ATTRIBUTION_PROJECTION
RECOMMENDATION_ATTRIBUTION_PROJECTION ── optional exact ACTION/WATCH/VIEWING/RATING references
RECOMMENDATION_ATTRIBUTION_PROJECTION 1 ── N RECOMMENDATION_ATTRIBUTION_EVENT_LEDGER
C1_RECOMMENDATION_SOURCE_EVENT_INBOX 0..N ── 0..1 RECOMMENDATION_ACTION (late-action exact-chain reconcile)
```

DETAIL action은 C1 behavior event와 WatchIntent를 모두 참조하지 않는다. OTT action만 committed C1 event를
정확히 하나 참조하고, C1 event 하나는 최대 한 recommendation action에 연결된다. 하나의 WatchIntent는 distinct
ACTIVE_REUSED click 때문에 여러 action에서 참조될 수 있지만 downstream attribution은 CREATED action 하나에만 있다.
반복 action은 허용하되 projection은 `(stageRank DESC, server occurredAt ASC, actionEventId ASC)`의 deterministic
winner 한 chain만 참조한다. stageRank는 RATED, WATCH_CONFIRMED, OTT_OPTION_OPENED, DETAIL_OPENED 순이다.

## 기존 C2A 재사용

- `recommendation_exposure_batch/item`의 model/artifact/catalog/candidate/input/policy snapshot과
  expected-star disabled constraint를 그대로 사용한다.
- exposure item에 nullable `delivery_item_id` unique FK를 추가하는 migration 후보가 필요하다. 다른
  delivery에서 같은 movie가 반복될 수 있으므로 movie/recommendationVersion unique는 만들지 않는다.
- C1 WatchIntent·ViewingRecord·Rating의 owner/movie FK 의미를 변경하지 않는다.

## 신규 entity invariant

### RECOMMENDATION_DELIVERY_BATCH

- PK `delivery_id` UUID, owner composite UK `(delivery_id, actor_user_id)`.
- `delivery_revision >= 1`; `expires_at`은 current cursor expiry일 뿐 active collection expiry가 아니다.
- `mapping_version`은 non-empty typed column이며 C2A response→service movie mapping artifact의 exact version이다.
  current active mapping version과 다르면 append를 409로 막고 refresh한 typed version을 새 후보에만 적용한다. 기존 ACTIVE item은 유지하며 문자열 bag/JSON metadata로 대체하지 않는다.
- `eligibility_version`을 typed column으로 저장한다. append 전 actor의 USER_RECOMMENDATION_ELIGIBILITY_VERSION
  row를 `FOR SHARE` lock하고 version double-read와 current Catalog UI_READY·C1 상태를 읽는다. C1 Viewing/Rating
  mutation은 같은 actor row를 `FOR UPDATE`한 transaction에서 version+1과 함께 commit한다. Rating은 exact item을
  COMPLETED_RATED로 종료하고 Viewing-only는 기존 item을 삭제하지 않는다.
- 각 append outcome은 `COMPLETE|PARTIAL|EMPTY`, selected_count `0..3`; collection item_count는 0..500이다.
- label `POPULARITY_BASELINE`, ranking alpha 0, composition `BASELINE_THREE`만 허용한다.
- recommendation/artifact/policy/catalog/candidate/input/composition version을 typed column으로 저장한다.
- `catalog_eligibility_version`도 typed bigint snapshot으로 저장한다. C2B는 Catalog singleton version을 `FOR SHARE`,
  actor eligibility version을 `FOR SHARE` 순서로 잠근 뒤 두 version과 current rows를 final double-check하고 delivery/exposure
  commit까지 유지한다. C0 activation/UI_READY mutation은 singleton을 `FOR UPDATE`하고 같은 transaction에서 +1한다.
- 이 internal version/checksum/hash는 public DTO에 매핑하지 않는다.
- expected-star value, raw FastAPI JSON, candidate 전체, token/user bearer는 저장하지 않는다.

### RECOMMENDATION_DELIVERY_ITEM

- PK `delivery_item_id`, composite FK `(delivery_id, actor_user_id)`.
- `position 1..500`, source_rank positive, `(delivery_id,position)`과 `(delivery_id,movie_id)` unique. 종료 뒤 재번호화하지 않는다.
- recommendation_type은 현재 `POPULARITY_BASELINE`만.
- `source_rank 1..500`; 한 delivery 안 unique. rank response UUID set/cardinality가 request first500과 다르면 delivery를 만들지 않는다.
- movie는 안정 `movie_identity.id`; 생성 시 active UI_READY였음을 snapshot하되 exposure 시 재검증한다.
- display status는 `ACTIVE|COMPLETED_RATED|DISMISSED_NOT_INTERESTED|RETIRED_CATALOG`. Rating commit은 exact
  actor/movie ACTIVE item을 같은 transaction에서 COMPLETED_RATED로 전이하며 Viewing-only는 전이하지 않는다.

### RECOMMENDATION_DELIVERY_APPEND / RECOMMENDATION_ITEM_DISMISSAL

- append는 actor/delivery/expected revision/signed cursor/body event ID/header key를 검증하고 page 0..3 item과
  collection revision/scan offset을 한 transaction에 commit한다.
- dismissal reason은 NOT_INTERESTED const이며 free text·Rating 값·satisfaction field가 없다.
- Rating completion과 dismissal은 서로 다른 typed terminal reason이고 Rating delete가 item을 자동 복원하지 않는다.
- Catalog invalidation은 user reason이 아닌 RETIRED_CATALOG다.

### RECOMMENDATION_DELIVERY_ISSUE

- PK `(append_event_id,code)`; allowlisted code와 positive aggregate `count`, `retriable`만 저장한다.
- DB enum/check는 세 code 모두 `retriable=false`, `count>=1`을 강제하고 `(append_event_id,code)` key로 split count를 금지한다.
- 제외된 개별 movie ID나 upstream 원문은 저장하지 않는다.
- `page.scanned_count = page.selected_count + sum(issue.count)`를 transaction commit 전에 검증한다. exact code는
  `CANDIDATE_NOT_UI_READY`, `CANDIDATE_ALREADY_RATED`, `CANDIDATE_ALREADY_SEEN` 0..3개이고 candidate당
  precedence로 정확히 한 code만 증가시킨다. model mapping/set/rank drift는 issue row 없이 503이다.

### RECOMMENDATION_ACTION

- PK `action_event_id`; `(actor_user_id,idempotency_key,operation)` unique와 canonical request hash.
- header key ledger는 `(actor_user_id,operation,idempotency_key)` unique, body event ledger는
  `(actor_user_id,action_event_id)` unique다. 두 unique와 item row lock이 concurrent request의 single winner를 만든다.
- composite FK `(recommendation_item_id,actor_user_id)`로 cross-owner 연결을 DB에서도 차단한다.
- `DETAIL_OPENED`는 c1_behavior_event_id/watch_intent_id null이다.
- `OTT_OPTION_OPENED`는 C1 current click `behavior_event_id` global unique FK가 필수이고 event owner/movie와 일치한다.
- `CREATED` click만 watch_intent_id가 non-null이며 downstream attribution 대상이다. `ACTIVE_REUSED`는 click-only,
  `ALREADY_WATCHED`는 click-only + watch_intent_id null이다.
- 같은 WatchIntent는 여러 click에 재사용될 수 있으므로 watch_intent_id unique는 금지한다. 같은 C1 click event를
  여러 recommendation item에 연결하지 못하도록 c1_behavior_event_id만 unique다.
- URL, provider destination, Rating 값, free text를 저장하지 않는다.
- `(recommendation_item_id,action_type)` unique는 두지 않아 반복 action 0..N을 보존한다.

### RECOMMENDATION_ATTRIBUTION_PROJECTION

- PK/FK `recommendation_item_id`; explicit action이 처음 생길 때만 생성한다.
- 최고 stage와 deterministic winner의 exact action/watch/viewing/rating reference를 보존한다. winner key는
  `(stage_rank DESC, winner_occurred_at ASC, winner_action_event_id ASC)`이고 equal stage later action은 winner를
  바꾸지 않는다. higher stage winner로 바뀌어도 stage는 낮아지지 않으며 모든 chain FK는 같은 CREATED action에서 온다.
- observed utility 상태는 현재 `NOT_COMPUTED`; numeric utility/prediction error는 null constraint.
- Rating delete는 projection row를 삭제하지 않고 rating logical status/revision을 snapshot한다.

### RECOMMENDATION_ATTRIBUTION_EVENT_LEDGER

- append-only PK `source_event_id`, owner/item/source_type/canonical payload hash/received_at을 저장한다.
- event ID replay는 동일 hash면 noop, 다른 hash면 dead-letter다. projector는 ledger claim 뒤 item projection row를 lock한다.
- Rating source는 `(rating_id,revision,event_type)`도 unique다. revision이 현재보다 높을 때만 적용하고 delete는
  highest revision `DELETED` tombstone을 남긴다. lower/equal replay가 ACTIVE로 되돌리지 못한다.

## Transaction

1. 최초 delivery collection/items와 각 append page/items/scan offset/revision은 각각 한 transaction이다.
2. exposure acknowledgement는 actor+operation의 HEADER key와 BODY exposureBatchId advisory lock을 hash
   오름차순으로 얻는다. 공통 IDEMPOTENCY_RECORD, 기존 C2A `REQUIRES_NEW` batch/items, delivery link, safe replay
   canonical domain payload를 한 transaction에 원자 반영한다. payload에는 HTTP status와 `replayed` flag가 없다.
   original wire는 201+replayed=false, replay wire는 200+replayed=true이며 나머지 canonical domain fields는 동일하다.
   crash는 모두 rollback이고 다른 key/same body ID winner는 canonical body가 같을 때만 기존 domain result를 replay한다.
3. C1 OTT click/WatchIntent는 C1 transaction에서 먼저 독립 commit한다.
4. C2B action은 같은 sorted HEADER/BODY advisory lock protocol로 IDEMPOTENCY_RECORD, action, outbox,
   attribution projection, canonical domain result를 별도 한 transaction에 commit하고 committed C1 event를 참조한다.
   C2B 실패가 C1 transaction을 rollback하지 않는다.
5. async Watch/Rating projector 실패는 C1 원 transaction을 rollback하지 않고 immutable eventId로 재시도한다.
6. C1 ViewingRecord·active Rating eligibility mutation은 USER_RECOMMENDATION_ELIGIBILITY_VERSION row를
   `FOR UPDATE`하고 mutation+version increment를 같은 transaction에 commit한다. C2B selection은 같은 row의
   `FOR SHARE` lock과 version double-read를 delivery commit까지 유지하므로 두 transaction은 선형화된다.
7. C0 Catalog activation/UI_READY mutation은 CATALOG_DISCOVERY_ELIGIBILITY_VERSION singleton `FOR UPDATE` 아래
   mutation+version increment를 commit한다. C2B는 Catalog shared lock을 actor lock보다 먼저 얻고 current Catalog/C1
   final check부터 delivery 또는 exposure commit까지 두 shared lock을 보유한다.
8. C1 Rating 제출은 actor eligibility lock 아래 Rating+version increment와 matching collection item의
   COMPLETED_RATED 전이를 함께 commit한다. 감상 확인만으로 item을 종료하지 않는다.

## Late-action event reconciliation

모든 C1 event는 immutable `C1_RECOMMENDATION_SOURCE_EVENT_INBOX`에서 eventId를 먼저 claim한다. attribution ledger의
`source_event_id`는 inbox에 대한 UNIQUE FK다. action보다 먼저 도착하면 owner/movie/resource chain,
source event ID/revision/type/canonical hash/server occurredAt을 `PENDING_ACTION`으로 저장한다. raw payload는 저장하지
않는다. CREATED OTT action commit은 exact behaviorEventId/WatchIntent chain reconcile outbox를 생성한다. projector는
item/action row lock 아래 해당 chain inbox를 `(stageRank ASC, sourceRevision ASC NULLS FIRST, serverOccurredAt ASC,
sourceEventId ASC)`로 읽고 ledger claim과 projection을 재계산한다. periodic reconcile도 같은 규칙을 사용하므로
재실행 결과가 같다. same event/hash는 noop, drift는 dead-letter다. ACTIVE_REUSED/ALREADY_WATCHED 또는 unrelated
historical WatchIntent를 추측해 backfill하지 않는다.

## C1 선행 계약 Gate

현 C1 `createWatchIntent` 응답은 이번 click의 behaviorEventId/server occurredAt을 반환하지 않고
ACTIVE_REUSED의 과거 clickedAt, ALREADY_WATCHED의 null WatchIntent만 제공한다. 따라서 migration과 public action은
`TASK-C2B-011`의 C1 click event 응답·typed projection 계약이 승인될 때까지 구현하지 않는다.
