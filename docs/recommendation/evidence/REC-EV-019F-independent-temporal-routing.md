# REC-EV-019F independent temporal routing 결과

상태: `INCONCLUSIVE`

## 1. 결론

REC-EV-019E frozen routing을 같은 Validation 사용자들의 더 뒤쪽 source row와 fresh future window에서 사전등록 확인했지만 성공 Gate를 통과하지 못했다. 독립성의 단위는 **source row와 temporal window**이며 **사용자 독립이 아니다**.

strict 802명의 candidate−comparator ΔNDCG@10은 `+0.003617`, user bootstrap 95% CI는 `[+0.000291, +0.007239]`였다. lower bound는 0보다 컸지만 mean이 사전 SESOI `0.005`에 못 미쳤다. Harm@2 delta의 one-sided 95% upper는 `0.003741`로 안전 한계 `0.005` 안이었다. Harm-first 규칙을 적용한 최종 판정은 `INCONCLUSIVE`다.

candidate recall@500은 `-0.012469`, positive mean rank percentile은 `+0.020316`으로 둘 다 악화했다. 두 지표는 Gate가 아니지만 숨기지 않고 전면 보고한다. positive rank percentile은 낮을수록 좋으므로 양의 delta는 악화다.

이 결과는 한국 사용자·한국 영화·최신 영화 문제를 해결했다는 근거가 아니다. `locked_test_used=false`, `champion=null`, `product_policy_updated=false`를 유지한다.

## 2. 2-phase lock

Phase 1에서 contract, preregistration, runner, 독립 verifier, tests와 backlog/readiness skeleton을 먼저 commit/push했다. 첫 실행은 독립 verifier가 window utility의 메모리 float64와 artifact float32 사이 정밀도 불일치를 발견해 거부했다. 해당 산출물은 결과로 채택하지 않았고 로컬 격리했다. metric이 artifact 저장 정밀도를 사용하도록 보정한 preregistration source commit `fe9764e12fdbe0362a816d452b39b7ae1385cb42`를 push한 뒤 새 lock과 실행을 처음부터 만들었다.

유효 lock은 다음을 기록한다.

- git revision: `fe9764e12fdbe0362a816d452b39b7ae1385cb42`
- `dirty=false`
- `ranking_metrics_read=false`
- `eligibility_counts_observed=true`
- contract/preregistration/spec/source artifact/runner/verifier/validator/helper SHA-256
- `locked_test_used=false`, `champion=null`, `product_policy_updated=false`

`ranking_metrics_read=false`는 lock 전에 REC-EV-019F comparator/candidate ranking metric, delta, bootstrap, 판정이 없었다는 뜻이다. structural 1,021명과 strict 802명 등 eligibility 수치는 이미 감사에서 관측됐으며 blind 기대가 아니라는 사실을 lock과 source manifest에 기록했다.

## 3. temporal episode와 cohort

원천은 `global-time-v1/validation.parquet`의 고정 사용자 bucket 50..59뿐이다. 각 사용자의 기존 019A 의미상 first10 binary prefix 10번째 source position과 그 뒤 원본 observation 10개를 모두 지난 위치에서 tail을 시작했다. tail의 사용자 상대효용 누적을 reset하고 shrinkage 10, like `>=0.15`, dislike `<=-0.15`로 새 first10 non-neutral prefix를 만들었다. 10번째 새 선택 직후 정확히 10개 원본 observation을 fresh future window로 사용했다.

REC-EV-019C K5/K10 tuning panel 합집합 477명은 fresh future label 계산 전에 제외했다. 결과 cohort는 사전 공개한 감사 기대와 일치했다.

| 항목 | 사용자 |
| --- | ---: |
| structural cohort | 1,021 |
| strict cohort | 802 |
| 기존 019A K10 strict cohort와 겹침 | 629 |
| 기존 019A K10 strict cohort 밖 | 173 |
| 위 173명 중 019A Validation artifact에 완전히 새 사용자 | 31 |

사용자 overlap은 허용되고 공개된다. 새 prefix/window의 `user_key + source_position`을 기존 019A 의미상 prefix/evaluation source rows와 대조한 overlap은 `0`이다. 따라서 이 실험은 사용자 독립이 아니라 source-row/window 독립 confirmation이다.

## 4. frozen model과 routing

후보 41,625편, B8 LightFM T003 seed 17 exact cache/hash, B0 fallback, fold-in 80 epoch, tie-break `effective_score desc → movie_id asc`를 고정했다. fit/refit, threshold, candidate/route search는 없었다. REC-EV-019D prediction은 읽거나 재사용하지 않았다.

strict 사용자마다 fresh K5(first5)와 K10(first10)을 각각 새로 fold-in하고 두 profile 모두 41,625편을 full-catalog score했다. 두 profile에는 fresh K10의 candidate-valid seen item 전체를 공통 mask로 적용했다. routing은 REC-EV-019E와 canonical byte-equivalent semantic contract다.

| routing stratum | 사용자 | comparator | candidate |
| --- | ---: | --- | --- |
| `BOTH_LIGHTFM` | 568 | K5 | K5 |
| `K10_NEWLY_APPLICABLE` | 164 | B0 | K10 |
| `BOTH_FALLBACK` | 70 | B0 | B0 |

## 5. 전체 지표

| 지표 | comparator | candidate | delta |
| --- | ---: | ---: | ---: |
| NDCG@10 | 0.028016 | 0.031633 | +0.003617 |
| Recall@10 | 0.035011 | 0.040785 | +0.005774 |
| MRR@10 | 0.048159 | 0.051426 | +0.003268 |
| candidate recall@500 | 0.586035 | 0.573566 | **-0.012469** |
| positive mean rank percentile | 0.296288 | 0.316605 | **+0.020316 (악화)** |
| Harm@2 | 0.013716 | 0.014963 | +0.001247 |
| fallback rate | 0.291771 | 0.087282 | -0.204489 |
| applicability rate | 0.708229 | 0.912718 | +0.204489 |

NDCG benefit/neutral/harm 사용자 수는 `27/762/13`이다. 변화는 `K10_NEWLY_APPLICABLE` 164명에서만 발생했다. 이 stratum의 ΔNDCG는 `+0.017686`, ΔRecall은 `+0.028238`, ΔMRR은 `+0.015979`였지만 candidate recall@500은 `-0.060976`, positive rank percentile은 `+0.099353`, Harm@2는 `+0.006098`이었다. 전체 Gate는 사전등록한 802명 strict cohort의 paired bootstrap만 사용했다.

## 6. 독립 검증

`npm run recommendation:019f:check`가 다음을 통과했다.

- contract validator와 mutation/unit test 17개
- Validation bucket과 tail episode 독립 재구성
- tuning union 477 사전 제외와 structural 1,021/strict 802 재현
- source-row overlap 0, 사용자 overlap 629/173/31 재현
- 802명 전원 × K5/K10 = 1,604개 full-catalog profile rescore
- exact Top-10/Top-500와 persisted score, positive mean rank percentile 일치
- Top-500 boundary tie 0건
- paired delta, 10,000회 seed 20260924 bootstrap, Harm-first `INCONCLUSIVE` 재현

## 7. 해석과 재현성 한계

REC-EV-019F는 019E post-hoc 규칙을 더 뒤쪽 episode에서 재확인했지만 사용자 자체는 같은 Validation population에서 나올 수 있다. 따라서 target-domain confirmation이 아니며, 성공했다 해도 최대 상태는 `PASS_INDEPENDENT_TEMPORAL_WINDOW_REQUIRES_TARGET_DOMAIN_CONFIRMATION`이었다. 실제 결과는 그보다 낮은 `INCONCLUSIVE`다.

raw Validation Parquet과 frozen model cache, 생성 output은 Git에서 ignore되며 external artifact URI가 없다. manifest가 path/byte/SHA-256을 잠그고 로컬 재검증을 가능하게 하지만 commit만 가진 제3자는 완전 재현할 수 없다.

REC-EV-019C 결과 PPTX는 019C 전용이므로 수정하지 않았다.
