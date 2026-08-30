# FEELM Catalog 수집 계약

> 상태: `APPROVED` — C0 Catalog  
> 실제 데이터 근거: `docs/research/movielens-tmdb-data-audit.md`

## 1. 원천 역할

| Source | 사용 | 사용하지 않음 |
| --- | --- | --- |
| MovieLens 32M | 초기 영화 universe, 평점 수, 장르 fallback, TMDB/IMDb link | 최신 Catalog, 한국 현지화, OTT 정답 |
| TMDB movie/details | 영화 identity, localization, image, runtime, genre, country, credit, rating | FEELM 개인 예상 별점 |
| TMDB find by IMDb | 비어 있거나 stale한 TMDB ID 복구 | type·연도 검증 없는 자동 치환 |
| TMDB/JustWatch providers | KR availability snapshot | 완전한 provider deep link 보장 |
| FEELM 서비스 DB | 공개 UUID, version, visibility, 사용자 구독 | 외부 source ID를 사용자 resource ID로 노출 |

Wikipedia/Wikidata는 C0 필수 수집원이 아니다. Wikidata ID는 향후 enrichment를 위해 external ID로만
보존할 수 있다.

## 2. Artifact 경계

```text
MovieLens zip + TMDB API
  → Python catalog fetch/normalize job
  → versioned normalized artifact + quality report
  → Spring CatalogImportService staging import
  → Schema·quality Gate
  → active Catalog version publish
  → Catalog API local read
```

- Python job은 운영 DB에 직접 쓰지 않는다.
- artifact는 ADR-0006에 따라 UTF-8 JSONL schema v1을 사용한다. 첫 행은 version·provenance header다.
- normalized artifact에 token과 전체 HTTP header를 넣지 않는다.
- Spring import는 하나의 artifact version을 완전히 검증한 뒤 active pointer를 교체한다.

## 3. Identity 처리

```text
links.tmdbId 있음
  → TMDB /movie/{id}
      ├ success + IMDb/year/title 검증 → IDENTITY_VERIFIED
      └ 404/불일치 → IMDb /find

links.tmdbId 없음
  → IMDb /find

IMDb /find
  ├ movie 1개 + 검증 통과 → RECOVERED → IDENTITY_VERIFIED
  ├ tv only → TYPE_MISMATCH_TV
  ├ 결과 0 → TMDB_NOT_FOUND
  └ 다중·불일치 → IDENTITY_REVIEW_REQUIRED
```

검증 항목:

- IMDb ID 완전 일치
- TMDB media type movie
- 개봉연도 같음. 한쪽이 연도만 있고 다른 쪽이 전체 날짜면 연도 비교
- 정규화 제목 유사도. 연도·IMDb가 일치해도 다중 movie 결과면 review
- 기존 TMDB ID가 다른 ID로 복구되면 이전 ID도 provenance로 보존

현재 감사 기준으로 비어 있는 124개 TMDB ID 중 75개는 movie 복구, 11개는 TV, 38개는 미검색이었다.
stale ID도 발생하므로 null 여부만 검사하면 안 된다.

## 4. Metadata 정규화

| Normalized field | TMDB field·규칙 |
| --- | --- |
| original title/language | `original_title`, `original_language` |
| localization | translations의 locale별 title·overview; 공백은 null |
| release date | movie `release_date`; KR release는 별도 확장 가능 |
| runtime | 양수만 저장 |
| genres | TMDB genre mapping; 없으면 MovieLens 장르는 provenance와 함께 fallback 가능 |
| countries | `production_countries.iso_3166_1` |
| director | credits crew `job=Director` |
| cast | credits cast의 원래 order 보존 |
| images | poster/backdrop path; URL base·size는 projection에서 결정 |
| external rating | TMDB vote average/count, source·10점 scale 고정 |
| keywords/embedding input | 추천 feature artifact로 보존, Catalog 상세 필수 아님 |

## 5. Visibility 계산

```text
CATALOG_VISIBLE =
  identity verified
  AND media type movie
  AND not deleted
  AND display title fallback exists
  AND overview fallback exists
  AND genre >= 1

UI_READY =
  CATALOG_VISIBLE
  AND poster exists
  AND runtime > 0
  AND director >= 1
```

- 검색·직접 상세는 Catalog visible을 사용한다.
- 인기·유사·추천 candidate는 UI ready를 사용한다.
- cast 3명 미만은 오류가 아니다.
- 한국어 translation 누락은 visibility 실패가 아니다.

## 6. Availability 수집

- region은 `KR`만 요청한다.
- source 배열 `flatrate`, `rent`, `buy`, `free`, `ads`를 서로 다른 enum으로 저장한다.
- 성공 응답은 offer 유무에 따라 `SUCCESS_LISTED` 또는 `SUCCESS_EMPTY` snapshot을 만든다.
- 네트워크·429·5xx는 `FAILED` snapshot을 기록하되 마지막 성공 snapshot을 덮어쓰지 않는다.
- source URL은 기본적으로 snapshot의 aggregator URL이며 offer link type은 `AGGREGATOR`다.
- provider ID와 표시명·logo는 provider master로 upsert한다.
- TMDB/JustWatch attribution을 UI credits와 제품 About 영역에서 제공한다.

## 7. 초기 갱신 주기

| 데이터 | 주기 | 실패 처리 |
| --- | --- | --- |
| 영화 identity·metadata | 7일 incremental, 전체 월 1회 | 기존 metadata 유지, source removed는 2회 연속 확인 후 전환 |
| 신규 TMDB movie 보강 | 일 1회 | 다음 실행 재시도 |
| KR availability | 24시간 | 마지막 성공 7일까지 stale serve |
| Genre/provider master | metadata·availability 실행 때 | 기존 active master 유지 |
| Similarity | metadata version publish 후 | 이전 similarity version 유지 |

이 값은 C0 운영 가정이다. 실제 변화율·API 할당량을 측정한 뒤 ADR로 수정한다.

## 8. Publish quality Gate

초기 전체 import는 다음을 모두 만족해야 한다.

| Gate | 조건 |
| --- | --- |
| external ID uniqueness | source별 중복 0건 |
| TV exposure | Catalog visible 중 0건 |
| required projection | Catalog visible의 display title·overview·genre 누락 0건 |
| UI-ready validity | UI ready의 poster·runtime·director 누락 0건 |
| relation integrity | orphan localization·genre·credit·offer 0건 |
| snapshot consistency | SUCCESS_EMPTY offer 0, SUCCESS_LISTED offer 1개 이상 |
| version atomicity | API가 한 요청에서 둘 이상의 active catalog version을 혼합하지 않음 |
| regression report | 이전 version 대비 visible/UI-ready/provider 수 증감과 이유 출력 |

MovieLens 전체 항목 수가 87,585보다 작다는 이유만으로 publish를 실패시키지 않는다. movie-only 검증,
source removal, UI 품질 Gate로 합법적으로 제외될 수 있으므로 상태별 수를 보고한다.

## 9. C0 고정 fixture

| Fixture ID | 목적 |
| --- | --- |
| `MOV-KO-FULL` | 한국어 title·overview, poster, KR flatrate 존재 |
| `MOV-EN-FALLBACK` | 한국어 없음, 영어 fallback |
| `MOV-NO-POSTER` | Catalog visible이나 UI ready 아님 |
| `MOV-NONE-LISTED` | 최신 성공 availability가 빈 목록 |
| `MOV-OTT-UNKNOWN` | 성공 snapshot 없음 또는 7일 초과 |
| `MOV-OTT-STALE` | 24시간 초과 7일 이내 offer |
| `MOV-STALE-ID-RECOVERED` | 기존 TMDB 404, IMDb로 새 movie ID 복구 |
| `MOV-TV-MISMATCH` | IMDb find가 TV만 반환하여 공개 제외 |
| `MOV-KO-FULL` | 유사 영화 source와 reason fixture도 겸함 |

fixture는 실제 외부 API 없이 import·API·UI 테스트에서 동일한 UUID와 값으로 재사용한다.
