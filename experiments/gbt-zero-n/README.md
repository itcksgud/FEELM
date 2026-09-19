# S15P21E106-622 GBT 0~N 실험

상태: **10% validation 실행 완료, FINAL_TEST 봉인**

이 실험은 S15P21E106-620의 episode·후보 계약과 S15P21E106-621이 고정한 MovieLens snapshot,
cutoff 계열과 seed 정책을 사용해
Spark `GBTRegressor`의 가변 이력 특징을 비교한다. 기존 RH230/245와 `GBT120_s339`는
legacy 기준선이며 이 실험의 새 profile로 재사용하지 않는다.

세 profile은 같은 TRAIN/VALIDATION episode와 후보를 사용한다.

| profile | 특징 |
| --- | --- |
| `movie_only` | MovieLens 장르·개봉연도·장르 수 |
| `history_aggregate` | movie-only + 이력 수·지원률·별점 분포·최근성 |
| `response_relation` | history-aggregate + 후보 장르와 긍정/부정 이력의 관계·개봉연도 거리 |

621 readiness 표본은 자원·계약 점검용으로 target이 모두 4점 이상이어서 회귀 학습 입력으로
사용하지 않는다. `gbt_zero_n_build_input.py`가 겹치지 않는 역할별 관측 창(TRAIN 2017–2019,
VALIDATION 2020–2021, FINAL_TEST 2022–2023)에서 사용자마다 별점을 보지 않는 SHA-256 순서로
관측 target을 최대 4편 선택한다. 후보와 target은 예측 시점 연도까지 개봉연도가 확인된 영화만
허용하고 개봉연도 결측은 as-of catalog에서 제외한다.
과거 이력의 0/1/2/4/7/15/25/40/full-N prefix를 만든다.

`UNKNOWN_SAMPLED` 후보는 비선호 label로 학습하지 않는다. TRAIN의 실제 관측 target 별점만
회귀 label로 사용하며, 한 사용자-target의 여러 N variant가 목적함수를 지배하지 않도록 variant
수의 역수를 `sample_weight`로 사용한다. VALIDATION 후보는 관측 target의 순위와 UNKNOWN 슬롯
비율을 진단하는 데만 사용한다.

KOBIS 2,176행은 현재 MovieLens/TMDB/서비스 확정 crosswalk가 0행이므로 이 최초 profile에
붙이지 않는다. 연결이 검증된 snapshot이 생기면 missing bucket을 포함한 별도 profile로 추가한다.
TMDB도 새 수집 없이 기존 동결 자료만 허용하되, 이 fixture 단계에서는 MovieLens 자체 속성만 쓴다.

입력 생성과 준비 명령은 기존 output을 덮어쓰지 않는다.

```powershell
py -3.12 scripts\gbt_zero_n_build_input.py `
  --movielens-root C:\higher\projects\MM\data\raw\ml-32m `
  --output outputs\recommendation-evidence\S15P21E106-622\fixture-input-v5 `
  --train-users 200 --validation-users 200 --final-test-users 200 --seed 622

py -3.12 scripts\gbt_zero_n_prepare.py `
  --input-root outputs\recommendation-evidence\S15P21E106-622\fixture-input-v5 `
  --movielens-root C:\higher\projects\MM\data\raw\ml-32m `
  --output-root outputs\recommendation-evidence\S15P21E106-622\fixture-prepared-v5 `
  --source-revision (git rev-parse HEAD)
```

학습은 `scripts/gbt_zero_n_run.py`가 고정 Docker image와 Spark 4.1.3, `local[4]`로 순차 실행한다.

```powershell
py -3.12 scripts\gbt_zero_n_run.py `
  --prepared-root outputs\recommendation-evidence\S15P21E106-622\fixture-prepared-v5 `
  --output-root outputs\recommendation-evidence\S15P21E106-622\fixture-fits-v6
```

621의 `ml32m-zero-n-readiness-10pct-v2`는 target 평점이 모두 4점 이상인 계약·자원 점검용
표본이므로 모델 학습 입력으로 사용하지 않는다. 10% 실행은 이 문서의 동일 생성기를
15,000/3,000/2,000명으로 실행한 `ten-percent-input-v6`를 사용한다. 재현 및 결과 해석은
[`RESULTS.md`](RESULTS.md)에 기록한다. FINAL_TEST는 profile과 설정을 고정하기 전에는 채점하지 않는다.

10% 실행 후에는 모든 episode와 후보를 전수 검사한다.

```powershell
py -3.12 scripts\gbt_zero_n_verify.py `
  --movielens-root C:\higher\projects\MM\data\raw\ml-32m `
  --input-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-input-v6 `
  --prepared-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-prepared-v6 `
  --fits-root outputs\recommendation-evidence\S15P21E106-622\ten-percent-fits-v6 `
  --output outputs\recommendation-evidence\S15P21E106-622\ten-percent-fits-v6\verification.json
```

`outputs/`는 Git에서 제외한다. 노트북에는 저장소 코드와 동결 원본을 옮긴 뒤 같은 명령으로
새 run ID를 생성하거나, 실행 결과가 필요하면 해당 run 디렉터리를 Git 밖에서 별도 복사한다.
clone 범위, runtime 이전과 이어서 쓸 프롬프트는 [`LAPTOP-HANDOFF.md`](LAPTOP-HANDOFF.md)에 정리했다.
