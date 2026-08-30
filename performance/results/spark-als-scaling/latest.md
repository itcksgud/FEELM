# Spark ALS 1→2 worker local scale-out evidence

> Status: `COMPLETED_LOCAL_SAME_HOST`
> Measured at: `2026-08-29T15:54:36.802047+00:00`
> Protocol: `spark-als-scaling-v1`, SHA-256 `61372442ce7959aaced6426f10acc6efb010e368d496fc301182e86d47039409`

## 판정

같은 물리 Windows 호스트에서 Spark standalone worker를 1개에서 2개로 늘렸을 때 ALS fit
중앙값은 `21.456s → 13.468s`, `1.593x`로 개선됐다. 사전에 정한 최소 `1.20x` Gate를 통과했고,
두 topology의 관측 executor 수는 각각 1개와 2개였다. 같은 예측 subset의 RMSE와 coverage 차이는
모두 0이었다.

따라서 **이 측정 workload에서는 worker 추가가 ALS batch 시간을 줄인다는 로컬 공학 근거가 있다.**
이는 같은 PC의 별도 worker JVM 결과이며, 다중 EC2·네트워크 shuffle·HDFS·운영 처리량을 증명하지
않는다.

## 고정 프로토콜

- 입력: `global-time-v1` Train/Validation, 원본 ID를 결과에 기록하지 않는 결정적 hash 20% 표본
- 행 수: Train `5,119,729`, Validation `640,171`
- ALS: explicit, rank 16, maxIter 3, regParam 0.1, seed 42, user/item block 각 32
- topology A: master 1 + worker 1, worker당 2 cores·4 GiB
- topology B: master 1 + worker 2, worker당 2 cores·4 GiB
- 각 topology: warm-up 1회 후 측정 3회, ALS fit 중앙값 비교
- Gate: fit 중앙값 speedup ≥ 1.20, RMSE 절대 차이 ≤ 0.01, prediction coverage 차이 ≤ 1e-12
- runtime: Python 3.12.5, PySpark 4.2.0, host logical CPU 12

## 결과

| Topology | 관측 executor | ALS fit 3회 (s) | 중앙값 (s) | app 전체 중앙값 (s) | RMSE | coverage |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| 1 worker | 1 | 21.456 / 20.911 / 21.461 | 21.456 | 40.084 | 0.915614 | 0.142246 |
| 2 workers | 2 | 13.784 / 13.385 / 13.468 | 13.468 | 31.299 | 0.915614 | 0.142246 |

- ALS fit speedup: `1.593x`
- application total speedup: `1.281x`
- RMSE absolute difference: `0`
- coverage absolute difference: `0`
- Gate: `PASS`

## 해석 제한

RMSE는 Validation 전체가 아니라 두 topology에서 동일하게 예측 가능한 `14.22%` subset의 관찰값이다.
Train과 Validation을 독립 hash 표본화하면서 sample Train에 없는 사용자·영화가 늘었기 때문이다.
따라서 이 RMSE를 추천 품질 채택 근거로 사용하지 않는다. 이 실험이 증명하는 범위는 **동일 입력·동일
모델 설정에서 topology만 바꿨을 때의 실행 시간과 결과 불변성**이다.

다중 서버 채택 전에는 같은 protocol을 실제 노드 간 네트워크·동일 총 자원 비교·worker 장애 복구와
함께 다시 측정해야 한다. HDFS도 이 결과만으로 도입하지 않는다.

## 재현

원본 MovieLens Parquet은 `outputs/`에만 유지한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File performance\run-spark-scaling-gate.ps1 `
  -Repetitions 3 -WarmupRuns 1 -SampleBuckets 20 `
  -CoresPerWorker 2 -WorkerMemoryGiB 4
```

스크립트는 독립 master/worker process와 로그를 `performance/.tmp/`에 만들고 `finally`에서 자신이
시작한 process tree만 종료한다. 기존 Compose와 PostgreSQL volume은 건드리지 않는다.
