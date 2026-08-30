# MovieLens 32M · TMDB 실제 데이터 감사

> 감사일: 2026-08-29  
> 대상: `ml-32m.zip` 전체와 현재 TMDB API 응답  
> 목적: FEELM의 추천 학습, 영화 카탈로그, 한국 OTT 비교에 실제로 쓸 수 있는 데이터와 빈 부분을 구분한다.

## 1. 결론

MovieLens 32M과 TMDB만으로 **기존 인기 영화의 추천·표시·OTT 비교 MVP는 충분히 만들 수 있다.**
Wikipedia를 기본 수집원으로 추가할 필요는 없다. 다만 다음 한계는 설계로 처리해야 한다.

1. MovieLens 영화의 63.5%는 평점이 10개 미만이다. ALS 점수를 신뢰하기 어려운 롱테일이 더 크다.
2. 무작위 카탈로그 표본에서 TMDB 한국어 제목은 39.9%, 한국어 줄거리는 32.3%만 존재했다.
   반면 화면에 표시할 제목과 영어 줄거리는 성공 응답 전부에 있었다.
3. 한국 구독형 OTT 정보는 무작위 표본의 16.2%에만 있었다. 값이 없다고 국내에서 절대 볼 수
   없다고 단정하면 안 된다.
4. MovieLens의 TMDB ID는 99.86% 채워져 있지만, ID가 있어도 삭제·변경되어 404가 나거나 TV
   작품으로 판명되는 사례가 있다.
5. `ml-32m.zip`에는 Tag Genome이 없다. `tags.csv`는 편향이 큰 자유 태그이며 대체재가 아니다.
6. MovieLens 상호작용은 2023-10-13에 끝난다. 2026년 현재 TMDB 카탈로그와 결합해도 신작의
   오프라인 추천 정답이 생기는 것은 아니다.

따라서 데이터 구조는 `MovieLens = 상호작용 정답`, `TMDB = 영화 표시·콘텐츠 특징`,
`Watch Providers = 현재 시점의 보조 가용성`으로 분리한다.

## 2. 감사 대상과 방법

### 2.1 원본

- 로컬 원본: `C:\higher\projects\MM\data\raw\ml-32m.zip`
- MovieLens 생성일: 2023-10-13
- 평점 관측 마지막 시각: 2023-10-13 02:29:07 UTC
- TMDB 조회: 2026-08-29, `ko-KR`
- TMDB 추가 응답: credits, keywords, external IDs, translations, images, watch providers,
  release dates

MovieLens 전체 파일은 전수 집계했다. TMDB는 호출량과 인기도 편향을 함께 확인하기 위해 세 표본을
구성했다.

| 표본 | 목적 | 성공 응답 |
| --- | --- | ---: |
| 균등 무작위 | 전체 카탈로그의 일반적 상태 추정 | 396 / 요청 400 |
| 평점 수 상위 | 실제 첫 화면에 노출될 인기작 상태 | 100 / 요청 100 |
| 인기도 × 시대 층화 | 0평점·롱테일·고전까지 고의로 압박 | 359 |

중복을 제거한 전체 감사 집합은 856편 요청, 843편 성공, 13편 404였다. 이 합집합은 층화 표본이
섞여 있으므로 전체 카탈로그의 비율 추정에는 쓰지 않고, 필드 누락 사례 탐색에만 쓴다.

## 3. MovieLens 32M 전수 프로필

### 3.1 기본 규모

| 항목 | 실제 값 | 해석 |
| --- | ---: | --- |
| 사용자 | 200,948명 | 인구통계 없음, 모두 최소 20편 이상 평가 |
| 평점 | 32,000,204개 | 0.5~5.0, 0.5 간격의 명시적 평점 |
| 영화 | 87,585편 | 평점 또는 태그가 하나 이상 있는 항목 |
| 자유 태그 적용 | 2,000,072개 | Tag Genome이 아닌 사용자 입력 문자열 |
| TMDB ID 있음 | 87,461편, 99.86% | 존재 여부만으로 현재 유효성을 보장하지 않음 |
| TMDB ID 없음 | 124편, 0.14% | 전부 IMDb ID는 존재 |
| IMDb ID 있음 | 87,585편, 100% | TMDB 복구 조회의 기준 키로 사용 가능 |
| MovieLens 장르 없음 | 7,080편, 8.08% | TMDB 장르로 보완 가능 |

### 3.2 영화별 상호작용은 매우 긴 꼬리를 가진다

| 평점 수 | 영화 수 | 사용 방안 |
| --- | ---: | --- |
| 0 | 3,153 | ALS Item Factor 없음. 콘텐츠 기반 전용 cold item |
| 1~9 | 52,471 | 협업 점수의 불확실성이 큼. 콘텐츠 점수와 신뢰도 보정 |
| 10~99 | 19,770 | 제한적으로 협업 추천 가능 |
| 100~999 | 7,794 | 비교적 안정적인 협업 신호 |
| 1,000~9,999 | 3,625 | warm item |
| 10,000 이상 | 772 | 강한 인기 편향이 있는 head item |

영화별 평점 중앙값은 5개, 90백분위는 230개, 99백분위는 8,877개이며 최댓값은
102,929개다. 0~9개 구간이 55,624편으로 전체의 63.5%다.

이 때문에 모든 영화를 같은 ALS 점수로 비교하면 안 된다. 학습에는 관측 평점을 유지하되, 서빙
시 `ratingCount`와 구간별 검증 오차를 이용해 신뢰도를 별도로 계산한다. 0평점 영화는 ALS가 아니라
TMDB 콘텐츠 임베딩과 인기·품질 제약으로만 후보화한다.

### 3.3 자유 태그는 약한 보조 특징이다

| 항목 | 실제 값 |
| --- | ---: |
| 태그를 한 사용자 | 15,848명, 전체 사용자의 7.89% |
| 태그가 있는 영화 | 51,323편, 전체 영화의 58.6% |
| 대소문자·공백 정규화 후 고유 태그 | 131,645개 |
| 태그 있는 영화의 태그 수 중앙값 | 5개 |
| 상위 1% 태깅 사용자의 태그 점유율 | 65.54% |
| 상위 10% 태깅 사용자의 태그 점유율 | 89.97% |
| 단일 사용자의 최대 태그 수 | 723,473개 |

상위 태그는 `sci-fi`, `atmospheric`, `action`, `comedy`, `funny`, `surreal`,
`visually appealing`, `based on a book` 등이다. 표현력은 좋지만 기여자 편향이 매우 크다.

따라서 `tags.csv`는 다음 처리 후에만 쓴다.

- 동일 사용자·영화·정규화 태그 중복 제거
- 사용자 한 명이 한 영화와 전체 말뭉치에 미치는 기여 상한 적용
- 대소문자, 구두점, 철자·동의어 정규화와 부적절 표현 필터링
- 원시 빈도 대신 TF-IDF/BM25 또는 사용자별 정규화 빈도 사용
- MovieLens 장르·TMDB 키워드·줄거리 임베딩과 결합하고 단독 정답으로 사용하지 않음

`ml-32m.zip`의 실제 파일은 `ratings.csv`, `tags.csv`, `movies.csv`, `links.csv`뿐이다.
`genome-scores.csv`와 `genome-tags.csv`는 없다. Tag Genome 실험을 하려면 별도 데이터셋의
MovieLens ID 버전 호환성을 먼저 검증해야 한다.

## 4. MovieLens ↔ TMDB 식별자 무결성

### 4.1 TMDB ID가 비어 있는 124편

MovieLens의 IMDb ID로 TMDB `/find`를 전수 조회한 결과다.

| 결과 | 수 | 처리 |
| --- | ---: | --- |
| TMDB 영화로 복구 가능 | 75 | 제목·연도·IMDb ID를 재검증한 뒤 `RECOVERED_BY_IMDB` |
| TMDB TV로만 검색됨 | 11 | 영화 전용 서비스라면 `TYPE_MISMATCH_TV`로 제외 |
| 검색 결과 없음 | 38 | MovieLens 최소 메타데이터만 유지하거나 제외 |

빈 TMDB ID의 60.5%는 IMDb 경유로 영화 ID를 복구할 수 있다. 그러나 `Dune (2000)`처럼
MovieLens에는 영화 항목으로 있으나 TMDB에서는 TV 미니시리즈인 사례가 있어 자동 치환하면 안 된다.

### 4.2 ID가 있지만 현재 404인 사례

감사 집합의 13개 stale ID를 IMDb로 다시 조회했다.

| 결과 | 수 | 예시 |
| --- | ---: | --- |
| 새 TMDB 영화 ID 복구 | 9 | `Saving Star Wars`, `Heroin(e)` |
| TV로 판명 | 1 | `Cosmos: A Personal Voyage` |
| 결과 없음 | 3 | 별도 검토 또는 제외 |

균등 무작위 표본에서는 400편 중 4편이 404였다. 즉 `tmdbId IS NOT NULL`만으로 수집 성공을
가정하지 말고, 수집 상태와 마지막 검증 시각을 저장해야 한다.

### 4.3 권장 식별 상태

```text
ML_TMDB_VERIFIED
RECOVERED_BY_IMDB
TYPE_MISMATCH_TV
TMDB_NOT_FOUND
IDENTITY_REVIEW_REQUIRED
```

IMDb 복구 결과는 다음 조건을 모두 확인한 뒤 채택한다.

1. TMDB external ID가 MovieLens IMDb ID와 동일하다.
2. `media_type=movie`다.
3. 개봉 연도가 일치하거나 허용 오차 안이다.
4. 제목 유사도가 기준 이상이다.
5. 여러 결과가 나오면 자동 선택하지 않는다.

## 5. TMDB 필드 충족률

### 5.1 전체 카탈로그에 가까운 균등 무작위 표본

비율의 분모는 성공 응답 396편이다.

| 필드 | 충족률 | 판단 |
| --- | ---: | --- |
| 표시 가능한 제목 | 100.0% | 한국어가 없으면 원제·영문 fallback 가능 |
| 한국어 현지화 제목 | 39.9% | `title` 존재와 구분해서 저장해야 함 |
| 한국어 줄거리 | 32.3% | 가장 큰 화면 품질 공백 |
| 영어 줄거리 | 100.0% | 감사 표본에서는 fallback 가능 |
| 포스터 | 98.5% | 기본 placeholder만 준비하면 됨 |
| 러닝타임 | 99.0% | 서비스 필터에 사용 가능 |
| 장르 | 99.5% | MovieLens 무장르 7,080편 보완 가능 |
| 감독 | 99.2% | 콘텐츠 특징과 설명에 사용 가능 |
| 출연진 3명 이상 | 92.2% | 단편·기록물은 실제로 인원이 적을 수 있음 |
| 키워드 | 69.4% | 없음을 오류로 보지 말고 임베딩으로 보완 |
| Wikidata ID | 83.8% | 향후 구조화 보강의 선택 경로 |
| 한국 내 모든 제안 | 24.0% | 구독·무료·광고·구매·대여 합계 |
| 한국 구독형 스트리밍 | 16.2% | OTT 비교에는 `flatrate`만 별도 사용 |
| 한국 개봉 정보 | 12.1% | 롱테일의 국내 개봉·등급은 매우 희소 |
| 한국 관람 등급 | 9.9% | 필수 필드로 잡으면 카탈로그가 크게 줄어듦 |

엄격한 화면 핵심 필드인 `제목 + 줄거리 fallback + 포스터 + 러닝타임 + 장르 + 감독`을 모두
가진 영화는 성공 응답 중 383편, 96.7%였다. 요청 전체 기준으로는 95.8%다.

### 5.2 인기도에 따라 품질이 급격히 달라진다

아래는 층화 감사 집합의 성공 응답이므로 각 구간의 품질 차이를 보는 용도다.

| MovieLens 평점 수 | 표본 N | 한국어 제목 | 한국어 줄거리 | 키워드 | 한국 구독형 OTT |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 | 75 | 5.3% | 1.3% | 80.0% | 4.0% |
| 1~9 | 304 | 22.0% | 17.1% | 59.5% | 9.2% |
| 10~99 | 144 | 59.0% | 45.8% | 86.8% | 22.2% |
| 100~999 | 96 | 86.5% | 79.2% | 95.8% | 29.2% |
| 1,000~9,999 | 80 | 96.3% | 95.0% | 100.0% | 46.3% |
| 10,000 이상 | 144 | 100.0% | 100.0% | 100.0% | 70.1% |

평점 수 상위 100편은 제목, 한국어 제목·줄거리, 포스터, 러닝타임, 장르, 감독, 키워드가 전부
100%였고 한국 구독형 OTT는 74%였다. 즉 초기 데모의 인기작 품질은 좋지만, FEELM의 차별점인
새로운 영화 탐색으로 갈수록 현지화와 OTT 데이터가 빠르게 약해진다.

### 5.3 실제 누락 예시

- `To Cross the Rubicon (1991)`: 포스터와 러닝타임 없음
- `Moral Tales, Filmic Issues (2006)`: 감독 없음
- `Manual of Arms (1966)`: 장르 없음
- 일부 단편·기록 영상: 출연진 3명 미만
- `Cosmos`: MovieLens 영화 항목이지만 TMDB에서는 TV 작품

이들은 모두 같은 종류의 오류가 아니다. 단편의 짧은 출연진처럼 실제 값이 적은 경우와 수집
누락을 구분해야 한다.

## 6. 데이터별 역할과 금지할 사용

| 데이터 | 사용 | 사용하지 않을 것 |
| --- | --- | --- |
| MovieLens ratings | ALS 학습, 시간 분할 평가, 예상 별점 보정 | 미평가를 싫어요로 간주, 한국 사용자 대표성 주장 |
| MovieLens genres | 최소 콘텐츠 특징, calibration | 유일한 의미 특징으로 사용 |
| MovieLens free tags | 정규화 후 약한 콘텐츠 특징 | 원시 빈도, Tag Genome으로 오인 |
| TMDB details | 제목, 줄거리, 포스터, 러닝타임, 장르 | TMDB 인기·평점을 MovieLens 정답에 섞기 |
| TMDB credits/keywords | 감독·배우·키워드 특징, 추천 이유 | 누락을 0점 또는 부정 신호로 취급 |
| TMDB translations | `ko → en → original` 표시 fallback | 한국어 없음과 제목 없음의 혼동 |
| TMDB watch providers | 현재 한국 가용성 표시와 필터 | 미응답을 ‘시청 불가’로 단정, 구매·대여를 구독으로 표시 |
| FEELM 사용자 이벤트 | 실서비스 fold-in과 온라인 만족도 | MovieLens 사용자 ID와 실사용자 연결 |

TMDB의 `vote_average`는 화면의 외부 참고 평점으로는 쓸 수 있지만, FEELM의 개인 예상 별점이나
MovieLens Test 정답과 이름·필드를 분리한다.

## 7. 서비스 투입 등급

한 개의 `isValid` 대신 목적별 eligibility를 둔다.

| 등급 | 조건 | 쓰임 |
| --- | --- | --- |
| `ALS_TRAINABLE` | MovieLens 평점 1개 이상 | ALS 학습 후보, 저빈도 신뢰도 별도 표시 |
| `WARM_ITEM` | 예: 평점 10개 이상 | 협업 점수 중심 후보. 최종 문턱은 검증으로 결정 |
| `COLD_ITEM` | 평점 1~9개 | 콘텐츠 점수 중심, 협업 점수 shrinkage |
| `CONTENT_ONLY` | 평점 0개, 콘텐츠 특징 있음 | 신작·롱테일 탐험 후보 |
| `UI_READY` | 검증된 영화 + 제목 + 줄거리 fallback + 포스터 + 핵심 특징 | 실제 영화 목록 노출 |
| `KO_LOCALIZED` | 한국어 제목과 줄거리 모두 있음 | 완전 현지화 표시 |
| `OTT_KR_FLATRATE` | KR `flatrate`가 있고 snapshot이 최신 | 한국 구독 OTT 비교 |
| `REVIEW_OR_EXCLUDED` | TV 불일치, not found, 핵심 표시 필드 부족 | 추천 결과에서 제외 또는 수동 검토 |

감사 합집합의 성공 응답 843편 중 824편이 위 `UI_READY` 핵심 필드를 충족했다. 이 수치는
층화 표본의 진단값이며 전체 비율 추정치는 아니다.

`WARM_ITEM >= 10`은 초기 운영 규칙 후보일 뿐 확정 명세가 아니다. 학습·서빙에서 1~9평점 영화를
제거했을 때 NDCG, Coverage, Novelty, cold-item 성능이 어떻게 변하는지 ablation으로 결정한다.

## 8. 권장 수집·정규화 흐름

```text
MovieLens links.csv
  ├─ tmdbId 있음 → TMDB movie details 조회
  │                  └─ 404 → IMDb /find 복구
  └─ tmdbId 없음 → IMDb /find 복구

복구 결과
  ├─ movie + ID/연도/제목 검증 통과 → catalog_movie
  ├─ tv → TYPE_MISMATCH_TV
  ├─ none → TMDB_NOT_FOUND
  └─ 모호함 → IDENTITY_REVIEW_REQUIRED

catalog_movie
  ├─ ko 번역 → 화면 우선
  ├─ en 번역 → fallback
  ├─ original → 최종 fallback
  ├─ credits/keywords/overview → 콘텐츠 Feature와 Embedding
  └─ KR watch providers → 별도 시점 스냅샷
```

최소 저장 필드는 다음과 같다.

```text
movieId, movielensId, imdbId, tmdbId
identityStatus, mediaType, sourceUpdatedAt, fetchedAt
titleKo, titleEn, originalTitle, displayTitle, displayTitleLocale
overviewKo, overviewEn, displayOverview, displayOverviewLocale
posterPath, runtime, genres, directors, cast, keywords
ratingCount, interactionTier
providerRegion, monetizationType, providerId, providerName, providerSnapshotAt
```

OTT는 영화 테이블의 현재 문자열 하나로 덮어쓰지 않고 시점 스냅샷으로 관리한다. `flatrate`,
`rent`, `buy`, `free`, `ads`를 분리하고, 화면의 ‘구독 중인 OTT에서 보기’에는 `flatrate`만 쓴다.

## 9. 추천 설계에 바로 반영할 변경

### 9.1 Hybrid 콘텐츠 축

현재 로컬 32M에는 Tag Genome이 없으므로 초기 Hybrid는 다음으로 구성한다.

```text
MovieLens genre
+ 정규화·기여 상한을 적용한 free tags
+ TMDB genre / director / cast / keywords
+ title·overview text embedding
```

ablation 순서는 `장르만 → TMDB 구조 특징 → 텍스트 임베딩 → 정규화 자유 태그`로 고정한다.
Tag Genome은 별도 데이터 호환성을 증명한 뒤 독립 실험으로만 추가한다.

### 9.2 오프라인 평가 우주를 분리한다

- `WARM_OFFLINE`: Train에 상호작용이 있는 영화. ALS·Top-N NDCG 평가
- `ITEM_COLD_SIMULATION`: 특정 영화의 Train 상호작용을 숨기고 콘텐츠 복구력 평가
- `POST_2023_CATALOG`: MovieLens 정답이 없는 최신 영화. 커버리지·사용자 조사·온라인 이벤트로 평가
- `PARTY_SYNTHETIC`: MovieLens 사용자로 만든 합성 그룹. 실제 파티 만족이라고 표현하지 않음

2023년 이후 TMDB 신작을 추가해 놓고 MovieLens NDCG가 좋아졌다고 주장할 수는 없다. 신작은 별도
cold-item 프로토콜과 실제 FEELM 이벤트가 필요하다.

### 9.3 누락은 점수가 아니라 상태다

한국어 줄거리 없음, OTT 응답 없음, 키워드 없음은 사용자가 그 영화를 싫어한다는 신호가 아니다.
추천 Feature에서는 missing indicator와 fallback을 사용하고, UI에서는 `정보 없음` 또는 원문
fallback으로 처리한다.

## 10. 구현 순서

1. 식별자 수집 상태와 출처·조회 시각을 담는 DB Schema를 확정한다.
2. `tmdbId → IMDb fallback → 타입/연도/제목 검증` 수집 Job을 만든다.
3. `UI_READY`, `KO_LOCALIZED`, `OTT_KR_FLATRATE`, interaction tier를 계산한다.
4. 장르만 쓰는 콘텐츠 Baseline을 만든 뒤 TMDB 구조 특징과 텍스트 임베딩을 차례로 추가한다.
5. 자유 태그 사용자 기여 상한·정규화를 구현하고 ablation으로 채택 여부를 결정한다.
6. OTT 제공처는 별도 갱신 주기와 `providerSnapshotAt`을 두고 실제 영화 목록으로 보여준다.
7. FEELM 자체 평가·조회·추천 클릭·감상 상태를 쌓아 2023년 이후와 한국 사용자 격차를 메운다.

Wikipedia/Wikidata 보강은 1~6이 끝난 뒤에도 반드시 필요한 제품 필드가 남을 때만 한다. 현재 표본은
영어 줄거리와 핵심 표시 필드가 충분하므로 Wiki 수집은 우선순위가 아니다.

## 11. 재현 방법과 산출물

토큰은 Git에 포함되지 않는 `.env.local`의 `TMDB_READ_ACCESS_TOKEN`에서 읽는다.

```powershell
py -3 scripts/movielens_profile.py `
  --archive C:\higher\projects\MM\data\raw\ml-32m.zip `
  --output outputs\tmdb-audit-2026-08-29\movielens_profile.json

py -3 scripts/tmdb_coverage_audit.py `
  --archive C:\higher\projects\MM\data\raw\ml-32m.zip `
  --output outputs\tmdb-audit-2026-08-29 `
  --workers 8

py -3 scripts/tmdb_identity_audit.py `
  --archive C:\higher\projects\MM\data\raw\ml-32m.zip `
  --errors outputs\tmdb-audit-2026-08-29\errors.json `
  --output outputs\tmdb-audit-2026-08-29\identity_repair.json `
  --workers 8
```

생성 산출물은 다음과 같다.

- `movielens_profile.json`: MovieLens 전체 통계
- `summary.json`: TMDB 필드 충족률과 구간별 통계
- `movie_field_audit.csv`: 영화별 정규화 감사 행
- `errors.json`: 404 등 실패 목록
- `identity_repair.json`: IMDb 복구와 TV 불일치 결과

`outputs/`와 `.env.local`은 Git에서 제외된다. 통계와 판단은 이 문서에 남기고 원본 응답과 비밀키는
커밋하지 않는다.

## 12. 라이선스와 표시 의무

- [MovieLens 32M README](https://files.grouplens.org/datasets/movielens/ml-32m-README.html)는
  연구 목적 사용과 출처 표기를 요구하고, 별도 허가 없는 상업·수익 목적 사용을 금지한다.
- [TMDB FAQ](https://developer.themoviedb.org/docs/faq)에 따라 비상업 API 사용도 TMDB 출처 표기,
  승인된 로고와 지정 문구가 필요하다.
- [TMDB Watch Providers](https://developer.themoviedb.org/reference/movie-watch-providers)는
  JustWatch 기반이며 JustWatch 출처 표기가 필수다. 응답은 완전한 딥링크가 아니라 제공 여부와
  TMDB URL 중심이다.

이 프로젝트가 공개 수익 서비스로 바뀌면 MovieLens와 TMDB의 상업 사용 허가를 다시 검토한다.
