# C2A Compose 통합 검증

> 상태: `PASS_LOCAL_2026-08-30` — 기존 named volume을 보존한 비파괴 local stack 검증

## 재현 명령

```powershell
cd C:\higher\projects\FEELM-standalone
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify-c2-compose.ps1 -Build
```

`-Build`는 제한시간이 있는 `docker compose up -d --build`만 실행한다. `down`, volume 삭제, 데이터베이스
초기화는 수행하지 않는다. 재빌드가 필요 없으면 `-Build`를 생략한다. 성공·실패 출력은 code,
count, version, boolean만 포함하며 service token, 사용자·영화 UUID, Rating, artifact 절대 경로를
출력하지 않는다.

실제 구현은 Docker의 무기한 `--wait`를 사용하지 않는다. `up -d --build` native process는 기본
300초 뒤 종료하고, 별도 health convergence는 기본 120초까지만 polling한다. 필요하면
`-BuildTimeoutSeconds`(30~900), `-HealthTimeoutSeconds`(10~300)로 명시적으로 조정한다.

## 검증 결과

2026-08-30 local Docker 27 / Compose 2.32에서 다음 결과를 확인했다.

| 경계 | 결과 |
| --- | --- |
| PostgreSQL, FastAPI, Spring, React health | 4/4 healthy |
| local artifact init | exit 0 |
| V100 ACTIVE Catalog | `catalog-fixture-20260829-01`, UI_READY 7편 |
| candidate store / 실제 FastAPI rank | candidate 7편, 반환 7편, 같은 Catalog version·UUID 집합 |
| ranking/star 정책 | `BAYESIAN_POPULARITY_ONLY`, alpha `0`, expected star `NOT_COMPUTED` |
| FastAPI artifact readiness | checksum·compatibility·dry-run 5 checks PASS |
| fail-closed transport | auth 없음 401, forbidden 403, request correlation 불일치 422 |
| Flyway | V1, V2, V3, V4, V5, V100, V101, V102 총 8개 적용 |
| C1 outbox → C2 projection | 처리 application 관측, snapshot/item hash 형식·count 일치 |
| 노출 persistence schema | V5 batch/item 2개 table 확인 |
| Spring container 경계 | recommender TCP 연결, candidate volume read-only, 필수 env key 존재 |

bounded `-Build` 1회 뒤 재빌드 없는 동일 검증을 3회 연속 실행해 모두 같은 PASS를 확인했다. Docker
CLI를 PATH에서 제거한 negative 실행은 exit 2와
`{"status":"FAIL","safeCode":"DOCKER_CLI_UNAVAILABLE"}`만 반환했다. native stderr, command,
환경 변수 값은 실패 JSON에 포함하지 않는다.

V100 SQL과 `recommender.local_stack_fixture`의 Catalog version, 안정 movie UUID, UI_READY 집합이
달라지면 recommender unit test가 실패한다. Compose probe도 candidate store와 실제 rank 결과의 UUID
집합을 다시 비교하므로, 생성기와 게시 데이터가 함께 잘못 바뀌는 경우에도 DB 비교에서 실패한다.

## 증거 경계

현재 승인 범위에는 공개 Spring 추천 endpoint가 없다. 따라서 Compose 검증은 존재하지 않는 HTTP
endpoint를 추가하거나 호출하지 않는다.

- 실제 container에서는 artifact init, authenticated FastAPI readiness/rank, backend→recommender TCP,
  PostgreSQL migration/outbox projection, backend/frontend health를 검증한다.
- Spring의 bearer header·timeout·strict response parsing은 `JdkRecommenderClientTest`, candidate/Catalog/
  Rating 연결은 `C2RecommendationPostgresIntegrationTest`, typed exposure transaction은
  `RecommendationExposurePostgresIntegrationTest`에서 자동 검증한다.
- runtime Spring caller와 공개 API, UI 노출, click·Rating attribution은 C2A 범위가 아니며 이 PASS가
  해당 기능의 완료나 production SLA를 의미하지 않는다.
- 운영 credential·회전과 production topology 성능은 각각 `DN-C2-004` 및 운영 재검증 전까지
  fail-closed 상태다.
