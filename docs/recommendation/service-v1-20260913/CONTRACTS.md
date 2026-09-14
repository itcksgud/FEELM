# 서비스 추천 v1 — 구현 계약과 연결 검사

상태: **APPROVED — 사용자 확정 제품 결정을 반영한 로컬 구현 기준.**  
팀 공통계약의 승인·코드 수정·DB migration 실행·배포는 아직 하지 않았다. 작성 2026-09-13.

[POLICY](POLICY.md)는 추천 규칙, [MODELS](MODELS.md)는 모델·특징·학습 준비, [OPERATIONS](OPERATIONS.md)는 실행 순서를 정의한다.
[KOBIS 데이터](KOBIS-DATA.md)는 확장 수집의 관측 단위·신원 검토·서비스에 사용할 누적값의 조건을 정의한다.
[상세 유사 영화](SIMILAR-MOVIES.md)는 API-031의 별도 콘텐츠 검색·공용 캐시·상세 탐색 계약이다.
이 문서는 그 규칙을 어느 모듈과 데이터 형식으로 연결하고 어떤 테스트로 완료할지 정한다.
아래 새 경로·필드·migration은 **구현할 대상**이지 현재 팀 코드에 이미 존재하는 요소가 아니다.

## 1. 현재 소스와 변경 경계

읽기 전용 `git ls-remote`로 확인한 원격 develop은 `ae9de07be180027d90e4c809d168fab1e7dad88b`다.
로컬 작업 트리 HEAD는 `b622d5eed62e08823e57d3d14fa7d9bcbdb8d7c8`로8커밋 뒤이며 checkout하지 않았다.
상세 읽기 증거는 [팀 계약 감사](../../../outputs/recommendation-evidence/service-v1-implementation-20260913/team-contract-audit.md)에 있다.

| 소유 영역 | 기존 기준 | 구현할 변경 |
| --- | --- | --- |
| Backend API/DB | `docs/api/recommendation.md`, `streaming-contract.md`, `docs/erd.md`, 추천 migration | 2+1 유지, 30슬롯·입력/세트generation·실제타입/fallback·버전 추적 |
| Backend 이벤트 | `infrastructure/kafka/RatingEventPayload.java`, Publisher/Outbox | 0.5별점 정밀도, 도메인과 inputVersion/Outbox 동시 저장 |
| Pipeline Batch | `pipeline/spark/batch/`, `jobs/batch/`, `contracts/` | 전체/targeted 후보 Producer, T/K·영화 snapshot, 공통 predictor 산출물 |
| Python Consumer | `pipeline/consumer/`는 현재 README만 있음 | 특징 생성·pool500 재정렬·발견10×10·비교adapter·원자적 게시·작업 재시도 |
| Pipeline 설정 | `settings.py`는 읽기/검증 코드, `configs/`는 값 기록 | 서비스 v1 설정 파일과 독립 실행 단위별 validator. 기존 settings.py 이동 금지 |
| Artifact 목록 | `pipeline/artifacts/`는 JSON 출처/해시 목록 | 모델/특징/군집/데이터/후보 manifest. LOCAL_ONLY와 서버배포가능 상태 구분 |
| 공통 ADR | ADR0002/0015/0017 | GBT기본, 배치후보→최신500 재정렬,8×128 내부검색으로 승격할 차이 기록 |

팀 최신 ADR0017은 강한 입력마다 Python이 Candidate500까지 재생성한다. 이번 배치 후보 구조와 같다고
표현하지 않는다. 기존3편UI를 유지하되, 20편버퍼·중간Top100·ALS전용score 의미는 이행 대상이다.

## 2. 버전과 ID

| 이름 | 타입·원본 | 규칙 |
| --- | --- | --- |
| `serviceMovieId` | PostgreSQL bigint, 양의 정수 | API/Redis 후보의 영화ID. TMDB/ML ID를 대신 사용하지 않음 |
| `inputVersion` | PostgreSQL bigint, 사용자별0이상 단조 증가 | 실제 별점·온보딩·확정 감상 추가/삭제·추천 필터 변경 트랜잭션당1증가. 파생 감상이 평점과 함께 바뀌면1회만. 소진/관심없음/노출/클릭만으로 증가하지 않음 |
| `modelVersion` | 길이128이하 불변 문자열 | 종류·가중치·보정·특징계약이 정해진 모델 버전 |
| `featureVersion` | 불변 문자열 | 특징 의미·순서·전처리·결측 처리. RH230와KOBIS245는 별도 |
| `catalogVersion` | 불변 문자열 | 영화·원천·적격성·매핑 snapshot 식별 |
| `policyVersion` | 불변 문자열 | 후보하한/순위/군집검색/K/H/출력규칙 식별 |
| `bundleId` | UUID | 위 버전·고정 군집·원천/특징 해시를 결합한 완성 묶음 |
| `poolVersion`, `rankingVersion` | UUID | 불변 결과 식별자. UUID의 대소로 최신 여부를 비교하지 않음 |
| `jobId`, `desiredJobToken` | UUID | 논리 작업·현재 원하는 작업. pool/ranking 종류별 독립 상태. 같은 작업 재시도는 같은ID 사용 |
| `sessionId` | DB UUID | 최초추천/명시새탐색에서생성. 일반추가·소진·입력변경에서는유지 |
| `exposureRevision` | session별DB bigint,0이상단조증가 | 실제노출·활성예약생성/해제의실제변화트랜잭션에서증가. 단순재시도는불변 |
| `setGeneration` | DB사용자별 양의 bigint | 입력이 같아도 소진 후 새 세트를 구분. inputVersion과 독립 |

MovieLens 사용자를 서비스 사용자에 매핑하지 않는다. 영화는 실제 DB의 `tmdb_id` export를 기준으로 연결한다.
KOBIS 코드는 검증된 매핑과 상태를 별도 보관한다. 제목이 비슷하다는 이유로 자동 확정하지 않는다.

KOBIS 원행의 신원과 서비스 영화의 신원을 별도 검증한다. 공식 화면에서 직접 확인한 KOBIS 코드도
그 자체로 TMDB·serviceMovieId·MovieLens ID를 확정하지 않는다. 서비스 DB의 `serviceMovieId↔tmdb_id`
export와 검토를 통과한 TMDB↔KOBIS crosswalk를 함께 사용해야 서비스 영화에 KOBIS 값을 붙일 수 있다.
코드 없는 원행의 KOBIS 코드 제안, 직접 코드 영화의 TMDB 제안, 코드 없는 원행의 TMDB 제안은
검토 대상에만 보관하며 확정 crosswalk와 분리한다. 제안을 근거로 MovieLens 평점이나 서비스 ID를 붙이지 않는다.

## 3. 스냅샷과 모델 산출물

새 공통 계약을 `pipeline/contracts/recommendation-v1-artifacts.md`에 구현한다.
개인 PC 경로나 이동하는 latest 파일만으로 재현을 표현하지 않고 불변 URI·크기·SHA256을 기록한다.

| 산출물 | 필수 내용 |
| --- | --- |
| `bundle-manifest.json` | contractVersion, bundleId, model/feature/catalog/policyVersion, source snapshot 목록, 각 artifact URI/bytes/hash, 완료시각 |
| 영화 특징 | serviceMovieId·TMDB ID, 특징배열·원천validity·후보eligibility, 특징순서와차원, 누락상태 |
| T/K source | TMDB 평균/투표/원천시각, KOBIS 관객/대상기간/누적범위/매핑상태, raw validity와candidate source pass 별도 |
| 군집 | 고정8맛 매핑·하위128중심·전처리·그룹대표·할당해시, 원본영화ID, 그룹별영화목록 |
| 그룹순위 | 그룹ID·serviceMovieId·source별rank·최종후보rank와동률규칙, 적격출처만기여 |
| 공용 pool | 같은bundle의배치T/K초기후보 최대500, source lineage |
| 모델 manifest | modelKind(GBT/FM/ALS), profile, readiness, native artifact, Python추론artifact, calibrator, feature schema, native-serving parity 증거 |
| 개인 pool | 최대500고유serviceMovieId, bundleId, poolVersion, builtFromInputVersion, jobId, 생성시각, 후보선정근거/rankingMode, excludedSessionId·excludedExposureRevision·excludedExposureHash |
| 소진제외스냅샷 | session의실제노출+다른활성예약serviceID를정렬/고유화한목록,sessionId,exposureRevision,hash,불변URI |

모델 profile은 `legacy-rh230`와 `service-v1-kobis245`를 구분한다. v1의 245차원 학습 artifact가 없으면
MODELS의 `buildStatus=NOT_BUILT`이며 운영 `readiness=NOT_READY`다. 기존 230 모델에 15열을 붙이거나 dv2의 `variant=original`을 GBT로 호출하지 않는다.
GBT/FM/ALS adapter는 MODELS의 공통 인터페이스와 같은 후보를 사용하고 자동 혼합하지 않는다.
추가한 ALS+GBT·ALS+FM은 [별도 비교 registry](comparison-registry.v1.json)의 shadow 정책이다.
단독 `modelKind`와 합성 `comparisonPolicyId`를 구분하고, 두 component의 모델/특징/보정
버전·ALS 학습 count 출처를 [결합 비교 행](HYBRID-COMPARISONS.md)에 함께 기록한다.
공개 rankingMode enum·기본 GBT 활성 포인터·DB/API 반환 계약은 이 확장으로 바꾸지 않는다.

그룹별 목록은 적격 영화의 전체 순서를 보관한다. 사전에 Top10만 잘라 저장한 뒤 감상을 제외하는 방식은 쓰지 않는다.
사용자 제외를 적용한 현재 목록에서 source별 rank를 다시 세고 처음10편을 선택한다.

`T_VALID`, `K_VALID`처럼 실제로 읽은 값이 유효한지와 `T_CANDIDATE_PASS`, `K_CANDIDATE_PASS`는 별도다.
추천 하한에 못 미치는 영화도 사용자 평가 이력 특징으로 사용할 수 있다. 선택한 최신 원본10편을 후보 하한으로 먼저 거르지 않는다.
KOBIS 미매칭·수집 실패·범위 불일치는 유효 관객 0과 다르다. 서비스 `movies.average_rating/rating_count`를 TMDB 필드로 덮어쓰지 않는다.

### KOBIS 원행에서 서비스 스냅샷까지

2026-09-13 [확장 수집 인계](../../../outputs/recommendation-evidence/kobis-expanded-20260913/delivery-manifest.json)는
2004~2025년 전체 연도와 2026-09-12까지의 부분 연도에 대한 영화×상영연도 관측72,700행이다.
전체 행을 보존했지만 직접 코드는 연도별 처음100행의2,300행·고유2,176코드에서만 확인했다.
코드 미확정70,400행은 고유 영화 수가 아니다. KOBIS 코드 제안2,682행, 직접 코드 영화의 TMDB 제안1,792건,
코드 미확정 원행의 TMDB 제안13,779행은 확정 매핑이 아니며, 현재 교차 원천 확정 ID는0건이다.
[수집 검토 PASS](../../../outputs/recommendation-evidence/kobis-expanded-20260913/result-review.json)와
[연결 v2 검토 PASS](../../../outputs/recommendation-evidence/kobis-expanded-20260913/result-link-v2-review.json)는
원행 보존·계산·제안 분리에 대한 검토이지 서비스 매핑 승인이나 모델 준비 완료가 아니다.

아래는 기존 `T/K source`의 대상기간·누적범위·매핑상태 및 source snapshot/manifest가 보존해야 하는
**논리 정보의 구체화**다. 새 DB 열·API 필드·runtime 설정·특징 열을 이 문서 갱신으로 추가하지 않는다.

| 단계 | 보존할 정보와 다음 단계 조건 |
| --- | --- |
| 원행 staging | 원본 파일 hash·원행 위치, 조회 대상기간 시작/끝, 통계 기준일과 수집 시각의 구분, 부분 연도 여부, 원문 관객·매출 및 음수 정정값. 연간 기간값과 조회 종료일 기준 누적값은 별도 의미로 보존 |
| 신원 제안·검토 | 직접 코드의 원문 근거, 제안 후보·방법·모호성, 검토한 증거와 버전. 미확정 행은 원행 단위로 남기며 이름·제안 ID만으로 영화별 합치기 금지 |
| 검토된 crosswalk | 서비스 DB↔TMDB 매핑 export의 버전/해시와 TMDB↔KOBIS 검토 근거/버전. 충돌·다중 후보·미검토 제안은 서비스 연결 대상에서 제외 |
| 정규화 KOBIS source | 확정 신원의 영화별로 동일한 정의의 누적 범위·선언한 통계 기준일/cutoff·검증된 누적값을 선택. 선택한 원행·원천 hash와 매핑 증거 버전으로 역추적 가능해야 함 |
| 특징·후보 bundle | 위 정규화 source를 영화 특징, 최신 선택 이력의 반응 특징, K 후보 하한·그룹순위가 공통으로 사용. 데이터·매핑·특징 해시를 같은 catalogVersion/bundleId에 연결 |

연도별 원행을 합산해 누적 관객을 만들거나, 조회기간/누적범위가 다른 값 중 최댓값을 골라 섞지 않는다.
직접 관측한 마지막 `period_end`의 누적값은 그 날짜의 관측이며 현재 snapshot 또는 개봉 이후 전체 생애 누적의
증거가 아니다. 최근에 수집했다는 사실로 과거 통계 기준일을 현재로 바꾸지 않는다. 현재 snapshot이 필요하면
신원이 확정된 영화에 대해 선언한 cutoff와 누적 범위를 맞춘 값을 검증한 뒤 선택한다.
음수 정정16행을 포함한 raw 값은 보존하고, 원천 숫자의 파싱 성공과 서비스에서 사용할 유효 누적값을 구분한다.
미확정·결측·수집실패·범위불일치는0으로 채우지 않는다. raw 수집과 매핑 검토 대상은 Top100/200으로 자르지 않는다.

임시 경로에 전체 산출물을 쓴 뒤 재로딩·해시·행수·ID·중복·유한값을 검증하고 manifest를 마지막에 완료한다.
기존 버전을 덮어쓰지 않는다. 게시 전 모델과 feature/catalog/policy의 호환성을 검사한다.
KOBIS 게시에는 원행 무결성과 별도로 검토된 service/TMDB↔KOBIS 매핑, 동일 누적 정의·선언한 cutoff,
후보·이력 특징의 source 일치를 검사한다. 해시 일치만으로 매핑을 유효하게 만들지 않는다.
확장 수집만으로 KOBIS245 특징 또는 모델을 활성화할 수 없으며 `buildStatus=NOT_BUILT`, `readiness=NOT_READY`는 그대로다.

## 4. Redis v1 namespace와 게시 조건

기존 `feelm:user:{id}:candidates/topn/vector`는 ALS 계약 소비자가 있으므로 조용히 덮어쓰지 않는다.
새 서비스 v1은 별도 namespace를 쓰고 API/Party/OTT 소비자를 명시적으로 이행한다.
중괄호는 문서의 ID자리표시자이며, 실제 Redis Cluster 도입 시 사용자별 원자적 연산의 hash-slot을 별도로 맞춘다.

| 새 키 | 값 / 작성자 |
| --- | --- |
| `feelm:rec:v1:bundle:active` | 활성bundleId / 배치게시자 |
| `feelm:rec:v1:bundle:{bundleId}` | 완성manifest/모델준비상태/공용후보참조 / 배치게시자 |
| `feelm:rec:v1:user:{id}:pool:{poolVersion}` | 불변개인pool / 배치후보게시자 |
| `feelm:rec:v1:user:{id}:pool:active` | bundleId,poolVersion,builtFromInputVersion / CAS게시자 |
| `feelm:rec:v1:user:{id}:ranking:{rankingVersion}` | 불변재정렬결과 / Consumer |
| `feelm:rec:v1:user:{id}:ranking:active` | bundleId,poolVersion,rankingVersion,rankingInputVersion,sessionId / Consumer |
| `feelm:rec:v1:user:{id}:desired-job:pool` | jobId,desiredJobToken,expectedBasePoolVersion,targetInputVersion,bundleId,제외scope / 작업조율기 |
| `feelm:rec:v1:user:{id}:desired-job:ranking` | jobId,desiredJobToken,expectedRankingVersion,targetInputVersion,expectedPoolVersion,bundleId,sessionId / 작업조율기 |

`poolVersion`/`rankingVersion`은 새 v1에서 순서가 없는 UUID다. 기존 숫자 candidateVersion의 크기 비교를 복사하지 않는다.
입력 최신성은 DBinputVersion과 동등 비교한다. pool은 해당 종류의 desiredJobToken과 expectedBasePoolVersion,
ranking은 별도 desiredJobToken과 expectedRankingVersion 및 참조 pool 조건으로 작업 역전을 막는다.
Redis 유실 뒤 옛 숫자 1을 재사용해서 과거 파티 캐시를 현재 결과로 오인하는 문제를 피한다.

### 개인 pool payload

```json
{
  "contractVersion": "feelm-recommendation-v1",
  "poolVersion": "<uuid>",
  "bundleId": "<uuid>",
  "builtFromInputVersion": 17,
  "jobId": "<uuid>",
  "provisionalPool": false,
  "excludedSessionId": null,
  "excludedExposureRevision": null,
  "excludedExposureHash": null,
  "items": [{ "serviceMovieId": 1001, "batchRank": 1 }]
}
```

배치 점수를 저장하더라도 디버깅값이며 최신 재정렬 점수로 사용하지 않는다. 후보는 최대 500개의 고유한 정상 service ID여야 한다.
정기/targeted Producer 모두 K≥10·모델/이력 지원 조건이면 GBT, K<10이면 초기 콘텐츠/대중 정책으로
전체 적격 카탈로그를 정렬해500편을 만든다. 콜드 사용자에게 GBT 후보 생성만 강제하지 않는다.

일별 일반 pool은 세션 노출을 영구 제외하지 않는다. `triggerReason=POOL_EXHAUSTED`인 targeted job만
현재 session의 실제 노출·다른 활성 예약 스냅샷을 전체 카탈로그 검색에 적용해 다음500을 만든다.
이 pool은 excludedSessionId가 non-null인 session 한정 결과다. 새 session에서는 폐기/비활성화하고
일반 pool 또는 공용500을 임시 사용하면서 새 후보 job을 만든다. 일반 pool의 제외 scope 필드3개는 모두null이다.
session 한정 pool은3개가 모두존재해야하며, 검증되지않은hash/다른session에서읽을수없다.

### ranking payload

```json
{
  "contractVersion": "feelm-recommendation-v1",
  "rankingVersion": "<uuid>",
  "sessionId": "<uuid>",
  "poolVersion": "<uuid>",
  "bundleId": "<uuid>",
  "rankingInputVersion": 18,
  "builtFromInputVersion": 17,
  "candidateRefreshPending": true,
  "provisionalPool": false,
  "modelKind": "GBT",
  "modelProfile": "service-v1-kobis245",
  "modelReadiness": "READY",
  "rankingMode": "MODEL_GBT",
  "personalizedItems": [],
  "discoveryItems": [],
  "discoveryTrace": { "supplyGroups": 10, "candidateCount": 100 }
}
```

위 빈 배열은 구조 예시이며 실행 결과가 아니다. `READY`는 유효 artifact·parity·평가 게이트를 통과한 경우에만 기록한다.
`rankingMode`는 MODEL_GBT/MODEL_FM/MODEL_ALS/INITIAL_CONTENT/INITIAL_PUBLIC을 구분하며 fallback을 모델 추론이라고 표시하지 않는다.
FM/ALS는 기본적으로 shadow 비교 결과에만 사용하며 공개 응답 모델을 자동으로 바꾸지 않는다.

`personalizedItems`는 정렬한 전체≤500, `discoveryItems`는 이번10×10 공급에서 정렬한 전체≤100이다.
Consumer는20/10으로 먼저 자르지 않는다. Spring이 각 라운드의 맞춤2편을 먼저 고르고 발견1편을 공통중복을
건너뛰어 고르는 순서로10라운드를 조립한 뒤, 최종 최대30슬롯을 예약한다. 정상 목표는 맞춤20/발견10이다.
발견의 현재≤100 공급을 다 확인해도 부족하면 POLICY의 동일조건 재공급을 한 번 수행하고, 이후 명시적 맞춤 대체를 적용한다.

같은 bundle의 이전 pool membership은 재사용할 수 있지만 ranking은 현재 inputVersion의 특징으로 만든다.
Spring은 DB 입력 버전과 rankingInputVersion이 다르거나, 활성 pool과 결과가 참조한 pool이 다른 순위를 새 세트에 사용하지 않는다.
ranking은 현재sessionId도 일치해야한다. 새 탐색은 inputVersion이같아도새session의제외로재정렬하며옛session결과를활성화하지않는다.

게시 순서는 불변 payload 저장→재읽기 검증→예상 active pointer/job token을 조건으로 단일 pointer 교체다.
동일 inputVersion이라도 다른 job이 먼저 게시했다면 expectedBasePoolVersion이 달라져 이전 job은 실패한다.
ranking 게시에는 별도로 desired-job:ranking의 token과 expectedRankingVersion, 현재 pool/input/bundle/session을 비교한다.
같은 inputVersion·poolVersion에서 시작한 두 ranking 작업도 기존 활성 ranking이 바뀌면 이전 작업 게시를 거부한다.
pool과 ranking의 desired token은 서로 덮어쓰지 않는다. pool 완료가 다음 ranking을 요청할 때도 ranking 상태만 바꾼다.
소진 재공급은 현재 active session과 excludedSessionId, exposureRevision/hash도 함께 비교한다.
그사이 실제 노출·다른 활성 예약이 바뀌면 작업을 supersede하고 새 제외 스냅샷으로 재생성한다.
동일 입력의 일반500을 반복 생성해 POOL_EXHAUSTED가 영구 반복되는 동작은 허용하지 않는다.
입력·계정 상태는 게시 직전과 Spring 세트 생성 시 다시 확인한다. DB와 Redis가 하나의 트랜잭션이라고 가정하지 않는다.
입력 검사 뒤 다시 평가가 바뀌더라도 Spring의 최종 버전 검사가 이전 결과 노출을 막아야 한다.

## 5. 이벤트와 작업 요청

RATING/ONBOARDING은 기존 이벤트 topic과 Outbox를 유지한다. payload는 변경 요약이며 실제 계산에는 최신 DB를 조회한다.
별점 score는 BigDecimal 등 소수 정밀도를 보존하는 DTO와 JSON 숫자로 표현한다. DELETE는 score를 생략한다.

확정 감상만 추가/삭제되거나 추천 필터가 바뀌는 경우는 새 `RECOMMENDATION_INPUT_CHANGED` 이벤트
(`feelm.recommendation-input.v1`)로 알린다. `reason=CONFIRMED_WATCH_CHANGED` 또는 `FILTER_CHANGED`,
`inputVersion`을 기록하고 공통 eventId/userId/occurredAt 봉투와 Outbox를 사용한다. Consumer는 동일한 최신 DB 재계산 경로로 처리한다.
평점과 파생 감상이 같은 트랜잭션에서 바뀌면 inputVersion을1회만 올리고 RATING 이벤트로 대표해 중복 증가하지 않는다.
온보딩 이벤트도 저장된 값에 실제 변화가 있을 때만 증가한다. 확정 감상의 그룹 분자/분모는 최신 상태에서 다시 계산한다.
관심없음은 `DISMISS` 제외와 현재 라운드 revision 처리로 끝내며 inputVersion을 올리지 않는다. 노출/클릭은 확정 감상으로 변환하지 않는다.

내부 추천 작업은 `feelm.recommendation-jobs.v1`에서 다음 논리 작업을 전달하는 계약을 추가한다.
사용자 기능 이벤트와 작업 제어 이벤트를 혼용하지 않는다. Topic·ACL·Consumer Group의 실제 배포값은 팀 환경 설정에 주입한다.

| jobType | payload 핵심 | 완료 후 |
| --- | --- | --- |
| USER_POOL_BUILD | userId,jobId,targetInputVersion,bundleId,desiredJobToken,expectedBasePoolVersion,triggerReason,excludedSessionId,excludedExposureRevision,excludedExposureHash,exclusionSnapshotUri | 현재입력경로와해당제외scope로Spark후보생성. CAS게시후USER_RERANK |
| USER_RERANK | userId,sessionId,jobId,desiredJobToken,expectedRankingVersion,targetInputVersion,bundleId,expectedPoolVersion,triggerReason | ranking종류별CAS상태와최신입력/session을대조해500전부/발견10×10재정렬 |
| USER_PURGE | userId,jobId,계정상태변경참조 | 사용자pool/ranking desiredjob둘다무효화,캐시·pool비활성화,배치/백필제외 |

작업 조율기는 기존 실행 단위의 제어 코드로 구현할 수 있으며 새 별도 서비스 배포를 전제로 하지 않는다.
한 논리 작업의 재시도는 같은 jobId와 출력 version을 재사용한다. 같은 사용자의 새 입력은 새 token을 발급해 오래된 작업의 게시를 차단한다.
조율기는 최초 접수 때 논리 요청의 멱등 키와 불변 job payload를 내구 저장하고, Kafka 전송 재시도에서는 그 기록을 재사용한다.
원본 eventId/명시 requestId/배치 실행 ID와 jobKind를 멱등 키에 포함한다. 새 입력·bundle로 대체하는 작업은 별도 요청으로 기록한다.
이미 완료한 동일 job의 재수신은 완료 결과를 확인해 종료하며, 그 작업의 token을 현재 원하는 작업으로 다시 등록하지 않는다.
재조회한 DB 입력이 targetInputVersion보다 새로우면 기존 작업을 superseded로 처리하고 새 job/version을 만든다.
이 검사는 별점 변화뿐 아니라 확정 감상과 추천 필터 변경에도 동일하게 적용한다. triggerReason은 진단 근거이며
payload의 reason만 보고 DB 감상 상태 재조회를 생략하지 않는다.
triggerReason은 RATING_CHANGED/ONBOARDING_CHANGED/CONFIRMED_WATCH_CHANGED/FILTER_CHANGED/POOL_MISSING/POOL_EXHAUSTED 등을구분한다.
POOL_EXHAUSTED만 현재session제외스냅샷을필수로사용한다. snapshotUri가가리킨정렬된ID목록과hash를검증하고,
게시때현재session/revision이달라졌으면새job/version으로재준비한다. 같은job재시도는처음스냅샷을임의교체하지않는다.
같은 불변 version에 다른 입력의 결과를 덮어쓰지 않는다. 새 작업 요청 전달을 확인한 뒤 이전 요청 처리를 완료한다.
원본 기능 이벤트의 접수와 작업 실행은 다음 두 단계로 구분한다.

1. **접수 Consumer:** 최신 DB 입력으로 pool/ranking 종류별 작업을 정하고, 내부 Kafka에 필요한 job 메시지를
   등록해 Broker 성공 응답을 받은 뒤 원본 이벤트 offset을 커밋한다. Spark 완료를 기다리지 않는다.
   둘 중 한 메시지만 등록한 뒤 실패하면 같은 논리 jobId로 재시도하며, 이미 등록한 쪽의 중복은 token/version으로 흡수한다.
2. **작업 Worker:** pool/ranking을 별도 실행 대기열 또는 Consumer Group으로 처리하여 긴 pool 작업이 즉시 재정렬을 막지 않게 한다.
   결과의 원자적 게시와 후속 요청 전달을 완료한 뒤 해당 job 메시지 offset을 커밋한다. 실패하면 미완료 offset을 유지하고
   같은 job/version을 재시도한다. superseded 작업은 새 작업 요청이 필요한 경우 그 등록을 확인하고 종료한다.

작업 조율기만 원하는 token을 발급한다. Redis 유실로 token이 없어진 오래된 Worker가 자기 token을 다시 등록해
최신 작업으로 부활해서는 안 된다. 현재 DB/bundle에서 새 복구 작업을 등록한다.
단계별 병렬 처리에서 동일 파티션의 연속 완료 구간만 커밋한다. 장시간 작업은 poll/heartbeat와 계산 실행을 분리한다.
약한 행동 누적은 eventId 중복 제거와 이벤트 시각을 사용하며, 평가 순서 역전은 DB 재조회로 처리한다.

## 6. API와 DB migration

### API 연결

| API | v1 유지/추가 |
| --- | --- |
| API036/037 평가등록·수정, API038 삭제 | 실제0.5별점, inputVersion 증가·세트무효화·Outbox와동시트랜잭션 |
| API024 온보딩 | 실제ratings와별도저장유지. 초기경로/특징정책은POLICY/MODELS |
| API035 감상확인·확정감상삭제 사용사례 | 실제확정감상집합이바뀌면 inputVersion/세트무효화/RECOMMENDATION_INPUT_CHANGED를 같은트랜잭션으로저장. 감상삭제의팀API번호는임의차용하지않음 |
| API045/046 추천필터입력 | 정규화한필터값을현재상태와비교. 실제변경이면 inputVersion/세트무효화/FILTER_CHANGED 이벤트저장. 같은필터재시도는증가없음 |
| API045 GET /recommendations | 최초session생성/기존session유지. 현재입력의2맞춤+1발견반환. 같은활성세트재사용,최종제외 |
| API046 POST /recommendations/more | sessionId와requestId(UUID)를추가해같은논리요청재시도멱등처리. 새라운드최대3편 |
| startRecommendationSession POST /recommendations/sessions | requestId필수/expectedSessionId nullable. 명시새탐색,현재session CAS·미소비예약해제. 새팀API번호임의부여없음 |
| API047 관심없음 | 같은세트내교체,별점/inputVersion변경없음. 발견대체의actualType을정확히반환 |
| API048 추천이력 | 노출된항목만. 당시bundle/ranking/input/실제유형/fallback사유보존 |
| API013 회원탈퇴 | 원본개인데이터삭제계약에추천캐시/작업/백필비활성화를연결 |

기존 `type=TASTE/DISCOVERY`는실제로제공한유형이다. 추가필드는다음과같다.

```json
{
  "type": "TASTE",
  "requestedType": "DISCOVERY",
  "actualType": "PERSONALIZED",
  "fallbackReason": "DISCOVERY_EXHAUSTED",
  "rankingMode": "MODEL_GBT"
}
```

`actualType=PERSONALIZED`는 맞춤 큐로 대체했다는 뜻이다. 초기 대중 경로면 rankingMode=INITIAL_PUBLIC로 표시하여
GBT가 예측했다고 주장하지 않는다. 같은 requestId 재시도에서도 새로운 관심없음·부적격은 다시 노출하지 않고 교체 이력을 재사용한다.
요청 사용자·세트 소유권을 검사하고, inputVersion이 바뀐 세트는 기존 409 RECOMMENDATION_SET_STALE 계약으로 거부한다.

세트 조회·추가·라운드 응답은 `sessionId`를 포함한다. 명시 새 탐색의 요청은 다음과 같다.

```json
{
  "requestId": "<uuid>",
  "expectedSessionId": null
}
```

첫 탐색은 expectedSessionId를 JSON null로 보낸다. 이미 같은 requestId로 완료한 요청이면 이전에 만든 sessionId를 반환하며
이전 session을 다시 활성화하지 않는다. 첫 실행에서는 현재 active session과 expectedSessionId가 다르면409로 거부한다.
인증된 사용자 자신의 session만 다룬다. 새 session·활성 포인터·이전 미소비 예약 해제를 같은 DB 트랜잭션으로 처리한다.
명시 새 탐색을 제외한 버퍼 소진·inputVersion 변경·추가/재시도는 session을 바꾸지 않는다. session 활동 TTL은 도입하지 않는다.

### 구현해야 할 migration

기존 migration을 수정하지 않고 새 파일에서 적용한다. 아래 논리 이름을 팀 명명 규칙에 맞춰 확정하고 같이 테스트한다.

| 변경 | 구체내용 |
| --- | --- |
| 사용자추천상태테이블 | `recommendation_user_states(user_id PK/FK,input_version bigint NOT NULL CHECK>=0,next_set_generation bigint>0,active_session_id nullable,active_set_id nullable,filters_json jsonb NOT NULL)` 신설. Backend가쓰기소유 |
| 추천session | recommendation_sessions에id UUID PK,user_id,exposure_revision bigint NOT NULL DEFAULT0 CHECK>=0,created_at,closed_at저장. 사용자별활성session1개부분유일제약. 추천세트에session_id FK추가,과거이력은null LEGACY로보존 |
| 새session요청멱등성 | recommendation_session_requests에user_id,request_id,expected_session_id,new_session_id저장. UNIQUE(user_id,request_id). 동일requestId가다른본문이면충돌로거부 |
| 작업요청멱등성 | recommendation_job_requests에job_id UUID PK,user_id,job_kind,idempotency_key UNIQUE,immutable_payload jsonb,payload_hash,status저장. 최초접수시불변ID/출력version을기록하고전송/작업재시도에서재사용. 완료·superseded상태가원하는token을되살리지않게함 |
| 세트generation | recommendation_sets에개인사용자별generation bigint추가. 기존행은(user_id,generated_at,id)순으로백필. 개인행은generation>0 |
| 기존UK교체 | `uix_recommendation_sets_user_input_model`만제거하고개인(user_id,generation)유일인덱스생성. 같은input/model로새세트허용. Party기존제약은별도범위로보존 |
| input_version 타입확정 | 최종 `recommendation_sets.input_version`은 **bigint NOT NULL CHECK(input_version>=0)**. 기존varchar값은legacy_input_version에원문보존하고별도LEGACY표시로이행. 아래고정순서사용 |
| 버전증거 | 세트에bundle_id,pool_version,ranking_version,policy_version,feature_version,catalog_version,built_from_input_version,ranking_input_version 추가. 과거행은LEGACY상태/null허용으로구분 |
| 모델길이 | `model_version varchar(128) NOT NULL`로변경한다. 길이를절삭해버전충돌을만들지않음 |
| 라운드멱등성 | recommendation_rounds에user_id,session_id,set_id,round_no,request_id,할당item참조저장. UNIQUE(user_id,request_id),UNIQUE(set_id,round_no),세트의session/소유자와일치검증 |
| 실제유형 | recommendation_items에requested_type,actual_type,fallback_reason,ranking_mode추가. 기존type은실제제공유형으로유지 |
| 예약과실제노출 | unexposed추천items는활성세트의예약으로취급. reservation_released_at추가. 새session/강한입력에서옛미소비예약만해제하고exposed_at/반환이력보존. 노출·활성예약집합변화는동일트랜잭션의exposure_revision증가 |
| 기존행백필 | 기존type에맞춰requested/actual유형만채우고알수없는모델/원천버전은추측하지않음. user_states다음generation은사용자기존최대+1 |

input_version 이행은 다음 한 가지 순서로 고정한다.

1. 추천 쓰기를 중지한 migration 구간에서 기존 개인 유일 인덱스를 제거하고 기존 `input_version`을
   `legacy_input_version varchar(100)`으로 이름 변경한다. 원문은 변환·해시·추정 없이 보존하고 NOT NULL은 해제한다.
2. 새 `input_version bigint NOT NULL DEFAULT 0 CHECK(input_version>=0)`와 `record_contract_version`을 추가한다.
   기존 행은 record_contract_version=LEGACY, input_version=0으로 명시하고 **새 입력을 검증한 결과로 사용하지 않는다**.
   비숫자 과거값을 bigint로 캐스팅하거나 과거 입력 순서를 만들어내지 않는다.
3. 현재 사용자 상태는 input_version=0으로 시작해 현재 원본에서 새 v1 결과를 생성한다. 이후 모든 강한 입력 트랜잭션마다1씩 증가한다.
   신규 v1 세트는 state의 bigint를 명시 저장하며 legacy_input_version=NULL, record_contract_version=feelm-recommendation-v1이다.
4. 모델 컬럼을 varchar(128)로 넓히고, 세트 generation을 백필한 뒤 개인(user_id,generation) 유일 인덱스를 적용한다.
5. 기존 Party 유일 인덱스는 이름 변경을 따라 legacy_input_version 원문에 연결된 상태로 보존한다.
   공통 테이블의 기존 input_version에 문자열을 쓰는 모든 작성자는 전환 전에 조사한다. 미이행 Party 작성자가 있으면 migration 공개를 중단하며,
   Party의 새 bigint 입력 버전 작성/소비는 별도 이행 계약을 맞춘 뒤 활성화한다. 이번 개인 v1은 party_id=NULL로만 새 행을 쓴다.

LEGACY 행의0은 과거 입력이 같다는 증거가 아니다. API는 LEGACY를 이력으로만 읽고 활성세트 후보에서 제외한다.
인수 테스트는 비숫자 legacy 입력, 같은 모델의 여러 옛 세트, 사용자/Party 이력 보존과 새 bigint 음수 거부를 포함한다.

평가 DTO의 현재 Integer 문제는 DB migration만으로 해결되지 않는다. RatingEventPayload와 Controller/Service 입력 DTO,
JSON 직렬화 검증을 같은 변경에 포함한다. BigDecimal 4.49를 4.5로 반올림해 통과시키지 않는다.
세트 generation을 입력 version으로 대체하는 우회도 금지한다.

## 7. 기존 키·Party·OTT 이행

1. 새 v1 namespace와manifest를생성하되기존ALS키를변경하지않는다.
2. 개인추천API가v1계약을읽도록전환하고,구버전consumer/API를같은사용자의활성키작성자로혼용하지않는다.
3. Party/OTT가읽는원시score의의미와modelKind/calibration·버전cache키를별도계약에서확인한다.
4. 동일조건비교가필요한FM/ALS결과는모델별별도bundle에보존한다. 읽기실패를이유로다른모델결과를조용히대체하지않는다.
5. 모든소비자이행과rollback검증이끝난뒤에만기존키정리대상을확정한다. 이번개인추천v1문서가Party/OTT순위식을승인한것은아니다.

## 8. 요구사항 → 구현 → 인수 테스트

이ID는v1운영/공통계약의추적ID이며연구실험번호가아니다. 테스트는아직실행하지않았다.

| 요구사항 | API/사용사례 | Entity/event·소유모듈 | 인수테스트ID와통과조건 |
| --- | --- | --- | --- |
| R-v1-OPS-01 실제별점보존 | API036/037/038 | ratings/RatingEventPayload·Backend | T-v1-01:0.5,4.5,5.0 DB→Outbox→JSON보존;4.49/5.5거부;DELETEscore생략 |
| R-v1-OPS-02 배치500+최신재정렬 | API045/046 | pool/ranking·Batch/Consumer | T-v1-02:후보500전부현재특징으로점수화;101~500위영화가재정렬1위로승격가능;API내전체카탈로그추론없음 |
| R-v1-OPS-03 콜드사용자 | 최초추천/강한입력 | sharedpool/USER_POOL_BUILD | T-v1-03:K0/9/10 임시경로;K<10 targeted가전체적격을초기콘텐츠/대중으로정렬하고GBT를강제하지않음;완료후새pool재정렬 |
| R-v1-OPS-04 입력역전방지 | 평가/온보딩/감상/추천필터변경 | inputVersion/RECOMMENDATION_INPUT_CHANGED | T-v1-04:확정감상만추가삭제해도버전/그룹분자분모갱신;필터변경갱신;평점+파생감상한트랜잭션1회;관심없음/노출/클릭만은불변;늦은job게시거부 |
| R-v1-OPS-05 발견10×10 | API045/046 | 그룹순위/discoveryTrace·Consumer | T-v1-05:0공급은skip,1편공급도1그룹;최대10그룹/그룹10편;11번째그룹/영화로보충안함 |
| R-v1-OPS-06 대중근거하한 | 후보배치/이력특징 | sourcevalidity/eligibility | T-v1-06:TMDBvotes20/21,KOBIS9999/10000경계;양쪽fail후보제외;같은영화의실제이력/H선정은유지 |
| R-v1-OPS-07 2+1·fallback | API045/046/047 | ranking전체목록/세트/라운드 | T-v1-07:Consumer P≤500/D≤100 전체게시;P2→D1 라운드탐욕조립에서교차중복뒤P21+/D11+를사용해재료손실없음;그뒤30슬롯예약;발견재공급1회후명시대체 |
| R-v1-OPS-08 동시성·멱등 | API046 | recommendation_rounds/userstates | T-v1-08:동일requestId동시2요청은1라운드소비;서로다른요청은중복영화없음;입력변경후옛세트409 |
| R-v1-OPS-09 세트소진·migration | API046 | bigintinput/generation/개인UK | T-v1-09:동일input/model의새세트성공;generation만증가;비숫자legacy원문/Party이력보존;LEGACY활성화금지;inputbigint음수거부;model128보존 |
| R-v1-OPS-10 job/CAS | USER_POOL_BUILD/RERANK | 종류별token/expectedPool·RankingVersion | T-v1-10:pool·ranking동시접수서로token덮지않음;동일input/pool두ranking중기대active버전/token맞는결과만게시;재시도동일UUID |
| R-v1-OPS-11 부분실패복구 | 장애/Redis유실 | 기능event/jobevent/activepointer/offset | T-v1-11:원본이벤트는내구job등록후배치완료전ack;부분등록재시도멱등;joboffset은게시/후속요청완료후ack;payload만쓴종료미노출;token유실옛worker부활금지 |
| R-v1-OPS-12 bundle정합 | 모델/배치전환 | manifest·전체모듈 | T-v1-12:230/245불일치와모델미준비거부;native-serving예측일치;구job의새bundle게시차단 |
| R-v1-OPS-13 삭제·백필 | API013/카탈로그변경 | 계정/제외/백필 | T-v1-13:탈퇴뒤늦은job재생성불가;삭제/부적격최종제외;기존영화ID와고정그룹기준보존 |
| R-v1-OPS-14 기존소비자보존 | 개인API/Party/OTT | 기존키·새v1키 | T-v1-14:구키불변;v1활성포인터선택일관;score의미불일치소비거부;rollback경로 |
| R-v1-OPS-15 추천session | API045/046/startRecommendationSession | session/userstate/requestid | T-v1-15:최초session생성;입력변경·소진시동일session노출보존;명시새탐색만교체;동일requestId동일session;expectedSession경합409;옛미소비예약만해제 |
| R-v1-OPS-16 소진재공급진행성 | API046/USER_POOL_BUILD | scopedpool/exclusionsnapshot/sessionrevision | T-v1-16:최상위500이모두소진된카탈로그≥600에서POOL_EXHAUSTED가501위이후공급;하한준수;추가노출/예약뒤옛CAS거부;새session에서scopedpool재사용불가;일별일반pool은영구세션제외안함 |

로컬테스트는합성fixture로경계를확인한다. 별도실환경검증에서는스냅샷/모델버전과명령을기록하고,
실제Spark후보배치·Kafka/Redis재시도·평가저장부터3편노출까지의지연을측정한다.
로컬테스트PASS를HDFS/EC2운영검증PASS로바꾸어적지않는다.
원천 관련 R-v1-OPS-06/12/13은 [ACCEPTANCE의 D01~D06](ACCEPTANCE.md)으로 확장 수집의
행 중복·제안·과거 기준일·정정값·부분 연도·혼합 원천 경계도 검증한다. 이 데이터 검수 명세 역시 아직 실행하지 않았다.

## 9. 남아 있는 구현 준비

정책을새로탐색하는목록이아니다. v1을실행가능하게만드는선행산출물이다.

- MODELS에서정한KOBIS245모델학습·선정·평가·서빙변환artifact와준비상태.
- 팀API/DB/ADR의공통계약반영,정밀별점DTO와세트generationmigration,이벤트도메인연결.
- 독립Consumer실행환경·작업조율·500재정렬·10×10공급·원자적게시구현.
- 확장 KOBIS 원행 수집과 제안 산출물의 독립 검산은 완료. 서비스/TMDB↔KOBIS 신원 확정, 동일 cutoff/누적범위 검증과 일별/targeted 배치 연결은 남아 있음.
- 인수테스트및실환경성능/실패복구기록. 외부게시·배포는사용자별도요청과팀절차에따른다.

## 10. API-031 상세 유사 영화 확장

[SIMILAR-MOVIES §3~7](SIMILAR-MOVIES.md)이 정책·저장·API·이벤트·S01~S08 검수의 기준이다.
2026-09-13 로컬 HEAD b622d5e 확인에서 공개 API-031은 이미 경로·응답·pgvector HNSW를
명시하지만 Spring 구현·HNSW 테이블·선택 인증 경로와 프런트 실조회는 미완료다. 이 확인을
원격 최신 상태로 일반화하지 않는다. 폐기된 internal.md의 IN-04를 구현 근거로 사용하지 않는다.

| 영역 | 이행할 내용 |
| --- | --- |
| API·ADR0014 | GET /api/v1/movies/{movieId}/similar, 인증 선택, size 기본10·최대20·카드 응답 유지. E5 잠정 표현을 GKT131 재사용으로 변경하는 로컬 설계 차이와 로그인 감상/별점/관심없음 제외 명시 |
| DB·배치 | 검증된 ID와 version별 GKT131 vector·후보 적격 flag·partial HNSW·활성 버전 등록·벡터/manifest 검수. 기존 embeddings에 차원/모델 이름만 바꾸어 덮어쓰기 금지 |
| Spring·Redis | 공용 Top50 캐시, miss 시 예산 내 DB 근접 검색1회, 현재 후보/사용자 제외 후 최대size 반환. 기본 추천 Consumer·Kafka 작업 큐에 의존하지 않음 |
| 프런트 | 현재 similar mock을 실제 API로 교체. 기본10편 가로 목록, 부족·빈·오류 상태, 유사 실패가 상세/리뷰를 막지 않음 |
| 이벤트·이력 | 실제 목적 영화 상세의 기존 VIEW만 사용. 유사 목록으로 추천 항목 ID·세트·세션 제외·감상·inputVersion 생성 없음 |

새 DB/캐시 계약은 이 로컬 설계의 구현 대상이며 팀 DB·공통 API에 이미 반영됐다고 표시하지 않는다.
모델245열·기존 개인화 설정은 이 추가로 바뀌지 않는다.
