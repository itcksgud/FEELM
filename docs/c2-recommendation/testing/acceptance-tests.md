# C2 Recommendation Serving Acceptance Test

> 상태: `APPROVED_C2A_INTERNAL_POPULARITY_ONLY` — star·public 항목은 해당 decision Gate가 유지된다.

## 1. 공통 fixture

| ID | 조건 |
| --- | --- |
| `FX-C2-READY` | 네 artifact metadata가 같은 family/ID space/rating scale이며 calibration v2가 exact dependency checksum과 ranking alpha 0을 bind; mapping은 verified/recovered Catalog identity와 catalog checksum을 bind |
| `FX-C2-CANDIDATES` | service UUID 4개, mapping/model available 3개, unmapped 1개 |
| `FX-C2-RATINGS-K10` | active C1 Rating 10개, integer 1~5, canonical UUID order와 revision 포함 |
| `FX-C2-RATINGS-K5` | active C1 Rating 5개; expected-star UI 승인 없음 |
| `FX-C2-BAD-FAMILY` | mapping metadata compatibility_id만 다름 |
| `FX-C2-BAD-CHECKSUM` | factor payload 한 byte 변경, sidecar 유지 |
| `FX-C2-STAR-FAIL` | ranking artifact set은 ready이나 injected star computation failure |
| `FX-C2-C1-OUTBOX` | RATING_UPDATED eventId를 두 번 전달 |

실제 artifact 값은 test fixture sidecar에서 만든다. token, 실제 userId, MovieLens user ID, filesystem
절대 경로를 golden response에 넣지 않는다.

## 2. 요청·UUID·결정론

| ID | Given / When / Then |
| --- | --- |
| `AC-C2-001` | Given FX-C2-READY와 service UUID 후보, When rank, Then 응답 movieId는 service UUID이고 MovieLens ID가 없다. |
| `AC-C2-002` | Given 같은 canonical request와 artifact set, When 입력 후보 순서를 바꿔 반복, Then rank·score·recommendationVersion·snapshot이 같다. |
| `AC-C2-003` | Given 동점 Popularity score, When rank, Then service UUID 오름차순으로 안정 정렬한다. |
| `AC-C2-004` | Given header/body requestId 불일치, When rank, Then 422 REQUEST_ID_MISMATCH이고 계산하지 않는다. |
| `AC-C2-005` | Given invalid UUID 또는 Rating 0/6/decimal, When rank, Then 422이고 raw body를 error message에 복사하지 않는다. |

## 3. Artifact·readiness

| ID | Given / When / Then |
| --- | --- |
| `AC-C2-006` | Given FX-C2-READY, When readiness, Then 200 READY이고 네 kind와 serving dry-run이 PASS다. |
| `AC-C2-007` | Given FX-C2-BAD-CHECKSUM과 이전 set 없음, When startup/readiness/rank, Then set publish를 거부하고 readiness·rank는 503이다. |
| `AC-C2-008` | Given FX-C2-BAD-FAMILY, When load, Then 503 ARTIFACT_COMPATIBILITY_FAILURE이며 partial set을 사용하지 않는다. |
| `AC-C2-009` | Given ready old set과 invalid new set, When reload, Then old set은 원자적으로 유지되고 reload failure가 safe code로 관측된다. |
| `AC-C2-010` | Given live process와 artifact 없음, When live/ready, Then live=200 LIVE, ready=503 NOT_READY다. |

## 4. Ranking·star head·이유

| ID | Given / When / Then |
| --- | --- |
| `AC-C2-011` | Given K0/K1/K3/K5/K10/K20 각각, When rank, Then rankingPolicy=BAYESIAN_POPULARITY_ONLY와 rankingAlpha=0.0이며 candidate order가 K 때문에 바뀌지 않는다. |
| `AC-C2-012` | Given starPolicy=DISABLED, When rank, Then expectedStar.status=NOT_COMPUTED, value=null, displayEligible=false다. |
| `AC-C2-013` | Given FX-C2-RATINGS-K10과 explicit candidate opt-in, When rank, Then calibrated candidate value는 1~5이며 rank에는 기여하지 않는다. |
| `AC-C2-014` | Given K5 candidate 계산, When response, Then 계산 여부와 무관하게 승인 전 displayEligible=false이고 confidence=EVIDENCE_REVIEW_REQUIRED 또는 INSUFFICIENT_DATA다. |
| `AC-C2-015` | Given 검증되지 않은 K2/K4 또는 K와 Rating 개수 불일치, When candidate star 요청, Then ranking은 PARTIAL로 유지되고 VALIDATED_K_INPUT_NOT_AVAILABLE issue다. |
| `AC-C2-016` | Given emitted reason, When response 검사, Then code=POPULARITY_BASELINE이고 ranking policy version 근거만 있으며 개인화 근거 문장이 없다. |
| `AC-C2-017` | Given reason UI decision 미승인, When rank, Then reasons 빈 배열도 schema-valid이며 서버가 임의 표시 개수를 채우지 않는다. |
| `AC-C2-032` | Given artifact rating scale=0.5..5.0과 C1 expected-star contract=1..5, When star candidate 활성화, Then DN-C2-008 전 계산을 거부해 ranking PARTIAL/NOT_COMPUTED와 STAR_SCALE_INCOMPATIBLE issue로 유지하고 clamp하지 않는다. |

## 5. 부분 실패·빈 상태·timeout

| ID | Given / When / Then |
| --- | --- |
| `AC-C2-018` | Given FX-C2-CANDIDATES, When rank, Then accepted 3개는 200 PARTIAL이고 unmapped 1개는 SERVICE_ID_NOT_MAPPED issue다. |
| `AC-C2-019` | Given 모든 후보가 invalid/unmapped/model-missing, When rank, Then 200 EMPTY, items=[], issue만 있고 가짜 fallback item이 없다. |
| `AC-C2-020` | Given FX-C2-STAR-FAIL, When candidate star 요청, Then Popularity 순위는 200 PARTIAL이고 STAR_HEAD_UNAVAILABLE, expectedStar NOT_COMPUTED다. |
| `AC-C2-021` | Given FastAPI 503, connection failure 또는 configured deadline 초과, When Spring adapter 호출, Then future public operation은 503 RECOMMENDATION_SERVICE_UNAVAILABLE이며 stale 성공 body가 없다. |
| `AC-C2-022` | Given recommender 중단, When C1 Rating mutation, Then C1 transaction은 성공하고 recommendationRefresh=QUEUED/outbox retry다. |

## 6. C1 입력과 outbox

| ID | Given / When / Then |
| --- | --- |
| `AC-C2-023` | Given active Ratings와 deleted/unrated/watched=false/LIKE rows, When input snapshot, Then active integer Ratings만 포함한다. |
| `AC-C2-024` | Given 같은 active Rating rows를 다른 DB 반환 순서로 읽음, When inputVersion 생성, Then canonical movieId 정렬로 같은 version이다. |
| `AC-C2-025` | Given FX-C2-C1-OUTBOX, When consumer가 같은 eventId를 두 번 받음, Then downstream 적용은 한 번이고 C1 상태는 변하지 않는다. |
| `AC-C2-026` | Given Rating delete commit, When outbox 처리 후 새 snapshot, Then deleted Rating은 fold-in 입력에서 빠지고 inputVersion이 변경된다. |

## 7. Snapshot·관측·보안

| ID | Given / When / Then |
| --- | --- |
| `AC-C2-027` | Given COMPLETE/PARTIAL 결과, When snapshot 검사, Then artifact/model/checksum/policy/mapping/catalog/candidate/input version이 모두 있다. |
| `AC-C2-028` | Given 후보 20개 중 3개 실제 노출, When persistence 검사, Then recommendation exposure item은 3개만 있고 각각 position과 recommendationVersion을 가진다. |
| `AC-C2-029` | Given 추천 뒤 클릭 없음·Rating 없음, When outcome inference, Then negative/satisfaction record를 자동 생성하지 않는다. |
| `AC-C2-030` | Given service auth 없음/무효/권한 부족, When internal operation, Then 각각 401/403이고 사용자 bearer로 대체하지 않는다. |
| `AC-C2-031` | Given request·artifact 오류, When log/metric 검사, Then token·userId·Rating 값·movieId metric label·artifact path·raw body가 없다. |

## 8. 자동 테스트 묶음

| Test ID | 책임 |
| --- | --- |
| `TEST-REC-C2-CORE` | Python artifact load, UUID mapping, candidate export/store, Popularity, star opt-in, 결정론 |
| `TEST-API-C2-CONTRACT` | FastAPI request/response schema와 error/partial/health |
| `TEST-BE-C2-ADAPTER` | Spring internal client, timeout/503 mapping, Catalog 재검증 |
| `TEST-BE-C2-OUTBOX` | C1 outbox idempotency와 active Rating inputVersion |
| `TEST-BE-C2-SNAPSHOT` | exposure snapshot과 version/attribution |
| `TEST-SEC-C2` | service auth, secret/PII log·metric 검사 |
| `TEST-PERF-C2` | REC-EV-007 Fold-in/timeout benchmark; 수치 Gate 산출 |

`TEST-BE-C2-SNAPSHOT`은
`backend/src/test/java/com/feelm/catalog/c2/recommendation/RecommendationExposurePostgresIntegrationTest.java`
에서 PostgreSQL 17 migration/FK/check, 20개 중 실제 3개만 저장, exact batch replay, 다른 batch의 반복
노출 보존, concurrent exact replay 단일 적용, cross-owner read 0건, 중간 FK 실패 전체 rollback,
safe error/log를 자동 검증한다.
