# FEELM Catalog 내비게이션 계약

> 상태: `APPROVED` — C0 Catalog
> 승인 확장: `docs/c1-draft/ui/navigation-map.md` — C1 Rating·Film  
> Canonical registry: `docs/spec/approved-slices.json`

```mermaid
flowchart TD
    APP[앱 진입] --> SEARCH_HOME[SCR-CAT-001 검색 홈]
    SEARCH_HOME -->|query submit·debounce| SEARCH_RESULTS[SCR-CAT-002 검색 결과]
    SEARCH_HOME -->|인기 영화 선택| MOVIE_DETAIL[SCR-CAT-004 영화 상세]
    SEARCH_RESULTS -->|filter| FILTER[SCR-CAT-003 검색 필터]
    FILTER -->|적용| SEARCH_RESULTS
    FILTER -->|취소| SEARCH_RESULTS
    SEARCH_RESULTS -->|영화 선택| MOVIE_DETAIL
    MOVIE_DETAIL -->|유사 영화 선택| MOVIE_DETAIL
    MOVIE_DETAIL -->|OTT option| EXTERNAL[외부 TMDB/provider]
    MOVIE_DETAIL -->|뒤로| PREVIOUS{진입 경로}
    PREVIOUS --> SEARCH_RESULTS
    PREVIOUS --> SEARCH_HOME
    MOVIE_DETAIL -->|404 검색 복귀| SEARCH_HOME
```

## Route

| Route | Screen | Query/state |
| --- | --- | --- |
| `/search` | `SCR-CAT-001` | recent query는 device local |
| `/search/results` | `SCR-CAT-002` | `q`, filter, sort; cursor는 화면 내부 상태 |
| `/movies/:movieId` | `SCR-CAT-004` | internal UUID |

## 규칙

- 검색 결과에서 상세로 이동했다가 뒤로 가면 query, filter, scroll position과 로드한 page를 복원한다.
- 직접 URL로 상세에 진입하면 뒤로 행동은 검색 홈으로 이동한다.
- filter sheet는 browser history에 필수 route를 추가하지 않는다.
- 외부 link 후 앱으로 돌아오면 상세 상태를 유지한다.
- 인증이 없어도 모든 C0 route에 접근할 수 있다.
- 잘못된 movieId와 공개 불가 영화는 같은 404 화면을 사용한다.
