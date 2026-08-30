# ADR-0001 — 사용자 요청은 로컬 Catalog read model을 사용한다

> 상태: `ACCEPTED`  
> 결정일: 2026-08-29

## Context

영화 검색·상세에서 TMDB를 요청마다 호출하면 외부 장애, rate limit, 응답 지연, stale ID와 locale
fallback이 사용자 API에 직접 전파된다. 동일 요청의 결과 version도 고정하기 어렵다.

## Decision

Python batch가 외부 데이터를 versioned artifact로 만들고 Spring import가 검증 후 PostgreSQL active
Catalog로 publish한다. 사용자 요청은 PostgreSQL만 읽는다.

## Consequences

- TMDB 장애 중에도 마지막 정상 Catalog를 제공할 수 있다.
- 검색·추천·화면이 같은 catalogVersion을 공유한다.
- 최신성과 즉시성 대신 batch freshness와 storage 비용이 생긴다.
- 수집·import·publish 관측성과 운영 절차가 필요하다.

## Rejected alternatives

- Frontend direct TMDB: token·계약·정합성·fallback 통제가 불가능하다.
- Spring runtime proxy: 장애 격리와 안정적 pagination/version을 충족하지 못한다.
- 초기부터 Elasticsearch: 87,585편과 현재 검색 요구에는 운영 복잡성이 앞선다.

