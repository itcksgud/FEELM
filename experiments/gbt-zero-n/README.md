# S15P21E106-622 GBT 0~N 실험

상태: **validation 증거 완성, 모델 승격 보류, FINAL_TEST 봉인**

이 실험은 S15P21E106-620의 episode·후보 계약과 S15P21E106-621의 MovieLens snapshot,
시간 분할, seed 정책을 사용해 Spark `GBTRegressor`가 0~N개 평가 이력을 처리하는 방식을 비교한다.
학습과 검증은 로컬 Docker에서만 실행하며 서버, HDFS, E106 CI/CD를 사용하지 않는다.

## 비교 profile

| profile | 특징 |
| --- | --- |
| `movie_only` | MovieLens 장르·개봉연도·장르 수 |
| `history_aggregate` | movie-only + 이력 수·지원률·별점 분포·최근성 |
| `response_relation` | history-aggregate + 후보 장르와 긍정/부정 이력의 관계·개봉연도 거리 |

입력 생성기는 겹치지 않는 관측 창(TRAIN 2017–2019, VALIDATION 2020–2021,
FINAL_TEST 2022–2023)을 사용한다. 사용자별 target은 별점을 보지 않는 SHA-256 순서로 최대 4편을
고른다. 각 target에는 0/1/2/4/7/15/25/40 중 가능한 prefix와 **사용 가능한 전체 N**을 반드시
만든다. 따라서 N=3, 5, 41 같은 값도 full-history variant로 평가된다.

`UNKNOWN_SAMPLED` 후보는 비선호 label로 학습하지 않는다. TRAIN의 관측 target 별점만 회귀 label로
사용하고, 한 user-target의 여러 N variant가 목적함수를 지배하지 않도록 variant 수의 역수를
`sample_weight`로 사용한다. VALIDATION 후보는 관측된 positive와 low-rating 후보에 대해서만 순위를
판정하며 모든 비율은 분자·분모를 함께 기록한다.

KOBIS 2,176행은 MovieLens/TMDB/서비스 영화와 검증된 crosswalk가 없어 이번 profile에 결합하지
않는다. TMDB도 새로 수집하지 않는다. title 우선순위와 TMDB/KOBIS 인기도는 최종 사용자 평가 직전
별도 rerank/profile에서 비교한다.

## 10% 실행

기존 output은 덮어쓰지 않는다. 아래 v7이 임의 N과 LOW_HISTORY_COHORT를 포함한 현재 기준이다.

```powershell
py -3.12 scripts\gbt_zero_n_build_input.py `
  --movielens-root C:\higher\projects\MM\data\raw\ml-32m `
  --output outputs\recommendation-evidence\S15P21E106-622\ten-percent-input-v7 `
  --train-users 15000 --validation-users 3000 --final-test-users 2000 --seed 622

py -3.12 scripts\gbt_zero_n_prepare.py `
  --input-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-input-v7 `
  --movielens-root C:\higher\projects\MM\data\raw\ml-32m `
  --output-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-prepared-v7 `
  --source-revision (git rev-parse HEAD)

py -3.12 scripts\gbt_zero_n_run.py `
  --prepared-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-prepared-v7 `
  --output-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-fits-v7
```

기본 실행 후 전수 검증, seed 민감도, pairwise ranking 목적함수, legacy 감사를 수행한다.

```powershell
py -3.12 scripts\gbt_zero_n_verify.py `
  --movielens-root C:\higher\projects\MM\data\raw\ml-32m `
  --input-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-input-v7 `
  --prepared-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-prepared-v7 `
  --fits-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-fits-v7 `
  --output outputs\recommendation-evidence\S15P21E106-622\ten-percent-fits-v7\verification.json

py -3.12 scripts\gbt_zero_n_seed_sensitivity.py `
  --prepared-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-prepared-v7 `
  --base-fits-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-fits-v7 `
  --output-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-sensitivity-v1

py -3.12 scripts\gbt_zero_n_rank_run.py `
  --prepared-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-prepared-v7 `
  --output-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-rank-v2

py -3.12 scripts\gbt_zero_n_rank_evaluate.py `
  --regression-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-fits-v7\response_relation `
  --rank-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-rank-v2 `
  --output outputs\recommendation-evidence\S15P21E106-622\ten-percent-rank-v2\comparison-v2.json --seed 622

py -3.12 scripts\gbt_zero_n_legacy_audit.py `
  --legacy-root C:\higher\projects\FEELM-standalone\outputs\recommendation-evidence\final344\GBT120_s339 `
  --output outputs\recommendation-evidence\S15P21E106-622\ten-percent-legacy-audit-v1.json

py -3.12 scripts\gbt_zero_n_completion_verify.py `
  --prepared-manifest outputs\recommendation-evidence\S15P21E106-622\ten-percent-prepared-v7\manifest.json `
  --base-verification outputs\recommendation-evidence\S15P21E106-622\ten-percent-fits-v7\verification.json `
  --validation-report outputs\recommendation-evidence\S15P21E106-622\ten-percent-fits-v7\validation-report.json `
  --sensitivity-report outputs\recommendation-evidence\S15P21E106-622\ten-percent-sensitivity-v1\sensitivity-report.json `
  --rank-comparison outputs\recommendation-evidence\S15P21E106-622\ten-percent-rank-v2\comparison-v2.json `
  --rank-metrics outputs\recommendation-evidence\S15P21E106-622\ten-percent-rank-v2\metrics.json `
  --legacy-audit outputs\recommendation-evidence\S15P21E106-622\ten-percent-legacy-audit-v1.json `
  --output outputs\recommendation-evidence\S15P21E106-622\ten-percent-completion-v4\completion-report.json
```

과거 `GBT120_s339`는 cohort와 feature가 달라 숫자를 직접 비교하지 않고 파일·실행 정체성만
감사한다. 마지막 `gbt_zero_n_completion_verify.py`는 모든 증거가 같은 준비물과 후보를 썼는지,
FINAL_TEST가 닫혀 있는지 검사하고 승격 여부를 기록한다. 현재 결과는 `DO_NOT_PROMOTE`다.
세부 수치와 판정은 [`RESULTS.md`](RESULTS.md), 노트북 이전 절차는
[`LAPTOP-HANDOFF.md`](LAPTOP-HANDOFF.md)를 따른다.

`outputs/`는 Git에서 제외한다. 결과를 다른 컴퓨터로 옮길 때는 저장소 코드와 동결 원본을 먼저
맞추고, 결과 디렉터리는 Git 밖에서 복사한 후 verifier를 다시 실행한다.
