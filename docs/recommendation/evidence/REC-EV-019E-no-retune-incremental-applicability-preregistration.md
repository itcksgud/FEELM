# REC-EV-019E no-retune incremental applicability gate 사전등록

> 상태: `APPROVED_POST_HOC_VALIDATION_ONLY_BEFORE_RESULT`
> ID: `REC-EV-019E-NO-RETUNE-INCREMENTAL-APPLICABILITY-GATE`
> 실행 역할: `VALIDATION_019E_POST_HOC`
> `locked_test_used=false`, `champion=null`, `product_policy_updated=false`

## 증거 성격과 질문

이 실험은 REC-EV-019D confirmatory 결과와 Harm@2 원인 분해를 본 뒤 선택한 운영 완화책이다. 019D와
동일한 confirmatory 1,053명을 다시 사용하므로 완전히 새로운 confirmatory evidence가 아니다. 통과해도
상태는 `PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION`으로 제한하며, target-independent한 새
Validation 집단에서 사전등록 confirmation을 통과하기 전에는 champion이나 제품 정책을 바꾸지 않는다.

K10 cohort 1,479명에서 기존 K5·K10 tuning-panel 합집합 426명을 제외한 1,053명만 Gate에 사용한다.
comparator는 019D primary의 `K5 profile + common K10 candidate-valid seen mask`다.

## 고정 candidate routing

threshold, weight, 추가 parameter, 후보 탐색은 없다. 모든 branch는 common K10 candidate-valid seen mask를
유지한다.

1. K5에 candidate-valid positive와 negative anchor가 모두 있으면 K5 fold-in ranking을 유지한다.
2. K5는 불가하고 K10에 두 anchor가 모두 있으면 K10 fold-in ranking을 선택한다.
3. K10도 불가하면 B0 ranking을 선택한다.

따라서 019D strata와의 대응은 `BOTH_LIGHTFM → K5`, `K10_NEWLY_APPLICABLE → K10`,
`BOTH_FALLBACK → B0`로 결정적이다. 019D common-mask Top-500에서 ranking 전체를 선택할 뿐 score를
혼합하거나 재정렬하지 않는다. exact reuse의 근거는 별도 019D verifier가 hashed LightFM item
representation과 allowlisted 입력에서 1,479명×2 arm×2 estimand=5,916개 full-catalog ranking을 다시
계산해 exact Top-10/Top-500, Top-500 boundary tie, full-catalog positive mean rank percentile과 aggregate를
검증하는 것이다.

## 지표와 판정

comparator 대비 candidate의 paired NDCG@10, Recall@10, MRR@10, candidate recall@500, Harm@2와
fallback/applicability를 보고한다. strata별 같은 지표와 NDCG benefit(>0)·neutral(=0)·harm(<0) 사용자
수도 공개한다. 사용자 percentile bootstrap 10,000회, seed `20260924`를 사용한다.

판정 순서는 다음과 같으며 JSON 계약의 `priority`가 규범적이다.

1. Harm@2 delta one-sided 95% upper가 `0.005`보다 크면 `FAIL_SAFETY_MARGIN_EXCEEDED`.
2. 그렇지 않고 mean ΔNDCG@10이 `0.005` 이상이며 two-sided 95% CI lower가 0보다 크면
   `PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION`.
3. 그 밖은 `INCONCLUSIVE_POST_HOC_VALIDATION`.

## lock과 post-hoc 오염의 정확한 의미

019D result·Harm 분해·prediction은 이미 본 post-hoc source이며 이를 숨기지 않는다. 여기서
`future_metrics_read=false`는 019E routed hybrid metric, delta, bootstrap, decision이 lock 전에 생성되거나
읽히지 않았다는 뜻일 뿐 019D에 blind였다는 뜻이 아니다.

실행 전 lock은 contract·이 사전등록·모든 allowlisted source의 경로/크기/SHA-256, runner/verifier/contract
validator SHA-256, git revision과 dirty status/status hash를 기록한다. lock이 존재하고 이 값들이 일치해야만
`--role validation-019e-post-hoc --resume` 실행이 가능하다. Locked Test와 제품 정책 경로는 allowlist에 없고
접근을 금지한다.

## 해석 경계

기대와 다른 결과도 그대로 보고한다. 통과하더라도 같은 1,053명의 재사용과 mitigation 선택의 post-hoc
오염 때문에 fresh target-independent Validation이 필요하다. 한국 사용자 만족, 실서비스 효과, champion,
제품 정책을 승인하지 않는다.
