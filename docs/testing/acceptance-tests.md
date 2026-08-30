# FEELM C0 Catalog Acceptance Test

> 상태: `APPROVED`  
> 승인 확장: `docs/c1-draft/testing/acceptance-tests.md` — C1 Rating·Film  
> Canonical registry: `docs/spec/approved-slices.json`  
> Fixture: `docs/testing/fixtures.md`  
> 고정 시계: `2026-08-29T12:00:00Z`

## 1. 검색·필터

| ID | Given / When / Then |
| --- | --- |
| `AC-CAT-001` | Given KO 전체 fixture, When `query=나우 유`, Then `MOV-KO-FULL`을 200 결과로 반환한다. |
| `AC-CAT-002` | Given 감독·배우 search document, When 감독 또는 배우명 query, Then 연결 영화를 반환한다. |
| `AC-CAT-003` | Given 공백 query, When 검색, Then query 없음으로 정규화하고 POPULARITY 기본 정렬을 사용한다. |
| `AC-CAT-004` | Given 장르 2개와 국가 2개, When filter, Then category 내 OR·category 간 AND 결과만 반환한다. |
| `AC-CAT-005` | Given Netflix flatrate와 Google Play rent fixture, When provider만 filter, Then 기본 FLATRATE만 적용한다. |
| `AC-CAT-006` | Given 2 page 이상 결과, When 같은 version·조건으로 cursor 순회, Then 중복·누락 없이 안정적인 순서다. |
| `AC-CAT-007` | Given 다른 query 또는 catalogVersion의 cursor, When 재사용, Then 400 `INVALID_CURSOR`다. |
| `AC-CAT-008` | Given 결과 없는 query, When 검색, Then 200, `items=[]`, `totalCount=0`, `hasNext=false`다. |
| `AC-CAT-009` | Given from year > to year 또는 limit 51, When 검색, Then field error가 있는 400이다. |
| `AC-CAT-010` | Given invalid optional token, When 검색, Then 익명 downgrade 없이 401이다. |

## 2. 영화 상세·현지화

| ID | Given / When / Then |
| --- | --- |
| `AC-CAT-011` | Given KO title·overview, When 상세 조회, Then 둘 다 ko-KR 값과 실제 locale을 반환한다. |
| `AC-CAT-012` | Given KO 없음·EN 있음, When 상세 조회, Then EN fallback과 `en-US` locale을 반환한다. |
| `AC-CAT-013` | Given Catalog-visible poster null 영화, When 상세 조회, Then 200과 `posterUrl=null`을 반환한다. |
| `AC-CAT-014` | Given TMDB rating, When 상세 조회, Then source=TMDB, scale=10, count를 함께 반환한다. |
| `AC-CAT-015` | Given credit fixture, When 상세 조회, Then 감독과 cast 최대 10명을 source order로 반환한다. |
| `AC-CAT-016` | Given TV mismatch 또는 UI incomplete ID, When 직접 조회, Then 둘 다 404 `MOVIE_NOT_FOUND`다. |
| `AC-CAT-017` | Given 정상 영화, When 상세 조회, Then raw MovieLens/TMDB/IMDb ID와 user ID를 응답하지 않는다. |

## 3. OTT 옵션

| ID | Given / When / Then |
| --- | --- |
| `AC-CAT-018` | Given 6시간 전 listed snapshot, When OTT 조회, Then LISTED/FRESH와 type별 offer group을 반환한다. |
| `AC-CAT-019` | Given Netflix 구독 사용자, When OTT 조회, Then Netflix FLATRATE가 다른 FLATRATE보다 앞이고 true다. |
| `AC-CAT-020` | Given 비회원, When OTT·검색 provider 조회, Then 모든 `isSubscribed=null`이다. |
| `AC-CAT-021` | Given 6시간 전 empty 성공 snapshot, When OTT 조회, Then NONE_LISTED/FRESH, groups empty다. |
| `AC-CAT-022` | Given 성공 snapshot 없음, When OTT 조회, Then UNKNOWN/UNKNOWN이며 NONE_LISTED가 아니다. |
| `AC-CAT-023` | Given 72시간 전 listed 성공 snapshot과 이후 refresh 실패, When 조회, Then LISTED/STALE 기존 offer다. |
| `AC-CAT-024` | Given TMDB availability URL, When 조회, Then link type AGGREGATOR이며 direct로 표기하지 않는다. |
| `AC-CAT-025` | Given 로그인 사용자가 구독하지 않은 offer, When 조회, Then 숨기지 않고 false로 뒤에 표시한다. |
| `AC-CAT-026` | Given flatrate/rent/buy가 같은 provider에 있음, When 조회, Then 세 type을 합치지 않는다. |
| `AC-CAT-027` | Given 공개 불가 movieId, When OTT 조회, Then 404 `MOVIE_NOT_FOUND`다. |

## 4. 유사 영화

| ID | Given / When / Then |
| --- | --- |
| `AC-CAT-028` | Given similarity fixture, When 조회, Then source 영화 자신을 포함하지 않는다. |
| `AC-CAT-029` | Given UI-ready와 no-poster·TV 후보, When 조회, Then UI-ready 영화만 반환한다. |
| `AC-CAT-030` | Given 같은 similarityVersion, When 같은 요청 반복, Then 순서와 reason이 동일하다. |
| `AC-CAT-031` | Given 유사 후보, When 조회, Then raw score 없이 구조화 reason 최대 3개를 반환한다. |
| `AC-CAT-032` | Given 유사 후보 0개, When 조회, Then 200과 빈 items다. |

## 5. Facet

| ID | Given / When / Then |
| --- | --- |
| `AC-CAT-033` | Given active genre master, When 장르 조회, Then displayOrder 순의 stable UUID·name을 반환한다. |
| `AC-CAT-034` | Given 영화가 연결된 국가, When 국가 조회, Then ISO code·한국어 name을 반환한다. |
| `AC-CAT-035` | Given active KR providers, When 비회원 provider 조회, Then priority 순·isSubscribed null이다. |
| `AC-CAT-036` | Given Netflix 구독 사용자, When provider 조회, Then Netflix만 isSubscribed true다. |

## 6. 장애·버전·성능·보안

| ID | Given / When / Then |
| --- | --- |
| `AC-CAT-037` | Given TMDB network 차단과 active Catalog, When 검색·상세, Then 외부 호출 없이 정상 응답한다. |
| `AC-CAT-038` | Given PostgreSQL 조회 불가, When Catalog API, Then 503 `CATALOG_UNAVAILABLE`와 traceId다. |
| `AC-CAT-039` | Given 새 import quality Gate 실패, When publish, Then 기존 active version과 API 결과가 유지된다. |
| `AC-CAT-040` | Given active version, When 한 응답 생성, Then body와 `X-Catalog-Version`이 동일하다. |
| `AC-CAT-041` | Given 87,585편 warm DB, When 20개 검색 200회, Then p95 <=300ms다. |
| `AC-CAT-042` | Given 같은 DB, When 상세 200회, Then p95 <=200ms다. |
| `AC-CAT-043` | Given API·job 오류, When log 검사, Then token·Authorization·원본 외부 body가 없다. |
| `AC-CAT-044` | Given query·cursor 공격 문자열, When 요청, Then parameterized query·cursor 검증으로 SQL 실행문이 변조되지 않는다. |

## 7. Frontend component/E2E

| ID | Given / When / Then |
| --- | --- |
| `AC-CAT-045` | Given 연속 query A→B와 A의 느린 응답, When B가 먼저 완료, Then A가 B 화면을 덮어쓰지 않는다. |
| `AC-CAT-046` | Given 검색 상세 진입 후 back, When 복귀, Then query·filter·scroll·loaded pages가 복원된다. |
| `AC-CAT-047` | Given poster null, When 카드·상세 렌더, Then local placeholder와 안정된 layout을 보인다. |
| `AC-CAT-048` | Given LISTED/STALE/NONE_LISTED/UNKNOWN, When OTT 영역 렌더, Then 네 계약 문구·행동을 구분한다. |
| `AC-CAT-049` | Given externalRating, When 렌더, Then `TMDB value/10` label이며 예상 별점으로 보이지 않는다. |
| `AC-CAT-050` | Given AGGREGATOR link, When 렌더, Then 버튼은 `시청 옵션 확인`이고 외부 이동임을 표시한다. |
