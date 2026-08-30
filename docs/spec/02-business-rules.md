# FEELM Catalog 업무 규칙

> 상태: `APPROVED` — C0 Catalog  
> 승인 확장: `docs/c1-draft/02-business-rules.md` — C1 Rating·Film  
> Canonical registry: `docs/spec/approved-slices.json`  
> 규칙 ID는 API·ERD·Acceptance Test에서 그대로 참조한다.

이 문서는 C0 `BR-CAT-*` 기반 규칙이다. 인증된 WatchIntent·Rating·projection 쓰기에는 승인된
C1 `BR-C1-*` 확장을 함께 적용하며, C2A 내부 규칙은 공개 제품 규칙으로 승격하지 않는다.

## 1. 접근과 데이터 원천

| ID | 규칙 |
| --- | --- |
| `BR-CAT-001` | 검색·상세·유사 영화·OTT 옵션은 비회원도 조회할 수 있다. |
| `BR-CAT-002` | 사용자 요청 경로는 TMDB를 실시간 호출하지 않고 로컬 Catalog read model만 조회한다. |
| `BR-CAT-003` | MovieLens 평점은 학습·통계 입력이고, 영화 표시 데이터의 우선 출처는 검증된 TMDB snapshot이다. |
| `BR-CAT-004` | `mediaType=MOVIE`, identity 검증 통과, 삭제되지 않은 항목만 공개 조회할 수 있다. |
| `BR-CAT-005` | 검색·상세에는 `CATALOG_VISIBLE`만 노출한다. 인기·유사 영화에는 더 엄격한 `UI_READY`만 노출한다. 그 외 상태의 직접 ID 조회는 404다. |

## 2. 검색

| ID | 규칙 |
| --- | --- |
| `BR-CAT-010` | `query`는 trim 후 1~100자다. 빈 문자열은 query 없음으로 처리한다. |
| `BR-CAT-011` | C0 검색 대상은 현지화 제목·원제·감독명·배우명이다. 대소문자와 연속 공백은 정규화한다. |
| `BR-CAT-012` | C0는 초성 검색·자동 철자 교정·형태소 동의어 확장을 보장하지 않는다. |
| `BR-CAT-013` | 서로 다른 filter category는 AND, 같은 category의 여러 값은 OR로 결합한다. |
| `BR-CAT-014` | `ottProviderIds` filter는 `KR`의 최신 사용 가능 snapshot에 provider offer가 있는 영화를 찾는다. 기본 monetization type은 `FLATRATE`다. |
| `BR-CAT-015` | query가 있으면 기본 정렬은 `RELEVANCE`, 없으면 `POPULARITY`다. 동점의 최종 정렬키는 `movieId` 오름차순이다. |
| `BR-CAT-016` | cursor는 catalogVersion·filter·sort와 결합된다. 다른 조건에 재사용하면 `INVALID_CURSOR`다. |
| `BR-CAT-017` | 페이지 크기 기본값은 20, 최댓값은 50이다. 같은 version과 조건에서는 중복·누락 없이 안정적이다. |
| `BR-CAT-018` | 검색 결과가 없으면 HTTP 200과 빈 `items`, `hasNext=false`를 반환한다. |

## 3. 현지화·영화 상세

| ID | 규칙 |
| --- | --- |
| `BR-CAT-020` | 제목 fallback은 값이 있는 `ko-KR → en-US → originalTitle` 순서다. |
| `BR-CAT-021` | 줄거리 fallback은 값이 있는 `ko-KR → en-US → original language` 순서다. |
| `BR-CAT-022` | 선택된 실제 locale을 `displayTitleLocale`, `overviewLocale`로 반환한다. |
| `BR-CAT-023` | 포스터가 없으면 `posterUrl=null`이고 frontend가 로컬 placeholder를 사용한다. fake 원격 URL을 만들지 않는다. |
| `BR-CAT-024` | 외부 평점은 출처·척도·평가 수를 함께 반환한다. 개인 예상 별점과 같은 필드에 넣지 않는다. |
| `BR-CAT-025` | 감독·출연진은 TMDB credit order를 보존한다. 상세 출연진 기본 반환 상한은 10명이다. |
| `BR-CAT-026` | 상세 응답의 `metadataAsOf`와 `catalogVersion`으로 데이터 기준 시점을 공개한다. |

## 4. OTT 옵션

| ID | 규칙 |
| --- | --- |
| `BR-CAT-030` | C0 OTT region은 `KR`로 고정한다. 다른 region 요청은 허용하지 않는다. |
| `BR-CAT-031` | `FLATRATE`, `RENT`, `BUY`, `FREE`, `ADS`를 별도 그룹으로 반환한다. |
| `BR-CAT-032` | 로그인 사용자에게는 구독한 provider의 `FLATRATE` offer를 먼저 정렬한다. 비회원은 provider display priority와 이름으로 정렬한다. |
| `BR-CAT-033` | 구독하지 않은 provider를 숨기지 않는다. `isSubscribed=false`로 뒤에 표시한다. 비회원은 `isSubscribed=null`이다. |
| `BR-CAT-034` | TMDB watch-provider URL은 `AGGREGATOR` link다. 검증된 provider URL만 `DIRECT`로 표기한다. |
| `BR-CAT-035` | `AGGREGATOR`를 ‘해당 OTT에서 바로 재생’으로 안내하지 않고 ‘시청 옵션 확인’으로 표시한다. |
| `BR-CAT-036` | 성공 snapshot이 24시간 이내면 `FRESH`, 24시간 초과 7일 이내면 `STALE`, 없거나 7일 초과면 `UNKNOWN`이다. |
| `BR-CAT-037` | `NONE_LISTED`는 7일 이내 성공 snapshot에 offer가 0개일 때만 사용한다. |
| `BR-CAT-038` | refresh 실패 시 마지막 성공 snapshot이 7일 이내면 stale 데이터와 실패 없는 API 응답을 제공한다. |

## 5. 유사 영화

| ID | 규칙 |
| --- | --- |
| `BR-CAT-040` | 기준 영화 자신, TV, identity 미검증, UI 미준비 영화는 유사 영화 목록에서 제외한다. |
| `BR-CAT-041` | 기본 반환 수는 10, 최댓값은 30이다. |
| `BR-CAT-042` | 같은 `similarityVersion`에서 순서는 결정적이며 동점은 `movieId`로 정렬한다. |
| `BR-CAT-043` | C0 유사도는 장르·감독·배우·키워드·텍스트 임베딩 중 이용 가능한 feature를 사용한다. feature 누락을 0점 선호로 해석하지 않는다. |
| `BR-CAT-044` | similarity score 원시는 UI에 노출하지 않는다. 응답에는 구조화된 공통 특징 reason을 최대 3개 제공한다. |

## 6. 오류와 개인정보

| ID | 규칙 |
| --- | --- |
| `BR-CAT-050` | 존재하지 않거나 공개 불가한 movieId는 구분하지 않고 `MOVIE_NOT_FOUND` 404로 반환한다. |
| `BR-CAT-051` | validation 오류는 field별 detail이 있는 `VALIDATION_ERROR` 400으로 반환한다. |
| `BR-CAT-052` | 비회원 응답에는 구독 여부, 사용자 ID, 개인 예상 별점이 포함되지 않는다. |
| `BR-CAT-053` | optional access token이 유효하지 않으면 익명으로 무시하지 않고 `INVALID_ACCESS_TOKEN` 401을 반환한다. |
| `BR-CAT-054` | 모든 오류는 안정적인 `code`, 사용자 비노출 내부 추적용 `traceId`, 안전한 `message`를 포함한다. |
