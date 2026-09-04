# REC-EV-019C MovieLens 대리 Validation과 구간 분석

> 상태: `PASS_VALIDATION_ANALYSIS_ONLY`
> 실행 범위: Validation only
> Locked Test: 열지 않음
> 제품 정책: 변경하지 않음
> champion: `null`

## 1. 목적

이 실험은 두 문제를 분리해 확인한다.

1. MovieLens가 외국·인기 영화에 치우치고 데이터 수록 이후 영화에는 상호작용이 없을 때, TMDB 구조
   특징과 384차원 E5 콘텐츠 벡터가 영화 표현을 보완할 수 있는가?
2. 실제 추천 후 시청·평가 피드백을 받을 수 없을 때, MovieLens를 학습·평가·검증 환경으로 사용해 초기
   입력 수와 다음 모델 후보를 좁힐 수 있는가?

MovieLens 사용자는 FEELM 사용자가 아니며, 미평가 영화는 싫어요가 아니다. 이번 결과는 실제 만족도나
한국 사용자 성능을 증명하지 않는다.

## 2. 실행과 검증

중단된 LightFM T003 seed 42 안정성 학습은 cache를 유지한 채 아래 명령으로 재개했다.

```powershell
py -3 scripts/run_rec_ev_019c_validation.py --mode validation --role validation --resume
```

실행은 K=5 사용자 1,614명, K=10 사용자 1,479명과 후보 41,625편을 평가했다. 예측
11,662,500행, Validation metric 23,325행을 생성했다. selection lock을 만들었고 Locked Test 파일은
열지 않았다.

```powershell
py -3 scripts/verify_rec_ev_019c_validation.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-019c-validation.json
py -3 scripts/analyze_rec_ev_019c_validation.py
py -3 scripts/verify_rec_ev_019c_analysis.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json
```

## 3. 모델 비교

![모델 비교](../figures/rec-ev-019c-model-comparison.png)

| 모델 | K5 NDCG@10 | K10 NDCG@10 | K5 Harm@2 | K10 Harm@2 |
| --- | ---: | ---: | ---: | ---: |
| B0 인기도 | 0.0402 | 0.0291 | 4.34% | 3.52% |
| B2 ItemKNN | 0.0055 | 0.0022 | 0.43% | 0.54% |
| B4 관측 BPR | 0.0587 | 0.0583 | 3.66% | 3.11% |
| B6 TMDB 구조 | 0.0307 | 0.0218 | 2.66% | 1.15% |
| B7 TMDB 텍스트 | 0.0250 | 0.0131 | 2.42% | 0.81% |
| **B8 LightFM** | **0.0713** | **0.0725** | 3.72% | 2.91% |
| B9 RRF | 0.0615 | 0.0622 | 3.22% | 2.84% |

LightFM의 인기도 대비 paired NDCG@10 차이는 K5 `+0.0311` (95% CI `[0.0243, 0.0383]`),
K10 `+0.0434` (95% CI `[0.0355, 0.0515]`)였다. Harm@2 차이는 K5 `-0.62%p`, K10
`-0.61%p`였지만 두 신뢰구간 모두 0을 포함했다.

TMDB 구조·텍스트 콘텐츠 단독 모델은 두 K 모두 인기도보다 낮았다. 콘텐츠 벡터가 영화 표현을
가능하게 한다는 사실과 MovieLens 관측 선호를 잘 복원한다는 사실은 같지 않았다.

## 4. 사용자별 변화와 입력 조건

![개선·동률·악화 비율](../figures/rec-ev-019c-benefit-harm-rates.png)

| 조건 | 개선 | 동률 | 악화 | LightFM fallback |
| --- | ---: | ---: | ---: | ---: |
| K=5 | 15.49% | 79.12% | 5.39% | 38.79% |
| K=10 | 21.77% | 72.01% | 6.22% | 11.49% |

이력량 Q1~Q4에서 LightFM과 인기도의 NDCG 차이는 모두 양수였다. 다만 입력에 좋아요와 싫어요가 모두
있을 때 K5 `+0.0463`, K10 `+0.0474`가 나타났고, 한쪽 신호만 있을 때는 100% 인기도로 fallback했다.
따라서 현 대리 환경에서는 K10과 양방향 선호 신호를 LightFM 비교 후보의 최소 조건으로 둔다.

## 5. 영화 구간 분석

![영화 인기도·원어 구간](../figures/rec-ev-019c-item-slices.png)

관측 GOOD Top-10 적중은 인기도 Q1~Q3에서 인기도와 LightFM 모두 0이었다. Q4에서만 K5는
4.93%에서 8.61%, K10은 3.75%에서 9.46%로 올랐다.

한국어 원어 영화의 관측 GOOD는 K5 21건, K10 23건뿐이었다. 이 구간의 Top-10 적중은 두 모델 모두
0이었다. 비한국어 원어 구간에서는 K5 4.74%에서 8.29%, K10 3.63%에서 9.16%로 올랐다.

따라서 LightFM의 평균 개선은 대부분 인기 영화와 비한국어 원어 영화에서 발생했다. 이번 실험은 문제 1을
해결하지 못했다.

## 6. seed 안정성과 자원

| 모델·조건 | 5-seed NDCG 평균 | 표준편차 |
| --- | ---: | ---: |
| B4 BPR K5 | 0.06533 | 0.00520 |
| B4 BPR K10 | 0.06191 | 0.00256 |
| B8 LightFM K5 | 0.06689 | 0.00223 |
| B8 LightFM K10 | 0.07543 | 0.00168 |

재개 실행의 wall clock은 6,088.5초, peak RSS는 683,896,832 bytes, artifact 합계는
34,324,269 bytes였다.

## 7. 결정

- 문제 2의 다음 후보는 K10, 좋아요·싫어요 양방향 입력, LightFM T003이다.
- 한쪽 신호만 있거나 feature가 부족하면 B0 인기도로 fallback한다.
- MovieLens 전체 관측 범위 보조 실험을 수행하지 않았으므로 시점 정책은 미결정이다.
- 문제 1은 미해결이다. 목표 도메인 행동 데이터나 독립적인 한국 영화 평가 표본이 필요하다.
- champion은 선택하지 않고 현재 제품 정책을 유지한다.
- Locked Test를 열지 않는다.

## 8. 근거 파일

- Validation manifest: `docs/recommendation/evidence/manifests/rec-ev-019c-validation.json`
- 분석 manifest: `docs/recommendation/evidence/manifests/rec-ev-019c-analysis.json`
- 분석 summary: `outputs/recommendation-evidence/rec-ev-019c/analysis-summary.json`
- 분석 코드: `scripts/analyze_rec_ev_019c_validation.py`
- 분석 검증기: `scripts/verify_rec_ev_019c_analysis.py`
- 결과 장표: `docs/presentation/FEELM-REC-EV-019C-results.pptx`
