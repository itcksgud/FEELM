# C3 Local MVP Acceptance Tests

> 상태: `APPROVED` — 32 AC

## Local actor

| ID | Given / When / Then |
| --- | --- |
| `AC-C3-001` | non-loopback bind로 시작하면 application startup이 실패한다. |
| `AC-C3-002` | missing/unknown `X-Local-Actor-Id`는 401 `LOCAL_ACTOR_UNAUTHORIZED`다. |
| `AC-C3-003` | body/query actorId가 header actor 권한을 바꾸지 못한다. |
| `AC-C3-004` | 다른 actor의 private Party/comparison은 payload 없는 404다. |

## Party·invitation

| ID | Given / When / Then |
| --- | --- |
| `AC-C3-005` | createParty는 owner 1명, DRAFT, 1/4, provider 2~4개, revision 1을 반환한다. |
| `AC-C3-006` | provider 1/5개 또는 duplicate는 400이고 Party가 없다. |
| `AC-C3-007` | same idempotency key/body replay는 같은 Party이며 duplicate row가 없다. |
| `AC-C3-008` | same key/different body는 409이고 최초 Party를 바꾸지 않는다. |
| `AC-C3-009` | owner가 allowlist fake actor를 초대하면 PENDING 하나와 Party revision advance가 원자 반영된다. |
| `AC-C3-010` | self/unknown actor 초대는 `LOCAL_ACTOR_UNAVAILABLE`, duplicate pending은 409다. |
| `AC-C3-011` | non-owner invitation create와 non-recipient accept는 private 404다. |
| `AC-C3-012` | recipient accept는 ACCEPTED, MEMBER 한 건, Party revision과 DRAFT→ACTIVE를 원자 반영한다. |
| `AC-C3-013` | same accept key/body replay는 최초 response이고 member/outbox가 늘지 않는다. |
| `AC-C3-014` | stale Party/invitation revision은 409이며 partial member/invitation 변경이 없다. |
| `AC-C3-015` | 3/4 Party의 두 concurrent accept는 한 요청만 성공하고 최종 4/4다. |
| `AC-C3-016` | listMyParties, getParty, owner invitation list, recipient invitation list로 reload 뒤 흐름을 복구한다. |

## OTT catalog comparison

| ID | Given / When / Then |
| --- | --- |
| `AC-C3-017` | 2~4 provider create는 KR/FLATRATE/COMPLETE catalogVersion의 immutable READY comparison이다. |
| `AC-C3-018` | incomplete/missing materialization은 503이고 comparison/provider/movie partial row 0건이다. |
| `AC-C3-019` | Netflix summary movieCount=3, Watcha=2이고 각 전체 traversal distinct count와 같다. |
| `AC-C3-020` | overlap movie는 Netflix/Watcha 목록 각각 한 번이며 availableProviderIds 두 개를 가진다. |
| `AC-C3-021` | 모든 item은 실제 C0 movieId/title/poster/year이고 placeholder/대표 subset이 없다. |
| `AC-C3-022` | limit=2 traversal은 중복/누락 없이 totalCount까지 도달한다. |
| `AC-C3-023` | hasNext=true면 nextCursor string, false면 null이고 items≤limit≤100이다. |
| `AC-C3-024` | tampered/cross-provider/cross-actor cursor는 400 `INVALID_CURSOR`다. |

## Party baseline

| ID | Given / When / Then |
| --- | --- |
| `AC-C3-025` | Netflix+Watcha Party는 coverage DESC, popularity rank ASC, title ASC, movieId ASC 순이다. |
| `AC-C3-026` | 같은 provider/materialization의 다른 Party와 member 수 변경 전후 순서가 byte-stable하다. |
| `AC-C3-027` | explanation은 available/selected provider count, popularity rank, policy version만 가진다. |
| `AC-C3-028` | schema/response/log에 Rating·behavior·expectedStar·utility·satisfaction·fairness·Average·Balanced가 없다. |
| `AC-C3-029` | owner/accepted member는 조회하고 pending/non-member는 404다. |

## UI·Gate

| ID | Given / When / Then |
| --- | --- |
| `AC-C3-030` | provider summary마다 `전체 영화 보기 (N)` accessible link가 있고 320px에서 1열이다. |
| `AC-C3-031` | fake actor, loading/empty/401/404/409/503와 retry/recovery가 semantic role로 구분된다. |
| `AC-C3-032` | main OpenAPI와 generated schema에는 승인된 11개 operation만 있고 production config/backend는 수정되지 않는다. |
