# REC-EV-001 — MovieLens 시간 분할·사용자 rating-style

> 상태: `COMPLETED`  
> 생성 시각: 2026-08-29T10:07:29.718061+00:00  
> Protocol: `global-time-v1`  
> Source SHA-256: `e4a68655d7386b8f95f2f2424b2ff975dfdd15ffd59e0d864a14dca43e99d6ee`

## 1. 결론

MovieLens 전체 32,000,204개 평점을 전역 timestamp 기준으로 분할했고, 경계 timestamp를 통째로
뒤 split에 배치해 Train→Validation→Test 사이의 시간 중첩을 제거했다. 검증 결과는
`PASS`다.

사용자별 Train 평균과 raw 4점 이상 비율은 넓게 다르다. 따라서 모든 사용자에게 `rating >= 4`를
동일한 추천 성공 기준으로 쓰지 않는다. 이 결과는 사용자 개인 척도로 정규화하는
`user-ecdf-shrunk-v1`의 필요성을 뒷받침하지만, 어떤 추천 모델이 우수한지는 아직 판단하지 않는다.

또한 기존 사용자·기존 영화로만 평가할 수 있는 warm row coverage는 Validation
`15.03%`, Test
`9.78%`다. 전역 시간 분할은
실제 미래의 신규 사용자·영화 fallback을 평가하는 대표 프로토콜로 유지하되, 개인화 모델 자체는
같은 사용자의 과거가 있는 warm-user diagnostic으로 별도 평가해야 한다.

## 2. 고정 전역 시간 분할

| Split | Rows | Rate | Users | Movies | Time range (UTC) |
| --- | --- | --- | --- | --- | --- |
| train | 25,600,163 | 80.00% | 170,491 | 50,977 | 1995-01-09T11:46:44+00:00 → 2018-10-03T07:21:40+00:00 |
| validation | 3,200,020 | 10.00% | 22,919 | 54,803 | 2018-10-03T07:21:45+00:00 → 2020-11-05T19:45:33+00:00 |
| test | 3,200,021 | 10.00% | 22,252 | 63,300 | 2020-11-05T19:45:35+00:00 → 2023-10-13T02:29:07+00:00 |

경계:

- Train: timestamp `< 1538551305`
- Validation: `1538551305 <= timestamp < 1604605535`
- Test: timestamp `>= 1604605535`

| Split | Warm rows | Warm coverage | New-user rows / users | New-item rows / movies | Both-new rows |
| --- | --- | --- | --- | --- | --- |
| validation | 480,907 | 15.03% | 2,522,073 / 16,196 | 90,703 / 14,746 | 106,337 |
| test | 312,952 | 9.78% | 2,445,284 / 17,561 | 145,629 / 26,745 | 296,156 |

신규 사용자·신규 영화 행을 제거하지 않았다. 후속 모델은 warm coverage와 fallback 포함 coverage를
분리해 보고해야 한다.

## 3. 사용자별 rating-style 차이

Train 사용자 평균 분포:

- P10 `3.092`
- P25 `3.392`
- Median `3.692`
- P75 `3.981`
- P90 `4.238`

| Train mean quartile | Users | Mean rating | Median raw 4+ rate | P10~P90 |
| --- | --- | --- | --- | --- |
| Q1_LOWER_MEAN | 42,623 | 3.063 | 33.3% | 16.6%~46.8% |
| Q2 | 42,720 | 3.550 | 50.0% | 39.4%~60.0% |
| Q3 | 42,535 | 3.832 | 63.7% | 54.5%~72.2% |
| Q4_HIGHER_MEAN | 42,613 | 4.234 | 79.8% | 69.7%~92.4% |

`raw 4+ rate`는 사용자가 Train에서 준 평점 중 4점 이상 비율이다. 이것은 사용자의 영화 선택과
점수 사용 습관이 함께 섞인 관측치이며 성격상의 엄격함을 뜻하지 않는다. 그럼에도 quartile별
차이가 크면 공통 4점 threshold가 사용자마다 다른 양성 비율을 만든다는 사실은 변하지 않는다.

- raw 4+가 한 번도 없는 Train 사용자: 230명
- Train 평점이 모두 raw 4+인 사용자: 1,015명

## 4. Train 이력량

| Train history | Users |
| --- | --- |
| K1-2 | 49 |
| K3-4 | 25 |
| K5-9 | 72 |
| K10-19 | 223 |
| K20-49 | 63,501 |
| K50-99 | 40,417 |
| K100+ | 66,204 |

MovieLens 전체 사용자는 최소 20편을 평가했지만 전역 시간 분할의 Train 시점에는 이력이 더 적은
사용자가 존재한다. 이 분포는 K0/K3/K5/K10 cold-start 실험의 실제 대상 크기를 정하는 근거다.
다만 자연적으로 Train 이력이 20편 미만인 사용자는
369명뿐이므로, 안정적인
cold-start 비교는 충분한 이력이 있는 사용자의 최초 K개만 의도적으로 남기는 시뮬레이션으로 만든다.

## 5. 검증과 재현

- Source row 보존: `True`
- Strict time order: `True`
- 경계 timestamp 분리 금지: `True`
- 사용자 profile 입력: Train only
- 분할 artifact와 profile checksum: manifest에 기록

재현 명령:

```powershell
py -3 scripts/movielens_time_split_profile.py `
  --archive C:\higher\projects\MM\data\raw\ml-32m.zip `
  --output-dir outputs\recommendation-evidence\global-time-v1 `
  --manifest docs\recommendation\evidence\manifests\global-time-v1.json `
  --evidence docs\recommendation\evidence\REC-EV-001-rating-style.md
```

## 6. 판단 가능한 것과 불가능한 것

판단 가능:

- 공통 raw `4점 이상`을 사용자 공통 만족 기준으로 사용하면 사용자별 양성 비율이 달라진다.
- 후속 추천 평가는 개인 Train 분포와 이력량을 반영해야 한다.
- Validation/Test에는 Train에 없던 사용자·영화 fallback 평가가 필요하다.

아직 판단 불가:

- 예상 별점을 화면에 표시할지
- K5와 K10 중 어느 onboarding 부담이 적절한지
- ALS·Hybrid 중 어느 모델이 더 나은지
- confidence 경계와 탐험 손실 허용치

다음 evidence는 `REC-EV-002` Bias·Popularity·ALS 기준선과 예상 별점 calibration이다.
