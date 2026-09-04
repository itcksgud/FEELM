# REC-EV-019D 동일 미래 구간 prefix ablation 결과

> 상태: `FAIL_SAFETY_MARGIN_EXCEEDED`
> 실행 범위: Validation only
> `locked_test_used=false`, `champion=null`, `product_policy_updated=false`

## 1. 질문과 사전등록

REC-EV-019C의 K5와 K10은 사용자와 미래 구간이 달라 직접 비교할 수 없었다. 019D는 기존 K10 strict
Validation 사용자 1,479명과 각 사용자의 K10 미래 10행을 고정하고, K10 prefix의 first5와 first10만
바꾸었다. 기존 K5·K10 tuning panel 합집합을 K10 cohort와 교차한 426명을 제외해 1,053명을
confirmatory 집합으로 고정했으며 이 집합에서는 tuning과 threshold를 바꾸지 않았다.

기계 판독 계약 SHA-256 `8d077f3633c0808a0fa8824d5f7e369433a3339d723327710bd6813c827f14f5`와
입력 경로·크기·SHA-256, cohort·mask·B8 LightFM T003 seed 17·B0 fallback·bootstrap·판정 순서를
`protocol-lock.json`에 먼저 기록했다. lock은 `2026-09-04T17:34:47.974557+00:00`에 생성됐고
`future_labels_joined_at_lock=false`였다. 그 뒤에만 K10 future label을 결합했다.

## 2. cohort와 적용 strata

사용자마다 `input_rank=1..10`, `source_position` 엄격 증가, timestamp 비감소, first5가 first10의 정확한
prefix임을 직접 확인했다. confirmatory strata는 사전 예상과 모두 일치했다.

| strata | 사용자 |
| --- | ---: |
| K5·K10 모두 LightFM 적용 | 661 |
| K5 fallback → K10 새 적용 | 277 |
| K5·K10 모두 B0 fallback | 115 |
| 합계 | 1,053 |

원시 prefix에는 양쪽 label이 있지만 candidate-valid anchor가 부족한 사용자는 confirmatory 1,053명에서
K5 61명, K10 34명이었다.
이는 효과 strata가 아니라 적용 가능성 손실 진단이다.

## 3. primary: common K10 seen mask

두 arm 모두 K10 prefix의 candidate-valid 10편 전체를 추천 후보에서 제외하고 fold-in profile만 first5와
first10으로 바꿨다. 따라서 profile information ablation이지만 후보 제외에 K10 정보를 쓰는 통제 실험이며,
end-to-end K5 serving 비교가 아니다.

| confirmatory 1,053명 | K5 profile | K10 profile | K10 − K5 |
| --- | ---: | ---: | ---: |
| NDCG@10 | 0.047958 | 0.074519 | +0.026562 |
| Recall@10 | 0.054149 | 0.085814 | +0.031665 |
| MRR@10 | 0.086919 | 0.126167 | +0.039248 |
| candidate recall@500 | 0.669516 | 0.672365 | +0.002849 |
| Harm@2 | 0.029440 | 0.032289 | +0.002849 |
| fallback rate | 0.372270 | 0.109212 | -0.263058 |

사용자 단위 percentile bootstrap 10,000회, seed `20260924`에서 NDCG delta의 two-sided 95% CI는
`[0.017837, 0.035203]`이었다. 평균 `+0.026562`와 lower>0은 사전 효능 기준을 통과했다. 그러나
Harm@2 delta의 one-sided 95% upper는 `0.012346`으로 사전 안전 한계 `0.005`를 넘었다.

사전 판정 우선순위에서 safety margin 초과는 효능 성공보다 먼저 `FAIL`이다. 따라서 최종 판정은
`FAIL_SAFETY_MARGIN_EXCEEDED`다. 평균 NDCG가 좋아졌다는 이유로 이 판정을 `PASS`나 `K10 채택`으로
바꾸지 않는다.

## 4. secondary: arm-specific seen mask

K5는 first5, K10은 first10의 candidate-valid 항목을 각각 제외했다. 이 분석은 profile 정보와 seen set을
함께 바꾸므로 end-to-end 진단일 뿐 사전 의사결정 Gate가 아니다.

| confirmatory 1,053명 | K5 | K10 | K10 − K5 |
| --- | ---: | ---: | ---: |
| NDCG@10 | 0.045593 | 0.074519 | +0.028926 |
| Recall@10 | 0.052424 | 0.085814 | +0.033390 |
| MRR@10 | 0.081756 | 0.126167 | +0.044412 |
| candidate recall@500 | 0.669516 | 0.672365 | +0.002849 |
| Harm@2 | 0.028490 | 0.032289 | +0.003799 |

secondary에서도 평균 품질은 증가 방향이지만 primary 안전 판정을 덮어쓰지 않는다.

## 5. 실행·재현과 캐시 한계

```powershell
py -3 scripts/run_rec_ev_019d_prefix_ablation.py --phase lock --role validation-019d
py -3 scripts/run_rec_ev_019d_prefix_ablation.py --phase run --role validation-019d --resume
py -3 scripts/verify_rec_ev_019d_prefix_ablation.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-019d-validation.json
py -3 scripts/verify_rec_ev_019d_prefix_ablation.py `
  --manifest docs/recommendation/evidence/manifests/rec-ev-019d-validation.json `
  --full-rescore-users all
```

양 arm은 019C의 `B8_LIGHTFM-T003-S17/result.npz`를 공통 사용했다. 파일 크기는 19,946,732 bytes,
SHA-256은 `da3414ef1a18cbe515e319a93f04ca5498e76669ab756680384e8f82bc918c35`이며 config는 128차원,
10 epochs, adagrad, learning rate 0.05, item/user alpha 1e-6, seed 17이다. source manifest에는 config,
interactions, item features와 result의 경로·크기·SHA-256을 모두 기록했다.

과거 사전등록에는 cache가 없거나 무결성 검증에 실패하면 같은 config로 한 번 refit한다고 적혔지만 실제
runner에는 해당 refit 경로가 없고 source manifest도 exact cache reuse를 기록한다. 과거 contract와 lock
hash는 소급 변경하지 않았다. 대신
`contracts/rec-ev-019d-post-run-audit-amendment.json`에서 이후 재검증·후속 실험의 권위를 exact
cache/hash required로 좁혔고 cache 부재·불일치는 no-refit fail-closed로 처리한다.

캐시와 원본 Parquet은 `outputs/` ignore에만 있고 외부 artifact URI가 없다. 따라서 현재 로컬 원자료가 있는
환경에서는 재검증 가능하지만 commit만 받은 제3자는 base fit이나 결과를 재현할 수 없다. 실행기는 사용자
batch 최대 32명, full score matrix 비저장, batch별 hash checkpoint와 `--resume`를 강제했다.

독립 verifier의 기본 실행은 SHA-256 기반으로 고른 64명을 full-catalog 재점수하는 bounded deterministic
mode다. 최종 감사에서는 `--full-rescore-users all`로 hashed LightFM item representation과 allowlisted
prefix/window에서 1,479명×2 arm×2 estimand=5,916개 ranking을 모두 다시 계산했다. exact Top-10/Top-500,
full-catalog `positive_mean_rank_percentile`, aggregate가 일치했고 Top-500/501 boundary tie는 0건이었다.
aggregate audit hash는 `6c0f633da7b2327bb04f566b72bf4b5886ea6dfab3a9e74136d3f3cf1048cfe9`다.

과거 019D lock은 timestamp·contract/spec/source artifact hash는 기록했지만 runner/verifier source SHA-256과
git revision/dirty status는 attest하지 않았다. 이 한계는 과거 lock에 소급 삽입하지 않았다. 강화된 코드·git
attestation은 REC-EV-019E lock부터 적용했다.

## 6. 019C prediction과 PPTX

019C K5 prediction에는 현재 K10 cohort 사용자 226명이 없고, K5 미래 창은 현재 K10 미래 창과 전원
다르며, seen mask·선택 과정도 다르다. Top-500만 저장돼 새 mask의 full-catalog 순위를 복원할 수도 없어
prediction은 재사용하지 않았다. base item representation만 hash 검증 후 재사용했다.

`docs/presentation/FEELM-REC-EV-019C-results.pptx`는 019C 모델 비교와 당시의 직접 비교 금지 결론을
기록한 019C 전용 덱이다. 019D는 사전 safety Gate에서 `FAIL`했고 제품 판정이나 019C 결과를 대체하지
않으므로 기존 덱은 수정하지 않았다. 019D 결과의 권위는 이 보고서, 기계 판독 manifest와 verifier에 둔다.

## 7. 결정과 해석 경계

- 같은 사용자·같은 미래 구간에서 first10 profile의 평균 NDCG 개선은 관측됐다.
- 동시에 사전 Harm@2 안전 한계를 넘었으므로 K10 정책 채택 근거는 `FAIL`이다.
- common seen primary는 K10 정보를 candidate mask에 사용하는 통제 ablation이다.
- 한국 사용자 만족, 한국 영화 문제, 최신 영화 문제를 해결했다고 쓰지 않는다.
- Locked Test를 열지 않고 `champion=null`, `product_policy_updated=false`를 유지한다.

## 8. 근거 파일

- 계약: `docs/recommendation/contracts/rec-ev-019d-prefix-ablation-artifacts.json`
- 사전등록: `docs/recommendation/evidence/REC-EV-019D-prefix-ablation-preregistration.md`
- manifest: `docs/recommendation/evidence/manifests/rec-ev-019d-validation.json`
- local outputs: `outputs/recommendation-evidence/rec-ev-019d/`
- runner: `scripts/run_rec_ev_019d_prefix_ablation.py`
- verifier: `scripts/verify_rec_ev_019d_prefix_ablation.py`
