# 적은 사용자로도 성립하는 한국 대상 추천 v1

상태: **DRAFT — 제품·팀 계약 검토 전 로컬 설계**  
작성일: 2026-09-14

## 결정

FEELM의 사용자 평가가 ALS·FM·GBT를 다시 학습할 만큼 쌓인다고 가정하지 않는다. 추천 v1은
사용자 한 명이 직접 준 적은 별점과 영화 콘텐츠만으로 계속 동작해야 한다. MovieLens 모델은
서비스의 정답 모델이 아니라 오프라인 비교 기준선과 제한된 보조 신호로 둔다.

```text
사용자가 준 0.5단위 별점
          ↓
장르·키워드·줄거리·국가·언어·연도·인물에 대한 개인 반응 벡터
          ↓
TMDB 카탈로그 전체의 콘텐츠 적합도 계산
          +
TMDB 글로벌 품질/인지도와 KOBIS 한국 극장 인지도의 보수적 사전값
          ↓
중복·기평가·관심없음 제외 + 다양성 재정렬
          ↓
추천 2편씩 제공
```

이 구조는 서비스 사용자가 한 명이어도 동작하고 MovieLens에 없는 새 영화도 TMDB 콘텐츠가
있으면 점수를 낼 수 있다. FEELM 데이터가 예상보다 많이 생긴 경우에만 별도 검증 후 FM·GBT를
추가한다. 그 추가 학습은 v1 성립 조건이 아니다.

## 각 데이터가 맡는 일

| 데이터 | 사용할 정보 | 맡길 일 | 맡기지 않을 일 |
| --- | --- | --- | --- |
| MovieLens | 사용자별 0.5~5 별점, 영화 간 공동 평가 | 알고리즘 오프라인 검증, ALS 비교 기준선, 분산 처리 실증 | 2026년 한국 사용자 정답, 최신/한국 영화의 최종 점수 |
| TMDB | 장르·키워드·줄거리·인물·국가·언어·개봉 정보, 평균평점·투표 수 | 콘텐츠 벡터와 글로벌 품질/인지도 사전값 | 한국 사용자 선호 정답 |
| KOBIS | 한국 극장 관객 수·매출·개봉 시점 | 한국에서 알려진 정도와 온보딩 대표작 선택 | 영화 품질·개인 취향 점수 |
| FEELM | 사용자가 직접 준 별점·노출·선택·제외 | 그 사용자 프로필과 직접 서비스 평가 | 많은 사용자 협업 데이터가 곧 생긴다는 가정 |

KOBIS 관객 수는 “한국인이 좋아한 정도”가 아니라 극장에서 본 규모다. TMDB 평균평점도 한국
평점이 아니다. 두 값을 MovieLens 별점과 한 열로 합치지 않고 서로 다른 의미의 특징과
confidence로 보존한다.

## 개인 점수 계산

### 1. 영화 벡터

각 영화를 다음 modality로 나눈다.

- 장르·키워드: multi-label sparse vector
- 줄거리: 결말·홍보 문구를 보수적으로 정제한 text embedding
- 국가·원어·개봉 연: categorical/numeric vector
- 감독·주요 배우: 지나치게 희소한 인물은 `OTHER`로 shrink한 sparse vector

각 modality를 따로 정규화한다. 메타데이터 누락은 0점이나 싫어요가 아니라 `missing`으로
기록하고 그 modality의 분모에서 뺀다.

### 2. 사용자 프로필

사용자 `u`가 영화 `i`에 준 별점을 `r_ui`라 한다. 사용자 평균은 적은 표본에서 흔들리지 않게
전체 중립값 `3.0` 쪽으로 shrink한다.

```text
mu_u = (lambda_mean * 3.0 + sum(r_ui)) / (lambda_mean + n)
w_ui = clip(r_ui - mu_u, -2, 2)

positive_profile = weighted_mean(x_i, w_ui > 0)
negative_profile = weighted_mean(x_i, w_ui < 0, weight=abs(w_ui))
user_profile = normalize(positive_profile - alpha * negative_profile)
```

실제 구현에서는 기존 실험에서 유리했던 사용자 내 상대값과 원점수 대조를 같은 fixture로 다시
검증해 `lambda_mean`과 `alpha`를 고정한다. 별점은 계속 0.5 단위로 저장하며 LIKE/DISLIKE로
변환해 잃지 않는다.

후보 영화 `j`의 콘텐츠 점수는 보유한 modality끼리만 cosine similarity를 계산한 뒤, 사용자가
평가한 영화 수와 특징 범위로 confidence를 붙인다.

```text
n_eff = 서로 다른 콘텐츠 특징에 실제 근거를 준 유효 평가량
confidence_u = n_eff / (n_eff + lambda_profile)
base_score = confidence_u * content_score
           + (1 - confidence_u) * market_prior
```

평가 편수 K 하나로 인기→개인화를 갑자기 전환하지 않는다. 같은 10편이어도 전부 비슷한 영화면
근거가 적기 때문에 `n_eff`를 쓴다. UI에서 개인화 시작 문구가 필요하면 K=2·4·6·8·10의
held-out 곡선과 작은 사용자 평가에서 정하지만, 내부 가중치는 연속적으로 변한다.

## TMDB와 KOBIS 결합

원시 투표 수·평균평점·관객 수를 직접 더하거나 큰 쪽을 점수로 고르지 않는다.

1. TMDB 평균평점은 vote count로 전체 평균 쪽에 Bayesian shrink한다.
2. TMDB vote count는 개봉연도·국가/언어 cohort 안에서 `log1p` percentile로 바꾼다.
3. KOBIS 관객 수는 개봉연도와 전국/연간 누적 기준을 맞춘 뒤 `log1p` percentile로 바꾼다.
4. 한 출처가 없으면 다른 출처를 사용할 수 있도록 후보 자격은 두 confidence의 `max`를 쓴다.
5. 순위 사전값은 존재하는 출처의 confidence-weighted average를 쓴다. KOBIS는 인지도 항에만
   들어가고 예측 별점을 올리는 품질 정답으로 쓰지 않는다.

한국 맞춤은 KOBIS 점수를 크게 주는 것으로 끝나지 않는다. 한국에서 인지된 영화를 초기 화면과
온보딩 질문에 충분히 포함하고, 사용자가 한국 영화·국가·언어·인물 특징에 준 자신의 반응이
콘텐츠 프로필에 직접 반영되게 한다.

## ALS와 GBT의 위치

### ALS

ALS는 MovieLens 사용자 평가로 만든 영화 factor가 있는 교집합에서만 점수를 낸다. 최신 영화나
MovieLens 미지원 영화에는 factor가 없으므로 서비스 기본 모델로 쓰지 않는다. 지원 영화에 한해
다음 조건을 모두 만족할 때만 낮은 가중치의 보조 점수로 비교할 수 있다.

- 사용자가 평가한 영화 중 ALS factor 보유 영화가 충분하다.
- 후보도 ALS factor를 가진다.
- 최근/한국/저인기 slice에서 콘텐츠 단독보다 악화되지 않았다는 검증이 있다.
- ALS 미지원에는 평균점 같은 가짜 기본값을 넣지 않고 `UNAVAILABLE`로 둔다.

### MovieLens로 학습한 GBT/FM

GBT/FM이 맞히는 정답도 MovieLens 평점이다. TMDB/KOBIS 특징을 넣어도 target domain이 한국
서비스로 바뀌지는 않는다. 따라서 이 모델은 다음 용도로 제한한다.

- 콘텐츠 특징과 별점 사이 관계가 MovieLens에서 재현되는지 확인
- 콘텐츠 개인화 점수의 비교 기준 또는 보조 점수
- 모델/특징 파이프라인의 분산 학습 실증

서비스의 기본 점수로 승격하려면 FEELM의 소규모라도 직접적인 블라인드 선호 평가에서 콘텐츠
기본안보다 좋아야 한다. 데이터가 적어서 통계적 우위를 판단하지 못하면 콘텐츠 기본안을 유지한다.

## 온보딩 질문

고정된 “팝콘 맛 8개”를 정답 군집으로 먼저 두지 않는다. 군집은 질문 후보를 고르고 중복을
줄이는 도구로만 쓴다.

1. 영화 콘텐츠 공간을 여러 K와 seed로 군집하고 stability·coverage·대표작 인지도를 평가한다.
2. 각 안정된 넓은 영역에서 중심에 가깝고 TMDB/KOBIS 인지도가 있는 영화 1~2편을 후보로 둔다.
3. 사용자의 첫 답으로 선호가 갈리는 특징을 찾고, 다음 질문은 그 차이를 가장 많이 줄이는
   영화를 고른다.
4. 별점은 0.5 단위로 받되 한 화면의 질문 수를 제한한다.
5. K=2·4·6·8·10별 추천 개선량과 이탈/응답 시간을 함께 비교한다.

계층형 질문은 편의성을 위한 탐색 경로다. 최종 사용자를 군집 하나에 강제로 넣거나 군집 번호를
개인의 정답 취향으로 쓰지 않는다. 여러 특징에 동시에 속하는 multi-label 반응을 유지한다.

## 추천 2편과 발견

첫 두 편은 다음처럼 역할을 달리할 수 있다.

- 1번: 콘텐츠 적합도와 신뢰 가능한 대중성 사전값이 모두 높은 안전 추천
- 2번: 콘텐츠 적합도 하한을 지키면서 첫 영화와 덜 겹치는 인접 탐색 추천

MMR 같은 재정렬로 두 영화의 장르·키워드·인물 중복을 낮춘다. “발견”을 별도 K-means 군집
정답으로 만들 필요는 없다. 사용자가 더 보기를 누르면 같은 방식으로 2편씩 이어가며 이미 노출,
평가, 관심없음 영화는 제외한다.

## 검증

### 오프라인

MovieLens 시간순 holdout에서 K=2·4·6·8·10 입력만 남겨 다음을 비교한다.

- 인기 기준선
- 개인 콘텐츠 프로필
- 개인 콘텐츠 + TMDB/KOBIS 사전값
- 지원 교집합의 ALS 보조
- MovieLens GBT/FM 보조

MSE만 보지 않고 NDCG/Recall@2·4·6·10, 지원률, 최신/한국/저인기 slice, 추천 중복·다양성,
최악 사용자 분위수를 본다. 이 결과는 알고리즘의 상대 동작과 실패 구간을 보여줄 뿐 한국 서비스
정답으로 표현하지 않는다.

### 작은 직접 평가

사용자가 적다면 같은 사람이 여러 모델의 추천을 모두 평가하는 paired crossover가 효율적이다.
모델 이름을 숨기고 순서를 무작위화한 뒤 각 추천 2편에 “보고 싶은 정도”를 받는다. 평균과 함께
개인별 차이, 중앙값, bootstrap 구간, 미지원률을 보고한다. 표본이 작으면 우위를 확정하지 않고
descriptive evidence로 남긴다.

노출 로그에는 `user`, `candidate set`, `policy/model version`, `rank`, `score components`,
`shown_at`, `selected/rated/dismissed`와 선택 확률을 남긴다. 추천된 영화에만 반응이 생기는
partial-label 편향이 있으므로, 이후 로그를 새 정책의 정답처럼 단순 재사용하지 않는다.

랜덤 시뮬레이션 별점은 오류 처리·지표 계산·갱신 안정성을 검증하는 데만 쓴다. 추천 만족도를
증명하지 않는다.

## 빅데이터 분산 처리의 역할

서비스 기본 모델이 콘텐츠 기반이어도 Spark/HDFS 역할은 명확하다.

- MovieLens32M, TMDB, KOBIS 정제·ID 연결·row-lineage
- 콘텐츠 sparse/text vector 생성과 전체 카탈로그 특징 저장
- 영화-영화 이웃과 사용자 후보 pool의 일괄 계산
- 여러 K의 군집 안정성·대표작 계산
- MovieLens 오프라인 비교와 1M→10M→32M, Worker1→2 성능 검증
- 모델·특징·후보·평가 산출물의 HDFS/Parquet 버전 관리

온라인에서는 사용자의 작은 프로필 벡터와 이미 계산한 영화 벡터를 비교하면 된다. 이 계산을
억지로 실시간 Spark Job으로 만들지 않는다.

## 멘토 피드백에서 얻은 답

- “콘텐츠 기반으로 합친다”는 답은 MovieLens 정답을 KOBIS로 바꾸라는 뜻이 아니다. 협업
  데이터가 없는 사용자·영화에 콘텐츠가 직접 다리를 놓게 한다는 뜻이다.
- “만족하지 않으면 데이터를 쌓는다”는 말은 대규모 재학습이 곧 가능하다는 보장이 아니다.
  작은 직접 평가는 모델 선택 근거로 쓰고, 대규모 학습은 충분해질 때만 연다.
- 군집 수를 먼저 8로 고정하지 말라는 피드백은 온보딩 질문의 정보량·피로도 곡선을 먼저 보라는
  뜻이다. 군집은 질문과 다양성의 도구이지 추천 정답이 아니다.
- “오프라인 평가만”은 MovieLens에서 알고리즘을 비교할 수 있다는 뜻이다. 도메인이 다른 한국
  사용자의 만족도까지 검증됐다는 뜻은 아니므로 발표에서 두 주장을 분리한다.

## 참고한 1차 자료

- GroupLens, MovieLens 32M README: 1995~2023년 MovieLens 활동 32,000,204건이며 사용자
  demographic은 없다. <https://files.grouplens.org/datasets/movielens/ml-32m-README.html>
- Kula, *Metadata Embeddings for User and Item Cold-start Recommendations*: 콘텐츠 metadata와
  협업 신호를 함께 표현하는 hybrid가 sparse/cold-start를 보완할 수 있음을 실험했다.
  <https://arxiv.org/abs/1507.08439>
- Li et al., *Unbiased Offline Evaluation of Contextual-bandit-based News Article Recommendation
  Algorithms*: 노출되지 않은 후보의 반응은 관측되지 않는 partial-label 문제와 무작위 logging의
  필요성을 설명한다. <https://arxiv.org/abs/1003.5956>
