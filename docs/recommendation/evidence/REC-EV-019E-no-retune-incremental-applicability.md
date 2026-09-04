# REC-EV-019E no-retune incremental applicability gate 결과

> 상태: `PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION`
> 실행 범위: Validation only, post-hoc mitigation
> `locked_test_used=false`, `champion=null`, `product_policy_updated=false`

## 1. 판정의 의미

REC-EV-019D의 confirmatory 실패와 Harm 원인 분해를 본 뒤, 이미 K5에 적용 가능한 사용자는 K5 ranking을
유지하고 K10에서 새로 적용 가능해진 사용자만 K10 ranking으로 전환하는 규칙을 선택했다. 동일한
confirmatory 1,053명을 재사용했으므로 새 confirmatory evidence가 아니다. 사전등록 Gate는 통과했지만
허용되는 최대 상태를 `PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION`으로 제한한다.

이 결과는 champion 선택이나 제품 정책 변경 권한이 없다. 다음 유효한 단계는 이 규칙을 결과를 보기 전에
고정한 뒤 fresh target-independent Validation 집단에서 다시 확인하는 것이다.

## 2. 결과 전 고정한 routing

comparator는 019D primary의 K5 profile + common K10 candidate-valid seen mask다. candidate는 threshold,
weight, tuning 없이 다음 세 branch만 사용한다.

| 019D strata | 사용자 | candidate ranking | comparator 대비 변경 |
| --- | ---: | --- | --- |
| `BOTH_LIGHTFM` | 661 | K5 fold-in | 없음 |
| `K10_NEWLY_APPLICABLE` | 277 | K10 fold-in | B0에서 K10 fold-in으로 전환 |
| `BOTH_FALLBACK` | 115 | B0 | 없음 |

모든 branch의 seen mask는 common K10 candidate-valid mask다. ranking을 섞거나 다시 정렬하지 않고 019D의
해당 ranking 전체를 선택했다.

## 3. confirmatory 1,053명 결과

| 지표 | K5 comparator | routed candidate | candidate − comparator |
| --- | ---: | ---: | ---: |
| NDCG@10 | 0.047958 | 0.061955 | +0.013997 |
| Recall@10 | 0.054149 | 0.070386 | +0.016237 |
| MRR@10 | 0.086919 | 0.107918 | +0.020999 |
| candidate recall@500 | 0.669516 | 0.648623 | -0.020893 |
| Harm@2 | 0.029440 | 0.026591 | -0.002849 |
| fallback rate | 0.372270 | 0.109212 | -0.263058 |
| applicability rate | 0.627730 | 0.890788 | +0.263058 |

사용자 percentile bootstrap 10,000회, seed `20260924`에서 ΔNDCG@10은 `+0.013997`, two-sided 95% CI는
`[0.008433, 0.019758]`이었다. ΔHarm@2는 `-0.002849`, one-sided 95% upper는 `0.003799`로 safety
상한 `0.005` 이하였다. 사전 판정 순서상 safety FAIL 조건이 먼저 불성립하고, 이어 mean ΔNDCG `>=0.005`
및 CI lower `>0`을 만족했다.

NDCG benefit/neutral/harm 사용자는 `70/957/26`이다. 661명 `BOTH_LIGHTFM`과 115명
`BOTH_FALLBACK`은 규칙상 ranking이 그대로여서 모두 neutral이다. 변화는 277명
`K10_NEWLY_APPLICABLE`에서만 생겼고 그 안의 benefit/neutral/harm은 `70/181/26`, 평균 ΔNDCG는
`+0.053208`, ΔHarm은 `-0.010830`이었다.

## 4. 숨기지 않는 trade-off

candidate recall@500은 전체 confirmatory에서 `-0.020893`, 새 적용 277명에서는 `-0.079422` 감소했다.
Top-10 품질과 Harm은 Gate를 통과했지만 positive가 Top-500 안에 하나라도 들어오는 사용자 비율은 낮아졌다.
positive mean rank percentile도 comparator `0.244894`에서 candidate `0.270648`로 커졌다. 따라서 이 결과를
모든 ranking 품질 지표의 일괄 개선으로 표현하지 않는다.

## 5. lock·독립 검증·019D 감사 보정

019E lock은 `2026-09-04T18:09:19.577449+00:00`에 hybrid 결과보다 먼저 생성됐다. contract hash는
`d2847fa757fa7e842550d865c5b5223071e378bb7cb1e7382defb850da4a1f22`, 기준 git revision은
`f08f64484fb4b5ae170ea266199374f5460d3230`, dirty status hash는
`b92f03ee14e063f55f3e114d376be2e731845c43f8c257f60ed549cf00a535b6`다. lock은 runner, verifier,
contract validator SHA-256과 모든 source path/size/hash를 기록했다.

`future_metrics_read=false`는 019E hybrid metric·delta·bootstrap·decision이 lock 전에 존재하거나 읽히지
않았다는 뜻이다. 019D result·Harm 분해·ranking은 이미 본 post-hoc source이며 lock과 보고서가 이를
명시한다.

019D verifier는 최종 감사에서 hashed T003 seed-17 item representation과 allowlisted prefix/window를
사용해 1,479명×2 arm×2 estimand=5,916개 ranking을 41,625편 full catalog에서 다시 계산했다. exact
Top-10/Top-500, full-catalog positive mean rank percentile, 모든 aggregate가 일치했고 Top-500/501 boundary
tie는 0건이었다. aggregate audit hash는
`6c0f633da7b2327bb04f566b72bf4b5886ea6dfab3a9e74136d3f3cf1048cfe9`다.

019D 과거 contract의 “cache가 없으면 refit” 문구는 실제 runner/source manifest와 불일치했다. 과거 lock
hash는 변경하지 않고 post-run audit amendment에서 이후 재검증과 후속 실험의 권위를 exact cache/hash
required, cache 부재·불일치 시 fail-closed no-refit으로 좁혔다. 과거 019D lock에 runner/verifier hash와
git revision/dirty attestation이 없다는 한계도 소급 삽입하지 않고 공개했다. 강화된 attestation은 019E부터
적용했다.

## 6. 실행과 재현

```powershell
npm run recommendation:019e:contract:check
npm run recommendation:019e:lock
npm run recommendation:019e:run
npm run recommendation:019e:check
npm run recommendation:019d:full-rescore:check
```

runner는 Validation-only role, 입력 open 전 allowlist/forbidden 검사, user batch 64, batch checkpoint와
`--resume`를 제공한다. 019E는 새 model fit이나 full score matrix를 만들지 않는다. source ranking의 exactness는
위 019D full-rescore 감사로 독립 검증한다.

## 7. PPTX와 제품 경계

`docs/presentation/FEELM-REC-EV-019C-results.pptx`는 019C 모델 비교와 당시 해석 경계를 기록한 019C 전용
자료다. 019E는 post-hoc 운영 완화이고 fresh confirmation 전에는 019C 결론이나 제품 정책을 대체하지
않으므로 PPTX를 수정하지 않았다.

- Locked Test를 열지 않았다.
- `champion=null`을 유지한다.
- `product_policy_updated=false`를 유지한다.
- 한국 사용자 만족, 실서비스 성능, 한국 영화·최신 영화 문제 해결을 주장하지 않는다.
- fresh target-independent preregistered Validation이 다음 필수 Gate다.

## 8. 근거 파일

- 계약: `docs/recommendation/contracts/rec-ev-019e-no-retune-incremental-applicability-gate.json`
- 사전등록: `docs/recommendation/evidence/REC-EV-019E-no-retune-incremental-applicability-preregistration.md`
- manifest: `docs/recommendation/evidence/manifests/rec-ev-019e-validation.json`
- local outputs: `outputs/recommendation-evidence/rec-ev-019e/`
- 019D audit amendment: `docs/recommendation/contracts/rec-ev-019d-post-run-audit-amendment.json`
- runner: `scripts/run_rec_ev_019e_no_retune_incremental_applicability.py`
- verifier: `scripts/verify_rec_ev_019e_no_retune_incremental_applicability.py`
