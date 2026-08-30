# ADR-0003 — C0 검색은 PostgreSQL로 구현한다

> 상태: `ACCEPTED`  
> 결정일: 2026-08-29

## Context

C0는 약 8.8만 영화의 제목·감독·배우 부분 검색과 구조화 filter가 필요하다. 초성·오타 교정은
승인 범위가 아니며 서비스 정형 데이터는 이미 PostgreSQL을 사용한다.

## Decision

정규화한 `MOVIE_SEARCH_DOCUMENT`와 PostgreSQL index를 사용한다. `pg_trgm`, GIN과 text search의
구체 조합은 benchmark로 선택하되 API 의미는 바꾸지 않는다.

## Consequences

- 추가 검색 cluster 없이 transaction과 catalogVersion을 공유한다.
- 한국어 초성·고급 형태소 검색은 보장하지 않는다.
- 87,585편 p95 300ms를 넘으면 query/index를 먼저 최적화하고 그 뒤 외부 검색엔진을 재검토한다.

