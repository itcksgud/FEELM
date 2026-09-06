# REC-EV-028B 라벨 개방·개인화 귀속 분석 설계

> 상태: `READY_FOR_INDEPENDENT_EXACT_AUDIT`

## 목적

MovieLens는 한국 사용자 로그도 아니고 데이터셋 이후 신작의 정답도 없다. 따라서 이 실험은 한국 사용자나
실제 신작 성능을 주장하지 않는다. MovieLens에서 사용자가 평가한 영화를 의도적으로 가린 뒤, TMDB
구조화 콘텐츠 특징만으로 그 영화 2편을 고르게 했을 때 다음 두 질문에 답한다.

1. 콘텐츠를 쓰는 것 자체가 무작위 선택보다 나은가?
2. 그 차이가 전역적인 영화 편향이 아니라 **해당 사용자가 입력한 평가**에서 실제로 발생하는가?

두 번째 질문 때문에 전체 LightFM 점수뿐 아니라 사용자 항을 없앤 `BIAS_ONLY`, 사용자 항만 남긴
`DOT_ONLY`, 다른 사용자의 입력을 붙인 `PROFILE_SHUFFLE`을 함께 비교한다.

## 라벨을 여는 조건

REC-EV-028A2에서 21개 LightFM fit과 297,780개 비라벨 점수를 먼저 봉인했다. 별도 작업
`01a0751b-6621-7a61-94b2-4c401c7a3066`이 실제 파일의 해시·shape·Cartesian·순위를 재계산해
`REC_EV_028A2_ACTUAL_PRELABEL_EXECUTION_PASS`를 반환했다.

REC-EV-028B는 이 봉인 파일의 재귀 해시를 다시 확인한 뒤에만 한 번의 순차 scan으로 허용된 평가 사용자의
평점을 연다. 허용되지 않은 사용자는 userId만 읽고 행 전체를 버리며 timestamp, future reserve,
Locked Test, final reserve는 열지 않는다.

## 정답과 입력 정책

- 정답 `Q`: 사용자의 전체 MovieLens 평점 분포에서 해당 영화 평점의 mid-rank percentile.
- 주 평가: `PERCENTILE_MAGNITUDE`, K=8, Top-2.
- 민감도: binary K=8, percentile K=4·12. 이 세 결과는 설명용이며 모델 선택이나 통과 판정에 쓰지 않는다.
- 낮은 평가 위험: Top-2 중 `Q <= 0.20`이 하나라도 있으면 `HARM20=1`.
- 효용: Top-2 평균 Q와 최저 Q.

고정 별점 임계값 대신 사용자별 percentile을 쓰므로 평점을 후하게 주는 사용자와 박하게 주는 사용자의 기준
차이를 직접 보정한다.

## 사용자와 영화의 두 추정 단위

동일 영화가 여러 사용자에게 반복해서 정답으로 등장할 수 있다. 사용자 평균만 보면 몇몇 인기 영화가 결과를
지배할 수 있으므로 두 결과를 동시에 보고한다.

- 사용자 균등: 사용자 한 명을 한 표로 계산한다.
- 영화 균등: 같은 영화의 발생치를 먼저 평균한 뒤 영화 한 편을 한 표로 계산한다.

사용자 family 168개와 영화 family 112개 전체에 각각 5,000회 공유 exponential multiplier bootstrap과
max-t 동시 신뢰구간을 적용한다. 사용자의 이점과 영화 균등 이점이 모두 통과해야만
`ATTRIBUTED_PERSONALIZED_CONTENT_SIGNAL`로 판정한다.

## 해석 경계

- `KR`은 MovieLens 안의 한국 제작 영화 proxy, `RECENT`는 2020–2023 개봉 proxy다.
- 이 결과는 실제 한국 사용자나 MovieLens 이후 영화 성능의 증명이 아니다.
- 통과하더라도 이 실행에서 future reserve를 자동으로 열지 않는다. 다음 모델 비교는 새 계약과 독립 감사를
  거쳐야 한다.
- 제품의 한국 영화 노출 비율, 재랭킹 quota, 서비스 정책은 바꾸지 않는다.

실행 계약은 `docs/recommendation/contracts/rec-ev-028b-label-analysis.json`이다.
