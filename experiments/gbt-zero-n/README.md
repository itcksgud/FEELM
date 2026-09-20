# S15P21E106-622 GBT 0~N 실험

상태: **0~N 입력 지원 PASS / GBT 단독·hard switch REJECTED_THROUGH_K50 /
인기도 80% + GBT 20% 혼합 OFFLINE_VALIDATED_CANDIDATE / 실제 저이력 미평가 /
운영 승격 보류 / FINAL_TEST 봉인**

이 실험은 평가 1~2개만으로 높은 개인화 성능을 주장하기 위한 것이 아니다. GBT가 평가 이력
0개부터 임의 N개까지 입력받도록 만들고, 모델에 제공되는 정보량 K에 따라 어떤 추천 정책이
안전한지 같은 조건에서 측정하는 것이 목표다.

## 현재 판정

| 질문 | 판정 |
| --- | --- |
| GBT가 정확히 K개의 이력을 입력받을 수 있는가? | `PASS` |
| K가 증가하면 평점 예측 오차가 개선되는가? | 통제 prefix의 세 seed에서 K=1부터 K=0 대비 개선 |
| 어느 K부터 인기도를 버리고 GBT 단독으로 전환할 수 있는가? | 테스트한 K=5, 10, 20, 40, 50에서 없음 |
| 인기도와 GBT를 함께 쓸 수 있는가? | 80:20 혼합이 CONFIRM 500명·3개 seed·전 K에서 통과 |
| 실제 이력이 K개뿐인 신규 사용자를 검증했는가? | `NOT_EVALUATED_CONTROLLED_PREFIX` |
| 운영 모델로 바로 승격할 수 있는가? | `DO_NOT_PROMOTE` |

현재 후보는 “K 이상이면 GBT로 갈아탄다”가 아니다. 테스트한 K=0, 1, 2, 5, 10, 20,
40, 50 전체에서 인기도를 주 신호로 유지하고 GBT를 20% 보조 신호로 섞는 same-pool
rerank 정책이다.

## 실제 저이력과 통제 prefix의 차이

MovieLens 32M은 모든 사용자가 최소 20개 평가를 갖도록 만들어진 데이터다. 따라서 이 데이터만으로
전체 생애 이력이 0~19개인 실제 신규 사용자 모집단을 평가할 수 없다. 이전 고정 달력 cutoff는
cutoff 이전 이력이 작은 셀을 만들었지만, 이는 “해당 시점까지 관측된 이력”과 “실제 신규 사용자”를
혼합하며 시간대·가입시점 효과까지 함께 바꾼다.

이번 설계는 각 사용자의 안전한 anchor 앞 평가 중 최근 exact K개만 모델 feature에 제공한다.
같은 사용자·미래 판단·후보 풀을 K마다 반복하므로 정보량 K의 효과를 비교할 수 있다. 숨긴 과거까지
포함한 `total_history_count`와 실제 feature에 제공한 `provided_history_count=K`를 분리하고,
controlled prefix를 실제 full-history 사용자로 표시하지 않는다.

이 설계가 답하는 질문은 “동일한 성숙 사용자에게 K개 신호만 주면 무엇이 달라지는가?”다.
“실제 평가가 K개뿐인 신규 사용자에게도 같은가?”는 답하지 않는다.

## v16 데이터·시간 계약

- TRAIN 5,000명, VALIDATION 1,000명, FINAL_TEST 0명
- VALIDATION은 사용자 단위 `SELECTION` 500명과 `CONFIRM` 500명으로 분리
- 이전 v11 VALIDATION 사용자 1,000명은 전부 제외
- exact K: 0, 1, 2, 5, 10, 20, 40, 50
- 사용자마다 미래 관측 판단 10개와 target 1개를 사용
- 모든 history event는 `prediction_at`보다 이전, 모든 target은 이후
- target과 후보 영화의 개봉 연도는 `prediction_at`의 연도 이하여야 함
- 같은 user-target의 후보와 평가 판단은 모든 K와 세 seed에서 동일
- popularity는 선택된 TRAIN 사용자의 각 `prediction_at` 이전 평가만 사용
- `UNKNOWN_SAMPLED`는 미평가이며 비선호 label로 취급하지 않음
- target을 후보에 강제로 포함하므로 범위는 `SAME_POOL_RERANK`; 후보 생성 평가는 아님
- FINAL_TEST는 파일에 기록하지 않고 seal
  `358f392e16a142b10a6cc3885370be7f7523624922c44554904360390c7df70`만 보존

v16은 48만 episode와 3,998,090개 입력 후보 행을 전수 검사했다. 준비된 validation 예측 후보는
5,318,240행이며 세 seed의 후보 identity digest는
`9b991a8c31c55bdd23caa25b7aa42cd089570012653b74ed34f269d340113ace`로 같다.

## 모델과 정책

`popularity_signed_affinity` GBT는 영화 장르·연도, TRAIN as-of popularity, 현재 K개 평점 통계,
후보와 좋아한/싫어한 장르의 관계를 사용한다. Spark `GBTRegressor` 설정은 60 trees, depth 5,
step size 0.04, feature subset `sqrt`, subsampling 0.8이다.

서로 척도가 다른 점수는 episode 안의 percentile로 바꾼 뒤 혼합한다.

```text
blend_score = 0.80 * POPULAR_COUNT_PERCENTILE + 0.20 * GBT_PERCENTILE
```

alpha=0.20은 CONFIRM 코호트를 열기 전에 고정했다. 통과 조건은 세 seed(622, 1622, 2622)의
모든 K에서 Recall@10과 NDCG@10이 순수 popularity 대비 -0.02 비열등이고, 저평점 노출
점추정이 감소하는 것이다. 24개 seed×K 셀이 모두 통과했다. 반대로 GBT 단독 hard switch는
세 seed 모두 K=50까지 통과하지 못했다.

세부 수치는 [RESULTS.md](RESULTS.md), 정책 계약은
[promotion-policy.json](promotion-policy.json), 사전 고정 혼합 설정은
[policy-v5-robust-blend.json](policy-v5-robust-blend.json), v16 증거 영수증은
[policy-v16-confirmation.json](policy-v16-confirmation.json)을 따른다.

## 재현 실행

아래 예시는 v16 run ID를 재현한다. 같은 경로가 이미 있으면 덮어쓰지 않고 새 run ID를 사용한다.

```powershell
py -3.12 scripts\gbt_zero_n_prefix_build_input.py `
  --movielens-root C:\higher\projects\MM\data\raw\ml-32m `
  --output outputs\recommendation-evidence\S15P21E106-622\prefix-input-v16-contract `
  --train-users 5000 --validation-users 1000 --final-test-users 0 --seed 622 `
  --unknown-probability 0.0012 `
  --exclude-validation-users-parquet outputs\recommendation-evidence\S15P21E106-622\prefix-prepared-v11\validation-targets.parquet `
  --final-test-seal-id 358f392e16a142b10a6cc3885370be7f7523624922c44554904360390c7df70

py -3.12 scripts\gbt_zero_n_prepare.py `
  --input-root outputs\recommendation-evidence\S15P21E106-622\prefix-input-v16-contract `
  --movielens-root C:\higher\projects\MM\data\raw\ml-32m `
  --output-root outputs\recommendation-evidence\S15P21E106-622\prefix-prepared-v16-contract `
  --source-revision (git rev-parse HEAD)

py -3.12 scripts\gbt_zero_n_run.py `
  --prepared-root outputs\recommendation-evidence\S15P21E106-622\prefix-prepared-v16-contract `
  --output-root outputs\recommendation-evidence\S15P21E106-622\prefix-fits-v16-seed622 `
  --config experiments\gbt-zero-n\config-v3.json
```

seed 1622와 2622는 각각 `config-v4-prefix-seed1622.json`,
`config-v4-prefix-seed2622.json`과 별도 output을 사용한다. 고정 혼합 정책 확인은 다음과 같다.

```powershell
py -3.12 scripts\gbt_zero_n_blend_evaluate.py `
  --predictions outputs\recommendation-evidence\S15P21E106-622\prefix-fits-v16-seed622\popularity_signed_affinity\validation-candidate-predictions.parquet `
  --output outputs\recommendation-evidence\S15P21E106-622\prefix-policy-v16-contract\blend-confirm-seed622.json `
  --evaluation-split CONFIRM `
  --selection-report experiments\gbt-zero-n\policy-v5-robust-blend.json `
  --noninferiority-margin 0.02 --seed 622
```

hard switch는 `gbt_zero_n_policy_evaluate.py`, 전체 계약은 `gbt_zero_n_verify.py`로 확인한다.
`outputs/`는 Git에서 제외한다. 다른 컴퓨터에서 결과를 옮기면 증거 영수증의 파일 크기와 SHA-256,
MovieLens 원본 SHA-256, source bundle digest를 먼저 대조한다. 모델 선택 과정에서는 FINAL_TEST
경로를 만들거나 열지 않는다.
