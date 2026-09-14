# Service v1 B1 TMDB 결측 view 생성 계획

상태: **DRAFT — 실행 코드 작성 전 독립 검토 필요** · 2026-09-13.

## 목적과 범위

기존 RH230 자연 상태와 같은 4,997,069개 학습 목표에 대해 TMDB 대중성 세 채널을 영화 단위로
가린 RH230 축을 만든다. 이 단계는 B1 GBT 학습 입력 준비이며 모델을 학습하거나 평가 목표를
읽지 않는다. KOBIS, 서비스 ID, 실제 서비스 후보, 사용자 평가 문맥도 읽지 않는다.

## 고정 입력

| 입력 | 고정 조건 |
| --- | --- |
| 팀 `training-recipe.v1.json` | SHA-256 `d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b` |
| 팀 `service-v1.json` | SHA-256 `1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343` |
| `rec-ev-045/metadata.parquet` | 85,517행·6,891,831 bytes; SHA-256 `4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d` |
| `text339/catalog.parquet` | 85,517행; SHA-256 `0bde668e0e26f5f82b5c41d90d62c7569fd350bf2a2fd4b59bb4402f438c5947` |
| 기존 RH230 train | 4,997,069행; SHA-256 `9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45` |
| 검증된 row lineage | manifest `3cb6862bdba78fb3014971c4d7ffdc26069e09b261bbe7479bdbdf7add10c265`; Parquet `44fd15d301e5fe9c076ab3df6de3a063bf3a0b43248b1610d0c550e1478d4bce` |
| `foundation340_features.py` | SHA-256 `83e2586d9acc8bfb8aec8dc47b8133f4bc0da8678ba0d82bdd295b5c7bf13cf9` |

수식의 전이 의존 코드도 함께 고정한다.

- `cold_item_features.py`: `78ed5bbea151dc12d7a8bb144fa389333c779550fcfdff66d28d90c6900a4cec`
- `text339_relations.py`: `fdc970a6506cfd27dcc2df15cd2785ce5f221f76bd74eaeb24aa6d73c5ccd4d6`
- `rec047_features.py`: `a6f3fdc26cc88cceea2f6b1fe1244285bd9e29f7175874f7ab2eb8fae5904268`
- `rec046_common.py`: `42dd14e83ecbee0c02c5e4233abb4b6a8d807ad2213c24a22cfb82350bc47848`

모든 입력과 새 구현 파일을 시작과 게시 직전에 다시 해시한다. 어느 하나라도 바뀌면 실패하고
immutable 출력 경로를 게시하지 않는다.

## 영화 mask와 사전 고정 census

MovieLens 정수 영화 ID를 10진 문자열로 만들고 다음 식을 그대로 적용한다.

```text
d = int.from_bytes(SHA256(UTF8("train_mask:339:ml:" + movie_id)), "big")
masked = 5*d < 2^256
```

결과를 보며 20%를 맞추거나 seed를 바꾸지 않는다. 고정 입력에서 결과는 다음과 같아야 한다.

| 항목 | 고정 수 |
| --- | ---: |
| catalog 영화 | 85,517 |
| mask 영화 | 17,099 |
| mask된 목표 행 | 966,274 |
| 전체 이력쌍 | 41,750,890 |
| mask된 이력쌍 | 8,074,083 |
| mask 이력이 하나 이상인 행 | 2,640,486 |
| 목표 또는 이력에 mask가 있는 행 | 3,095,318 |

## 30개 TMDB 특징 재계산

MovieLens catalog의 `movie_id`와 metadata의 `movie_id`는 전 행 동일·고유·엄격 오름차순이어야
한다. 목표와 이력 ID는 `searchsorted` 뒤 원 ID가 정확히 같은지 확인해 metadata 행으로 바꾼다.

자연 metadata 전체에서 기존 `Features`를 한 번 만들어 기존 prior
`mean=6.173088067675869`, `mass=48`, `prior_movies=44,920`을 재확인한다. 그 뒤 별도 masked
crowd 상태에서 mask 영화의 `tmdb_vote_average`, `tmdb_vote_count`, `tmdb_popularity` 세 채널에
대해 **value=0, present=false**를 적용한다. 원시값만 0으로 바꾸면 투표 수와 popularity가
present=true가 되므로 허용하지 않는다. mask 뒤 남은 영화로 prior를 다시 추정하지 않는다.

각 행의 목표와 엄격 과거 이력 전체를 mask한 상태에서 기존 RH 생성 순서를 그대로 재현한다.
먼저 `Features.crowd(..., shrink=False)`로 `x200..x229` 전체를 만들고, 기존 RH가 shrinkage를
적용한 정확한 여섯 열 `x200,x207,x208,x210,x211,x212`만
`Features.crowd(..., shrink=True)` 결과로 덮어쓴다. item value/present, input
fraction/mean/variance/reliability, rating covariance, candidate delta, response cross, input present를
모두 다시 계산한다. 목표가 mask됐다는 이유로 30열 전체를 0으로 만들지 않는다. 이력의 남은
근거가 있으면 통계는 남는다.

같은 lineage로 자연 상태 `Features.batch(...)` 230열을 전부 재생성한 뒤, 기존 RH 순서대로
위 여섯 열을 shrinkage crowd 결과로 덮어쓴다. 이 자연 재생성값과 기존 RH230 전 열의 최대
절대차가 `2e-6` 이하인지 전 행 확인한다. 단순히 원본 앞200열을 복사한 뒤 같다고 판정하지 않는다.
실제 masked 출력의 `x000..x199`, `row_id`, `uid`, `label`은 검증을 마친 기존 RH230에서 그대로
복사해 바이트 값이 전 행 동일해야 한다. label은 0.5 단위인지 확인하고 목표·이력의 정체성과
순서를 바꾸지 않는다.

## 물리 출력과 논리 view

원본 RH230은 복사하지 않고 고정 해시로 참조한다. 새 immutable 디렉터리에는 다음 세 파일만
원자적으로 게시한다.

1. `tmdb-masked-rh230.parquet`: `row_id:int64`, `uid:int32`, `label:float64`,
   `x000..x229:float32`의 4,997,069행.
2. `views-manifest.json`: 아래 네 논리 view와 물리 축을 고정한다.
3. `manifest.json`: 모든 입력·출력 해시, census, parity, runtime, 자원 사용, 미완료 단계를 기록한다.

| view_id | 이름 | 물리 축 | GBT weight |
| ---: | --- | --- | ---: |
| 0 | NATURAL | 기존 RH230 | 0.25 |
| 1 | TMDB_MASKED | 새 masked RH230 | 0.25 |
| 2 | KOBIS_MASKED | 기존 RH230 | 0.25 |
| 3 | BOTH_MASKED | 새 masked RH230 | 0.25 |

학습기는 두 물리 축을 각각 한 번만 읽는 2N행으로 축약하면 안 된다. Spark에서 각 원행의
view_id 0..3을 실제로 만들어 논리 19,988,276행과 원행별 weight 합 1을 검사해야 한다.
`views-manifest.json`은 아직 실행되지 않은 학습을 완료로 표시하지 않으며
`readyForTraining=false`, `modelFitPerformed=false`를 유지한다.

## 실행과 실패 조건

- row lineage와 RH230의 row group을 함께 streaming하고 한 번에 최대 4,096행만 계산한다.
- 전체 4view 또는 전체 feature matrix를 단일 NumPy 배열에 올리지 않는다.
- 입력 Parquet, 출력 writer, 임시 파일은 성공·실패에서 모두 닫는다.
- 자연 crowd parity, 스키마, 행 수, mask census, finite 값, 0.5 label, 출력 재열기, 입력 재해시 중
  하나라도 실패하면 임시 디렉터리를 제거한다.
- 출력 디렉터리가 이미 있으면 덮어쓰지 않는다. 쓰기·종료·게시 실패도 동일하게 정리한다.
- 일반 Python과 `python -O`에서 합성 전체 빌드, mask 경계, 후보/이력 동시 mask, prior 고정,
  자연 parity 실패, 행 대응 실패, 입력 mutation, writer/publish 실패, Windows handle 해제를 검사한다.

이 출력은 독립 결과 감사 후에만 B1 Spark 실행기의 입력 후보가 된다. B1 GBT 학습 성공,
KOBIS245 준비, 서비스 품질, 2026년 한국 사용자 적합성을 의미하지 않는다.
