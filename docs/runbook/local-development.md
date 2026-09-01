# FEELM 로컬 개발 Runbook

> 상태: `APPROVED` — 로컬 실행과 검증 명령의 단일 기준

## 1. 현재 가능한 검증

검증한 기준 버전:

- Git
- Docker 27.x와 Docker Compose 2.32 이상
- Java 17 LTS
- Node.js 22.x와 npm 11.x
- Python 3.12.x
- PostgreSQL 17.6-alpine은 Docker로 실행한다.

추천 evidence 작업 전 Python 의존성:

```powershell
py -3.12 -m pip install --require-hashes -r scripts\requirements-build-tools.lock
py -3.12 -m pip install --no-build-isolation --require-hashes -r requirements-data.lock
py -3.12 -m pip install --require-hashes -r requirements-ml.lock
```

`requirements-ml.lock`은 REC-EV-019B의 고정 ONNX E5 embedding 실행에만 필요하다. 모델 파일은
계약에 기록된 Hugging Face revision에서 내려받고 SHA-256을 확인하며 저장소에는 commit하지 않는다.

```powershell
cd C:\higher\projects\FEELM-standalone
npm ci
npm run contracts:check
npm run c1:contracts:check
npm run c2:contracts:check
npm run openapi:lint
```

OpenAPI mock:

```powershell
npm run openapi:mock
```

기본 주소는 `http://127.0.0.1:4010`이다. 예시:

```powershell
Invoke-RestMethod 'http://127.0.0.1:4010/api/v1/movies?query=나우%20유'
```

Mock은 OpenAPI 요청 파라미터를 검증하고 계약의 example 또는 schema 기반 응답을 반환한다.
외부 API와 실제 token을 사용하지 않는다.

## 2. 데이터 감사 재현

실제 token은 `.env.local`에만 둔다.

```powershell
py -3 scripts/movielens_profile.py `
  --archive C:\higher\projects\MM\data\raw\ml-32m.zip `
  --output outputs\tmdb-audit\movielens_profile.json

py -3 scripts/tmdb_coverage_audit.py `
  --archive C:\higher\projects\MM\data\raw\ml-32m.zip `
  --output outputs\tmdb-audit `
  --workers 8
```

`outputs/`, `.env.local`, MovieLens 원본은 commit하지 않는다.

### 2.0 Catalog artifact pipeline

작은 fake 기반 검증:

```powershell
py -3.12 -m unittest discover -s data-pipeline\tests -p 'test_*.py'
```

실제 MovieLens/TMDB artifact 명령과 schema는 `data-pipeline/README.md`를 따른다. 실제 token은
`.env.local`에서만 읽고 생성물·cache·identity map은 `outputs/` 아래에 둔다.

생성한 JSONL을 Compose PostgreSQL에 원자 게시하려면 host 절대 경로만 전달한다. token과 artifact
내용은 image에 복사하지 않는다.

```powershell
$env:CATALOG_ARTIFACT_HOST_PATH = (Resolve-Path outputs\catalog\catalog.jsonl).Path
docker compose -f docker-compose.yml -f docker-compose.catalog-import.yml up -d --build --wait
```

backend는 기본 로컬 `postgres,local,compose` profile을 유지한 채 artifact를 검증·staging하고 quality
Gate를 통과한 version만 ACTIVE로 바꾼다. 같은 파일 재시작은 멱등이며, 실패하면 기존 ACTIVE version을 유지한다. 이 override는
현재 로컬 catalog를 실제 artifact로 교체하는 명시적 작업이므로 일반 fixture 개발에는 사용하지 않는다.
같은 override의 `recommender-artifact-init`은 입력 Catalog bytes에서 mapping과 popularity candidate를
다시 만들어 Spring·FastAPI와 동일한 `catalogVersion`을 사용한다. 따라서 실제 Catalog 게시 뒤 기존
fixture 추천이 버전 불일치 503으로 남지 않는다.

### 2.1 추천 판단 자료 REC-EV-001

```powershell
py -3 scripts/movielens_time_split_profile.py `
  --archive C:\higher\projects\MM\data\raw\ml-32m.zip `
  --output-dir outputs\recommendation-evidence\global-time-v1 `
  --manifest docs\recommendation\evidence\manifests\global-time-v1.json `
  --evidence docs\recommendation\evidence\REC-EV-001-rating-style.md

py -3 -m unittest discover -s scripts\tests -p "test_*.py"
py -3 scripts\verify_movielens_evidence.py `
  --manifest docs\recommendation\evidence\manifests\global-time-v1.json
```

분할 Parquet과 사용자 profile은 `outputs/`에 두고, checksum·경계·집계만 문서와 manifest로
commit 대상에 둔다.

### 2.2 추천 판단 자료 REC-EV-002

```powershell
py -3 scripts/recommendation_baseline_calibration.py `
  --split-dir outputs\recommendation-evidence\global-time-v1 `
  --split-manifest docs\recommendation\evidence\manifests\global-time-v1.json `
  --output-dir outputs\recommendation-evidence\rec-ev-002 `
  --manifest docs\recommendation\evidence\manifests\rec-ev-002.json `
  --evidence docs\recommendation\evidence\REC-EV-002-prediction-calibration.md

py -3 scripts/verify_recommendation_baseline.py `
  --manifest docs\recommendation\evidence\manifests\rec-ev-002.json
```

이 실행은 Test를 입력으로 받지 않는다. Windows 로컬 Spark는 `winutils.exe` 없이도 ALS 학습은
가능하지만 Hadoop 형식 모델 저장은 실패할 수 있다. 실행기는 학습된 user/item factor를 Spark
driver에서 회수해 NPZ로 저장하고, 검증기는 factor 내적과 저장된 예측이 일치하는지 다시 확인한다.
임의 출처의 `winutils.exe`를 설치하지 않는다.

### 2.3 추천 판단 자료 REC-EV-003/003B

```powershell
py -3 scripts/recommendation_cold_start_curve.py `
  --split-dir outputs\recommendation-evidence\global-time-v1 `
  --split-manifest docs\recommendation\evidence\manifests\global-time-v1.json `
  --baseline-manifest docs\recommendation\evidence\manifests\rec-ev-002.json `
  --baseline-predictions outputs\recommendation-evidence\rec-ev-002\validation_predictions.parquet `
  --baseline-candidates outputs\recommendation-evidence\rec-ev-002\sampled_ranking_scored.parquet `
  --output-dir outputs\recommendation-evidence\rec-ev-003 `
  --cohort-manifest docs\recommendation\evidence\manifests\cold-start-cohort-v1.json `
  --manifest docs\recommendation\evidence\manifests\rec-ev-003.json `
  --evidence docs\recommendation\evidence\REC-EV-003-cold-start.md

py -3 scripts\verify_recommendation_cold_start.py `
  --manifest docs\recommendation\evidence\manifests\rec-ev-003.json

py -3 scripts\recommendation_cold_start_blend.py `
  --cold-start-manifest docs\recommendation\evidence\manifests\rec-ev-003.json `
  --output-dir outputs\recommendation-evidence\rec-ev-003b `
  --manifest docs\recommendation\evidence\manifests\rec-ev-003b.json `
  --evidence docs\recommendation\evidence\REC-EV-003B-cold-start-blend.md

py -3 scripts\verify_recommendation_cold_start_blend.py `
  --manifest docs\recommendation\evidence\manifests\rec-ev-003b.json
```

REC-EV-003은 cohort 사용자를 ALS·Bias 학습에서 완전히 제외하므로 REC-EV-002보다 오래 걸린다.
REC-EV-003B는 저장된 결과로 별점과 순위의 α를 별도로 선택하므로 Spark를 다시 학습하지 않는다.

### 2.4 Catalog movieId ↔ MovieLens mapping artifact

추천 모델의 MovieLens ID를 서비스 UUID로 직접 해석하지 않는다. 게시 후보 Catalog JSONL에서 검증된
MOVIELENS external ID만 versioned mapping artifact로 내보낸다.

```powershell
$env:PYTHONPATH='recommender\src'
py -3 -m feelm_recommender export-catalog-mapping `
  --catalog outputs\catalog\catalog.jsonl `
  --mapping outputs\catalog\recommender-mapping.json `
  --metadata outputs\catalog\recommender-mapping.metadata.json `
  --quarantine outputs\catalog\recommender-mapping.quarantine.json `
  --compatibility-id catalog-recommender-family-v1
```

동일 Catalog bytes와 compatibility ID는 byte-identical 결과를 만든다. quarantine과 accepted count는
입력 Catalog 범위의 coverage일 뿐 전체 MovieLens 또는 운영 catalog coverage로 주장하지 않는다.

### 2.5 C2A 내부 Popularity-only serving

네 artifact와 sidecar를 가진 결정적 local fixture set을 만든 뒤 checksum/family/head binding과
Popularity dry-run을 검증한다.

```powershell
$env:PYTHONPATH='recommender\src'
py -3.12 -m feelm_recommender export-serving-fixture `
  --output-dir outputs\c2-serving-fixture
py -3.12 -m feelm_recommender validate-serving-set `
  --manifest outputs\c2-serving-fixture\artifact-set.json
$env:C2_AUTH_MODE='fake'
$env:C2_ARTIFACT_SET_MANIFEST=(Resolve-Path outputs\c2-serving-fixture\artifact-set.json).Path
uvicorn feelm_recommender.api:app --app-dir recommender\src --host 127.0.0.1 --port 8000
```

Catalog smoke mapping을 연결할 때는 `--mapping`과 `--mapping-metadata`를 함께 준다. 이 경우에도
Bias/factor/calibration은 fixture이므로 입력 Catalog mapping 범위 확인일 뿐 production coverage가 아니다.
local fake credential은 공개 fixture `test-c2-service-token`(허용)과
`test-c2-forbidden-token`(인증됐지만 권한 없음)이다. `C2_AUTH_MODE=fake`를 명시하지 않으면 둘 다
fail-closed된다. 운영 credential 발급·회전은 `DN-C2-004` 전 만들지 않는다.

```powershell
$headers=@{Authorization='Bearer test-c2-service-token'}
Invoke-RestMethod http://127.0.0.1:8000/internal/health/live -Headers $headers
Invoke-RestMethod http://127.0.0.1:8000/internal/health/ready -Headers $headers
```

readiness는 네 payload와 dry-run이 모두 통과한 뒤에만 200이다. liveness 200만으로 artifact ready를
주장하지 않는다.

### 2.6 C2A batch candidate artifact

Catalog JSONL, 같은 Catalog checksum에 묶인 mapping, ready serving manifest에서
`GLOBAL_VERIFIED_CATALOG_V1` 후보와 count-only quarantine을 생성하고 local store에 원자 게시한다.

```powershell
$env:PYTHONPATH='recommender\src'
py -3.12 -m feelm_recommender export-batch-candidates `
  --catalog outputs\catalog-smoke\catalog.jsonl `
  --mapping outputs\catalog-smoke\recommender-mapping.json `
  --mapping-metadata outputs\catalog-smoke\recommender-mapping.metadata.json `
  --serving-manifest outputs\c2-serving-smoke\artifact-set.json `
  --candidate outputs\c2-candidate-smoke\candidate-set.json `
  --quarantine outputs\c2-candidate-smoke\quarantine.json `
  --store-dir outputs\c2-candidate-smoke\store

py -3.12 -m feelm_recommender inspect-candidate-store `
  --store-dir outputs\c2-candidate-smoke\store
```

같은 입력의 재실행은 candidate payload, quarantine, active pointer가 byte-identical하다. local store는
immutable version과 active 1개, 직전 rollback 1개를 보존하지만 TTL·retention 기간을 정하거나 이전
version을 삭제하지 않는다. accepted 0건, mapping/Catalog/model checksum 불일치는 active pointer 교체 전
실패한다. smoke 2편은 입력 범위 검증일 뿐 production coverage가 아니다.

## 3. 제품 코드 검증·실행

새 checkout 기준 전체 bootstrap·검증·fresh E2E·C2A Compose probe:

```powershell
npm run verify:reproduce
```

이 명령은 root/frontend/e2e npm lock과 Python hash lock에서 격리 환경을 만들고,
`feelm-standalone` 전용 PostgreSQL·추천 artifact volume을 초기화한다. 볼륨을 보존해야 하거나 이미
dependency가 준비된 환경에서 빠른 회귀만 실행할 때는 `npm run verify`를 사용한다.

추천 evidence의 tracked-safe protocol·manifest·결정 패킷과 로컬 Spark scaling 결과만 빠르게
검증하려면 다음을 사용한다. 원본 MovieLens나 `outputs/` 없이도 통과해야 한다.

```powershell
npm run recommendation:evidence:check
npm run recommendation:vnext:readiness:check
npm run security:secrets:check
```

`recommendation:vnext:readiness:check`는 REC-EV-019~026 오프라인 구현 계약, task graph,
`40/10/10/40` user split, K10·미래 10개·positive 3개·candidate-positive Gate, 019A/019B artifact
schema·실행 명령, 현재 popularity-only 보호 경계와 REC-EV-019P artifact checksum을 함께 검증한다.
출력 `decision=GO`는 019A/019B 구현 착수 승인이고 제품 champion이나 019C 모델 실행 승인이 아니다.

### 3.0 Spark ALS 1→2 worker local scale-out

REC-EV-001의 Train/Validation Parquet이 `outputs/recommendation-evidence/global-time-v1`에 있을 때
별도 Spark standalone master와 worker JVM으로 동일 ALS workload를 비교한다. 기존 Compose와 DB
volume은 사용하지 않는다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File performance\run-spark-scaling-gate.ps1 `
  -Repetitions 3 -WarmupRuns 1 -SampleBuckets 20 `
  -CoresPerWorker 2 -WorkerMemoryGiB 4
```

2026-08-30 로컬 측정은 5,119,729 Train행에서 1→2 worker ALS fit 중앙값 `21.456s→13.468s`
(`1.593x`)였다. 같은 물리 Windows host의 JVM scale-out 증거이며 다중 서버, HDFS 또는 운영
capacity 증거가 아니다. 상세 조건과 14.22% prediction coverage 제한은
`performance/results/spark-als-scaling/latest.md`를 따른다.

```powershell
docker compose up -d postgres
.\backend\gradlew.bat -p backend test
npm ci --prefix frontend
npm run test --prefix frontend
npm run build --prefix frontend
docker compose up -d --build --wait
npm run verify:e2e
```

C1 실제 브라우저 mutation은 local migration fixture를 소비한다. 새 Compose 데이터베이스에서 C0+C1
전체 10개를 한 번 실행하며, 반복 확인은 `npm run test:c0 --prefix e2e` 또는 새 CI Compose project를
사용한다. 테스트가 기존 볼륨을 삭제하거나 초기화하지는 않는다.

local C1 API는 required bearer를 검증한다. 아래 값은 `AUTH_MODE=fake`에서만 인식하는 공개 fixture
식별자이며 실제 계정 credential이 아니다.

```text
Authorization: Bearer test-c1-owner-token
```

### 3.1 C1 Rating outbox worker

기본 Compose는 `OUTBOX_WORKER_ENABLED=true`로 C1 Rating event를 C2 active-Rating input projection에
연결한다. worker는 한 poll에서 최대 25건만 처리하며, 각 event를 별도 transaction에서
`FOR UPDATE SKIP LOCKED`로 claim한다. 같은 JVM의 poll 재진입은 건너뛰고 여러 instance가 경쟁하면
잠긴 event 대신 다음 event를 선택한다. process 중 instance가 종료되면 transaction rollback으로
claim도 해제된다.

consumer 실패는 savepoint까지 rollback되어 이미 commit된 C1 mutation에는 영향을 주지 않는다.
재시도는 30초부터 지수 backoff하되 최대 1시간이고 총 8회로 제한한다. 8회 실패한 event는
`DEAD_LETTER`가 되어 자동 재시도하지 않는다. worker는 event payload·actor·평가 값을 로그로
출력하지 않는다. 현재 연결된 route는 `RATING_CREATED`, `RATING_UPDATED`, `RATING_DELETED`이며,
다른 event는 별도 consumer가 등록되기 전까지 이 worker가 claim하지 않는다.

수동 backend 실행에서 worker를 켜려면 다음처럼 명시한다.

```powershell
$env:OUTBOX_WORKER_ENABLED='true'
cd backend
.\gradlew.bat bootRun --args='--spring.profiles.active=postgres,local'
```

Linux/macOS에서는 `./backend/gradlew -p backend test`를 사용한다. README에 다른 임시 명령을 추가하지
말고 이 Runbook을 갱신한다.

### 3.2 Spring → C2A 내부 추천 adapter

Spring은 local candidate store의 `active.json`과 immutable payload를 checksum/version 검증한 뒤,
현재 active Catalog에서 `catalogVisible && uiReady`인 service UUID만 FastAPI에 보낸다. C1 Rating은
REPEATABLE READ projection snapshot의 `inputVersion`과 `(movieId,value,revision)`만 전송한다.
사용자 ID·email·사용자 bearer·raw behavior는 요청에 포함하지 않으며 응답도 fragment의 exact field와
service UUID, version/checksum을 다시 검증한다. 호출 전후 Catalog version/UI_READY가 달라지면 stale
성공 결과를 사용하지 않는다.

Compose backend의 candidate store 경로는 `/c2-artifacts/candidates/store`다. local fake service auth는
다음 네 Gate를 모두 명시한 경우만 열린다.

```text
C2_CLIENT_LOCAL_FAKE_ENABLED=true
C2_CLIENT_AUTH_MODE=fake
C2_SERVICE_TOKEN=test-c2-service-token
C2_CANDIDATE_STORE_PATH=/c2-artifacts/candidates/store
```

mode 미설정, Gate false, 다른 token은 Spring adapter에서 fail-closed된다. 운영 credential 방식은
`DN-C2-004` 전 구현하지 않는다. `C2_RECOMMENDER_TIMEOUT_MS=750`은 REC-EV-007 local-loopback
실측으로 선택한 임시 공학 값일 뿐이며,
REC-EV-007 benchmark 전 production SLA로 주장하지 않는다. timeout·connection·401·403·503·invalid
response는 typed 내부 실패가 되며 이전 성공 body로 fallback하지 않는다.

### 3.3 추천 실제 노출 snapshot

`RecommendationExposureService`는 `InternalRecommendationService` 결과 중 caller가 실제 표시하기로
선택한 항목만 PostgreSQL V5 schema에 `REQUIRES_NEW` transaction으로 저장한다. public controller나
OpenAPI endpoint는 아직 없다. caller는 FastAPI `requestId`와 별개인 `exposureBatchId` UUID와 실제
`exposedAt`, 1부터 연속된 표시 position을 넘긴다.

같은 batch ID·동일 canonical payload 재시도는 기존 `recommendationItemId`를 반환한다. 같은 batch
ID의 다른 payload는 거부하고, 다른 batch ID는 같은 영화·같은 recommendationVersion이어도 새 노출로
보존한다. 저장 실패는 batch/item 전체가 rollback된다. 노출 뒤 click·Rating이 없더라도 negative나
outcome row를 만들지 않는다.

```powershell
.\backend\gradlew.bat -p backend test `
  --tests com.feelm.catalog.c2.recommendation.RecommendationExposurePostgresIntegrationTest
```

### 3.4 C2A 실제 Compose 통합 검증

기존 개발용 PostgreSQL·artifact named volume을 삭제하지 않고 image를 재빌드한 뒤, V100 Catalog와
후보 집합, FastAPI 실제 rank, V3~V5/V100~V102 migration, C1 outbox→C2 Rating snapshot,
backend/frontend health를 한 번에 검증한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-c2-compose.ps1 -Build
```

재빌드가 필요 없으면 `-Build`를 생략한다. 스크립트는 `docker compose down`, volume 삭제, DB 초기화를
호출하지 않는다. 출력은 safe code/count/version만 포함하고 token, 사용자·영화 UUID, Rating,
artifact 절대 경로는 출력하지 않는다. V100 SQL과 local fixture Catalog version/movie UUID/UI_READY가
어긋나면 unit test와 Compose DB 교차 검증이 실패한다.

`-Build`는 Docker native process를 기본 300초로 제한하고, 이후 health를 최대 120초 polling한다.
무기한 `docker compose up --wait`는 사용하지 않는다. 느린 개발 PC에서는 다음처럼 상한 안에서만
명시적으로 늘린다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-c2-compose.ps1 `
  -Build -BuildTimeoutSeconds 600 -HealthTimeoutSeconds 180
```

현재 공개 Spring 추천 endpoint는 승인되지 않았으므로 Compose에서 존재하지 않는 endpoint를 만들거나
호출하지 않는다. 실제 FastAPI auth/rank와 Spring container network/config/mount/DB 경계를 검증하고,
Spring JDK client의 bearer·timeout·strict parser 및 exposure transaction은 backend Testcontainers 증거와
구분한다. 상세 결과는 `docs/testing/c2a-compose-integration.md`를 따른다.

### 3.5 C2B 로컬 baseline 실제 브라우저 E2E

승인된 `DN-C2B-002` 범위는 실제 backend·recommender·PostgreSQL·React를 함께 올려 검증한다. 이
baseline은 popularity 순위만 사용하며 예상 별점과 추천 이유를 표시하지 않는다. 새 local fixture에서
최초 3편, 최대 3편 누적 append, 명시적 `관심 없음` 이탈, 시청 확인만으로는 카드 유지, 정수 별점
완료 시 같은 transaction에서 카드 이탈을 한 브라우저 흐름으로 확인한다.

기존 개발 stack과 named volume을 보존하기 위해 반드시 고유한 Compose project 이름을 사용한다.
스크립트는 기본 stack을 `down`하지 않고 별도 host port를 사용하며, 완료 후 전용 컨테이너와 network만
내린다. `--volumes`를 호출하지 않으므로 전용 검증 volume도 보존된다. 같은 project 이름을 재사용하면
fresh-state 오염을 막기 위해 fail-closed되므로 다시 실행할 때 새 이름을 지정한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-c2b-e2e.ps1 `
  -ProjectName feelm-c2b-e2e-local-001
```

기본 격리 port는 PostgreSQL `55432`, backend `58080`, recommender `58000`, frontend `55173`이다.
이미 사용 중이면 `-PostgresHostPort`, `-BackendHostPort`, `-RecommenderHostPort`,
`-FrontendHostPort`로 다른 값을 지정한다. 아래 JSON은 실행 성공 시 출력하는 safe schema다. 실제
2026-08-30 성공 실행은 `docs/testing/local-mvp-compose-e2e-20260830.md`에 기록했으며, raw token·사용자·
영화 ID 없이 다음 count와 불변식만 남겼다.

```json
{"status":"PASS","safeCode":"C2B_REAL_COMPOSE_BROWSER_E2E_PASS","initialItemCount":3,"appendedCollectionCount":5,"finalActiveItemCount":3,"viewingOnlyPreserved":true,"ratingCompletionRemoved":true,"developerVolumesModified":false}
```

`recommender-artifact-init`는 local candidate artifact를 생성한 뒤 directory `0755`, file `0644`로
마감한다. backend는 volume을 계속 read-only로 mount하며, 이 권한 보정은 non-root Spring process의
fixture 읽기만 허용한다. 운영 artifact 배포·retention·권한 모델을 승인하거나 증명하는 절차는 아니다.

C2B 테스트는 C1의 due WatchIntent를 실제로 소비하므로 기존 stateful C0/C1 E2E와 같은 DB에서 순서를
섞지 않는다. `e2e/playwright.c2b.config.ts`와 고유 Compose project가 이 상태 경계를 강제한다.
K10 alpha 0.2 champion, 예상 별점, 추천 이유, 운영 bearer/secret, production SLA는 이 E2E의 범위가
아니다.

### 3.6 C2B→C6 isolated local-MVP browser E2E

C2B baseline과 C3 local Party·OTT → C4 Mailpit 가입·인증·온보딩 → C5 factual report·공유·알림 →
C6 추천 해석 로컬 실험을
실제 PostgreSQL·Spring·React·Mailpit 구성으로 확인한다. 두 흐름은 fixture 상태 충돌을 피하려고 별도
고유 Compose project에서 실행하며 기본 개발 project와 volume을 건드리지 않는다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-c2b-e2e.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File e2e\local-mvp\run-local-mvp-e2e.ps1
```

상세 port, secret 비기록과 isolated volume 규칙은 `e2e/local-mvp/README.md`를 따른다. 2026-08-30
working-tree 실행 결과는 `docs/testing/local-mvp-compose-e2e-20260830.md`에 기록돼 있다.

이 흐름에서도 C5 account lifecycle·제품 expected-star·self-reported satisfaction·제품 taste diagnosis,
Party public champion, production provider/OAuth/credential은 호출하거나 활성화하지 않는다.

### 3.7 C6 추천 해석 로컬 실험실

Compose의 loopback-only 설정에서 `http://localhost:5173/__experiments/recommendation-interpretation`을
열면 최근 평가 수에 맞는 검증 K 버킷, REC-EV-003B 예상 별점, 개인 평가 분포 ECDF 기대
효용과 장르·감독·배우 차원 표본 수/평균/lift를 확인할 수 있다. `C6_LOCAL_ENABLED=true`,
`C6_LOCAL_EXPERIMENT_ENABLED=true`, `VITE_LOCAL_FEATURES_ENABLED=true`가 모두 필요하며 일반
내비게이션에는 노출되지 않는다.

예상 별점은 제품 승인 값이 아니며 모든 예측은 `displayEligible=false`다. `expectedRelativeUtility`는
예상 별점이 사용자 자신의 기존 평가 분포에서 어디에 위치하는지를 추정한 값이지, 감정이나
자기보고 만족도를 측정한 값이 아니다. 실험 계약과 제한은
`docs/c6-recommendation-interpretation/local-contract.md`를 따른다.

relative-utility v2 선택 근거를 재생성하려면 REC-EV-003/003B 산출물이 있는 환경에서
다음을 실행한다. MovieLens Test는 읽지 않고 모델 선택 이후의 Validation tail만 사용한다.

```powershell
$env:PYTHONPATH='scripts'
py -3.12 scripts/recommendation_relative_utility_evaluation.py `
  --source-manifest docs/recommendation/evidence/manifests/rec-ev-003b.json `
  --cold-predictions outputs/recommendation-evidence/rec-ev-003/cold_start_validation_predictions.parquet `
  --onboarding outputs/recommendation-evidence/rec-ev-003/onboarding_first_20.parquet `
  --result docs/recommendation/evidence/results/rec-ev-015-evaluation.json `
  --manifest docs/recommendation/evidence/manifests/rec-ev-015.json `
  --evidence docs/recommendation/evidence/REC-EV-015-relative-utility.md
Remove-Item Env:PYTHONPATH
```

같은 비식별 MovieLens 사용자 A에서 알고리즘별 실제 영화 Top-10, 취향 벡터, held-out 순위와
들어온/빠진 제목을 다시 생성하려면 REC-EV-001~004B/011 대용량 artifact가 있는 환경에서 실행한다.

```powershell
$env:PYTHONPATH='scripts'
py -3.12 scripts/recommendation_user_case_study.py
py -3.12 scripts/verify_recommendation_user_case_study.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-016.json
Remove-Item Env:PYTHONPATH
```

MovieLens 영화·장르 공동 선호, Train 시점 자유 태그 TF-IDF, Validation→Test alpha ablation을
재생성하려면 다음을 실행한다. TMDB 120편 preview와 843편 감사 표본은 coverage gate 확인에만
사용하며 추천 성능 입력에는 넣지 않는다.

```powershell
$env:PYTHONPATH='scripts'
py -3.12 scripts/recommendation_relational_ablation.py
py -3.12 scripts/verify_recommendation_relational_ablation.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-017.json
Remove-Item Env:PYTHONPATH
```

binary onboarding K10 cohort가 최소 5,000명인지 재검증하려면 `global-time-v1` Train/Test Parquet이 있는
환경에서 다음을 실행한다. 결과는 추천 성능이 아니라 REC-EV-019 실행 feasibility다.

```powershell
$env:PYTHONPATH='scripts'
py -3 scripts/recommendation_binary_onboarding_preflight.py
py -3 scripts/verify_recommendation_binary_onboarding_preflight.py
Remove-Item Env:PYTHONPATH
```

## 4. local profile

- PostgreSQL 17.6-alpine
- Spring Boot 3.5.16 / Java 17
- React 19 / Vite 7 / Node 22
- 외부 TMDB 호출 없는 Catalog fixture import
- fake token decoder와 fake user subscription adapter
- C1 owner/other fake bearer와 WatchIntent·Rating·Film fixture
- 고정 clock `2026-08-29T12:00:00Z`
- frontend는 OpenAPI mock 또는 local Spring API 선택 가능

`db/local` fixture는 `V100+` 예약 범위를 사용한다. 이미 C0 fixture가 적용된 개발 볼륨에 이후 정식
`V2` migration을 추가할 수 있도록 `local` profile만 Flyway out-of-order를 허용한다. 운영
`postgres` 단독 profile은 기본값인 `false`를 유지한다.

## 5. 환경 변수

| 변수 | 필수 profile | 비밀 | 목적 |
| --- | --- | --- | --- |
| `TMDB_READ_ACCESS_TOKEN` | data job | 예 | TMDB 수집·감사. v4 Read Access Token 우선, 로컬 job은 v3 API key도 허용 |
| `POSTGRES_DB` | Compose | 로컬 기본값 가능 | 로컬 DB 이름 |
| `POSTGRES_USER` | Compose/backend | 로컬 기본값 가능 | 로컬 DB 사용자 |
| `POSTGRES_PASSWORD` | Compose/backend | 예 | 로컬 DB 비밀번호 |
| `DATABASE_URL` | backend | 로컬 기본값 가능 | PostgreSQL 연결 |
| `DATABASE_USERNAME` | backend | 로컬 기본값 가능 | JDBC 사용자 |
| `DATABASE_PASSWORD` | backend | 예 | JDBC 비밀번호 |
| `CATALOG_ARTIFACT_PATH` | import | 아니오 | normalized artifact 위치 |
| `CATALOG_ARTIFACT_HOST_PATH` | Compose import override | 아니오 | container에 read-only mount할 JSONL의 host 절대 경로 |
| `AUTH_MODE` | backend | 아니오 | `fake` 또는 실제 JWT adapter |
| `FIXED_CLOCK_INSTANT` | test/local | 아니오 | freshness 결정성 |
| `CURSOR_SIGNING_KEY` | backend | 예 | opaque cursor 변조 방지 키 |
| `OUTBOX_WORKER_ENABLED` | backend local/runtime | 아니오 | bounded C1 Rating outbox worker 활성화; 기본 Compose는 `true` |
| `VITE_API_BASE_URL` | frontend build | 아니오 | 비우면 same-origin `/api` 사용 |
| `VITE_LOCAL_FEATURES_ENABLED` | frontend build | 아니오 | 기본 `false`; localhost Compose build만 `true`로 C3/C5/C6 local route와 C4 Mailpit 안내를 포함 |
| `VITE_C1_FAKE_BEARER_TOKEN` | frontend local build | 아니오 | `AUTH_MODE=fake`에서만 쓰는 C1 fixture token |
| `C2_AUTH_MODE` | recommender local/test | 아니오 | `fake`일 때만 공개 C2 fixture credential을 인식; 기본은 fail-closed |
| `C2_ARTIFACT_SET_MANIFEST` | recommender local/container | 아니오 | 네 payload/sidecar를 가리키는 artifact-set manifest 경로 |
| `C2_CANDIDATE_STORE_PATH` | backend local/container | 아니오 | checksum 검증할 active candidate store root; Compose는 `/c2-artifacts/candidates/store` |
| `C2_RECOMMENDER_BASE_URL` | backend local/container | 아니오 | C2A 내부 base URL; request path/body는 로그 금지 |
| `C2_CLIENT_AUTH_MODE` | backend local/test | 아니오 | `fake` 외에는 현재 fail-closed; 운영 auth는 DN-C2-004 |
| `C2_CLIENT_LOCAL_FAKE_ENABLED` | backend local/test | 아니오 | 공개 fixture token 허용을 명시하는 추가 Gate; 기본 `false` |
| `C2_SERVICE_TOKEN` | backend local/test | 예 | Spring service bearer; local fixture 외 실제 값은 문서/로그 금지 |
| `C2_RECOMMENDER_TIMEOUT_MS` | backend local/test | 아니오 | REC-EV-007 local 후보 750ms; production SLA 아님 |
| `C6_LOCAL_ENABLED` | backend local | 아니오 | C6 external route를 local profile에서만 활성화; 기본 `false` |
| `C6_LOCAL_EXPERIMENT_ENABLED` | recommender local/test | 아니오 | C6 internal interpretation route 활성화; 기본 `false`, C2 `/rank`의 star head는 계속 비활성 |

새 변수를 추가하면 `.env.example`, 이 표, adapter test를 같은 변경에서 갱신한다.

## 6. 문제 해결

| 증상 | 확인 |
| --- | --- |
| OpenAPI lint 실패 | path parameter required, nullable 3.1 문법, example/schema 일치 |
| traceability 실패 | operation·screen·BR·DEC·AC ID가 실제 문서에 존재하는지 확인 |
| mock 401만 반환 | operation security에 anonymous `{}`가 있는지 확인 |
| TMDB 401 | token 값을 출력하지 말고 `.env.local` 존재와 발급 상태만 확인 |
| 오래된 cursor | catalogVersion·filter 변경 후 첫 page부터 다시 요청 |
| OTT 없음 혼동 | latest success와 serveUntil로 NONE_LISTED/UNKNOWN 계산 확인 |
| Windows에서 `5173`이 잠시 연결 거부 | `docker compose ps`로 frontend health를 확인하고 `docker compose restart frontend` 후 다시 요청 |
