# C0 Catalog 87,585편 Performance Gate

> 측정 시각(UTC): 2026-08-29T13:12:43.0242559+00:00
> Git commit: e2768cd35ab51f942a16bd1ed8b8da713ca6a40e
> Working tree dirty: True

- Dataset: 87585편, active catalog catalog-performance-87585-v1
- Backend: Spring Boot postgres profile, 별도 포트 18081
- PostgreSQL: 17.6-alpine ephemeral container, 별도 포트 55432, volume 없음
- Warm 반복: endpoint당 200회, 사전 warmup 10회
- 오류: 0

| Scenario | p50 | p95 | p99 | max | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| search-query-20 | 72.243ms | 97.411ms | 103.906ms | 118.519ms | PASS (≤ 300ms) |
| search-blank-query-20 | 8.047ms | 10.606ms | 11.343ms | 21.399ms | 관찰값 |
| movie-detail | 1.442ms | 1.896ms | 2.52ms | 3.154ms | PASS (≤ 200ms) |

- Initial cache build: 1906.997ms, HTTP 200, items 20
- 최종 판정: **PASS**

초기 요청은 PostgreSQL에서 active version 전체 projection을 읽어 immutable in-memory snapshot을 만드는 시간을 포함한다.
Warm 요청은 매번 active version UUID를 확인한 뒤 현재 in-memory 선형 검색·정렬 경로를 사용한다.
