# 서비스 추천 v1 r5 2노드 Spark/HDFS 학습·평가 계획

상태: **DRAFT — 독립 설계 검토와 팀 계약 결정 전 서버 실행 금지**  
Jira: `S15P21E106-399`  
작성일: 2026-09-14  
실행 대체 대상: `service-v1-b1-r4-server-fit-recovery.md`

## 1. 이번 계획의 결론

추천 v1의 장시간 학습을 단일 EC2 `local[5]`에서 수행하지 않는다. 공식 2노드 Spark
Standalone과 HDFS를 이용하도록 실행을 다시 설계한다. 성능 검증은 MovieLens 1M·10M·32M과
Worker 1·2를 분리해 측정하고, 모델 품질 평가는 같은 고정 snapshot과 사용자 시간순 split에서
수행한다.

이 계획은 GBT를 운영 기본 모델로 확정하지 않는다. 현재 팀의 채택 계약은 ALS+콘텐츠 Hybrid와
Python Fold-in Consumer 경계다. 최신 제품 판단은 사용자가 거의 늘지 않아도 성립하는
[개인 콘텐츠 프로필](service-v1-small-data-korean-strategy.md)을 기본으로 두는 것이다. ALS와
MovieLens GBT/FM은 오프라인 기준선·제한된 보조 신호다. 이 판단을 팀 구현으로 옮기려면 기존
ADR을 대체하는 결정과 모델·HDFS·Consumer 계약이 먼저 필요하다.

따라서 작업은 두 줄로 나눈다.

- **A. 공식 기준선:** 최신 팀 ALS 배치를 2노드/HDFS에서 검증하고 1→2 Worker 분산 성능과
  HDFS 산출물을 남긴다.
- **B. 서비스 v1 후보:** 개인 콘텐츠 프로필이 사용자 수에 의존하지 않는 기본안이다. B1 GBT는
  분산 실행 가능성만 검증하는 개발 모델이며, B3 GBT/FM은 KOBIS 245열 입력 준비와 팀 계약
  변경 후에만 비교한다.

## 2. 권위와 확인한 현재 상태

이 문서는 개인 저장소의 실행 설계다. 팀 계약을 변경하지 않는다. 기준은 다음 순서로 읽는다.

1. 팀 `docs/adr/0001-hdfs-spark-distributed-processing.md`: HDFS/Parquet, PySpark Batch,
   MovieLens 1M→10M→32M과 Worker 1→N 검증.
2. 팀 `docs/adr/0006-aws-ec2-two-nodes.md`: EC2 2노드와 Master/Worker 배치, 자원 경합 관리.
3. 팀 `docs/adr/0002-als-content-hybrid-recommendation.md`: 현재 채택 모델은 ALS+콘텐츠 Hybrid.
4. 팀 `docs/adr/0017-python-foldin-spark-trending-split.md`: ALS 재학습은 Spark Batch, 사용자
   candidate/top-N 재계산은 Python Consumer, Spring은 경량 재정렬.
5. 팀 `infra/README.md`, Compose, HDFS 설정: 현재 실제 실행 계약.

확인한 로컬 팀 저장소 상태는 다음과 같다.

| 항목 | 확인값 | 의미 |
| --- | --- | --- |
| 팀 working revision | `96a4b27d0c...` | 현재 worktree HEAD. 기존 미커밋 파일을 보존한다. |
| 마지막 로컬 remote-tracking revision | `0f0983c691...` | ALS 119 통합 포함. GitLab을 조회하지 않아 원격 최신성은 주장하지 않는다. |
| Spark master | `spark://spark-master:7077` | Master 컨테이너에서 client mode로 제출한다. |
| Worker | 2개, 각 2 Core/4 GiB 광고 | 현재 합계 4 Core/8 GiB. Compose 변경 없이 사용하는 기본값이다. |
| 실행 자원 | total executor 4 Core, executor 2 Core/2 GiB | 기존 실환경 검증값. 새 fit에 충분하다는 뜻은 아니다. |
| Spark 이미지 | `feelm-spark-ml:4.1.3-numpy2.2.6` | 두 서버가 같은 revision에서 같은 이미지 ID를 사용해야 한다. |
| HDFS | `hdfs://hdfs-namenode:8020`, DataNode 2, replication 2 | 각 Job에 `spark.hadoop.dfs.replication=2`를 명시한다. |
| 실측 호스트 메모리 | 각 약 15 GiB라는 과거 기록 | ADR의 각 32 GiB와 불일치한다. 실행일에 다시 확인한다. |
| v1 B1 | 230 특징, 원행 4,997,069, 4-view 논리행 19,988,276 | 분산 요건의 MovieLens 32M 원천 자료를 대신하지 않는다. |
| v1 B3 | 245 특징 | KOBIS 연결·q99·row-lineage가 `NOT_BUILT`라 학습 불가다. |

## 3. 실행 전에 풀어야 하는 계약 결정

아래 항목은 기술값을 임의로 정하지 않는다. 하나라도 미결이면 관련 단계만 `BLOCKED`로 남긴다.

| 결정 | 현재 상태 | 필요한 결정 |
| --- | --- | --- |
| 실제 Spark 자원 | Compose 2+2 Core/4+4 GiB, ADR 계획은 3+5 Core/12~14+20 GiB | 현재값 유지 또는 인프라 ADR/Compose 변경. r5 기본은 현재값 유지이며 full fit 성공을 보장하지 않는다. |
| 운영 모델 권위 | 채택 ADR은 ALS+콘텐츠, 로컬 v1은 GBT 기본 | GBT가 비교군인지 대체 모델인지 팀이 결정하고 ADR을 대체한다. |
| 후보 책임 | ADR-0017은 Consumer의 후보/top-N 재계산, v1 문서는 Spark batch pool500 | candidate 생산자, 갱신 주기, Redis/Parquet schema를 하나로 확정한다. |
| GBT 산출물 | 팀 `pipeline/contracts`에 HDFS GBT 모델 계약 없음 | 경로, schema, 원자 공개, rollback, Consumer 로딩 계약을 추가한다. |
| B3 입력 | KOBIS 확정 crosswalk 0, 245열 `NOT_BUILT` | ID 연결 검산, 누적 기준, q99, row-lineage, 평가 교집합을 먼저 만든다. |
| 분산 성능 판정 | 요구는 있으나 반복 수와 합격 임계값 없음 | r5는 반복과 계산식을 고정해 수치를 보고하며, 임의의 최소 speedup을 사후 생성하지 않는다. |

## 4. 목표 토폴로지와 불변 규칙

현재 서버 이름은 EC2 번호 대신 역할로 기록한다. ADR의 EC2 번호와 현재 배치가 다르기 때문이다.

```text
분산 노드 J15E106A
  Spark Master + Worker 2
  HDFS NameNode + DataNode 2
  Kafka 및 기존 분산 서비스

서비스 노드 J15E106
  Spark Worker 1
  HDFS DataNode 1
  서비스 컨테이너
```

- 같은 승인 revision과 같은 Spark image ID를 두 서버에서 확인한다.
- `spark-submit`은 Master 컨테이너에서 client mode로 실행한다.
- 기존 고정 포트 7079/7080/7081을 사용하는 Job은 동시에 실행하지 않는다.
- HDFS·Kafka·DB·Redis volume을 재생성하지 않는다. `docker compose down -v`와 NameNode
  재포맷을 금지한다.
- 입력, 중간 결과, 모델, 평가 evidence는 run별 HDFS 절대 URI에 저장한다. 로컬 파일만 남긴
  실행은 분산 완료 증거가 아니다.
- 입력 snapshot과 출력 run ID는 no-overwrite다. 실패한 run은 실패 상태를 보존하고 새 run
  ID로 처음부터 재실행한다.
- Worker 장애 시험은 작은 전용 fixture에서만 수행한다. full model fit 중 Worker를 내리지 않는다.

## 5. 데이터와 산출물 경계

실제 내부 주소를 문서에 넣지 않고 실행 환경에서 다음 논리 루트를 주입한다.

```text
<HDFS_ROOT>/recommendation/service-v1/r5/<run-id>/
  input-manifest/
  inputs/
  benchmark/
  models/
  predictions/
  evaluation/
  evidence/
```

입력 manifest는 visible regular file 전체의 byte 수와 SHA-256, 원천 역할, row count, schema,
생성 revision, 전처리 설정, snapshot ID를 기록한다. `_SUCCESS`, CRC, 숨김 파일은 데이터 파일
집합에서 제외하되 정책을 manifest에 적는다. 같은 snapshot을 Worker 1과 2 실행이 함께 사용한다.

모델 품질 비교의 사용자 시간순 train/validation/test, 후보 집합, seed, UNKNOWN 처리, 평점 0.5
단위와 지표 정의는 기존 봉인된 평가 계약을 바꾸지 않는다. Worker 수 비교에서는 모델 품질을
승자 선정에 사용하지 않고, 입력·출력 정합성과 실행 성능만 비교한다.

## 6. 분산 성능 실험

### 6.1 질문

1. 같은 MovieLens 자료와 같은 Job을 Worker 1개에서 2개로 늘렸을 때 처리 시간이 줄고
   throughput이 증가하는가?
2. 데이터가 1M→10M→32M으로 커질 때 두 Worker가 실제로 일하고 산출물 정합성이 유지되는가?
3. 서비스가 함께 실행되는 현재 자원에서 OOM, spill, 과도한 GC 또는 서비스 성능 저하가 생기는가?

### 6.2 고정 매트릭스

| 축 | 값 |
| --- | --- |
| 데이터 | MovieLens32M에서 같은 schema로 만든 exact 1,000,000·10,000,000·32,000,204행 nested snapshot |
| Worker | 1, 2 |
| 반복 | 조합별 3회 measured run. 1M으로 별도 1회 warm-up 후 측정 |
| 순서 | seed 399로 생성한 고정 교차 순서. 같은 순서를 실행 기록에 보존 |
| Job | 동일한 read→schema 검증→ID join→사용자/영화 집계→Parquet write 대표 배치 |
| 저장 | 반복마다 서로 다른 no-overwrite HDFS 출력 경로 |

1M과 10M은 32M 전체 행의 stable row key hash를 정렬해 앞 N개를 취한 exact nested snapshot으로
한 번 생성하고 hash·row count를 고정한다. subset 생성 시간은 benchmark 시간에서 제외한다.
각 데이터 크기 안에서 Worker 1과 2를 비교하고, 데이터 증가에 따른 처리량 변화를 기술한다.
추천 품질은 크기 간 우열을 비교하지 않는다. 기존 B1의 4-view 증식은 이 매트릭스의 32M으로
세지 않는다.

Worker 1 조건은 승인된 점검 시간에 Worker 2를 정상 중단하고 Master에서 `1 Alive`를 확인한
뒤 실행한다. 매 반복 후 산출물을 닫고 Worker 2를 복구해 `2 Alive`와 두 host 참여를 확인한
후 Worker 2 조건을 실행한다. 중단·복구가 승인되지 않으면 Worker 1 결과를 만들지 않고
`BLOCKED_MAINTENANCE_NOT_APPROVED`로 남긴다.

### 6.3 측정값과 계산

- `T`: Spark application 제출부터 `_SUCCESS` 확인까지의 wall-clock seconds
- `throughput = input_rows / T`
- `speedup = median(T_worker1) / median(T_worker2)`
- `efficiency = speedup / 2`
- 반복별 executor hostname, task/partition 수, input/output rows와 파일 수
- stage/task duration, shuffle read/write, spill, peak executor memory, GC time, failed/retried task
- 두 호스트 CPU·memory·disk와 서비스 health/latency의 before/during/after 값

각 조합은 3회 중 성공한 일부만 골라 median을 내지 않는다. 실패가 하나라도 있으면 실패 원인과
성공 회수를 보고하고 그 조합의 공식 speedup 판정은 `INCOMPLETE`다. speedup이 1 이하인 결과도
숨기지 않는다. 최소 합격 수치는 팀에서 사전에 정하지 않았으므로 r5는 관측값과 신뢰 범위만
보고하고 프로젝트 전체의 일반 성능으로 확대하지 않는다.

## 7. 모델 실행 단계

### A. ALS 공식 기준선

마지막으로 로컬에 fetch된 `origin/develop`의 S15P21E106-119 구현을 기준 후보로 삼는다. 서버
실행 전 원격 최신 revision과 MR 상태는 사람이 확인해야 한다.

1. `als_input_manifest.py`로 MovieLens32M 전처리·매핑 snapshot을 고정한다.
2. `als_baseline.py --mode validate`를 2 Worker/HDFS에서 실행한다.
3. 작은 고정 후보 1개로 분산 fit·save·reload·evaluation pilot을 수행한다.
4. pilot이 성공하고 자원 여유가 확인될 때만 119의 전체 설정 비교를 실행한다.
5. `MODEL_SELECTION`, `FINAL_EVALUATION`, `SERVING`, run report와 validation/test evidence를
   HDFS에 no-overwrite로 기록하고 로딩 검증한다.

test는 모델 선택에 쓰지 않는다. SERVING은 전체 평점 재학습 모델이므로 test 점수를 그 모델의
성능으로 표현하지 않는다. ALS 품질은 MovieLens 오프라인 지표이며 2026년 한국 사용자 만족도
정답이라고 주장하지 않는다.

### B. 개인 콘텐츠 기본안과 GBT/FM 비교

- 사용자별 콘텐츠 프로필은 FEELM 사용자 수가 한 명이어도 계산할 수 있는 서비스 기본 후보다.
  Spark Batch는 영화 벡터·이웃·후보 pool을 만들고 온라인 계층은 작은 사용자 프로필로 재정렬한다.
- B1 GBT는 230열 개발 비교와 Spark 분산 실행 가능성 확인까지만 허용한다.
- 기존 r4 runner/worker를 그대로 실행하지 않는다. `local[5]`/`local[4]` 강제, 단일 Docker,
  로컬 경로와 별도 runtime을 제거하고 공식 Spark image·HDFS URI·cluster master를 쓰는 새 Job이
  필요하다.
- B1 pilot은 HDFS 입력을 읽고 모델·예측·resource evidence를 HDFS에 쓴다. 성공해도 서비스 v1
  활성 모델이나 B3로 부르지 않는다.
- B3 GBT/FM은 KOBIS245 input readiness가 PASS이고 새 모델/Consumer 계약이 승인된 뒤 별도
  run ID에서 학습한다.
- ALS+GBT, ALS+FM 등 비교 경로는 동일한 candidate snapshot과 평가 모집단에서만 계산한다.
  ALS 미지원 영화에는 ALS 기본값을 만들지 않고 `UNAVAILABLE`로 유지해 router와 blend를 분리한다.

## 8. 내일 서버 실행 순서

각 단계는 앞 단계의 독립 검토가 PASS일 때만 다음 단계로 간다.

1. **계약 확인:** 실제 호스트 사양, 현재 Compose 자원, 실행 revision, 모델 범위, 점검 시간을 기록한다.
2. **읽기 전용 preflight:** 컨테이너 상태, 두 Worker/DN, 활성 Application/Driver, HDFS health,
   image/runtime parity, 고정 포트, scratch와 서비스 baseline을 확인한다.
3. **소형 cluster/HDFS smoke:** 기존 공식 이미지로 두 executor host, ML, HDFS replication 2와
   no-overwrite를 검증한다.
4. **분산 성능 매트릭스:** 1M→10M→32M × Worker 1/2 대표 배치를 실행한다.
5. **ALS pilot:** 같은 32M snapshot으로 단일 설정 분산 fit·save·reload·평가를 실행한다.
6. **전체 ALS 또는 B1 GBT pilot:** 앞 단계 자원 결과와 모델 계약이 허용한 하나만 실행한다.
7. **독립 결과 검토:** 입력 hash, row/schema, 두 host 참여, 계산, 실패·정리, 서비스 영향과
   주장의 범위를 확인한다.

preflight에서 현재 총 4 Core/8 GiB가 확인되면 12 GiB 이상을 요구하는 기존 GBT recipe를 억지로
실행하지 않는다. 자원 재배분은 Compose/서비스 경합 계약 변경이므로 별도 합의와 rollback 계획
없이 수행하지 않는다.

## 9. 사전 준비와 인수 기준

서버 접속 전 로컬에서 끝낼 일은 다음과 같다.

- [ ] r4 실행 중단 판정과 r5 profile을 독립 검토한다.
- [ ] 팀의 마지막 로컬 remote-tracking revision에서 ALS 119 CLI·테스트·HDFS 계약을 감사한다.
- [ ] 1M/10M/32M 원천 파일의 존재·byte·SHA-256·schema를 inventory로 만든다.
- [ ] 대표 배치 benchmark의 입력·출력 schema와 정확성 oracle을 고정한다.
- [ ] Worker 1/2 실행 순서와 metric JSON schema를 고정한다.
- [ ] 서버에서 실행할 명령은 내부 주소와 secret을 제외한 template으로 만든다.
- [ ] GBT B1의 cluster/HDFS/runtime gap을 코드 단위로 목록화한다.

서버 단계의 완료 조건은 다음과 같다.

- 두 Worker가 실제 partition을 처리했다는 서로 다른 executor hostname 증거가 있다.
- Worker 1/2가 같은 input snapshot·코드·파라미터를 사용하고 각 조합 3회 기록이 있다.
- 1M/10M/32M 각 출력의 row count·schema·집계 checksum이 oracle과 일치한다.
- HDFS 새 파일은 목표/실제 replication 2이며 missing/corrupt/under-replicated block이 없다.
- 성공·실패 run 모두 종료 상태가 명확하고 기존 정상 산출물을 덮어쓰지 않는다.
- 서비스 before/during/after 관측과 OOM·task retry·spill·GC 기록이 있다.
- ALS run은 모델·factor·metadata·evidence를 재로딩 검증한다.
- GBT run은 승인된 모델/HDFS/Consumer 계약 없이는 `DEVELOPMENT_ONLY`로만 기록한다.
- 독립 검토가 수치, 데이터 누수, 분산 증거와 주장 범위를 PASS한다.

## 10. 즉시 중단 조건

- 두 서버 revision 또는 Spark image ID가 다르다.
- Worker나 DataNode가 2개 모두 healthy가 아니거나 활성 Application/Driver가 있다.
- HDFS client의 effective replication이 2가 아니다.
- input manifest와 실제 파일 hash·row/schema가 다르다.
- fixed port를 사용하는 다른 Job이 있거나 서비스 점검 시간이 아니다.
- executor host 증거가 Worker 조건과 다르다.
- OOMKilled, disk 부족, corrupt/missing block, 서비스 health 실패가 발생한다.
- 현재 자원보다 큰 driver/executor/container 설정이 필요하지만 합의된 자원 변경이 없다.
- B1 결과를 B3 또는 운영 모델로 게시하려는 단계에 도달한다.

중단 후 같은 run ID의 실패 단계를 다시 실행하지 않는다. 원인을 기록하고, 입력·코드·자원·계약을
바꾼다면 새 run ID와 새 검토를 사용한다.

## 11. 이 계획으로 말할 수 있는 범위

성공하면 “공식 2노드 Spark/HDFS에서 지정한 MovieLens 배치가 두 Worker를 사용했고, 고정된
자료 규모별 처리 시간과 throughput을 관측했으며, 해당 모델 산출물을 HDFS에서 검증했다”고
말할 수 있다. 이것만으로 GBT가 ALS보다 서비스 만족도가 높다거나 2026년 한국 사용자에게
정답이라거나 추천 v1 전체 API·Consumer가 완성됐다고 말하지 않는다.
