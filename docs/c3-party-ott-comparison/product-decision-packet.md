# C3 Party·OTT 비교 local MVP 결정 패킷

> 상태: `APPROVED`  
> 승인 결과: `APPROVED_LOCAL_MVP` — `DN-C3-001`~`004`; `DN-C3-005`는 `DEFERRED`  
> `IMPLEMENTATION_AUTHORITY: LOCAL_MVP_ONLY`  
> production auth·배포 권위: `NO`; main OpenAPI local operation 병합: `YES`

## 1. 근거 감사 결론

REC-EV-005의 Balanced−Average 평균·최저·격차 CI는 모두 0을 포함하고 4인 공통평가 coverage도
0.69%~1.02%다. Average 역시 offline comparator다. 따라서 둘 중 하나를 실제 Party 만족도·공정성
정책으로 채택할 근거는 없다. 사용자의 현재 개발 권위는 “로컬에서 끝까지 동작하는 보수적 기능”을
허용하지만, 이 evidence 부족을 개인화 추정으로 메우는 권위는 아니다.

결론은 모델 기반 Party 추천을 켜는 것이 아니라, C0에서 이미 설명 가능한 사실만 사용하는 새 baseline을
도입하는 것이다. 이 baseline은 개인 취향을 예측하지 않고 동일한 입력에 동일한 결과를 낸다.

## 2. 승인된 local MVP

| 결정 | 승인값 | 공개 결과 |
| --- | --- | --- |
| `DN-C3-001` | `CATALOG_POPULARITY_KR_FLATRATE_V1` | 실제 영화, provider coverage, catalog popularity rank, 정책 버전 |
| `DN-C3-002` | `PARTY_CREATE_INVITE_ACCEPT_MAX4` | owner 1명으로 생성, fake actor 초대/수락, owner 포함 최대 4명 |
| `DN-C3-003` | `LOOPBACK_ALLOWLIST_FAKE_ACTOR` | `X-Local-Actor-Id` allowlist fixture만 허용; body actor 금지 |
| `DN-C3-004` | `KR_FLATRATE_COMPLETE_FIXTURE_FULL_LIST` | provider 2~4개 비교, overlap, provider별 실제 영화 전체 cursor 목록 |
| `DN-C3-005` | `DEFERRED` | Rating·노출·상세·click·taste analysis는 local MVP response/input에 없음 |

### 결정적 Party baseline

Party 생성 때 선택한 2~4개 provider의 동일 COMPLETE materialization을 사용한다. candidate는 C0
`UI_READY`이면서 선택한 provider 중 하나 이상의 KR `FLATRATE` membership이 있는 실제 영화다.

```text
availableProviderCount DESC
catalogPopularityRank ASC
normalizedDisplayTitle ASC
movieId ASC
```

- `catalogPopularityRank`는 고정 catalogVersion에서 materialize한 양의 정수다.
- 개인 Rating, MovieLens user, ALS factor, vector, 행동 count, 예상 별점은 읽지 않는다.
- 같은 `providerIds + catalogVersion + policyVersion`이면 Party 구성과 무관하게 같은 순서다.
- explanation은 `선택 OTT N개 중 M개에서 볼 수 있음`, `인기 기준 K위`라는 사실만 제공한다.
- “여러 명이 좋아할”, “만족도”, “공정성”, “예상 별점”이라고 표현하지 않는다.

### local fake actor

- 서버 bind는 loopback만 허용한다.
- `X-Local-Actor-Id`는 `testing/fixtures.md`에 있는 UUID allowlist만 받는다.
- header가 없거나 unknown이면 401 `LOCAL_ACTOR_UNAUTHORIZED`다.
- 초대 body는 `recipientActorId`를 받되 allowlist 내부 exact lookup만 하고 email/nickname 검색을 하지 않는다.
- nickname은 fake actor display snapshot이며 실제 계정 식별 정책으로 승격하지 않는다.

### Party lifecycle

`DRAFT(owner 1) → ACTIVE(first accept)`만 승인한다. PENDING invitation 수락은 Party·Invitation을 lock하고
member insert, Party revision, invitation terminal state, idempotency result를 한 transaction에 반영한다.
capacity 4 경쟁은 한 요청만 성공하고 다른 요청은 409 `PARTY_CAPACITY_REACHED`다. decline/cancel/leave/
close/kick/transfer/expiry는 local MVP route에 없다.

### OTT 비교

비교는 취향 점수가 아니라 provider catalog fact다. 2~4개 distinct provider가 한 seed COMPLETE
materialization을 공유한다. provider별 movie count와 overlap count를 보여주며 provider 목록의 모든 실제
영화를 stable cursor로 끝까지 볼 수 있다. 대표 영화 carousel은 전체 목록을 대체하지 않는다.

## 3. 공개 operation

| operationId | 의미 |
| --- | --- |
| `createOttCatalogComparison` | 2~4 provider의 immutable local comparison 생성 |
| `getOttCatalogComparison` | provider별 전체 movie count/overlap summary 조회 |
| `listOttCatalogComparisonMovies` | provider 한 곳의 실제 영화 전체 cursor page |
| `listMyParties` | local actor가 소유/참여한 Party 목록 |
| `createParty` | provider 2~4개를 고른 owner-only Party 생성 |
| `getParty` | owner/accepted member Party 상세 |
| `createPartyInvitation` | owner가 allowlist fake actor 초대 |
| `listPartyInvitations` | owner가 Party invitation 상태를 reload 뒤 복구 |
| `listMyPartyInvitations` | recipient의 PENDING/ACCEPTED invitation 조회 |
| `acceptPartyInvitation` | recipient 수락 및 최대 4명 원자 보장 |
| `listPartyBaselineRecommendations` | 결정적 실제 영화 baseline과 설명 조회 |

11개 operation은 main `docs/api/openapi.yaml`에 localActor security와 C3 prefix schema로 병합한다.

## 4. 명시적 차단

- Average/Balanced/ALS/embedding/utility 기반 Party public policy
- 구성원별 또는 aggregate 만족도·예상 별점·취향 차이 추정
- Rating·노출·상세·OTT click의 집계/가중/negative inference
- production OAuth/JWT, 실제 nickname/email directory, 외부 초대 발송
- Party decline/cancel/leave/close/kick/transfer/expiry/retention
- live TMDB availability refresh SLA와 stale fallback
- production 배포와 C3 local operation 외 API 확장

## 5. Rollback과 승격 Gate

local baseline은 feature flag 하나로 route 등록을 제거할 수 있으며 source Rating/Catalog를 수정하지 않는다.
production 승격 전에는 별도 제품 결정으로 인증·초대 privacy, lifecycle, availability freshness, 개인화
정책·평가 지표를 승인하고 main OpenAPI를 병합해야 한다. local 결과는 online 만족도 evidence로 사용하지 않는다.
