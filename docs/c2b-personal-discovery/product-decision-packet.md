# C2B 개인·발견 추천 제품 결정 패킷

> 계약 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`  
> 결정 준비 상태: `READY_FOR_PRODUCT_DECISION`  
> 승인 결과: `PARTIALLY_APPROVED_1_OF_6` — `DN-C2B-002` 승인, 나머지 5건 `REQUIRES_APPROVAL`  
> `LOCAL_BASELINE_IMPLEMENTATION_AUTHORITY: YES` — 조회·append·dismiss 계약과 로컬 구현만
> `PRODUCTION_ACTIVATION_AUTHORITY: NO` — 배포·개인화·XAI·예상 별점·exposure/action 권위 없음

이 문서는 현재 evidence가 허용하는 보수적 기본값을 제품 소유자가 선택할 수 있게 만든 패킷이다.
수치는 승인안이 아니라 제안이다. 로컬에서 승인된 세 endpoint의 ranking은 alpha 0이고
expected-star와 reason은 응답에 없으며, 나머지 endpoint와 production activation은 BLOCKED다.

| 결정 | 권장 token | 승인 전 fail-closed |
| --- | --- | --- |
| `DN-C2B-001` | `KEEP_PUBLIC_ALPHA0_SHADOW_K10` | public alpha0, label `인기 기준 추천` |
| `DN-C2B-002` | `BASELINE_THREE_CUMULATIVE_LOAD_MORE_RATED_OR_EXPLICIT_DISMISS` | **승인됨**: 최초 3편, 요청마다 최대 3편 누적, 평가 완료 또는 명시적 `관심 없음`이면 목록·향후 후보 제외 |
| `DN-C2B-003` | `MAX_ONE_FAITHFUL_REASON` | 공개 reason 0개 |
| `DN-C2B-004` | `STAR_DISABLED_FAIL_CLOSED` | `NOT_COMPUTED` |
| `DN-C2B-005` | `EXACT_STAGE_ONLY_C1_EVENT_AMENDMENT` | utility `NOT_COMPUTED`, OTT attribution 차단 |
| `DN-C2B-006` | `NO_STALE_VERSIONED_RETENTION_CANDIDATE` | stale success·production SLA 없음 |

## DN-C2B-001 — 최소 K와 public personalization

**권장 기본값:** `PUBLIC_RANKING_ALPHA=0`, `PUBLIC_LABEL=POPULARITY_BASELINE`,
`MINIMUM_EVIDENCE_K=10`, `SHADOW_K=10`, `SHADOW_ALPHA=0.2`,
`K_GT_10_POLICY=NOT_APPROVED`, `PUBLIC_CHAMPION=null`.

REC-EV-011의 full-catalog offline K10에서 Popularity NDCG@10 `0.004723` 대비 blend `0.006154`,
paired improvement CI `[0.000253,0.002783]`가 양수였다. 그러나 manifest의 champion은 null이고
public UI 승인도 false다. K20 alpha0.3 CI는 0을 가로지르므로 K≥10을 한 정책으로 일반화하지 않는다.

### 반대안

- 즉시 K10 alpha0.2 공개: offline 신호를 빠르게 쓰지만 online 만족·서비스 데이터 drift 근거가 없다.
- 모든 K≥10에 같은 alpha: 단순하지만 input selection/bucketing이 검증되지 않았다.
- 권장안은 shadow 결과만 비교하고 공개 순위·label을 alpha0으로 유지한다.

### Privacy·UX

shadow 입력은 active integer Rating과 service movie UUID뿐이며 user ID/token은 FastAPI에 보내지 않는다.
사용자에게 평가 개수 부족을 실패나 싫어요로 말하지 않고 승인 전 항상 `인기 기준 추천`으로 표시한다.

### Rollback

향후 활성화 시 versioned flag로 alpha를 켠다. offline/online relevance regression, inputVersion 불일치,
오류율 상승 시 즉시 alpha0으로 되돌리고 기존 exposure의 version snapshot은 보존한다.

## DN-C2B-002 — 3편 composition, 누적 추가 추천, 평가 완료·명시적 관심 없음 종료

**승인값(2026-08-30):** `COMPOSITION=BASELINE_THREE`, `INITIAL_COUNT=3`,
`APPEND_PAGE_SIZE=3`, `CANDIDATE_BOUNDARY=TOP500`, `LOAD_MORE=CUMULATIVE`,
`CURSOR=OPAQUE_SIGNED`, `CURSOR_TTL=10m`, `SESSION_DUPLICATE=FORBIDDEN`,
`VERSION_DRIFT=409_REFRESH_REQUIRED`, `ITEM_EXIT=RATING_COMPLETED_OR_EXPLICIT_NOT_INTERESTED`,
`DISCOVERY_SLOT=DISABLED`.

REC-EV-013 constrained 2+1 v1은 모든 후보가 relevance budget을 실패했고 `selected:null`,
`two_plus_one:null`, `discovery_policy:null`이다. 최소 selection 상대 NDCG 손실도 `0.285714`였다.
Explore05는 REC-EV-004B에서 NDCG@10 `0.009382→0.005113`, 약 45.5% 손실로 이미 기각됐다.

BASELINE_THREE는 C2A ordered Top500을 score 내림차순·stable service movie UUID 오름차순 tie-break로 읽고,
active UI_READY 재검증, **아직 collection에 없고** C1 ViewingRecord·active Rating에도 없는 영화를 순서 보존
backfill한다. MovieLens Train-known은 서비스 사용자 seen exclusion이 아니다. 최초 조회는 3편, 사용자가
추가 추천을 요청할 때마다 최대 3편을 기존 active collection 뒤에 원자적으로 append한다. Top500 boundary를
소진한 마지막 요청만 1~2편 PARTIAL 또는 0편 EMPTY가 될 수 있다.

기존 collection item은 추가 요청·새로고침·재진입만으로 교체, 재정렬, 삭제하지 않는다. 사용자가 평가를
제출 완료하면 해당 item을 `COMPLETED_RATED`로, 명시적 `관심 없음`을 누르면 `DISMISSED_NOT_INTERESTED`로
바꾸고 목록과 이후 append 후보에서 제외한다. 무클릭·미평가·낮은 별점 자체·감상 완료·`나중에`는 제거
사건으로 추론하지 않는다. 두 상태는 서로 다르며 어느 쪽도 자동으로 추천 만족/실패 label로 변환하지 않는다. Catalog 비활성/
삭제/UI_READY 상실은 안전상 `RETIRED_CATALOG`로 숨길 수 있지만 사용자 관심 없음으로 기록하지 않는다.

초기/재진입 GET은 server-side collection의 모든 active item을 반환해 React memory가 사라져도 목록을 복구한다.
append는 `expectedRevision`과 actor·collection·revision·typed version·scan offset·expiry에 바인딩된 opaque signed
cursor를 사용하고 같은 body/idempotency replay는 같은 0~3편 delta만 반환한다. concurrent append는 single winner,
stale revision/cursor는 기존 collection을 지우지 않고 409 refresh-required다. refresh는 여전히 유효한 기존 item을
carry-forward하고 새 후보에만 current seen/rated exclusion을 적용한다.
내부 rank request는 immutable candidate store의 첫 `min(500,total)` UUID exact set이고 response는 같은 set을
exact once, sourceRank 1..N<=500으로 반환해야 한다. delivery는 scanned=selected+excluded와 issue count 합을 보존한다.
SafeIssue exact enum은 `CANDIDATE_NOT_UI_READY`, `CANDIDATE_ALREADY_RATED`, `CANDIDATE_ALREADY_SEEN`이고
cardinality는 0..3이다. code-key unique, positive count, retriable=false이며 stored rows에서 response/summary를
재계산한다. model mapping/set/rank drift는 PARTIAL issue가 아니라 503이다.

### 반대안

- 2+1 즉시 공개: 발견 서사는 생기지만 현재 모든 후보가 사전 relevance budget을 실패했다.
- stateless 다음 페이지로 기존 카드 교체: 구현은 단순하지만 사용자가 고른 후보가 사라지고 재진입 복구가 안 된다.
- 감상만으로 자동 제거: 평가 전 비교 중인 추천을 잃는다.
- 승인안은 server-side 누적 collection, opaque cursor, 평가 완료, 명시적 dismiss를 서로 분리한다.

### Privacy·UX

제외 사유는 allowlisted SafeIssue code와 aggregate count만 공개한다. 제외된 movie/version/hash는 숨긴다.
`관심 없음`은 recommendation item ID에 대한 owner-scoped event이며 raw 자유 텍스트를 받지 않는다. 화면은 기존
카드를 그대로 두고 새 카드를 아래에 append하며 평가 commit 또는 dismiss commit 성공 시 해당 카드를 제거한다.

### Rollback

future discovery policy는 새 compositionVersion으로만 활성화한다. relevance budget 실패나 candidate exhaustion이
증가하면 BASELINE_THREE를 유지하고 2+1 snapshot을 baseline으로 재해석하지 않는다. append 장애 시 기존 active
collection은 그대로 유지하고 새 append만 재시도한다. active collection의 물리 purge 기간은 DN-C2B-006 승인 전
확정하지 않되, purge가 `관심 없음` 없이 유효한 item을 제품 화면에서 소실시키는 수단이 되어서는 안 된다.

## DN-C2B-003 — 공개 추천 이유

**권장 기본값:** `PUBLIC_REASON_MAX=1`, `REASON_POLICY=FAITHFUL_TYPED_ONLY`,
`REASON_UI=DISABLED_UNTIL_COPY_APPROVAL`, `GENERIC_REASON=FORBIDDEN`.

REC-EV-006은 40,000 추천 position에서 Popularity reason emittable coverage `0.999825`를 기록했지만
reason UI 승인과 문구는 없다. 따라서 승인 전 0개, 승인 후에도 실제 rank effect가 있는 typed reason 중
최대 1개만 선택하고 “취향에 딱 맞아요” 같은 근거 없는 문구를 만들지 않는다.

### 반대안

- 이유 3개: 풍부하지만 작은 card에서 과잉 설명과 모순 위험이 커진다.
- generic reason: coverage는 높지만 faithful rank contribution을 설명하지 못한다.
- 이유 없음: 가장 안전하지만 추천을 이해할 단서가 없다. 현재 fail-closed 값이다.

### Privacy·UX

Rating 값·영화 ID·내부 score/version을 copy에 넣지 않는다. 승인 시 localization, screen reader,
overflow test를 통과한 한 문장만 표시한다.

### Rollback

reason mismatch/translation 오류가 탐지되면 reason 영역만 숨기며 ranking과 card는 유지한다.
과거 reason을 새 policy의 설명으로 재사용하지 않는다.

## DN-C2B-004 — 예상 별점

**권장 기본값:** `EXPECTED_STAR_STATUS=NOT_COMPUTED`, `EXPECTED_STAR_VALUE=null`,
`DISPLAY_ELIGIBLE=false`, `CONFIDENCE=NOT_EVALUATED`, `CONFIDENCE_POLICY_VERSION=null`.

REC-EV-003C는 MovieLens와 서비스 Rating scale/alignment가 준비되지 않아 star를 fail-closed했다. clamp·round로
그 차이를 숨기는 방안도 기각됐다. 따라서 별점 숫자나 confidence badge를 만들지 않는다.

### 반대안

- 점수를 1~5로 clamp: 직관적이지만 척도 오류를 가린다.
- rank percentile 표시: 별점 오해는 줄지만 사용자가 기대하는 의미와 다르고 별도 calibration이 필요하다.
- 권장안은 held-out C1 paired scale evidence 전 숫자를 계산하지 않는다.

### Privacy·UX

값이 없음을 0점으로 표시하지 않고 card에서 별점 영역 자체를 숨긴다. 내부 prediction/debug 값도 공개 DTO와
analytics에 싣지 않는다.

### Rollback

향후 활성화해도 feature flag와 confidencePolicyVersion으로 격리한다. calibration drift나 coverage Gate 실패 시
즉시 `NOT_COMPUTED`로 되돌리고 과거 숫자를 캐시에서 제거한다.

## DN-C2B-005 — action/outcome과 utility

**권장 기본값:** `ATTRIBUTION=EXACT_STAGE_ONLY`, `ATTRIBUTION_POLICY=c2b-direct-action-chain-v1`,
`UTILITY_STATUS=NOT_COMPUTED`, `LAST_TOUCH=FORBIDDEN`,
`C1_CLICK_EVENT_AMENDMENT=REQUIRED`.

EXPOSED→DETAIL_OPENED→OTT_OPTION_OPENED→WATCH_CONFIRMED→RATED의 관측 stage만 기록하고 만족·utility를
추론하지 않는다. 현재 C1 `createWatchIntent`는 이번 click behaviorEventId/server occurredAt을 반환하지 않아
ACTIVE_REUSED와 ALREADY_WATCHED를 특정 추천 item에 정확히 연결할 수 없다.

TASK-C2B-011에서 모든 click의 behaviorEventId, server occurredAt, outcome, nullable activeWatchIntentId를
반환해야 한다. 한 click event는 recommendation action 하나에만 연결한다. CREATED만 downstream chain을
연결하고 ACTIVE_REUSED와 ALREADY_WATCHED는 click-only다. 같은 WatchIntent의 여러 click은 가능하며
ALREADY_WATCHED는 WatchIntent null이다.

C1 click/WatchIntent transaction을 먼저 독립 commit하고 C2B action은 별도 transaction에서 committed event를
연결한다. header idempotency key와 body event ID를 owner scope로 각각 잠가 concurrent duplicate single winner를
만든다. projector는 immutable event ledger와 item row lock을 쓰며 Rating highest revision과 DELETE tombstone만 적용한다.
반복 action 0..N은 보존하되 singular projection winner는 stage desc, server occurredAt asc, actionEventId asc로
결정하고 watch/viewing/rating FK를 한 CREATED chain에서만 가져온다.
C1 source event가 action보다 먼저 도착하면 immutable PENDING_ACTION inbox에 보존한다. later CREATED action
commit과 periodic worker는 exact behaviorEvent/WatchIntent chain만 deterministic ordering으로 reconcile하고
ACTIVE_REUSED/ALREADY_WATCHED historical chain은 backfill하지 않는다.

### 반대안

- WatchIntent ID last-touch: 구현은 쉽지만 ACTIVE_REUSED가 여러 추천 item에 outcome을 복제한다.
- client timestamp 결합: 조작·clock skew·replay에 취약하다.
- action stage만 기록: 인과 과장은 막지만 제품 utility 판단은 별도 연구가 필요하다. 이것이 권장안이다.

### Privacy·UX

destination URL·Rating 값·raw event를 저장하지 않고 owner/movie는 서버 FK로 검증한다. 미클릭·미평가를
negative로 만들지 않으며 사용자에게 추천 성공률을 표시하지 않는다.

### Rollback

event linkage 결함 시 OTT attribution consumer만 중단하고 C1 click/WatchIntent는 그대로 commit한다.
projection을 eventId로 재처리하되 last-touch나 과거 WatchIntent outcome으로 보충하지 않는다.

## DN-C2B-006 — delivery/cache/retention/운영 Gate

**권장 기본값 후보:** `APPEND_CURSOR_TTL=10m`, `TERMINAL_UNEXPOSED_RETENTION=24h`,
`EXPOSURE_ACTION_ATTRIBUTION_RETENTION=90d`, `IDEMPOTENCY_RETENTION=24h`,
`CACHE_CONTROL=PRIVATE_NO_STORE`, `CACHE_KEY=ACTOR_PLUS_TYPED_VERSIONS`,
`STALE_SUCCESS_FALLBACK=DISABLED`, `SPRING_TIMEOUT_CANDIDATE=750ms`,
`RATING_SNAPSHOT_FRESHNESS_CANDIDATE=3000ms`, `PRODUCTION_SLA=NOT_APPROVED`.

REC-EV-007은 local Uvicorn loopback에서 후보≤100 p95 최대 `4.1012 ms`, 후보1000 p95 `30.6875 ms`,
동시성4 p95 `15.1896 ms`와 `332.9953 rps`를 측정했다. 750ms/3000ms는 보수 규칙으로 만든 local 후보이지
Spring·DB·TLS·load balancer를 포함한 production SLA가 아니다.

GET은 actor의 current active collection을 복구하며 exposure가 아니고 `private, no-store`다. 10분 후보 TTL은
append cursor 만료이지 active collection 가시성 만료가 아니다. exposedAt/occurredAt은 server clock이다.
내부 version/checksum/hash는 public response와 safe log에 없다.
단, GET/append마다 Catalog UI_READY와 C1 상태를 재검증한다. Rating commit은 exact item을 COMPLETED_RATED로
종료하고 Viewing-only는 기존 item을 제거하지 않는다. cursor/version stale은 append를 409로 막되 기존 collection을
삭제하지 않는다. ErrorResponse는 raw UUID가 아닌 route template만 반환한다.
typed snapshot에는 non-empty `mapping_version`과 Catalog eligibility version 실제 column이 포함되고 current mapping
drift는 TTL 안에도 stale다. C0 activation/UI_READY는 Catalog singleton FOR UPDATE 아래 mutation+version을 commit한다.
C2B는 Catalog singleton FOR SHARE 뒤 actor eligibility FOR SHARE 순서로 잠그고 current Catalog/C1 final check와 두
version double-check를 delivery/exposure commit까지 유지한다. lock commit 순서가 concurrency 선형화 시점이다.
이 actor row는 기존 `eligibility-version row` 계약을 그대로 사용한다.
exposure header IDEMPOTENCY_RECORD와 body exposure batch/items/delivery link/canonical domain result는 sorted dual advisory lock
아래 한 REQUIRES_NEW transaction이므로 crash 뒤 절반 상태를 허용하지 않는다.
stored result는 HTTP status/`replayed`를 제외한 canonical domain payload다. original wire는 201+replayed=false,
replay wire는 200+replayed=true이고 나머지 domain fields는 canonical JSON 기준 동일하다. exposure items는 ID/position
unique이며 array order와 같은 1..N gap 없는 수열이다.

### 반대안

- stale cache 200: 가용성은 높지만 retired movie/옛 policy를 성공처럼 노출한다.
- cursor TTL 0: stale 위험은 낮지만 rank/DB 부하와 append row가 늘어난다.
- 장기 raw retention: 분석은 쉬우나 privacy 비용이 증가한다. 권장 후보는 typed 최소 projection만 보존한다.

### Privacy·UX

terminal/unexposed append record 24시간, exposure/action 90일, idempotency 24시간 뒤 purge 후보를 둔다. ACTIVE
collection 가시성 purge 기간은 미확정이며 평가 완료·관심 없음·Catalog 퇴역 없이 소실시키지 않는다. 비식별
utility aggregate도 별도 privacy 승인 전 만들지 않는다. append 503에서는 기존 card를 유지하고 실패 delta만 retry한다.

### Rollback

운영 topology 측정에서 timeout/error/storage/privacy Gate를 못 맞추면 public activation을 하지 않는다. 활성 후
문제가 생기면 새 append만 끄고 stale fallback 없이 503으로 닫으며 기존 active collection과 audit retention은 보존한다.

## 제품 소유자 응답 형식

```text
DN-C2B-00x: <권장 token 또는 DEFER>
수치 변경: <없음 또는 field=value>
허용 손실: <제품/privacy trade-off>
rollback trigger: <측정 가능한 조건>
재검토 evidence: <ID/version>
```

현재 승인 현황은 `1/6`이며, 나머지 5건과 선행 계약 Gate가 끝날 때까지 공개 implementation authority는 `NO`다.
