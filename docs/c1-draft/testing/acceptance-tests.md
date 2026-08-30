# C1 Rating·Film Acceptance Test

> 상태: `APPROVED`  
> Fixture: `testing/fixtures.md`

## 1. OTT 클릭·WatchIntent

| ID | Given / When / Then |
| --- | --- |
| `AC-C1-001` | Given owner와 유효한 C0 offer, When createWatchIntent, Then 201 WatchIntent·외부 destination을 반환하고 OTT_LINK_CLICKED event를 한 건 기록한다. |
| `AC-C1-002` | Given 새 WatchIntent, When transaction commit, Then ViewingRecord·Rating·Frame·Popcorn·취향 aggregate는 생성되지 않는다. |
| `AC-C1-003` | Given 같은 actor·operation·key·body, When create를 재시도, Then 최초 응답을 replay하고 intent·event를 추가하지 않는다. |
| `AC-C1-004` | Given 사용된 key, When 다른 offer body로 재사용, Then 409 IDEMPOTENCY_KEY_REUSED이고 상태가 변하지 않는다. |
| `AC-C1-005` | Given 공개 불가 movie 또는 현재 검증 불가 offer, When create, Then 존재 여부를 과도하게 노출하지 않는 404다. |
| `AC-C1-006` | Given 같은 user·movie의 active intent, When 다른 key로 실제 재클릭, Then `ACTIVE_REUSED`와 같은 intent ID를 반환하고 clickedAt/due/expiry/revision은 그대로이며 `OTT_LINK_CLICKED` event만 한 건 추가한다. |
| `AC-C1-007` | Given WI-PENDING과 WI-LINK, When pending 목록 조회, Then due인 WI-PENDING만 안정적 순서로 반환한다. |
| `AC-C1-008` | Given terminal·expired intents, When pending 목록 조회, Then 모두 제외한다. |
| `AC-C1-009` | Given WI-EXPIRED, When 취향·추천 입력 조회, Then 감상·평가·부정 선호 기여가 0건이다. |
| `AC-C1-010` | Given CLICK-T0, When scheduler 경계를 통과, Then dueAt은 정확히 T0+48h, expiresAt은 T0+7d이고 due/expiry 각각 boundary-1ms/boundary/boundary+1ms에서 pending 포함·제외를 검증한다. |

## 2. 감상 확인·지연 평가

| ID | Given / When / Then |
| --- | --- |
| `AC-C1-011` | Given WI-PENDING, When watched=true, Then terminal CONFIRMED_WATCHED와 ViewingRecord를 만들고 Rating·Frame·Popcorn은 만들지 않는다. |
| `AC-C1-012` | Given watched=true 완료, When unrated 목록 조회, Then 해당 영화가 1건 표시된다. |
| `AC-C1-013` | Given 같은 confirmation key/body retry, When 재호출, Then 같은 ViewingRecord를 반환하고 record·event를 추가하지 않는다. |
| `AC-C1-014` | Given WI-PENDING, When watched=false, Then terminal 사실 event를 남기고 ViewingRecord·Rating·부정 선호 기여는 만들지 않는다. |
| `AC-C1-015` | Given due 전·expired·terminal intent, When confirmation 요청, Then 409 WATCH_INTENT_NOT_CONFIRMABLE이고 상태가 변하지 않는다. |
| `AC-C1-016` | Given USER-C1-OTHER의 intent, When owner token으로 확인, Then 404이고 타 사용자 상태가 노출되지 않는다. |
| `AC-C1-017` | Given stale expectedRevision, When confirmation, Then 409 REVISION_CONFLICT이고 event를 만들지 않는다. |
| `AC-C1-018` | Given confirmation transaction 내부 실패, When 요청, Then intent·ViewingRecord·event·outbox·idempotency 결과가 모두 rollback된다. |

## 3. Rating 생성·수정·삭제

| ID | Given / When / Then |
| --- | --- |
| `AC-C1-019` | Given VR-UNRATED와 active v1 flavor assignment exactly 1, When value=4 PUT, Then Rating·Frame·Popcorn·contribution·aggregate·event·outbox를 한 transaction으로 만들고 RATED_COMPLETED다. |
| `AC-C1-020` | Given value 0, 6, 3.5 또는 null, When PUT, Then field error가 있는 400이며 상태가 변하지 않는다. |
| `AC-C1-021` | Given confirmed ViewingRecord가 없는 영화, When PUT, Then 409 WATCH_CONFIRMATION_REQUIRED이며 수동 감상 사실을 발명하지 않는다. |
| `AC-C1-022` | Given 같은 create key/body retry, When PUT, Then Rating·Frame·Popcorn이 각 1개이고 behavior event가 추가되지 않는다. |
| `AC-C1-023` | Given RATING-ONE value=4 revision=2, When value=5 expectedRevision=2, Then Rating revision=3, 같은 Frame/Popcorn ID, aggregate sum이 +1이다. |
| `AC-C1-024` | Given update expectedRevision=1, When 현재 revision=2, Then 409 REVISION_CONFLICT이고 value·aggregate가 유지된다. |
| `AC-C1-025` | Given RATING-ONE, When DELETE, Then Rating은 DELETED+deletedAt+deletionTraceId, VR-RATED는 WATCHED_CONFIRMED, Frame·Popcorn·contribution은 제거, SHADOW count/ratingCount/ratingSum은 0으로 역산되고 delete event·outbox까지 한 transaction이다. |
| `AC-C1-026` | Given 성공한 DELETE의 같은 key retry, When 재호출, Then 최초 결과 replay이고 delete event·aggregate delta가 중복되지 않는다. |
| `AC-C1-027` | Given recommender/FastAPI 중단, When Rating PUT, Then C1 transaction은 성공하고 recommendationRefresh=QUEUED다. |
| `AC-C1-028` | Given FAIL-AFTER-RATING/POPCORN 또는 delete aggregate 역산 실패, When mutation, Then 이전 Rating·ViewingRecord·Film·Popcorn·aggregate·event·outbox 상태가 완전히 유지된다. |
| `AC-C1-029` | Given 최초 mutation과 replay, When event 검사, Then 최초 상태 변경에만 allowlist event 한 건이 있다. |
| `AC-C1-030` | Given OnboardingPreference LIKE/DISLIKE, When C1 aggregate 조회, Then 일반 Rating count/sum에 포함되지 않는다. |
| `AC-C1-031` | Given 영화에 flavor assignment가 없음, When Rating create, Then 409 FLAVOR_ASSIGNMENT_REQUIRED이고 Rating부터 partial 저장하지 않는다. |

## 4. Film·Popcorn·Taste 조회

| ID | Given / When / Then |
| --- | --- |
| `AC-C1-032` | Given RATING-ONE, When Film 조회, Then 전체 active Frame count=1이고 기간 Film로 오표기하지 않는다. |
| `AC-C1-033` | Given 여러 Frame과 cursor, When 같은 revision·조건으로 순회, Then 중복·누락 없이 일시 내림차순·movieId tie-breaker다. |
| `AC-C1-034` | Given FRAME-ONE, When 상세 조회, Then 영화 요약·내 Rating·감상 확인일·provider만 반환하고 userId·외부 ID·한줄평은 없다. |
| `AC-C1-035` | Given 다른 사용자 Frame, When 조회, Then 404다. |
| `AC-C1-036` | Given Rating/Frame/Popcorn 0건 사용자, When 각 목록 조회, Then 200, items 빈 배열, totalCount=0이다. |
| `AC-C1-037` | Given SHADOW aggregate, When bucket 조회, Then code=SHADOW, displayName=긴장, count=1과 averageRating=4.0을 별도 field로 반환한다. |
| `AC-C1-038` | Given HEART count=0, When bucket 조회, Then displayName=여운, averageRating=null이며 0점이 아니다. |
| `AC-C1-039` | Given C1 raw aggregate, When taste profile 조회, Then GENRE/COUNTRY/DIRECTOR의 count·average만 제공하고 미승인 score·keyword·ERA는 없다. |
| `AC-C1-040` | Given Catalog version 교체, When C1 조회, Then 기존 contribution version과 aggregate가 사용자 행동 없이 자동 변경되지 않는다. |
| `AC-C1-041` | Given active 상태, When invariant query, Then Frame count=Popcorn count=파생 대상 active Rating count이고 orphan 0건이다. |

## 5. 보안·행동 기록·장애

| ID | Given / When / Then |
| --- | --- |
| `AC-C1-042` | Given token 없음, When 모든 C1 operation 호출, Then 401이며 개인 데이터가 없다. |
| `AC-C1-043` | Given invalid token, When 호출, Then 익명 downgrade 없이 401이다. |
| `AC-C1-044` | Given 정상 C1 응답, When schema 검사, Then userId·MovieLens/TMDB/IMDb raw ID가 없다. |
| `AC-C1-045` | Given PostgreSQL 불가, When mutation·조회, Then 503 RATING_SERVICE_UNAVAILABLE와 traceId이고 partial success가 없다. |
| `AC-C1-046` | Given API·worker 오류, When log 검사, Then Authorization·email·raw external body·rating value가 message/metric label에 없다. |
| `AC-C1-047` | Given C1 mutation, When behavior event 검사, Then allowlist type/payload만 있고 token·destination URL·자유 텍스트가 없다. |
| `AC-C1-048` | Given behavior event replay, When 현재 상태 조회, Then event를 source of truth처럼 중복 적용하지 않는다. |
| `AC-C1-049` | Given outbox consumer 3회 실패 후 복구, When retry, Then C1 state는 유지되고 eventId당 downstream 적용은 1회다. |
| `AC-C1-050` | Given 다른 query/revision의 cursor, When 재사용, Then 400 INVALID_CURSOR이고 데이터가 섞이지 않는다. |

## 6. Frontend

| ID | Given / When / Then |
| --- | --- |
| `AC-C1-051` | Given watched=true, When “나중에 평가”, Then Frame·Popcorn 성공 animation 없이 unrated 목록에만 표시된다. |
| `AC-C1-052` | Given Rating 선택, When saving 중 중복 click, Then 같은 idempotency key를 사용하고 UI count를 낙관적으로 두 번 늘리지 않는다. |
| `AC-C1-053` | Given transaction 실패, When Rating editor, Then 선택값을 유지하고 Film 반영 성공 화면을 표시하지 않는다. |
| `AC-C1-054` | Given revision conflict, When update, Then 최신 값 refresh 행동을 제공하고 사용자의 stale 값으로 덮어쓰지 않는다. |
| `AC-C1-055` | Given bucket count와 average, When 렌더, Then 색만으로 flavor를 구분하지 않고 `count`와 `내 평균 x/5`를 각각 읽을 수 있다. |

## 7. 승인 결정 회귀·Catalog quality Gate

| ID | Given / When / Then |
| --- | --- |
| `AC-C1-056` | Given 가장 최근 intent가 CONFIRMED_NOT_WATCHED 또는 EXPIRED이고 ViewingRecord 없음, When 새 key로 실제 클릭, Then 새 CREATED intent와 새 clickedAt+48h/+7d 시각 및 클릭 event 한 건을 만든다. |
| `AC-C1-057` | Given ViewingRecord가 이미 있음, When 새 key로 OTT 클릭, Then `ALREADY_WATCHED`, watchIntent=null, 새 intent 0건, 클릭 event 한 건이며 같은 key replay는 event를 추가하지 않는다. |
| `AC-C1-058` | Given v1 매핑 seed, When 19개 승인 TMDB genre ID를 검사, Then 각 primary ID는 승인된 8개 안정 코드 중 정확히 하나와 승인 표시명에 대응한다. |
| `AC-C1-059` | Given active projection의 visibilityStatus=UI_READY 후보가 unknown/genre 0개/assignment 0개/복수 중 하나, When Catalog publish Gate, Then publish가 실패하고 C1 rating-eligible로 노출되지 않는다. |
