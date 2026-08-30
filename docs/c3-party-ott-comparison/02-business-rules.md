# C3 Business Rules

> 상태: `APPROVED` — `LOCAL_MVP_ONLY`

## Local actor·권한

| ID | 규칙 |
| --- | --- |
| `BR-C3-001` | C3 local operation은 loopback 서버에서만 등록한다. non-loopback bind면 startup fail이다. |
| `BR-C3-002` | `X-Local-Actor-Id`는 fixture allowlist exact UUID만 허용하며 body/query actor를 신뢰하지 않는다. |
| `BR-C3-003` | unknown/missing actor는 401, 다른 actor의 private Party/comparison은 404다. |
| `BR-C3-004` | mutation은 Idempotency-Key+canonical body hash를 저장하고 same key/different body는 409다. |
| `BR-C3-005` | actor header, cursor, idempotency body hash는 log에 남기지 않는다. |

## Party·invitation

| ID | 규칙 |
| --- | --- |
| `BR-C3-010` | Party create는 owner membership 하나, status DRAFT, memberCount=1, maximumMemberCount=4를 원자 생성한다. |
| `BR-C3-011` | Party는 2~4 distinct provider를 저장하며 변경 operation은 local MVP에 없다. |
| `BR-C3-012` | owner만 allowlist `recipientActorId`로 PENDING invitation을 만들고 self/unknown/duplicate는 safe error다. |
| `BR-C3-013` | 같은 Party+recipient PENDING invitation은 최대 하나다. |
| `BR-C3-014` | recipient만 PENDING invitation을 accept할 수 있고 decline/cancel/leave/close/kick/transfer route는 없다. |
| `BR-C3-015` | accept는 PARTY→INVITATION→MEMBER→IDEMPOTENCY 순 lock 후 membership, invitation, Party revision을 한 transaction에 반영한다. |
| `BR-C3-016` | owner 포함 accepted member는 최대 4명이며 마지막 자리 concurrent accept는 한 요청만 성공한다. |
| `BR-C3-017` | 첫 member accept는 DRAFT→ACTIVE, 이후 accept는 ACTIVE를 유지한다. |
| `BR-C3-018` | owner/accepted member만 Party와 baseline을 읽으며 pending recipient는 accept 전 Party 상세을 읽지 못한다. |

## OTT catalog comparison

| ID | 규칙 |
| --- | --- |
| `BR-C3-020` | comparison은 2~4 distinct provider, KR, FLATRATE, 단일 COMPLETE fixture materialization을 사용한다. |
| `BR-C3-021` | comparison header·provider summary는 원자 생성하며 materialization 불완전 시 503과 partial row 0건이다. |
| `BR-C3-022` | provider summary `movieCount`는 해당 provider 전체 cursor traversal의 distinct 실제 영화 수와 같다. |
| `BR-C3-023` | item은 C0 UI_READY movieId/title/poster/year와 availableProviderIds를 가진다. 대표/placeholder item은 금지다. |
| `BR-C3-024` | overlap movie는 각 해당 provider list에 한 번씩 포함한다. |
| `BR-C3-025` | cursor는 actor/comparison/provider/materialization/last key에 결합하고 tamper/reuse는 400이다. |
| `BR-C3-026` | page items≤requested limit≤100이며 hasNext와 nextCursor nullability가 일치한다. |
| `BR-C3-027` | comparison은 immutable이고 catalog fixture 교체는 새 comparisonId를 만든다. |

## Deterministic Party baseline

| ID | 규칙 |
| --- | --- |
| `BR-C3-030` | candidate는 Party provider 중 하나 이상에 있는 COMPLETE materialization의 C0 UI_READY 실제 영화다. |
| `BR-C3-031` | 순서는 provider coverage DESC, catalog popularity rank ASC, normalized title ASC, movieId ASC다. |
| `BR-C3-032` | 같은 providerIds/catalogVersion/policyVersion은 Party membership과 무관하게 같은 결과를 낸다. |
| `BR-C3-033` | item은 availableProviderCount, selectedProviderCount, catalogPopularityRank, policyVersion으로만 설명한다. |
| `BR-C3-034` | Rating, exposure, detail, click, MovieLens user, ALS/vector, Average/Balanced를 읽거나 response에 넣지 않는다. |
| `BR-C3-035` | 예상 별점, 만족도/확률, 효용, 공정성, 구성원 취향 문구를 사용하지 않는다. |
| `BR-C3-036` | baseline cursor는 Party revision/provider/materialization/policy/last key에 결합한다. |

## Deferred production

| ID | 규칙 |
| --- | --- |
| `BR-C3-090` | OAuth/JWT, 실제 nickname/email 초대, 외부 발송은 production 결정 전 BLOCKED다. |
| `BR-C3-091` | Party decline/cancel/leave/close/kick/transfer/expiry/retention은 production 결정 전 BLOCKED다. |
| `BR-C3-092` | live availability freshness/SLA와 last-known-good는 production 결정 전 BLOCKED다. |
| `BR-C3-093` | Party taste analysis, 만족도 추정, 개인화 추천과 typed behavior attribution은 production 결정 전 BLOCKED다. |
| `BR-C3-094` | main OpenAPI 병합과 배포는 별도 승인 task 전 BLOCKED다. |

