# REC-EV-005 — MovieLens 합성 파티 집계 정책 비교

> 상태: `COMPLETED_OFFLINE_EVIDENCE`  
> 생성 시각: 2026-08-29T14:47:46.137390+00:00  
> 제품 정책 승인: `NO` — PARTY_BALANCED_V1·공개 API·UI를 승인하지 않음  
> 실제 파티 만족도: `NOT_OBSERVED`

## 1. 결론

Held-out Test에서 Balanced의 Average 대비 평균 효용 차이는 -0.0013 (95% CI [-0.0037, +0.0007]), 최저 효용 차이는 +0.0005 ([-0.0035, +0.0045]), 격차 차이는 -0.0042 ([-0.0116, +0.0024])로 세 CI가 모두 0을 포함했다. 또한 4인 Test 공통평가 coverage는 0.69%~1.02%뿐이다. 따라서 Balanced는 비교 후보일 뿐 개선 근거나 제품 PARTY_BALANCED_V1 승인 근거가 아니며 실제 파티 만족도도 관측하지 않았다.

이 결과는 MovieLens 사용자를 묶고 모든 구성원이 실제 평가한 후보에서 측정한 오프라인 진단이다.
파티가 함께 영화를 골랐거나 만족했다는 관측이 없으므로 실제 파티 만족도·온라인 성공률로 부르지 않는다.

## 2. 누수 방지와 후보 경계

- 취향 유사도·예측 효용: REC-EV-002 Train-only ALS factor와 Train rating profile
- 그룹 구간·Balanced threshold/weight 선택: Validation만 사용
- 최종 수치: 고정 파라미터로 Test 평가
- Test 평점 값은 파라미터 선택에 사용하지 않음
- 후보: 모든 구성원이 해당 split에서 평가한 known item 중 안정 hash 최대 20개
- 추천 목록: 후보에서 Top-3
- 미평가 영화는 싫어요로 만들지 않으며 full-catalog coverage를 주장하지 않음

## 3. Validation에서 선택한 Balanced 후보

```text
score = mean(relativeUtility)
      - floorWeight × max(0, floor - min(relativeUtility))
      - gapWeight × (max(relativeUtility) - min(relativeUtility))
```

- floor: `0.5`
- floorWeight: `0.5`
- gapWeight: `0.25`
- validation predicted relevance-loss budget: `0.03`
- validation observed mean-loss budget: `0.02`

이 값은 합성 Validation에서 고른 **비교 후보**이며 제품 공정성 선호가 아니다. 평균 효용을 얼마나
포기할지는 REC-PD-005에서 제품 소유자가 별도로 결정해야 한다.

## 4. Held-out Test 전체 결과

| Policy | Parties | 평균 효용 | 최저 구성원 효용 | 구성원 격차 | predicted relevance 손실 |
| --- | ---: | ---: | ---: | ---: | ---: |
| AVERAGE | 270 | 0.5610 | 0.3944 | 0.3235 | 0.0000 |
| LEAST_MISERY | 270 | 0.5558 | 0.3907 | 0.3165 | 0.0073 |
| MOST_HAPPINESS | 270 | 0.5525 | 0.3862 | 0.3237 | 0.0117 |
| BALANCED | 270 | 0.5596 | 0.3949 | 0.3192 | 0.0017 |

`최저 구성원 효용`은 각 구성원의 Top-N 평균 효용 중 최솟값을 party별 계산한 뒤 macro 평균한다.
`격차`는 같은 구성원 평균의 max-min이다. relative utility는 0~1의 Train rating-style
mid-rank ECDF이며 보편적 만족 확률이 아니다.

### Average 대비 paired bootstrap 차이 (95% CI)

| Policy | Δ 평균 효용 | Δ 최저 효용 | Δ 격차 |
| --- | --- | --- | --- |
| LEAST_MISERY | -0.0052 [-0.0088, -0.0013] | -0.0037 [-0.0094, +0.0021] | -0.0070 [-0.0160, +0.0012] |
| MOST_HAPPINESS | -0.0085 [-0.0147, -0.0028] | -0.0082 [-0.0164, +0.0000] | +0.0002 [-0.0107, +0.0100] |
| BALANCED | -0.0013 [-0.0037, +0.0007] | +0.0005 [-0.0035, +0.0045] | -0.0042 [-0.0116, +0.0024] |

Balanced의 세 CI가 모두 0을 포함하므로 Average보다 평균·최저 효용 또는 격차를
개선했다는 근거가 아니다. Validation에서 선택된 비교 후보로만 유지한다.

## 5. 2/3/4명 × 취향 그룹

| 인원 | 그룹 | Policy | 평균 효용 | 최저 효용 | 격차 | relevance 손실 |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 2 | DISSIMILAR | AVERAGE | 0.6045 | 0.4961 | 0.2168 | 0.0000 |
| 2 | DISSIMILAR | BALANCED | 0.6020 | 0.4948 | 0.2143 | 0.0022 |
| 2 | DISSIMILAR | LEAST_MISERY | 0.5912 | 0.4824 | 0.2177 | 0.0071 |
| 2 | DISSIMILAR | MOST_HAPPINESS | 0.6051 | 0.4885 | 0.2332 | 0.0132 |
| 2 | MIDDLE | AVERAGE | 0.5839 | 0.4726 | 0.2226 | 0.0000 |
| 2 | MIDDLE | BALANCED | 0.5850 | 0.4748 | 0.2205 | 0.0000 |
| 2 | MIDDLE | LEAST_MISERY | 0.5846 | 0.4748 | 0.2197 | 0.0012 |
| 2 | MIDDLE | MOST_HAPPINESS | 0.5827 | 0.4686 | 0.2282 | 0.0104 |
| 2 | SIMILAR | AVERAGE | 0.5831 | 0.4803 | 0.2055 | 0.0000 |
| 2 | SIMILAR | BALANCED | 0.5831 | 0.4803 | 0.2055 | 0.0002 |
| 2 | SIMILAR | LEAST_MISERY | 0.5849 | 0.4874 | 0.1950 | 0.0049 |
| 2 | SIMILAR | MOST_HAPPINESS | 0.5746 | 0.4764 | 0.1965 | 0.0054 |
| 3 | DISSIMILAR | AVERAGE | 0.5372 | 0.3564 | 0.3715 | 0.0000 |
| 3 | DISSIMILAR | BALANCED | 0.5306 | 0.3532 | 0.3479 | 0.0063 |
| 3 | DISSIMILAR | LEAST_MISERY | 0.5242 | 0.3400 | 0.3558 | 0.0134 |
| 3 | DISSIMILAR | MOST_HAPPINESS | 0.5266 | 0.3551 | 0.3572 | 0.0133 |
| 3 | MIDDLE | AVERAGE | 0.5351 | 0.3477 | 0.3645 | 0.0000 |
| 3 | MIDDLE | BALANCED | 0.5313 | 0.3448 | 0.3604 | 0.0007 |
| 3 | MIDDLE | LEAST_MISERY | 0.5245 | 0.3384 | 0.3530 | 0.0056 |
| 3 | MIDDLE | MOST_HAPPINESS | 0.5013 | 0.3056 | 0.3783 | 0.0152 |
| 3 | SIMILAR | AVERAGE | 0.5276 | 0.3559 | 0.3168 | 0.0000 |
| 3 | SIMILAR | BALANCED | 0.5276 | 0.3559 | 0.3168 | 0.0000 |
| 3 | SIMILAR | LEAST_MISERY | 0.5171 | 0.3428 | 0.3180 | 0.0093 |
| 3 | SIMILAR | MOST_HAPPINESS | 0.5221 | 0.3630 | 0.3037 | 0.0125 |
| 4 | DISSIMILAR | AVERAGE | 0.5261 | 0.3025 | 0.4357 | 0.0000 |
| 4 | DISSIMILAR | BALANCED | 0.5216 | 0.3011 | 0.4327 | 0.0039 |
| 4 | DISSIMILAR | LEAST_MISERY | 0.5180 | 0.2938 | 0.4324 | 0.0114 |
| 4 | DISSIMILAR | MOST_HAPPINESS | 0.5214 | 0.2770 | 0.4517 | 0.0119 |
| 4 | MIDDLE | AVERAGE | 0.5778 | 0.3666 | 0.3928 | 0.0000 |
| 4 | MIDDLE | BALANCED | 0.5812 | 0.3673 | 0.3949 | 0.0002 |
| 4 | MIDDLE | LEAST_MISERY | 0.5845 | 0.3721 | 0.3920 | 0.0077 |
| 4 | MIDDLE | MOST_HAPPINESS | 0.5753 | 0.3694 | 0.3885 | 0.0102 |
| 4 | SIMILAR | AVERAGE | 0.5735 | 0.3715 | 0.3849 | 0.0000 |
| 4 | SIMILAR | BALANCED | 0.5742 | 0.3824 | 0.3800 | 0.0020 |
| 4 | SIMILAR | LEAST_MISERY | 0.5730 | 0.3847 | 0.3650 | 0.0051 |
| 4 | SIMILAR | MOST_HAPPINESS | 0.5629 | 0.3723 | 0.3755 | 0.0131 |

## 6. 공통평가 후보 coverage

| Split | 인원 | 그룹 | 추출 시도 | 평가 가능 | 선택 party | 평가 가능 coverage |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| validation | 2 | DISSIMILAR | 20000 | 18196 | 30 | 90.98% |
| validation | 2 | MIDDLE | 20000 | 18811 | 30 | 94.06% |
| validation | 2 | SIMILAR | 20000 | 19204 | 30 | 96.02% |
| validation | 3 | DISSIMILAR | 20000 | 5735 | 30 | 28.68% |
| validation | 3 | MIDDLE | 20000 | 5738 | 30 | 28.69% |
| validation | 3 | SIMILAR | 20000 | 6680 | 30 | 33.40% |
| validation | 4 | DISSIMILAR | 20000 | 790 | 30 | 3.95% |
| validation | 4 | MIDDLE | 20000 | 743 | 30 | 3.72% |
| validation | 4 | SIMILAR | 20000 | 852 | 30 | 4.26% |
| test | 2 | DISSIMILAR | 18207 | 13582 | 30 | 74.60% |
| test | 2 | MIDDLE | 20791 | 16382 | 30 | 78.79% |
| test | 2 | SIMILAR | 21002 | 17055 | 30 | 81.21% |
| test | 3 | DISSIMILAR | 17848 | 1968 | 30 | 11.03% |
| test | 3 | MIDDLE | 20527 | 2745 | 30 | 13.37% |
| test | 3 | SIMILAR | 21625 | 2649 | 30 | 12.25% |
| test | 4 | DISSIMILAR | 16984 | 118 | 30 | 0.69% |
| test | 4 | MIDDLE | 20776 | 212 | 30 | 1.02% |
| test | 4 | SIMILAR | 22240 | 219 | 30 | 0.98% |

## 7. 순위가 뒤집힌 실제 MovieLens 사례

### raw_average_vs_relative_utility

- party: `TEST-S2-DIS-019` / 2명 / DISSIMILAR
- raw_average_preferred: **Devil Wears Prada, The (2006)** — raw 평균 4.000, relative utility 평균 0.375, 구성원 rating [4.0, 4.0]
- relative_utility_preferred: **Shrek 2 (2004)** — raw 평균 2.750, relative utility 평균 0.411, 구성원 rating [0.5, 5.0]

### average_vs_balanced

- party: `TEST-S2-DIS-029` / 2명 / DISSIMILAR
- average_top: **Thing, The (1982)** — raw 평균 2.750, relative utility 평균 0.410, 구성원 rating [1.5, 4.0]
- balanced_top: **Sully (2016)** — raw 평균 3.750, relative utility 평균 0.594, 구성원 rating [4.0, 3.5]

### most_happiness_vs_least_misery

- party: `TEST-S2-DIS-023` / 2명 / DISSIMILAR
- most_happiness_top: **Specialist, The (1994)** — raw 평균 3.000, relative utility 평균 0.401, 구성원 rating [3.5, 2.5]
- least_misery_top: **First Blood (Rambo: First Blood) (1982)** — raw 평균 4.500, relative utility 평균 0.925, 구성원 rating [4.0, 5.0]

사용자·영화 raw ID는 추적 문서와 manifest에 저장하지 않았다. 제목과 익명 party/member 위치만
남겨 rating-style 정규화와 정책 선택이 순서를 바꾼 실제 관측 사례를 재검토할 수 있게 했다.

## 8. 재현

```powershell
py -3.12 scripts/recommendation_party_policy.py `
  --split-dir outputs/recommendation-evidence/global-time-v1 `
  --split-manifest docs/recommendation/evidence/manifests/global-time-v1.json `
  --baseline-manifest docs/recommendation/evidence/manifests/rec-ev-002.json `
  --output-dir outputs/recommendation-evidence/rec-ev-005 `
  --manifest docs/recommendation/evidence/manifests/rec-ev-005.json `
  --evidence docs/recommendation/evidence/REC-EV-005-party-policy.md

py -3.12 scripts/verify_recommendation_party_policy.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-005.json
```

## 9. Evidence gap와 결정 경계

- MovieLens에는 파티 생성·투표·선택·공동 감상·만족도 데이터가 없다.
- 공통평가 후보만 사용해 observation bias가 크며 full-catalog 정책 coverage가 아니다.
- 특히 4인 Test 공통평가 평가 가능 coverage는 약 0.7%~1.0%에 불과하다. 이 심각한
  선택 편향 때문에 4인 일반 파티로 결과를 외삽할 수 없다.
- 구성원이 여러 합성 party에 재사용되므로 party row를 완전히 독립 표본으로 해석하지 않는다.
- REC-EV-002 ALS는 sampled ranking에서 Popularity보다 약했다. 집계 결과가 개인 추천 코어의
  약점을 상쇄하거나 PARTY_BALANCED_V1 채택을 뜻하지 않는다.
- 실제 FEELM party 로그와 사용자 결정 없이는 threshold·weight·손실 예산을 승인하지 않는다.

따라서 이 문서는 REC-PD-005 판단 자료를 채우지만 `party_aggregation` champion은 계속 null이고,
공개 API·UI 구현도 시작하지 않는다.
