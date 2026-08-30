# REC-EV-004 — Hybrid·탐험 관련성 손실 Pareto

> 상태: `COMPLETED_SAMPLED_DIAGNOSTIC`  
> 생성 시각: 2026-08-29T15:15:05.623891+00:00  
> Candidate scope: `SAMPLED_1_POSITIVE_PLUS_199_DETERMINISTIC_NEGATIVES`  
> Full-catalog claim: `NO`

## 1. 결론

Popularity는 채택된 개인화 ranking champion이 아니라 비교 기준선이다. genre-only content와
Popularity+genre Hybrid도 validation/test 후보일 뿐 champion이 아니다. Validation에서 Pareto front와
사전 선언한 relevance loss budget 후보를 잠근 다음 held-out Test를 열었지만, sampled candidate 결과는
full-catalog 채택 근거가 아니므로 탐험 비율·2+1 구성·제품 loss budget을 승인하지 않는다.

- Validation loss budget candidate `0%`: `POPULARITY` / `LOCKED_BEFORE_TEST`
- Validation loss budget candidate `1%`: `EXPLORE_05_ON_POPULARITY` / `LOCKED_BEFORE_TEST`
- Validation loss budget candidate `3%`: `EXPLORE_05_ON_POPULARITY` / `LOCKED_BEFORE_TEST`
- Validation loss budget candidate `5%`: `EXPLORE_05_ON_POPULARITY` / `LOCKED_BEFORE_TEST`

제품 결정: `WAITING_FOR_FULL_CATALOG_AND_PRODUCT_APPROVAL`.

## 2. 고정 조건과 누수 방지

- REC-EV-001 `global-time-v1` Train/Validation/Test checksum을 재검증했다.
- REC-EV-002 `bias_parameters.npz` checksum과 split binding을 검증해 Popularity 충분통계를 재사용했다.
- user profile, genre affinity, Popularity, novelty는 Train만 사용했다.
- 모든 정책은 같은 사용자별 1 positive + 199 deterministic negatives를 사용했다.
- positive는 Train-only `user-ecdf-shrunk-v1 >= 0.7`인 최신 train-known held-out item이다.
- Validation에서 Hybrid α `[0.25, 0.5, 0.75]`, exploration weight `[0.05, 0.1, 0.2, 0.3]`만 비교했다.
- `[0.0, 0.01, 0.03, 0.05]`는 제품 허용치가 아니라 Test 전에 고정한 연구용 후보다.
- Pareto/budget 선택을 확정한 후에만 Test 정책을 평가했다.
- Novelty는 Train item 빈도의 self-information bits, diversity는 Top-10 genre cosine distance,
  catalog coverage는 Train-known universe 대비 고유 Top-10, long-tail은 Train count 상위 20% 밖 노출률이다.

## 3. Validation Pareto 비교

| Policy | NDCG@10 | Recall@10 | Novelty bits | Diversity | Catalog coverage | Long-tail | Genre cal. distance |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CONTENT_GENRE | 0.0594 | 0.1123 | 19.197 | 0.2173 | 45.05% | 65.66% | 0.6888 |
| EXPLORE_05_ON_POPULARITY | 0.3648 | 0.6115 | 15.126 | 0.7503 | 8.99% | 15.42% | 0.5064 |
| EXPLORE_10_ON_POPULARITY | 0.3409 | 0.5890 | 15.792 | 0.8155 | 10.34% | 23.71% | 0.4934 |
| EXPLORE_20_ON_POPULARITY | 0.2063 | 0.4121 | 18.691 | 0.9321 | 12.91% | 55.25% | 0.4966 |
| EXPLORE_30_ON_POPULARITY | 0.0517 | 0.1380 | 21.873 | 0.9845 | 14.09% | 87.61% | 0.5509 |
| HYBRID_CONTENT_25 | 0.2281 | 0.4005 | 16.764 | 0.4633 | 16.25% | 35.43% | 0.6036 |
| HYBRID_CONTENT_50 | 0.1699 | 0.2809 | 18.367 | 0.3397 | 23.20% | 53.36% | 0.6514 |
| HYBRID_CONTENT_75 | 0.1300 | 0.1978 | 19.715 | 0.2639 | 32.12% | 67.48% | 0.6788 |
| POPULARITY | 0.3673 | 0.6131 | 14.922 | 0.7128 | 8.52% | 12.41% | 0.5164 |

Pareto front: `CONTENT_GENRE, EXPLORE_05_ON_POPULARITY, EXPLORE_10_ON_POPULARITY, EXPLORE_20_ON_POPULARITY, EXPLORE_30_ON_POPULARITY, HYBRID_CONTENT_25, HYBRID_CONTENT_50, HYBRID_CONTENT_75, POPULARITY`.
Calibration distance는 낮을수록 좋고 나머지 탐험 지표는 높을수록 좋다. 단일 합성 KPI로 합치지 않았다.

## 4. Held-out Test

| Policy | NDCG@10 | Recall@10 | Novelty bits | Diversity | Catalog coverage | Long-tail |
| --- | --- | --- | --- | --- | --- | --- |
| EXPLORE_05_ON_POPULARITY | 0.3719 | 0.6182 | 15.188 | 0.7506 | 8.51% | 15.77% |
| POPULARITY | 0.3779 | 0.6230 | 14.976 | 0.7097 | 8.08% | 12.63% |

Test는 선택된 budget 후보와 Popularity만 보고하며 Test 결과로 α·weight·loss budget을 다시 고르지 않았다.

- `0%` locked candidate `POPULARITY`: Test relative NDCG loss `0.00%` / within budget `TRUE`
- `1%` locked candidate `EXPLORE_05_ON_POPULARITY`: Test relative NDCG loss `1.59%` / within budget `FALSE`
- `3%` locked candidate `EXPLORE_05_ON_POPULARITY`: Test relative NDCG loss `1.59%` / within budget `TRUE`
- `5%` locked candidate `EXPLORE_05_ON_POPULARITY`: Test relative NDCG loss `1.59%` / within budget `TRUE`

특히 Validation에서 1% 후보로 잠근 정책은 Test에서 1% budget을 벗어났다. 이 실패를 보고한 뒤
3%로 제품 budget을 바꾸지 않으며, budget 자체는 계속 미승인이다. paired user NDCG 차이와 1,000회
bootstrap 95% CI는 manifest의 `test_paired_ndcg_vs_popularity`에 기록했다.

## 5. 사용자 segment·K·coverage 회귀

| Policy | History segment | Users | NDCG@10 | Recall@10 | Novelty |
| --- | --- | --- | --- | --- | --- |
| POPULARITY | K20_49 | 343 | 0.4552 | 0.7318 | 14.583 |
| POPULARITY | K50_99 | 312 | 0.4375 | 0.7083 | 14.493 |
| POPULARITY | K100_PLUS | 3345 | 0.3644 | 0.6039 | 15.062 |
| EXPLORE_05_ON_POPULARITY | K20_49 | 343 | 0.4411 | 0.7201 | 14.787 |
| EXPLORE_05_ON_POPULARITY | K50_99 | 312 | 0.4298 | 0.7083 | 14.725 |
| EXPLORE_05_ON_POPULARITY | K100_PLUS | 3345 | 0.3594 | 0.5994 | 15.273 |

| K | Policy | NDCG@10 | Recall@10 | Novelty | Catalog coverage |
| --- | --- | --- | --- | --- | --- |
| K3 | CONTENT_GENRE | 0.0512 | 0.1055 | 18.618 | 41.09% |
| K3 | HYBRID_CONTENT_25 | 0.2442 | 0.4268 | 16.661 | 15.72% |
| K5 | CONTENT_GENRE | 0.0473 | 0.0965 | 19.242 | 43.47% |
| K5 | HYBRID_CONTENT_25 | 0.2329 | 0.4062 | 16.926 | 16.08% |
| K10 | CONTENT_GENRE | 0.0451 | 0.0920 | 19.458 | 43.80% |
| K10 | HYBRID_CONTENT_25 | 0.2240 | 0.3927 | 16.982 | 16.03% |
| K20 | CONTENT_GENRE | 0.0460 | 0.0940 | 19.509 | 42.55% |
| K20 | HYBRID_CONTENT_25 | 0.2286 | 0.4057 | 16.972 | 16.09% |

- Train-known movies: `50,977`
- movies.csv genre available: `47,328`
- Validation/Test candidate genre row coverage: `92.80%` / `92.77%`
- non-zero content user-profile coverage: `99.13%`

자연 warm-user cohort만 포함하므로 K0/new-user 성능은 측정하지 않았다. genre metadata가 없는 candidate는
content contribution 0으로 fail-safe 처리되며 응답 coverage와 genre calibration coverage를 분리했다.
K 증가가 relevance를 단조 개선하지 않았으므로 onboarding 품질 근거로 사용하지 않는다. positive-item
popularity 구간의 상세 회귀는 manifest와 `aggregate-results.json`에 있다.

## 6. 실패 사례

manifest의 failure cases는 raw user/movie ID 없이 Popularity 대비 held-out positive rank가 가장 크게
후퇴한 5개 사례의 history segment와 rank 변화만 남긴다. 이 회귀 때문에 전체 평균의 novelty 증가만으로
정책을 채택하지 않는다.

## 7. REC-EV-006 provenance

`rec-ev-004-reason-provenance.json`은 실제 평가 점수의 `BAYESIAN_POPULARITY`, `GENRE_AFFINITY`,
`NOVELTY_PRIOR`, `MARGINAL_GENRE_DIVERSITY` 출처와 reason faithfulness Gate를 구조화한다. 이는
REC-EV-006 입력일 뿐 reason UI·표시 개수·개인화 설명 문구 승인이 아니다.

## 8. 재현

```powershell
py -3.12 scripts/recommendation_exploration_pareto.py `
  --split-manifest docs/recommendation/evidence/manifests/global-time-v1.json `
  --baseline-manifest docs/recommendation/evidence/manifests/rec-ev-002.json `
  --archive C:\higher\projects\MM\data\raw\ml-32m.zip `
  --output-dir outputs/recommendation-evidence/rec-ev-004 `
  --tracked-result docs/recommendation/evidence/results/rec-ev-004-aggregate.json `
  --manifest docs/recommendation/evidence/manifests/rec-ev-004.json `
  --evidence docs/recommendation/evidence/REC-EV-004-exploration-pareto.md `
  --reason-provenance docs/recommendation/evidence/manifests/rec-ev-004-reason-provenance.json

py -3.12 scripts/verify_recommendation_exploration_pareto.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-004.json
```

## 9. 한계

- sampled negative 순위는 full-catalog 순위와 정책 우열이 바뀔 수 있다.
- MovieLens 미평가는 싫어요가 아니며 novelty/diversity는 탐험 만족을 직접 측정하지 않는다.
- genre-only content는 TMDB text/keyword Hybrid를 대표하지 않는다.
- 개인 ranking champion, 2+1 탐험 구성, 제품 weight/loss budget, reason UI는 모두 미승인이다.
