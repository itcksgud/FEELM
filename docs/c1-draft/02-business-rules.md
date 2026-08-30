# C1 Rating·Film 업무 규칙

> 상태: `APPROVED`

## 1. 접근·소유권

| ID | 규칙 |
| --- | --- |
| `BR-C1-001` | 모든 C1 operation은 유효한 로그인 사용자만 호출할 수 있다. 인증 없음·무효는 401이다. |
| `BR-C1-002` | actor는 인증 claim에서만 결정한다. request body·path로 임의 userId를 받지 않는다. |
| `BR-C1-003` | WatchIntent·ViewingRecord·Rating·Frame은 소유자만 읽고 변경한다. 다른 소유자의 UUID는 404로 응답한다. |
| `BR-C1-004` | 추천/FastAPI/Spark 장애는 WatchIntent·ViewingRecord·Rating·Film·Popcorn read/write를 실패시키지 않는다. |
| `BR-C1-005` | C1 응답은 MovieLens 사용자 ID, 외부 TMDB/IMDb ID, 내부 userId를 포함하지 않는다. |
| `BR-C1-006` | Film·Popcorn의 타 사용자 공개·비교는 FR-25 계약 전 허용하지 않는다. |

## 2. 멱등성·transaction

| ID | 규칙 |
| --- | --- |
| `BR-C1-010` | 모든 mutation은 `Idempotency-Key`가 필수다. 같은 actor·operation·key·request hash는 최초 status/body를 replay한다. |
| `BR-C1-011` | 같은 actor·operation·key를 다른 canonical body에 재사용하면 409 `IDEMPOTENCY_KEY_REUSED`다. |
| `BR-C1-012` | mutation의 도메인 변경, behavior event, outbox, idempotency 결과는 하나의 PostgreSQL transaction에 기록한다. |
| `BR-C1-013` | Rating mutation의 Rating·Frame·Popcorn·FlavorAggregate·TasteAggregate 변경은 하나의 transaction이다. 하나라도 실패하면 전부 rollback한다. |
| `BR-C1-014` | outbox consumer의 실패는 committed C1 transaction을 되돌리지 않는다. consumer는 eventId로 중복 적용을 막고 재시도한다. |
| `BR-C1-015` | client의 `expectedRevision`이 현재 resource revision과 다르면 409 `REVISION_CONFLICT`이며 상태를 바꾸지 않는다. |

## 3. OTT 클릭·감상 확인

| ID | 규칙 |
| --- | --- |
| `BR-C1-020` | OTT 외부 페이지를 열기 직전에 클릭 transaction을 성공시킨다. 결과가 `CREATED` 또는 `ACTIVE_REUSED`면 WatchIntent와 destination을, `ALREADY_WATCHED`면 destination과 기존 감상 상태를 반환한다. 저장 실패 시 외부 이동을 자동 완료로 기록하지 않는다. |
| `BR-C1-021` | 링크 클릭만으로 ViewingRecord, Rating, Frame, Popcorn, 취향 aggregate를 만들지 않는다. |
| `BR-C1-022` | 새 WatchIntent는 server가 기록한 최초 active `clickedAt`으로 `confirmationDueAt=clickedAt+48h`, `expiresAt=clickedAt+7d`를 계산한다. due는 inclusive, expiry는 exclusive다. |
| `BR-C1-023` | 같은 user/movie에 `LINK_CLICKED` 또는 `CONFIRMATION_PENDING` intent가 있으면 다른 Idempotency-Key의 실제 재클릭도 기존 intent를 `ACTIVE_REUSED`로 반환한다. 기존 intent의 provider/offer, `clickedAt`, due, expiry, status, revision은 갱신하지 않고 현재 클릭 provider/offer를 담은 `OTT_LINK_CLICKED` event만 한 건 추가한다. |
| `BR-C1-024` | `watched=true` 응답은 감상 사실만 확인하며 Rating을 강제 생성하지 않는다. 사용자·영화당 ViewingRecord는 최대 1개다. |
| `BR-C1-025` | `watched=false` 응답은 사실 event로 저장하되 `DN-C1-005` 전에는 부정 취향·싫어요 신호로 사용하지 않는다. |
| `BR-C1-026` | terminal 또는 아직 due가 아닌 intent의 확인 요청은 409 `WATCH_INTENT_NOT_CONFIRMABLE`이며 상태를 바꾸지 않는다. |
| `BR-C1-027` | 가장 최근 intent가 `CONFIRMED_NOT_WATCHED` 또는 `EXPIRED`이고 ViewingRecord가 없으면 후속 실제 클릭은 새 WatchIntent를 만들 수 있다. terminal intent 자체를 재활성화하지 않는다. |
| `BR-C1-028` | ViewingRecord가 이미 있으면 클릭 transaction은 새 WatchIntent를 만들지 않고 `ALREADY_WATCHED`를 반환하며 실제 클릭의 `OTT_LINK_CLICKED` event를 한 건 기록한다. |
| `BR-C1-029` | 같은 actor·operation·Idempotency-Key·body replay는 `CREATED`, `ACTIVE_REUSED`, `ALREADY_WATCHED` 어느 결과에서도 최초 응답을 replay하고 event를 추가하지 않는다. |

## 4. Rating

| ID | 규칙 |
| --- | --- |
| `BR-C1-030` | Rating은 confirmed ViewingRecord가 있는 본인 영화에만 생성할 수 있다. 수동 감상 생성은 `DN-C1-004` 전 C1 범위 밖이다. |
| `BR-C1-031` | Rating 값은 정수 1~5다. decimal, 0, 6, null은 400 validation error다. |
| `BR-C1-032` | 온보딩 LIKE/DISLIKE를 일반 Rating으로 저장하거나 C1 aggregate에 직접 합치지 않는다. |
| `BR-C1-033` | 사용자·영화당 활성 Rating은 최대 1개다. 같은 resource PUT은 create 또는 update이며 revision을 증가시킨다. |
| `BR-C1-034` | 재감상을 별도 ViewingRecord·Rating·Frame으로 생성하지 않는다. |
| `BR-C1-035` | Rating create 직후 ViewingRecord의 공개 상태는 `RATED_COMPLETED`이며 Frame·Popcorn을 정확히 하나 생성한다. |
| `BR-C1-036` | Rating update는 기존 값의 aggregate 기여를 제거하고 새 값의 기여를 같은 transaction에서 반영한다. Frame·Popcorn identity는 유지한다. |
| `BR-C1-037` | Rating delete는 Rating을 `DELETED`로 soft-delete하고 감사 metadata를 유지한다. ViewingRecord는 `WATCHED_CONFIRMED`로 되돌리고 Frame·Popcorn·Rating contribution을 공개 활성 projection에서 제거하며 Flavor/Taste aggregate를 같은 transaction에서 역산한다. `RATING_DELETED` event·outbox·idempotency 결과도 같은 transaction이다. |
| `BR-C1-038` | delete commit 뒤 삭제 Rating은 활성 조회·추천 입력에 없고 `(active Rating↔Frame↔Popcorn)` 1:1과 aggregate count/sum이 일치해야 한다. rollback이면 Rating·ViewingRecord·Frame·Popcorn·aggregate·event·outbox가 모두 이전 상태다. |
| `BR-C1-039` | write response의 `derivedState`는 transaction 완료 뒤의 Frame·Popcorn·aggregate 반영 상태만 반환한다. 비동기 추천 갱신 완료로 표현하지 않는다. |

## 5. Film·Popcorn·취향 집계

| ID | 규칙 |
| --- | --- |
| `BR-C1-040` | Film은 사용자의 모든 활성 Frame 모음이며 period로 잘라 별도 Film처럼 세지 않는다. |
| `BR-C1-041` | Frame은 활성 Rating이 있는 ViewingRecord에서만 존재하고 `(userId,movieId)`가 unique다. |
| `BR-C1-042` | Popcorn은 Frame과 1:1이며 하나의 `flavorId`를 가진다. C1 rating-eligible 영화는 active projection의 `visibilityStatus=UI_READY`이고 active mapping version에 `MovieFlavorAssignment`가 정확히 하나다. `UI_READY`가 C0 catalog-visible 최소 요건을 상위 충족한다. |
| `BR-C1-043` | Flavor `count`는 활성 Popcorn 수, `averageRating`은 해당 활성 Rating 합/개수다. 둘을 하나의 선호 점수로 합치지 않는다. |
| `BR-C1-044` | aggregate 입력이 0이면 row를 제거하거나 `ratingCount=0, averageRating=null`로 제공한다. `averageRating=0`을 만들지 않는다. |
| `BR-C1-045` | TasteAggregate는 승인된 dimension의 source ID별 `ratingCount`, `ratingSum`, 계산 average를 보존한다. 최종 취향 점수·키워드는 `DN-C1-007` 전 제공하지 않는다. |
| `BR-C1-046` | Rating 시점의 `catalogVersion`, `flavorMappingVersion`, `derivationVersion`을 contribution에 기록한다. Catalog 갱신을 사용자 행동처럼 해석해 집계를 몰래 바꾸지 않는다. |
| `BR-C1-047` | Film·Ratings·Unrated 목록은 opaque cursor와 안정적인 tie-breaker를 사용한다. 기본 정렬은 관련 일시 내림차순, 최종 `movieId` 오름차순이다. |
| `BR-C1-048` | Rating 0건, Frame 0건, Popcorn 0건은 200과 빈 목록·0 count로 반환한다. |
| `BR-C1-049` | flavor mapping v1은 primary TMDB genre(`displayOrder=0`)를 `ADRENALINE/WONDER/JOY/HEART/SHADOW/REAL/LEGACY/RHYTHM`에 승인 표대로 매핑한다. unknown·genre 0개·assignment 0개/복수는 Catalog publish quality Gate를 실패시키며, 개선은 새 version으로만 한다. |

## 6. 사용자 평가·행동 기록

| ID | 규칙 |
| --- | --- |
| `BR-C1-050` | 실제 user mutation마다 allowlist event를 같은 transaction에 append한다: `OTT_LINK_CLICKED`, `WATCH_CONFIRMATION_RESPONDED`, `RATING_CREATED`, `RATING_UPDATED`, `RATING_DELETED`. 다른 key의 실제 OTT 재클릭은 intent 상태를 바꾸지 않아도 새 클릭 event 한 건이다. |
| `BR-C1-051` | event는 `eventId`, actor pseudonymous ID, occurredAt, eventType, resource type/ID, traceId, schemaVersion, allowlist payload만 가진다. Authorization·외부 응답·자유 로그 body를 넣지 않는다. |
| `BR-C1-052` | UserBehaviorEvent는 audit·분석 입력이며 WatchIntent·ViewingRecord·Rating의 현재 상태를 대신하지 않는다. |
| `BR-C1-053` | 같은 key idempotent replay는 새 behavior event를 만들지 않는다. 최초 요청이 실제 클릭이면 outcome과 무관하게 클릭 event 한 건만, 다른 상태 변경이면 해당 allowlist event 한 건만 만든다. |
| `BR-C1-054` | behavior event의 학습 사용과 보존 기간은 `DN-C1-006` 승인 전 운영 배포 Gate다. |

## 7. 오류·빈 상태

| ID | 규칙 |
| --- | --- |
| `BR-C1-060` | 요청 값 오류는 field error가 있는 400 `VALIDATION_ERROR`다. |
| `BR-C1-061` | 없는 resource와 다른 사용자의 resource는 모두 404 `RESOURCE_NOT_FOUND`다. |
| `BR-C1-062` | DB transaction을 시작하거나 완료할 수 없으면 503 `RATING_SERVICE_UNAVAILABLE`; partial success body를 반환하지 않는다. |
| `BR-C1-063` | 409 충돌은 안정적인 code와 최신 revision을 다시 읽을 수 있는 안전한 message를 제공한다. |
| `BR-C1-064` | 모든 오류는 traceId를 포함하고 token, userId, rating value를 message에 포함하지 않는다. |
