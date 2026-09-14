# Service v1 B1 GBT r3 fit recovery

상태: `REVIEWED — IMPLEMENTATION AND PUBLIC PREFLIGHT ALLOWED; FIT EXECUTION BLOCKED UNTIL NEW PREFLIGHT SIBLING REVIEW PASS`

작성 목적: B1 GBT120 seed339의 r2 fit이 90분 제한으로 중단된 사실을 보존하면서, 모델·입력·분할을
바꾸지 않고 다음 한 번의 fit을 새 namespace에서 수행하기 위한 복구 계약을 정한다. 독립 설계 검토를
통과했으므로 이 계약에 따른 runner와 감사기 구현 및 public preflight 실행은 허용한다. 새 public
preflight의 독립 sibling review가 PASS하기 전에는 fit 실행을 허용하지 않으며, 이 문서는 배포를
승인하지 않는다.

## 1. 확인된 r2 사실

| 항목 | 확인값 |
| --- | --- |
| 실패 증거 | `outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r2-fit-failure.json` |
| 실패 증거 pin | 24,854 bytes, SHA-256 `4ad571439af7f290dd32ce27922519f31e3b156f90998b26a69e2c892aba7e37` |
| 실행 시간 | 5,403.813초 |
| 판정 | `timedOut=true`, `resourceStatus=RESOURCE_STOP` |
| 종료 상태 | exit 143, Docker `OOMKilled=false` |
| 작업 위치 | `GradientBoostedTrees.boost → RandomForest.findBestSplits`, 취소된 Spark Job 87 |
| 결과 상태 | worker terminal result 없음, `model/native` 미생성, 성공 fit bundle 미게시 |
| 정리 상태 | `cleanupComplete=true`, 해당 컨테이너 정지·삭제 완료 |

Job ID는 GBT iteration과 1:1로 대응하지 않으므로 Job 87을 tree 수나 완주율로 해석하지 않는다.
immutable failure에서 독립 확인되는 것은 90분 상한 시점에 `estimator.fit` 내부였다는 사실이다.
입력·partition 검증 약 15분, fit 약 75분이라는 구간 분할은 당시 관찰 메모이지만 cleanup된
`run.log`의 content가 failure에 내장되지 않아 지금 독립 재구성할 수 없다. 따라서 이를 실행 시간
추정이나 PASS gate의 봉인된 근거로 쓰지 않는다. 12GiB가 충분하다고 확정할 수도 없다. timeout
시점에는 cgroup peak를 회수하지 못했기 때문이다. 다만 90분 동안 OOM 없이 학습 단계가 계속된
사실은 같은 메모리에서 더 긴 1회 제한 실행을 검토할 약한 근거다.

r2 failure는 실행 command와 stdout, 자원·정리 상태는 내장하지만 cleanup된 fit temp에 있던
`input-lock.json`, `preflight-reference.json`, `run.log`의 내용 전체를 failure 하나만으로 복원할 수
없다. 따라서 r2 failure를 당시 fit 입력 전체가 폐쇄된 증거로 과대해석하지 않는다. r3는 이 결손을
새 preflight의 현재-file 재검증과 아래 self-contained failure 계약으로 보완한다.

r2 full preflight와 sibling review는 성공 증거로 보존한다.

| 증거 | bytes | SHA-256 |
| --- | ---: | --- |
| r2 preflight `manifest.json` | 2,351 | `7a47a40ac923ba1f8a638ad6029aa72349245b65055f3db8915a2327de174e01` |
| r2 preflight sibling review | 8,706 | `cc49688618bfd408a957a22522456912320519d981c34ad87aefb190a0910132` |
| r2 preflight `resource.json` | 1,565 | `a91445352b3fd2f53b84106667686f1d8778ef5b4399d2c15631c7844b634ffd` |
| r2 preflight `input-lock.json` | 3,489 | `fbd4ae898a8cc35e3e4e7c4442298d6940dc88b3854d20182b7f38c87334d775` |

r2 full preflight는 4,997,069 source rows와 19,988,276 logical rows, partition 8을 확인했고
869.359초, peak 10,224,336,896 bytes로 PASS했다. 이는 r3의 데이터 관계 증거로 재사용할 수 있지만,
r2의 outer runner SHA와 run ID를 pin하므로 새 r3 실행을 그대로 승인하는 토큰으로 사용할 수는 없다.

## 2. 변경할 수 없는 학습 계약

r3에서 다음 값은 모두 r2와 같아야 한다.

- 자연 입력 4,997,069행과 4-view 논리 입력 19,988,276행
- 자연 입력 SHA-256 `9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45`
- masked parquet SHA-256 `27aee771597ba230654b2e99c1eea047fd25e3265161c51b6873474d47c65f01`
- masked manifest SHA-256 `82eb3635f1ae914a056c2813c4786dfa2de56abbb37aaf5ce852685f6eb6a557`
- views manifest SHA-256 `df8dd8bbfea4a71e506958c5b7e1499b5010350a326cbfac2c9bfe23a607792a`
- masked sibling review SHA-256 `f7e322206d95c8ad418926a08394625f84a25b611f4e9595c97dd03cca6c7b1c`
- Spark worker SHA-256 `9708e0b3fdc618c5df2240c9e1dcf368fd4f6fa411d7915c31104b3af2146d42`
- Docker image ID
  `sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8`
- Spark 4.1.3, Java 21, Python 3.10, AQE off, shuffle partition 8, 동일 partition/sort 규칙
- GBT seed339, 120 trees, depth5, bins32, step0.05, subsampling0.8, `sqrt`, squared loss,
  `view_weight=0.25` 및 나머지 resolved estimator 전부
- training recipe SHA-256 `d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b`

r2의 `trainingSourceSetSha256=6d82b745ec4e796f823f6506497ad9a15236f92a91a04d3452c4755fada499f9`는
이름과 달리 데이터·계약뿐 아니라 **r2 plan, runner, worker, test, portable dependency, image ID**까지
합친 16-record digest다. 새 r3 runner/profile을 넣으면서 이 digest가 그대로라고 주장하면 안 된다.
이 값은 r2 ancestor의 역사적 pin으로만 보존한다.

r3 lock schema는 다음 세 집합을 분리한다. canonical digest는 r2와 같은
`path NUL bytes NUL sha256 LF`, path ASC 규칙을 사용한다.

| 집합 | 내용 | r2에서 재계산한 기준 |
| --- | --- | --- |
| `modelInputRecords` | recipe·service/model/feature 계약 4개와 natural/masked/manifest/views/review 원천 5개 | 9 records, `cd41a8ac341cdbc1435bbce21bd78e3b3012e2c50ca25cfac0ef034088b1ccfd` |
| `workerRuntimeRecords` | byte-identical Spark worker·portable reader/dependency·Docker image ID | 4 records, `ff90bcd014b8ac498f2c5ccba27eb33d0a83f3ee062d36dc426e33a95c6a0a0a` |
| `executionRecords` | 새 r3 recovery plan/profile/runner/test와 phase별 실행 파일 | r3 구현 후 계산·봉인하며 r2 값과 같다고 요구하지 않음 |

`modelInputSetSha256`와 `workerRuntimeSetSha256`는 위 값과 같아야 한다. r3의
`executionSetSha256`, `controlReferenceSetSha256`, 전체 `inputSetSha256`는 새 record를 포함해 새로
계산한다. 세 source record group의 logical path는 서로 겹치면 안 되며, 전체 `inputSetSha256`는
`modelInputRecords ∪ workerRuntimeRecords ∪ executionRecords ∪ controlReferences`의 정확한 합집합으로
계산한다. 어떤 구현 파일도 worker-runtime/execution을 포함한 전체 lock에서 빠뜨린 채 기존 6d82
digest를 재사용하지 않는다.

행 수, feature, view, seed, estimator, partition 수를 줄이거나 바꾸는 것은 r3 recovery가 아니라 새
실험이다. timeout 이후 자동 재시도도 허용하지 않는다.

## 3. 고정 실행 profile 두 안

자원은 실행자가 CLI에서 임의 입력하지 않는다. 선택된 profile은 별도 versioned 계약과 runner 상수로
고정하고, runner는 `--cpus`, `--memory`, `--driver-memory`, `--timeout` override를 제공하지 않는다.
`execution-profile.json`과 `command.json`에 profile ID와 실제 Docker/Spark 명령을 기록하고 감사기가
정확한 값만 허용한다.

### A. 지금 실행 가능한 로컬 recovery — 1회 bounded probe

| 필드 | 고정값 |
| --- | --- |
| profile ID | `local4c12g-t14400-v1` |
| 최초 실행 run ID | `b1-gbt120-s339-v1-r3-local4c12g-t14400` |
| Docker | `--cpus 4 --memory 12g --memory-swap 12g --network none` |
| Spark | `local[4]`, driver 8g, shuffle partition 8, AQE off |
| fit hard timeout | 14,400초(4시간) |
| 동시 실행 | B1 관련 Spark job 0개, 다른 고부하 job 0개 |

같은 GBT120_s339의 natural 4,997,069행 fit은 30.4분이었다. B1의 logical rows는 정확히 4배이므로
단순 비례 fit 중앙 추정은 121.6분이다. 4시간은 이 값의 약 1.97배다. 입력 검증 시간은 추가되지만
현재 immutable 증거만으로 분리 산정할 수 없다. 이 계산은 실행 상한을 정하는 보조 근거일 뿐 완료
시간 보장이 아니며, 4-view 구성과 GBT 반복 비용이 선형이라는 가정도 결과 주장에 사용하지 않는다.

장점은 데이터 이동과 서버 준비 없이 즉시 실행할 수 있고, r2와 CPU·메모리·Spark topology가 같아
복구 변화가 timeout과 namespace에 집중된다는 점이다. 단점은 fit peak가 관측되지 않았고 과거
natural GBT120조차 strict 12GiB cap을 4,096 bytes 넘은 `EXCEPTION`이었다. 따라서 A는 완주 가능성이
입증된 권고 profile이 아니라, 사용 가능한 밤 시간을 쓰는 **새 namespace의 단 한 번 bounded resource
probe**다. 최대 4시간 동안 로컬 CPU4·RAM12GiB를 점유하며 OOM/timeout 가능성을 명시적으로 감수한다.

### B. 서버 고정 profile — 서버 확인 뒤의 대안

팀의 채택 문서는 EC2 두 대를 각각 8 vCPU/32GB로 계획하고, 두 번째 worker에 5 Core/20GB를
배정한다. 이 문서값은 실제 서버 상태를 증명하지 않으므로 아래 profile의 출발 근거일 뿐이다.

| 필드 | 고정값 |
| --- | --- |
| profile ID | `ec2-8vcpu32g-local5-fit20g-t14400-v1` |
| A보다 먼저 실행할 때 run ID | `b1-gbt120-s339-v1-r3-server5c20g-t14400` |
| A 실패 뒤 실행할 때 run ID | `b1-gbt120-s339-v1-r4-server5c20g-t14400` |
| 최소 host class | x86_64, 8 vCPU, 32GB class |
| Docker | `--cpus 5 --memory 20g --memory-swap 20g --network none` |
| Spark | `local[5]`, driver 12g, shuffle partition 8, AQE off |
| fit hard timeout | 14,400초(4시간) |
| 동시 실행 | maintenance window, B1 관련 Spark job 0개, 자원 경합 batch 0개 |

5 Core/20GB는 팀의 worker2 배정과 일치하며 host에 3 vCPU/약 12GB를 남긴다. 20GiB container는
r2 full preflight 관측 peak 약 9.52GiB의 2.10배이고, 과거 natural GBT120 관측 peak
12,884,905,984 bytes의 약 1.67배다. 이는 안전 여유의 설계 근거이지 B1 fit peak 예측이 아니다.

서버안은 실제 host와 Docker 가용 자원이 확인되기 전에는 실행 BLOCK이다. 기존 두 노드 Spark
cluster로 전환하는 안도 이 profile이 아니다. 현재 worker는 local filesystem의 exact-three masked
directory와 local cgroup을 계약으로 사용하므로, HDFS/standalone-cluster 실행은 별도 구현·preflight가
필요하다.

## 4. 권고와 실행 순서

**완주 가능성 기준 권고는 실제 사전검사를 통과한 B**다. 메모리 headroom이 더 크고 CPU도 1개
늘어난다. 현재는 서버 실측이 없으므로 B 실행은 BLOCK이다.

**지금 할 수 있는 작업 기준으로 A는 조건부 1회 probe**다. 로컬 Docker가 동일 image/runtime과
4CPU/12GiB에서 r2 preflight 및 90분 fit 진입까지 수행했고 데이터 이동이 필요 없다. A가 성공하면
내일 서버에서는 모델 파일 전달·hash 검증과 서비스 연결 작업만 수행할 수 있다. 성공을 전제로
일정을 약속하거나 A를 안정 profile로 표현하지 않는다.

A가 OOM, timeout, 측정 불가 또는 cleanup 실패로 끝나면 같은 namespace와 profile로 다시 실행하지
않는다. 실패를 immutable하게 게시하고 B를 `r4`로 올린 뒤 새 검토를 거친다. 서버가 먼저 준비되어
A를 실행하지 않고 B를 택하면 B가 `r3`다. 두 안을 같은 ordinal 또는 같은 final path로 경쟁 실행하지
않는다.

## 5. r2 preflight 재사용 판정

### 증거 재사용: PASS

r2 preflight bundle과 PASS review는 다음을 입증하는 ancestor evidence로 r3에 포함한다.

- 정확한 입력 hash와 full 19,988,276 logical-row identity
- 동일 Spark worker에서 4-view/weight/partition 계약 통과
- 4CPU/12GiB 환경의 full materialization resource 관측
- v1 preflight 실패에서 r2로 이어진 기존 recovery chain

### 직접 실행 승인 재사용: BLOCK

현재 r2 manifest/review는 r2 outer runner와 `b1-gbt120-s339-v1-r2` namespace를 고정한다. r3
runner와 output path를 허용하도록 기존 증거를 수정하거나 auditor가 run ID 검사를 느슨하게 만들면
안 된다. 선택한 r3 profile에서 새 public preflight와 sibling review를 만든 뒤 fit을 실행한다.

- A의 새 preflight는 같은 자원에서도 새 runner, timeout contract, r2 fit failure ancestry,
  no-clobber를 검증하기 위해 필요하다.
- B의 새 preflight는 host/cgroup, 5CPU/20GiB/driver12GiB command와 full resource 측정을 새로
  검증해야 하므로 반드시 필요하다.
- 새 preflight는 r2 bundle을 덮어쓰지 않고 r2 manifest/review와 r2 fit failure를 control
  references로 pin한다.

전체 r3 preflight의 비용은 r2 관측값 기준 약 15분이다. 장시간 fit과 비교해 작고, 기존 감사 계약을
우회하는 복잡한 예외를 추가하지 않는 편이 안전하다.

## 6. recovery ancestry와 immutable 산출물

선택된 run ID를 `R`이라 할 때 새 경로는 다음과 같다.

```text
R-preflight/
R-preflight-result-review.json
R-fit/
R-fit-result-review.json
R-score/
R-score-result-review.json
```

별도 `R-preflight-failure.json`, `R-fit-failure.json`, `R-score-failure.json`도 각각 단 한 번만 쓸 수
있다. r2의 모든 bundle, review, failure는 읽기 전용이며 변경·이동·삭제하지 않는다.

r3 `recovery-reference.json`은 최소한 다음을 고정한다.

1. r2 preflight manifest 2,351 bytes/SHA `7a47...4e01`과 sibling PASS review
   8,706 bytes/SHA `cc49...132`.
2. r2 preflight bundle의 정확한 inventory와 input/control/implementation digests.
3. r2 fit failure 24,854 bytes/SHA `4ad571...7e37`.
4. r2 failure payload의 phase/status/timeout/OOM/exit/cleanup/model-not-written 사실.
5. 기존 runner SHA `cbc8983a0047b5aa0a2fc5a2d3794080afcf48583fe1be90c6d2dd3dd5af6f8e`,
   기존 plan SHA `c5b90b0d19374588fc5cb7a1d98cd6510a1c3708df9667131df5eb3826c79d49`,
   동일 worker SHA `9708...d42`, 새 runner와 새 profile 계약의 bytes/SHA.
6. model recipe, source rows, logical rows, partition 수, `modelInputSetSha256`와
   `workerRuntimeSetSha256`가 r2 record에서 분리 재계산한 기준과 동일하다는 비교표.

새 preflight의 `input-lock.json`은 r2 ancestor들과 failure를 control references로 포함하고, 새
profile/runner 때문에 달라진 execution/phase input-set digest와 그대로인 model-input/worker-runtime
digest를 구분한다. r2의 혼합 `trainingSourceSetSha256=6d82...`는 ancestor field로만 기록한다.
worker가 바뀌거나 model/input/partition 계약이 하나라도 달라지면 recovery를 중단하고 새 실험으로
설계한다.

각 action은 실행 전에 다음 경로가 모두 없음을 확인한다. final, sibling review, sibling failure,
`.tmp-*`, `.scratch-*`, 해당 prefix의 Docker container 중 하나라도 있으면 자동 정리하지 않고 BLOCK한다.
성공은 같은 filesystem의 임시 directory에서 manifest를 마지막에 flush한 뒤 빈 final path로 한 번만
atomic rename한다. 실패는 성공 bundle을 만들지 않고 immutable failure만 게시한다.

timeout/OOM에서도 resource evidence를 남기도록 host monitor가 실행 중 cgroup peak를 주기적으로
누적한다. stop/remove 전에 마지막 sample과 Docker inspect를 먼저 보존한다. peak를 얻지 못하면
`UNKNOWN`이며 성공으로 승격하지 않는다.

r3 failure는 cleanup으로 temp를 지워도 그 파일 하나만으로 시도한 입력을 닫을 수 있어야 한다. 최소
다음을 failure JSON 내부에 값 또는 exact path/bytes/SHA inventory로 내장한다.

- 해당 phase `input-lock.json`의 full content와 canonical source/control/input-set digests
- 새 preflight/review, r2 preflight/review, r2 fit failure를 잇는 ancestor reference의 full content
  또는 각 referenced file의 complete inventory
- execution profile과 실제 Docker/Spark command, 새 runner/동일 worker/profile-contract pin
- temp `run.log`의 full content 또는 failure 안에 보존되는 bytes/SHA와 log payload
- worker terminal result 유무, 마지막 관측 cgroup peak, exit/OOM/timeout, stop/remove 결과
- success bundle/model이 게시되지 않았다는 경로 census

failure 기록에 필요한 phase lock과 ancestor reference는 컨테이너 실행 전에 host memory에도
canonical payload로 유지한다. temp cleanup 뒤 존재하지 않는 경로만 pin하는 방식은 허용하지 않는다.

## 7. 구현 변경 범위

### runner/worker

- r2 runner `scripts/run_service_v1_b1_gbt.py`, r2 plan
  `docs/recommendation/plans/service-v1-b1-spark-runner.md`, worker와 증거는 byte 단위로 동결한다.
  기존 runner/plan을 제자리 수정하면 r2 PASS review의 현재-file rehash가 깨지므로 허용하지 않는다.
- 선택 profile용 새 runner `scripts/run_service_v1_b1_gbt_r3.py`와 새 profile 계약 파일을 만든다.
- 자원 override CLI 없이 profile ID, CPU, container/driver memory, timeout, run ID를 상수로 고정한다.
- Spark worker의 학습 로직은 byte-identical하게 유지한다.
- public `preflight`, `fit`, `score`는 선택 run ID의 새 경로만 사용한다.
- fit은 새 PASS preflight review와 r2 recovery reference를 모두 재hash한 뒤에만 시작한다.
- fit input lock/manifest에 `execution-profile.json`, `recovery-reference.json`, 새 preflight chain을
  포함한다.

### Spark output auditor

- r2 review가 pin한 auditor를 보존하고 `scripts/audit_service_v1_b1_spark_outputs_r3.py`처럼 새 감사
  entry point를 만든다. 새 감사기가 r2 bundle을 읽는다고 해서 r2 review의 reviewer identity를
  새 감사기로 다시 쓰지 않는다.
- 선택된 run ID와 profile ID를 정확히 요구하고 generic `r2|r3` 허용식을 두지 않는다.
- Docker/Spark command에서 CPU, memory, swap, master, driver memory, partition, AQE, timeout metadata를
  profile과 비교한다.
- r2 fit failure와 r2 preflight bundle/review를 재hash하고 payload 의미까지 검증한다.
- 성공 fit path에 native model inventory, 120 trees, portable threshold parity, resource PASS를
  기존과 같이 요구한다.
- 실패 path와 success path가 동시에 존재하면 BLOCK한다.

### score와 evaluator chain

- score는 새 fit manifest/PASS review/model inventory와 r3 recovery ancestry를 재hash한다.
- score의 예측 대상 93,230행, feature 230개, label 미접근, 정렬과 finite 검사는 바꾸지 않는다.
- evaluator와 evaluation auditor의 고정 `RUN_ID` 및 canonical bundle path를 선택 r3 run ID로 바꾼다.
- selection/confirmation output도 r2 평가 산출물과 겹치지 않는 r3 suffix를 사용한다.
- evaluator가 score→fit→new preflight→r2 preflight/review→r2 fit failure까지 재귀적으로 검증하게
  한다. 평가 metric, calibration, user split, seed, gate는 바꾸지 않는다.
- selection과 confirmation의 독립 sibling auditor도 같은 ancestry와 evaluator/auditor hash를 pin한다.

## 8. 서버 최소 사전검사

B는 아래를 모두 실제 명령 출력으로 봉인한 뒤에만 preflight를 시작한다.

1. x86_64 Linux, `nproc >= 8`, host가 32GB class이며 실행 직전 `MemAvailable >= 24 GiB`.
2. output temp/final이 같은 local filesystem에 있고 atomic rename이 가능함. fit scratch volume의
   사용 가능 공간 `>= 80 GiB`; inode도 충분함.
3. Docker CPU/memory/swap limit 지원, cgroup peak 파일을 실행 중 읽을 수 있음.
4. exact Docker image ID `sha256:de611...46df8`, 컨테이너 내부 Spark/Java/Python 버전 일치.
5. natural/masked/review/recipe/team contract 파일의 bytes/SHA가 고정값과 일치하고 masked root는
   symlink 없는 정확히 세 regular files.
6. r2 preflight manifest/review와 r2 fit failure의 bytes/SHA 및 payload가 일치.
7. 선택 run ID의 success/failure/review/temp/scratch 경로와 container가 모두 없음.
8. 다른 B1 job과 고부하 batch가 없고, 5CPU/20GiB를 4시간까지 배정할 maintenance window 확보.
9. NTP/시계 정상, Docker daemon 재시작 예정 없음, SSH session 종료와 무관하게 host-side supervisor가
   runner를 유지하고 종료 코드를 보존함.

`MemAvailable 24 GiB`와 disk 80GiB는 profile 실행의 사전 하한이다. 실제 팀 문서의 EC2 표기만 보고
통과시키지 않는다. 하나라도 확인할 수 없으면 서버 fit은 BLOCK이다.

## 9. 최종 gate

### r3 구현을 시작해도 되는 조건

- 이 복구 설계가 독립 검토에서 PASS.
- A 또는 B 하나만 선택하고 run ID/profile/output namespace를 고정.
- r2 성공·실패 증거의 현재 bytes/SHA가 위 값과 일치.
- model input/worker runtime/partition/seed 변경이 없고 새 execution set은 별도 digest로 완전 봉인됨.

### r3 preflight PASS 조건

- 선택 profile의 실제 command와 host resource evidence가 계약과 정확히 일치.
- r2 ancestry 및 r2 fit failure pin 전체 PASS.
- full 4,997,069/19,988,276 identity와 8 partitions 재확인.
- resource peak 관측, OOM/timeout 없음, cleanup 완료.
- 새 immutable bundle과 독립 sibling review가 모두 PASS.

### fit 시작 BLOCK 조건

- 새 preflight/review 미완료 또는 hash drift.
- r2 fit failure나 r2 preflight 증거 drift.
- 기존 r3 final/failure/temp/container 존재.
- 선택 profile과 실제 CPU/RAM/timeout command 불일치.
- 서버 B의 최소 host/memory/disk/cgroup/maintenance 조건 미확인.

### fit 성공 조건

- 120 trees native model과 정확한 model inventory가 원자 게시됨.
- threshold fixture의 Spark/portable prediction 최대 절대차 `<=1e-6`.
- resource peak 관측, OOM/timeout 없음, container 정지·삭제 완료.
- fit manifest가 service/scoring을 스스로 승인하지 않고 audit pending 상태로 끝남.
- 독립 fit sibling review가 전체 ancestry, model, resource를 재검증해 PASS.

fit이 성공해도 서비스 배포나 모델 채택을 뜻하지 않는다. 이후 score, selection, confirmation이 각각
새 namespace와 독립 review를 통과해야 한다.
