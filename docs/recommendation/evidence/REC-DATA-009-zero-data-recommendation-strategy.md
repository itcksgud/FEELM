# FEELM 목표 도메인 데이터 부재 대응 전략

> 상태: `DRAFT_DECISION_PROPOSAL` — 팀 합의 전이며 구현 계약이 아니다.
> 기준일: 2026-08-31
> 질문: (1) MovieLens의 외국·유명 영화 편향을 어떻게 다룰 것인가? (2) 목표 사용자 정답이 없는 추천을 어떻게 평가할 것인가?

## 결론

두 문제를 데이터나 알고리즘만으로 제거할 수는 없다.

1. MovieLens의 한국 영화 평가 부족은 TMDB 메타데이터나 콘텐츠 임베딩으로 **보충할 수 없다**.
   콘텐츠 정보는 새 영화를 표현하지만 한국 사용자가 그 영화를 좋아한다는 행동 근거를 만들지 않는다.
2. FEELM 목표 사용자의 상호작용과 별도 정답셋이 모두 없으면 출시 전에 실제 개인화 성능을
   **채점할 수 없다**. MovieLens Recall/NDCG는 source-domain 재현 결과일 뿐 한국 20대 성능이 아니다.
3. 해결 방향은 ALS를 억지로 정당화하는 것이 아니라 문제를 바꾸는 것이다. 초기에는 다른 사용자의
   행동을 추론하지 않고, 현재 사용자가 직접 밝힌 취향과 상황을 사용하는
   **knowledge/content-based recommendation**을 기본값으로 둔다.
4. 출시 후에는 노출과 선택 확률을 포함한 평가 가능한 로그를 의도적으로 수집한다. 목표 도메인
   데이터로 콘텐츠·비개인화 기준선을 이긴 것이 확인되기 전까지 ALS는 실험 후보이며 제품 뼈대가 아니다.

초기 제품 제안은 다음 한 줄로 요약한다.

```text
ALS weight = 0
명시적 선호 + 영화 콘텐츠 + 현재 시청 조건 + 한국 시장 집계 prior + 다양성
→ 초기 추천
```

## 1. 현재 근거가 부정하는 것

### 1.1 MovieLens는 FEELM 목표 분포가 아니다

[REC-DATA-006](./REC-DATA-006-movielens-market-mix.md)의 STRICT 3분할에서 한국 제작 영화는
영화 1.23%, Rating 0.33%이고 유명 외국 영화 proxy는 영화 3.77%, Rating 78.73%다. 중앙 사용자의
이력 87.70%도 유명 외국 영화 proxy에 속한다. 이 수치는 MovieLens가 주로 어떤 상호작용 축을
학습하는지 보여줄 뿐, 한국 20대의 취향 분포를 보여주지 않는다.

MovieLens 전체로 ALS를 학습해도 한국 영화 item factor에 들어갈 행동 신호가 부족하다. TMDB 줄거리,
장르, 배우, 키워드, 임베딩은 cold item을 표현하고 검색하는 데 도움을 주지만 collaborative signal을
복원하지는 못한다.

### 1.2 카탈로그 확장은 선호 근거 확장이 아니다

[REC-DATA-007](./REC-DATA-007-catalog-recommendation-capability.md)에서 한국-origin-only 영화
10,624편은 MovieLens ALS 상호작용이 없다. 후보에 넣을 수 있다는 것과 사용자가 좋아할 확률을
검증했다는 것은 다르다.

### 1.3 KMRD도 목표 사용자 정답이 아니다

[REC-DATA-008](./REC-DATA-008-kmrd-feasibility.md)은 한국 영화 상호작용 비중이 더 높지만 다음
이유로 목표 사용자 proxy에서 제외한다.

- KMRD-5M 사용자는 원본 수집 단계에서 모두 Rating 20회 이상으로 선별됐다.
- 마지막 상호작용은 2020년 1월이며 연령·성별·국적이 없다.
- 중복·충돌 Rating, README와 배포 ZIP의 통계 불일치, 0점 예외가 있다.
- 명시적 LICENSE와 FEELM/TMDB item identity Gate를 통과하지 못했다.

기존 고활동 사용자의 이력을 가려 K0·K1을 만드는 것은 실제로 0~1개만 응답한 FEELM 신규 사용자를
재현하지 않는다. KMRD는 적재·학습 코드와 보고 형식의 진단용으로만 허용한다.

### 1.4 공개 한국 데이터의 역할도 제한적이다

공개적으로 확인한 한국 데이터 중 `익명 사용자 ID + 영화 ID + 행동/평점 + 시각 + 목표 인구 증거 +
사용 권한`을 모두 만족하는 것은 찾지 못했다.

| 데이터 | 보유 신호 | FEELM에서 허용되는 역할 | ALS/개인화 평가 한계 |
| --- | --- | --- | --- |
| 한국영상자료원 비디오·VOD 열람이력 | 열람일·시간, 영화코드, 연령, 성별, 약 120만 건 | 20대·한국 영화의 집계 인기와 시계열 prior | 공개 CSV에 안정적 사용자 ID가 없어 user-item 학습 불가 |
| Mendeley 네이버 리뷰어 이력 | user ID, Rating, timestamp, 과거 리뷰·평점 | 데이터 형식·리뷰어 행동 연구 | 학습 6편·검증 7편 중심의 선택 표본이며 추천 대표셋이 아님 |
| 한국미디어패널 | 동일 개인의 장기 미디어·OTT 이용과 인구통계 | 20대 이용 맥락·온보딩 질문 근거 | 작품별 영화 ID·선호 행렬이 아님 |
| KOCCA OTT 이용행태 조사 | 전국 설문과 1030 콘텐츠 선호 | 장르·상황의 집계 prior | 개인별 영화 상호작용 정답이 아님 |

이 자료들은 MovieLens 편향을 교정하는 target Rating으로 합치지 않는다. 집계 통계와 명시적 선호 질문을
설계하는 보조 근거로만 사용한다.

## 2. 연구에서 이 문제를 다루는 방식

이 상황은 `new-community/system cold start`, `pure user cold start`, `zero-shot cross-domain
recommendation`으로 연구된다. 다만 논문의 zero-shot은 보통 target 데이터를 학습에 사용하지 않는다는
뜻이다. 연구자는 숨겨 둔 target 상호작용으로 결과를 평가한다. FEELM은 그 평가셋도 없다는 점이 다르다.

### 2.1 Zero-shot cross-domain recommendation

ZESRec은 item ID 대신 자연어 설명이나 이미지 같은 연속 표현을 사용하고, source와 target에 공통
사용자·아이템이 없어도 전이하는 방식을 제안한다. 사용자 표현은 과거 상호작용의 콘텐츠 표현을
순차적으로 집계한다. 이는 TMDB 줄거리와 온보딩 응답으로 MovieLens에 없던 한국 영화를 다루는
연구 후보가 될 수 있다.

그러나 ZESRec도 target-domain 상호작용을 시험 정답으로 사용해 성능을 보고했다. source 행동 패턴이
한국 20대로 전이되는지는 별도 target 평가 없이는 알 수 없다. 따라서 zero-shot 모델은
`R&D candidate`이지 초기 제품 champion이 아니다.

### 2.2 Preference elicitation과 knowledge-based recommendation

과거 로그가 없으면 현재 사용자에게 정보량이 높은 질문을 직접 묻는 것이 가장 현실적이다. MeLU와
후속 rating-elicitation 연구는 동일한 인기작 목록을 많이 평가시키는 대신 사용자의 취향을 구분하는
evidence candidate를 선택하고, 앞선 답에 따라 다음 질문을 바꾸는 방식을 다룬다.

Knowledge-based recommender는 사용자 이력이나 이웃 사용자 대신 **현재 선호와 item knowledge**를
사용한다. FEELM에서는 다음 입력을 결합한다.

- 좋아하는 영화 1~3편 또는 둘 중 더 끌리는 영화
- 싫어하는 장르·소재·분위기
- 지금 원하는 분위기, 동반자, 러닝타임, 관람 등급
- 현재 이용 가능한 OTT와 한국 제공 여부

사용자에게 영화 10편을 강제하지 않는다. 0개 응답이면 개인화라고 부르지 않고 비개인화·다양성
목록을 제공한다. 1개 이상 명시적 신호가 생기면 그 즉시 콘텐츠 공간의 사용자 프로필을 만들 수 있다.
이는 collaborative personalization이 아니라 **explicit-preference personalization**이다.

### 2.3 Contextual bandit과 평가 가능한 로그

출시 후에는 추천만 하지 말고 학습과 평가에 필요한 노출 조건을 함께 기록한다. Contextual bandit은
허용 가능한 후보 안에서 탐색과 활용을 병행하며 피드백으로 갱신한다. 무작위 또는 확률적 노출의
확률을 보존하면 replay, inverse propensity scoring(IPS), doubly robust 계열로 새 정책을 오프라인에서
평가할 수 있다.

이 방법도 과거 로그가 0일 때 성능 숫자를 만들어 주지는 않는다. **앞으로 편향을 보정할 수 있는
데이터를 수집하는 방법**이다.

### 2.4 사용자 시뮬레이션의 역할 제한

RecSim 같은 시뮬레이터는 사용자 상태·선택 가정을 바꿔 알고리즘 안정성과 탐색 정책을 시험하는 데
유용하다. 그러나 시뮬레이터 결과는 입력한 행동 가정을 되돌려 받을 뿐이며 한국 사용자의 실제 취향
정답이 아니다. 개발·부하·정책 회귀 테스트에는 사용하되 FEELM 만족도 증거로 사용하지 않는다.

## 3. 제안하는 초기 추천 방식

### 3.1 Candidate 생성

```text
TMDB/KMDb 카탈로그
→ 줄거리·장르·감독·배우·키워드·국가·연도·관람 등급 정규화
→ 다국어 콘텐츠 embedding과 구조화 feature
→ pgvector 유사 후보 검색
```

MovieLens ALS 후보를 전체 카탈로그의 기본 뼈대로 사용하지 않는다. source-domain 상호작용 실험이나
유명 외국 영화 보조 후보로 분리할 수 있지만, 한국 영화 후보를 밀어내지 않도록 별도 source로 취급한다.

### 3.2 사용자 표현

초기 구현은 복잡한 학습 모델보다 설명 가능한 가중 중심값으로 시작할 수 있다.

```text
user_vector
= 좋아요 영화 embedding의 가중 평균
- 싫어요 영화 embedding의 가중 평균
+ 명시적 장르·분위기·상황 feature
```

평가 개수 자체를 개인화 경계로 쓰지 않는다. 상태를 다음처럼 구분한다.

| 사용자 상태 | 허용되는 추천 |
| --- | --- |
| 명시적 신호 0개 | 한국 시장 집계 prior + 신작 + 다양성. `개인화` 표기 금지 |
| 명시적 신호 1개 이상 | 콘텐츠·조건 기반 개인화와 실제 사용 feature 기반 추천 이유 |
| 최근 행동 존재 | 동일 사용자 프로필을 점진 갱신 |
| 목표 도메인 집단 데이터 Gate 통과 | collaborative 후보를 별도 실험으로 추가 |

### 3.3 Re-ranking

초기 점수는 다음 요소만 사용한다.

```text
content preference
+ explicit constraint satisfaction
+ Korean-market aggregate prior
+ availability/freshness
+ novelty
- redundancy
```

여기서 한국 시장 aggregate prior는 한국영상자료원 열람 집계, KISDI·KOCCA 조사처럼 출처가 명확한
연령·시장 통계로 제한한다. 사용자 ID가 없는 집계는 ALS 학습 신호가 아니라 비개인화 prior다.

## 4. 정답 없는 추천의 평가 답안

### 4.1 출시 전 측정할 수 있는 것

목표 사용자 만족도 대신 다음을 `system/semantic validation`으로 측정한다.

- item identity·메타데이터 coverage·중복·누락률
- 사용자 입력을 바꾸면 추천이 의도한 방향으로 변하는지에 대한 단조성
- 싫다고 한 요소와 하드 제약을 위반하지 않는지
- 추천 이유가 실제 score feature와 일치하는지
- 한국/외국, 인기/롱테일, 장르별 catalog coverage와 exposure
- intra-list diversity, novelty, 중복 억제
- 응답 시간, 재현성, 장애 fallback

이 지표는 구현이 일관적인지를 검증한다. `한국 20대에게 추천이 정확하다`는 결론을 허용하지 않는다.

### 4.2 연구 모델 검증과 제품 검증을 분리한다

공개 source-target 데이터셋에서 target 로그를 숨겨 zero-shot 방법을 재현할 수 있다. 이 결과의 명칭은
`cross-domain method benchmark`로 제한한다. FEELM 목표 성능이 아니다.

목표 도메인 성능은 다음 중 하나가 있어야만 측정한다.

1. 한국 목표 사용자 패널의 평가·선택 데이터
2. 실제 서비스의 노출·행동 로그
3. 신뢰 가능한 외부 target-domain user-item 데이터와 사용 권한

셋이 모두 없으면 정확도·Recall·NDCG·CTR lift를 제품 성능으로 보고하지 않는다.

### 4.3 출시 후 로그 계약

각 추천 노출마다 최소한 다음을 저장한다.

```text
event_id, anonymous_user_id, request_id, occurred_at
candidate_set_id, item_id, rank, source, model_version
score_components, policy_id, selection_probability
explicit_context, click/save/detail/watch/rating reward
```

`selection_probability`와 당시 candidate set을 저장하지 않으면 추천 정책이 만든 노출 편향을 나중에
보정하기 어렵다. 탐색은 전체 카탈로그 무작위 노출이 아니라 품질·안전 제약을 통과한 후보 안에서만
수행한다. 사용자가 매우 적으면 추정 분산이 크므로 점추정값과 함께 신뢰구간과 표본 수를 공개한다.

## 5. ALS를 다시 검토할 Gate

ALS는 사용자 한 명이 10개를 평가했을 때 자동으로 켜지지 않는다. 서비스 전체 target 데이터가 다음을
모두 만족할 때만 secondary candidate source로 검토한다.

1. FEELM 사용자와 영화의 실제 상호작용으로 시간순 Train/Validation/Test를 만들 수 있다.
2. target 영화 중 충분한 비율에 반복 상호작용이 있어 item factor를 학습할 수 있다.
3. Popularity와 knowledge/content baseline보다 held-out 성능이 높다.
4. 전체뿐 아니라 한국 영화, cold item, 낮은 활동 사용자 slice에서도 개선된다.
5. 사용자별 paired bootstrap 신뢰구간이 개선 방향으로 안정적이다.
6. 추천 이유·다양성·롱테일 exposure의 제품 Gate를 해치지 않는다.

보편적인 최소 사용자 수나 최소 Rating 수는 정하지 않는다. 위 Gate를 통과하지 못하면 ALS 가중치는
0으로 유지한다. 예상 실사용자가 30명 이하라면 production ALS가 끝까지 활성화되지 않는 상황도 정상적인
결론으로 받아들인다.

## 6. 현재 아키텍처 문서에 필요한 변경

팀 합의 전 제안 사항이다.

- `ALS 기반 추천을 기본 뼈대로 사용`을 확정사항에서 제거하고 R&D 후보로 이동한다.
- `Spark Batch Candidate`는 ALS 전용이 아니라 카탈로그 전처리·embedding·다중 source 후보 생성으로
  표현한다.
- FastAPI의 초기 책임은 explicit-preference user profile, vector retrieval, re-ranking, reason 생성이다.
- Kafka·이벤트 저장의 우선 목적을 실시간 모델 과시가 아니라 **평가 가능한 노출·행동 로그 보존**으로 둔다.
- 목표 데이터 Gate 전에는 MovieLens 점수와 FEELM 제품 점수를 같은 척도로 가중합하지 않는다.

## 7. 허용되는 주장과 금지되는 주장

| 허용 | 금지 |
| --- | --- |
| MovieLens에서 특정 모델이 특정 protocol의 기준선을 이겼다 | 한국 20대에게 추천 성능이 좋다 |
| 콘텐츠·제약 기반으로 MovieLens에 없는 한국 영화를 후보화한다 | 콘텐츠 유사도가 한국 영화 평가 부족을 해결한다 |
| 사용자 응답 1개부터 명시적 선호를 반영한다 | 사용자 10편부터 정확한 개인화가 된다 |
| 출시 후 평가 가능한 로그를 수집하도록 설계했다 | 사용자 없이 추천 정확도를 검증했다 |
| ALS는 target Gate 통과 전 실험 후보다 | ALS가 FEELM 추천의 검증된 기본 뼈대다 |

## 연구 근거

1. Ding et al., **Zero-Shot Recommender Systems** — item 자연어 설명을 범용 표현으로 사용해 공통
   사용자·아이템이 없는 target으로 전이한다. target 상호작용은 평가에 사용된다.
   <https://arxiv.org/html/2105.08318>
2. Lee et al., **MeLU: Meta-Learned User Preference Estimator for Cold-Start Recommendation** — 적은
   소비 이력과 evidence candidate selection으로 신규 사용자 선호를 추정한다.
   <https://arxiv.org/abs/1908.00413>
3. Nguyen et al., **Cold-start Recommendation by Personalized Embedding Region Elicitation**, UAI 2024 —
   모든 사용자에게 고정 seed를 묻는 한계를 지적하고 개인화된 rating elicitation을 제안한다.
   <https://proceedings.mlr.press/v244/nguyen24a.html>
4. Christakopoulou et al., **Towards Conversational Recommender Systems**, KDD 2016 — cold start에서
   어떤 질문을 물을지 선택하고 온라인 피드백으로 선호를 빠르게 좁힌다.
   <https://www.microsoft.com/en-us/research/publication/towards-conversational-recommender-systems/>
5. Felfernig et al., **Knowledge-based recommender systems: overview and research directions** — 현재
   사용자 선호와 item knowledge를 사용하는 방식과 collaborative/content cold-start의 차이를 정리한다.
   <https://pmc.ncbi.nlm.nih.gov/articles/PMC10925703/>
6. Li et al., **Unbiased Offline Evaluation of Contextual-bandit-based News Article Recommendation
   Algorithms**, WSDM 2011 — 무작위 traffic log를 replay해 정책을 오프라인 평가한다.
   <https://arxiv.org/abs/1003.5956>
7. Schnabel et al., **Recommendations as Treatments: Debiasing Learning and Evaluation**, ICML 2016 —
   추천 노출의 selection bias를 인과 추론과 propensity로 보정한다.
   <https://proceedings.mlr.press/v48/schnabel16.html>
8. Ie et al., **RecSim: A Configurable Simulation Platform for Recommender Systems** — 순차 추천 정책을
   가정별로 시험하는 시뮬레이션 환경이며 실제 target 사용자 정답을 대체하지 않는다.
   <https://arxiv.org/abs/1909.04847>
9. 한국영상자료원, **비디오VOD열람이력** — 영화코드·열람시각·연령·성별을 포함하지만 공개 CSV에
   안정적 사용자 ID가 없다.
   <https://www.data.go.kr/data/15095678/fileData.do>
10. Kim et al., **Korean Movie Reviews and Historical Ratings of the Reviewer** — DOI와 CC BY 4.0이
    명확하지만 특정 target 영화 리뷰어 중심의 데이터다.
    <https://data.mendeley.com/datasets/jb5knzh8yv/6>
11. 정보통신정책연구원, **한국미디어패널 원시자료** — 동일 가구·개인의 미디어 이용을 추적하지만
    작품별 추천 상호작용 행렬은 아니다.
    <https://stat.kisdi.re.kr/kor/contents/ContentsList.html?menuId=2010126&rootId=2010002&subject=MICRO10>
12. 한국콘텐츠진흥원, **2023 OTT 이용행태 조사** — 전국 13세 이상 5,041명의 OTT 이용과 1030
    콘텐츠 선호를 조사한 집계 근거다.
    <https://eng.kocca.kr/kocca/koccanews/reportview.do?menuNo=204767&nttNo=628>
