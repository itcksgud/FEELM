# C3 Local MVP Data Dictionary

> 상태: `APPROVED` — `LOCAL_MVP_ONLY`

| Entity | 핵심 field | 불변식 |
| --- | --- | --- |
| `LOCAL_FAKE_ACTOR` | actor_id, nickname, enabled | tracked allowlist; 실제 account/auth가 아님 |
| `PARTY` | party_id, owner_actor_id, name, status, member_count, revision | DRAFT/ACTIVE, member_count 1..4 |
| `PARTY_MEMBER` | party_id, member_id, actor_id, role, joined_at | owner 1, actor unique, removal field 없음 |
| `PARTY_PROVIDER` | party_id, provider_id | Party당 distinct 2..4 |
| `PARTY_INVITATION` | invitation_id, party_id, recipient_actor_id, status, revision, party_revision | PENDING/ACCEPTED; recipient accept 재검증용 Party revision snapshot |
| `AVAILABILITY_MATERIALIZATION` | id, catalog_version, region, scope, status | KR, FLATRATE, COMPLETE fixture |
| `AVAILABILITY_MEMBERSHIP` | materialization_id, provider_id, movie_id, catalog_popularity_rank | actual UI_READY membership unique |
| `OTT_CATALOG_COMPARISON` | id, owner_actor_id, materialization_id, status, created_at | READY immutable |
| `OTT_CATALOG_PROVIDER` | comparison_id, provider_id, movie_count | traversal distinct count와 동일 |
| `OTT_CATALOG_MOVIE` | comparison_id, provider_id, movie_id, display snapshot | actual title/poster/year snapshot |
| `PARTY_BASELINE_RUN` | run_id, party_id, party_revision, materialization_id, policy_version | policy=`CATALOG_POPULARITY_KR_FLATRATE_V1` |
| `PARTY_BASELINE_ITEM` | run_id, movie_id, position, provider_coverage, popularity_rank | deterministic unique order |
| `IDEMPOTENCY_RESULT` | actor_id, operation, key, request_hash, response | same body replay only |

## Public 금지 field

- Rating, expectedStar, predicted utility, satisfaction/fairness score, member vector
- exposure/detail/click counts와 source event ID
- materialization hash/signing key/idempotency request hash
- production email/OAuth/JWT subject

`actor_id`는 local fixture의 비밀이 아닌 UUID이지만 production identity로 해석하지 않는다.
