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

## 7. REC-EV-032 기본 추천의 연구용 비교

설계와 접근 범위는 [REC-EV-032](../recommendation/experiments/rec-ev-032/README.md)를 따른다.
공식 서비스 모델 채택이나 최종 전환 K 선택을 수행하는 명령이 아니다.

```powershell
cd C:\higher\projects\FEELM-standalone
py -3.12 -m unittest discover -s scripts/tests -p test_rec_ev_032_basic.py -v
py -3.12 scripts/rec_ev_032_basic.py prepare
py -3.12 scripts/rec_ev_032_basic.py train
py -3.12 scripts/rec_ev_032_basic.py score
py -3.12 scripts/rec_ev_032_basic.py evaluate
```

실데이터 명령은 독립 검토 PASS와 현재 코드·설계·설정·테스트 hash가 일치할 때만 실행된다.
각 단계의 완료 seal이 있으면 무결성을 확인해 재사용하고 부분 파일은 자동 덮어쓰지 않는다.
evaluate는 모든 정책·입력량의 추천 봉인 뒤 이미 개봉된 SELECTION 정답만 읽는다.
입력 원천과 산출물은 config의 고정 경로를 사용하며 새 데이터·모델을 commit하지 않는다.

### 7.1 같은 봉인 추천의 정답 보충

[정답 보충 설계](../recommendation/experiments/rec-ev-032/label-extension/README.md)는 원래 추천을
다시 생성하지 않고 같은 사용자·추천 영화의 원평점만 제한 조회한다. 별도의 독립 검토 PASS와
현재 보충 코드/설정/테스트 hash가 맞아야 실행된다.

```powershell
py -3.12 -m unittest discover -s scripts/tests -p test_rec_ev_032_label_extension.py -v
py -3.12 scripts/rec_ev_032_label_extension.py
```

결과는 `outputs/recommendation-evidence/rec-ev-032/label-extension`에 저장한다.
원래 모델·추천·평가 산출물을 바꾸지 않으며, 완료 봉인이 있는 경우 hash만 확인해 재사용한다.
[보충 결과](../recommendation/experiments/rec-ev-032/label-extension/RESULT.md): 실행 완료.
같은 추천의 추가 원본 조회는 종료했으며 모델·추천을 다시 실행할 필요가 없다.

### 7.2 기존 평가 영화 안에서 고정 순서 비교

[조건부 진단 계약](../recommendation/experiments/rec-ev-032/conditional-ranking/README.md)은
기존 전체 점수와 상위2편을 재현한 뒤 평가 영화의 부분순서를 채점한다.
7.1의 추가 정답 보충과는 별도 과제이며 원래 모델/산출물은 그대로 보존한다.

```powershell
py -3.12 -m unittest discover -s scripts/tests -p test_rec_ev_032_conditional.py -v
py -3.12 scripts/rec_ev_032_conditional.py
```

독립 검토 PASS와 현재5파일 hash가 맞아야 실행된다. 새 학습은 수행하지 않는다.
출력은 `outputs/recommendation-evidence/rec-ev-032/conditional-ranking`이다.
부분 실행은 자동 재시도하지 않고, 완료본은 hash 검증 후 점수 재계산 없이 재사용한다.
[조건부 비교 결과](../recommendation/experiments/rec-ev-032/conditional-ranking/RESULT.md): 실행 완료.
완료본 재사용 명령은 VERIFIED_EXISTING_COMPLETION_NO_RESCORING으로 확인했다.

### 7.3 기존 ALS 단독의 조건부 비교

[ALS 단독 설계](../recommendation/experiments/rec-ev-032/als-only/README.md)는 같은 학습 factor와
원별점 입력을 유지하고 콘텐츠 순위 결합만 제거한다. P0/M0는7.2의 완료 결과를 재사용한다.
P0는 MovieLens 보정 평균 별점 기준이며 TMDB popularity 또는 평가 수 순위가 아니다.

```powershell
py -3.12 -m unittest discover -s scripts/tests -p test_rec_ev_032_als_only.py -v
py -3.12 scripts/rec_ev_032_als_only.py
```

독립 설계·실행 검토 PASS와 현재6파일 hash가 맞아야 실행된다. 새 학습이나 원본 평점 조회는 없다.
출력은 `outputs/recommendation-evidence/rec-ev-032/als-only`다. 기존 산출물은 보존한다.
부분 실행은 자동 재시도하지 않고, 완료본은 필수 출력과 의존 봉인 검증 후 점수 재계산 없이 재사용한다.
고정 관측 영화 안의 탐색 비교이며 실제 서비스 품질·학습 수렴·최종 K를 결정하지 않는다.
[ALS 단독 결과](../recommendation/experiments/rec-ev-032/als-only/RESULT.md): 실행·독립 검산 완료.
완료본 재사용은 VERIFIED_EXISTING_COMPLETION_NO_RESCORING으로 확인했으며 새 점수 계산은 없었다.

### 7.4 사용자 분할 3개를 바꾼 ALS 비교

[사용자 분할 반복 설계](../recommendation/experiments/rec-ev-032/user-resplits/README.md)는
회차별 학습자 46,376명과 평가자 2,180명을 고정 seed 3개로 다시 구성한다.
각 회차의 ALS를 새로 학습하고 같은 학습자의 평점으로 P0를 재계산한다.
원별점 입력 0/5/10/30개, 모델 설정, 개인별 관측 영화와 평가 규칙은 유지한다.

```powershell
py -3.12 -m unittest discover -s scripts/tests -p test_rec_ev_032_user_resplits.py -v
py -3.12 scripts/rec_ev_032_user_resplits.py
```

설계·실행 독립 검토 PASS와 현재 7개 파일 hash가 맞아야 실데이터 명령을 실행할 수 있다.
출력은 `outputs/recommendation-evidence/rec-ev-032/user-resplits`다.
각 회차의 학습 평점 전체 행에서 해당 회차 평가자 유입이 없는지 확인한다.
회차 사이 역할 변경은 허용한다. 세 회차 점수를 모두 봉인한 뒤 기존 평가 정답 파일을 읽는다.
30분·프로세스 트리 12GiB 상한, 부분 실행 보존과 자동 재시도 금지를 적용한다.
완료본은 필수 출력·원천·봉인 연결을 검증하고 학습·점수 계산 없이 재사용한다.
개발 자료 안의 분할 안정성 점검이며 새 독립 자료의 확증이나 서비스 전환 K 결정은 아니다.
[분할 반복 결과](../recommendation/experiments/rec-ev-032/user-resplits/RESULT.md): 고정3회 새 학습·채점·독립 결과 검산 완료.
완료본 재사용은 VERIFIED_EXISTING_COMPLETION_NO_RETRAINING_OR_RESCORING으로 확인했다.
재사용 확인에서 추가 학습·점수 계산은 수행하지 않았다.

## 8. REC033 — 비교할 두 8맛 분류안 준비

[단계3 계약](../recommendation/experiments/rec-ev-033/README.md)은 기존 평점 독립 공통 영화85,517편에
TMDB 첫 내용 장르8그룹과 E5-small384 K-means8을 적용한다. 사용자 평점·H·평가 정답·raw archive는 읽지 않는다.

```powershell
py -3.12 -m unittest discover -s scripts/tests -p test_rec_ev_033_tastes.py -v
py -3.12 scripts/rec_ev_033_tastes.py
```

독립 설계·실행 검토 PASS와 README/config/runner/tests의 현재4개 hash가 맞아야 실제 실행할 수 있다.
출력은 `outputs/recommendation-evidence/rec-ev-033`이다. 한국어 TMDB 기존 캐시를 원천 body hash와 대조한다.
주 seed와 초기화 민감도용 seed의 KMeans 호출2회(n_init=10씩)로 종료한다. 새 다운로드·재인코딩·다른 k 탐색은 없다.
30분·프로세스 트리12GiB 상한을 적용하고 부분 실행은 보존한다. 완료본은 원천·캐시·필수출력·연결봉인 확인 후
`VERIFIED_EXISTING_COMPLETION_NO_FIT_OR_ASSIGNMENT`로 재사용한다. 추천 품질 승자·공식 맛·전환 K를 결정하는 명령이 아니다.

## 9. 단계4 역할·상태 합성 검증

[역할 제안](../recommendation/plans/service-policy-redesign/stage-4-policy-proposal.md)과
`stage-4-state-review.json`에 독립 검토한 파일 hash와 범위를 기록했다.

```powershell
py -3.12 -m unittest discover -s scripts/tests -p test_feelm_policy_states.py -v
```

합성 영화와 주어진 점수로13개 상태·역할 검사를 수행한다. 원별점0.5단위, 온보딩/감상량 분리,
삭제 후 경험·제외 유지, T/D 자격, 미배정·후보부족, 동일타입 교체를 확인한다.
실제 영화/사용자 자료를 읽거나 ALS를 호출하지 않는다. 주어진 순위를 쓰는 강제 진단 모드이며 K1 전환 정책이 아니다.
PostgreSQL20편 세트 버퍼·Redis500/100·이벤트의 실제 통합 테스트도 아니다.

## 10. REC034 — 두 분류안의 n30 후보 공급 진단

[REC034 계약](../recommendation/experiments/rec-ev-034/README.md)은 같은 기존 ALS factor와2,180명의 관측 입력30개를 유지한다.
독립 설계·실행 검토와8개 코드/문서 fingerprint가 일치해야 실행할 수 있다.

```powershell
py -3.12 -m unittest discover -s scripts/tests -p test_rec_ev_034_supply.py -v
py -3.12 scripts/rec_ev_034_supply.py
```

출력은 `outputs/recommendation-evidence/rec-ev-034`다. 새 ALS/K-means 학습·평가 E/H·raw archive·네트워크 접근은 없다.
제외 전 raw500→타입적격→100→관측입력 제외→T2/D1을 계산한다. 30분/12GiB 감시와 실패/부분 실행 보존을 적용한다.
완료본은 `VERIFIED_EXISTING_COMPLETION_NO_SCORING`으로 재사용한다. 관측 입력 내 미경험 proxy의 공급 진단이며
실제 전체 감상 이력·발견 성공률·분류 품질 우승·전환 K30을 검증하는 명령이 아니다.

### 10.1 평균 자격 조건에 대한 대안 하나

[gating-repair 계약](../recommendation/experiments/rec-ev-034/gating-repair/README.md)은 같은 저장된 raw500에서
원안을 먼저 재현·봉인한 뒤 양수 관측이 있는 맛/영화 근거를 쓰는 대안 하나를 비교한다.

```powershell
py -3.12 -m unittest discover -s scripts/tests -p test_rec_ev_034_gating_repair.py -v
py -3.12 scripts/rec_ev_034_gating_repair.py
```

독립 설계·실행 PASS와7파일 hash·원천17개가 맞아야 실행된다. 새 ALS 점수·학습·평가 E/H 접근은 없다.
출력은 `outputs/recommendation-evidence/rec-ev-034/gating-repair`, 완료 재사용은
`VERIFIED_EXISTING_COMPLETION_NO_RECALCULATION`이다.30분/12GiB 감시와 부분 실패 보존을 유지한다.
원안·대안 ×A/B의 전원 회복/회귀를 남긴 뒤 이 공급 비교를 종료하며 임계치·후보수·새 규칙을 추가 탐색하지 않는다.

원안과 후속 대안 모두 실행·독립 결과 검산·완료 재사용 검증을 마쳤다. 같은 명령은 봉인 재사용 경로로만 끝나야 한다.
실행 전 README의 DRAFT 표기는 fingerprint 보존을 위한 원문이며 현재 상태는 RESULT와 review/result-review.json을 따른다.

## 11. 입력 타입·슬롯 보존 합성 준비

```powershell
py -3.12 -m unittest discover -s scripts/tests -p test_feelm_input_packets.py -v
py -3.12 -m unittest discover -s scripts/tests -p test_feelm_slot_preservation.py -v
```

각각9개/6개 합성검사다. 실제 데이터·모델·DB·Redis·네트워크를 사용하지 않는다.
입력의 반별점/이진타입·직접packet검증·중복/삭제/현재값 처리를 확인하며 binary/mixed ranking 구현은 아니다.
슬롯 보존은 같은500/100에서 제외전T3/D1만 포함하는 연구제안과 최신제외후 보장불가 반례를 확인한다.
실제 추가 공급실험이나 공식정책 변경을 수행하지 않는다. 자세한 범위와 핀은
`docs/recommendation/plans/service-policy-redesign`의 `input-adapter-review.json`과 `slot-preservation-review.json`에 있다.
실제 품질 평가 준비는 같은 디렉터리의 `quality-judgment-protocol.md`/`quality-protocol-review.json`을 따른다.
모집·노출·응답 수집은 이 명령에 포함하지 않는다.

## 12. REC035 원별점·상대값·이진 응답 환산 비교

[사전 고정 연구 계약](../recommendation/experiments/rec-ev-035/README.md)과 독립 실행 검토의7파일 fingerprint가 일치해야 실행한다.
프로젝트 루트 `C:\higher\projects\FEELM-standalone`에서 다음 명령을 사용한다.

```powershell
py -3.12 -X utf8 -m unittest discover -s tests -p test_rec_ev_035_inputs.py -v
py -3.12 -X utf8 scripts/rec_ev_035_inputs.py
py -3.12 -X utf8 scripts/rec_ev_035_report.py
```

첫 명령은9개 합성검사다. 실행 명령은 기존 REC032 자료에서 상대값 ALS를1회 학습하고 A 원별점/B 상대값/C 이진환산을 비교한다.
C는 A와 같은 기존 raw factor를 사용하며 이진 ALS를 새로 학습하지 않는다. 세 경로의 전체 점수 봉인 뒤 기존 E/H로 채점한다.
출력은 `outputs/recommendation-evidence/rec-ev-035`이며30분/프로세스 트리12GiB 한도를 감시한다.
부분 실패는 보존하고 자동 재시도/덮어쓰기를 금지한다. 완료본 재실행은 원천·의존·필수출력을 검사한 뒤
`VERIFIED_EXISTING_COMPLETION_NO_FIT_OR_SCORING`으로 종료한다. 마지막 명령은 집계만으로 PNG/SVG를 만든다.
관측 영화 안의 조건부 진단이며 실제 온보딩·전체 새 추천 품질·서비스 K·8맛/슬롯 정책 채택을 검증하지 않는다.

REC035는 실제 실행·독립 전수 검산·보고서/차트 검토와 완료 재사용을 마쳤다. 위 실행 명령은 현재 봉인을 검사해 재사용한다.

## 13. REC036 현재 K-means 후보의 내용 설명 준비도

[REC036 진단 계약](../recommendation/experiments/rec-ev-036/README.md)의 독립 설계·실행 검토 뒤 실행한다.
기존 임베딩과 중심으로 배정·거리·표본·입력 문자열만 점검한다. 새 fit·인코딩·평점 접근은 없다.

```powershell
py -3.12 -X utf8 -m unittest discover -s scripts/tests -p test_rec_ev_036_readiness.py
py -3.12 -X utf8 scripts/rec_ev_036_readiness.py
```

review.json의7파일 fingerprint가 일치해야 실행하며 출력은 `outputs/recommendation-evidence/rec-ev-036`이다.
완료 재실행은 봉인 검사만 한다. 30분/프로세스 트리12GiB, 실패·부분 실행은 보존한다.
정량 산출 뒤 가린 카드의 AI 내용 기술을 봉인하고, 군집 연결 검수와 독립 결과 검산을 수행한다.


REC036 진단·가린 카드 기술120건·독립 전수 검산과 연결 내용 검수를 완료했다. 현재 명령은 봉인 재사용만 한다.
계산/출처는PASS이나8맛 설명 준비도는INCONCLUSIVE이며, [결과와 실제 영화 사례](../recommendation/experiments/rec-ev-036/RESULT.md)를 따른다.
README의 DRAFT는 연구 계약 원문의 상태이며 실행 완료·제품 준비도는 RESULT와 result-review.json에서 구분한다.

## 14. REC037 장르·세부 키워드 분리와 줄거리 근거

[REC037 계약](../recommendation/experiments/rec-ev-037/README.md)의 독립 설계·실행 검토 후 실행한다.
같은85,517편의 고정 배정으로 장르/키워드 c-TF-IDF를 각각 재계산하고, 기존120편 줄거리의 AI 구절 검수를 연결한다.

```powershell
py -3.12 -X utf8 -m unittest discover -s scripts/tests -p test_rec_ev_037_descriptions.py
py -3.12 -X utf8 scripts/rec_ev_037_descriptions.py
py -3.12 -X utf8 scripts/rec_ev_037_descriptions.py --validate-excerpts
```

review.json의5파일 fingerprint가 맞아야 실행한다. 출력은 `outputs/recommendation-evidence/rec-ev-037`이며
새 fit·인코딩·평점·네트워크는0회,30분/프로세스 트리12GiB 한도다. 부분 실패는 보존한다.
마지막 명령은 가린 원문 추출을 `synopsis-excerpts-draft.json`에 모은 뒤 실행한다. 각카드의
card/excerpts/theme/note 형식과1~2개·각180codepoint 이하의 정확한 substring을 검증·봉인하며 AI 해석의 참값 검증은 아니다.
모든 카드의 구절 봉인 전에는 새 키워드/군집과 연결한 의미 검토를 시작하지 않는다. 완료 재실행은 해당 봉인만 검사한다.

REC037의 실제 집계·128개 구절 봉인·독립 수치/연결/보고서 검수를 완료했다. 위 두 실행 명령은 현재
각각 VERIFIED_EXISTING_COMPLETION_NO_RECALCULATION, VERIFIED_EXISTING_EXCERPTS로 봉인 재사용만 한다.
01/02/04의 설명은 일부 보강됐지만 현재8맛 준비도는INCONCLUSIVE다. [결과](../recommendation/experiments/rec-ev-037/RESULT.md)와
같은 폴더의 result-review.json을 완료 상태의 기준으로 삼는다. 기존 수치 completion-seal의 검수 대기는 당시 단계 기록으로 보존한다.

## 15. REC038 장르·태그와 8개 군집 방법 비교

[REC038 계약](../recommendation/experiments/rec-ev-038/README.md)의 독립 설계·실행 검토 뒤 실행한다.
기존 두 기준안과 장르/태그/결합×K-means/NMF의6개 새 안을 같은85,517편에서 비교한다.
새 안은 각2개 초기화로12회fit하며 새 임베딩·평점 접근·설치는 없다. 모든 실행은 연구 저장소 루트에서 한다.

```powershell
py -3.12 -X utf8 -m unittest discover -s scripts/tests -p test_rec_ev_038_clusters.py -v
py -3.12 -X utf8 scripts/rec_ev_038_clusters.py
py -3.12 -X utf8 scripts/rec_ev_038_clusters.py --seal-descriptions
py -3.12 -X utf8 scripts/rec_ev_038_clusters.py --seal-judgments
```

단순 python은 다른 sklearn 환경일 수 있으므로 py -3.12/sklearn1.9.0을 고정한다. review.json의10파일
fingerprint가 맞아야 실행한다. 출력은 `outputs/recommendation-evidence/rec-ev-038`,4threads·30분/12GiB 제한이다.
수치 완료 뒤 description-packets의 가린 원문4편씩을 읽어 descriptions-draft.json에 기록하고 봉인한다.
그전에는 평가용 설명 패킷을 만들지 않는다. 이후 별도 검수자가 evaluation-packets의128편×8안 판정을
judgments-draft.json에 모아 마지막 명령으로 봉인한 뒤 실제 배정과 연결한다. 원문·배정키는ignored outputs에 둔다.
설명과 판단의 구체 JSON schema는 runner validator를 따른다. 완료 재실행은 각단계의 봉인만 검사한다.
태그누락·임시표시·native8점유실패·수렴상한을 그대로 공개하며 이 비교가 추천성능/최종K를 판정하지 않는다.

평가 봉인 뒤 `py -3.12 -X utf8 scripts/rec_ev_038_evaluation.py`로 실제 배정에 연결한다.
공통128편의 반복1,024건이며 전체/공통원문충분/공통태그지원 분모와 영화별 짝차이를 보고한다.
추가fit·설명수정·재채점은 없고 완료 재실행은 evaluation-seal을 검사한다.

REC038의 실제 12fit·64설명 봉인·128편×8안 판정 봉인·연결을 완료했다. 각 명령의 완료 재사용 상태는
VERIFIED_NUMERIC_COMPLETION_NO_FIT, VERIFIED_DESCRIPTIONS, VERIFIED_JUDGMENTS,
VERIFIED_EVALUATION_COMPLETION이다. 수치 단계의 SEMANTICS_PENDING 문구는 당시 상태로 보존하며
전체 완료와 최종 검토 상태는 [결과](../recommendation/experiments/rec-ev-038/RESULT.md) 및 result-review.json을 따른다.

```powershell
py -3.12 -X utf8 scripts/rec_ev_038_report.py
```

위 보고서 명령은 봉인된 연결 결과를 검증한 뒤 GROUPS.md의64군집·256설명영화 연결과 ignored outputs의
JUDGMENTS.md128편×8안 색인을 재생성한다. 모델·설명·판정은 바꾸지 않는다.
판정 파일은3검수자가43/43/42편을 맡아 확정했고, 한 번에 한 명만 지정 파일에 쓰도록 소유권을 순서대로 인계했다.
단순 장르 규칙과 장르 K-means는 연구의 잠정 후보이며 최종8맛·추천 품질·K는 보류한다.
새 장르 K-means에 REC034의 기존 전체 텍스트 공급 결과를 전용하지 않는다.

## 16. REC039 장르 후보의 추론 전용 계약

[REC039 계약](../recommendation/experiments/rec-ev-039/README.md)과 exact fingerprint PASS 뒤 실행한다.
원천9개 pin·REC038 봉인·당시 보고서 지문을 확인하고, 상위 문서5개를 수정 전에 master-before에 보존한다.
연구 저장소 루트에서 다음을 실행한다.

```powershell
py -3.12 -X utf8 -m unittest discover -s scripts/tests -p test_feelm_genre_candidates.py -v
py -3.12 -X utf8 scripts/rec_ev_039_candidate_contract.py
py -3.12 -X utf8 scripts/feelm_genre_candidates.py --package outputs/recommendation-evidence/rec-ev-039/candidate-package.json --genres '[35,18]'
```

출력은 outputs/recommendation-evidence/rec-ev-039다. 실제 adapter171,034배정 일치·304구성행·10입력×2예시를 생성했다.
새 fit·평점·128재채점·slot계산은 없다. 완료 재실행은 VERIFIED_CANDIDATE_CONTRACT_NO_RECALCULATION으로
패키지·원천·snapshot·필수출력만 확인한다. source/current master가 아니라 저장된 당시 master snapshot을 검사하므로
이 실행문서를 최신화해도 과거 REC038의 검토 기록을 바꿀 필요가 없다.
CLI는 연구 패키지용이며 공식 맛명·색상·공개 API가 아니다. 미지원 code=null/native=-1과 format_only를 소비자가 보존해야 한다.
단계4는 각 원천 pin과 이 completion-seal을 기록하고 새 후보의 공급을 독립 실행해야 한다.

## 17. REC040 전체 장르 개선 규칙 적용

[REC040 고정 계약](../recommendation/experiments/rec-ev-040/README.md)의 독립 설계·코드 fingerprint PASS 뒤 실행한다.
기존8개 장르 묶음을 유지한 binary cosine 규칙 ONE이다. 순서·중복을 제거하고 정확 동점과 미지원 상태를 반환한다.
연구 저장소 루트의 기존 py -3.12 환경을 사용한다.

```powershell
py -3.12 -X utf8 -m unittest discover -s scripts/tests -p test_feelm_genre_set_rule.py -v
py -3.12 -X utf8 scripts/rec_ev_040_set_rule.py
py -3.12 -X utf8 scripts/feelm_genre_set_rule.py --package outputs/recommendation-evidence/rec-ev-040/rule-package.json --genres '[18,80,53]'
```

실제85,517배정,3방법의456구성행·192대응표행·삭제변형72,996가중기록·합성42결과를 생성했다.
원천6개 pin·과거 봉인·master snapshot5개·출력16개를 확인하며 완료 재실행은
VERIFIED_SET_RULE_COMPLETION_NO_RECALCULATION으로 계산 없이 검증한다. 부분 실행은 덮어쓰지 않는다.
출력은 outputs/recommendation-evidence/rec-ev-040, 계산30분/12GiB 상한이다. 공식 API나 맛 매핑에 반영하지 않는다.
그룹 크기·동점·삭제 민감도·ARI는 품질 순위가 아니다. 이전128편 재채점·fit·평점·slot은 이번 범위에 없다.
[결과](../recommendation/experiments/rec-ev-040/RESULT.md)와 같은 폴더의 result-review.json에서 최종 독립 검토 상태를 확인한다.

## 18. REC041 분류·발견 정책과 채점 가능한 기회

[REC041 고정 계약](../recommendation/experiments/rec-ev-041/README.md)의 두 독립 검토자 PASS 이후
분류2안×발견 정책2안을 실행했다. 입력은 기존2,180명·n30·raw ALS500이며 새 학습·fold-in은 없다.
연구 저장소 루트에서 기존 py -3.12 환경을 사용한다.

```powershell
py -3.12 -B -X utf8 -m unittest discover -s scripts/tests -p test_feelm_discovery_policies.py -v
py -3.12 -B -X utf8 scripts/rec_ev_041_discovery.py
```

최초 실행은 exact14지문·18입력 pin과 이전 봉인을 확인하고 당시 master5개를 원 해시로 복사한다.
자격·순위·선택 슬롯5파일을 score-seal에 봉인한 뒤에만 기존 E/H를 디코딩한다. E와O30 교집합0·Q·반별점 격자를 확인한다.
총17출력은 outputs/recommendation-evidence/rec-ev-041에 보존했다. 부분 실행은 덮어쓰지 않고 failure.json을 남긴다.
완료 재사용은 VERIFIED_DISCOVERY_DIAGNOSTIC_NO_RECALCULATION으로 원천·지문·snapshot·출력을 검증한다.
현재 master를 갱신해도 과거 result-review를 수정하지 않는다. 계산 상한30분/12GiB, 실제121.375초/835.8MiB였다.

원별점 ALS는 유지하고 맛은 T/D 자격에 사용했다. TV-only는 두 분류 모두 역할에서 제외하며 개별 긍정 관측을 보존한다.
최종100의 발견 제공률은 개선 규칙97.2%→77.1%, 장르KM94.3%→58.9%였고 전 셀3편 공급은100%였다.
선택 슬롯의 기존 E 정답은 모두0개여서 실제 품질·불호 감소는 미판정이다. 원본 전체의 무평점을 판정한 것이 아니다.
입력량·영화 선정은 설계만 했고 실행하지 않았다. n30을 최종K나 온보딩 권장량으로 사용하지 않는다.
[결과와 해석 범위](../recommendation/experiments/rec-ev-041/RESULT.md) 및 같은 폴더의 result-review.json에서 최종 검토를 확인한다.
