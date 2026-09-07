# 고정 hash 추천 사례

상태: DRAFT — 설명용 실제 출력, 성공 사례 선정이 아니다.

점수나 정답을 보기 전에 고정한 `rec-ev-031-case-v1|user_key` hash 순서의 첫3명이다. 아래는 K30의 실제 Top3다. 입력은 같은 사용자의 고정 profile30과0.5점 단위 별점이며, 모델은 숨긴 이력을 보지 않았다. 영화명은 원본 movies.csv 표기다.

## 사례 1: ef18caa5359b

| 정책 | 상위3 | 관측 정답 |
| --- | --- | --- |
| COUNT | Shawshank Redemption, The (1994) → Forrest Gump (1994) → Pulp Fiction (1994) | UNKNOWN, UNKNOWN, UNKNOWN |
| BAYES | Shawshank Redemption, The (1994) → Parasite (2019) → Godfather, The (1972) | UNKNOWN, UNKNOWN, UNKNOWN |
| CONTENT | Thorne: Scaredy Cat (2010) → The Red Maple Leaf (2017) → The Alchemist's Letter (2015) | UNKNOWN, UNKNOWN, UNKNOWN |
| UNION | Knives Out (2019) → Shawshank Redemption, The (1994) → Thorne: Scaredy Cat (2010) | UNKNOWN, UNKNOWN, UNKNOWN |

## 사례 2: 00262c7f7d93

| 정책 | 상위3 | 관측 정답 |
| --- | --- | --- |
| COUNT | Shawshank Redemption, The (1994) → Pulp Fiction (1994) → Matrix, The (1999) | UNKNOWN, UNKNOWN, UNKNOWN |
| BAYES | Shawshank Redemption, The (1994) → Parasite (2019) → Godfather, The (1972) | UNKNOWN, UNKNOWN, UNKNOWN |
| CONTENT | Χούλιγκανς: Κάτω τα χέρια απ' τα νιάτα! (1983) → Loophole (1981) → Йо-хо-хо (1981) | UNKNOWN, UNKNOWN, UNKNOWN |
| UNION | Χούλιγκανς: Κάτω τα χέρια απ' τα νιάτα! (1983) → Shawshank Redemption, The (1994) → Loophole (1981) | UNKNOWN, UNKNOWN, UNKNOWN |

## 사례 3: 0dfc618df6f0

| 정책 | 상위3 | 관측 정답 |
| --- | --- | --- |
| COUNT | Shawshank Redemption, The (1994) → Forrest Gump (1994) → Matrix, The (1999) | UNKNOWN, UNKNOWN, UNKNOWN |
| BAYES | Shawshank Redemption, The (1994) → Parasite (2019) → Usual Suspects, The (1995) | UNKNOWN, UNKNOWN, UNKNOWN |
| CONTENT | Nishant (1975) → King Lear (Korol Lir) (1971) → September Vacation (1979) | UNKNOWN, UNKNOWN, UNKNOWN |
| UNION | Godfather: Part II, The (1974) → One Flew Over the Cuckoo's Nest (1975) → Shawshank Redemption, The (1994) | UNKNOWN, UNKNOWN, UNKNOWN |

COUNT/BAYES 목록은 서로 다른 사용자에게도 많이 겹친다. CONTENT와 UNION은 다른 목록을 만들었다. 다양성과 개인화 여부를 살필 사례이며 unknown을 불호·만족·새로운취향으로 판단하지 않는다. 모든 K와SHUFFLE까지 포함한 원본은 outputs/recommendation-evidence/rec-ev-031/cases.json에 있다.
