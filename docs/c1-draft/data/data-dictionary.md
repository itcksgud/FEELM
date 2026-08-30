# C1 Rating·Film 데이터 사전 조각

> 상태: `APPROVED`

## 1. Enum

| Enum | 값 | 의미 |
| --- | --- | --- |
| `WatchIntentStatus` | `LINK_CLICKED`, `CONFIRMATION_PENDING`, `CONFIRMED_WATCHED`, `CONFIRMED_NOT_WATCHED`, `EXPIRED` | OTT 클릭과 감상 확인 terminal 상태 |
| `ViewingStatus` | `WATCHED_CONFIRMED`, `RATED_COMPLETED` | 감상 사실과 평가 완료 분리 |
| `RatingLogicalStatus` | `ACTIVE`, `DELETED` | API 논리 상태; 물리 보존은 retention 결정 대상 |
| `TasteDimensionType` | `GENRE`, `COUNTRY`, `DIRECTOR` | C1에서 원천 키가 확정된 집계 dimension |
| `BehaviorEventType` | `OTT_LINK_CLICKED`, `WATCH_CONFIRMATION_RESPONDED`, `RATING_CREATED`, `RATING_UPDATED`, `RATING_DELETED` | state-changing user action allowlist |
| `OutboxStatus` | `PENDING`, `PROCESSING`, `PROCESSED`, `FAILED` | retry 가능한 내부 전달 상태 |
| `FlavorCodeV1` | `ADRENALINE`, `WONDER`, `JOY`, `HEART`, `SHADOW`, `REAL`, `LEGACY`, `RHYTHM` | v1 안정 코드; 표시명과 분리 |

`ERA`와 취향 keyword는 구간·산식이 정해지지 않아 C1 enum에 넣지 않는다.

## 2. WatchIntent

| Field | Type | Null | Source/Rule |
| --- | --- | --- | --- |
| `id` | UUID | N | service-generated public resource ID |
| `user_id` | UUID | N | auth claim, PII 비노출 |
| `movie_id` | UUID | N | C0 stable movieId |
| `provider_id` | UUID | N | C0 providerId |
| `source_offer_id` | UUID | Y | 클릭 시점 offer 추적; retired Catalog와 보존 경계 고려 |
| `status` | enum | N | state machine transition만 변경 |
| `clicked_at` | timestamptz | N | server clock |
| `confirmation_due_at` | timestamptz | N | 최초 active `clicked_at + interval '48 hours'`; 재클릭 불변 |
| `expires_at` | timestamptz | N | 최초 active `clicked_at + interval '7 days'`; 재클릭 불변 |
| `responded_at` | timestamptz | Y | terminal user response 시각; EXPIRED는 scheduler 시각 별도 허용 |
| `revision` | int | N | 1부터 증가, optimistic concurrency |

## 3. ViewingRecord·Rating

| Field | Type | Null | Source/Rule |
| --- | --- | --- | --- |
| `ViewingRecord.id` | UUID | N | service |
| `ViewingRecord.user_id` | UUID | N | owner |
| `ViewingRecord.movie_id` | UUID | N | user별 unique |
| `ViewingRecord.source_watch_intent_id` | UUID | N | watched=true를 만든 intent |
| `ViewingRecord.provider_id` | UUID | N | 확인 경로의 provider |
| `ViewingRecord.status` | enum | N | Rating 유무에 따른 공개 상태 |
| `ViewingRecord.watched_confirmed_at` | timestamptz | N | response commit 시각 |
| `Rating.id` | UUID | N | service |
| `Rating.value` | smallint | N | integer 1~5 |
| `Rating.revision` | int | N | create=1, update/delete마다 증가 |
| `Rating.logical_status` | enum | N | `DELETED`는 active read·추천·projection에서 제외 |
| `Rating.deleted_at` | timestamptz | Y | soft-delete commit 시각 |
| `Rating.deletion_trace_id` | varchar | Y | delete transaction 감사 추적; 일반 API 미노출 |
| `created_at/updated_at` | timestamptz | N | server clock |

## 4. Frame·Popcorn·aggregate

| Field | Type | Null | Rule |
| --- | --- | --- | --- |
| `Frame.id` | UUID | N | Film 공개 frameId |
| `Frame.rating_id` | UUID | N | active 1:1 |
| `Frame.derivation_version` | varchar | N | projection rule version |
| `Popcorn.id` | UUID | N | internal 또는 응답 ID |
| `Popcorn.frame_id` | UUID | N | unique |
| `Popcorn.flavor_id` | UUID | N | mapping version의 단일 assignment |
| `Popcorn.flavor_mapping_version` | varchar | N | 실제 적용된 mapping |
| `MovieFlavorAssignment.mapping_version` | varchar | N | 승인 v1 값은 `v1` |
| `MovieFlavorAssignment.movie_id` | UUID | N | C0 movieId; version과 복합 unique |
| `MovieFlavorAssignment.flavor_id` | UUID | N | v1 FlavorCode reference |
| `MovieFlavorAssignment.source_genre_id` | int | N | TMDB primary genre ID |
| `MovieFlavorAssignment.source_display_order` | int | N | v1은 반드시 0 |
| `FlavorAggregate.popcorn_count` | int | N | 활성 Popcorn 수, 0 이상 |
| `FlavorAggregate.rating_count` | int | N | 평균 분모; C1 invariant에서 popcorn_count와 같음 |
| `FlavorAggregate.rating_sum` | int | N | 0~5*count |
| `TasteAggregate.dimension_key` | varchar | N | GENRE UUID, COUNTRY ISO code, DIRECTOR UUID의 정규 문자열 |
| `TasteAggregate.rating_count` | int | N | 해당 source value에 기여한 활성 Rating 수 |
| `TasteAggregate.rating_sum` | int | N | 해당 기여 Rating 합 |

`averageRating`은 저장 필수 column이 아니라 `rating_count>0 ? rating_sum/rating_count : null` API 파생값이다.

## 5. UserBehaviorEvent

| Field | Type | Null | Rule |
| --- | --- | --- | --- |
| `event_id` | UUID | N | event 중복 방지 key |
| `actor_user_id` | UUID | N | pseudonymous owner; 외부 API 미노출 |
| `event_type` | enum | N | allowlist only |
| `resource_type` | varchar | N | `WATCH_INTENT`, `RATING` |
| `resource_id` | UUID | N | 변경 대상 |
| `occurred_at` | timestamptz | N | 최초 mutation commit 시각 |
| `trace_id` | varchar | N | 안전한 내부 추적 ID |
| `schema_version` | int | N | payload consumer 호환 |
| `payload` | jsonb | N | event별 allowlist; token, URL body, 자유 감상문, raw userId 금지 |

허용 payload 예:

| Event | Payload |
| --- | --- |
| `OTT_LINK_CLICKED` | `movieId`, `providerId`, `linkType` |
| `WATCH_CONFIRMATION_RESPONDED` | `movieId`, `watched` |
| `RATING_CREATED/UPDATED/DELETED` | `movieId`, `ratingRevision`; rating value는 domain row에서 권한 있게 조회 |

## 6. API 파생 필드

| Field | 계산 |
| --- | --- |
| `ratingStatus=UNRATED` | ViewingRecord 존재, active Rating 없음 |
| `ratingStatus=RATED` | active Rating 존재 |
| `film.totalCount` | active Frame 수 |
| `popcornBucket.totalCount` | active Popcorn 수; Film totalCount와 동일 |
| `averageRating` | ratingCount 0이면 null, 아니면 decimal sum/count |
| `isConfirmationDue` | non-terminal intent이고 approved dueAt <= now < expiresAt |

## 7. Flavor mapping v1 reference

| Code | displayName | primary TMDB genre IDs |
| --- | --- | --- |
| `ADRENALINE` | 짜릿함 | 28, 12 |
| `WONDER` | 상상 | 16, 14, 878 |
| `JOY` | 유쾌함 | 35, 10751 |
| `HEART` | 여운 | 18, 10749 |
| `SHADOW` | 긴장 | 80, 27, 9648, 53 |
| `REAL` | 현실 | 99 |
| `LEGACY` | 시대 | 36, 10752, 37 |
| `RHYTHM` | 리듬 | 10402, 10770 |

Catalog quality Gate는 active projection의 `visibilityStatus=UI_READY` 영화마다 active `v1` assignment가 정확히 하나인지,
source genre가 위 allowlist에 있고 `source_display_order=0`인지 검증한다. unknown·genre 0개·0/복수 assignment는
publish를 실패시킨다. `UI_READY`는 C0 catalog-visible 최소 요건을 상위 충족한다.

## 8. PII·보존

- user_id는 pseudonymous UUID지만 개인 취향 데이터와 결합되므로 개인 데이터로 취급한다.
- C1 사용자 응답은 user_id를 반환하지 않는다.
- access log query, error message, metrics label에 movie별 rating value나 userId를 넣지 않는다.
- Rating은 API 삭제 시 soft-delete한다. value 등 감사 row의 장기 익명화·물리 삭제 시점과 behavior/outbox/idempotency TTL은 `DN-C1-006` 승인 전 운영값을 만들지 않는다.
