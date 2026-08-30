# C2B 용어와 정책

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`

| 용어 | 정확한 의미 |
| --- | --- |
| `active Rating K` | C2A inputVersion에 포함된 active integer Rating 수. deleted/unrated/watched=false/onboarding은 제외 |
| `personalizationEligible` | K≥10이라는 필요조건을 충족. public personalization 승인이나 실제 적용을 뜻하지 않음 |
| `personalizationApplied` | 승인된 policy/artifact가 실제 순위에 양의 alpha로 적용됨. 현재 항상 false |
| `인기 기준 추천` | C2A `BAYESIAN_POPULARITY_ONLY`, alpha 0 fallback label |
| `내 평가를 반영한 추천` | K10 후보의 public champion 승인 뒤에만 가능한 proposed label |
| `delivery collection` | 최초 3편과 append delta를 누적해 재진입에도 복구하는 actor-owned 서버 목록. 내부 version snapshot은 저장하지만 공개하지 않으며 조회 자체는 exposure가 아님 |
| `append page` | 기존 collection을 바꾸지 않고 뒤에 최대 3편을 원자 추가하는 요청 결과 |
| `retained active item` | 평가 완료, 명시적 관심 없음, Catalog 안전 퇴역 전까지 collection에 남는 추천 item. 감상만으로 사라지지 않음 |
| `COMPLETED_RATED` | 사용자가 Rating 제출을 완료해 목록과 이후 후보에서 제외된 상태. 추천 만족/실패를 뜻하지 않음 |
| `NOT_INTERESTED` | 사용자가 명시적으로 누른 별도 dismiss 사건. Rating 값·무반응·만족도와 동일하지 않음 |
| `exposure acknowledgement` | React가 실제 render한 delivery item과 position을 서버에 확인한 사건. exposedAt은 DB clock으로 생성 |
| `recommendationItemId` | exposure commit 뒤 발급되는 항목 ID. owner/movie/action chain을 직접 연결 |
| `action` | 명시적 `DETAIL_OPENED` 또는 `OTT_OPTION_OPENED`. action 없음은 negative가 아님 |
| `exact attribution` | 같은 actor/movie의 recommendationItemId와 명시적 action/resource chain으로 연결. last-touch 추측 없음 |
| `outcome stage` | EXPOSED→DETAIL_OPENED→OTT_OPTION_OPENED→WATCH_CONFIRMED→RATED 중 관측된 최고 단계 |
| `observedRelativeUtility` | pre-Rating profile 기반 결과 후보. 공식 승인 전 `NOT_COMPUTED` |
| `discovery slot` | 승인된 2+1 정책의 세 번째 item. REC-EV-013에 feasible policy가 없어 현재 존재하지 않음 |
| `C1 click behavior event` | 한 번의 OTT click마다 C1이 발급할 eventId·server occurredAt·outcome. 현 C1 응답에는 없어 C2B OTT attribution은 차단됨 |
| `Top500 boundary` | active immutable candidate store의 앞에서부터 최대 500개를 정확히 한 번 rank하는 내부 요청·응답 집합 경계 |
| `selectionSummary` | 최종 scan의 scanned/selected/excluded count. scanned=selected+excluded이고 excluded=SafeIssue count 합 |
| `eligibility version` | C1 Viewing/Rating eligibility mutation과 C2B replay selection이 같은 actor row lock으로 직렬화하는 monotonic version |
| `mapping version` | C2A candidate UUID를 service movie에 연결한 mapping artifact의 exact typed version. delivery reuse/stale key이며 public 미노출 |
| `Catalog eligibility version` | C0 activation/UI_READY mutation과 C2B final check/commit을 singleton row lock으로 선형화하는 monotonic version |
| `projection winner` | item의 반복 action 중 stage desc, server occurredAt asc, actionEventId asc로 결정되는 singular exact chain |

## K와 label

1. K0~K9: `personalizationEligible=false`, label `인기 기준 추천`.
2. K≥10: `personalizationEligible=true`일 수 있지만 승인 전 `personalizationApplied=false`, label은 계속
   `인기 기준 추천`이다.
3. REC-EV-011 K10 alpha 0.2는 최소 evidence 후보다. K>10 input selection/bucketing과 artifact policy가
   승인되지 않았으므로 K≥10만으로 alpha를 임의 적용하지 않는다.
4. `추천`, `인기 기준 추천`, `내 평가를 반영한 추천`, `새로운 취향 후보`를 서로 바꿔 쓰지 않는다.

## 최초 3편과 누적 append

- C2A ordered Top500을 score 내림차순, 동점은 stable service movie UUID 오름차순으로 읽는다.
- Spring은 active Catalog UI_READY를 재검증하고 collection에 이미 있거나 종료된 영화, 새 후보 중 C1 ViewingRecord 또는 active Rating 영화를 제외한 뒤 순서대로 3편까지 backfill한다. MovieLens Train-known은 서비스 사용자의 seen exclusion이 아니다.
- 최초 요청은 3편, append는 요청마다 최대 3편이다. Top500 boundary를 순서대로 scan해 3개를 선택하면 해당 page는 COMPLETE다. boundary를 소진했는데 1~2개면 PARTIAL이다.
- 0개면 EMPTY다.
- 현재 composition은 `BASELINE_THREE`다.
- 추가 추천은 기존 active item을 교체·재정렬·삭제하지 않는다. 재진입 GET은 모든 active item을 복구한다.
- cursor는 actor, deliveryId, expected revision, typed versions, scan offset, expiry에 바인딩된 opaque signed 값이며 raw user/movie ID를 담지 않는다.
- 같은 cursor/body/key replay는 같은 delta이고 concurrent append는 single winner다. stale cursor/version은 409이며 기존 collection은 유지한다.
- Rating commit 성공은 item을 `COMPLETED_RATED`, 명시적 `관심 없음`은 `DISMISSED_NOT_INTERESTED`로 전이해 목록과 새 append 후보에서 제외한다.
- 감상 완료만으로는 제거하지 않는다. Rating 점수의 높고 낮음도 제거 사유나 추천 만족/실패로 변환하지 않는다.
- Catalog에서 안전하게 표시할 수 없어진 item은 `RETIRED_CATALOG`로 숨기며 두 사용자 상태로 기록하지 않는다.
- REC-EV-013 v1은 모든 후보가 relevance budget을 실패해 `selected:null`이다.
  `TWO_PERSONAL_ONE_DISCOVERY`는 새 version의 evidence와 별도 제품 결정 전 도입하지 않는다.

## Attribution

- deliveryItemId는 exposure 전 임시 식별자이고 외부 actor/movie 정보를 인코딩하지 않는 UUID다.
- exposure acknowledgement는 delivery owner와 item subset/position을 검증하고 기존 C2A exposure batch/item을 쓴다.
- 재진입 GET은 ACTIVE item의 exposureStatus와 이미 발급된 opaque recommendationItemId를 복구한다. EXPOSED item을
  다시 exposure로 중복 commit하지 않고 PREPARED item만 실제 render 뒤 acknowledge한다.
- DETAIL_OPENED는 recommendation item과 동일 movie의 상세 이동만 기록한다.
- OTT_OPTION_OPENED는 C1이 이번 click에 발급한 behaviorEventId를 기록해야 한다. client timestamp나 destination은 받지 않는다.
- 같은 behaviorEventId는 recommendation action 하나에만 연결한다. 같은 WatchIntent가 여러 click에 재사용될 수 있으므로 WatchIntent ID 자체는 dedup key가 아니다.
- CREATED click만 WatchIntent→ViewingRecord→Rating direct attribution 후보가 된다. ACTIVE_REUSED와 ALREADY_WATCHED는 click stage만 기록하며, ALREADY_WATCHED의 WatchIntent는 null이다.
- 여러 exposure가 있어도 명시 recommendationItemId가 없으면 임의 last-touch로 하나를 선택하지 않는다.

## Delivery replay와 privacy

- GET은 current collection의 모든 active item과 다음 opaque cursor를 반환한다. GET만으로 새 page나 exposure를 만들지 않는다.
- append/final commit은 Catalog singleton `FOR SHARE`를 먼저, actor eligibility `FOR SHARE`를 다음에 잠그고 mapping/catalog/C1 typed version을 double-read한다. C0은 activation/UI_READY와 Catalog version+1, C1은 Viewing/Rating과 actor version+1을 각각 `FOR UPDATE` transaction에 commit한다.
- Rating version 변화는 exact movie item을 원자적으로 `COMPLETED_RATED`로 전이한다. 그 밖의 C1 version 변화는 기존 active item을 삭제하지 않고 새 append 후보 exclusion에만 반영한다.
- cursor TTL/version drift는 append를 409로 막고 refresh를 요구하지만 current collection을 삭제하지 않는다. C0 Catalog invalidation만 별도 `RETIRED_CATALOG` 전이를 허용한다.
- 응답은 `Cache-Control: private, no-store`이며 opaque deliveryId·revision·expiresAt만 공개한다. 내부 version/checksum/hash는 응답·로그에 없다.

## SafeIssue와 idempotency

- SafeIssue는 candidate당 정확히 하나만 센다. exact enum precedence는 `CANDIDATE_NOT_UI_READY`, `CANDIDATE_ALREADY_RATED`, `CANDIDATE_ALREADY_SEEN` 순서이며 issue cardinality는 0..3이다.
- SafeIssue는 code-key unique, count positive, retriable=false이고 response/summary는 stored issue rows로부터 재계산한다.
- upstream response 누락·추가·중복·rank 오류는 SafeIssue로 낮추지 않고 503으로 닫는다.
- body event ID와 Idempotency-Key는 서로 대체하지 않는다. actor+operation+key와 owner+event ID 두 축을 모두 잠그며 concurrent duplicate는 single winner 결과만 남긴다.
- exposure는 공통 `IDEMPOTENCY_RECORD` header ledger와 C2A exposure batch body ledger를 정렬된 advisory lock 아래 같은 `REQUIRES_NEW` transaction에 저장한다. header ledger만 또는 exposure만 남는 crash 상태는 허용하지 않는다.
- idempotency에는 canonical domain payload만 저장한다. original은 201+replayed=false, replay는 200+replayed=true이며 transport flag/status 외 domain fields는 canonical JSON 기준 동일하다.
- exposure item은 ID/position 각각 unique이며 array 순서와 같은 position 1..N gap 없는 수열이다.

## Late-action reconcile

- C1 event가 action보다 먼저 도착하면 immutable inbox에 PENDING_ACTION으로 보존한다.
- CREATED OTT action commit과 periodic worker는 exact behaviorEvent/WatchIntent chain만 deterministic ordering으로 reconcile한다.
- same event/hash replay는 noop, drift는 dead-letter이며 ACTIVE_REUSED/ALREADY_WATCHED나 unrelated historical chain을 추측하지 않는다.
- item은 action 0..N을 허용하지만 projection은 `(stageRank DESC, server occurredAt ASC, actionEventId ASC)` winner 한 chain만 참조한다. non-winner action을 삭제하거나 winner와 FK를 섞지 않는다.
