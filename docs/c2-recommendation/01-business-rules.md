# C2 Recommendation Serving 업무 규칙

> 상태: `APPROVED_C2A_INTERNAL_POPULARITY_ONLY`

## 1. 경계와 식별자

| ID | 규칙 |
| --- | --- |
| `BR-C2-001` | C2 공개 소비자는 Spring뿐이다. FastAPI operation은 내부 service credential을 요구하고 사용자 bearer를 전달받지 않는다. credential 발급·회전 방식은 `DN-C2-004` 전 운영 배포 Gate다. |
| `BR-C2-002` | 요청의 모든 영화 식별자는 C0/C1과 같은 FEELM service UUID다. MovieLens item ID는 HTTP 요청·응답에 노출하지 않고 versioned mapping artifact 내부에서만 사용한다. |
| `BR-C2-003` | Spring은 active Catalog에서 노출 가능한 후보만 요청하고, 응답을 표시하기 직전에도 현재 active projection을 재검증한다. stale candidate를 FastAPI 응답만 믿고 노출하지 않는다. |
| `BR-C2-004` | FastAPI 요청은 userId, email, Authorization 원문, 자유 행동 payload를 포함하지 않는다. 입력은 candidate UUID, active Rating snapshot, version과 correlation ID로 제한한다. |
| `BR-C2-005` | 같은 canonical request와 같은 artifact set은 항목 순서와 score가 결정적이어야 한다. 동점은 service UUID 오름차순으로 해소한다. |

## 2. Candidate와 Rating 입력

| ID | 규칙 |
| --- | --- |
| `BR-C2-010` | `candidateSetVersion`은 batch producer가 만든 불변 후보 집합 버전이다. FastAPI는 후보를 새로 발견하지 않고 전달된 집합만 검증·순위화한다. |
| `BR-C2-011` | 후보는 service UUID로 중복 제거한다. invalid UUID, mapping 없음, model item 없음은 해당 항목만 issue로 제외한다. |
| `BR-C2-012` | `inputVersion`은 Spring이 한 consistent read에서 얻은 active C1 Rating의 `(movieId,value,revision)` 정렬 결과와 입력 정책 버전을 식별한다. Rating 값은 integer 1~5다. |
| `BR-C2-013` | deleted Rating, Rating 없는 ViewingRecord, 온보딩 LIKE/DISLIKE, 미평가, 클릭 없음, `watched=false`는 active Rating fold-in 입력이 아니다. |
| `BR-C2-014` | C1 Rating mutation은 추천 호출을 기다리지 않는다. domain transaction과 outbox가 먼저 commit되고 consumer가 `eventId`를 멱등 처리해 입력 cache/snapshot을 무효화하거나 재생성한다. |
| `BR-C2-015` | batch candidate와 fold-in은 다른 head다. batch는 후보 집합을 제공하고, 현재 Fold-in은 opt-in expected-star 계산에만 사용하며 ranking score에는 기여하지 않는다. |

## 3. Artifact와 readiness

| ID | 규칙 |
| --- | --- |
| `BR-C2-020` | 한 serving set은 Bias, ALS item factors, calibration bundle schema v2, item mapping schema v1 네 artifact와 metadata sidecar로 구성한다. |
| `BR-C2-021` | 네 metadata의 `compatibility_id`, source ID space, rating scale이 같아야 하며 각 payload SHA-256이 sidecar와 일치해야 한다. |
| `BR-C2-022` | calibration metadata는 exact Bias/factor/mapping payload SHA-256, policy version, 분리된 `star_blend`/`ranking` head와 ranking alpha를 bind한다. |
| `BR-C2-023` | mapping은 `IDENTITY_VERIFIED` movie의 `VERIFIED`/`RECOVERED` MovieLens 양의 정수 ID와 service UUID만 담는 versioned 1:1 mapping이다. conflict·invalid·unverified row를 활성 serving index에 넣지 않고, mapping sidecar의 catalogVersion/catalog SHA-256과 payload checksum을 보존한다. |
| `BR-C2-024` | process liveness는 artifact 적합성을 뜻하지 않는다. readiness는 네 payload 검증, family/checksum binding, mapping accepted record, Popularity ranking 호출 가능 조건을 모두 통과할 때만 200이다. |
| `BR-C2-025` | artifact reload는 새 set을 전부 검증한 뒤 원자적으로 교체한다. 검증 실패 시 이전 ready set을 유지하거나, 이전 set이 없으면 readiness 503으로 남는다. 일부 artifact만 교체하지 않는다. |

## 4. Ranking과 expected-star

이 slice의 ranking alpha는 정확히 `0.0`이다. REC-EV-003B candidate는 champion이 아니다.

| ID | 규칙 |
| --- | --- |
| `BR-C2-030` | 현재 ranking은 모든 K에서 `BAYESIAN_POPULARITY_ONLY`이고 Fold-in alpha는 정확히 `0.0`이다. Fold-in 개인화 ranking 개선을 주장하지 않는다. |
| `BR-C2-031` | Popularity는 단순 장애 대체물이 아니라 현재 검증된 ranking 기준선이다. star candidate가 꺼지거나 부분 실패해도 accepted candidate의 Popularity 순위는 반환할 수 있다. |
| `BR-C2-032` | expected-star는 `DISABLED`가 기본이다. `REC_EV_003B_CANDIDATE`는 명시적 feature/config opt-in이며 champion 승격을 의미하지 않는다. |
| `BR-C2-033` | star candidate가 사용하는 K는 artifact policy가 검증한 `0,1,3,5,10,20` 중 요청 Rating 개수와 정확히 같아야 한다. K10은 첫 실질 데이터 후보일 뿐 UI 숫자 표시 승인이 아니다. |
| `BR-C2-034` | `expectedStar.value`는 개인 1~5 척도 예측이며 TMDB 평점·실제 Rating·relative utility가 아니다. |
| `BR-C2-035` | `displayEligible=true`는 `REC-PD-001` 숫자 UI 승인과 versioned confidence policy가 모두 있을 때만 가능하다. 승인 전에는 계산값이 있어도 false이고 confidence는 `EVIDENCE_REVIEW_REQUIRED`다. |
| `BR-C2-036` | `LOW/MEDIUM/HIGH` 경계는 `REC-PD-002`와 REC-EV-007 결과로 versioning한다. 이 문서에서 수치 경계를 만들지 않는다. 입력 부족·미계산은 별도 enum이며 LOW로 위장하지 않는다. |
| `BR-C2-037` | C1 Rating과 제품 expected-star 의미는 `1..5`지만 현재 recommender artifact metadata는 MovieLens `0.5..5.0` scale이다. `DN-C2-008`에서 C1 scale용 재보정 또는 versioned transform을 검증하기 전 star head를 활성화하지 않고, 단순 clamp·반올림으로 불일치를 숨기지 않는다. Popularity-only readiness는 이 Gate와 분리한다. |

## 5. 구조화 이유와 snapshot

| ID | 규칙 |
| --- | --- |
| `BR-C2-040` | reason은 display 문장이 아니라 versioned allowlist code와 실제 scoring 근거다. 현재 순위에 충실한 code는 `POPULARITY_BASELINE`뿐이며 없는 개인화 근거를 생성하지 않는다. |
| `BR-C2-041` | reason 배열은 비어 있을 수 있다. 표시 개수와 상세 수준은 `REC-PD-007`/REC-EV-006 전 결정하지 않으며 Spring이 임의 개수로 채우지 않는다. |
| `BR-C2-042` | Spring은 실제 노출 항목만 `recommendationItemId`, position, recommendationVersion, model/artifact/input/candidate/policy version과 expected-star 표시 상태를 snapshot으로 보존한다. 전체 batch candidate를 노출로 저장하지 않는다. |
| `BR-C2-043` | 추천 후 상세·OTT·감상·Rating attribution은 가능한 경우 `recommendationItemId`를 사용한다. 사건 없음은 negative outcome이 아니다. |
| `BR-C2-044` | 같은 영화의 여러 노출은 각각 보존하고 attribution policy version을 기록한다. 자동 결과는 `추천 결과 효용 추정치`이며 만족도 직접 측정으로 표현하지 않는다. |

## 6. 오류, timeout, 빈 상태와 부분 성공

| ID | 규칙 |
| --- | --- |
| `BR-C2-050` | request schema·UUID·Rating 범위 오류는 422이고 추론을 시작하지 않는다. service credential 없음/무효는 401, 권한 부족은 403이다. |
| `BR-C2-051` | artifact 없음·checksum/family 불일치·ready set 없음은 503이다. incompatible set에서 fallback score를 만들지 않는다. |
| `BR-C2-052` | 일부 candidate만 invalid/unmapped/model-missing이면 accepted 항목을 200 `PARTIAL`로 반환하고 안전한 item issue를 함께 준다. accepted 후보가 없으면 200 `EMPTY`이며 가짜 항목을 만들지 않는다. |
| `BR-C2-053` | star head만 실패하면 Popularity 순위는 200 `PARTIAL`, expectedStar는 `NOT_COMPUTED`, issue는 `STAR_HEAD_UNAVAILABLE`이다. ranking과 star 실패를 하나로 숨기지 않는다. |
| `BR-C2-054` | Spring outbound timeout·connection failure·FastAPI 503은 public recommendation operation에서 503 `RECOMMENDATION_SERVICE_UNAVAILABLE`로 매핑한다. 이전 결과 사용 정책이 승인되기 전 stale result를 성공처럼 반환하지 않는다. timeout 수치는 REC-EV-007의 `DN-C2-005`로 정한다. |
| `BR-C2-055` | FastAPI 장애는 이미 committed C1 Rating·Film·Popcorn transaction을 rollback하거나 실패시키지 않는다. outbox는 재시도한다. |
| `BR-C2-056` | issue/error/log에는 trace/request ID, safe code와 version만 포함한다. token, userId, Rating 값, artifact 경로, raw request body를 message나 metric label에 넣지 않는다. |

## 7. 관측성

| ID | 규칙 |
| --- | --- |
| `BR-C2-060` | metric은 operation/status/outcome, latency, accepted/excluded candidate count, star status, artifactSetVersion, policyVersion을 low-cardinality label 또는 structured field로 기록한다. movieId·requestId·inputVersion을 metric label로 쓰지 않는다. |
| `BR-C2-061` | readiness 실패는 secret/path 없이 failing artifact kind와 safe reason code를 제공한다. liveness와 readiness를 별도로 monitor한다. |
| `BR-C2-062` | recommendationVersion별 노출·후속 funnel을 연결하되 클릭·미평가를 만족/불만족으로 단정하지 않는다. |

## 8. 미결정 Gate

| Gate | 상태 | 구현 차단 범위 |
| --- | --- | --- |
| `DN-C2-001` / `REC-PD-001` | `EVIDENCE_REQUIRED` | 예상 별점 숫자 UI, `displayEligible=true` |
| `DN-C2-002` / `REC-PD-002` | `WAITING_FOR_REC-EV-007` | LOW/MEDIUM/HIGH 경계·policy version |
| `DN-C2-003` / `REC-PD-007` | `EVIDENCE_REQUIRED` | reason 표시 개수·문구·추가 code |
| `DN-C2-004` | `SECURITY_DESIGN_REQUIRED` | service credential 발급·회전·운영 배포 |
| `DN-C2-005` / `REC-PD-008` | `LOCAL_PROVISIONAL_COMPLETE__PRODUCTION_VALIDATION_REQUIRED` | REC-EV-007에서 local timeout 750 ms·healthy freshness 3000 ms 후보 선택. stale success 비활성, 운영 topology 재검증 전 SLA 주장 금지 |
| `DN-C2-006` / `REC-PD-004` | `EVIDENCE_REQUIRED` | exploration 후보·가중치·2+1 조합 |
| `DN-C2-007` / `REC-PD-005` | `EVIDENCE_REQUIRED` | party 집계·공정성 정책 |
| `DN-C2-008` | `EVIDENCE_COMPLETE_FAIL_CLOSED__BLOCKED_PENDING_C1_PAIRED_VALIDATION` | REC-EV-003C에서 clamp/round 기각, affine 보류, star-disabled 선택. `c1-product-star-alignment-pairs-v1`의 held-out C1 evidence 뒤에만 재평가 |
