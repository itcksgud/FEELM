# REC-EV-019F independent temporal routing preregistration

상태: `APPROVED_FOR_PREREGISTERED_VALIDATION_ONLY`

## 1. 질문과 증거 한계

REC-EV-019F는 REC-EV-019E에서 동결한 no-retune routing rule을 같은 Validation 사용자들의 더 뒤쪽 source row와 새 future window에서 다시 확인한다. 독립성의 단위는 **사용자**가 아니라 **source row와 temporal window**다. 따라서 성공하더라도 최대 상태는 `PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION`이며, 한국 사용자·한국 영화·최신 영화 문제를 해결했다는 근거가 아니다.

Locked Test와 제품 정책 경로는 열거나 변경하지 않는다. 모든 상태와 산출물은 `locked_test_used=false`, `champion=null`, `product_policy_updated=false`를 유지한다. REC-EV-019C의 frozen B8 LightFM T003 seed 17 cache와 B0 fallback만 사용하며 fit, refit, threshold 변경, route/candidate search를 금지한다.

## 2. 새 temporal episode

원천은 `global-time-v1/validation.parquet` 중 고정 사용자 bucket 50..59다. 각 사용자의 REC-EV-019A K10 10번째 선택 source position 뒤에 있던 원본 future observation 10개까지 모두 지난 다음 row, 즉 `old_k10_position + 11`에서 tail을 시작한다.

tail에서 사용자 상대효용 누적 상태를 0으로 reset한다. Base Train rating 분포의 고정 midrank ECDF, shrinkage 10, like `>=0.15`, dislike `<=-0.15`로 첫 10개 non-neutral label을 새 K10 prefix로 선택한다. 그 10번째 선택 직후의 원본 observation 정확히 10개가 fresh future window다. 새 prefix와 window에는 `user_key + zero-based source_position` row identity를 보존한다. 기존 019A K10 prefix와 evaluation source row와의 overlap은 반드시 0이어야 한다.

REC-EV-019C K5/K10 tuning panel 합집합은 477명으로 이미 관측되었다. 이 사용자는 fresh future label을 계산하기 전에 제외한다. 새 K10 prefix와 10-row window가 모두 존재하는 structural cohort를 먼저 고정한 뒤, future positive 3개 이상이고 고정 후보 41,625편 안의 positive가 1개 이상인 사용자만 strict cohort로 삼는다.

독립 감사에서 structural 1,021명, strict 802명을 이미 관측했다. strict 사용자 가운데 기존 019A K10 strict cohort와 겹치는 사용자는 629명, 그 밖은 173명이며, 그 173명 중 019A Validation 산출물에 전혀 없던 사용자는 31명으로 관측했다. 이 수치는 blind 기대가 아니며 drift 탐지용이다. 실제 실행 값이 다르면 기대값을 강제하거나 포장하지 않고 fail closed한다.

## 3. 동결 모델과 routing

고정 후보는 41,625편이다. 모든 strict 사용자에 대해 새 prefix first 5와 first 10으로 각각 target user를 새로 fold-in하고, 두 profile 모두 41,625편 전체를 score한다. REC-EV-019D prediction을 읽거나 재사용하지 않는다. 두 ranking에는 fresh K10 prefix의 candidate-valid seen item 전체를 동일하게 mask하고, score 내림차순 후 movie ID 오름차순 tie-break를 적용한다.

비교군은 K5 profile policy다. K5에 candidate-valid positive와 negative anchor가 모두 있으면 K5 LightFM ranking, 아니면 B0다. 후보군은 REC-EV-019E와 byte-equivalent한 의미 계약을 사용한다. K5 양쪽 anchor가 있으면 K5 ranking, K5가 불가하고 K10 양쪽 anchor가 있으면 K10 ranking, K10도 불가하면 B0다. threshold와 학습 가능한 route parameter는 없다.

## 4. 지표와 판정

strict cohort에서 candidate minus comparator의 paired NDCG@10, Recall@10, MRR@10, candidate recall@500, positive mean rank percentile, Harm@2를 계산한다. fallback/applicability와 routing stratum, NDCG benefit/neutral/harm 사용자 수를 함께 보고한다. candidate recall@500과 positive rank percentile은 Gate가 아니지만 악화되면 전면 보고한다.

user bootstrap은 percentile 10,000회, seed 20260924다. 판정 순서는 다음과 같다.

1. Harm@2 delta의 one-sided 95% upper가 `0.005`보다 크면 `FAIL`.
2. 그렇지 않고 mean delta NDCG@10이 `0.005` 이상이며 two-sided 95% lower가 0보다 크면 `PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION`.
3. 그 밖은 `INCONCLUSIVE`.

## 5. 2-phase delivery와 재현성

Phase 1은 이 사전등록, machine contract, runner, 독립 verifier, tests, backlog skeleton만 commit/push한다. 이 단계에서는 REC-EV-019F ranking metric과 결과를 만들거나 읽지 않는다. Phase 2는 clean preregistration commit에서 lock을 만들며 contract/preregistration/spec/source/runner/verifier/validator SHA, git revision, `dirty=false`, `ranking_metrics_read=false`, `eligibility_counts_observed=true`를 기록한다. 그 뒤에만 실행·full-rescore 검증·결과 문서를 만들고 별도 commit/push한다.

원본 Parquet과 frozen cache는 ignore된 로컬 artifact이고 external artifact URI가 없다. 따라서 commit만 가진 제3자는 실행을 완전 재현할 수 없다. source path/byte/SHA-256과 코드 SHA를 lock하고 로컬 재검증은 가능하지만, 이는 원격 artifact 보존을 대신하지 않는다.

REC-EV-019C 결과 PPTX는 019C 전용이므로 019F에서 수정하지 않는다.
