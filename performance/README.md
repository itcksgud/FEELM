# C0 Catalog 87,585편 성능 Gate

Spark ALS worker scale-out 측정은
[`results/spark-als-scaling/latest.md`](./results/spark-als-scaling/latest.md)와
[`run-spark-scaling-gate.ps1`](./run-spark-scaling-gate.ps1)을 사용한다. Catalog HTTP Gate와 Spark
batch Gate는 서로 다른 workload이므로 수치를 합쳐 운영 capacity로 표현하지 않는다.

`NFR-01`, `AC-CAT-041`, `AC-CAT-042`를 현재 PostgreSQL read adapter와 API 경로에서 재현한다.
기존 `docker-compose.yml`의 PostgreSQL, backend, frontend와 volume은 사용하거나 변경하지 않는다.

## 측정 대상

- `generate_series`로 만든 안정적인 movie identity 87,585개
- active catalog projection, ko-KR localization, 장르 연결, search document 각 87,585개
- 첫 검색 요청의 전체 Catalog cache build 시간
- query 검색 20개 반환: warm 200회 p50/p95/p99, Gate p95 ≤ 300ms
- 공백 query를 query 없음으로 정규화한 인기순 검색 20개 반환: warm 200회 p50/p95/p99 관찰
- 상세 조회: warm 200회 p50/p95/p99, Gate p95 ≤ 200ms
- 모든 측정 요청 오류 0건

측정은 동시성 1의 로컬 초기 Gate다. 운영 capacity 또는 부하 한계를 의미하지 않는다.

## 격리

스크립트는 다음 리소스만 임시 생성한다.

- volume 없는 `postgres:17.6-alpine` 컨테이너, 기본 host port `55432`
- 별도 Spring Boot process, 기본 port `18081`
- `performance/.tmp/`의 backend log

성공과 실패 모두 `finally`에서 backend process와 임시 컨테이너를 종료·제거한다. 사용자 Compose
container와 named volume은 건드리지 않는다. 포트가 이미 사용 중이면 임의 프로세스를 종료하지 않고
실패하므로 다른 포트를 인자로 지정한다.

## 실행

요구 사항은 Docker Desktop, Java 17, PowerShell 7이다.

```powershell
cd C:\higher\projects\FEELM-standalone
.\performance\run-catalog-gate.ps1
```

포트를 바꿀 때:

```powershell
.\performance\run-catalog-gate.ps1 -DatabasePort 55433 -BackendPort 18082
```

빠른 harness 점검도 최소 100회 조건을 유지한다.

```powershell
.\performance\run-catalog-gate.ps1 -Requests 100 -WarmupRequests 5
```

작은 결과만 `performance/results/latest.json`, `performance/results/latest.md`에 남는다. 로그와 임시
파일은 Git에서 제외된다.

## 판정

현재 adapter는 첫 요청에서 active version의 모든 projection을 immutable snapshot으로 만들고,
warm 요청마다 active version UUID를 확인한 뒤 Java 메모리에서 선형 필터·정렬한다. 검색 p95 Gate가
실패하면 결과를 숨기지 않는다. `movie_search_document.search_vector`, popularity 정렬 index와
페이지 경계를 PostgreSQL에서 직접 사용하는 query port로 전환한 후 같은 harness로 다시 측정한다.
