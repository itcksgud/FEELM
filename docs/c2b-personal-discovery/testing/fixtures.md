# C2B fixture 계약

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`

모든 ID는 local fake UUID이며 MovieLens/TMDB external ID, 실제 user/token을 포함하지 않는다.

## 사용자

| Fixture | active Rating K | 기대 eligibility/applied/label |
| --- | ---: | --- |
| `FIX-C2B-USER-K0` | 0 | false / false / POPULARITY_BASELINE |
| `FIX-C2B-USER-K9` | 9 | false / false / POPULARITY_BASELINE |
| `FIX-C2B-USER-K10` | 10 | true / false / POPULARITY_BASELINE; candidate blocked |
| `FIX-C2B-OTHER-OWNER` | 10 | owner isolation 404 |

K는 active integer Rating만 센다. 각 fixture에 deleted Rating, unrated Viewing, watched=false,
onboarding preference를 한 건씩 추가해도 K가 변하지 않아야 한다.

## 영화·candidate

| Fixture | 상태 | 사용 |
| --- | --- | --- |
| `MOV-C2B-A/B/C` | active UI_READY + mapped | baseline three |
| `MOV-C2B-D` | active UI_READY + mapped | 반복 delivery 후보 |
| `MOV-C2B-MAPPING-MISSING` | request Top500에는 있으나 model response mapping 누락 | SafeIssue/PARTIAL이 아닌 public 503 contract drift |
| `MOV-C2B-STALE` | delivery 뒤 active Catalog에서 RETIRED | exposure stale 409 |
| `MOV-C2B-HIDDEN` | visibility BLOCKED | 요청/응답/노출 제외 |

C2A ordered Top500 fixture에는 동점 score 두 편을 넣어 stable service movie UUID 오름차순 tie-break를 검증한다.
MovieLens Train-known만인 영화는 제외하지 않고, service C1 ViewingRecord/active Rating 영화는 제외 후 다음
UI_READY 후보로 backfill한다.
499/500/501 경계와 candidate store total<500 fixture를 둔다. request first min(500,total)과 response exact same
UUID set/sourceRank를 검증하고 missing/extra/duplicate response는 503으로 실패시킨다.

## Delivery/exposure

- `DEL-C2B-COMPLETE`: A/B/C, position 1/2/3, label POPULARITY_BASELINE, BASELINE_THREE.
- `DEL-C2B-PARTIAL`: Top500을 끝까지 소진하고 exact SafeIssue 제외 뒤 A/B만 안전, PARTIAL.
- `DEL-C2B-EMPTY`: 안전 item 0.
- `DEL-C2B-STALE`: 생성 후 Catalog version 교체.
- `DEL-C2B-ISSUES-COMPLETE`: 후보 제외가 있었지만 backfill 후 A/B/C 3개, COMPLETE + aggregate issue/count.
- `DEL-C2B-REPLAY`: 재진입 GET은 같은 collection ID/revision과 ACTIVE item을 복구하고 page를 자동 추가하지 않는다.
- `DEL-C2B-MAPPING-DRIFT`: typed mapping_version v1 collection 뒤 active v2면 append는 409 refresh-required이며 기존 ACTIVE item은 유지된다.
- `DEL-C2B-VIEWED/RATED`: ViewingRecord만 추가되면 기존 item 유지, Rating commit이면 exact item COMPLETED_RATED.
- `DEL-C2B-ISSUE-PRECEDENCE`: 한 candidate가 세 exclusion predicate를 모두 가지면
  `CANDIDATE_NOT_UI_READY` count 하나만 증가하고, RATED+SEEN이면 `CANDIDATE_ALREADY_RATED`만 증가한다.
- 모든 append fixture는 page selectionSummary와 appendedItem/issue count 합 불변식을 가진다.
- `DEL-C2B-ISSUE-DRIFT`: duplicate code, count=0, retriable=true, summary arithmetic mismatch를 각각 거부한다.
- `EXP-C2B-A-B`: React가 A/B만 실제 render해 2개만 acknowledge.
- `EXP-C2B-ORDER`: duplicate item/position, 1·3 gap, array order와 position 불일치를 거부하고 1..N만 허용한다.
- 같은 movie A를 별도 delivery/exposure에 다시 포함해 새 recommendationItemId인지 검증한다.

## Action/resource chain

- `ACT-C2B-DETAIL`: exposed A에 DETAIL_OPENED, watchIntent 없음.
- `ACT-C2B-OTT-CREATED`: exposed B + same owner/movie current C1 behaviorEventId + CREATED WatchIntent.
- `ACT-C2B-OTT-REUSED`: current click event outcome ACTIVE_REUSED; click-only, downstream 복제 없음.
- `ACT-C2B-OTT-ALREADY`: current click event outcome ALREADY_WATCHED; WatchIntent null, click-only.
- `ACT-C2B-WRONG-MOVIE`: item A + C1 click event movie B, 409.
- `ACT-C2B-OTHER`: 다른 owner recommendationItemId, 404.
- `CHAIN-C2B-RATED`: item B→OTT action→WatchIntent→ViewingRecord→Rating.
- `CHAIN-C2B-NONE`: exposure 뒤 action 없음; projection/outcome row 없음.
- `CHAIN-C2B-MULTI-ACTION`: 같은 item의 DETAIL 2회, OTT CREATED/REUSED와 두 C1 chain을 보존한다. projection은
  stage desc, occurredAt asc, actionEventId asc winner 한 chain만 참조하고 equal/lower later action에 흔들리지 않는다.

## REC-EV-013 completed rejection

contract fixture는 REC-EV-013 v1의 `selected:null`, `two_plus_one:null`, `discovery_policy:null`을
전제로 한다. `TWO_PERSONAL_ONE_DISCOVERY`, discovery distance/type/badge는 fixture에 넣지 않는다.
validator가 artifact checksum과 기각 상태를 확인하며 public fixture는 BASELINE_THREE다.

## Error fixture

- missing/invalid bearer
- invalid UUID/timestamp, duplicate/non-contiguous position
- exact idempotency replay와 body reuse conflict
- FastAPI timeout/503, candidate checksum failure, DB unavailable
- stale delivery, cross-owner delivery/item
- client forged exposedAt/occurredAt field rejected; internal version/checksum/hash absent
- duplicate C1 click behaviorEventId across recommendation items conflicts while distinct clicks may reuse WatchIntent
- C1 click commit 후 C2B action fault rollback, header/body dual-id conflict, different-key concurrent single winner
- exposure header/body sorted advisory lock 전후 crash injection: header/exposure/link/canonical domain result 전부 0 또는 전부 1
- original 201/replayed=false와 replay 200/replayed=true의 canonical domain payload 동일성 및 stored envelope 부재
- different header keys + same exposureBatchId concurrent same body는 한 mutation과 두 safe replay header records,
  body drift는 409
- eligibility/collection lock race: Rating-first와 append-first 모두 exact item 종료·unrelated existing item 유지·single append delta로 수렴
- Catalog singleton lock race: C0 activation first/new state, C2B final-check commit first/current result+next stale

## 누적 collection·append·종료 fixture

- `COL-C2B-INITIAL`: ACTIVE A/B/C, sequencePosition 1/2/3, revision 1, signed cursor offset 3
- `COL-C2B-APPEND-1`: same collection에 D/E/F가 4/5/6으로 append, A/B/C ID·position 불변, revision 2
- `COL-C2B-APPEND-REPLAY`: same cursor/event/key replay가 D/E/F delta와 revision 2를 반환하고 row 증가는 0
- `COL-C2B-APPEND-RACE`: revision 2에서 concurrent append 두 개, single winner G/H/I position 7/8/9
- `COL-C2B-CURSOR-STALE/TAMPERED/CROSS-ACTOR`: mutation 없이 409/400/404, existing ACTIVE set 불변
- `COL-C2B-RATED`: B에 Rating 1 또는 5 commit 후 B=`COMPLETED_RATED`, GET은 A/C, satisfaction row 0
- `COL-C2B-WATCHED-ONLY`: C ViewingRecord만 commit, C는 ACTIVE 유지
- `COL-C2B-NOT-INTERESTED`: A explicit event 후 A=`DISMISSED_NOT_INTERESTED`, free-text payload는 400
- `COL-C2B-RATING-DELETE`: B Rating delete/replay 뒤에도 B 종료 유지, duplicate exit row 0
- `COL-C2B-CATALOG-RETIRED`: F UI_READY 상실 뒤 `RETIRED_CATALOG`, user dismiss/rating event 0
- `COL-C2B-APPEND-FAULT`: append statement별 fault injection에서 page/items/offset/revision 모두 이전 상태 또는 모두 commit
- C1 event-before-action inbox: late CREATED action trigger와 periodic reconcile shuffle/replay가 같은 projection hash
- immutable projector event replay/payload drift와 Rating out-of-order/concurrent revisions, higher DELETE tombstone
- ErrorResponse path canary UUID/query/upstream/filesystem path — route template 외 captured output에 부재
- safe log canary token/email/path/Rating value — captured output에 모두 부재
