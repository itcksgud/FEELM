# REC-EV-006 — 구조화 추천 이유 coverage·faithfulness

> 상태: `COMPLETED_OFFLINE_EVIDENCE`  
> Source: `REC-EV-004` actual Test scoring contributions  
> Reason UI approved: `NO`

## 1. 결론

40,000개 sampled Test recommendation position에서 실제 score contribution과
single-feature ablation rank effect를 함께 검사했다. feature가 존재하거나 점수 기여가 양수라는 이유만으로
표시 가능하다고 하지 않는다. active policy, 양의 contribution, rank/position effect, provenance version,
민감정보 부재를 모두 만족한 경우만 `EMITTABLE_CANDIDATE`이며, 이는 REC-EV-008 화면 비교 입력이지
실제 UI 표시 승인이 아니다.

## 2. 이유별 coverage

| Reason | Positive contribution | Emittable candidate | Blocked: no rank effect | Blocked: inactive |
| --- | --- | --- | --- | --- |
| `GENRE_AFFINITY` | 0.00% | 0.00% | 0 | 40,000 |
| `LESS_POPULAR_DISCOVERY` | 100.00% | 24.31% | 30,275 | 0 |
| `LIST_DIVERSITY` | 78.86% | 59.98% | 7,554 | 0 |
| `POPULARITY_BASELINE` | 100.00% | 99.98% | 7 | 0 |

`GENRE_AFFINITY`는 REC-EV-004의 선택 정책이 Popularity 기반이므로 차단한다. novelty/diversity도 실제
ablation에서 위치 효과가 없는 행은 `NO_RANK_EFFECT`로 차단한다.

## 3. Ablation

| Variant | NDCG@10 | Novelty bits | Diversity | ΔNDCG vs full |
| --- | --- | --- | --- | --- |
| `BASE_ONLY` | 0.3779 | 14.976 | 0.7097 | +0.0060 |
| `FULL` | 0.3719 | 15.188 | 0.7506 | +0.0000 |
| `WITHOUT_DIVERSITY` | 0.3748 | 15.069 | 0.7117 | +0.0029 |
| `WITHOUT_NOVELTY` | 0.3774 | 15.032 | 0.7428 | +0.0055 |
| `WITHOUT_POPULARITY` | 0.0005 | 23.605 | 1.0000 | -0.3715 |

이 수치는 `SAMPLED_1_POSITIVE_PLUS_199_DETERMINISTIC_NEGATIVES` 범위이며 full-catalog나 온라인 설명 만족 근거가 아니다.

## 4. Typed contract와 실패 fixture

- `rec-ev-006-reason-contract.json`: `EMITTABLE_CANDIDATE`와 `BLOCKED` 상태, 차단 code, 금지 필드
- `rec-ev-006-failure-fixtures.json`: inactive feature, zero contribution, no rank effect, bad provenance,
  sensitive evidence를 fail-closed 검증
- raw MovieLens user/movie ID, Rating row, feature vector, token, path는 tracked artifact에 없다.

## 5. 재현

```powershell
py -3.12 scripts/recommendation_reason_faithfulness.py `
  --rec-ev-004-manifest docs/recommendation/evidence/manifests/rec-ev-004.json `
  --output-dir outputs/recommendation-evidence/rec-ev-006 `
  --tracked-result docs/recommendation/evidence/results/rec-ev-006-aggregate.json `
  --manifest docs/recommendation/evidence/manifests/rec-ev-006.json `
  --evidence docs/recommendation/evidence/REC-EV-006-reason-faithfulness.md `
  --typed-contract docs/recommendation/evidence/manifests/rec-ev-006-reason-contract.json `
  --failure-fixtures docs/recommendation/evidence/manifests/rec-ev-006-failure-fixtures.json

py -3.12 scripts/verify_recommendation_reason_faithfulness.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-006.json
```

## 6. 남은 Gate

UI 문구, 이유 표시 개수, 펼치기, reason ordering은 REC-EV-008에서 비교하고 제품 소유자가 결정한다.
`EMITTABLE_CANDIDATE`를 공개 reason으로 자동 승격하지 않는다.
