# REC-EV-020P — 상위 2편 추천 평가 사전검사

> 상태: `020P-A PASS / 020P-B BLOCKED`
> 범위: MovieLens Validation 사용자만 사용
> 제품 반영: 없음
> Locked Test 성능 확인: 하지 않음

## 한 줄 결론

20편 평가판으로 “싫어할 영화를 상위 2편에 띄웠는가”와 “좋아할 영화가 있었는데도 놓쳤는가”를
채점할 사용자 수는 충분하다. 다만 새 기준으로 비교할 인기도 기준선과 개인화 후보의 예측 파일이 아직
고정되지 않았으므로, 어느 모델이 더 좋은지는 아직 계산하지 않았다.

## 실제 확인한 숫자

| 항목 | 결과 |
| --- | ---: |
| 스캔한 전체 MovieLens 평점 | 32,000,204개 |
| Base Train으로 집계한 평점 | 12,795,564개 |
| Validation 평점 | 3,216,490개 |
| Validation 사용자 | 20,271명 |
| 중복 사용자-영화 평점 | 0개 |
| 고정 무작위 순서 | 20개 seed |
| 평가판 크기 민감도 | 10편 / 20편 / 30편 |
| 원본 사용자 번호 출력 | 없음 |

![K별 평가 가능 사용자와 채점 가능 비율](../figures/top2-k-eligibility.png)

20편 평가판 기준 결과는 다음과 같다.

| 사용자 입력 K | 평가 가능 사용자 | 좋은 영화 놓침 채점 가능 사용자 | 채점 가능 비율 |
| ---: | ---: | ---: | ---: |
| 0 | 20,271 | 19,864 | 97.99% |
| 1 | 19,734 | 19,360 | 98.10% |
| 3 | 18,917 | 18,577 | 98.20% |
| 5 | 18,242 | 17,922 | 98.25% |
| 10 | 16,795 | 16,516 | 98.34% |
| 20 | 14,644 | 14,415 | 98.44% |
| 30 | 12,902 | 12,724 | 98.62% |
| 50 | 10,367 | 10,255 | 98.92% |

## 무엇을 뜻하나

- K=10을 쓰면 Validation 사용자 16,795명으로 모델을 비교할 수 있다.
- 그중 16,516명은 20편 안에 사용자가 상대적으로 좋아한 영화가 최소 한 편 있어 “놓침”을 채점할 수 있다.
- K가 커질수록 이력이 짧은 사용자가 빠지므로 전체 사용자 수는 줄어든다.
- 채점 가능 비율이 높은 것은 **모델 성능이 높다는 뜻이 아니다.** 정답을 비교할 조건이 충분하다는 뜻이다.

## 아직 계산하지 않은 것

`REC-EV-020P-B`에는 같은 사용자·같은 20편에서 아래 두 결과가 모두 필요하다.

1. 현재 기준선인 Bayesian 인기도 추천의 상위 2편
2. 결과를 보기 전에 고정한 개인화 후보 하나의 상위 2편

이 두 예측 파일과 SHA-256이 없으므로 harm·miss 차이, 필요한 Test 사용자 수, 통계 구간은 만들지 않았다.
숫자를 임의로 채우지 않고 `BLOCKED_MISSING_LOCKED_BASELINE_CHALLENGER_ARTIFACTS`로 남겼다.

## 재현 명령

```powershell
py -3 scripts/recommendation_top2_v4_preflight.py --protocol docs/recommendation/protocols/rec-eval-top2-v4.json --role validation
py -3 scripts/validate_recommendation_top2_v4_preflight.py --manifest docs/recommendation/evidence/manifests/rec-ev-020p.json
```

원본 대용량 Parquet은 `outputs/` 아래에 있고 Git에는 올리지 않는다. 체크섬과 실행 상태는
`docs/recommendation/evidence/manifests/rec-ev-020p.json`에 남긴다.
