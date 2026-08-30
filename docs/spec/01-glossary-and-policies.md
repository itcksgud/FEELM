# FEELM Catalog 용어와 공통 정책

> 상태: `APPROVED` — C0 Catalog  
> 승인 확장: `docs/c1-draft/01-glossary-and-policies.md` — C1 Rating·Film  
> Canonical registry: `docs/spec/approved-slices.json`  
> 기준일: 2026-08-29

이 문서는 C0 기반 용어다. Rating·ViewingRecord·Film·Frame·Popcorn·Taste의 더 구체적인 정의는
승인된 C1 확장을 함께 적용한다.

## 1. 용어

| 용어 | 계약 정의 |
| --- | --- |
| 영화(Movie) | TMDB에서 `media_type=movie`로 검증된 장편·단편 영화 항목. TV 결과는 포함하지 않는다. |
| Catalog | 서비스가 마지막으로 정상 수집·검증해 로컬 DB에 저장한 영화 읽기 모델 |
| Catalog version | 검색 결과의 동일한 데이터 기준을 식별하는 불투명 문자열 |
| Catalog visible | identity 검증 영화 중 표시 제목·줄거리·장르가 있어 검색·상세에 노출 가능한 상태 |
| UI ready | 제목, 줄거리 fallback, 포스터, 러닝타임, 장르, 감독을 가진 검증 영화 |
| 표시 제목 | `ko-KR → en-US → original` 순서로 선택한 화면용 제목 |
| 표시 줄거리 | `ko-KR → en-US → 그 외 원문` 순서로 선택한 화면용 줄거리 |
| 현지화 locale | 실제로 선택된 텍스트의 BCP 47 언어 태그. 요청 locale로 거짓 표기하지 않는다. |
| OTT provider | Netflix, Watcha, wavve처럼 시청 옵션을 제공하는 서비스 기준 정보 |
| OTT offer | 영화·지역·provider·monetization type·스냅샷의 조합 |
| OTT availability | 최신 성공 스냅샷에서 확인되는 시청 옵션의 집합 |
| 구독형 | TMDB/JustWatch의 `flatrate`; rent·buy와 분리한다. |
| Aggregator link | TMDB의 availability 페이지처럼 제공처를 확인하는 링크. 해당 OTT 재생 딥링크가 아니다. |
| Direct link | 특정 provider의 해당 작품 재생·상세로 직접 가는 것이 검증된 링크 |
| 외부 평점 | TMDB 등 출처가 명시된 전체 사용자 집계 평점 |
| FEELM 평균 별점 | FEELM 서비스 사용자가 입력한 1~5점 평균. C0에는 데이터가 없어 nullable이다. |
| 내 예상 별점 | 개인 모델이 예측한 1~5점. Recommendation 계약이며 외부 평점과 다르다. |
| 유사 영화 | 구조화 특징과 임베딩 기반으로 기준 영화와 가까운 다른 UI-ready 영화 |

## 2. 식별자·시간·코드

- 공개 `movieId`, `providerId`, `personId`는 내부 UUID 문자열이다.
- MovieLens·TMDB·IMDb ID는 external ID이며 공개 resource ID로 사용하지 않는다.
- 시간은 ISO 8601 UTC로 전달하고 DB에는 timezone을 보존한다.
- locale은 BCP 47, 국가·지역은 ISO 3166-1 alpha-2 대문자를 사용한다.
- `KR` 이외 OTT region은 C0 범위 밖이다.
- cursor는 클라이언트가 해석하지 않는 opaque string이다.
- enum 값은 OpenAPI의 대문자 snake case를 기준으로 한다.

## 3. 누락과 빈 상태

| 상황 | 의미 | 금지할 해석 |
| --- | --- | --- |
| 한국어 제목 없음 | 영문·원제로 fallback | 영화 제목 자체가 없음 |
| 한국어 줄거리 없음 | 영문 또는 원문 fallback | 사용자가 싫어할 영화 |
| poster 없음 | UI placeholder 사용 | 영화 조회 실패 |
| 키워드 없음 | 유사도 feature 일부 누락 | 유사하지 않음 |
| 최신 성공 OTT 스냅샷에 offer 0개 | `NONE_LISTED` | 한국에서 절대 시청 불가 |
| 성공 스냅샷 없음·허용기간 초과 | `UNKNOWN` | `NONE_LISTED` |
| 오래됐지만 허용기간 안인 성공 스냅샷 | `STALE` 표시와 함께 제공 | 현재 정보라고 단정 |

## 4. 평점 표시

척도와 출처가 없는 별 아이콘 숫자는 금지한다.

| 필드 | 척도 | 필수 label 예 |
| --- | --- | --- |
| `externalRating` | 응답의 `scale` | `TMDB 7.3/10` |
| `feelmRating` | 1~5 | `FEELM 평균 3.8/5` |
| `preferenceEstimate` | 1~5 | `내 예상 별점 4.2/5` |

서로 변환해 같은 값처럼 보여주지 않는다. 값이 없으면 숫자 영역을 숨기고 `0점`을 표시하지 않는다.

## 5. 문서·응답 언어

- API enum과 field name은 영어를 사용한다.
- 사용자 문구 예시는 한국어를 기준으로 한다.
- `displayTitleLocale`과 `overviewLocale`은 실제 fallback 결과를 반환한다.
- 번역 문자열이 비어 있으면 locale record가 존재해도 사용하지 않는다.
