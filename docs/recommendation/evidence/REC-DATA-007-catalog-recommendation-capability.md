# FEELM 카탈로그 추천 가능성 전수 감사

> 상태: `COMPLETED_FAIL_CLOSED_METADATA_GAP`
> 단위: MovieLens의 고유 TMDB 연결 + TMDB 미연결 MovieLens item + MovieLens에 없는 한국-origin proxy
> 주의: 추천 품질 실험이 아니라, 현재 확보한 데이터로 어떤 추천 신호를 만들 수 있는지 확인한 inventory다.

## 결론

현재 자료만으로는 **ALS를 FEELM 전체 카탈로그의 기본 뼈대로 확정할 수 없다.**
ALS 학습 자격이 있는 카탈로그 행은 84,397개지만,
MovieLens에 연결되지 않은 한국-origin proxy가 10,624개다.
이 항목에는 협업 신호가 없다.

그렇다고 한국 영화 공백을 콘텐츠 유사도로 해결할 수 있다고도 아직 말할 수 없다.
전수 범위에서 한국어·영어 줄거리, 키워드, 감독, 배우를 수집한 artifact가 없어서
rich-content 가능 판정 행은 0개다. 이는 메타데이터가 실제로
없다는 뜻이 아니라 **현재 감사 입력에서 존재 여부를 관측하지 않았다는 뜻**이다.

따라서 이번 조치 A의 결과는 `ALS vs 콘텐츠` 승자 선택이 아니라 다음 구현 Gate다.

1. ALS는 `MovieLens-linked + rating_count > 0` 구간에서만 후보 신호로 허용한다.
2. 한국-origin-only 구간은 TMDB 상세 메타데이터 전수 수집 전까지 content-only 가능으로 간주하지 않는다.
3. 줄거리·키워드·감독·배우 수집 후 이 표를 다시 실행해 `content-only 가능`과 `fallback 필요`를 재분류한다.
4. 추천 품질은 별도 평가 데이터 없이는 주장하지 않는다.

## 감사 모집단

| 항목 | 값 |
| --- | ---: |
| 카탈로그 고유 행 | 98,173 |
| 원본 MovieLens item | 87,585 |
| 고유 TMDB ID | 98,049 |
| MovieLens 연결 카탈로그 행 | 87,549 |
| MovieLens 미연결 한국-origin proxy | 10,624 |
| ALS 학습 자격 행 | 84,397 |
| 합산 MovieLens Rating | 32,000,204 |

`MovieLens item` 여러 개가 같은 TMDB ID를 가리키는 경우 한 카탈로그 행으로 합쳤고,
원래 ID는 CSV의 `movielens_ids`에 보존했다. 한국-origin 모집단은 TMDB
`with_origin_country=KR`, 1870-01-01~2023-10-13 proxy이므로 현재 전체 한국 영화 목록과 동일하지 않다.

## 현재 추천 가능 구간

| 구간 | 영화 | 비율 |
| --- | --- | --- |
| ALS_ELIGIBLE_CONTENT_UNRESOLVED | 6,553 | 6.67% |
| ALS_ELIGIBLE_GENRE_ONLY | 77,843 | 79.29% |
| ALS_ELIGIBLE_NO_CONTENT | 1 | 0.00% |
| CONTENT_GENRE_ONLY_NO_ALS | 2,627 | 2.68% |
| FALLBACK_REQUIRED_CONTENT_UNRESOLVED | 11,149 | 11.36% |

`GENRE_ONLY`는 MovieLens 장르만 관측했다는 뜻이다. 줄거리 embedding이나 감독·배우·키워드 기반
유사도를 구현할 수 있다는 증거가 아니다. ALS factor도 rating 수로 자격만 판정했으며, 실제 factor
artifact를 입력하지 않았다면 `NOT_AUDITED_NO_FACTOR_ARTIFACT`로 유지한다.

## 원산지 구간

| 원산지 판정 | 영화 | 비율 |
| --- | --- | --- |
| FOREIGN_OBSERVED | 11,960 | 12.18% |
| KOREAN_OBSERVED | 22 | 0.02% |
| KOREAN_PROXY | 11,680 | 11.90% |
| UNKNOWN_ORIGIN | 74,511 | 75.90% |

`UNKNOWN_ORIGIN`을 외국 영화로 바꾸지 않았다. TMDB 생산국을 실제로 읽지 않은 항목은 미관측이다.

## MovieLens 상호작용 구간

| Rating 구간 | 영화 | 비율 |
| --- | --- | --- |
| NO_MOVIELENS_LINK | 10,624 | 10.82% |
| R10000_PLUS | 772 | 0.79% |
| R1000_9999 | 3,627 | 3.69% |
| R100_999 | 7,790 | 7.93% |
| R10_99 | 19,756 | 20.12% |
| R1_9 | 52,452 | 53.43% |
| ZERO | 3,152 | 3.21% |

`POPULAR`은 MovieLens Rating 1,000개 이상, `TAIL`은 1~999개로 고정했다. 이는 인지도의
정답이 아니라 ALS 신호량을 보기 위한 분석 구간이다.

## 원산지 × Popular/Tail

| 원산지 | POPULAR_R1000_PLUS | TAIL_R1_999 | ZERO | NO_MOVIELENS_LINK |
| --- | --- | --- | --- | --- |
| FOREIGN | 4,360 | 7,600 | 0 | 0 |
| KOREAN | 20 | 1,050 | 8 | 10,624 |
| UNKNOWN | 19 | 71,348 | 3,144 | 0 |

## 원산지 × New/Old

| 원산지 | OLD_PRE_2015 | NEW_2015_PLUS | UNKNOWN_YEAR |
| --- | --- | --- | --- |
| FOREIGN | 10,170 | 1,760 | 30 |
| KOREAN | 649 | 429 | 10,624 |
| UNKNOWN | 52,360 | 21,564 | 587 |

New/Old 경계는 2015년이다. 한국-origin-only 항목의
상세 release year를 현재 artifact가 보존하지 않아 `UNKNOWN_YEAR`가 크게 나타난다. 따라서 이
구간 역시 영화가 오래됐다는 뜻이 아니라 데이터가 아직 없다는 뜻일 수 있다.

## 콘텐츠 필드 관측 상태

| 필드 | 관측 있음 | 미관측 | 상태 전체 |
| --- | --- | --- | --- |
| genres_presence | 80,470 | 17,702 | {"NOT_OBSERVED": 1, "OBSERVED_MOVIELENS": 80470, "UNKNOWN_NOT_AUDITED": 17702} |
| overview_ko_presence | 0 | 98,049 | {"NOT_APPLICABLE_NO_TMDB": 124, "UNKNOWN_NOT_AUDITED": 98049} |
| overview_en_presence | 0 | 98,049 | {"NOT_APPLICABLE_NO_TMDB": 124, "UNKNOWN_NOT_AUDITED": 98049} |
| keywords_presence | 0 | 98,049 | {"NOT_APPLICABLE_NO_TMDB": 124, "UNKNOWN_NOT_AUDITED": 98049} |
| director_presence | 0 | 98,049 | {"NOT_APPLICABLE_NO_TMDB": 124, "UNKNOWN_NOT_AUDITED": 98049} |
| cast_presence | 0 | 98,049 | {"NOT_APPLICABLE_NO_TMDB": 124, "UNKNOWN_NOT_AUDITED": 98049} |

TMDB ID가 있다는 사실과 TMDB 상세 필드가 확보됐다는 사실을 구분했다. 미수집 값을 `false`로
채우지 않았기 때문에, 이 표는 콘텐츠 추천 성능을 과장하지 않는다.

## 산출물

- 전수 행 CSV: `outputs/recommendation-evidence/catalog-recommendation-capability-v1.csv`
- 요약 JSON: 이 보고서와 함께 생성된 `catalog-recommendation-capability-v1.json`
- 재실행 코드: `scripts/catalog_recommendation_capability_audit.py`

CSV는 생성물 디렉터리(`outputs/`)에 두며 Git 추적 대상이 아니다. 요약 JSON과 이 보고서만
근거 문서로 유지한다.
