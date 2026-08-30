# C3 Glossary and Policies

> 상태: `APPROVED` — `LOCAL_MVP_ONLY`

| 용어 | 계약 의미 |
| --- | --- |
| Local Actor | `X-Local-Actor-Id`로 선택된 allowlist fake UUID. 실제 인증이 아님 |
| Party | owner 포함 최대 4명의 local 모임 |
| OTT Catalog Comparison | 개인 취향이 아니라 선택 provider의 KR FLATRATE 실제 영화 membership 비교 |
| Availability Materialization | 한 catalogVersion의 seed된 COMPLETE KR FLATRATE membership |
| Party Baseline | `CATALOG_POPULARITY_KR_FLATRATE_V1`; 사용자 신호를 읽지 않는 결정적 영화 순서 |
| Provider Coverage | 선택 provider 중 해당 영화를 제공하는 distinct provider 수 |
| Catalog Popularity Rank | 고정 catalogVersion C0 POPULARITY ordering의 1-based rank |
| Stable Cursor | actor/comparison 또는 party/provider/materialization/sort key에 결합된 opaque cursor |
| Actual Movie | C0 `UI_READY` movieId/title/poster/year를 가진 catalog item |

## Local actor policy

- 서버는 loopback bind만 허용한다.
- header UUID는 fixture allowlist exact match이며 unknown/missing은 401이다.
- body/query의 owner/member ID는 actor authority가 아니다.
- invite의 `recipientActorId`도 allowlist lookup 전용이며 email/nickname 검색 결과를 만들지 않는다.

## Ranking policy

```text
availableProviderCount DESC
catalogPopularityRank ASC
normalizedDisplayTitle ASC
movieId ASC
```

response explanation은 네 정렬 fact 중 사용자에게 의미 있는 coverage/rank와 `policyVersion`만 포함한다.
Average/Balanced, predicted utility, expected star, satisfaction probability, member taste는 금지한다.

## Catalog comparison policy

- provider는 2~4개 distinct UUID다.
- region/scope는 `KR`/`FLATRATE` 상수다.
- local fixture의 단일 COMPLETE materialization만 선택한다.
- comparison은 immutable이며 provider summary의 `movieCount`가 전체 cursor traversal distinct 수와 같다.
- overlap movie는 각 provider 목록에 한 번씩 있고 `availableProviderIds`로 교집합을 설명한다.
- 대표 영화 subset으로 실제 영화 전체 목록을 대체하지 않는다.

## Cursor/error policy

- 기본 limit 20, 최대 100이다.
- cursor는 opaque하고 다른 actor/comparison/party/provider에서 재사용하면 400 `INVALID_CURSOR`다.
- private Party/comparison의 다른 actor 접근은 404다.
- stale revision, capacity, idempotency body mismatch는 409다.
- seed materialization이 없거나 불완전하면 503이며 empty catalog로 바꾸지 않는다.

## Deferred production policy

production OAuth/JWT, 실제 초대 lookup, invitation lifecycle, availability freshness, Rating/행동 attribution,
Party taste analysis와 개인화 추천은 미정이다. Local Actor나 local baseline을 production 보안·추천 정책으로
재사용하지 않는다.

