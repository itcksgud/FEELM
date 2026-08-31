# REC-EV-017 영화·장르 공동 선호와 자유 태그 ablation

> 상태: `COMPLETED_MOVIELENS_RELATIONAL_EVIDENCE_TMDB_BLOCKED` — MovieLens 관계·태그 근거이며 제품 champion은 아니다.

## 결론

REC-EV-016의 장르-only 설명을 그대로 확장하지 않고 세 가지 질문을 분리했다.

1. 개인 평균보다 A를 높게 평가한 사람들이 B도 높게 평가하는가?
2. A 장르를 상대적으로 선호한 사용자에게 어떤 B 장르 선호 lift가 있는가?
3. 장르 외 자유 태그의 주제·분위기 정보가 full-catalog held-out ranking을 개선하는가?

Validation이 선택한 Popularity↔Tag 가중치는 `0.1`였다. Test NDCG@10은 Popularity `0.009382`, 선택 Hybrid `0.016769`, Tag-only `0.012641`였다. 이 결과는 아래 aggregate 표와 사용자 A의 실제 제목으로 함께 읽는다.

## 데이터 경계

| 축 | 입력 | 누수 방지0 | 해석 |
| --- | --- | --- | --- |
| 영화→영화 | Train ratings | 사용자별 Train 평균만 사용 | 조건부 공동 선호; 인과·감상 순서 아님 |
| 장르→장르 | Train ratings + genres | Train만 사용, 두 장르 모두 3편 이상 노출 | 조건부 선호 lift |
| 태그 | Train boundary 이전 tags.csv | 평가 cohort 기여 제외 + 사용자당 500개 상한 | 분위기·주제 TF-IDF; Tag Genome 아님 |
| TMDB | 전수 artifact 없음 | 실행하지 않음 | 120편 preview/843편 편향 감사 표본을 성능 실험에 사용하지 않음 |

미평가를 싫어요로 바꾸지 않았다. 영화 관계의 조건부 비율은 A를 선호한 사람 중 B도 실제로 평가한 사람만 분모에 들어가며, 따라서 노출 편향이 남는다.

## 사용자 A의 영화 anchor → 연관 영화

Anchor는 사용자 A가 본인 Train 평균보다 최소 0.5 높게 평가했고 평가 수가 100개 이상인 영화 중, residual·평점·support 순으로 결과를 보기 전에 3편을 골랐다. lift는 사용자 평점 성향을 보정하고 support 50·prior 100으로 shrink했다.

### Forrest Gump (1994) — 사용자 A 5.0, 평균 대비 +1.119

전체 평가자 85,040명 중 개인 평균보다 높게 평가한 사람 61,045명.

| B 영화 | 공동 평가 support | A 선호 집단 B 선호 | B 전체 기준 | shrunken lift | score |
| --- | --- | --- | --- | --- | --- |
| Cocoon (1985) | 5642 | 46.4% | 40.1% | 1.154 | 0.2025 |
| Hanging Up (2000) | 494 | 14.0% | 11.5% | 1.177 | 0.1955 |
| Jakob the Liar (1999) | 500 | 35.2% | 29.0% | 1.176 | 0.1954 |
| Grand Canyon (1991) | 1317 | 55.0% | 47.4% | 1.148 | 0.1849 |
| About Last Night... (1986) | 1148 | 37.0% | 32.0% | 1.145 | 0.1803 |

### Shawshank Redemption, The (1994) — 사용자 A 5.0, 평균 대비 +1.119

전체 평가자 84,098명 중 개인 평균보다 높게 평가한 사람 72,199명.

| B 영화 | 공동 평가 support | A 선호 집단 B 선호 | B 전체 기준 | shrunken lift | score |
| --- | --- | --- | --- | --- | --- |
| Beautiful Girls (1996) | 2266 | 58.0% | 52.6% | 1.098 | 0.1288 |
| Grand Canyon (1991) | 1836 | 52.1% | 47.4% | 1.094 | 0.1228 |
| On Golden Pond (1981) | 2973 | 63.3% | 58.5% | 1.079 | 0.1068 |
| Nixon (1995) | 2876 | 51.4% | 47.5% | 1.077 | 0.1039 |
| North Dallas Forty (1979) | 422 | 43.8% | 39.3% | 1.092 | 0.1030 |

### Matrix, The (1999) — 사용자 A 5.0, 평균 대비 +1.119

전체 평가자 74,304명 중 개인 평균보다 높게 평가한 사람 56,102명.

| B 영화 | 공동 평가 support | A 선호 집단 B 선호 | B 전체 기준 | shrunken lift | score |
| --- | --- | --- | --- | --- | --- |
| Interview with the Vampire: The Vampire Chronicles (1994) | 11439 | 62.3% | 50.3% | 1.237 | 0.3044 |
| Beautiful Girls (1996) | 1222 | 65.0% | 52.6% | 1.217 | 0.2619 |
| Dumb & Dumber (Dumb and Dumber) (1994) | 14086 | 37.7% | 31.5% | 1.196 | 0.2563 |
| Stargate (1994) | 13659 | 49.3% | 41.2% | 1.194 | 0.2542 |
| Natural Born Killers (1994) | 9638 | 53.1% | 44.6% | 1.187 | 0.2450 |

## 사용자 A anchor를 합친 추천

| 순위 | 영화 | 점수 | 가장 큰 anchor 근거 |
| --- | --- | --- | --- |
| 1 | Beautiful Girls (1996) | 0.5797 | Matrix, The (1999) (lift 1.22), Shawshank Redemption, The (1994) (lift 1.10) |
| 2 | Interview with the Vampire: The Vampire Chronicles (1994) | 0.4670 | Matrix, The (1999) (lift 1.24), Forrest Gump (1994) (lift 1.07) |
| 3 | Grand Canyon (1991) | 0.4440 | Forrest Gump (1994) (lift 1.15), Shawshank Redemption, The (1994) (lift 1.09) |
| 4 | Primal Fear (1996) | 0.4318 | Matrix, The (1999) (lift 1.13), Forrest Gump (1994) (lift 1.09) |
| 5 | Dumb & Dumber (Dumb and Dumber) (1994) | 0.4148 | Matrix, The (1999) (lift 1.20), Forrest Gump (1994) (lift 1.07) |
| 6 | Nixon (1995) | 0.4063 | Matrix, The (1999) (lift 1.12), Forrest Gump (1994) (lift 1.08) |
| 7 | Four Rooms (1995) | 0.3940 | Matrix, The (1999) (lift 1.14), Forrest Gump (1994) (lift 1.10) |
| 8 | Field of Dreams (1989) | 0.3903 | Forrest Gump (1994) (lift 1.13), Shawshank Redemption, The (1994) (lift 1.07) |
| 9 | Jakob the Liar (1999) | 0.3865 | Forrest Gump (1994) (lift 1.18), Matrix, The (1999) (lift 1.08) |
| 10 | Mumford (1999) | 0.3847 | Matrix, The (1999) (lift 1.11), Forrest Gump (1994) (lift 1.10) |

자연 발생 held-out `Misérables, Les (2012)`의 관계 후보 순위: `7695`. 관계 점수가 있는 Train-known 후보 coverage는 15.3%다.

## 사용자 A의 선호 장르 → 다른 장르

장르 관계는 두 장르를 각각 최소 3편 평가한 사용자만 비교한다. `P(B 선호 | A 선호, A·B 노출) / P(B 선호 | A·B 노출)`이며 A가 B를 유발한다는 뜻이 아니다.

### Sci-Fi 선호 집단

| B 장르 | support | 조건부 선호율 | 기준 선호율 | shrunken lift |
| --- | --- | --- | --- | --- |
| Action | 56881 | 57.1% | 36.1% | 1.583 |
| Adventure | 56435 | 59.7% | 42.8% | 1.395 |
| IMAX | 27405 | 64.9% | 54.1% | 1.198 |
| Horror | 34388 | 39.8% | 34.0% | 1.169 |
| Thriller | 56395 | 55.5% | 48.2% | 1.153 |

### Adventure 선호 집단

| B 장르 | support | 조건부 선호율 | 기준 선호율 | shrunken lift |
| --- | --- | --- | --- | --- |
| Action | 68646 | 55.7% | 35.9% | 1.549 |
| Sci-Fi | 64511 | 52.2% | 37.4% | 1.395 |
| Fantasy | 59886 | 61.8% | 47.1% | 1.313 |
| IMAX | 31598 | 69.6% | 54.3% | 1.280 |
| Children | 52194 | 48.6% | 40.1% | 1.213 |

### Thriller 선호 집단

| B 장르 | support | 조건부 선호율 | 기준 선호율 | shrunken lift |
| --- | --- | --- | --- | --- |
| Action | 77957 | 45.6% | 35.8% | 1.274 |
| Horror | 45971 | 42.9% | 34.1% | 1.258 |
| Mystery | 59008 | 78.1% | 66.7% | 1.170 |
| Sci-Fi | 72702 | 43.1% | 37.4% | 1.153 |
| Crime | 75280 | 79.0% | 71.1% | 1.111 |

## 장르 밖의 자유 태그 취향

Train boundary 이전 2,000,072개 원본 태그 중 정규화·중복 제거·사용자 상한 뒤 219,866개를 사용했다. vocabulary는 2,977개이고 Train-known 영화 9,857/50,977편에 벡터가 있다.

| 방향 | 태그 | weight |
| --- | --- | --- |
| 선호 | time travel | +0.2058 |
| 선호 | star trek | +0.1929 |
| 선호 | sci fi | +0.1707 |
| 선호 | space | +0.1611 |
| 선호 | science fiction | +0.1253 |
| 선호 | arnold | +0.1249 |
| 선호 | james bond | +0.1223 |
| 선호 | tv | +0.1199 |
| 선호 | 007 | +0.1155 |
| 선호 | future | +0.1120 |
| 비선호 | human nature | -0.1194 |
| 비선호 | serial killer | -0.1149 |
| 비선호 | animation | -0.1060 |
| 비선호 | steampunk | -0.1007 |
| 비선호 | slasher | -0.0936 |
| 비선호 | john carpenter | -0.0932 |
| 비선호 | halloween | -0.0925 |
| 비선호 | hilarious | -0.0839 |
| 비선호 | disney | -0.0781 |
| 비선호 | cinematography | -0.0762 |

## Tag Hybrid aggregate

Validation에서만 alpha를 선택하고 Test에서는 잠긴 alpha와 Tag-only 진단을 읽었다. 후보는 Train-known 50,977편 전체이며 Train-seen 제외, positive 비주입이다.

| phase | alpha | NDCG@10 | Recall@10 | Candidate Recall@500 | Catalog coverage |
| --- | --- | --- | --- | --- | --- |
| Validation | 0.0 | 0.010187 | 0.021761 | 0.300000 | 0.001903 |
| Validation | 0.1 | 0.020882 | 0.037874 | 0.317940 | 0.008337 |
| Validation | 0.25 | 0.020668 | 0.039369 | 0.342691 | 0.016831 |
| Validation | 0.5 | 0.018395 | 0.036877 | 0.364452 | 0.028601 |
| Validation | 0.75 | 0.016390 | 0.033555 | 0.359967 | 0.038625 |
| Validation | 1.0 | 0.011393 | 0.023090 | 0.284718 | 0.071385 |
| Test | 0.0 | 0.009382 | 0.022500 | 0.308000 | 0.001726 |
| Test | 0.1 | 0.016769 | 0.033250 | 0.327500 | 0.007533 |
| Test | 1.0 | 0.012641 | 0.024750 | 0.288750 | 0.058870 |

Test의 tag profile coverage는 99.3%, held-out 영화 tag coverage는 83.7%다. coverage가 없는 것은 싫어요나 0점이 아니다.
선택 Hybrid의 Test paired NDCG 차이는 `+0.007386`이며 95% bootstrap CI는 `[+0.003948, +0.010696]`다.

선택 Hybrid의 Test 구간별 차이는 다음과 같다. 전체 개선이 어느 구간에서 발생했는지를 숨기지 않는다.

| 구간 축 | 구간 | 사용자 | NDCG@10 | Popularity 대비 차이 | 차이 95% CI | Recall@10 |
| --- | --- | --- | --- | --- | --- | --- |
| history_segment | K20_49 | 343 | 0.022379 | +0.012280 | [-0.000853, +0.026822] | 0.040816 |
| history_segment | K50_99 | 312 | 0.020741 | +0.010906 | [-0.001010, +0.023862] | 0.044872 |
| history_segment | K100_PLUS | 3345 | 0.015823 | +0.006556 | [+0.003335, +0.010168] | 0.031390 |
| positive_segment | P1_LONG_TAIL | 1008 | 0.000000 | +0.000000 | [+0.000000, +0.000000] | 0.000000 |
| positive_segment | P2 | 995 | 0.001466 | -0.011484 | [-0.017592, -0.006182] | 0.004020 |
| positive_segment | P3 | 1002 | 0.010061 | +0.007461 | [+0.003298, +0.011979] | 0.022954 |
| positive_segment | P4_HEAD | 995 | 0.055814 | +0.033664 | [+0.023042, +0.044659] | 0.106533 |

## 사용자 A의 Tag 정책 실제 목록

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

held-out 전체 순위: `19937`, Popularity와 Top-10 overlap `10/10`.

### TAG_CONTENT

| 순위 | 영화 | 장르 | 점수 |
| --- | --- | --- | --- |
| 1 | Star Trek II: The Wrath of Khan (1982) | Action, Adventure, Sci-Fi, Thriller | 0.665959 |
| 2 | Star Trek Beyond (2016) | Action, Adventure, Sci-Fi | 0.638529 |
| 3 | ARQ (2016) | Sci-Fi, Thriller | 0.633124 |
| 4 | Terminator 3: Rise of the Machines (2003) | Action, Adventure, Sci-Fi | 0.630133 |
| 5 | Looper (2012) | Action, Crime, Sci-Fi | 0.62211 |
| 6 | Star Trek III: The Search for Spock (1984) | Action, Adventure, Sci-Fi | 0.620756 |
| 7 | Star Trek Into Darkness (2013) | Action, Adventure, Sci-Fi, IMAX | 0.619217 |
| 8 | Stargate (1994) | Action, Adventure, Sci-Fi | 0.619026 |
| 9 | Star Wars: Episode II - Attack of the Clones (2002) | Action, Adventure, Sci-Fi, IMAX | 0.617094 |
| 10 | Interstellar (2014) | Sci-Fi, IMAX | 0.613072 |

held-out 전체 순위: `4362`, Popularity와 Top-10 overlap `0/10`.

### HYBRID_TAG_ALPHA_0_1

| 순위 | 영화 | 장르 | 점수 |
| --- | --- | --- | --- |
| 1 | Interstellar (2014) | Sci-Fi, IMAX | 0.9591 |
| 2 | Blade Runner (1982) | Action, Sci-Fi, Thriller | 0.956396 |
| 3 | Ivan Vasilievich: Back to the Future (Ivan Vasilievich menyaet professiyu) (1973) | Adventure, Comedy | 0.954311 |
| 4 | North by Northwest (1959) | Action, Adventure, Mystery, Romance, Thriller | 0.954063 |
| 5 | To Kill a Mockingbird (1962) | Drama | 0.953083 |
| 6 | Casablanca (1942) | Drama, Romance | 0.953053 |
| 7 | Rear Window (1954) | Mystery, Thriller | 0.952903 |
| 8 | The Martian (2015) | Adventure, Drama, Sci-Fi | 0.952896 |
| 9 | Departed, The (2006) | Crime, Drama, Thriller | 0.952786 |
| 10 | Arrival (2016) | Sci-Fi | 0.952777 |

held-out 전체 순위: `19924`, Popularity와 Top-10 overlap `3/10`.

## 판단

- Validation 선택 alpha는 `0.1`다. Test paired CI까지 양수면 다음 offline 후보가 되지만 제품 채택은 아니다.
- 실제 Test에서는 P2 인기도 구간이 회귀하고 P1 롱테일이 개선되지 않았다. 전체 평균 양수만으로 일반 ranking 후보를 열지 않는다.
- 영화·장르 관계는 추천 근거 후보와 실패 분석에는 유용하지만, 단일 사용자 사례로 champion을 선택하지 않는다.
- 자유 태그는 장르보다 풍부하지만 기여자 편향과 낮은 coverage가 있어 단독 제품 특징으로 채택하지 않는다.
- TMDB 감독·배우·키워드·줄거리 embedding ablation은 전수 Train-known feature artifact가 생긴 뒤 같은 protocol의 새 evidence로 실행한다.
- 개인 ranking champion은 계속 `null`, fallback은 Popularity다.

## 재현

```powershell
$env:PYTHONPATH='scripts'
py -3.12 scripts/recommendation_relational_ablation.py
Remove-Item Env:PYTHONPATH
```
