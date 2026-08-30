# C2B 데이터 사전

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`

## USER_RECOMMENDATION_ELIGIBILITY_VERSION — C1 선행 amendment

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `actor_user_id` | UUID FK | N | PK; service user, public/log 미노출 |
| `version` | bigint | N | 1 이상 monotonic; ViewingRecord 또는 active Rating eligibility mutation마다 +1 |
| `updated_at` | timestamptz | N | C1 mutation DB clock |

C1 mutation은 이 row를 `FOR UPDATE`하고 domain row와 version을 같은 transaction에 commit한다. C2B replay/selection은
`FOR SHARE` lock 아래 version을 시작·종료에 double-read하고 delivery commit까지 유지한다. 없는 row 생성 race도
actor key advisory lock으로 single winner 처리하며 local-memory/asynchronous version은 허용하지 않는다.

## CATALOG_DISCOVERY_ELIGIBILITY_VERSION — C0 선행 amendment

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `singleton_id` | smallint | N | PK, const 1 |
| `version` | bigint | N | Catalog activation/deactivation/UI_READY eligibility mutation마다 +1 |
| `updated_at` | timestamptz | N | C0 mutation DB clock |

C0은 singleton을 `FOR UPDATE`하고 Catalog mutation+version increment를 같은 transaction에 commit한다. C2B는 이
row를 `FOR SHARE`로 먼저, actor eligibility row를 두 번째로 잠그고 final Catalog/C1 check와 version double-read부터
delivery 또는 exposure commit까지 유지한다. C0-first면 새 state, C2B-first면 현재 결과가 commit되고 다음
GET/exposure는 증가 version으로 stale다. amendment 미지원/lock unavailable은 public 503이다.

## RECOMMENDATION_DELIVERY_BATCH

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `delivery_id` | UUID | N | PK, opaque |
| `actor_user_id` | UUID FK | N | owner; 공개 응답·로그 금지 |
| `source_request_id` | UUID | N | internal correlation, dedup key 아님 |
| `delivery_revision` | integer | N | 1 이상, append/exit transaction마다 증가하는 opaque public revision |
| `expires_at` | timestamptz | N | current append cursor 만료 시각 후보 10분; collection 가시성 만료가 아님 |
| `outcome` | enum | N | COMPLETE/PARTIAL/EMPTY |
| `label` | enum | N | 현재 POPULARITY_BASELINE |
| `active_rating_count` | integer | N | 0 이상; 값 자체를 metric label로 사용 금지 |
| `minimum_evidence_k` | integer | N | 현재 10 |
| `personalization_eligible` | boolean | N | K≥10 필요조건 |
| `personalization_applied` | boolean | N | 현재 false constraint |
| `personalization_policy_status` | enum | N | NOT_ENOUGH_ACTIVE_RATINGS/CANDIDATE_BLOCKED |
| `composition` | enum | N | 현재 BASELINE_THREE |
| `recommendation_version` | varchar | N | C2A exact snapshot |
| `mapping_version` | varchar | N | C2A movie mapping artifact exact non-empty version; reuse/stale key |
| `artifact_set_version` | varchar | N | C2A ready set |
| `policy_version` | varchar | N | current ranking policy |
| `ranking_policy` | enum | N | BAYESIAN_POPULARITY_ONLY |
| `ranking_alpha` | numeric | N | 현재 0 constraint |
| `catalog_version` | varchar/FK | N | active Catalog public version |
| `catalog_eligibility_version` | bigint | N | C0 singleton final-check snapshot |
| `candidate_set_version` | varchar | N | immutable active candidate |
| `input_version` | varchar | N | canonical active Rating input |
| `eligibility_version` | bigint | N | current C1 Viewing/Rating exclusion linearization version |
| `composition_version` | varchar | N | 현재 c2b-baseline-three-v1 |
| `item_count` | smallint | N | 누적 selected item 0..500; active+terminal 합 |
| `active_item_count` | smallint | N | status ACTIVE count; response items 수와 일치 |
| `next_scan_offset` | smallint | N | 0..500, 이미 판정한 Top500 prefix 다음 위치 |
| `has_more` | boolean | N | next_scan_offset<500이고 exhausted가 아닐 때 true |
| `created_at` | timestamptz | N | server clock |

actor의 current collection 하나를 재진입 GET으로 복구한다. append cursor는 actor/delivery/revision/typed versions/
next_scan_offset/expiry에 서명 바인딩한다. Rating commit은 exact movie ACTIVE item을 종료하고, Viewing-only 변화는
기존 ACTIVE item을 삭제하지 않는다. Catalog invalid item만 별도 안전 퇴역시킨다. 내부 version/checksum/hash는
stale validation 전용이고 public DTO에는 없다.

## RECOMMENDATION_DELIVERY_ITEM

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `delivery_item_id` | UUID | N | PK, exposure 전 임시 item ID |
| `delivery_id + actor_user_id` | composite FK | N | delivery owner 일치 |
| `movie_id` | UUID FK | N | stable service movie identity |
| `position` | smallint | N | append 순서 1..500, delivery 안 unique/단조; 종료 뒤 gap 유지 |
| `source_rank` | integer | N | C2A rank 1..500, delivery 안 unique |
| `recommendation_type` | enum | N | 현재 POPULARITY_BASELINE |
| `catalog_visible_at_delivery` | boolean | N | true constraint |
| `ui_ready_at_delivery` | boolean | N | true constraint |
| `display_status` | enum | N | ACTIVE/COMPLETED_RATED/DISMISSED_NOT_INTERESTED/RETIRED_CATALOG |
| `status_changed_at` | timestamptz | Y | ACTIVE면 null, terminal/system status면 DB clock |
| `completed_rating_id` | UUID FK | Y | COMPLETED_RATED만 non-null; Rating 값은 저장하지 않음 |
| `created_at` | timestamptz | N | batch와 같은 clock |

`display_status`는 단일 typed 종료 사유다. Rating 제출 commit은 exact actor/movie의 모든 ACTIVE item을
`COMPLETED_RATED`로 바꾸고 Rating delete가 자동 복원하지 않는다. ViewingRecord만 생긴 경우 상태를 바꾸지 않는다.

## RECOMMENDATION_DELIVERY_APPEND

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `append_event_id` | UUID | N | body event PK, actor scope canonical replay |
| `delivery_id + actor_user_id` | composite FK | N | owner collection |
| `expected_revision` | integer | N | request precondition |
| `result_revision` | integer | N | 성공 시 expected+1, EMPTY exhausted no-op replay도 canonical |
| `start_scan_offset`, `end_scan_offset` | smallint | N | 0..500 monotonic range |
| `outcome` | enum | N | COMPLETE/PARTIAL/EMPTY |
| `selected_count` | smallint | N | 0..3 |
| `canonical_request_sha256` | char(64) | N | cursor 원문이 아닌 canonical claims hash 포함 |
| `created_at` | timestamptz | N | DB clock |

append, 신규 item 0..3, issue, collection scan offset/revision, IDEMPOTENCY_RECORD domain result는 정렬된
HEADER/BODY lock 아래 한 transaction이다. 같은 cursor/body/key replay는 같은 delta이고 concurrent winner 하나만
sequence position을 점유한다.

## RECOMMENDATION_ITEM_DISMISSAL

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `dismissal_event_id` | UUID | N | owner-scoped body event PK |
| `delivery_item_id + actor_user_id` | composite FK | N | owner item |
| `reason` | enum | N | const NOT_INTERESTED; 자유 텍스트 금지 |
| `idempotency_key` | UUID | N | `(actor,operation,key)` unique header scope |
| `canonical_request_sha256` | char(64) | N | exact replay/conflict |
| `created_at` | timestamptz | N | DB clock |

동일 item의 최초 valid dismissal만 ACTIVE→DISMISSED_NOT_INTERESTED를 만든다. 이미 COMPLETED_RATED/
RETIRED_CATALOG인 item에는 409이며, 같은 event/key/body replay만 기존 결과를 돌려준다.

## RECOMMENDATION_ACTION

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `action_event_id` | UUID | N | owner-scoped body event ID; `(actor_user_id,action_event_id)` unique |
| `actor_user_id` | UUID FK | N | authenticated actor |
| `recommendation_item_id` | UUID FK | N | exposed item, same owner |
| `movie_id` | UUID FK | N | item movie denormalized with equality constraint/trigger |
| `action_type` | enum | N | DETAIL_OPENED/OTT_OPTION_OPENED |
| `c1_behavior_event_id` | UUID FK | Y | OTT action만 required, global unique current click event |
| `c1_click_outcome` | enum | Y | CREATED/ACTIVE_REUSED/ALREADY_WATCHED; C1 typed projection에서 검증 |
| `watch_intent_id` | UUID FK | Y | CREATED만 non-null; REUSED/ALREADY_WATCHED click-only |
| `occurred_at` | timestamptz | N | client 입력이 아니라 C1 event 또는 DB clock |
| `idempotency_key` | UUID | N | `(actor_user_id,operation,idempotency_key)` unique header scope |
| `canonical_request_sha256` | char(64) | N | exact replay/conflict |
| `created_at` | timestamptz | N | DB clock |

한 recommendation item은 action 0..N이고 `(recommendation_item_id,action_type)` unique는 없다. C1 behavior event만
global unique라 한 click을 여러 action에 붙일 수 없다.

## RECOMMENDATION_DELIVERY_ISSUE

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `append_event_id` | UUID FK | N | initial/append page에 종속 |
| `code` | enum | N | allowlisted safe code, delivery 안 unique |
| `count` | integer | N | 1 이상 aggregate count |
| `retriable` | boolean | N | 세 allowlisted exclusion code 모두 false CHECK |

제외된 개별 candidate ID, raw error, 내부 version/checksum/hash는 저장·반환하지 않는다.
exact code allowlist/precedence는 `CANDIDATE_NOT_UI_READY` → `CANDIDATE_ALREADY_RATED` →
`CANDIDATE_ALREADY_SEEN`이며 append page당 code unique, cardinality 0..3이다.
candidate마다 정확히 하나의 code만 count하고 `page.scanned_count=page.selected_count+sum(count)`를 만족한다.
model mapping missing/extra/duplicate나 sourceRank/set cardinality drift는 issue로 저장하지 않고 503이다.

## Exposure dual idempotency physical record

header ledger는 공통 `IDEMPOTENCY_RECORD`를 재사용한다.

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `actor_user_id + operation_code + idempotency_key` | composite PK | N | header axis; 다른 owner result 조회 금지 |
| `request_hash` | char(64) | N | deliveryId+body event ID+ordered item/position canonical body SHA-256 |
| `domain_result_payload` | jsonb | N | canonical domain fields만; HTTP status와 replayed flag 금지 |
| `original_transport_status` | smallint | N | const 201; audit/mapper 검증용 |
| `resource_id` | UUID | N | operation_code가 식별하는 append/dismiss/exposure/action body event ID |
| `created_at`, `expires_at` | timestamptz | N | 24h retention 후보 |

body ledger는 C2A `RECOMMENDATION_EXPOSURE_BATCH.exposure_batch_id` PK와 `canonical_payload_sha256`다. 두 advisory
lock key `(actor,operation,HEADER,idempotencyKey)`와 `(actor,operation,BODY,exposureBatchId)`를 byte-order 정렬해
transaction-scoped으로 얻고 IDEMPOTENCY_RECORD·exposure batch/items·delivery link·safe result를 같은
`REQUIRES_NEW` transaction에 저장한다. 다른 header key가 same body ID를 써도 same canonical body만 기존 result를
가리키는 새 header record로 replay하며 drift는 409다.
action은 같은 protocol에서 `RECOMMENDATION_ACTION.action_event_id`와 canonical_request_sha256를 body ledger로
사용하고 header record·action·outbox·projection·safe result를 한 transaction에 저장한다.
append와 dismissal도 각각 `RECOMMENDATION_DELIVERY_APPEND.append_event_id`,
`RECOMMENDATION_ITEM_DISMISSAL.dismissal_event_id`를 body ledger로 쓰고 collection revision/item status/domain result를
같은 transaction에 저장한다.

wire mapper는 original commit에만 HTTP 201+`replayed=false`, 모든 same canonical result replay에 HTTP
200+`replayed=true`를 붙인다. stored `domain_result_payload`의 나머지 fields는 canonical JSON 기준 byte-equivalent하게
복원하며 transport envelope를 DB payload로 덮어쓰지 않는다.

## RECOMMENDATION_ATTRIBUTION_PROJECTION

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `recommendation_item_id` | UUID FK | N | PK, owner exposure item |
| `actor_user_id` | UUID FK | N | owner composite FK |
| `movie_id` | UUID FK | N | exposure/action/resource chain과 동일 |
| `highest_stage` | enum | N | DETAIL_OPENED/OTT_OPTION_OPENED/WATCH_CONFIRMED/RATED |
| `winner_stage_rank` | smallint | N | 1 DETAIL, 2 OTT, 3 WATCH, 4 RATED; 감소 금지 |
| `winner_occurred_at` | timestamptz | N | C1 occurredAt 또는 detail DB occurredAt |
| `winner_action_event_id` | UUID FK | N | equal stage tie-break 최소 actionEventId |
| `detail_action_event_id` | UUID FK | Y | exact action |
| `ott_action_event_id` | UUID FK | Y | exact action |
| `watch_intent_id` | UUID FK | Y | exact C1 chain |
| `viewing_record_id` | UUID FK | Y | exact C1 chain |
| `rating_id` | UUID FK | Y | exact C1 chain |
| `rating_revision` | integer | Y | linked Rating revision snapshot |
| `rating_logical_status` | enum | Y | ACTIVE/DELETED snapshot |
| `attribution_policy_version` | varchar | N | c2b-direct-action-chain-v1 |
| `utility_status` | enum | N | 현재 NOT_COMPUTED |
| `observed_relative_utility` | numeric | Y | 현재 null constraint |
| `prediction_error` | numeric | Y | 현재 null constraint |
| `updated_at` | timestamptz | N | projector clock |

projection winner는 `(winner_stage_rank DESC, winner_occurred_at ASC, winner_action_event_id ASC)` 첫 action이다.
higher-stage action이 생기면 winner를 바꿀 수 있지만 equal/lower stage later action은 기존 winner를 바꾸지 않는다.
`detail_action_event_id`는 별도로 DETAIL action 중 `(occurred_at ASC, action_event_id ASC)` 첫 사건을 보존한다.
watch_intent/viewing/rating FK는 winner OTT action이 CREATED일 때만 그 same chain에서 모두 가져오며 서로 다른
action의 FK를 조합하지 않는다. non-winner action과 source ledger는 삭제하지 않는다.

## RECOMMENDATION_ATTRIBUTION_EVENT_LEDGER

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `source_event_id` | UUID | N | append-only PK, immutable dedup claim |
| `actor_user_id + recommendation_item_id` | composite FK | N | owner 일치 |
| `source_type` | enum | N | C1_CLICK/VIEWING/RATING |
| `source_resource_id` | UUID | N | typed resource, API/log 비공개 |
| `source_revision` | integer | Y | Rating만 required, 1 이상 |
| `event_type` | enum | N | CREATED/UPDATED/DELETED allowlist |
| `canonical_payload_sha256` | char(64) | N | same event replay 비교, 원문 저장 금지 |
| `received_at` | timestamptz | N | DB clock |

Rating delete event는 새 highest revision의 DELETED tombstone을 projection에 적용하며 rating value는 어느 C2B
table에도 저장하지 않는다. equal same payload는 noop, equal drift는 dead-letter, lower revision은 stale noop다.
projection update는 recommendation item row lock으로 직렬화한다.

## C1_RECOMMENDATION_SOURCE_EVENT_INBOX

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `source_event_id` | UUID | N | PK, 모든 C1 source event의 최초 immutable dedup claim |
| `actor_user_id`, `movie_id` | UUID FK | N | C1 owner/movie exact chain |
| `behavior_event_id`, `watch_intent_id`, `viewing_record_id`, `rating_id` | UUID FK | Y | source type별 exact resource chain |
| `source_type`, `source_revision`, `event_type` | enum/integer/enum | N/Y/N | deterministic ordering과 tombstone |
| `server_occurred_at`, `received_at` | timestamptz | N | client clock 금지 |
| `canonical_payload_sha256` | char(64) | N | raw payload 금지; same replay 비교 |
| `reconcile_status` | enum | N | PENDING_ACTION/PROJECTED/DEAD_LETTER/EXPIRED_UNATTRIBUTED |
| `recommendation_item_id` | UUID FK | Y | exact CREATED action reconcile 뒤만 non-null |

CREATED action commit이 exact chain reconcile event를 내고 periodic worker도 PENDING_ACTION을 재검사한다. 정렬은
`stageRank ASC, sourceRevision ASC NULLS FIRST, serverOccurredAt ASC, sourceEventId ASC`이며 같은 event/hash 재처리는
noop이다. ACTIVE_REUSED/ALREADY_WATCHED 또는 과거 unrelated WatchIntent에는 연결하지 않는다.
`RECOMMENDATION_ATTRIBUTION_EVENT_LEDGER.source_event_id`는 이 inbox PK의 UNIQUE FK이므로 direct/late path가
같은 source event를 이중 적용할 수 없다.
PENDING_ACTION은 event server time+90d retention 후보까지 재조정하며 이후 EXPIRED_UNATTRIBUTED terminal이다.

## 금지 필드

raw email/token/user bearer, MovieLens user/movie ID, destination URL, raw request/response JSON, 자유 텍스트,
클릭 없음 기반 negative, 관심 없음에서 파생한 numeric Rating, satisfaction boolean은 어느 신규 table에도 두지 않는다.
