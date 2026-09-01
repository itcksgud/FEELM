# FEELM standalone

FEELM은 실제 영화 Catalog, 한국 OTT 제공 정보와 검증 가능한 추천 실험을 결합하는 개인 프로젝트다.
C0·C1과 승인된 C2B·C3·C4·C5 local-MVP 수직 기능, 제품에서 분리된 C6 추천 해석
로컬 실험실이 working tree에 구현돼 있다. 검색→감상·평가, popularity baseline 발견,
local Party·OTT 비교, Mailpit 회원가입·온보딩, factual report·공유·알림과 예상 별점·개인
기준 기대 효용·취향 관측 근거 실험을 localhost fixture와 PostgreSQL에서 재현할 수 있다.

현재 판정은 `LOCAL_MVP_IMPLEMENTED_AWAITING_REVISION_REPRODUCTION`이다. C2B와 C3→C6 실제 통합
Compose browser E2E는 통과했지만, 아직 사용자 승인 revision과 clean-checkout 재현이 없으므로
revision 기준 프로젝트 완료나 production readiness를 뜻하지 않는다.
범위와 남은 Gate는 [local-MVP 완성 단계 고찰](./docs/planning/local-mvp-completion-reflection-20260830.md)과
[프로젝트 완료 Gate](./docs/planning/project-completion-gates.yaml)를 따른다.

## 구성

- `frontend/`: React·TypeScript·Vite UI
- `backend/`: Spring Boot Catalog·WatchIntent·Rating·Film API와 PostgreSQL read/write model
- `data-pipeline/`: MovieLens/TMDB normalized Catalog artifact 생성
- `recommender/`: evidence-bounded Bias/Popularity/ALS Fold-in 내부 코어
- `docs/`: 제품·API·DB·추천·테스트 계약

공개 `movieId`는 수집 version과 무관한 identity다. 제목, 검색, 유사도와 OTT 정보는
catalogVersion별 projection으로 저장해 원자 publish와 rollback 중에도 URL을 유지한다.

## 빠른 시작

필수 도구와 정확한 실행 순서는
[로컬 개발 Runbook](./docs/runbook/local-development.md)을 단일 기준으로 사용한다.

```powershell
Copy-Item .env.example .env.local
docker compose up --build
```

- UI: `http://localhost:5173`
- Catalog API: `http://localhost:8080`
- Health: `http://localhost:8080/actuator/health`

실제 TMDB token은 `.env.local`에만 넣는다. local fixture 실행에는 token이 필요하지 않다.

## 검증

새 checkout에 필요한 npm/Python 의존성을 lock에서 설치하고, 전체 검증·fresh Compose·브라우저 E2E·
C2A 통합 probe까지 실행하는 기준 명령은 다음과 같다.

```powershell
npm run verify:reproduce
```

이 명령은 `.codex-tmp/reproduction` 아래 venv를 새로 만들고 `feelm-standalone`의 PostgreSQL·추천
artifact local volume을 초기화한다. 이미 준비된 개발 환경에서 빠른 비컨테이너 회귀만 반복할 때는
`npm run verify`를 사용한다.

개별 명령은 다음과 같다.

```powershell
npm ci
npm run contracts:check
npm run c2b:contracts:check
npm run c2b:decisions:check
npm run c3:contracts:check
npm run c3:decisions:check
npm run c4:contracts:check
npm run c4:decisions:check
npm run c5:contracts:check
npm run c6:contracts:check
npm run approvals:check
npm run completion:gates:check
npm run completion:gates:mutation:check
npm run supply-chain:check
npm run recommendation:evidence:check
npm run security:secrets:check
npm run security:history:check
npm run security:java:check
npm run openapi:lint
npm run openapi:mock:check
./backend/gradlew.bat -p backend --dependency-verification strict test
./backend/gradlew.bat -p backend --dependency-verification strict writeRuntimeCycloneDx
# OSV Scanner v2.5.1 설치·checksum 검증 후:
osv-scanner scan source -L backend/build/reports/runtime.cdx.json
npm ci --prefix frontend
npm run test --prefix frontend
npm run build --prefix frontend
py -3.12 -m pip install --require-hashes -r scripts\requirements-build-tools.lock
py -3.12 -m pip install --no-build-isolation --require-hashes -r requirements-data.lock
py -3.12 -m pip install --require-hashes -r requirements-ml.lock
py -3.12 -m unittest discover -s data-pipeline\tests -p 'test_*.py'
py -3.12 -m pip install --require-hashes -r recommender\requirements-test.lock
py -3.12 -m pip install --no-deps --no-build-isolation -e recommender
$env:PYTHONPATH='recommender\src;recommender\tests'
py -3 -m unittest discover -s recommender\tests -p 'test_*.py'
Remove-Item Env:PYTHONPATH
```

Compose가 실행 중이면 브라우저 E2E와 C2A container/DB 경계도 검증한다.

```powershell
npm ci --prefix e2e
npm run install:browsers --prefix e2e
npm run verify:e2e:fresh
```

C2B와 C3→C6 isolated local-MVP·로컬 실험 브라우저 흐름의 실제 재현 명령은 다음과 같다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-c2b-e2e.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File e2e\local-mvp\run-local-mvp-e2e.ps1
```

두 흐름 모두 2026-08-30 working tree에서 safe PASS를 확인했다. 실행 ID, 검증한 상태 전이와 기본 개발
volume 보존 결과는 `docs/testing/local-mvp-compose-e2e-20260830.md`에 기록돼 있다.

`verify:e2e:fresh`는 반복 실행 가능한 fixture를 위해 `feelm-standalone` Compose의 PostgreSQL·추천
artifact 볼륨만 삭제하고 다시 만든 뒤 Playwright와 C2A Compose probe를 실행한다. 해당 local volume을
보존해야 하면 실행하지 말고 `npm run verify:e2e`와 `scripts/verify-c2-compose.ps1`을 각각 실행한다.

Python 실행 의존성은 `requirements-data.lock`, `requirements-ml.lock`, `recommender/requirements.lock`,
`recommender/requirements-test.lock`의 버전과 artifact SHA-256을 사용한다. 의도적으로 갱신할 때만
`scripts/refresh-python-locks.ps1`을 실행하고 lock diff와 `npm run supply-chain:check`를 검토한다.
컨테이너 base image, PostgreSQL image, GitHub Actions와 Gradle 배포 ZIP도 digest 또는 checksum으로
고정되어 있다.

추천 수치는 모델 홍보 문구가 아니라 versioned evidence로 관리한다. full-catalog K10 Fold-in
alpha 0.2는 Popularity 대비 NDCG@10과 candidate recall@500이 개선된 offline 후보지만 public
champion은 아니다. 제품 C2B 카드의 예상 별점은 paired C1 규모 검증 전까지 `NOT_COMPUTED`를
유지한다. 다만 C6 로컬 실험실에서는 `displayEligible=false`로 예상 별점·개인 ECDF 기대 효용·
표본 수가 드러난 취향 관측 근거를 비교한다. 이는 자기보고 만족도가 아니다. Explore05와 constrained
2+1 v1은 relevance 손실 Gate를 실패해 비활성이다.

집계값이 실제 추천 목록을 어떻게 바꾸는지는
[MovieLens 사용자 A 추천 변화 사례](./docs/recommendation/evidence/REC-EV-016-user-case-a.md)에서 확인할 수 있다.
동일한 비식별 사용자의 실제 영화 Top-10을 알고리즘별로 비교하지만, 이 한 사례로 모델을 채택하지는 않는다.

후속 [영화·장르 관계와 자유 태그 ablation](./docs/recommendation/evidence/REC-EV-017-relational-tag-ablation.md)은
개인 평균을 보정한 영화 공동 선호와 태그 의미까지 사용한다. 전체 Test NDCG는 상승했지만 인기도
P2 구간 회귀와 long-tail 무효과 때문에 fallback은 계속 Popularity다.

C5 account lifecycle, 제품 expected-star·satisfaction·taste diagnosis/compare, C3 Party public champion과
production email/OAuth/storage/notification provider는 local-MVP 제품 범위에서 명시적으로 제외한다.

Spark standalone worker 1→2 local 측정은 Train 5,119,729행에서 ALS fit 중앙값 `21.456s→13.468s`
(`1.593x`)였지만 같은 물리 host의 공학 증거일 뿐 다중 서버·HDFS·운영 capacity 주장이 아니다.

## 작업 규칙

새 구현은 [AGENTS.md](./AGENTS.md), [문서 지도](./docs/README.md), OpenAPI와 migration을 따른다.
전체 기능별 완료/차단 상태는 [프로젝트 완료 Gate](./docs/planning/project-completion-gates.yaml)에서 추적한다.
비밀값, MovieLens 원본, 생성된 대용량 artifact와 실험 output은 Git에 넣지 않는다.
