# C2B 업무 규칙

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`

## 인증·입력·격리

| ID | 규칙 |
| --- | --- |
| `BR-C2B-001` | 모든 public draft operation은 required bearer actor만 사용한다. body/path의 user ID로 actor를 선택하지 않는다. |
| `BR-C2B-002` | 다른 actor의 delivery/recommendationItemId/action은 존재 여부를 숨기는 404다. |
| `BR-C2B-003` | FastAPI에는 user ID·user bearer·email·raw behavior를 보내지 않고 C2A candidate UUID와 active Rating snapshot만 보낸다. |
| `BR-C2B-004` | deleted Rating, unrated Viewing, onboarding, watched=false, action 없음은 personalization input이나 negative signal이 아니다. |
| `BR-C2B-005` | C1 Rating mutation은 추천 호출·delivery·outcome projection 실패와 독립적으로 commit한다. |

## Ranking·K·세 슬롯

| ID | 규칙 |
| --- | --- |
| `BR-C2B-010` | K0~9는 `personalizationEligible=false`, `personalizationApplied=false`, label `POPULARITY_BASELINE`이다. |
| `BR-C2B-011` | K10은 개인화라고 부를 수 있는 최소 evidence 필요조건이다. REC-EV-011 alpha 0.2는 offline candidate이며 승인 전 적용하지 않는다. |
| `BR-C2B-012` | K≥10이어도 decision/artifact/input policy가 승인되지 않으면 C2A alpha 0과 `인기 기준 추천`을 유지한다. |
| `BR-C2B-013` | REC-EV-004B Explore05는 상대 NDCG 손실 약 45.5% 때문에 후보에서 제외하고 fallback/discovery로 사용하지 않는다. |
| `BR-C2B-014` | C2A ordered Top500(score desc, tie stable service movie UUID asc)을 active Catalog UI_READY로 재검증하고, collection existing/terminated item과 새 후보의 C1 ViewingRecord·active Rating 영화를 제외해 순서 보존 backfill한다. MovieLens Train-known은 서비스 사용자 seen exclusion이 아니다. 최초/append마다 최대 3편을 선택해 누적하고 page 기준 3편 COMPLETE, boundary 소진 뒤 1~2편 PARTIAL, 0편 EMPTY다. |
| `BR-C2B-015` | REC-EV-013 v1은 모든 constrained 2+1 후보가 relevance budget을 실패했으므로 composition은 `BASELINE_THREE`; discovery item/type/distance를 생성하지 않는다. |
| `BR-C2B-016` | validator는 REC-EV-013 manifest·protocol/result checksum·positive-injection false와 `selected:null`을 확인한다. 새 evidence version과 제품 결정 전 `2+1`은 BLOCKED다. |
| `BR-C2B-017` | expectedStar는 REC-EV-003C 때문에 `NOT_COMPUTED`, value null, displayEligible false다. |
| `BR-C2B-018` | reason UI는 REC-PD-007 전 숨긴다. internal typed reason을 public 문구로 자동 변환하지 않는다. |

## Delivery와 exposure

| ID | 규칙 |
| --- | --- |
| `BR-C2B-020` | 최초/append rank 결과 중 page당 최대 3편의 typed snapshot을 actor-owned delivery collection에 원자 누적한다. collection 전체는 Top500을 넘지 않고, 전체 candidate/raw FastAPI JSON은 저장하지 않는다. |
| `BR-C2B-021` | delivery GET은 exposure가 아니며 `Cache-Control: private, no-store`다. React가 실제 render한 item만 exposure acknowledgement로 commit한다. |
| `BR-C2B-022` | exposure request는 delivery item subset, 실제 연속 position, Idempotency-Key만 가진다. exposedAt은 DB clock으로 생성한다. owner/version/stale 검증 실패는 저장하지 않는다. |
| `BR-C2B-023` | 같은 exposure key+canonical body는 replay하고 다른 body reuse는 409다. 다른 acknowledgement는 같은 영화를 새 recommendationItemId로 보존할 수 있다. |
| `BR-C2B-024` | batch/item은 C2A exposure transaction으로 원자 저장한다. 일부 item만 성공시키지 않는다. |
| `BR-C2B-025` | delivery 생성과 exposure 저장 사이 Catalog/candidate/policy가 바뀌거나 TTL이 끝나면 409 `RECOMMENDATION_DELIVERY_STALE`; stale 성공으로 저장하지 않는다. 공개 응답에는 내부 version/checksum/hash를 싣지 않는다. |

## Action·outcome attribution

| ID | 규칙 |
| --- | --- |
| `BR-C2B-030` | action은 owner recommendationItemId와 같은 movie에만 연결하며 actionEventId로 멱등 처리한다. action 시각은 client가 아니라 DB clock을 사용한다. |
| `BR-C2B-031` | `DETAIL_OPENED`는 상세 진입을 뜻할 뿐 선호·만족·감상을 뜻하지 않는다. |
| `BR-C2B-032` | 현 C1 응답은 이번 click behaviorEventId/occurredAt을 주지 않으므로 OTT exact attribution은 차단한다. C1은 모든 CREATED/ACTIVE_REUSED/ALREADY_WATCHED click에 eventId·server occurredAt·outcome·nullable activeWatchIntentId를 반환해야 한다. URL·destination 원문은 저장하지 않는다. |
| `BR-C2B-033` | CREATED click만 recommendationItem→C1 click event→WatchIntent→ViewingRecord→Rating FK/owner/movie chain으로 direct downstream attribution한다. ACTIVE_REUSED·ALREADY_WATCHED는 click-only이고 ALREADY_WATCHED의 WatchIntent는 null이다. |
| `BR-C2B-034` | 한 C1 click behaviorEventId는 recommendation action 하나에만 연결한다. 같은 WatchIntent에 여러 recommendation item/click이 있어도 downstream outcome을 복제하거나 last-touch하지 않는다. 명시 연결이 없으면 UNATTRIBUTED다. |
| `BR-C2B-035` | outcome stage는 관측 사건에 따라 단조 증가하고 중간 단계를 건너뛸 수 있다. Rating delete는 과거 RATED event를 지우지 않고 현재 rating state를 별도 표시한다. |
| `BR-C2B-036` | Rating 제출 commit은 exact movie의 active collection item을 `COMPLETED_RATED`로 종료한다. action 없음·감상만 완료·Rating 없음은 negative/outcome/종료 row를 자동 만들지 않는다. Rating 값의 높고 낮음은 종료 여부나 만족도 분류에 쓰지 않는다. |
| `BR-C2B-037` | observedRelativeUtility·predictionError·satisfaction은 normalization/confidence policy 승인 전 `NOT_COMPUTED`다. |

## 오류·관측성

| ID | 규칙 |
| --- | --- |
| `BR-C2B-040` | 최초 GET 뒤 append operation은 opaque signed cursor, expectedRevision, Idempotency-Key를 요구한다. invalid body/UUID/cursor 형식은 400, 인증 실패 401, cross-owner 404, stale revision/cursor/idempotency conflict는 기존 collection을 지우지 않는 409다. |
| `BR-C2B-041` | 한 initial/append page의 최종 선택 3개면 제외 issue가 있어도 COMPLETE, 1~2개면 PARTIAL, 0개면 EMPTY다. issue는 allowlisted code·positive aggregate count·retriable만 포함하고 기존 active item 수와 page selectedCount를 혼합하지 않는다. |
| `BR-C2B-042` | FastAPI timeout/503, 내부 service credential 401/403, upstream 422 contract drift, artifact/candidate/config failure, DB unavailable은 public 503이고 실패한 새 delta를 성공처럼 반환하지 않는다. 이미 commit된 active collection은 유지하며 public 사용자 bearer 오류만 401이다. |
| `BR-C2B-043` | active Catalog version과 UI_READY를 호출 전후 검증한다. deleted/stale/unmapped movie를 노출하지 않는다. |
| `BR-C2B-044` | 공개 응답·로그·metric에 token, actor/movie/request/item UUID, Rating 값, destination, raw body, artifact path, internal version/checksum/hash를 넣지 않는다. |
| `BR-C2B-045` | metric label은 operation/outcome/policy/composition/status와 count bucket만 사용한다. |
| `BR-C2B-046` | 운영 승인 후보는 append cursor TTL 10분, terminal/unexposed retention 24시간, exposure/action attribution 90일, idempotency 24시간, version-keyed private/no-store cache다. ACTIVE collection purge 기간은 별도 승인 대상이며 production topology/privacy 측정 전 활성화하지 않고 stale fallback을 비활성으로 유지한다. |
| `BR-C2B-047` | 공개 endpoint와 React 화면은 모든 required decision/evidence가 승인되고 main contract가 병합되기 전 구현하지 않는다. |

## 최종 감사 불변식

| ID | 규칙 |
| --- | --- |
| `BR-C2B-048` | C1 OTT click/WatchIntent transaction은 추천과 무관하게 먼저 독립 commit한다. 이후 C2B action은 별도 transaction에서 committed C1 behaviorEvent를 검증·연결한다. C2B 실패가 C1 commit을 rollback하지 않고, C1 미commit/rollback event를 C2B가 참조하지 않는다. |
| `BR-C2B-049` | collection GET/append 전 current Catalog UI_READY와 C1 eligibility를 같은 lock protocol로 재검증한다. Rating commit은 같은 actor version `FOR UPDATE` transaction에서 exact movie item을 `COMPLETED_RATED`로 전이한다. 감상-only 또는 unrelated version 변화는 기존 active item을 삭제하지 않고 새 append 후보 exclusion에만 반영한다. Catalog invalid item만 `RETIRED_CATALOG`로 숨긴다. |
| `BR-C2B-050` | rank request candidate는 immutable active candidate store의 첫 `min(500,total)` service movie UUID를 순서대로 정확히 한 번 포함한다. response는 같은 UUID 집합을 누락·추가·중복 없이 정확히 한 번 rank하며 `sourceRank 1..N<=500`을 가진다. set/cardinality 위반은 candidate issue가 아니라 503 upstream contract drift다. |
| `BR-C2B-051` | candidate 제외는 candidate당 정확히 하나의 code만 센다. precedence는 `CANDIDATE_NOT_UI_READY` → `CANDIDATE_ALREADY_RATED` → `CANDIDATE_ALREADY_SEEN`이다. model missing/extra/duplicate, invalid rank는 503이고 `PERSONALIZATION_CANDIDATE_BLOCKED`는 personalization state이지 SafeIssue가 아니다. |
| `BR-C2B-052` | 각 initial/append page의 `selectionSummary.scannedCount = selectedCount + excludedCount`, `selectedCount = appendedItems.length`, `excludedCount = sum(issues.count)`다. scannedCount는 해당 page에서 남은 Top500 scan 수, selectedCount는 0..3, issues는 최대 3개 code unique이며 zero count row를 만들지 않는다. collection activeItems count와 selectedCount는 별도다. |
| `BR-C2B-053` | exposureBatchId/actionEventId body ID와 Idempotency-Key header는 별도 불변식이다. idempotency scope는 `(actor,operation,key)`이고 canonical body를 고정한다. body event ID는 owner scope에서 unique다. 같은 key+body는 replay, key reuse/body drift 또는 event ID/body drift는 409다. |
| `BR-C2B-054` | 서로 다른 key가 같은 body event ID로 동시에 들어오면 DB unique/row lock으로 single winner만 mutation한다. canonical body가 같으면 loser는 winner 결과를 replay하고, 다르면 409다. cross-owner event/delivery/item은 존재를 숨기는 404이며 다른 owner의 key/result를 조회하지 않는다. |
| `BR-C2B-055` | attribution projector는 source behavior/rating event ID를 immutable dedup ledger에 먼저 claim하고 recommendation item row를 lock한다. Rating revision은 strictly increasing revision만 적용한다. equal revision+same canonical payload는 noop, equal+different payload는 conflict/dead-letter, lower revision은 stale noop다. delete는 highest revision DELETED tombstone을 남기고 numeric Rating을 저장하지 않는다. |
| `BR-C2B-056` | ErrorResponse.path는 allowlisted route template만 반환한다. 실제 UUID/query/cursor/body 값, upstream URL/path, filesystem path를 message/path/trace에 포함하지 않는다. unknown route도 raw request target 대신 safe template code를 쓴다. |
| `BR-C2B-057` | dual idempotency의 물리 header ledger는 공통 `IDEMPOTENCY_RECORD(actor_user_id,operation_code,idempotency_key)`이다. exposure body ledger는 C2A `RECOMMENDATION_EXPOSURE_BATCH.exposure_batch_id`, action body ledger는 `RECOMMENDATION_ACTION.action_event_id`다. transaction-scoped advisory lock 두 개를 actor+operation+`HEADER:key`와 `BODY:eventId` hash의 정렬된 순서로 잡고, header record·domain mutation·safe replay response를 같은 transaction에 commit한다. exposure는 batch/items/delivery link를 같은 `REQUIRES_NEW`에 포함한다. crash는 전부 commit 또는 전부 rollback이며 다른 key/same event ID의 same canonical body는 winner 결과를 replay하고 drift는 409다. |
| `BR-C2B-058` | 한 recommendation item은 반복 DETAIL/OTT action 0..N을 허용한다. singular projection winner는 모든 action chain의 `(stageRank DESC, C1 occurredAt 또는 detail DB occurredAt ASC, actionEventId ASC)` 순서 첫 action이다. stageRank는 `RATED > WATCH_CONFIRMED > OTT_OPTION_OPENED > DETAIL_OPENED`; ACTIVE_REUSED/ALREADY_WATCHED는 OTT_OPTION_OPENED까지만이다. higher stage가 생기면 winner가 바뀔 수 있으나 stage는 낮아지지 않고, equal stage의 later action은 winner를 바꾸지 않는다. watch/viewing/rating FK는 winner가 CREATED chain일 때만 같은 chain에서 온다. 모든 non-winner action/event도 ledger에 남지만 projection FK를 혼합하지 않는다. |
| `BR-C2B-059` | C0/C1/C2B 동시성의 선형화 시점은 Catalog singleton·actor eligibility version과 collection revision lock을 보유한 transaction commit이다. C1 Rating이 먼저면 exact item 종료 뒤 append, C2B append가 먼저면 새 delta commit 뒤 Rating이 item을 종료한다. 어느 순서도 unrelated 기존 item을 삭제하지 않는다. final double-read drift는 새 append를 409/503으로 막되 기존 collection은 보존한다. |
| `BR-C2B-060` | SafeIssue exact enum은 `CANDIDATE_NOT_UI_READY`, `CANDIDATE_ALREADY_RATED`, `CANDIDATE_ALREADY_SEEN` 세 개뿐이고 delivery issue cardinality는 0..3이다. model mapping missing/extra/duplicate와 rank set/cardinality drift는 issue/PARTIAL이 아니라 public 503이다. glossary·fixture·ERD·DB enum·OpenAPI가 축약 code를 사용하지 않는다. |
| `BR-C2B-061` | collection은 C2A mapping artifact의 exact non-empty `mapping_version` typed column을 저장한다. current active mapping version과 다르면 append는 stale 409 refresh-required이고 기존 ACTIVE item을 삭제하지 않는다. refresh 뒤 새 후보에만 새 mapping을 쓰며 JSON metadata나 recommendation version으로 추론하지 않는다. |
| `BR-C2B-062` | SafeIssue는 DB `(append_event_id,code)` PK와 OpenAPI `x-unique-by: code`로 page/code당 한 row만 허용한다. 세 code의 retriable은 모두 false이고 count는 positive aggregate다. response는 stored rows에서만 만들며 page summary는 scanned=selected+excluded, selected=appendedItems count, excluded=issue count sum을 commit·serialization 양쪽에서 검증한다. |
| `BR-C2B-063` | idempotency record는 HTTP status/`replayed`를 제외한 canonical domain result만 저장한다. original wire는 201+replayed=false, same canonical replay는 200+replayed=true이고 나머지 domain fields는 canonical JSON 기준 동일하다. replay 때문에 stored payload를 변경하지 않는다. |
| `BR-C2B-064` | C0 activation/UI_READY mutation은 singleton `CATALOG_DISCOVERY_ELIGIBILITY_VERSION`을 `FOR UPDATE`하고 mutation+version+1을 같은 transaction에 commit한다. C2B는 Catalog singleton `FOR SHARE` 뒤 actor eligibility `FOR SHARE` 순으로 잠그고 current Catalog/C1 final check와 두 version double-read부터 delivery/exposure commit까지 유지한다. 먼저 commit한 transaction이 선형화되며 protocol 미지원은 503이다. |
| `BR-C2B-065` | 모든 C1 source event는 하나의 immutable inbox에서 먼저 eventId를 claim한다. action보다 먼저 오면 PENDING_ACTION이고, attribution ledger `source_event_id`는 inbox를 UNIQUE FK로 참조한다. CREATED OTT action commit은 exact behaviorEvent/WatchIntent chain reconcile을 enqueue하고 periodic reconcile도 같은 deterministic ordering으로 late events를 ledger/projection에 backfill한다. PENDING_ACTION은 event server time+90d retention 후보까지 재조정하고 이후 EXPIRED_UNATTRIBUTED로 terminal 처리해 더 늦은 action에 추측 연결하지 않는다. same event/hash replay는 noop, drift는 dead-letter이며 ACTIVE_REUSED/ALREADY_WATCHED·unrelated historical chain도 연결하지 않는다. |
| `BR-C2B-066` | exposure request/response item 배열은 item ID와 position이 각각 unique하고 position이 array order와 같은 1부터 시작하는 gap 없는 연속 수열이어야 한다. DB는 `(batch_id,position)`·`(batch_id,delivery_item_id)` unique와 deferred contiguous-count check를 commit 전에 적용한다. |
| `BR-C2B-067` | `DN-C2B-002`의 승인 token은 `BASELINE_THREE_CUMULATIVE_LOAD_MORE_RATED_OR_EXPLICIT_DISMISS`다. 최초 3편과 append page size 3은 discovery 2+1 승인을 뜻하지 않으며 composition은 계속 `BASELINE_THREE`다. |
| `BR-C2B-068` | collection item의 `sequence_position`은 append 시 1..500 단조 증가하며 기존 position을 재번호화하지 않는다. 종료 뒤 gap은 허용하고 화면은 active item만 sequence 순으로 표시한다. exposure page position 1..3은 별도 immutable snapshot이다. |
| `BR-C2B-069` | append cursor는 actor, deliveryId, expected revision, mapping/catalog/candidate/input version, next scan offset, expiry에 서명 바인딩한다. raw actor/movie ID는 노출하지 않고 같은 cursor+body+key replay는 동일 delta, concurrent append는 single winner다. |
| `BR-C2B-070` | append 성공 transaction은 page record, 0..3 신규 collection item, scan offset, revision+1, idempotency domain result를 함께 commit한다. 실패·timeout·409는 기존 active item과 revision을 부분 변경하지 않는다. |
| `BR-C2B-071` | 명시적 관심 없음은 owner-scoped delivery item에 `DISMISSED_NOT_INTERESTED` 사건을 기록한다. 자유 텍스트·Rating 값·만족도는 받지 않고 same key/event replay는 같은 결과, cross-owner는 404다. |
| `BR-C2B-072` | C1 Rating commit은 같은 actor/movie의 모든 current active collection item을 `COMPLETED_RATED`로 원자 전이한다. Rating update/delete replay는 새 종료를 중복 생성하지 않으며 Rating 삭제가 종료 item을 자동 복원하지 않는다. |
| `BR-C2B-073` | active GET은 `ACTIVE` item만 sequence 순서로 반환한다. `COMPLETED_RATED`, `DISMISSED_NOT_INTERESTED`, `RETIRED_CATALOG`는 목록과 이후 candidate에서 제외하되 서로 다른 typed reason으로 감사한다. |
| `BR-C2B-074` | 감상 확인/ViewingRecord만으로 기존 item을 종료하지 않는다. 무클릭·미평가·낮은 별점은 관심 없음으로 추론하지 않고, 평가 완료와 관심 없음 어느 것도 추천 만족/실패 KPI로 자동 변환하지 않는다. |
| `BR-C2B-075` | active collection의 제품 가시성은 평가 완료·관심 없음·Catalog 안전 퇴역 전 유지한다. 물리 retention/purge 기간은 DN-C2B-006 대상이며 purge job이 임의로 active item을 제품 목록에서 소실시켜서는 안 된다. |
