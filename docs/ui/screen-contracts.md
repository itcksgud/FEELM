# FEELM Catalog 화면 계약

> 상태: `APPROVED` — C0 Catalog  
> 승인 확장: `docs/c1-draft/ui/screen-contracts.md` — C1 Rating·Film  
> Canonical registry: `docs/spec/approved-slices.json`  
> 시각 원본: `C:\Users\kingc\Downloads\FEELM UI Mockups Final FOR REAL.html`  
> SHA-256: `c438d2da2b53c45c1bbc577799c40e416249c753cf6eaf1c1b281be90622afbf`  
> 원본 크기: 20,048,486 bytes, `data-screen-label` 39개

시각 원본은 레이아웃 참고다. 화면 데이터 의미와 상태는 이 문서와 OpenAPI가 우선한다.

## SCR-CAT-001 — 검색 홈

- 목업: `1a ⑩ 검색 홈`
- 접근: 비회원·회원
- 초기 API: `searchMovies(sort=POPULARITY, limit=6)`
- 최근 검색어: C0는 사용자 device local storage, 서버 동기화 없음

### 표시

- 제목·감독·배우 검색 입력
- 최근 검색어 최대 10개, 개별 삭제와 전체 삭제
- ‘지금 많이 찾는 영화’ 대신 ‘인기 영화’ 최대 6편
- 인기 영화가 없으면 해당 section 숨김

### 행동

| 행동 | 결과 |
| --- | --- |
| 검색어 입력 | 250ms debounce 후 `SCR-CAT-002` 결과 요청 |
| submit | trim query로 결과 화면 이동, 빈 query submit은 무시 |
| 최근 검색어 선택 | 해당 query 결과 화면 이동 |
| 인기 영화 선택 | `SCR-CAT-004`로 이동 |
| 전체 삭제 | local recent queries 삭제, 확인 modal 없음 |

### 상태

- 인기 영화 loading: 카드 6개 skeleton
- 인기 영화 오류: section 안 retry, 검색 입력은 계속 사용 가능
- query가 없을 때 결과 없음 문구를 표시하지 않는다.

## SCR-CAT-002 — 영화 검색 결과

- 목업: `1a ⑩-1 영화 검색 결과`
- 접근: 비회원·회원
- API: `searchMovies`

### 카드 필드

- poster 또는 local placeholder
- `displayTitle`
- 개봉 연도와 대표 장르 1개
- 외부 평점을 표시할 경우 `source value/scale` 전체 label
- KR `FLATRATE` provider badge 최대 3개, 나머지는 `+N`
- `availabilityStatus=UNKNOWN`이면 provider badge 대신 ‘OTT 정보 확인 중’
- `NONE_LISTED`이면 ‘등록된 한국 OTT 없음’

### 검색·필터

- query는 화면에 유지한다.
- filter 버튼은 `SCR-CAT-003`을 연다.
- 적용 filter 수를 badge로 표시한다.
- 기본 정렬은 query가 있으면 관련도, 없으면 인기순이다.
- 스크롤 하단에서 `nextCursor`가 있을 때 다음 페이지를 요청한다.

### 상태

| 상태 | 화면 |
| --- | --- |
| loading 최초 | 결과 skeleton 6개 |
| loading 다음 page | 기존 결과 유지 + 하단 spinner |
| empty | `검색 결과가 없어요`와 filter 초기화 행동 |
| validation error | 잘못된 filter를 제거하고 다시 시도하도록 안내 |
| recoverable error | query·filter와 기존 결과를 보존하고 retry |

느린 이전 query 응답은 최신 query 결과를 덮어쓰지 않는다.

## SCR-CAT-003 — 검색 필터

- 목업: 없음 — C0에 신규로 필요한 계약 화면
- 형태: 모바일 bottom sheet 권장, 구현체가 같은 행동을 보존하면 별도 화면 가능
- facet API: `listGenres`, `listCountries`, `listOttProviders`

### 필터

- 장르 다중 선택: 같은 category 안 OR
- 제작 국가 다중 선택: 같은 category 안 OR
- 개봉 연도 from/to
- OTT provider 다중 선택: KR 기준
- OTT 유형: 기본 `FLATRATE`; 사용자가 펼쳐서 RENT/BUY/FREE/ADS 선택 가능
- category 간 AND

### 행동

- 적용: filter를 결과 화면에 반영하고 첫 page 재조회
- 초기화: 모든 filter 제거, query는 유지
- 닫기: 변경 전 filter 유지
- 유효하지 않은 연도 범위는 적용 버튼 비활성화하고 field error 표시

## SCR-CAT-004 — 영화 상세

- 목업: `1a ⑥-2 영화 상세페이지`
- 접근: 비회원·회원
- API: `getMovie`, `getSimilarMovies`; OTT 영역은 detail 포함 summary 후 필요 시 `getMovieOttOffers`

### 표시 순서

1. poster/backdrop, 표시 제목
2. 개봉연도·대표 장르·러닝타임
3. 명시적으로 label된 외부 평점. 개인 예상 별점은 Recommendation 완료 전 숨김
4. TMDB overview 기반 줄거리와 실제 locale 표시가 필요한 fallback 상태
5. 감독과 출연진
6. `SCR-CAT-005` OTT 옵션
7. 유사 영화 최대 10편과 공통 특징 reason

### C0에서 숨기는 목업 영역

- ‘AI 영화 요약’ 문구와 생성 요약
- ‘별점 매기기’, 감상평 입력
- 다른 사용자 한줄평
- 개인 추천 이유

이 기능은 삭제가 아니라 후속 vertical contract가 생길 때 노출한다.

### 상태

- 404: `영화 정보를 찾을 수 없어요`와 검색 복귀
- poster null: local placeholder, 레이아웃 유지
- overview가 영어·원문 fallback이면 값은 표시하되 한국어 번역이라고 표기하지 않음
- 유사 영화 empty/error는 상세 본문과 분리하고 해당 section만 숨기거나 retry

## SCR-CAT-005 — OTT 옵션 영역

- 목업: 상세 내 `OTT`, `OTT 링크` 영역
- API: `getMovieOttOffers`

### 표시

1. 로그인 사용자의 구독 `FLATRATE`
2. 다른 `FLATRATE`
3. `RENT`, `BUY`, `FREE`, `ADS` 접힌 그룹

각 offer는 provider 이름, monetization label, 구독 여부, link type을 가진다.

| API 상태 | 화면 문구·행동 |
| --- | --- |
| `LISTED/FRESH` | offer 그룹 표시 |
| `LISTED/STALE` | offer 표시 + `정보 기준 {snapshotAt}` |
| `NONE_LISTED` | `현재 등록된 한국 시청 옵션이 없어요` |
| `UNKNOWN` | `OTT 정보를 확인할 수 없어요` + retry |

- `AGGREGATOR` 버튼: `시청 옵션 확인`
- `DIRECT` 버튼: `{providerName}에서 보기`
- 새 창·외부 앱을 열기 전에 외부 이동임을 표시한다.
- C0 Catalog 영역만 실행할 때는 외부 이동만 수행하며 분석 의미가 있는 event를 만들지 않는다.
  C0+C1 합성 화면에서는 승인된 C1 WatchIntent API의 성공 응답을 받은 뒤에만 server destination으로
  이동하고, 기록 실패 시 이동하지 않는다.

## 목업 수정 목록

| 현재 목업 | 계약 수정 |
| --- | --- |
| 검색 결과의 `★ 7.3` | `TMDB 7.3/10`처럼 출처·척도 표시 또는 숨김 |
| 상세의 `★ 3.7` | 값의 source가 확정되지 않으면 숨김 |
| `넷플릭스에서 바로 보기` | 검증된 direct link가 없으면 `시청 옵션 확인` |
| `시청 가능한 OTT 없음` | `NONE_LISTED`와 `UNKNOWN` 문구 분리 |
| `AI 영화 요약` | C0에서 TMDB 줄거리만 표시하고 AI label 제거 |
| 검색 홈 `지금 많이 찾는 영화` | C0에서 `인기 영화`; 실제 검색 trend는 FR-27 |
