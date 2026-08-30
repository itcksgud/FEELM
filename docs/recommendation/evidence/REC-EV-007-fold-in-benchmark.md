# REC-EV-007 — FastAPI serving·Fold-in benchmark

> 상태: `COMPLETED_LOCAL_PROVISIONAL`  
> 실행일: 2026-08-29  
> Test 사용: `NO`  
> 결정 연결: `DN-C2-005 / REC-PD-008`

## 1. 결론

사전에 잠근 local-loopback Gate는 모두 통과했다. 현재 C2A HTTP 성공 경로는 여전히
`BAYESIAN_POPULARITY_ONLY`, ranking alpha `0.0`, star `DISABLED`다. 아래 Fold-in 수치는
REC-EV-003 cohort-excluded item factor의 계산 코어 진단이며 HTTP 순위 경로가 실행한 시간이 아니다.

로컬 기본값 후보는 Spring outbound timeout `750 ms`, active Rating snapshot healthy-path freshness
목표 `3,000 ms`다. 이전 결과를 stale success로 반환하지 않으며 운영 네트워크·Spring client·컨테이너
자원 제한을 포함한 배포 환경에서 다시 검증하기 전에는 SLA로 부르지 않는다. 이 결과는 예상 별점
활성화, confidence 경계 또는 개인화 순위 개선의 근거가 아니다.

## 2. 측정 전 고정한 조건과 Gate

- Python 3.12, 실제 Uvicorn loopback socket과 `httpx` 0.28.1을 사용했다.
- synthetic service UUID fixture 1,000개를 사용했다. 실제 Catalog·사용자 traffic이 아니다.
- 후보 수 `10/100/1000`, active Rating K `0/1/3/5/10/20`, 동시성 `1/4/8`을 고정했다.
- 각 HTTP 시나리오는 warmup 20회 뒤 120회, 동시성은 240회, artifact reload는 30회,
  Fold-in core는 300회 반복했다.
- percentile은 nearest-rank로 계산했다. Gate는 결과를 보기 전에
  `rec-ev-007-v1` 코드 상수로 고정했다.

| Gate | 사전 기준 | 실측 worst case | 결과 |
| --- | ---: | ---: | --- |
| HTTP 후보 ≤100 p95 | ≤250 ms | 4.1012 ms | PASS |
| HTTP 후보 1000 p95 | ≤1000 ms | 30.6875 ms | PASS |
| HTTP 동시성 4 p95 | ≤500 ms | 15.1896 ms | PASS |
| HTTP 동시성 4 처리량 | ≥20 rps | 332.9953 rps | PASS |
| readiness p95 | ≤50 ms | 1.3466 ms | PASS |
| valid atomic reload p95 | ≤2000 ms | 18.2387 ms | PASS |
| 비활성 Fold-in core p95 | ≤100 ms | 2.1281 ms | PASS |

## 3. 실제 C2A HTTP 경로

Rating K가 바뀌어도 ranking policy와 순서는 Popularity로 유지된다. K는 요청 parsing과 계약 검증
비용만 달리하며 Fold-in을 호출하지 않는다.

| 후보 | K별 p95 범위 | K별 p99 범위 | 처리량 범위 |
| ---: | ---: | ---: | ---: |
| 10 | 1.5569–2.2476 ms | 1.7211–2.6691 ms | 626.6–745.3 rps |
| 100 | 3.3630–4.1012 ms | 3.4564–6.1781 ms | 300.9–330.1 rps |
| 1000 | 28.3595–30.6875 ms | 29.3964–36.5277 ms | 50.6–53.9 rps |

동시성 1/4/8의 후보 100, K10 p95는 각각 `3.3775/15.1896/31.2375 ms`, 처리량은
`321.7/333.0/326.4 rps`였다. 이는 단일 개발 PC의 loopback 결과라 운영 capacity 숫자가 아니다.

## 4. Artifact lifecycle

- 초기 load+validate p95: `23.8100 ms`
- valid atomic reload p95: `18.2387 ms`
- readiness p95: `1.3466 ms`
- checksum을 손상한 reload: 거부, `10.5412 ms`, 기존 READY set 유지

손상 artifact 시험 로그와 결과에는 안전한 reason code만 남기며 host path·token·raw ID를 남기지
않았다.

## 5. 별도 비활성 Fold-in 코어 진단

입력은 REC-EV-003의 cohort-excluded factor `42,299 × 32`, regularization `0.1`이다. payload는
manifest SHA-256으로 확인했으며 결과 metadata에는 checksum·shape만 남기고 원본 경로를 남기지
않았다. K0은 계산할 user factor가 없어 `NOT_APPLICABLE_NO_FOLD_IN`이다.

| Rating K | 후보 10 p95 | 후보 100 p95 | 후보 1000 p95 |
| ---: | ---: | ---: | ---: |
| 1 | 0.9944 ms | 0.9284 ms | 2.1281 ms |
| 3 | 0.8818 ms | 0.8255 ms | 0.9428 ms |
| 5 | 0.7756 ms | 0.8564 ms | 0.9771 ms |
| 10 | 0.7380 ms | 0.7533 ms | 0.9936 ms |
| 20 | 0.7813 ms | 0.7720 ms | 1.3689 ms |

이 표는 `ItemFactorModel.fold_in + score`의 CPU 계산 시간이다. serialization, Spring round-trip,
remote network, DB snapshot read/cache, process contention을 포함하지 않는다. 더 빠르다는 사실도
alpha 0을 바꾸거나 expected-star를 활성화할 품질 근거가 아니다.

## 6. 기술값 선택 규칙과 제한

timeout은 후보≤100과 동시성≤4 HTTP p99의 최댓값에 3을 곱하고 50 ms 단위로 올린 뒤
`750..2000 ms`로 제한한다. healthy freshness는
`max(3000, outbox poll 1000 + 2 × timeout)`을 500 ms 단위로 올린다. 이번 결과는 각각
`750 ms`, `3000 ms`다.

`generated_at`, 환경, 관측 latency/throughput, Gate 결과와 이 권고는 프로토콜 해시에서 제외된다.
따라서 동일 조건은 `protocol_sha256`으로 비교할 수 있다. 반면 전체 result checksum은 실행 시각과
관측값을 포함하므로 매 실행 달라지는 것이 정상이며 manifest에 이를 명시했다.

## 7. 재현과 산출물

```powershell
py -3.12 -m pip install --require-hashes -r recommender\requirements-test.lock
py -3.12 -m pip install --no-deps --no-build-isolation -e recommender
py -3.12 scripts/recommendation_serving_benchmark.py `
  --factor-artifact outputs/recommendation-evidence/rec-ev-003/cohort_excluded_item_factors.npz `
  --factor-manifest docs/recommendation/evidence/manifests/rec-ev-003.json `
  --result docs/recommendation/evidence/results/rec-ev-007-local-20260829.json `
  --manifest docs/recommendation/evidence/manifests/rec-ev-007.json

$env:PYTHONPATH='recommender/src'
py -3.12 -m unittest recommender.tests.test_benchmark -v
```

- machine result: `results/rec-ev-007-local-20260829.json`
- tracked manifest: `manifests/rec-ev-007.json`
- result SHA-256: `e9df695ac8510b1fbe5cc139eb5dfb01f6d8e5521ac63ba37156bb22da8c9fc6`
- protocol SHA-256: `56b3c5f4716fb919b30231ffa014c3d9507fad4baf9a570d56ba14de74dbfca0`

운영 채택 전에는 Spring client와 동일 host topology, TLS/load balancer, container CPU/memory limit,
DB snapshot read와 outbox 지연을 포함한 부하 시험으로 750/3000 ms 후보를 재검증한다.
