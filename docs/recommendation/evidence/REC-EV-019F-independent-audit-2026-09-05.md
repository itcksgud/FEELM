# REC-EV-019F independent audit — 2026-09-05

상태: `PASS_NO_RESULT_CHANGING_DEFECT`

## 감사 결론

`b8be4a3a628c6d917c9cc2ffdbc9ae5f6667e861`의 REC-EV-019F 결과를 기존 runner와 분리된 verifier로
원자료에서 다시 구성하고 strict 사용자 802명 전원을 full-catalog rescore했다. 결과를 바꾸는 결함은
발견하지 못했다. 최종 판정은 계속 `INCONCLUSIVE`다.

이 감사에서도 `locked_test_used=false`, `champion=null`, `product_policy_updated=false`를 유지했다.
REC-EV-019C 전용 PPTX는 열거나 수정하지 않았다.

## 재검증 결과

| 항목 | 독립 감사 값 | 판정 |
| --- | ---: | --- |
| structural cohort | 1,021명 | 일치 |
| strict cohort | 802명 | 일치 |
| 과거 019A source-row overlap | 0 | 일치 |
| 과거 019A K10 사용자 overlap | 629명 | 일치 |
| 과거 K10 밖 사용자 | 173명 | 일치 |
| 019A Validation 완전 신규 사용자 | 31명 | 일치 |
| `BOTH_LIGHTFM` | 568명 | 일치 |
| `K10_NEWLY_APPLICABLE` | 164명 | 일치 |
| `BOTH_FALLBACK` | 70명 | 일치 |
| candidate−comparator ΔNDCG@10 | `+0.003616669` | 일치 |
| ΔNDCG 95% CI | `[+0.000290762, +0.007238696]` | 일치 |
| Harm@2 one-sided 95% upper | `0.003740648` | 일치, 0.005 이내 |
| candidate recall@500 delta | `-0.012468828` | 악화 공개 일치 |
| positive mean rank percentile delta | `+0.020316482` | 악화 공개 일치 |
| full-rescore | 802명, K5/K10 1,604 rankings | exact Top-10/Top-500 일치 |

Harm Gate는 통과했지만 평균 ΔNDCG가 사전 SESOI `0.005`보다 작으므로 harm-first 규칙의 세 번째 분기인
`INCONCLUSIVE`가 맞다. candidate recall과 positive rank percentile은 Gate가 아니지만 둘 다 악화했으며
결과 문서에 누락되지 않았다.

## preregistration provenance와 invalid run

유효 protocol lock은 ranking metric을 읽기 전의 clean revision
`fe9764e12fdbe0362a816d452b39b7ae1385cb42`를 가리킨다. 현재 contract, preregistration, runner,
verifier, validator는 해당 lock source와 동일했고 `dirty=false`, `ranking_metrics_read=false`,
`eligibility_counts_observed=true` attestation도 재확인했다.

최초 float64 메모리 값과 float32 artifact 값이 달랐던 실행은
`outputs/recommendation-evidence/rec-ev-019f-invalid-float64-run-20260905/`에 격리되어 있고 유효 결과
경로와 분리돼 있다. metric이 저장 artifact의 float32 값을 사용하도록 보정한 뒤 새 clean lock에서 전체
실행을 다시 만들었으므로 격리 run은 판정에 사용되지 않았다.

## 실행 명령

```powershell
py -3 scripts/verify_rec_ev_019f_independent_temporal_routing.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-019f-validation.json `
  --full-rescore-users all
```

verifier 결과는 `PASS_INDEPENDENT_RECONSTRUCTION_AND_FULL_RESCORE`였고, exact Top-10/Top-500,
positive mean rank percentile, source-row overlap과 모든 집계값을 재현했다.
