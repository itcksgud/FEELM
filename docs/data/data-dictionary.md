# FEELM Catalog 데이터 사전

> 상태: `APPROVED` — C0 Catalog
> 승인 확장: `docs/c1-draft/data/data-dictionary.md` — C1 Rating·Film  
> Canonical registry: `docs/spec/approved-slices.json`

## 1. 공통 enum

| Enum | 값 | 의미 |
| --- | --- | --- |
| `IdentityStatus` | `IDENTITY_VERIFIED`, `TYPE_MISMATCH_TV`, `TMDB_NOT_FOUND`, `IDENTITY_REVIEW_REQUIRED`, `SOURCE_REMOVED` | 외부 식별 상태 |
| `VisibilityStatus` | `UI_READY`, `CATALOG_VISIBLE`, `UI_INCOMPLETE` | 공개·추천 사용 가능 수준 |
| `ExternalIdSource` | `MOVIELENS`, `TMDB`, `IMDB`, `WIKIDATA` | 외부 ID namespace |
| `CreditType` | `DIRECTOR`, `CAST` | C0 공개 credit |
| `SnapshotFetchStatus` | `SUCCESS_LISTED`, `SUCCESS_EMPTY`, `FAILED` | 한 movie·region 조회 결과 |
| `MonetizationType` | `FLATRATE`, `RENT`, `BUY`, `FREE`, `ADS` | OTT 제공 유형 |
| `OfferLinkType` | `AGGREGATOR`, `DIRECT` | 링크가 실제 provider 딥링크인지 여부 |
| `CatalogVersionStatus` | `STAGING`, `ACTIVE`, `RETIRED`, `REJECTED` | read model publish 상태 |
| `SyncRunStatus` | `RUNNING`, `SUCCEEDED`, `FAILED`, `REJECTED` | batch 실행 결과 |

## 2. Movie identity와 Catalog projection

`MOVIE_IDENTITY`는 수집 version과 무관한 공개 식별자다.

| Field | Type | Null | Source | 규칙 |
| --- | --- | --- | --- | --- |
| `id` | UUID | N | service | 공개 `movieId`; Catalog 갱신 시 유지하며 외부 ID에서 결정적으로 생성하지 않음 |
| `created_at` | timestamptz | N | service | identity 최초 생성 시각 |

아래 필드는 `MOVIE_CATALOG_PROJECTION`에 저장하며 `(catalog_version_id, movie_id)`가 복합키다.

| Field | Type | Null | Source | 규칙 |
| --- | --- | --- | --- | --- |
| `catalog_version_id` | UUID | N | service | projection의 publish version, 복합 PK/FK |
| `movie_id` | UUID | N | service | 안정적인 `MOVIE_IDENTITY.id`, 복합 PK/FK |
| `media_type` | varchar | N | TMDB/find | C0는 `MOVIE`만 허용 |
| `identity_status` | enum | N | identity job | 검증 전 공개 금지 |
| `visibility_status` | enum | N | quality rule | 공개·유사 후보 Gate |
| `original_title` | varchar(500) | N | TMDB | 원문 제목 |
| `original_language` | varchar(16) | N | TMDB | BCP 47로 정규화 |
| `release_date` | date | Y | TMDB | 연도만 알면 별도 partial-date를 고려하되 C0 import는 null 허용 |
| `runtime_minutes` | int | Y | TMDB | 양수 |
| `poster_path` | varchar | Y | TMDB | base URL과 size는 응답 projection에서 결합 |
| `backdrop_path` | varchar | Y | TMDB | 동일 |
| `tmdb_vote_average` | numeric(4,2) | Y | TMDB | 0~10, 외부 평점 |
| `tmdb_vote_count` | bigint | N | TMDB | 0 이상 |
| `metadata_fetched_at` | timestamptz | N | ingestion | 실제 성공 조회 시각 |
| `deleted` | boolean | N | service | source removed 시 true |

## 3. External identity

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `movie_id` | UUID | N | `MOVIE_IDENTITY` FK |
| `source` | enum | N | external namespace |
| `external_id` | varchar(64) | N | TMDB는 숫자 문자열, IMDb는 `tt` prefix 정규화 |
| `verification_status` | varchar | N | `VERIFIED`, `RECOVERED`, `UNVERIFIED` |
| `verified_at` | timestamptz | Y | 검증 성공 시각 |

## 4. Localization

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `catalog_version_id` | UUID | N | projection 복합 FK |
| `movie_id` | UUID | N | projection 복합 FK |
| `locale` | varchar(16) | N | BCP 47; `ko-KR`, `en-US` 우선 |
| `title` | varchar(500) | Y | 공백 문자열은 null로 정규화 |
| `overview` | text | Y | 공백 문자열은 null로 정규화 |
| `source` | varchar | N | C0는 `TMDB` |
| `fetched_at` | timestamptz | N | source snapshot 시각 |

`displayTitle`과 `overview`는 저장 원천 field가 아니라 locale row와 original field를 선택한 API
projection이다. 실제 선택 locale을 함께 반환한다.

## 5. Genre·country·credit

| 항목 | 규칙 |
| --- | --- |
| Genre | service UUID와 안정적인 code 사용. TMDB numeric ID는 source mapping에 둘 수 있다. |
| Country | ISO alpha-2 code를 PK로 사용하고 한국어·영문 표시명을 분리한다. |
| Person | service UUID, TMDB person ID unique. 이름은 source display name이며 계정 User와 무관하다. |
| Movie relation | genre·country·credit 관계는 모두 `(catalog_version_id, movie_id)` projection에 종속된다. |
| Director | crew job이 Director인 credit만 C0 `DIRECTOR`로 정규화한다. |
| Cast | TMDB cast order를 `credit_order`로 보존하고 API는 앞 10명까지 반환한다. |

## 6. OTT provider·snapshot·offer

| Field | Type | Null | 규칙 |
| --- | --- | --- | --- |
| `snapshot.catalog_version_id` | UUID | N | active/staging projection version |
| `snapshot.movie_id` | UUID | N | 같은 version의 movie projection FK |
| `provider.id` | UUID | N | 공개 providerId |
| `provider.tmdb_provider_id` | bigint | N | TMDB provider ID unique |
| `provider.provider_code` | varchar | N | 내부 안정 코드; 표시명 변경과 분리 |
| `provider.display_name` | varchar | N | source 표시명 |
| `provider.logo_path` | varchar | Y | UI local fallback 허용 |
| `provider.display_priority` | int | N | 비회원 정렬, 0 이상 |
| `snapshot.region` | char(2) | N | C0 `KR` |
| `snapshot.fetch_status` | enum | N | 성공 목록/성공 빈 목록/실패 구분 |
| `snapshot.source` | varchar | N | `TMDB_JUSTWATCH` |
| `snapshot.aggregator_url` | varchar | Y | TMDB availability URL |
| `snapshot.fetched_at` | timestamptz | N | 요청 성공·실패 시각 |
| `snapshot.fresh_until` | timestamptz | N | 성공 기준 +24시간 |
| `snapshot.serve_until` | timestamptz | N | 성공 기준 +7일 |
| `offer.monetization_type` | enum | N | type별 분리 |
| `offer.link_type` | enum | N | 기본 `AGGREGATOR` |
| `offer.landing_url` | varchar | Y | link type의 실제 URL |

## 7. API 파생 상태

| API field | 계산 |
| --- | --- |
| `availabilityStatus=LISTED` | 7일 이내 마지막 성공 snapshot이 `SUCCESS_LISTED` |
| `availabilityStatus=NONE_LISTED` | 7일 이내 마지막 성공 snapshot이 `SUCCESS_EMPTY` |
| `availabilityStatus=UNKNOWN` | 성공 snapshot 없음 또는 `serve_until < now` |
| `freshness=FRESH` | 성공 snapshot의 `fresh_until >= now` |
| `freshness=STALE` | `fresh_until < now <= serve_until` |
| `freshness=UNKNOWN` | availability unknown |
| `isSubscribed` | 익명 null, 로그인 시 `(user_id, provider_id)` 존재 여부 |

## 8. PII·보존

- Catalog 자체에는 PII가 없다.
- `USER_OTT_SUBSCRIPTION.user_id`는 Profile domain의 pseudonymous UUID이며 공개 응답에 노출하지 않는다.
- 성공 availability snapshot은 품질 분석을 위해 최소 90일 보존하고 active 조회는 최신 성공 1개만 사용한다.
- failed snapshot의 안전한 failure code는 30일 보존하고 외부 응답 본문·token은 저장하지 않는다.
- 원본 TMDB 응답 보존 여부는 라이선스·용량 검토 후 별도 ADR로 결정한다. C0 필수는 normalized field와 provenance다.
