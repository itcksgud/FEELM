# C2B Acceptance Criteria

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`  
> 로컬 구현 승인 AC: `AC-C2B-001`, `AC-C2B-012`, `AC-C2B-014`, `AC-C2B-015`, `AC-C2B-020`,
> `AC-C2B-063`, `AC-C2B-087`, `AC-C2B-088`, `AC-C2B-089`, `AC-C2B-090`, `AC-C2B-092`, `AC-C2B-093`,
> `AC-C2B-094`, `AC-C2B-095`, `AC-C2B-096`, `AC-C2B-097`  
> 확장 차단 AC: 개인화·XAI·예상 별점·exposure/action·production을 검증하는 나머지 AC.
> 차단 AC는 설계 안전성 기록이며 현재 구현 완료를 뜻하지 않는다.
> 로컬 승인 AC는 구현·자동화 대상이고, 나머지 AC는 추가 승인 전 PASS를 주장하지 않는다.

## 인증·입력

| ID | Given / When / Then |
| --- | --- |
| `AC-C2B-001` | missing bearer로 각 operation 호출 시 401이고 body에 내부 actor 정보가 없다. |
| `AC-C2B-002` | invalid bearer 호출 시 401이며 token 원문이 응답·로그에 없다. |
| `AC-C2B-003` | 다른 owner delivery/item 호출 시 존재 여부를 숨기는 404다. |
| `AC-C2B-004` | request body/path에 actor-like field를 추가하면 400이며 인증 actor를 덮어쓰지 못한다. |
| `AC-C2B-005` | active Rating input에 deleted/unrated/watched=false/onboarding을 섞어도 K/inputVersion에서 제외된다. |
| `AC-C2B-006` | FastAPI request에는 service movie UUID/rating tuple/version만 있고 user ID·bearer·email·raw behavior가 없다. |
| `AC-C2B-007` | invalid UUID/timestamp/action conditional field는 400이고 mutation이 없다. |

## Ranking·K·3편

| ID | Given / When / Then |
| --- | --- |
| `AC-C2B-008` | K0 delivery는 eligibility/applied false, label POPULARITY_BASELINE, alpha0이다. |
| `AC-C2B-009` | K9도 같은 fallback label이고 개인화 부족을 dislike로 표현하지 않는다. |
| `AC-C2B-010` | K10은 eligibility true이나 decision 전 applied false와 CANDIDATE_BLOCKED다. |
| `AC-C2B-011` | K10 alpha0.2를 public response/rank에 임의 적용하지 않는다. |
| `AC-C2B-012` | 최초 Top500 scan/backfill 뒤 3개를 선택하면 active collection은 3개이고 position 1..3이다. |
| `AC-C2B-013` | Top500 소진 뒤 안전 후보 1~2개면 PARTIAL이고 중복/stale/unmapped item으로 채우지 않는다. |
| `AC-C2B-014` | 안전 후보 0개면 EMPTY/items[]이며 fake baseline card가 없다. |
| `AC-C2B-015` | 모든 item type은 POPULARITY_BASELINE, composition은 BASELINE_THREE다. |
| `AC-C2B-016` | Explore05 policy/code가 response/artifact selection/fallback에 없다. |
| `AC-C2B-017` | expectedStar는 NOT_COMPUTED/null/false/NOT_EVALUATED다. |
| `AC-C2B-018` | public reason 배열은 비어 있고 UI reason 영역이 없다. |

## Delivery·exposure

| ID | Given / When / Then |
| --- | --- |
| `AC-C2B-019` | delivery collection은 page마다 최대 3 typed item을 누적해 전체 최대 500만 저장하고 전체 candidate/raw FastAPI JSON을 저장하지 않는다. |
| `AC-C2B-020` | GET 성공만으로 C2A exposure item이 생성되지 않는다. 재진입 GET은 기존 EXPOSED item의 recommendationItemId를 복구하고 PREPARED item만 render ack 대상이다. |
| `AC-C2B-021` | A/B만 render acknowledgement하면 정확히 2개 exposure item만 저장된다. |
| `AC-C2B-022` | exposure batch/item/delivery link는 한 transaction이고 중간 실패 시 전부 0건이다. |
| `AC-C2B-023` | 같은 idempotency key/body는 같은 recommendationItemId를 replay한다. |
| `AC-C2B-024` | 같은 key의 다른 body는 409이고 기존 batch를 바꾸지 않는다. |
| `AC-C2B-025` | 다른 exposure batch에서 같은 movie는 새 recommendationItemId로 저장된다. |
| `AC-C2B-026` | Catalog/candidate/policy drift 뒤 acknowledgement는 409 STALE이고 exposure가 없다. |
| `AC-C2B-027` | duplicate/non-contiguous/out-of-delivery position은 400이고 일부 저장이 없다. |

## Action·outcome attribution

| ID | Given / When / Then |
| --- | --- |
| `AC-C2B-028` | DETAIL_OPENED exact item action은 watchIntent 없이 저장되고 stage가 DETAIL_OPENED다. |
| `AC-C2B-029` | C1 계약 보완 뒤 OTT_OPTION_OPENED는 same owner/movie current behaviorEventId가 있어야 저장된다. |
| `AC-C2B-030` | 다른 movie/owner C1 behaviorEvent는 409/404이며 action·projection이 없다. |
| `AC-C2B-031` | 같은 actionEvent/idempotency/body는 replay, 다른 body는 409다. |
| `AC-C2B-032` | item→OTT action→CREATED click event→WatchIntent→ViewingRecord→Rating exact chain만 RATED stage로 전이한다. |
| `AC-C2B-033` | 명시 recommendationItemId 없는 같은 영화 사건은 최근 exposure에 last-touch하지 않는다. |
| `AC-C2B-034` | exposure 뒤 사건 0건이면 negative/outcome/projection row를 생성하지 않는다. |
| `AC-C2B-035` | Rating delete 후 과거 RATED 연결은 보존하고 current rating status만 DELETED로 갱신한다. |
| `AC-C2B-036` | utility/predictionError/satisfaction 값은 policy 승인 전 NOT_COMPUTED/null이다. |

## 오류·격리·UI

| ID | Given / When / Then |
| --- | --- |
| `AC-C2B-037` | candidate 일부 invalid라도 backfill 결과 3개면 COMPLETE, 1~2개면 PARTIAL이며 safe issue/count만 반환한다. |
| `AC-C2B-038` | FastAPI timeout/503/artifact failure는 public 503이며 stale delivery를 200으로 쓰지 않는다. |
| `AC-C2B-039` | DB unavailable은 503이고 delivery/exposure/action partial write가 없다. |
| `AC-C2B-040` | C2B 실패가 committed C1 Rating/WatchIntent/Viewing/Frame/Popcorn을 rollback하지 않는다. |
| `AC-C2B-041` | metric/log에는 token, actor/movie/item/request UUID, Rating 값, destination, raw body/path가 없다. |
| `AC-C2B-042` | SCR-C2B-001 initial/append COMPLETE/PARTIAL/EMPTY/409/503와 기존 collection 유지·retry 접근성 상태가 React test에서 구분된다. |
| `AC-C2B-043` | card render 뒤에만 exposure ack하며 ack 실패 시 recommendationItemId를 꾸며내지 않는다. |
| `AC-C2B-044` | 상세/action 실패는 C0 상세을 막지 않고 OTT action 실패는 C1 WatchIntent/외부 이동을 rollback하지 않는다. |
| `AC-C2B-045` | validator가 REC-EV-013 checksum, K10/alpha0.2, positive-injection false, selected/two_plus_one/discovery null을 확인하고 2+1을 차단한다. |

## 블라인드 감사 보완 Gate

| ID | Given / When / Then |
| --- | --- |
| `AC-C2B-046` | C2A Top500은 score 내림차순, 동점 stable service movie UUID 오름차순이며 Spring 선택이 이 순서를 보존한다. |
| `AC-C2B-047` | MovieLens Train-known만인 영화는 제외하지 않고, service C1 ViewingRecord와 active Rating 영화만 seen/rated로 제외한다. |
| `AC-C2B-048` | stale/hidden/seen/rated 후보를 건너뛰고 Top500에서 3편까지 backfill하며 exhausted 뒤 가짜 item을 만들지 않는다. |
| `AC-C2B-049` | 제외 issue가 있어도 최종 3편이면 COMPLETE이고 PARTIAL로 잘못 표시하지 않는다. |
| `AC-C2B-050` | SafeIssue는 allowlisted code/count>=1/retriable만 가지며 candidate/movie/internal version을 노출하지 않는다. |
| `AC-C2B-051` | OpenAPI는 signed cursor+expectedRevision append를 제공하고 page size는 3이며 기존 active item을 교체하지 않는다. |
| `AC-C2B-052` | 재진입 GET은 같은 actor의 active collection을 복구하고 page/exposure를 자동 생성하지 않으며 `private, no-store`다. append cursor TTL 후보는 10분이다. |
| `AC-C2B-053` | cursor TTL 만료 또는 catalog/candidate/policy drift 뒤 append는 409 refresh-required이며 기존 active collection은 유지된다. invalid Catalog item만 안전 퇴역한다. |
| `AC-C2B-054` | exposure/action request의 client exposedAt/occurredAt은 400이고 저장 시각은 DB/C1 server clock이다. |
| `AC-C2B-055` | public delivery/action/error와 safe log에 internal version/checksum/hash가 없다. |
| `AC-C2B-056` | 내부 FastAPI service credential 401/403과 upstream 422는 public 503이고, public user bearer 오류만 401이다. |
| `AC-C2B-057` | 현 C1 response만으로 OTT exact attribution을 구현하지 않고 action task가 BLOCKED다. |
| `AC-C2B-058` | C1 보완 응답은 every click의 behaviorEventId/server occurredAt/outcome/nullable activeWatchIntentId를 제공하고 owner/movie를 server에서 검증한다. |
| `AC-C2B-059` | 같은 C1 behaviorEventId의 다른 recommendation item 재사용은 conflict이고 exact key/body replay만 허용한다. distinct click은 같은 WatchIntent를 재사용할 수 있다. |
| `AC-C2B-060` | ACTIVE_REUSED click은 click-only이며 원래 WatchIntent의 Viewing/Rating을 새 recommendation item에 복제하지 않는다. |
| `AC-C2B-061` | ALREADY_WATCHED click은 activeWatchIntentId null인 click-only이며 과거 Viewing/Rating을 귀속하지 않는다. |

## Transaction·boundary·concurrency 감사 Gate

| ID | Given / When / Then |
| --- | --- |
| `AC-C2B-062` | C1 click/WatchIntent가 commit된 뒤 C2B action 별도 transaction을 fault-inject해 실패시키면 C1 row/event는 유지되고 C2B action/projection은 0건이다. |
| `AC-C2B-063` | collection item에 ViewingRecord만 생겨도 기존 active card는 남고, 다음 append 후보에는 같은 movie가 중복되지 않는다. |
| `AC-C2B-064` | append와 C1 Rating이 concurrent이면 같은 eligibility/collection lock을 쓴다. 어느 commit 순서에서도 exact movie item은 COMPLETED_RATED, unrelated 기존 item과 성공한 append delta는 유지된다. |
| `AC-C2B-065` | rank request는 active immutable store의 첫 min(500,total) UUID를 exact once로 보내고 response의 missing/extra/duplicate UUID는 public 503이며 delivery가 없다. |
| `AC-C2B-066` | valid rank response는 same UUID set exact once, sourceRank 1..N<=500이고 tie-break가 고정된다. |
| `AC-C2B-067` | 한 candidate가 UI_READY=false+RATED+SEEN이어도 `CANDIDATE_NOT_UI_READY`만 증가한다. RATED+SEEN은 `CANDIDATE_ALREADY_RATED`만 증가하고 exact enum 외 code는 schema/DB에서 거부한다. |
| `AC-C2B-068` | 모든 page outcome에서 scannedCount=selectedCount+excludedCount, selectedCount=appendedItems.length, excludedCount=sum(issue.count)며 active collection count와 혼합하지 않는다. |
| `AC-C2B-069` | issues는 allowlist 세 code를 최대 한 번씩, 최대 3개, positive count만 반환하며 policy blocked/upstream drift를 issue로 섞지 않는다. |
| `AC-C2B-070` | header Idempotency-Key same+body same은 replay, header key/body drift와 body event ID/other body drift는 409다. exposureBatchId와 actionEventId 모두 검증한다. |
| `AC-C2B-071` | 다른 keys의 같은 owner body event ID concurrent 요청은 mutation single winner이고 same body loser는 replay, drift loser는 409다. 다른 owner 결과는 404로 숨긴다. |
| `AC-C2B-072` | projector가 같은 immutable source event ID/payload를 재수신하면 ledger/projection count·hash가 변하지 않고 payload drift는 dead-letter다. |
| `AC-C2B-073` | Rating revisions를 3,1,2,3 순서와 concurrent 순서로 보내도 revision 3 상태만 남고 equal same은 noop, equal different는 dead-letter다. |
| `AC-C2B-074` | ACTIVE revision 뒤 더 높은 DELETE revision은 DELETED tombstone을 남기며 늦은 ACTIVE/lower revision이 되살리지 않고 Rating numeric 값은 저장하지 않는다. |
| `AC-C2B-075` | 모든 400/401/404/409/503 ErrorResponse.path는 route template allowlist 중 하나이며 raw UUID/query/cursor/upstream/filesystem path가 없다. |
| `AC-C2B-076` | schema/ERD test가 collection item 0..500, append item/issue 0..3, exposure batch item 1..3, delivery item 최대 1 exposure, exposure item action 0..N, C1 event 최대 1 OTT action, WatchIntent multi-click cardinality를 검증한다. |
| `AC-C2B-077` | exposure dual-idempotency transaction 각 statement 뒤 crash를 주입하면 IDEMPOTENCY_RECORD, exposure batch/items, delivery link, canonical domain result가 모두 0 또는 모두 1이다. 다른 header keys/same exposureBatchId concurrent same body는 mutation 하나와 winner domain result replay만 남고 drift는 409다. |
| `AC-C2B-078` | 같은 item의 action을 shuffled/concurrent 순서로 넣어도 projection winner는 `(stageRank DESC, server occurredAt ASC, actionEventId ASC)`와 일치하고 same CREATED chain의 watch/viewing/rating FK만 가지며 non-winner ledger/action은 보존된다. |
| `AC-C2B-079` | C1 eligibility mutation과 C2B append lock 순서를 각각 강제하면 Rating은 exact item 종료, Viewing-only는 기존 item 유지, append는 성공 delta 보존 규칙으로 선형화된다. version double-read drift/lock unavailable은 새 mutation 없는 409/503이다. |
| `AC-C2B-080` | model mapping missing/extra/duplicate 또는 Top500 set/rank drift fixture는 delivery/issue 없이 503이고, valid delivery issue는 exact CANDIDATE_* enum 0..3과 summary 합만 가진다. |
| `AC-C2B-081` | collection migration/schema에 non-empty typed `mapping_version` column이 있다. active mapping version이 바뀌면 append는 409 refresh-required이고 기존 ACTIVE item은 유지되며 public DTO/log에는 version이 없다. |
| `AC-C2B-082` | 동일 append page에서 같은 SafeIssue code를 split insert하거나 retriable=true/count=0을 저장하면 DB/schema가 거부한다. serialization은 code unique stored rows만 사용하고 scanned=selected+excluded, selected=appendedItems.length, excluded=sum(issue.count)를 재검증한다. |
| `AC-C2B-083` | exposure/action original은 201+replayed=false, 같은 canonical body의 same/different header-key replay는 200+replayed=true다. stored canonical domain payload에는 status/replayed가 없고 나머지 wire domain fields는 canonical JSON 기준 동일하며 drift는 409다. |
| `AC-C2B-084` | C0 activation/UI_READY mutation과 C2B delivery/exposure final check를 양 순서로 race하면 Catalog singleton shared/exclusive lock commit 순서대로 C0-first는 새 state, C2B-first는 current result+다음 request stale다. Catalog/C1 version drift 또는 lock protocol 미지원은 mutation 없는 503이다. |
| `AC-C2B-085` | C1 click/viewing/rating event를 CREATED recommendation action보다 먼저 전달한 뒤 action commit·periodic reconcile·중복 replay 순서를 섞어도 immutable inbox가 exact chain만 deterministic backfill하고 최종 ledger/projection hash가 같다. payload drift는 dead-letter이고 REUSED/ALREADY_WATCHED historical chain은 연결되지 않는다. event+90d 뒤 PENDING은 EXPIRED_UNATTRIBUTED terminal이며 더 늦은 action도 backfill하지 않는다. |
| `AC-C2B-086` | exposure request/response items에 duplicate delivery/recommendation item ID, duplicate position, gap, position과 array order 불일치를 각각 넣으면 OpenAPI semantic validator와 DB constraint가 거부한다. valid items는 position 1..N contiguous이며 original/replay에서 같은 순서다. |
| `AC-C2B-087` | 최초 GET은 최대 3편을 만들고 재진입 GET은 server-side ACTIVE item 전체를 sequence position 순으로 복구하며 새 page를 자동 append하지 않는다. |
| `AC-C2B-088` | append 성공은 기존 item ID/position/order를 그대로 두고 뒤에 정확히 최대 3편을 추가한다. React key와 기존 DOM card가 교체되지 않는다. |
| `AC-C2B-089` | 동일 cursor+expectedRevision+appendEventId+Idempotency-Key replay는 같은 delta와 revision을 반환하고 item을 중복 생성하지 않는다. |
| `AC-C2B-090` | 같은 revision의 concurrent append는 한 요청만 commit하고 loser는 canonical replay 또는 409다. 신규 sequence range에 중복·내부 gap이 없다. |
| `AC-C2B-091` | stale/expired/tampered/cross-actor cursor는 409/400/404로 실패하고 기존 collection item·revision을 삭제하거나 교체하지 않는다. cursor claim과 raw 값은 로그에 없다. |
| `AC-C2B-092` | 관심 없음 commit은 해당 owner item만 DISMISSED_NOT_INTERESTED로 바꾸고 GET 목록과 이후 append 후보에서 제외한다. 나머지 item 순서는 유지된다. |
| `AC-C2B-093` | 관심 없음 body는 reason NOT_INTERESTED만 허용하고 free text/Rating/만족도 필드를 거부한다. same event/key replay는 동일 결과, body drift는 409, cross-owner는 404다. |
| `AC-C2B-094` | Rating 제출 commit은 같은 actor/movie의 ACTIVE collection item을 동일 transaction에서 COMPLETED_RATED로 바꾸고 다음 GET/append에서 제외한다. |
| `AC-C2B-095` | Rating 점수 1과 5 모두 동일하게 COMPLETED_RATED이며 어느 값도 관심 없음 또는 추천 만족/실패 label로 변환하지 않는다. |
| `AC-C2B-096` | ViewingRecord/감상 완료만 있고 Rating이 없으면 기존 item은 ACTIVE로 남는다. 무클릭·미평가도 종료 event를 만들지 않는다. |
| `AC-C2B-097` | Rating update/delete/replay는 종료 event를 중복 생성하지 않고 Rating 삭제가 COMPLETED_RATED item을 자동 복원하지 않는다. |
| `AC-C2B-098` | Catalog 비활성·삭제·UI_READY 상실은 item을 RETIRED_CATALOG로 숨기되 관심 없음/평가 완료 event를 만들지 않는다. |
| `AC-C2B-099` | append 503/fault injection은 신규 page/item/offset/revision을 모두 rollback하고 이미 commit된 ACTIVE collection은 유지한다. |
| `AC-C2B-100` | active collection 물리 retention은 DN-C2B-006 승인 전 확정 purge되지 않으며 purge candidate가 ACTIVE item을 평가 완료·관심 없음 없이 소실시키지 않는다. |
