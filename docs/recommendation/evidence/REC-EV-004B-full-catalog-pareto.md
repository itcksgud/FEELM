# REC-EV-004B Full-catalog Hybrid·exploration Pareto revalidation

Status: `COMPLETED_FULL_CATALOG_EVIDENCE` (offline evidence only; no product policy approval)

## 결론

- `global-time-v1`의 REC-EV-004와 동일한 warm cohort에서 Train-known 영화 50,977개를 매 사용자마다 모두 score scan했다.
- 사용자의 Train-seen 영화는 Top-500 전에 제외했고 held-out positive는 후보에 강제 주입하지 않았다.
- Validation이 정책/프로토콜과 hash를 먼저 잠갔고 Test CLI는 그 hash와 Validation artifact checksum을 검증한 뒤에만 Test를 열었다.
- 이는 REC-EV-004의 sampled 결과를 재명명한 것이 아닌 별도 `REC-EV-004B` evidence다.
- `EXPLORE_05_ON_POPULARITY`는 Popularity와 같은 Top-500을 재정렬하므로 candidate recall@500은 0.3080으로 같지만, Test NDCG@10은 0.009382→0.005113(상대 손실 약 45.5%)이고 paired CI는 `[-0.006604,-0.002002]`다. Pareto 목록에 있다는 사실은 채택 근거가 아니다.
- 제품 탐험 weight, 2+1 구성, 개인화 ranking champion은 승인하지 않는다.

## Held-out Test (4000 users)

| Policy | candidate recall@500 | NDCG@10 | Recall@10 | Novelty bits | Diversity | List genre coverage | Pair genre coverage | Catalog coverage | Long-tail exposure |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| POPULARITY | 0.308000 | 0.009382 | 0.022500 | 11.574434 | 0.730413 | 0.982475 | 0.965372 | 0.001726 | 0.000000 |
| CONTENT_GENRE | 0.036000 | 0.000955 | 0.001750 | 15.503606 | 0.044983 | 1.000000 | 1.000000 | 0.089119 | 0.228375 |
| HYBRID_CONTENT_25 | 0.169500 | 0.007435 | 0.012750 | 12.659010 | 0.156260 | 1.000000 | 1.000000 | 0.014771 | 0.005000 |
| EXPLORE_05_ON_POPULARITY | 0.308000 | 0.005113 | 0.013000 | 14.331556 | 0.777470 | 0.901850 | 0.803722 | 0.002021 | 0.000000 |

`candidate recall@500`은 사용자별 단일 held-out positive가 자연스럽게 Top-500에 들어온 비율이며 Top-10 relevance와 같은 지표가 아니다.

## 범위와 Gate

- Candidate: full 50,977 Train-known universe → seen exclusion → deterministic Top-500 → Top-10.
- genre 미상 zero-vector는 selection diversity 보상을 받지 않고, metric에서도 미상 genre가 포함된 pair를 diversity 0으로 보수 처리한다. list/pair genre coverage는 별도 보고한다.
- 비교 정책은 REC-EV-004에서 잠긴 `POPULARITY`, `CONTENT_GENRE`, `HYBRID_CONTENT_25`, `EXPLORE_05_ON_POPULARITY`뿐이다. 새 탐색은 하지 않았다.
- full catalog는 MovieLens Train-known 범위를 뜻하며 서비스 Catalog 전체나 production coverage를 뜻하지 않는다.
- tracked aggregate/lock은 raw user/movie ID를 포함하지 않는다. 실패 사례는 순번과 segment/rank만 남겼다.
- 측정 자원: 35.651s, observed peak RSS 1762639872 bytes, 203908000 score evaluations.

## 재현

```powershell
$env:PYTHONPATH=(Resolve-Path 'scripts').Path
py -3.12 scripts/recommendation_exploration_full_catalog.py validation --split-manifest docs/recommendation/evidence/manifests/global-time-v1.json --baseline-manifest docs/recommendation/evidence/manifests/rec-ev-002.json --rec-ev-004-manifest docs/recommendation/evidence/manifests/rec-ev-004.json --archive C:\higher\projects\MM\data\raw\ml-32m.zip --validation-result docs/recommendation/evidence/results/rec-ev-004b-validation.json --protocol-lock docs/recommendation/evidence/results/rec-ev-004b-protocol-lock.json
py -3.12 scripts/recommendation_exploration_full_catalog.py test --split-manifest docs/recommendation/evidence/manifests/global-time-v1.json --baseline-manifest docs/recommendation/evidence/manifests/rec-ev-002.json --rec-ev-004-manifest docs/recommendation/evidence/manifests/rec-ev-004.json --archive C:\higher\projects\MM\data\raw\ml-32m.zip --validation-result docs/recommendation/evidence/results/rec-ev-004b-validation.json --protocol-lock docs/recommendation/evidence/results/rec-ev-004b-protocol-lock.json --test-result docs/recommendation/evidence/results/rec-ev-004b-test.json --manifest docs/recommendation/evidence/manifests/rec-ev-004b.json --evidence docs/recommendation/evidence/REC-EV-004B-full-catalog-pareto.md
py -3.12 scripts/verify_recommendation_exploration_full_catalog.py --manifest docs/recommendation/evidence/manifests/rec-ev-004b.json
```
