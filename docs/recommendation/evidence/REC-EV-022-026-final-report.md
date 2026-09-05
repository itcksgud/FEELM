# MovieLens 편향과 출시 전 추천 평가에 대한 최종 실험 보고서

> 상태: `CONFIRMATORY_STOPPED_UNTIL_NEW_TARGET_EVIDENCE`
>
> 범위: `REC-EV-022A`부터 `REC-EV-026`까지의 입력 정책, 콘텐츠 전이, 사용자·영화 구간 분석
>
> 제품 결정: `champion=null`, `product_policy_updated=false`

## 1. 목적

이 실험은 “개인화 모델이 인기도보다 좋다”를 다시 확인하기 위해 시작하지 않았다. 서비스 개발에서
실제로 맞닿은 문제는 다음 두 가지였다.

1. MovieLens의 Rating은 유명 외국 영화 proxy에 78.73% 집중되고 한국 제작 영화에는 0.33%만
   남아 있다. 데이터 수록 이후 영화에는 사용자 상호작용도 없다. 이 데이터만으로 한국 영화와 새 영화를
   추천하기 어렵다.
2. 출시 전에는 추천을 받은 사용자가 영화를 보고 다시 평가하는 순환을 반복할 수 없다. 따라서 실제
   FEELM 만족도를 직접 최적화하거나 평가할 수 없다.

첫 번째 문제에는 TMDB 구조 특징과 384차원 다국어 E5 임베딩으로 상호작용이 적거나 없는 영화를
표현하는 방법을 적용했다. 두 번째 문제에는 MovieLens Rating을 모델 학습과 오프라인 평가에 사용하되,
사용자마다 다른 별점 기준을 사용자 내 percentile로 바꾸고 이미 평가한 영화에서 선호를 복원하는
대리 평가를 적용했다.

이 두 방법은 데이터 부재를 없애지 않는다. 목표는 제한된 데이터에서 무엇을 비교할 수 있고, 어떤
제품 결론은 내릴 수 없는지를 재현 가능하게 결정하는 것이었다.

## 2. 실험 질문과 평가 설계

| 질문 | 채택한 설계 | 해석 경계 |
| --- | --- | --- |
| 입력은 몇 편이 필요한가 | K=0부터 30까지 탐색하고 별도 사용자 집합에서 K=6·8·14를 확인 | K 하나를 제품 기본값으로 선택하지 않음 |
| 좋아요·싫어요와 별점 중 무엇이 나은가 | 사용자 내 별점 percentile을 shrinkage한 뒤 `BINARY_SIGN`, `PERCENTILE_MAGNITUDE`, `ORDINAL_RANK` 비교 | MovieLens의 과거 별점을 실제 온보딩 행동이라고 부르지 않음 |
| 맞춤 추천 2편을 어떻게 평가하는가 | Top-2 평균 선호 percentile과 Top-2 중 최저 선호 percentile을 함께 사용 | 좋아하는 한 편만 맞추고 싫어하는 한 편을 섞는 모델을 통과시키지 않음 |
| 사용자마다 한 장면만 보는가 | 동일 사용자에서 네 개의 분리된 4편 panel을 만들고 각 panel의 Top-2를 평가 | 사용자 timestamp를 시청 순서로 해석하지 않음 |
| 한국 영화와 최근 영화는 어떻게 보는가 | 한국 제작 target과 비한국 control, 2020~2023 target과 2020년 이전 control을 분리 | MovieLens 사용자를 한국 사용자라고 부르지 않음 |
| 콘텐츠 벡터가 실제로 도움이 되는가 | TMDB 구조, E5, LightFM, RRF, feature transfer, E5→BPR 정렬을 단계적으로 비교 | 같은 평가값에서 유리한 모델만 사후 선택하지 않음 |

### 2.1 사용자별 별점 해석

고정된 4점 이상을 좋아요로 두지 않았다. 사용자 프로필의 별점 `r`을 다음 값으로 바꿨다.

`q_K(r) = (프로필에서 r보다 낮은 별점 수 + 0.5 × 같은 별점 수 + 5 × 전체 Train 사전분포의 mid-CDF) / (K + 5)`

- `BINARY_SIGN`은 `q_K`가 사용자 중앙보다 높은지 낮은지만 사용한다.
- `PERCENTILE_MAGNITUDE`는 중앙에서 얼마나 떨어졌는지까지 사용한다.
- `ORDINAL_RANK`는 프로필 안의 순서만 사용한다.

따라서 평점을 후하게 주는 사용자와 박하게 주는 사용자에게 같은 4점을 동일한 의미로 강제하지 않는다.

### 2.2 Top-2 평가

- 효용: 추천된 두 편의 `q_eval` 평균이다. 높은 값이 좋다.
- 최악 항목 손실: `1 - min(q_eval)`이다. 낮은 값이 좋다.
- REC-EV-026에서는 4편 panel에서 모델이 고른 Top-2와 무작위 Top-2의 기대값을 비교했다.
- 평가 단위는 영화 행이 아니라 사용자다. 같은 사용자의 네 panel을 독립 사용자처럼 세지 않았다.

이 평가는 “좋아하는 영화를 뽑는 것”과 “싫어하는 영화를 한 편이라도 섞지 않는 것”을 동시에 본다.
다만 평가 대상은 사용자가 이미 Rating을 남긴 영화다. 실제 노출 뒤 시청·평가를 관측한 결과는 아니다.

## 3. 실험 흐름

| 실험 | 질문 | 결과 | 다음 결정 |
| --- | --- | --- | --- |
| REC-EV-022A/B | K=0~30과 별점 인코딩 | 별도 확인 집합에서 `K={6,8,14} × {BINARY_SIGN,PERCENTILE_MAGNITUDE}` 여섯 cell 유지 | 모델 개발 후보만 고정, 제품 K 미선택 |
| REC-EV-023A~D | 구조·E5·RRF·LightFM | masked pseudo-cold에서 TMDB 구조 신호는 유지됐으나 E5·RRF·feature-only LightFM의 증분 근거는 없음 | 직접 콘텐츠 조합만으로 해결됐다고 보지 않음 |
| REC-EV-023E/F | 한국 제작·2020~2023 전이 | 각각 319명·686명에서 `NO_ROBUST_TARGET_SIGNAL` | 목표 구간의 별도 실험 필요 |
| REC-EV-024A/B | 한국·최근 영화 anchor 2개 고정 | `NO_SUFFICIENT_INPUT_REMEDY` | 입력만 바꿔 모델 한계를 해결하지 못함 |
| REC-EV-025A/B R1 | 콘텐츠 feature transfer | 한국 319명은 정밀도 부족, 최근 685명은 robust head 없음 | 콘텐츠와 협업 표현의 간극을 직접 다루기로 함 |
| REC-EV-026 | E5를 5개 BPR 공간에 ridge 정렬 | 한국 target 평균은 양수였으나 동시추론 정밀도 부족, 최근 target은 사전 margin 미달 | 새 목표 사용자 evidence 전까지 확증 중단 |

K=6·8·14는 성능 순위가 아니다. REC-EV-022B가 서로 다른 정보량 구간을 후속 모델 비교에 남긴
결과다. K=30까지 plateau를 확인하지 못했으므로 “K=14가 최적” 또는 “더 많은 입력은 무의미”라고
말할 수 없다.

## 4. REC-EV-026 결과

REC-EV-026은 384차원 E5 벡터를 다섯 BPR seed의 128차원 item-factor 공간으로 각각 ridge 정렬했다.
Mapper는 비한국 또는 2020년 이전 source item으로만 학습했고, target과 control은 mapper 선택에서
제외했다. 사용자별 profile 14편, target 4편, control 4편을 네 panel로 평가했다.

### 4.1 실행 무결성

- 한국 제작 전이 `REC-EV-026A`: 181명, target 136편, control 1,853편
- 최근 영화 전이 `REC-EV-026B`: 445명, target 774편, control 2,570편
- 공동 max-T family: 312 contrasts, bootstrap 4,000회, 유효 attempt 0~3,999
- Mapper validation cosine 평균: A `0.04053`, B `0.03970`, 다섯 seed 모두 0 초과
- 원본 실행 산출물 18개는 사후 분석 전후 동일 SHA-256 inventory를 유지
- Rating timestamp 0 byte, Locked Test·final reserve 미개봉

설계·preflight·실행·결과 재계산은 별도 Codex 채팅에서 각각 독립 검토했다. 최종 읽기 전용 감사가
12개 검사를 통과했고 원본 18개 파일의 hash가 바뀌지 않았음을 확인했다.

### 4.2 한국 제작 target

E5→BPR의 target 평균은 모든 여섯 입력 cell에서 양수였다.

| 인코딩 | K | 효용 개선 | 최악 항목 안전 개선 | 효용이 양수인 사용자 | 안전 개선이 양수인 사용자 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Binary | 6 | 0.0309 | 0.0342 | 55.8% | 48.6% |
| Binary | 8 | 0.0391 | 0.0402 | 56.4% | 48.6% |
| Binary | 14 | 0.0453 | 0.0533 | 58.0% | 53.6% |
| Percentile | 6 | 0.0478 | 0.0506 | 61.9% | 54.1% |
| Percentile | 8 | 0.0511 | 0.0582 | 62.4% | 55.2% |
| Percentile | 14 | 0.0489 | 0.0511 | 61.3% | 53.0% |

이 값은 사후 기술 통계다. 181명에서 26개 동시구간의 half-width가 0.05 이상이었고 최대값은
0.06812였다. 사전에 잠근 target, control, 조건부 gap, 네 panel, 증분 기준을 모두 통과한 cell은
없었다. 관측된 분산과 critical value를 고정한 단순 근사로는 half-width 0.05 미만에 182~336명이
필요하다. 현재 허용된 미노출 모집단은 남아 있지 않으므로 같은 181명을 재사용해 이 수를 채울 수 없다.

### 4.3 2020~2023 target

E5→BPR의 효용 개선은 `0.0108~0.0193`, 안전 개선은 `0.0107~0.0163`이었다. 절대 target의 12개
점추정치 모두 사전 margin 0.02에 미달했다. 표본 445명에서 정밀도만 늘린다고 원래 기준이 자동으로
통과하는 모양이 아니다.

선택률도 개봉 연도에 따라 달랐다. 네 편 중 Top-2의 무작위 기준은 50%인데 E5→BPR의 연도별
선택률은 2020년 `49.4~52.2%`, 2021년 `51.8~53.0%`, 2022년 `44.9~47.7%`, 2023년
`42.8~49.0%`였다. 2022~2023 후보를 상대적으로 덜 고르는 방향이 반복됐다. 이는 release-year
slice 기술 통계이며 원인이나 미래 영화 성능을 증명하지 않는다.

## 5. 사용자·영화 구간에서 확인한 한계

### 사용자 구간

프로필 별점 표준편차를 사용자 수가 비슷한 네 구간으로 나눴다. 한국 target에서 E5→BPR 평균은
모든 구간과 여섯 cell에서 양수였지만, 안전 개선이 양수인 사용자 비율은 일부 구간에서 34.8~41.3%에
머물렀다. 최근 target은 일부 구간에서 평균 안전 개선도 0에 가까웠고, Percentile K6의 고분산 Q4는
`-0.00086`이었다. 평균 하나로 모든 사용자에게 이득이라고 말할 수 없다.

### 영화 구간

- 한국 target Top-2 선택의 59.8~61.4%가 상위 10편에 집중됐다. 선택된 고유 영화는 95~101편이었다.
- 최근 target Top-2 선택의 상위 10편 비중은 24.1~25.1%, 고유 영화는 543~555편이었다.
- 한국 target의 2020~2023 후보는 panel 행이 33개뿐이다. 한국 영화와 최신 영화를 동시에 다루는
  교차 구간에 일반화할 근거가 부족하다.

한국 target의 양수 평균은 “한국 영화 전체를 넓게 추천했다”는 뜻이 아니다. 소수 영화 집중과 사용자별
안전성 이질성을 함께 해결해야 한다.

## 6. 최종 판정

별도 채팅의 최종 심사는 `NEXT_STEP_PASS_STOP_CONFIRMATORY`를 반환했다.

1. 이미 본 stage1/stage2 label에서 유리한 K, 인코딩, 모델만 좁혀 다시 검정하면 사후선택이다.
2. 같은 사용자와 label에 새 모델을 적용해도 원래 제품 주장의 독립 확인이 되지 않는다.
3. 같은 데이터에서 허용되는 후속 작업은 효과 크기, ablation, 실패 원인, power planning을 포함한
   탐색·기술 분석뿐이다.
4. 확증은 모델, cell, margin, 다중비교, 중단 규칙을 먼저 잠근 뒤 새 한국 사용자 또는 새 시간 cohort의
   label을 열 때 다시 시작한다.

따라서 이 작업은 한국 영화 추천 문제를 완벽히 해결했다고 결론 내리지 않는다. 다음 두 가지를 해결했다.

- 출시 전 대리평가에서 입력 수, 별점 해석, Top-2 효용과 최악 항목, 목표 영화 구간을 함께 비교할 수
  있는 실험 계약을 만들었다.
- 콘텐츠를 협업 표현에 정렬하는 방향이 한국 target에서 추가 검증할 가치가 있음을 찾았고, 같은
  데이터로 제품 결론을 만들 수 없는 중단선을 정했다.

아직 해결하지 못한 것은 실제 한국 사용자의 만족도, MovieLens 수록 이후 영화의 추천 품질, 제품 K와
입력 UI, production champion이다.

## 7. 새 evidence를 받기 위한 서비스 계측

출시 또는 제한된 사용자 실험에서는 다음 정보를 새 모집단에서 수집해야 한다.

- 온보딩에서 사용자가 평가한 영화와 원래 별점. 서버가 사용자 내 percentile 변환값도 함께 기록한다.
- 추천에 사용한 모델 version, K, 인코딩, 후보 네 편, 노출한 두 편과 순서.
- 상세 조회, 재생 시작, 시청 완료 확인, 노출 뒤 별점과 평가 시각.
- 노출 전에 잠근 한국 제작, 최근 개봉, 인기도, 콘텐츠 결측 구간.

새 label을 보기 전에 K와 모델 후보, 최소 효과, 사용자 수, 중단 규칙을 고정한다. 첫 온라인 평가는
Top-2 중 최저 만족을 primary safety로 유지하고 평균 만족을 utility로 함께 본다. 이 evidence가 생기기
전에는 E5→BPR을 개발 후보로만 유지한다.

## 8. 자기소개서에 사용할 수 있는 문장

### 어려운 문제 해결형

영화 추천 서비스를 개발하며 추천 모델보다 먼저 데이터의 대표성 문제를 확인했습니다. MovieLens의
Rating 78.73%가 유명 외국 영화 proxy에 집중된 반면 한국 제작 영화는 0.33%에 불과했고, 출시 전이라
추천 뒤 시청과 평가를 다시 받는 과정도 반복할 수 없었습니다. 그래서 MovieLens는 사용자별 선호 행동을
복원하는 오프라인 평가에만 쓰고, 한국 영화와 상호작용이 없는 영화는 TMDB 구조 특징과 E5 임베딩으로
표현했습니다. 사용자마다 다른 별점 기준은 개인 내 percentile로 바꾸고, K=0~30과 입력 방식 세 가지를
검증했으며, 실제 화면과 같은 Top-2의 평균 선호와 최저 선호를 동시에 평가했습니다. 콘텐츠 벡터를 BPR
공간에 정렬한 모델은 한국 영화 구간에서 평균 개선을 보였지만 181명의 동시 신뢰구간이 충분히 좁지
않았습니다. 저는 유리한 조건만 다시 고르는 대신 제품 채택을 보류하고, 새 한국 사용자 로그가 생기기
전까지 확증을 중단했습니다. 데이터가 부족한 상황에서 성능을 과장하지 않고, 무엇을 결정할 수 있는지와
다음 검증에 필요한 evidence를 분리한 경험입니다.

### AI 활용형

AI를 추천 점수 생성기로만 사용하지 않았습니다. 다국어 E5로 영화 설명을 384차원 벡터로 만들고,
MovieLens에서 학습한 다섯 BPR 표현 공간에 정렬해 한국 영화로 취향을 전이할 수 있는지 검증했습니다.
동시에 생성형 AI는 독립 심사자 역할로 사용해 실험 계약의 label 누수, 사후선택, resume 재현성, 원본
hash 보존을 반복 점검하게 했습니다. 가설과 통과 기준, Locked Test 금지선은 제가 먼저 고정했습니다.
평균 개선이 나왔어도 통계 정밀도가 부족하자 AI에게 문장을 다듬게 하는 대신 확증 중단 판정을 다시
검토시켰고, 새 한국 사용자 evidence 없이는 제품 성능을 주장하지 않았습니다. AI의 출력보다 검증 가능한
경계와 실패 조건을 설계한 경험입니다.

## 9. 재현 명령

```powershell
.codex-tmp/reproduction/data/Scripts/python.exe scripts/validate_rec_ev_026_design.py
.codex-tmp/reproduction/data/Scripts/python.exe scripts/validate_rec_ev_026_execution.py
.codex-tmp/reproduction/data/Scripts/python.exe scripts/run_rec_ev_026_preflight.py --resume
.codex-tmp/reproduction/data/Scripts/python.exe scripts/run_rec_ev_026_experiment.py --phase run --resume
.codex-tmp/reproduction/data/Scripts/python.exe scripts/audit_rec_ev_026_result.py
.codex-tmp/reproduction/data/Scripts/python.exe scripts/analyze_rec_ev_026_posthoc.py
python -m pytest scripts/tests/test_analyze_rec_ev_026_posthoc.py -q
```

## 참고

- GroupLens, [MovieLens 32M](https://grouplens.org/datasets/movielens/32m/)
- Ji et al., [A Critical Study on Data Leakage in Recommender System Offline Evaluation](https://arxiv.org/abs/2010.11060)
- Kula, [Metadata Embeddings for User and Item Cold-start Recommendations](https://arxiv.org/abs/1507.08439)
- Wei et al., [Contrastive Learning for Cold-Start Recommendation](https://arxiv.org/abs/2107.05315)
- Li et al., [Cross-domain Recommendation via Contrastive Learning](https://arxiv.org/abs/2302.02151)
- Rashid et al., [Getting to Know You: Learning New User Preferences in Recommender Systems](https://arxiv.org/abs/2010.14013)
