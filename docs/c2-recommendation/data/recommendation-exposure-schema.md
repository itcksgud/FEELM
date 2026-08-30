# C2 추천 노출 snapshot 데이터 계약

> 상태: `APPROVED_C2A_INTERNAL_POPULARITY_ONLY`  
> 물리 기준: `backend/src/main/resources/db/migration/V5__c2_recommendation_exposure_snapshot.sql`

## 1. 소유권과 transaction

Spring `RecommendationExposureService`만 노출 snapshot을 쓴다. 실제 caller가 표시하기로 선택한
항목을 `REQUIRES_NEW` transaction 한 번에 batch와 item으로 저장한다. FastAPI·Python은 이 DB를
쓰지 않는다. 전체 candidate, FastAPI raw JSON, reason 원문, Rating 값, token은 column이 아니다.

## 2. RECOMMENDATION_EXPOSURE_BATCH

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `exposure_batch_id` | UUID | N | 한 render commit 식별자; PK |
| `source_request_id` | UUID | N | FastAPI correlation; dedup key가 아님 |
| `actor_user_id` | UUID | N | pseudonymous owner; 공개 응답·로그 금지 |
| `recommendation_version` | varchar | N | canonical request+artifact 결정적 version |
| `artifact_set_version` | varchar | N | 검증된 serving set |
| `compatibility_id` | varchar | N | artifact family |
| `policy_version` | varchar | N | ranking policy version |
| `ranking_policy` | enum | N | 현재 `BAYESIAN_POPULARITY_ONLY`만 |
| `ranking_alpha` | numeric | N | 현재 정확히 0 |
| `mapping_version` | varchar | N | service UUID mapping version |
| `catalog_version` | varchar/FK | N | `CATALOG_VERSION.public_version` |
| `candidate_set_version` | varchar | N | immutable batch 후보 version |
| `input_version` | varchar | N | active Rating input snapshot version |
| `*_model_version` | varchar ×4 | N | bias/factors/calibration/mapping |
| `*_payload_sha256` | char(64) ×4 | N | 각 exact payload checksum |
| `attribution_policy_version` | varchar | N | 현재 `c2-direct-item-attribution-v1` |
| `exposed_at` | timestamptz | N | caller가 실제 render commit에 부여한 시각 |
| `item_count` | integer | N | 양수이며 deferred trigger로 실제 item 수와 같음 |
| `canonical_payload_sha256` | char(64) | N | exact batch retry/conflict 판별 |
| `created_at` | timestamptz | N | DB write clock |

같은 `source_request_id`, recommendationVersion, movie는 여러 batch에서 반복될 수 있다. unique로
합치지 않는다. 같은 batch ID의 exact canonical payload만 replay하고 다른 재사용은 거부한다.

## 3. RECOMMENDATION_EXPOSURE_ITEM

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `recommendation_item_id` | UUID | N | 후속 attribution 식별자; PK |
| `exposure_batch_id + actor_user_id` | composite FK | N | batch owner와 일치 |
| `movie_id` | UUID/FK | N | 안정적인 `MOVIE_IDENTITY.id` |
| `position` | integer | N | 실제 표시 순서, 1 이상, batch 안 unique |
| `source_rank` | integer | N | FastAPI rank, 1 이상 |
| `recommendation_type` | enum | N | 현재 `POPULARITY_BASELINE`만 |
| `expected_star_status` | enum | N | 현재 `NOT_COMPUTED`만 |
| `expected_star_value` | numeric | Y | 현재 반드시 null |
| `expected_star_display_eligible` | boolean | N | 현재 반드시 false |
| `expected_star_confidence` | enum | N | 현재 `NOT_EVALUATED`만 |
| `expected_star_confidence_policy_version` | varchar | Y | 현재 반드시 null |

한 batch 안의 movie는 unique지만 다른 batch에서 같은 movie를 다시 노출할 수 있다. 항목 삭제는 batch
삭제 cascade 외에는 하지 않으며 retention은 별도 승인 전 정하지 않는다.

## 4. Outcome 비생성 원칙

노출은 `EXPOSED` 사실만 보존한다. click/Rating이 0건이어도 negative, satisfaction,
`observedRelativeUtility` 또는 별도 outcome row를 자동 생성하지 않는다. 이후 사건은 명시적인
`recommendationItemId`와 승인된 attribution policy를 가진 후속 task에서만 연결한다.
