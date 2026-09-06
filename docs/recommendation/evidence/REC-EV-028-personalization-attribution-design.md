# REC-EV-028A 개인화 귀인·영화 단위 검증 설계

> 상태: `PROPOSED_FOR_INDEPENDENT_EXACT_AUDIT`
>
> 고정 기준: `Percentile, K=8, Top-2`

## 목적

REC-EV-027은 MovieLens 상호작용을 영화 열 전체에서 제거한 뒤에도 TMDB 구조 특징을 학습한 LightFM이
직접 콘텐츠 cosine보다 사용자의 관측 선호를 더 잘 복원한다는 결과를 냈다. 그러나 아직 두 질문이
남았다.

1. 개선이 사용자의 8편 입력에서 나온 개인화인가, 학습된 전역 item bias인가?
2. 같은 인기 영화가 많은 사용자에게 반복 등장한 결과를 한국·최신 영화 전반으로 잘못 일반화한 것은
   아닌가?

REC-EV-028A는 새 모델을 더 붙이기 전에 이 두 원인을 분리한다. 여기서 개인화가 확인돼야만 별도로
남겨 둔 다음 개발 사용자에서 E5-only, 구조+E5, harm-aware 모델을 비교한다.

## REC-EV-027에서 확인한 것과 못 한 것

무작위 cold item 4개 외부 fold의 8,974명에서 LightFM은 직접 구조 cosine보다 `HARM20`을 6.84%p
줄이고, Top-2 평균 percentile을 4.90%p, 두 편 중 낮은 percentile을 5.71%p 높였다. 세 효과의 동시
95% 하한도 모두 0보다 컸다.

한국 제작 proxy 661명과 2020–2023 proxy 1,058명에서는 LightFM이 무작위보다 나았지만 직접 구조
cosine보다 낫다는 동시구간은 모두 0을 포함했다. 최신 proxy의 `HARM20`은 35.7%였다. 이는 4개
MovieLens 평가 영화 중 고른 두 편에 사용자 하위 20% 영화가 포함됐다는 뜻이지 production 실패율이
아니다.

사후 진단에서는 최신 영화 중 MovieLens 평가 1,000개 이상 구간의 선택된 영화 low-tail 비율은 16.7%,
평가 1–9개 구간은 42.3%였다. 또 한국 proxy target 2,644개 중 상위 10편이 50.5%를 차지해 영화
유효표본수가 약 26.8편뿐이었다. 따라서 사용자 bootstrap만으로 넓은 한국 영화 일반화를 주장할 수 없다.

## 새 데이터 역할

Locked Test와 final reserve는 계속 닫는다. REC-EV-027에서 모델 학습 역할이었던 사용자만 다시
결정적으로 나눠 다음처럼 사용한다.

- 80%: REC-EV-028 모델 학습
- 10%: 개인화 귀인 평가
- 10%: 귀인 통과 뒤 새 모델을 평가할 미래 개발 reserve

귀인 평가 사용자와 미래 개발 reserve의 별점은 모델 학습에 들어가지 않는다. 이번 단계에서 미래
reserve는 user ID로 phase만 판별하고, movie ID·membership·profile·별점·점수·정답은 전혀 만들거나
열지 않는다. 귀인 통과 뒤 새 정확 계약의 감사를 받아야 membership부터 만들 수 있다.
과거에 이 사용자가 REC-EV-027 학습에 기여했다는 한계가 있으므로 독립 확인이 아니라 adaptive
development evidence로만 부른다.

## 비교할 네 가지 LightFM 점수

모델과 hyperparameter는 REC-EV-027 승자 그대로 다시 학습한다.

| 점수 | 남기는 정보 | 확인 질문 |
| --- | --- | --- |
| Full personalized | item bias + 본인 profile vector | 현재 시스템 |
| Bias only | item bias만 | 전역적으로 잘 평가되는 콘텐츠 특징만으로 충분한가 |
| Dot only | 본인 profile dot item factor만 | bias 없이 개인화 신호가 남는가 |
| Profile shuffle | item bias + 다른 사용자의 profile vector | 아무 profile이나 붙여도 같은가 |

직접 구조 cosine과 무작위 기대값도 같은 slate에서 함께 보고한다. `Full`이 bias-only와 shuffled-profile을
모두 이겨야 개인화로 귀인한다. Dot-only는 전역 성향과 개인화의 결합 구조를 설명하는 진단이지 단독
Gate가 아니다.

## 사용자 평균과 영화 평균을 분리하는 이유

사용자 기준 지표는 서비스가 두 편을 보여줄 때의 질문을 그대로 유지한다.

- `HARM20`: 둘 중 하나라도 사용자 하위 20%면 1
- `TOP2_MEAN_Q`: 두 편의 평균 사용자 percentile
- `TOP2_MIN_Q`: 두 편 중 낮은 사용자 percentile

영화 기준에서는 각 target 영화가 사용자의 slate에 등장한 횟수와 무관하게 한 표를 갖도록 item-macro
기여도를 별도로 만든다. Top-2 평균에 대한 영화별 기여와 선택된 두 slot의 low-tail 기여를 영화 안에서
먼저 평균한 뒤 영화들을 동일 가중한다. 같은 사용자가 여러 영화에 기여하는 상관도 무시하지 않도록,
사용자와 영화에 독립적인 exponential multiplier를 동시에 부여해 user×movie 불확실성을 전파한다.
영화별 고정 등장 횟수의 역수를 기본 가중치로 사용하고 사용자·영화 multiplier를 occurrence에 함께
곱하므로, 한 번만 등장한 영화에서도 사용자 불확실성이 상쇄되지 않는다.
사용자 지표를 영화 지표로 바꾸는 것이 아니라 두 일반화 질문을 둘 다 통과시키는 방식이다.

무작위 cold 결과는 R0–R4를 각각 보고하고, pooled에서는 사용자별 이용 가능한 fold 차이를 먼저 동일
가중 평균한다. 영화 pooled는 각 영화가 유일하게 속한 cold fold의 item value를 합친 뒤 영화들을 동일
가중한다. 후속 모델 reserve를 여는 판정은 이 `RANDOM_POOLED` 사용자·영화 결과만 사용한다.

한국·최신 track은 사용자 수 외에 고유 target 영화 수와 target slot 분포의 유효 영화 수도 Gate로 둔다.
이 기준을 못 넘으면 사용자 수가 많아도 상태는 `POPULAR_RATED_PROXY_ONLY`다.

## K와 입력 방식

`Percentile, K=8`은 REC-EV-022B에서 모델 개발 후보로 남은 셀 중 현재 비교 기준이다. 최적 K나 제품
입력 UI라는 뜻은 아니다. Binary K=8과 Percentile K=4·12도 모든 점수와 함께 봉인하지만, 이번 귀인
판정이나 모델 선택에는 쓰지 않는다. 서비스가 한 번에 두 편을 보여준다는 현재 인터페이스에 맞춰 평가는
Top-2로 유지한다.

## 다음 단계

무작위 item-cold에서 사용자 개인화와 영화 일반화가 모두 통과한 경우에만 아직 정답을 열지 않은 다음
개발 사용자로 다음 세 시스템을 비교한다.

1. 동일 LightFM 학습기에 E5만 넣어 표현 차이를 분리한다.
2. 동일 LightFM 학습기에 구조 특징과 E5를 함께 넣어 E5의 순증분을 본다.
3. BPR 공간 정렬 없이 profile→slate를 직접 학습하고 low-tail에 별도 비용을 주는 episodic 모델을 본다.

DropoutNet은 학습 때 cold 조건을 의도적으로 만들라는 근거로, CLCRec은 콘텐츠와 협업 표현의 의존성을
대조학습하라는 근거로 참고한다. 다만 REC-EV-028A는 새 모델 전에 현재 개선의 정체부터 밝힌다.

어떤 성공도 한국 사용자나 실제 출시 당시 신작 성능을 증명하지 않는다. 그 두 주장은 한국 사용자 평가나
운영 탐색 로그가 생길 때까지 계속 닫는다.
