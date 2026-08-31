# REC-EV-016 MovieLens 사용자 A 추천 변화 사례

> 상태: `COMPLETED_REPRODUCIBLE_CASE_DIAGNOSTIC` — 사례 설명 자료이며 champion 또는 제품 채택 근거가 아니다.

## 먼저 답

기존 문서에는 전체 평균 지표는 있었지만, 한 사용자의 실제 영화 목록이 모델마다 어떻게 바뀌는지 보여주는 설명이 없었다. 이 문서는 결과를 보고 고른 사용자가 아니라 두 평가 cohort의 교집합에서 고정 해시로 뽑은 `MovieLens 사용자 A`를 대상으로 Popularity, 장르 Content, Hybrid, ALS, 탐험, K10 Fold-in을 같은 조건에서 다시 계산한다.

## 어떤 데이터를 어떻게 나눴나

| 항목 | 값 |
| --- | --- |
| 원본 | MovieLens 32M ratings.csv + movies.csv |
| 전체 | 32,000,204 ratings / 200,948 users / 87,585 movie metadata rows |
| Train | 25,600,163 ratings; 2018-10-03 07:21:40 UTC까지 |
| Validation | 3,200,020 ratings; 모델·가중치 선택 전용 |
| Test | 3,200,021 ratings; 선택이 끝난 뒤 최종 비교 |
| 후보 | Train에서 알려진 50,977편 전체; Train에서 본 영화 제외; 정답 강제 삽입 없음 |
| 사용 | rating, timestamp, movieId, title, genres |
| 미사용 | tags.csv, links.csv, TMDB, 포스터, OTT, 사용자 인구통계 |

전역 시간 분할을 썼기 때문에 미래에 들어온 신규 사용자·신규 영화까지 포함된다. 모델 학습과 사용자 취향 계산은 Train만 사용하고, Validation으로 가중치를 고른 뒤 Test 결과를 읽었다.

## 사용자 A는 어떻게 골랐나

- warm Test 사례와 leakage-safe cold-start 평가에 모두 들어가는 1,011명만 후보로 뒀다.
- `REC-EV-016|CASE-A|intersection|v1|내부 user id` SHA-256이 가장 작은 사용자를 선택했다.
- 추천 결과, 평점, 장르, 성공 여부는 선택에 쓰지 않았다. 원본 ID는 추적 문서에 저장하지 않는다.

## 사용자 A의 취향·평점 성향

| 관측 | 값 | 해석 범위 |
| --- | --- | --- |
| Train 평가 수 | 274 | Train 사용자 중 활동량 86.3% percentile |
| 평균 | 3.881 | 전체 Train 3.532 대비 +0.350 |
| 표준편차 | 0.869 | 점수 사용 폭을 나타내며 성격 진단이 아님 |
| 4점 이상 비율 | 62.4% | 공통 4점 threshold 대신 개인 ECDF를 쓰는 이유 |

장르 취향은 각 평점에서 그 사용자의 Train 평균을 빼고, 영화의 다중 장르 벡터에 더한 뒤 L2 정규화했다. 따라서 단순히 많이 본 장르가 아니라, 본인의 평소 점수보다 높거나 낮게 준 방향이다.

| 방향 | 장르 | centered affinity | 이력 노출 비중 |
| --- | --- | --- | --- |
| 선호 | Sci-Fi | +0.5533 | 8.8% |
| 선호 | Adventure | +0.4339 | 10.1% |
| 선호 | Thriller | +0.3222 | 10.1% |
| 선호 | Action | +0.2364 | 11.7% |
| 선호 | Drama | +0.1771 | 14.0% |
| 비선호 | Horror | -0.3384 | 2.1% |
| 비선호 | Children | -0.2386 | 3.6% |
| 비선호 | Animation | -0.2366 | 3.2% |
| 비선호 | Crime | -0.1903 | 6.4% |
| 비선호 | Comedy | -0.1141 | 12.2% |

Train에서 높게 평가한 예: Toy Story 3 (2010), Finding Nemo (2003), Iron Giant, The (1999), Adjustment Bureau, The (2011), Bill Cosby, Himself (1983)

Train에서 낮게 평가한 예: Halloween (1978), Monty Python's Life of Brian (1979), Dumb and Dumberer: When Harry Met Lloyd (2003), eXistenZ (1999), Crouching Tiger, Hidden Dragon (Wo hu cang long) (2000)

이것은 MovieLens 행동의 기술적 요약이다. 사람의 성격·정체성·실제 FEELM 만족도를 추론한 것이 아니다.

## 같은 사용자, 같은 Test 정답, 알고리즘만 변경

자연 발생 held-out 정답은 **Misérables, Les (2012)**, 실제 평점 4.5, Train 평점 습관으로 환산한 상대 효용 0.742이다. 정답은 후보에 강제로 넣지 않았다.

| 정책 | 정답 전체 순위 | Top-10 | NDCG@10 | Popularity와 겹침 | 결론 |
| --- | --- | --- | --- | --- | --- |
| POPULARITY | 19937 | miss | 0.000000 | 10/10 | reference |
| CONTENT_GENRE | 27300 | miss | 0.000000 | 0/10 | case diagnostic only |
| HYBRID_CONTENT_25 | 20731 | miss | 0.000000 | 1/10 | case diagnostic only |
| ALS_WARM | 12831 | miss | 0.000000 | 0/10 | case diagnostic only |
| EXPLORE_05_ON_POPULARITY | greedy Top-500만 정의 | miss | 0.000000 | 0/10 | case diagnostic only |

### POPULARITY

| 순위 | 영화 | 장르 | 점수 |
| --- | --- | --- | --- |
| 1 | Planet Earth II (2016) | Documentary | 1.0 |
| 2 | Planet Earth (2006) | Documentary | 0.99998 |
| 3 | Band of Brothers (2001) | Action, Drama, War | 0.999941 |
| 4 | Godfather: Part II, The (1974) | Crime, Drama | 0.999882 |
| 5 | Seven Samurai (Shichinin no samurai) (1954) | Action, Adventure, Drama | 0.999843 |
| 6 | Rear Window (1954) | Mystery, Thriller | 0.999823 |
| 7 | 12 Angry Men (1957) | Drama | 0.999804 |
| 8 | One Flew Over the Cuckoo's Nest (1975) | Drama | 0.999765 |
| 9 | Casablanca (1942) | Drama, Romance | 0.999725 |
| 10 | North by Northwest (1959) | Action, Adventure, Mystery, Romance, Thriller | 0.999686 |

- 새로 들어온 영화: 없음
- 빠진 영화: 없음

### CONTENT_GENRE

| 순위 | 영화 | 장르 | 점수 |
| --- | --- | --- | --- |
| 1 | Star Trek II: The Wrath of Khan (1982) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |
| 2 | Lost World: Jurassic Park, The (1997) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |
| 3 | Spawn (1997) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |
| 4 | Road Warrior, The (Mad Max 2) (1981) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |
| 5 | Jurassic Park III (2001) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |
| 6 | Clockstoppers (2002) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |
| 7 | Spider-Man (2002) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |
| 8 | You Only Live Twice (1967) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |
| 9 | I, Robot (2004) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |
| 10 | War of the Worlds (2005) | Action, Adventure, Sci-Fi, Thriller | 0.999637 |

- 새로 들어온 영화: Star Trek II: The Wrath of Khan (1982), Lost World: Jurassic Park, The (1997), Spawn (1997), Road Warrior, The (Mad Max 2) (1981), Jurassic Park III (2001), Clockstoppers (2002), Spider-Man (2002), You Only Live Twice (1967), I, Robot (2004), War of the Worlds (2005)
- 빠진 영화: Planet Earth II (2016), Planet Earth (2006), Band of Brothers (2001), Godfather: Part II, The (1974), Seven Samurai (Shichinin no samurai) (1954), Rear Window (1954), 12 Angry Men (1957), One Flew Over the Cuckoo's Nest (1975), Casablanca (1942), North by Northwest (1959)

### HYBRID_CONTENT_25

| 순위 | 영화 | 장르 | 점수 |
| --- | --- | --- | --- |
| 1 | Blade Runner (1982) | Action, Sci-Fi, Thriller | 0.997281 |
| 2 | The Martian (2015) | Adventure, Drama, Sci-Fi | 0.995804 |
| 3 | Serenity (2005) | Action, Adventure, Sci-Fi | 0.994289 |
| 4 | Prestige, The (2006) | Drama, Mystery, Sci-Fi, Thriller | 0.993615 |
| 5 | 2001: A Space Odyssey (1968) | Adventure, Drama, Sci-Fi | 0.993406 |
| 6 | Avengers: Infinity War - Part I (2018) | Action, Adventure, Sci-Fi | 0.992332 |
| 7 | Wages of Fear, The (Salaire de la peur, Le) (1953) | Action, Adventure, Drama, Thriller | 0.992266 |
| 8 | Seven Samurai (Shichinin no samurai) (1954) | Action, Adventure, Drama | 0.991886 |
| 9 | Guardians of the Galaxy (2014) | Action, Adventure, Sci-Fi | 0.9917 |
| 10 | Ex Machina (2015) | Drama, Sci-Fi, Thriller | 0.991077 |

- 새로 들어온 영화: Blade Runner (1982), The Martian (2015), Serenity (2005), Prestige, The (2006), 2001: A Space Odyssey (1968), Avengers: Infinity War - Part I (2018), Wages of Fear, The (Salaire de la peur, Le) (1953), Guardians of the Galaxy (2014), Ex Machina (2015)
- 빠진 영화: Planet Earth II (2016), Planet Earth (2006), Band of Brothers (2001), Godfather: Part II, The (1974), Rear Window (1954), 12 Angry Men (1957), One Flew Over the Cuckoo's Nest (1975), Casablanca (1942), North by Northwest (1959)

### ALS_WARM

| 순위 | 영화 | 장르 | 점수 |
| --- | --- | --- | --- |
| 1 | Acı Aşk (2009) | Drama | 6.349079 |
| 2 | Smoke 'Em If You Got 'Em (1988) | Comedy | 6.085207 |
| 3 | Uninvited (1988) | Horror, Sci-Fi | 6.085207 |
| 4 | Macho Madness - The Randy Savage Ultimate Collection (2009) | 미상 | 5.949165 |
| 5 | The Thorn (1971) | Comedy | 5.557581 |
| 6 | 1968 (2018) | 미상 | 5.540464 |
| 7 | Loot (1970) | Comedy, Crime | 5.509454 |
| 8 | Heroes Shed No Tears (1986) | Action | 5.467054 |
| 9 | Don't Laugh at My Romance (2008) | Comedy, Drama | 5.448565 |
| 10 | NOFX Backstage Passport 2 | 미상 | 5.41178 |

- 새로 들어온 영화: Acı Aşk (2009), Smoke 'Em If You Got 'Em (1988), Uninvited (1988), Macho Madness - The Randy Savage Ultimate Collection (2009), The Thorn (1971), 1968 (2018), Loot (1970), Heroes Shed No Tears (1986), Don't Laugh at My Romance (2008), NOFX Backstage Passport 2
- 빠진 영화: Planet Earth II (2016), Planet Earth (2006), Band of Brothers (2001), Godfather: Part II, The (1974), Seven Samurai (Shichinin no samurai) (1954), Rear Window (1954), 12 Angry Men (1957), One Flew Over the Cuckoo's Nest (1975), Casablanca (1942), North by Northwest (1959)

### EXPLORE_05_ON_POPULARITY

| 순위 | 영화 | 장르 | 점수 |
| --- | --- | --- | --- |
| 1 | Cosmos | 미상 | - |
| 2 | Blue Planet II (2017) | Documentary | - |
| 3 | Over the Garden Wall (2013) | Adventure, Animation, Drama | - |
| 4 | Can't Change the Meeting Place (1979) | Action, Crime | - |
| 5 | Notorious (1946) | Film-Noir, Romance, Thriller | - |
| 6 | General, The (1926) | Comedy, War | - |
| 7 | Interstellar (2014) | Sci-Fi, IMAX | - |
| 8 | Maltese Falcon, The (a.k.a. Dangerous Female) (1931) | Mystery | - |
| 9 | Shining, The (1980) | Horror | - |
| 10 | Little Big Man (1970) | Western | - |

- 새로 들어온 영화: Cosmos, Blue Planet II (2017), Over the Garden Wall (2013), Can't Change the Meeting Place (1979), Notorious (1946), General, The (1926), Interstellar (2014), Maltese Falcon, The (a.k.a. Dangerous Female) (1931), Shining, The (1980), Little Big Man (1970)
- 빠진 영화: Planet Earth II (2016), Planet Earth (2006), Band of Brothers (2001), Godfather: Part II, The (1974), Seven Samurai (Shichinin no samurai) (1954), Rear Window (1954), 12 Angry Men (1957), One Flew Over the Cuckoo's Nest (1975), Casablanca (1942), North by Northwest (1959)

## Cold-start: 첫 10편만 알려줬을 때

이 부분은 같은 사용자 A를 ALS 학습에서 통째로 제외한 별도 cohort-excluded 모델이다. 최초 10개 평점만으로 user factor를 Fold-in하고, Validation에서 고정한 `0.8 × Popularity + 0.2 × Fold-in`을 사용했다.

| 순서 | 영화 | 평점 | 장르 |
| --- | --- | --- | --- |
| 1 | Fatal Attraction (1987) | 4.0 | Drama, Thriller |
| 2 | Glory (1989) | 5.0 | Drama, War |
| 3 | X-Files: Fight the Future, The (1998) | 4.0 | Action, Crime, Mystery, Sci-Fi, Thriller |
| 4 | From Dusk Till Dawn (1996) | 4.0 | Action, Comedy, Horror, Thriller |
| 5 | James and the Giant Peach (1996) | 3.0 | Adventure, Animation, Children, Fantasy, Musical |
| 6 | Star Trek VI: The Undiscovered Country (1991) | 4.5 | Action, Mystery, Sci-Fi |
| 7 | Kingpin (1996) | 4.5 | Comedy |
| 8 | Basic Instinct (1992) | 4.0 | Crime, Mystery, Thriller |
| 9 | League of Their Own, A (1992) | 3.5 | Comedy, Drama |
| 10 | Tron (1982) | 4.5 | Action, Adventure, Sci-Fi |

cold-start held-out 정답: **11.22.63 (2016)**

| 정책 | 정답 순위 | Top-10 | NDCG@10 | 상대 목록 변화 |
| --- | --- | --- | --- | --- |
| POPULARITY | 3979 | miss | 0.000000 | Popularity 대비 10/10 겹침 |
| FOLDIN_BLEND_ALPHA_0_2 | 5363 | miss | 0.000000 | Popularity 대비 8/10 겹침 |

### K10 POPULARITY

| 순위 | 영화 | 장르 | 점수 |
| --- | --- | --- | --- |
| 1 | Planet Earth II (2016) | Documentary | 4.43609 |
| 2 | Planet Earth (2006) | Documentary | 4.435363 |
| 3 | Shawshank Redemption, The (1994) | Crime, Drama | 4.41622 |
| 4 | Band of Brothers (2001) | Action, Drama, War | 4.36299 |
| 5 | Godfather, The (1972) | Crime, Drama | 4.328678 |
| 6 | Usual Suspects, The (1995) | Crime, Mystery, Thriller | 4.291583 |
| 7 | Godfather: Part II, The (1974) | Crime, Drama | 4.260422 |
| 8 | Schindler's List (1993) | Drama, War | 4.254128 |
| 9 | Seven Samurai (Shichinin no samurai) (1954) | Action, Adventure, Drama | 4.252545 |
| 10 | Rear Window (1954) | Mystery, Thriller | 4.235692 |

### K10 FOLDIN_BLEND_ALPHA_0_2

| 순위 | 영화 | 장르 | 점수 |
| --- | --- | --- | --- |
| 1 | Planet Earth II (2016) | Documentary | 4.541782 |
| 2 | Planet Earth (2006) | Documentary | 4.541669 |
| 3 | Shawshank Redemption, The (1994) | Crime, Drama | 4.516806 |
| 4 | Band of Brothers (2001) | Action, Drama, War | 4.498248 |
| 5 | Godfather, The (1972) | Crime, Drama | 4.442637 |
| 6 | Usual Suspects, The (1995) | Crime, Mystery, Thriller | 4.39864 |
| 7 | Godfather: Part II, The (1974) | Crime, Drama | 4.371579 |
| 8 | Cosmos | 미상 | 4.366136 |
| 9 | Seven Samurai (Shichinin no samurai) (1954) | Action, Adventure, Drama | 4.339152 |
| 10 | Star Wars: Episode V - The Empire Strikes Back (1980) | Action, Adventure, Sci-Fi | 4.327679 |

## 이 한 사람에서 실제로 드러난 변화

- 장르 Content는 Popularity와 0/10만 겹쳤고 목록 내 장르 다양성이 0.000이었다. 선호 장르 조합만 정확히 반복하는 과특화가 실제 제목 목록에서 확인된다.
- Hybrid 25%는 Popularity와 1/10만 겹치며 Sci-Fi 중심으로 바뀌었다. 사용자 취향은 더 잘 보이지만 이 held-out 정답 순위와 전체 cohort NDCG는 개선하지 못했다.
- raw ALS는 Popularity와 0/10 겹쳤고, 평균 novelty가 23.554 bits였다. 희귀·메타데이터 미상 영화의 내적 점수가 5점 범위를 넘어 상단을 점유하므로 보정되지 않은 ALS dot product를 그대로 추천 순위에 쓰면 안 된다.
- Explore 5%도 Popularity와 0/10만 겹쳤다. 가중치는 작아도 greedy marginal diversity가 매 단계 작동해 상위 목록을 전부 교체할 수 있다.
- K10 Fold-in은 10편 중 2편을 바꿨지만 이 사용자의 cold held-out 순위는 3,979위에서 5,363위로 악화됐다. 전체 1,323명 평균의 작은 양의 효과가 모든 개인의 개선을 뜻하지 않는다.

## 무엇을 채용했고 무엇을 버렸나

| 후보 | 전체 사용자 근거 | 현재 판단 |
| --- | --- | --- |
| Popularity | REC-EV-004B Test NDCG@10 0.009382 | 로컬 fallback 유지 |
| 장르 Content | 0.000955; coverage는 넓지만 relevance 급락 | 단독 ranking 기각 |
| Hybrid 25% | 0.007435; Popularity보다 낮음 | 고정 weight 기각 |
| Explore 5% | 0.005113; paired CI가 명확히 음수 | 2+1 및 weight 기각 |
| Warm ALS | REC-EV-002 sampled NDCG가 Popularity보다 낮고 전체 coverage 11.74% | champion 기각; 진단만 유지 |
| K10 Fold-in 20% | REC-EV-011 NDCG 0.004723→0.006154; paired CI [0.000253, 0.002783] | offline candidate만 유지 |

사용자 A 한 명의 성공·실패 때문에 채택한 항목은 없다. 사례는 알고리즘이 무엇을 바꾸는지 설명하고 버그를 찾는 자료이며, 채택 판단은 잠긴 전체 cohort 결과로 한다. 현재 개인 ranking champion은 여전히 `null`이다.

## 모델별 실제 계산

- Popularity: Train 평점 평균을 50개 prior로 Bayesian shrinkage.
- Content: 사용자 Train 평균을 뺀 장르 선호 벡터와 영화 장르 cosine.
- Hybrid: 전체 후보 내 percentile로 정규화한 뒤 `0.75 Popularity + 0.25 Content`.
- ALS warm: explicit ALS rank 32, regParam 0.1, 10 iterations, seed 42의 user/item factor 내적.
- Explore: Popularity Top-500 안에서 `0.95 popularity + 0.05 × (novelty/2 + marginal genre diversity/2)` greedy 재정렬.
- K10 Fold-in: 평가 사용자를 ALS 학습에서 제외하고 최초 10개 평점으로 user factor만 풀어 `0.8 popularity + 0.2 fold-in`.

## 한계

- MovieLens의 미평가는 싫어요가 아니며 held-out 한 편도 사용자의 전체 만족도를 대표하지 않는다.
- 장르만 사용한 Content는 감독·배우·키워드·줄거리 임베딩이 없다.
- 전역 시간 분할의 사용자 유입 구조가 FEELM 가입자와 같다는 보장은 없다.
- 사용자 A는 설명용 단일 사례다. 일반화는 REC-EV-004B/011 aggregate와 paired CI만 담당한다.

## 재현

```powershell
py -3.12 scripts/recommendation_user_case_study.py
```

스크립트는 모든 입력 artifact checksum을 확인하고 결과 JSON, manifest, 이 문서를 같은 고정 규칙으로 다시 만든다.
