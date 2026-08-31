# MovieLens 평가 설계 재판단

> 상태: `DATA_AUDIT_COMPLETE_PROTOCOL_AMENDMENT_PROPOSED`
> 기준일: 2026-08-31
> 범위: 기술통계와 기존 오프라인 증거의 해석 교정. 모델 champion 또는 제품 성능 승인이 아니다.

## 한 줄 결론

`25개 입력 → 90일 뒤 정답 → 후보 500개`를 하나의 고정 프로토콜로 쓰지 않는다.
MovieLens timestamp는 관람 시각이 아니며 같은 날 과거 평점을 몰아 넣은 비율이 너무 높다.
주 평가는 **온보딩 선호 복원**, 보조 평가는 **다음 평가 세션**으로 분리하고 K·N은 Validation 곡선으로 고른다.

## 데이터에서 확인된 사실

### 1. K=25

- 200,948명 중 181,154명(90.15%)이 Rating 25개 이상이다.
- 그러나 MovieLens 사용자는 애초에 최소 20개 Rating을 가진 사람만 수록된 표본이다. 이 90.15%는
  실제 FEELM 사용자가 온보딩 25개를 완료한다는 근거가 아니다.
- 사용자 Rating 수 중앙값은 73개지만 활동 기간 중앙값은 0일이다.
- 사용자별 하루 최대 Rating 수 중앙값은 59개이고, K=25 경계 뒤 같은 UTC 날짜 Rating이 더 있는
  사용자는 96.09%다.

따라서 K=25는 모델 입력량 실험값으로는 쓸 수 있지만 제품 최소 입력 개수로 확정하지 않는다.

### 2. 90일

K=25에서 90일을 완전히 관측할 수 있는 사용자는 180,028명이다.

| 미래 정의 | 미래 Rating 5개 이상 | 개인 기준 positive 1개 이상 |
| --- | ---: | ---: |
| K번째와 같은 timestamp만 제외 | 163,402명, 90.76% | 158,447명, 88.01% |
| K번째와 같은 UTC 날짜 전체 제외 | 60,546명, 33.63% | 59,430명, 33.01% |

같은 날짜를 제외하면 90일 positive 표본이 약 3분의 1로 줄어든다. 기간을 90일에서 365일로 늘려도
positive 1개 이상 비율은 33.01%에서 36.25%로만 증가한다. 이 데이터에서는 “90일이면 충분한가”보다
“평가 입력 세션을 미래 관람으로 볼 수 있는가”가 먼저 해결할 문제다.

### 3. 후보 500개

- 2023년 MovieLens 제목 개봉연도 기준 Pool 근사는 86,968편이고 500편은 0.575%다.
- 기존 REC-EV-011은 50,977개 Train-known 영화 전체를 순위화했다.
- 이 실험은 사용자당 held-out positive가 정확히 1개였으며 K10 Fold-in의
  `candidate_recall@500`은 27.8912%, Popularity는 25.8503%였다.
- 즉 기존 약 25~28%는 “90일 안의 여러 정답 중 일부”가 아니라 **정답 한 편이 Top 500에 든 사용자 비율**이다.

500은 큰 숫자처럼 보여도 5만 편 Pool의 약 1%다. 100%가 되지 않는 것이 산술 오류는 아니지만,
현재 수치만으로 500을 좋은 cutoff라고 승인할 수도 없다. N과 정답 Horizon은 서로 다른 축이다.

### 4. 한국 영화 표본

TMDB `with_origin_country=KR`와 MovieLens TMDB ID를 교차한 proxy 결과다.

| 항목 | 값 |
| --- | ---: |
| 한국-origin proxy 전체 | 11,680편 |
| MovieLens 교차 영화 | 1,056편, 전체 영화의 1.21% |
| 교차 영화 Rating | 90,885개, 전체 Rating의 0.284% |
| 1편 이상 평가 사용자 | 32,848명 |
| 5편 이상 | 4,053명 |
| 10편 이상 | 1,379명 |
| 25편 이상 | 260명 |
| 자기 Rating의 20% 이상이 한국-origin | 32명 |

한국-origin 영화 item slice는 만들 수 있지만 “한국 영화 선호 사용자”를 별도 Train/Validation/Test의
주 집단으로 쓰기에는 작다. MovieLens에는 국가와 연령이 없으므로 한국 20대 성능은 계속 미평가다.

### 5. 한국 시장 인지 가능 외국 영화까지 확장

한국 사용자의 취향 공간을 한국 제작 영화로 한정하면 안 된다. MovieLens Rating 100개 이상인
12,185편을 TMDB details와 전수 교차해 한국어 제목, 한국 개봉 기록, 현재 한국 provider를 확인했다.
한국-origin은 기존 Discover 집합에 `production_countries=KR`인 head 영화를 추가해 보완했다.

| 집합 | 정의 | 영화 | 전체 영화 비율 | Rating | 전체 Rating 비율 | 25편 이상 평가 사용자 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 한국-origin | KR origin/production proxy | 1,078 | 1.23% | 106,455 | 0.33% | 305 |
| 확장 BROAD | 한국-origin + 외국 head 100+ + 한국어 제목 | 12,077 | 13.79% | 30,828,263 | 96.34% | 180,011 |
| 확장 MODERATE | BROAD + 한국 개봉 또는 현재 provider | 8,084 | 9.23% | 26,950,652 | **84.22%** | 172,434 |
| 확장 STRICT | 한국-origin + 외국 head 1,000+ + 한국어 제목 + 한국 극장 개봉/현재 provider | 4,376 | 5.00% | 25,298,738 | **79.06%** | 170,511 |

따라서 `0.284%`는 “한국 제작 영화만의 상호작용 비율”이지 한국 사용자를 위한 MovieLens 활용률이 아니다.
보수적인 STRICT에서도 전체 Rating의 79.06%를 사용할 수 있다. MODERATE를 기본 한국 시장 연관 slice,
BROAD와 STRICT를 민감도 상·하한으로 사용한다.

다만 MovieLens Rating 수는 글로벌 인기도이고 TMDB에는 한국별 인지도 점수가 없다. 이 결과는 실제 한국
20대 인지율이 아니라 `KOREAN_MARKET_RECOGNIZABLE_PROXY`다. 실제 인지도 주장은 KOBIS 흥행 데이터,
한국 검색·관심도 또는 목표 사용자 조사가 추가된 뒤에만 가능하다.

### 6. KOBIS 교차 검증과 고전 영화 예외

KOBIS 연도별 외국 영화 상위 2004~2023과 역대 외국 영화 상위를 합쳐 941편을 얻었고, MovieLens
head/TMDB에 818편을 보수적으로 연결했다. 원개봉일이 2004년 이후이고 국내 관객 100만 명 이상인
337편에서 MODERATE proxy의 영화 recall은 98.52%, STRICT는 86.94%였다. 따라서 MODERATE는
한국 극장 흥행작을 포함하는 평가 slice 후보로 유지할 수 있다.

그러나 KOBIS 부재는 저인지도의 증거가 아니다. `Titanic (1997)`은 2012·2023 재개봉 연도별 목록에
잡히지만 표시 관객은 454,336명뿐이어서 1998년 원개봉 인지도의 크기와 비교할 수 없다. 같은 문제를
피하려고 2004년 이전 외국 영화 중 MovieLens 평점 1,000개 이상, 한국어 제목, 국내 개봉/provider
신호가 있는 1,904편을 `LEGACY_PRE_2004` 검색·설문 후보로 분리했다. 이 후보들의 MovieLens Rating은
17,909,656개(55.97%)이고, 한 편 이상 평가한 사용자는 200,693명이다. 쇼생크 탈출, 포레스트 검프,
매트릭스, 스타워즈, 타이타닉 같은 고전을 KOBIS 관객 cutoff만으로 버리면 오히려 평가가 크게 왜곡된다.

## 제안하는 두 평가 트랙

### Track A — 온보딩 선호 복원, 주 평가

서비스 질문은 “방금 K개 취향을 받은 새 사용자에게 이미 좋아할 법한 영화를 찾는가?”다.

1. 사용자를 먼저 Train/Validation/Locked Test로 분리한다.
2. Train 사용자로 item factor와 콘텐츠 모델을 학습한다.
3. Validation/Test 사용자는 학습에서 제외하고 K개 입력만으로 fold-in한다.
4. 동일 사용자의 나머지 관측 Rating 중 positive 여러 개를 정답 집합으로 둔다.
5. 미평가 영화는 negative가 아니라 unknown으로 유지한다.
6. 이 트랙은 다음 관람 예측이라고 부르지 않고 `PREFERENCE_RECONSTRUCTION`으로 보고한다.

| 축 | Validation 실험값 | 현재 결정 |
| --- | --- | --- |
| K | 5, 10, 25; 50은 부담 상한 진단 | 25 최소 강제 금지 |
| N | 50, 100, 200, 500, 1,000, 2,000 | 곡선 전까지 500 미확정 |
| relevance | 다중 positive, raw 4.0+와 개인 상대효용 민감도 | 한 편 정답 단독 보고 금지 |
| metric | Recall@N, HitRate@N, NDCG@10, coverage, novelty | 사용자 macro와 분모 공개 |

### Track B — 다음 평가 세션, 보조 평가

1. K번째 입력과 같은 UTC 날짜를 모두 제외한다.
2. 이후 서로 다른 평가 날짜가 존재하는 사용자만 포함한다.
3. 7/30/90/180/365일을 모두 보고 하나를 먼저 고정하지 않는다.
4. 같은 사용자의 다음 5/10/20개 Rating 기반 event slice도 함께 보고한다.
5. 이 집단은 K=25 기준 90일 내 미래 Rating이 있는 39.88%의 선택 편향 표본임을 공개한다.
6. timestamp가 관람일이 아니라는 한계 때문에 `NEXT_RATING_SESSION_PROXY`라고 명명한다.

## Pool과 최신 영화

- MovieLens 오프라인 Pool은 각 cutoff 시점에 알려진 영화만 사용하고 사용자가 이미 평가한 영화는 제외한다.
- ALS는 Train 상호작용이 있는 item만 직접 점수화한다. factor가 없는 영화는 명시된 Popularity 또는
  Content fallback으로 동일 Pool에 남긴다.
- 2023-10-13 이후 TMDB 신작은 실제 서비스 후보 Pool에는 포함하되 MovieLens Recall의 정답으로
  만들지 않는다. `POST_2023_CATALOG`는 콘텐츠 cold-item coverage, 사용자 조사, 이후 FEELM 이벤트로 평가한다.
- 협업 학습은 MovieLens 전체를 사용한다. `EXPANDED_MODERATE`는 모델 학습 Pool을 잘라내는 필터가 아니라
  한국 시장 연관 평가 slice와 온보딩 제시 영화 Pool 후보다.
- 추천 후보 Pool은 전체 카탈로그를 유지해 한국에서 덜 알려진 영화의 발견 가능성을 없애지 않는다.
- 한국-origin slice와 `EXPANDED_MODERATE`를 함께 보고, 사용자 cohort는 Train 이력만으로 정의한다.

## 기존 프로토콜에 미치는 영향

`rec-eval-vnext-2`와 REC-EV-019P의 다음 10개 Rating은 실행 가능성 proxy로는 유지할 수 있다.
다만 이를 미래 관람 성능으로 해석하지 않는다. 다음 구현 전에 아래 amendment를 잠근다.

1. `candidate.top_candidates=500` 단일값 앞에 Validation N curve를 추가한다.
2. K25를 diagnostic에 추가하되 K5/K10 결과와 온보딩 비용을 함께 본다.
3. 동일 timestamp뿐 아니라 동일 UTC 날짜 제외 결과를 별도 보조 트랙으로 낸다.
4. 기존 단일 held-out positive와 신규 다중-positive 결과를 섞어 비교하지 않는다.
5. 한국-origin, `EXPANDED_MODERATE`, post-2023 결과를 전체 NDCG와 별도의 coverage/slice 표로 낸다.

## 근거 파일

- [시간·후보 기술통계](./REC-DATA-001-temporal-feasibility.md)
- [한국-origin 교차 감사](./REC-DATA-002-korean-origin-coverage.md)
- [한국 시장 인지 가능 외국 영화 proxy](./REC-DATA-004-korean-market-awareness-proxy.md)
- [KOBIS 외국 영화 흥행 교차 검증](./REC-DATA-005-kobis-boxoffice-validation.md)
- [시간·후보 원본 JSON](./results/movielens-temporal-feasibility-v1.json)
- [한국-origin 원본 JSON](./results/tmdb-korean-origin-movielens-v1.json)
- [한국 시장 확장 원본 JSON](./results/tmdb-korean-market-awareness-proxy-v1.json)
- [KOBIS 교차 검증 원본 JSON](./results/kobis-foreign-boxoffice-validation-v1.json)
- [기존 단일-positive full-catalog 결과](./REC-EV-011-cold-foldin-full-catalog.md)
- [vNext 프로토콜](../protocols/rec-eval-vnext.json)

TMDB 한국-origin 집합은 공식 Discover API의 `with_origin_country`와 `primary_release_date` 필터를
연도별로 호출해 만들었다: <https://developer.themoviedb.org/reference/discover-movie>.
