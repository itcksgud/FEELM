# REC-EV-027 strict item-cold 모델 실험 설계

> 상태: `PROPOSED_FOR_INDEPENDENT_DESIGN_AUDIT`
>
> 입력 정책: `Percentile, K=8`
>
> 출력·평가: `Top-2`

## 해결하려는 질문

한국 영화와 MovieLens 수집 이후 영화에는 서비스 정답이 없다. 따라서 이 실험은 한국 사용자 성능이나
실제 신작 성능을 직접 증명하지 않는다. 대신 MovieLens 영화의 모든 상호작용을 학습에서 제거해 신작을
모사하고, TMDB 구조 특징과 E5만으로 사용자의 상대적 선호를 복원할 수 있는지 검증한다.

핵심 질문은 다음 하나다.

> 어떤 영화의 MovieLens 상호작용을 전부 삭제했을 때도 콘텐츠 모델이 사용자가 실제로 높게 평가한
> 영화를 Top-2에 올리고, 사용자 하위 20% 영화를 피할 수 있는가?

## 기존 실험과의 차이

REC-EV-023B는 interaction-masked item-disjoint였지만 candidate-core가 이미 과거 train interaction을
요구했다. 또한 cold 영화를 20편 이상 평가한 사용자와 고정 JUDGED20에 조건화됐다. 따라서 계약상
`STRICT_COLD_START`와 `FULL_CATALOG_RETRIEVAL` 주장이 금지됐다.

REC-EV-027은 candidate-core나 기존 REC-EV-019B의 영화 membership을 사용하지 않는다. 기존 019B
feature artifact도 Base Train 사용자가 평가한 영화에서 시작했으므로 strict universe의 근거가 될 수 없다.
먼저 Rating을 열지 않고 MovieLens `movies.csv`와 `links.csv`의 전체 87,585편을 TMDB로 보강한다.
이 단계는 별도의 불변 catalog-build 계약에 원본·구현·런타임을 고정하고, 87,585편 모두 같은
identity 검증과 feature 추출 경로를 거친다.
기존 TMDB 응답과 E5 벡터는 요청·본문 hash와 embedding input hash가 정확히 일치할 때 계산 캐시로만
재사용한다. 새 full-catalog feature artifact를 hash로 고정한 다음 영화 ID hash로 fold를 만든다.

cold fold에 속한 영화는 모든 학습 사용자에게서 Rating column 전체를 삭제한다. 학습 과정은 cold
영화의 Rating 값, Rating 수, 인기도, co-occurrence, item-ID 특징, 실제 BPR item factor를 보지 못한다.

## 실험 흐름

1. `movies.csv`와 `links.csv`만으로 87,585편의 Rating-independent 콘텐츠 artifact를 만든다.
2. 새 artifact를 고정한 뒤 영화 ID만으로 5개 item fold를 만든다.
3. 평가 사용자를 hash로 screen 20%와 이후 검증 80%에 먼저 분리하고, fold 0을 cold로 두어 screen
   사용자에서만 전체 모델을 1차 screen한다.
4. 학습 사용자는 warm 영화만으로 모델과 percentile prior를 학습한다.
5. 평가 사용자는 warm 영화 8편으로 `Percentile Magnitude` 프로필을 만든다.
6. 각 평가 사용자가 실제 평가한 cold 영화 20편을 Rating 값 없이 hash로 선택한다.
7. Percentile과 Binary K=8의 모든 모델 점수·순위를 함께 봉인한 뒤에만 20편의 Rating을 연다.
8. 하위 20% 영화 포함 여부를 먼저 보고, Top-2 평균·최저 percentile을 함께 본다.
9. screen에 한 번도 등장하지 않은 평가 사용자만 사용해, 통과한 learned system 최대 2개와 direct
   content 기준 하나를 fold 1~4에서 다시 학습한다.
10. 같은 후기 검증 사용자와 finalist를 TMDB `production_countries`에 KR이 있는 영화와 2020~2023
    영화 cold 조건에서
    추가로 평가한다. 이 두 proxy의 모든 순위도 함께 봉인한 뒤 label을 한 번만 연다.

## 입력과 정답

`Percentile, K=8`은 모델 차이를 보기 위한 고정 개발 기준이다. 최적 K라는 뜻이 아니다.

- 입력 weight: 각 fold·track의 warm Rating만으로 사용자별 10-bin histogram을 만들고, 사용자를 동일
  가중해 prior를 다시 계산한다. 관측한 프로필 8편의 midrank를 이 prior로 `tau=5` smoothing한 뒤
  `2q-1`로 변환한다.
- 평가 label: 해당 평가 사용자의 허용된 MovieLens 전체 Rating 이력에서 target Rating의 midrank
  percentile을 계산한다.
- target Rating은 점수와 순위를 모두 봉인하기 전에는 읽지 않는다.
- 미평가 영화는 싫어요로 변환하지 않는다.

## 모델 비교

| 구분 | 모델 | 확인하려는 것 |
| --- | --- | --- |
| 무신호 | Random expectation | 고정 slate에서 우연히 2편을 뽑은 결과 |
| 직접 콘텐츠 | TMDB structured cosine | 구조 특징 자체의 cold 신호 |
| 직접 콘텐츠 | E5 cosine | 텍스트 의미 자체의 cold 신호 |
| 단순 결합 | Structured+E5 RRF | 두 독립 신호의 보완성 |
| feature hybrid | feature-only LightFM | item ID 없이 행동으로 구조 특징을 학습할 이득 |
| CF 정렬 | E5→fold-specific BPR ridge | E5를 협업 공간으로 정렬할 이득 |
| 순수 콘텐츠 학습 | E5 episodic content encoder | BPR 공간 없이 K=8→Top-2 목적을 직접 학습할 이득 |

E5→BPR의 teacher도 fold별로 새로 학습한다. 기존 REC-EV-019C factor는 cold 영화 Rating을 학습했으므로
REC-EV-027 점수에는 사용할 수 없다. LightFM은 structured, 두 learned E5 모델은 E5를 사용하므로
이 표는 알고리즘만의 우열이 아니라 `모델×특징` system-cell 비교다.

## 평가 우선순위

서비스는 두 편을 보여주므로 모든 1차 지표는 Top-2다.

1. `HARM20`: Top-2 중 사용자의 하위 20% 영화가 하나라도 있는 비율. 낮을수록 좋다.
2. `TOP2_MEAN_Q`: 추천 두 편의 사용자별 percentile 평균. 높을수록 좋다.
3. `TOP2_MIN_Q`: 추천 두 편 중 더 낮은 percentile. 높을수록 좋다.
4. `GOOD80`: 추천 두 편 중 상위 20% 영화가 하나라도 있는 비율. 보조 지표다.

learned system은 Random 대비 `HARM20`을 악화시키지 않고, 평균과 최저 percentile이 모두 양의 방향이어야
다음 fold로 진행한다. 하나의 가중합 점수로 서로 다른 실패를 숨기지 않는다.

## 해석 경계

- random item fold 통과: 학습에서 interaction column을 제거한, 이미 평가된 feature-complete cold
  slate 안에서 콘텐츠 신호가 상대 선호에 이전된다는 근거다.
- TMDB production-country KR cold 통과: 고활동 MovieLens 평가자에게서 해당 proxy 영화 간 상대 선호를
  복원한다는 proxy 근거다.
- 2020~2023 cold 통과: 현재 TMDB metadata를 사용한 회고적 newer-item transfer 근거다.
- 어떤 결과도 한국 사용자, 실제 서비스 신작, 미평가 전체 카탈로그 relevance를 증명하지 않는다.
- 전체 cold catalog에는 정확도 label이 없으므로 추천 집중도와 coverage만 진단한다.
- 평가 slate membership에는 사용자가 그 cold 영화를 평가했다는 사실을 사용한다. 따라서 interaction을
  전혀 사용하지 않는다는 뜻이 아니라, model fitting·scoring에는 cold Rating 값·수·요인·인기도를
  차단하고 평가 membership만 예외로 사용하는 조건부 선호 복원 실험이다.

기계 판독 계약은
[`rec-ev-027-strict-item-cold-model-screen.json`](../contracts/rec-ev-027-strict-item-cold-model-screen.json)에
고정한다.
