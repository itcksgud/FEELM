# Service v1 B1 GBT r4 server fit recovery

상태: `DRAFT — INDEPENDENT DESIGN REVIEW PENDING; NO IMPLEMENTATION OR EXECUTION AUTHORIZED`

작성 목적: B1 GBT120 seed339의 r3 로컬 fit이 4시간 제한으로 중단된 사실을 보존하고,
모델·입력·분할·평가를 바꾸지 않은 채 팀 EC2 한 대에서 전달 검증부터 confirmation 평가까지
한 번 수행할 수 있는 구현 계약을 정의한다. 이 초안 자체는 구현, 전달 생성, 서버 public
preflight, fit, score, evaluation, 모델 채택 또는 배포를 승인하지 않는다.

## 1. r3의 확정 결과와 한계

| 항목 | 확인값 |
| --- | --- |
| 실패 증거 | `outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure.json` |
| 실패 증거 pin | 100,384 bytes, SHA-256 `75ad1999a8e24d723c56e0b8ecd4f96a573c2086b86daf675d68ebf889940276` |
| 독립 실패 검토 | `b1-gbt120-s339-v1-r3-local4c12g-t14400-fit-failure-result-review.json`, 8,184 bytes, SHA-256 `4c76189254643d58bb1045c89aa879b64be06ddbce9e88294ae7d6229836191d` |
| 실행 | timeout 14,400초, cleanup 포함 14,404.484초 |
| 종료 | exit 143, timeout=true, pre-stop/final `OOMKilled=false` |
| 자원 | cgroup 2초 간격 6,452표본, peak 10,227,834,880 / 12,884,901,888 bytes(79.38%) |
| 중단 위치 | `GBTRegressor.fit`, Spark Job 272의 `GradientBoostedTrees → RandomForest.findBestSplits` |
| 성공 산출물 | worker terminal result, native model, fit bundle, 성공 review 모두 없음 |
| 정리 | container·outer PID·temp·scratch 모두 없음, cleanup error 0 |

독립 판정은 **failure artifact integrity PASS, downstream BLOCK**이다. 따라서 r3 fit auditor, score,
selection과 confirmation은 실행하지 않았다. Spark Job 번호는 tree 수나 완주율이 아니므로 남은
시간을 추정하는 근거로 사용하지 않는다.

r3 public preflight는 아래 정확한 8개 파일과 sibling review를 남겼다.

| 파일 | bytes | SHA-256 |
| --- | ---: | --- |
| `command.json` | 8,653 | `1c8979c7d60474e871fb3220194fca5eba39b186ad016a6cee7d2d5fc33134eb` |
| `execution-profile.json` | 2,283 | `6074818bf05b640ba3ff2b83a2d03d714635761d7e1f317ceefd1332332f4273` |
| `input-lock.json` | 6,493 | `9a3f678ec0fc454498cbb7dbb54973c4c2b9e3eceaee2d3238b96d51cd15f460` |
| `manifest.json` | 3,149 | `c2f2e7914bd5a222001759d1ecd40119a2fa7a8d17ea3214847050e8535da407` |
| `partition-identity.json` | 2,489 | `2a7f7fbd9000730db749cc673d9a43a5027fa3ca76d517cda826f3f420d45c99` |
| `recovery-reference.json` | 5,314 | `d35f8ecd0c8c5fca4981fc25b2e550f919c197abf37ca35d823ce603eab9f96d` |
| `resource.json` | 8,851 | `bf5714a12c394c835d00f18d473772e00da758b4c2998d2175d7f437a120bace` |
| `run.log` | 12,961 | `be8521910c5506e181ca01e6864885056e88542cbbb0d14e7b6d6e0979628171` |
| sibling review | 11,801 | `0d397589778aff2a04d2df2101ac61344e6ce3e7f9d2e204e47156611f92413a` |

이 preflight review는 한 명의 publisher만 실행한다는 운영 통제 아래 생성됐다. 당시 r3 Spark
auditor에는 동시 publisher race가 남아 있었으므로 이 review는 해당 실행의 역사적 integrity
증거일 뿐 동시 발행 안전성의 증거가 아니다. r4는 12절의 공유 publication primitive로 이 결함을
구현 수준에서 없앤다.

r3 Server B `ec2-8vcpu32g-local5-fit20g-t14400-v1` is **SUPERSEDED** by r4
`ec2-8vcpu32g-local5-fit20g-t28800-v1`. r3 Server B의 14,400초 timeout, run ID와 namespace는
실행·재사용·alias할 수 없고 역사적 초안으로만 남긴다.

## 2. 변경하지 않는 모델과 평가 계약

r4에서 아래 값은 r3와 byte 또는 값 단위로 같아야 한다.

- 자연 입력 4,997,069행, 4-view 논리 입력 19,988,276행, score 93,230행
- 4개 masked view와 view별 weight 0.25
- feature 230개, partition 8개
- GBT seed339, trees120, depth5, bins32, step0.05, subsampling0.8, `sqrt`, squared loss
- model input set SHA-256 `cd41a8ac341cdbc1435bbce21bd78e3b3012e2c50ca25cfac0ef034088b1ccfd`
- worker runtime set SHA-256 `ff90bcd014b8ac498f2c5ccba27eb33d0a83f3ee062d36dc426e33a95c6a0a0a`
- Spark worker SHA-256 `9708e0b3fdc618c5df2240c9e1dcf368fd4f6fa411d7915c31104b3af2146d42`
- Docker image ID `sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8`
- Spark 4.1.3, Java 21, container Python 3.10, AQE off, 동일 sort/partition 규칙
- training recipe SHA-256 `d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b`
- calibration 45명, selection 45명, confirmation 180명과 role digest
  `cf92b686ab21a950611084682e46bfd319426d5dbc3c4f58836a4836b3f416c6`
- Top-2/4/6/10, bootstrap 10,000회, user/movie/sign-flip seed 339/340/341, 기존 gate
- B0/B1 모델 축, `ALL`·`ALS_TARGET_SUPPORTED`·`ALS_TARGET_UNSUPPORTED` strata

행·feature·view·tree를 줄이거나 estimator, label, role, bootstrap 또는 성능 gate를 바꾸는 일은
recovery가 아니라 새 실험이다. r4의 허용 변경은 서버 identity/path/receipt/ancestry, 자원 profile,
원자 발행과 crash 처리뿐이다.

## 3. r3 구현 ancestry pin

r4 public preflight와 모든 후속 단계는 현재 파일을 다시 읽어 아래 r3 구현을 정확히 재hash한다.

| 역할 | 경로 | bytes | SHA-256 |
| --- | --- | ---: | --- |
| plan | `docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md` | 22,087 | `4de13f041532b7249b22f5a2bbfbd7919f79d94e4c25f2ae16fe95104a2b3d9a` |
| profile | `docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json` | 1,522 | `761c210364ab465f9143ded5dba8c2f3fba9a1d34c10f1ea54d742a528494c23` |
| runner | `scripts/run_service_v1_b1_gbt_r3.py` | 143,994 | `783a5a4c7f1783416271de0a1cde6a6be55409ef8b625a757f8a07c153c7a574` |
| Spark auditor | `scripts/audit_service_v1_b1_spark_outputs_r3.py` | 89,009 | `92a9e48597205f54509ccd7fbd9c717cb791bbd31a6998cb9d966177f830d3be` |
| evaluator | `scripts/evaluate_service_v1_b1_r3.py` | 215,832 | `de036adc7b35d151aeaf8006e6a2a64f86130f0d9f49b80ac8fa15ec336fc87a` |
| evaluation auditor | `scripts/audit_service_v1_b1_evaluation_outputs_r3.py` | 165,251 | `7f44c00ade107dd82e54658c39d765df2aa8ac7cfddc8e650ba4bebb230fa28c` |
| runner test | `tests/test_service_v1_b1_gbt_runner_r3.py` | 25,063 | `c348c6d02741ffe4a30635c3a2fc0285c8503f15618cac75b3c65943582145af` |
| Spark auditor test | `tests/test_audit_service_v1_b1_spark_outputs_r3.py` | 19,497 | `12af534b89383318cdc6b8fb27610dca58880737b4b7a970825ee8a70a9a9d18` |
| evaluator test | `tests/test_evaluate_service_v1_b1_r3.py` | 116,597 | `5a69d17ada3074c34ffbdce3e14c18333fc163b58ebf80b6ba59b5dc9b673494` |
| evaluation auditor test | `tests/test_audit_service_v1_b1_evaluation_outputs_r3.py` | 29,865 | `5bb03fd080f4e48ea8c38a142c60095d0448245433808c01174b818602a349b4` |
| normalized evaluation auditor core | evaluator SHA literal 하나를 64개 `0`으로 치환한 UTF-8 source | 계산 규칙으로 결정 | `fdd756107c11d19c6332274017bb8ed4933ddb7f377023b18e43f7d748eaaf50` |

r3 preflight 8개 inventory와 review, r3 fit failure와 failure review도 1절 값으로 pin한다.
r3 `recovery-reference.json`이 가리키는 r2 preflight/review/fit-failure/plan/runner를 재귀적으로
현재 파일에서 확인한다. 하나라도 없거나 bytes/SHA/semantic fact가 다르면 r4 ancestry는 BLOCK이다.

`recovery-reference.json` schema는
`feelm-service-v1-b1-r4-recovery-reference/2`, 상태는
`R3_TIMEOUT_ANCESTRY_VERIFIED`로 고정한다. model, worker-runtime, r4 execution,
r2/r3 control-reference record group은 서로 겹치지 않아야 한다. canonical record digest는
`path NUL bytes NUL sha256 LF`를 ASCII path 순서로 연결해 SHA-256을 계산한다.

## 4. execution profile /2 exact schema

profile은
`docs/recommendation/plans/service-v1-b1-r4-ec2-8vcpu32g-local5c20g-t28800-profile.json`
한 파일이다. parser는 unknown 또는 missing field, JSON `null`, 숫자형 boolean, 정수 대신 실수,
중복 key를 모두 거부한다.

| JSON Pointer | exact type과 제약 |
| --- | --- |
| `/` | object; exact keys `schemaVersion,status,profileId,runId,recoveryOrdinal,executionScope,hostClass,singleAttempt,automaticRetry,docker,spark,timeoutsSeconds,maintenanceWindowsSeconds,serverPreconditions,hostRuntime,publicationRuntime,runnerSupervisor,evaluationSupervisor,resourceObservation,modelContract,authorizationPolicy` |
| `/schemaVersion` | string, `feelm-service-v1-b1-execution-profile/2` |
| `/status` | string, `DRAFT_REQUIRES_INDEPENDENT_REVIEW` |
| `/profileId` | string, `ec2-8vcpu32g-local5-fit20g-t28800-v1` |
| `/runId` | string, `b1-gbt120-s339-v1-r4-server5c20g-t28800` |
| `/recoveryOrdinal` | string, `r4` |
| `/executionScope` | string, `SERVER_THROUGH_CONFIRMATION` |
| `/hostClass` | string, `linux-x86_64-8vcpu-32g-class` |
| `/singleAttempt`, `/automaticRetry` | boolean; 각각 `true`, `false` |
| `/docker` | object; exact keys `cpus,memory,memorySwap,maxMemoryBytes,network,imageMode,imageArchiveRelativePath,imageId,imageUnpackedSizeBytes,singleDockerLoad`; profile literal과 type이 deep-equal |
| `/spark` | object; exact keys `master,driverMemory,shufflePartitions,adaptiveExecution`; string `local[5]`, string `12g`, integer 8, boolean `false` |
| `/timeoutsSeconds` | object; exact integer keys `preflightDryRun,preflightFull,fit,score,evaluationSelection,evaluationConfirmation`, 값 7200/7200/28800/7200/14400/14400 |
| `/maintenanceWindowsSeconds` | object; exact integer keys `preflightDryStart,preflightFullStart,fit,score,evaluationSelection,evaluationConfirmation`, 값 16200/9000/32400/9000/16200/16200 |
| `/serverPreconditions` | object; profile 파일의 exact 15 keys. architecture와 requiredCgroupVersion은 string, CPU/RAM/disk/inode는 integer, 나머지는 boolean |
| `/hostRuntime` | object; profile 파일의 exact 22 keys와 literal/type이 deep-equal |
| `/publicationRuntime` | object; exact keys `canonicalPlatform,localPreparationMode,localDistribution,linuxSourceStandaloneRoot,linuxSourceTeamRoot,linuxEvidenceRoot,linuxDeliveryStagingRoot,windowsMirrorRoot,windowsMirrorAuthoritative`; 앞의 여덟 값은 string, 마지막 값은 boolean `false` |
| `/runnerSupervisor` | object; exact keys `kind,preflightUnit,fitUnit,scoreUnit,cpuQuotaPercent,memoryMaxBytes,memorySwapMaxBytes,tasksMax,killMode,timeoutStopSeconds,cgroupPollIntervalSeconds`; unit/enum은 string, limit는 integer, interval은 JSON number |
| `/evaluationSupervisor` | object; exact keys `kind,selectionUnit,confirmationUnit,selectionMonitorUnit,confirmationMonitorUnit,cpuQuotaPercent,memoryMaxBytes,memorySwapMaxBytes,tasksMax,killMode,timeoutStopSeconds,cgroupPollIntervalSeconds,monitorCpuQuotaPercent,monitorMemoryMaxBytes,monitorTasksMax`; unit/enum은 string, limit는 integer, poll interval은 JSON number |
| `/resourceObservation` | object; poll interval은 JSON number 2.0, 나머지 exact 세 필드는 boolean `true` |
| `/modelContract` | object; 행/feature/partition/seed/tree는 integer, 세 SHA는 lowercase 64-hex string이며 profile 파일의 exact 10 keys |
| `/authorizationPolicy` | object; exact keys `profileMutable,currentState,implementationGates,deliveryBuildGates,serverTransferGates,serverReceiptBuildGates,publicPreflightGates,fitGates,scoreGates,evaluationSelectionGates,evaluationConfirmationGates,permanentlyForbidden`; profileMutable은 boolean `false`, currentState는 고정 string, 나머지는 중복 없는 non-empty string array |

profile은 승인 과정에서 수정하지 않는다. `currentState`는 이 파일이 DRAFT라는 사실이며 이후
권한은 별도 immutable review의 `decision`으로 증명한다. 이 조건형 정책 덕분에 profile의
`false`를 뒤집거나 delivery manifest에서 조기 `true`를 쓰지 않는다.
구현 후 implementation review는 profile 전체 bytes/SHA를 pin하고, 모든 producer와 auditor는
파싱 결과가 이 절의 literal 값 및 independently reviewed profile JSON과 deep-equal인지 확인한다.

## 5. 고정 서버 자원과 phase window

| 항목 | 고정값 |
| --- | --- |
| Docker | `--cpus 5 --memory 20g --memory-swap 20g --network none` |
| Spark | `local[5]`, driver12g, shuffle8, AQE off |
| preflight dry 시작 | dry 7,200초와 뒤따르는 full·정리 시간을 포함해 잔여 예약 >=16,200초 |
| preflight full 시작 | full 7,200초와 정리 시간을 포함해 잔여 예약 >=9,000초 |
| fit | hard timeout 28,800초, 별도 예약 32,400초 |
| score | hard timeout 7,200초, 별도 예약 9,000초 |
| calibrate-select | hard timeout 14,400초, 별도 예약 16,200초 |
| confirmation | hard timeout 14,400초, 별도 예약 16,200초 |
| 재시도 | phase마다 ordinal 1회, 자동 또는 같은 run/phase 재시도 없음 |

5 core/20GiB는 팀 ADR-0006의 EC2 #2 Spark Worker 배정과 일치하고 host에 3 core/약12GiB를
남긴다. r3 peak 9.525GiB(약 9,754MiB)에 대한 20GiB는 2.10배의 관측 여유다. r3는 OOM이 아니라 timeout으로
끝났으므로 메모리 증가는 완료를 보장하지 않는다. 28,800초는 검열된 r3 fit wall time의 2배이고
40 core-hour의 상한이다. fit 예약은 timeout 뒤 마지막 sample, Docker inspect, stop/remove,
failure fsync를 위해 3,600초를 더 둔다.

preflight, fit, score, selection, confirmation은 서로 다른 maintenance window다. 하나의 8시간
예약을 모든 단계가 공유한다고 해석하지 않는다. 각 단계 시작 시 해당 단계의 전체 예약 시간이
남아 있어야 한다.

## 6. canonical design review와 r4 구현 closure

### design review artifact

canonical design review의 logical name은
`b1-gbt120-s339-v1-r4-server5c20g-t28800-design-result-review.json`이고 Linux canonical path는
`/home/kingc/.feelm-r4/evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r4-server5c20g-t28800-design-result-review.json`이다.
schema는 `feelm-service-v1-b1-r4-design-review/1`, exact top-level keys는
`schemaVersion,status,runId,profileId,createdAt,reviewer,target,checks,decision,
dependencyFingerprint,publication`이다.

- `status=PASS`.
- `reviewer` exact keys는 `kind,sessionId,host,processId`; 값 type은 string,string,string,integer다.
- `target` exact keys는 `plan,profile`; 각 값은 12절의 pin record다.
- `checks` exact keys는 `priorP1Closed,priorP2Closed,r3PinsVerified,
  profileSchemaVersion2Valid,draftStatusPreserved,noImplementationPresent,
  targetFilesStableDuringReview`; 모두 boolean `true`다. 마지막 검사는 review가 pin한 plan/profile
  두 파일의 review 시작·종료 bytes/SHA가 같다는 뜻이며 저장소의 기존 다른 변경을 판정 대상으로
  끌어들이지 않는다.
- `decision` exact keys와 PASS 값은
  `designIntegrity=PASS,implementationEligible=true,deliveryBuildEligible=false,
  publicPreflightEligible=false,fitEligible=false,scoreEligible=false,
  evaluationEligible=false,deploymentAuthorized=false`다.
- `dependencyFingerprint`는 plan/profile 두 pin의 canonical record-set SHA-256이다.
- `publication` exact keys는 `mode,canonicalRoot,filesystemType,claimPath,tempPath,
  bootstrapPublisherSource,bootstrapPublisherSha256,interpreterPath,interpreterSha256,
  renameNoReplaceProbe,dependencyFingerprintAtAcquire,dependencyFingerprintBeforeRename,
  requiredPostconditions`다. mode는 `BOOTSTRAP_LINUX_RENAME_NOREPLACE`, filesystemType은
  `ext2/ext3`, renameNoReplaceProbe는 boolean `true`, source/path/SHA/fingerprint 계열은 string이다.
  requiredPostconditions exact keys는 `publishedBytesRehashRequired,fileAndParentFsyncRequired,
  dependencyFingerprintStableRequired,claimIdentityMatchRequired,claimRemovalRequired`이고 모두
  boolean `true`다. 이는 rename 전 관측값과 수행 의무이며 rename 뒤 상태의 self-attestation이 아니다.

design review failure의 exact logical name은
`b1-gbt120-s339-v1-r4-server5c20g-t28800-design-result-review-failure.json`이다.
failure schema는 `feelm-service-v1-b1-r4-design-review-bootstrap-failure/1`, exact keys는
`schemaVersion,status,runId,profileId,phase,createdAt,reviewer,target,error,
dependencyFingerprintAtAcquire,dependencyFingerprintBeforeRename,namespaceCensus,
bootstrapPublication,deploymentAuthorized`다. status=`FAILED`, phase=`design-review-bootstrap`,
deploymentAuthorized=false이고 bootstrapPublication은 위 design bootstrap publication exact
shape다. 이 failure는 shared publication evidence schema를 사용하지 않는다.
design review는 이후 shared publication module의 구현을 승인하는 증거이므로 그 module을 사용할
수 없는 **유일한 bootstrap 예외**다. 독립 reviewer는 full bootstrap publisher source를
`publication.bootstrapPublisherSource`에 넣고 그 UTF-8 SHA를 기록한다. publisher는 아래 Linux
workflow에서 claim `O_CREAT|O_EXCL|O_NOFOLLOW`, temp fsync, dependency 재hash,
`renameat2(RENAME_NOREPLACE)`, parent fsync, post-publication 재hash, claim inode/token 확인을
직접 수행한다. failure/crash에는 claim과 temp를 자동 정리하거나 같은 ordinal로 재시도하지 않는다.
두 번째 독립 read-only process가 review의 full schema, target current SHA, canonical review rehash,
claim/temp 부재와 dependency 안정성을 검증해야 `implementationEligible=true`를 사용할 수 있다.

### local Linux canonical workflow

Windows에서는 source 편집, archive build와 비공개 unit test만 수행한다. public design,
implementation, delivery review와 canonical delivery staging은 WSL2 `Ubuntu`의 ext4
`/dev/sde`에서만 수행한다.

1. WSL에서 `uname -m=x86_64`, distribution `Ubuntu`, canonical root의
   `statfs.f_type=ext2/ext3`와 free bytes >=80GiB를 확인한다. `/mnt/c`의 v9fs는 canonical
   publication parent로 거부한다.
2. Windows source는
   `/mnt/c/higher/projects/FEELM-standalone`과
   `/mnt/c/higher/projects/S15P21E106`에서 `O_RDONLY|O_NOFOLLOW`로만 연다. review 전후
   exact inventory/SHA가 같지 않으면 BLOCK한다.
3. canonical review root는
   `/home/kingc/.feelm-r4/evidence/service-v1-pretraining-20260913`, delivery staging root는
   `/home/kingc/.feelm-r4/delivery/b1-gbt120-s339-v1-r4-server5c20g-t28800`다. 두 root는
   ext4 내부 realpath이고 symlink/hard-link alias가 없어야 한다.
4. design bootstrap을 제외한 모든 producer/reviewer는 12절 shared primitive의 Linux
   integration test를 같은 ext4에서 먼저 PASS한다.
5. Windows 확인용 mirror는
   `outputs/recommendation-evidence/service-v1-r4-review-mirrors` 아래 SHA별 directory에
   `CREATE_NEW`로 복사하고 flush/reopen/rehash한다. mirror는 권한 evidence, delivery input,
   namespace census 또는 canonical artifact가 아니다.
6. 서버 전달 archive는 WSL ext4 delivery staging의 exact inventory와 canonical
   design/implementation/delivery review를 포함한다. delivery manifest가 archive record를
   봉인하고 WSL canonical files를 전송 완료까지 보존한다.

WSL host Python 3.12.3은 모델/evaluation runtime으로 사용하지 않는다. bootstrap review publisher는
자신의 interpreter executable SHA와 version을 review에 기록하는 publication 전용 예외다.

### 구현 source set

canonical design review와 두 번째 검증이 PASS한 뒤에만 아래 exact source set을 새로 만든다.
이 절은 구현 목록이며 현재 초안에서 생성이나 실행을 승인하지 않는다.

### 실행 파일

- `scripts/service_v1_b1_r4_publication.py`
- `scripts/audit_service_v1_b1_r4_implementation.py`
- `scripts/build_service_v1_b1_r4_server_delivery.py`
- `scripts/audit_service_v1_b1_r4_server_delivery.py`
- `scripts/build_service_v1_b1_r4_server_receipt.py`
- `scripts/audit_service_v1_b1_r4_server_receipt.py`
- `scripts/run_service_v1_b1_gbt_r4.py`
- `scripts/audit_service_v1_b1_spark_outputs_r4.py`
- `scripts/evaluate_service_v1_b1_r4.py`
- `scripts/audit_service_v1_b1_evaluation_outputs_r4.py`

### 테스트

- `tests/test_service_v1_b1_r4_publication.py`
- `tests/test_audit_service_v1_b1_r4_implementation.py`
- `tests/test_build_service_v1_b1_r4_server_delivery.py`
- `tests/test_audit_service_v1_b1_r4_server_delivery.py`
- `tests/test_build_service_v1_b1_r4_server_receipt.py`
- `tests/test_audit_service_v1_b1_r4_server_receipt.py`
- `tests/test_service_v1_b1_gbt_runner_r4.py`
- `tests/test_audit_service_v1_b1_spark_outputs_r4.py`
- `tests/test_evaluate_service_v1_b1_r4.py`
- `tests/test_audit_service_v1_b1_evaluation_outputs_r4.py`

### 고정 runtime 설치 자료

- `requirements/service-v1-b1-r4-host-runtime.lock`
- `runtime/service-v1-b1-r4-wheelhouse-manifest.json`
- `runtime/service-v1-b1-r4-wheelhouse/` 아래 wheelhouse manifest가 선언한 exact regular files
- `runtime/feelm-rec046-spark-local.tar`

requirements lock은 host CPython 3.12.3 대상 NumPy 1.26.4, pandas 2.2.3, PyArrow 19.0.1과
모든 전이 의존성을 package/version/artifact SHA-256으로 고정한 pip requirements 형식이다.
모든 requirement line은 `==` version과 하나 이상의 `--hash=sha256:`을 가져야 한다.
wheelhouse manifest는
`schemaVersion,pythonVersion,interpreterTag,abiTag,platformTag,files,wheelhouseSetSha256`
exact keys, 값 `3.12.3,cp312,cp312,manylinux_2_17_x86_64`를 사용한다. 각 file record는
`path,bytes,sha256`만 가진다.

Docker archive는 local image inspect가 ID
`sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8`,
unpacked `Size=936463936` bytes임을 확인한 뒤 정확히 한 번
`docker image save --output runtime/feelm-rec046-spark-local.tar sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8`로 만든다.
936,463,936은 tar bytes가 아니다. archive의 실제 bytes/SHA는 implementation review와 delivery의
regular-file record에서 계산해 봉인한다.

### implementation review evidence

canonical evidence는 Linux canonical root의
`b1-gbt120-s339-v1-r4-server5c20g-t28800-implementation-result-review.json`이고 schema는
`feelm-service-v1-b1-r4-implementation-review/1`이다. exact top-level keys는
`schemaVersion,status,runId,profileId,createdAt,reviewer,target,checks,testRuns,decision,
dependencyFingerprint,publication`다.
`target` exact keys는 `designReview,plan,profile,sourceFiles,testFiles,runtimeFiles`다.
앞의 세 값은 pin record이고 세 array는 각각 위 실행 파일 10개, 테스트 10개, requirements lock,
wheelhouse manifest와 펼친 wheel inventory, Docker archive의 pin을 정확히 한 번씩 포함한다. array의
원소는 pin record exact shape이고 path 중복을 거부한다. `checks` exact keys는
`schemaValid,designReviewPass,r3PinsVerified,sourceSetExact,testSetExact,runtimeSetExact,
noAssertProductionAst,normalTestsPass,optimizedTestsPass,pyCompilePass,negativeTestsPass,
linuxPublicationIntegrationPass,mutualPinPass,namespaceValid`; 모두 boolean `true`다.
`testRuns` exact keys는 `normal,optimized,pyCompile,negative,linuxPublicationIntegration`이고 각
원소 exact keys는 `argv,exitCode,stdoutSha256,stderrSha256,evidence`; argv는 non-empty string array,
exitCode는 integer 0, 두 SHA는 lowercase 64-hex, evidence는 pin record다. normal/optimized는 동일
테스트 집합을 각각 CPython normal/`-O`로 실행하고 Linux publication integration은 WSL ext4에서
실제 multiprocessing·`renameat2(RENAME_NOREPLACE)`를 검증한다.
`decision` exact keys는 `implementationIntegrity,deliveryBuildEligible,publicPreflightEligible,
fitEligible,scoreEligible,evaluationEligible,deploymentAuthorized`이고 PASS review에서 값은
`PASS,true,false,false,false,false,false`다.
`dependencyFingerprint`는 target 전체 pin, checks/testRuns canonical JSON, 현재 implementation auditor와
shared-publication source pin의 record-set digest다. `publication`은 12절 shared exact shape다.

delivery manifest는 이 implementation review를 rehash하고 closure에 포함한다. implementation
review가 빠졌거나 source/test가 하나라도 extra/missing이면 delivery build는 BLOCK이다.
implementation review failure의 exact name은
`b1-gbt120-s339-v1-r4-server5c20g-t28800-implementation-result-review-failure.json`이고
PASS/failure publication 모두 WSL ext4의 shared primitive를 사용한다.

## 7. r4 evaluator와 auditor의 비순환 pin

r4 evaluator는 2절의 계산·입력·평가 semantics를 그대로 유지하고 r4 run/profile/path,
server receipt ancestry와 publication primitive만 바꾼다. evaluation auditor와 evaluator는 다음
순서로 봉인한다.

1. evaluation auditor source에서 정확히 하나인
   `REVIEWED_R4_EVALUATOR_SHA256`의 lowercase 64-hex string literal을 64개 `0` byte로 치환해
   normalized core SHA-256을 계산한다.
2. evaluator의 `REVIEWED_R4_EVALUATION_AUDITOR_CORE_SHA256`에 그 core SHA를 넣는다.
3. evaluator 전체 SHA-256을 계산한다.
4. auditor의 `REVIEWED_R4_EVALUATOR_SHA256`에 evaluator 전체 SHA를 넣는다.
5. auditor normalized core가 1번 값과 같은지 다시 확인한다.
6. evaluator 전체 SHA, auditor 전체 SHA, normalized core SHA와 두 테스트 SHA를 implementation
   review에 각각 기록한다.

치환 대상 AST assignment가 0개 또는 2개 이상이면 BLOCK한다. evaluator에 auditor 전체 SHA를
넣거나 normalized core에 evaluator SHA literal을 남기지 않는다. auditor는 현재 evaluator 전체
파일을 rehash하고 evaluator는 현재 auditor의 normalized core를 rehash한다.

## 8. server delivery 계약

### canonical path와 producer

- manifest:
  Linux canonical root의
  `b1-gbt120-s339-v1-r4-server5c20g-t28800-delivery-manifest.json`
- review:
  같은 디렉터리의
  `b1-gbt120-s339-v1-r4-server5c20g-t28800-delivery-manifest-result-review.json`
- builder: `scripts/build_service_v1_b1_r4_server_delivery.py`
- independent auditor: `scripts/audit_service_v1_b1_r4_server_delivery.py`

delivery schema는 `feelm-service-v1-b1-r4-server-delivery/2`다. exact top-level keys는
`schemaVersion,status,runId,profileId,createdAt,producer,sourceRoots,destinationLayout,
implementationReview,modelInputs,scoreInputs,workerRuntime,evaluationInputs,
controlAndImplementation,ancestorEvidence,crossGroupReferences,crossGroupReferencesSha256,
recordGroupDigests,deliverySetSha256,hostRequirements,authorization,publication`이다.

- `status`는 `DELIVERY_AUDIT_PENDING`이다.
- owning record exact keys는 `recordId,logicalPath,sourceRoot,destinationRelativePath,
  bytes,sha256,kind`다. recordId/path/root/kind는 string, bytes는 non-negative integer,
  SHA는 lowercase 64-hex다. sourceRoot는 `standalone|team|wslEvidence|wslDelivery|virtual`,
  kind는 `regular-file|docker-image-contract`이다.
- `recordId=SHA256(sourceRoot UTF8 || NUL || logicalPath UTF8)`다. regular file은 realpath,
  `st_dev/st_ino`, bytes/SHA를 검사하고 directory는 모든 child regular file로 펼친다.
  virtual Docker image contract bytes는 exact UTF-8 JSON
  `{"imageId":"sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8","imageUnpackedSizeBytes":936463936,"mode":"ARCHIVE"}\n`이다.
- `producer`와 `implementationReview` exact keys는 `recordId` 하나다. 두 recordId가 가리키는
  builder와 implementation review actual record는 `controlAndImplementation`에 정확히 한 번
  들어간다. top-level reference는 owning record가 아니다.
- `sourceRoots` exact keys는 `standalone,team,wslEvidence,wslDelivery`이고 각 값은 생성 시
  lexical absolute Linux string이다.
- `destinationLayout` exact keys는 `siblingRootsRequired,standaloneDirectoryName,
  teamDirectoryName,deliveryDirectoryName,preserveRelativePaths`이며 값은
  `true,FEELM-standalone,S15P21E106,b1-gbt120-s339-v1-r4-server5c20g-t28800,true`다.
- owning group은 `modelInputs,scoreInputs,workerRuntime,evaluationInputs,
  controlAndImplementation,ancestorEvidence.records` **여섯 개**다. 모든 recordId,
  destinationRelativePath와 resolved physical `st_dev/st_ino`는 여섯 group의 union에서
  pairwise-disjoint다.
- `ancestorEvidence` exact keys는 `schemaVersion,records,semanticFacts,recordSetSha256,
  semanticFactsSha256,closureSha256`.
  schema는 `feelm-service-v1-b1-r4-ancestor-evidence/1`; records는 owning record array다.
  semanticFacts exact keys/type은
  `r2RunId:string,r3RunId:string,r3PreflightStatus:string,r3PreflightReviewStatus:string,
  r3FitFailureStatus:string,r3TimedOut:boolean,r3OomKilled:boolean,r3CleanupComplete:boolean,
  r3DownstreamBlocked:boolean,r3ServerBProfileId:string,r3ServerBStatus:string`.
  마지막 값은 `SUPERSEDED`이고 나머지는 1·3절의 literal fact와 같아야 한다.
  `recordSetSha256`는 records의 group digest, `semanticFactsSha256`는 RFC 8785 JCS로 직렬화한
  semanticFacts의 SHA-256, `closureSha256`는 ASCII
  `recordSetSha256 NUL semanticFactsSha256 LF`의 SHA-256이다.
- `crossGroupReferences`는 exact 두 object의 array다. 각 object exact keys는
  `referenceId,consumerGroup,consumerLogicalName,ownerGroup,ownerRecordId`이며 모두 string이다.
  exact reference는
  `evaluation.artifact_manifest → modelInputs의 contract/service-v1.json`,
  `evaluation.score_axis → scoreInputs의 source/natural-score.parquet`다. reference는 digest
  union에 record를 추가하지 않는다. `crossGroupReferencesSha256`는 array를 위 exact 순서로
  RFC 8785 JCS 직렬화한 SHA-256이다.
- `recordGroupDigests` exact keys는 `modelInputs,scoreInputs,workerRuntime,evaluationInputs,
  controlAndImplementation,ancestorEvidence`이며 각 값은 lowercase 64-hex string이다.
- 각 group digest는 record를 `logicalPath NUL bytes NUL sha256 LF`로 encoding하고 logicalPath
  ASCII 순서로 연결한 SHA-256이다. `deliverySetSha256`는 manifest 자신과 cross-reference를
  제외한 여섯 owning group의 정확한 disjoint union을 같은 규칙으로 계산한다.
- `hostRequirements` exact keys는 `profile,serverPreconditions,hostRuntime,runnerSupervisor,
  evaluationSupervisor,docker,spark,timeoutsSeconds,maintenanceWindowsSeconds`; profile은 pin,
  나머지는 reviewed profile의 해당 object와 JSON deep-equal이다.
- `authorization` exact keys와 값은
  `state=DELIVERY_AUDIT_PENDING,serverTransferEligible=false,publicPreflightEligible=false,
  fitEligible=false,scoreEligible=false,evaluationEligible=false,deploymentAuthorized=false`다.
- `publication`은 12절 shared exact shape다.

delivery review schema는 `feelm-service-v1-b1-r4-server-delivery-review/1`, exact keys는
`schemaVersion,status,runId,profileId,createdAt,reviewer,target,checks,decision,
dependencyFingerprint,publication`다. reviewer는 design review와 같은 exact shape다.
target exact keys는 `manifest,implementationReview,deliverySetSha256,
crossGroupReferencesSha256`; 앞의 두 값은 pin record이고 뒤의 두 값은 lowercase SHA다. checks exact keys는
`schemaValid,implementationReviewPass,groupUnionDisjoint,crossReferencesValid,
allFilesRehashed,imageArchiveVerified,ancestryVerified,authorizationValid,
namespaceValid`이고 모두 boolean `true`다. dependencyFingerprint는 target과 current
auditor/shared-publication source pin의 canonical digest이고 publication은 12절 exact shape다.
PASS일 때 `decision` exact 값은
`deliveryIntegrity=PASS,serverTransferEligible=true,publicPreflightEligible=false,
dockerLoadEligible=true,runtimeInstallEligible=true,deploymentAuthorized=false`다. manifest와 review는 preflight를 조기
승인하지 않는다.

## 9. 서버에서 평가할 전체 전달 입력

서버에서 score audit 뒤 selection과 confirmation까지 수행한다. 따라서 delivery에는 학습 9개,
score 1개, worker runtime 4개, Docker archive, r4 control/implementation 전체와 아래 평가 입력을 모두 포함한다.
각 경로는 delivery에서 destination record로 펼쳐지고 서버 receipt에서 다시 hash된다.

학습 logical path exact set은
`contract/training-recipe.v1.json`, `contract/service-v1.json`, `contract/MODELS.md`,
`contract/feature-schema.v1.json`, `source/natural-train.parquet`,
`source/tmdb-masked-train.parquet`, `source/masked-manifest.json`,
`source/views-manifest.json`, `source/masked-review.json`이다. score-only exact set은
`source/natural-score.parquet` 하나다. worker runtime exact set은
`implementation/service_v1_b1_spark_worker.py`,
`implementation/combination340_models.py`, `implementation/rec046_common.py`,
`runtime/docker-image-id`다. model/score/worker group은 r3 input-lock의 physical source record를
재검증한 뒤 동일 logical path로 delivery에 넣는다. Docker archive는 기존
`workerRuntimeSetSha256`를 바꾸지 않도록 `controlAndImplementation`이 소유한다.
특히 `runtime/docker-image-id`는 r3의 71-byte regular file, SHA-256
`07666208de67cba550e28294fc88c22981071e2026894da726c12ea42c806137`을 workerRuntime에서
그대로 보존한다. 새 virtual `runtime/docker-image-contract.json` record와 regular-file
`runtime/feelm-rec046-spark-local.tar` record는 `controlAndImplementation`에 각각 한 번만 둔다.

| logical name | owning group 또는 cross-reference | source path | bytes | SHA-256 |
| --- | --- | --- | ---: | --- |
| artifact manifest | cross-reference to `modelInputs` record `contract/service-v1.json` | team `pipeline/artifacts/service-v1.json` | 15,504 | `1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343` |
| evaluation contract | `evaluationInputs` | team `pipeline/docs/service-v1/EVALUATION.md` | 12,466 | `db7bd86f6b72b2edee7b6a65610bdb177c65937b518288417e3c6bdbb02bf3e3` |
| contexts | `evaluationInputs` | standalone `outputs/recommendation-evidence/text339/contexts.json` | 12,290,713 | `951fc2464bd3aea25ea486c79c33084f6241f7846a3ed626e9d5fc3907a7aab7` |
| catalog | `evaluationInputs` | standalone `outputs/recommendation-evidence/text339/catalog.parquet` | 728,100 | `0bde668e0e26f5f82b5c41d90d62c7569fd350bf2a2fd4b59bb4402f438c5947` |
| labels | `evaluationInputs` | standalone `outputs/recommendation-evidence/text339/labels.parquet` | 75,490 | `e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8` |
| evaluation seal | `evaluationInputs` | standalone `outputs/recommendation-evidence/text339/evaluation-seal.json` | 4,890 | `0ab420fb3e50cf770d9dd7a24d64e5ce8896a4c17b38a7ba5df73398ad6fb9c7` |
| roles | `evaluationInputs` | standalone `outputs/recommendation-evidence/final344/roles.csv` | 5,806 | `466b7cede9cb2d67f2bd7fca4fcdada770943bdd9d5924c35ccfdbe6a4cf2cc9` |
| metadata | `evaluationInputs` | standalone `outputs/recommendation-evidence/rec-ev-045/metadata.parquet` | 6,891,831 | `4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d` |
| score axis | cross-reference to `scoreInputs` | standalone `outputs/recommendation-evidence/foundation340/RH/score.parquet` | 2,985,357 | `9832425537943823f524ae6730aa1059b77d2c0464664f1dc29357bf95078f9b` |
| training ratings | `evaluationInputs` | standalone `outputs/recommendation-evidence/text339/ratings.parquet` | 32,950,407 | `28b46687abec2e0edb3a892ec4f4dbd9d5cca701bf220b2812f9e7cf9b905a63` |
| B0 predictions | `evaluationInputs` | standalone `outputs/recommendation-evidence/final344/GBT120_s339/predictions.npy` | 745,968 | `6520b9094c89824fa2ed833da7618851536d9a7ce3f74d20d2233c71e34cea4f` |
| B0 seal | `evaluationInputs` | standalone `outputs/recommendation-evidence/final344/GBT120_s339-seal.json` | 6,911 | `ba9be1a4769d9cd77a8d8b0a11b437ada10254a030656e98cfa7f7f07d7ffee7` |

ALS factor directory
`outputs/recommendation-evidence/combination340/ALS/item-factors`의 exact physical inventory는
다음 18개다. 빈 `_SUCCESS`와 CRC도 제외하지 않는다.

| child | bytes | SHA-256 |
| --- | ---: | --- |
| `_SUCCESS` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `._SUCCESS.crc` | 8 | `1d44f510ec2ed7595badbec80583316defc14e8dd89130d719724149adfaa07d` |
| `.part-00000-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc` | 5,012 | `11178dfd21ed556940979fa479b90e4aa8428664a408092c0e764ff6510136bd` |
| `.part-00001-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc` | 5,956 | `d8ebea0b42fe459706af7a5345d4b33eece9a2915aae3c5c24938c20947315a9` |
| `.part-00002-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc` | 5,124 | `5302d3a66499097662b384cd57766a05b2b5a5440f54f44a9d0b63321a411289` |
| `.part-00003-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc` | 5,944 | `0340a491786074efad6361c5780b3ff2e65551093a26f0e9e6ddd1e6121171fc` |
| `.part-00004-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc` | 4,968 | `ea8bdfa93384e93bac31484c7c6fdc3d130885d92c22a921d7a84144665bdd30` |
| `.part-00005-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc` | 6,140 | `f1c2e1de9731f9952b87c3ff6d7bf6ca84ed9926f9e3fbd5731029a711f38f29` |
| `.part-00006-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc` | 4,988 | `ebc504ac0f2382deb12b70929da9a103bdf005b6c52c1b5409fa1ca3651b3a5b` |
| `.part-00007-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet.crc` | 6,292 | `10d719359e51b9f57198eefb75099636080a96c61ee04153cc467732d9cb7cff` |
| `part-00000-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet` | 640,387 | `f6d328a55d9059e5e4170215071aeb522381e9aceb247a195cddee2df249ee66` |
| `part-00001-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet` | 761,270 | `de79f165b1ae21beabd1bf6352d029b88b6383567689192f2280a45b9f680fa6` |
| `part-00002-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet` | 654,425 | `0c761ca6573a8c35162af0b77294030b9c4f00cef30b3fbb549689d05397c9aa` |
| `part-00003-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet` | 759,762 | `9819744e3bad16494129969a423a6ee650d5ee3b2ca6e07b18f9121614ca5d76` |
| `part-00004-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet` | 634,621 | `c0230036ee3de8fab7d2b6138a8b45f4ba2b1f15adc19093f16503c4fc5d39c6` |
| `part-00005-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet` | 784,751 | `666787c824db1064feb0f8563d49180a7de87beb18e9a82e8554b313fbb7537b` |
| `part-00006-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet` | 636,997 | `089631f252fbcc9a9cc875836ee0c62db7002b16c175f7f96f997fce37b337f6` |
| `part-00007-bdcc1753-c6f9-4e6d-a9c6-df4b64b3d65c-c000.snappy.parquet` | 804,047 | `1fb552c709468dca889e02d184a61fe911c7ba70e64a24bc5fe0a2b07dbf828b` |

receipt와 preflight는 label 파일 bytes를 SHA-256으로만 확인할 수 있다. 이 작업은
`labelBytesHashed=true,labelRowsRead=false,evaluationTargetsRead=false`로 기록한다.
Parquet schema/row/rating을 읽거나 context와 join하는 동작은 score review PASS 뒤
calibrate-select에서 처음 허용한다. score container에는 labels, contexts, roles, catalog,
metadata, ratings, B0/ALS evaluation mount 자체를 제공하지 않는다.

## 10. server receipt 계약

### canonical bundle과 producer

- bundle:
  `outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r4-server5c20g-t28800-server-receipt`
- exact children:
  `destination-inventory.json`, `host-probe.json`, `maintenance-reservations/` 아래
  `preflight-dry.json,preflight-full.json,fit.json,score.json,calibrate-select.json,confirmation.json`,
  `image-load.json`, `runtime/venv-file-inventory.json`,
  `runtime/service-v1-b1-r4-host-runtime-lock.json`, `manifest.json`
- sibling review:
  `b1-gbt120-s339-v1-r4-server5c20g-t28800-server-receipt-result-review.json`
- producer: `scripts/build_service_v1_b1_r4_server_receipt.py`
- independent auditor: `scripts/audit_service_v1_b1_r4_server_receipt.py`

receipt manifest schema는 `feelm-service-v1-b1-r4-server-receipt/1`, exact keys는
`schemaVersion,status,runId,profileId,createdAt,producer,delivery,actualRoots,
destinationInventory,hostProbe,maintenanceReservations,imageLoad,hostRuntime,filesystem,
rehashSummary,authorization,publication`다.

- `status=RECEIPT_AUDIT_PENDING`.
- `producer`는 pin record다. `delivery` exact keys는
  `manifest,review,deliverySetSha256,crossGroupReferencesSha256`; 앞의 두 값은 pin record,
  뒤의 두 값은 lowercase SHA다.
- `actualRoots` exact keys는 `standalone,team,delivery,commonParent,outputRoot,scratchRoot,
  runtimeRoot`; 모두
  realpath를 거친 absolute string이며 standalone/team은 같은 commonParent의 직접 child다.
- `destinationInventory` exact keys는
  `schemaVersion,records,expectedDeliverySetSha256,actualDeliverySetSha256,
  recordSetSha256,missingCount,extraCount,specialFileCount,hardLinkAliasCount`.
  records는 delivery owning record마다 exact
  `recordId,logicalPath,absolutePath,bytes,sha256,stDev,stIno,nlink,kind`을 갖고
  recordId/string, path/string, counts/integer type을 쓴다. 모든 destination을 서버에서 다시
  읽고 missing/extra/special/hardLinkAlias count는 모두 0이어야 한다.
- `hostProbe`는 `unameMachine,logicalCpu,memTotalBytes,memAvailableBytes,dockerServerVersion,
  cgroupVersion,cgroupControllers,cgroupPeakReadable,scratchFreeBytes,outputFreeBytes,
  scratchFreeInodes,outputFreeInodes,outputDevice,scratchDevice,renameNoReplaceProbe,
  supervisorKind,observedAt` exact fields를 담는다. image는 imageLoad에서만 판정한다.
- `maintenanceReservations` exact keys는 `preflightDry,preflightFull,fit,score,
  calibrateSelect,confirmation`이고 각 값은 대응 canonical child의 pin record다. `imageLoad`,
  `hostRuntime`은 각 canonical child의 pin record다.
- `filesystem` exact keys는 `stageAndFinalSameDevice,regularFilesOnly,symlinkCount,
  junctionCount,fifoCount,socketCount,deviceCount,hardLinkAliasCount,duplicateInodeCount`.
  앞의 두 값은 boolean `true`, 모든 count는 integer 0이다.
- `rehashSummary` exact keys는 `recordsExpected,recordsVerified,labelBytesHashed,
  labelRowsRead,evaluationTargetsRead,dependencyFingerprint,destinationRecordSetSha256,
  venvRecordSetSha256`; count는 integer, booleans는 `true,false,false`, digest는 lowercase SHA다.
- `authorization` exact 값은
  `state=RECEIPT_AUDIT_PENDING,publicPreflightEligible=false,fitEligible=false,
  scoreEligible=false,evaluationEligible=false,deploymentAuthorized=false`.
- `publication`은 12절 shared exact shape다.

각 phase maintenance reservation schema는
`feelm-service-v1-b1-r4-maintenance-reservation/2`, exact keys는
`schemaVersion,status,reservationId,hostIdentity,phase,startsAt,endsAt,
dockerRestartScheduled,hostRebootScheduled,issuedBy,createdAt`다. status=`ACTIVE`,
reservationId는 UUID string, hostIdentity/issuedBy는 non-empty string, 시각은 RFC3339 UTC다.
phase는 파일명과 일치하는 `preflight-dry|preflight-full|fit|score|calibrate-select|confirmation`
중 하나이고 여섯 record에서 각각 정확히 한 번 나타난다. 두 scheduled boolean은 false다.
receipt builder는 각 record의 기간이 해당 profile maintenance window 이상인지 확인한다. 각
dynamic gate는 자기 phase record 하나의 current pin/reservationId/endsAt만 재검증하며 다른 phase
record의 기간이나 reservationId를 대체 사용하지 않는다.

### Docker archive import

delivery review PASS는 `dockerLoadEligible=true`일 때만 서버 receipt builder에게 아래 단 한 번의
load를 승인한다. receipt builder는 archive destination record를 다시 hash한 뒤
`docker image ls --no-trunc --digests --format json`과 exact-ID inspect 결과를
`beforeImageCensus`에 저장한다. 그 다음
`docker load --input runtime/feelm-rec046-spark-local.tar`를 정확히 한 번 호출하고 stdout/stderr,
exit code와 command를 lossless하게 기록한다. 재호출, pull, build, tag 변경은 금지한다.

`image-load.json` schema는 `feelm-service-v1-b1-r4-image-load/1`, exact keys는
`schemaVersion,archive,command,startedAt,completedAt,exitCode,stdout,stderr,
beforeImageCensus,afterImageCensus,loadInvocationCount,expectedImageId,actualImageId,
expectedUnpackedSizeBytes,actualUnpackedSizeBytes,status`다. loadInvocationCount=1,
exitCode=0, expected/actual ID는
`sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8`,
expected/actual unpacked size는 936,463,936, status=`PASS`여야 한다. archive bytes는 manifest
record에서 가져오며 936,463,936과 같다고 요구하지 않는다. load 또는 최종 inspect가 실패하면
receipt final을 만들지 않고 exact receipt-build failure를 게시해 downstream을 BLOCK한다.

`archive`는 destination-inventory의 regular-file record pin이고 `command`는 absolute archive
path를 마지막 원소로 갖는 exact string array `docker,load,--input,PATH`다. 두 image census는
ASCII imageId 순으로 정렬한 array이며 원소 exact keys는
`imageId,repoTags,repoDigests,sizeBytes`; imageId는 string, 두 repo 값은 ASCII-sorted string array,
sizeBytes는 non-negative integer다. stdout/stderr는 string, 시각은 RFC3339 UTC, exitCode와
loadInvocationCount/size는 integer다.

### sealed host runtime

delivery review의 `runtimeInstallEligible=true`만 host runtime 설치를 승인한다. 서버의
`python3`을 realpath로 해석해 regular executable, CPython 3.12.3인지 먼저 확인하고
`STANDALONE_ROOT`는 receipt `actualRoots.standalone`, `DELIVERY_ROOT`는
`actualRoots.delivery`, `RUNTIME_HOME`은
`STANDALONE_ROOT/.runtime/service-v1-b1-r4/home`, `VENV_ROOT`는
`STANDALONE_ROOT/.runtime/service-v1-b1-r4/venv`, `VENV_PYTHON`은
`VENV_ROOT/bin/python3`의 exact realpath로 정의한다. VENV_ROOT가 없을 때 아래 순서로 정확히
한 번 만든다.

1. clean environment
   `HOME=RUNTIME_HOME`,
   `PATH=/usr/bin:/bin`, `PYTHONNOUSERSITE=1`, `PIP_CONFIG_FILE=/dev/null`,
   `PYTHONDONTWRITEBYTECODE=1`, `LC_ALL=C.UTF-8`, `TZ=UTC`를 사용하고
   `PYTHONPATH`는 unset한다.
2. `python3 -I -m venv --copies VENV_ROOT`.
3. absolute `VENV_PYTHON -I -m pip install
   --no-index --require-hashes --only-binary=:all: --find-links
   DELIVERY_ROOT/runtime/service-v1-b1-r4-wheelhouse -r
   DELIVERY_ROOT/requirements/service-v1-b1-r4-host-runtime.lock`.
4. 생성된 venv의 directory를 제외한 모든 entry를 `lstat`한다. generated `lib64` link는
   exact target `lib`임을 확인한 뒤 seal 전에 제거하며 그 외 symlink, FIFO, socket, device,
   hard-link alias는 BLOCK한다.
5. 모든 regular file을 relative path ASCII 순으로 `path NUL bytes NUL sha256 LF` digest에
   넣는다. 이후 venv는 read-only이며 어느 phase도 pip, bytecode 또는 cache를 쓰지 않는다.

venv inventory schema는 `feelm-service-v1-b1-r4-venv-inventory/1`, exact keys는
`schemaVersion,root,records,regularFileCount,symlinkCount,specialFileCount,
hardLinkAliasCount,recordSetSha256`다. record exact keys는
`path,bytes,sha256,mode,stDev,stIno,nlink`; path/SHA는 string이고 나머지는 integer다.

host runtime lock schema는 `feelm-service-v1-b1-r4-host-runtime-lock/1`, exact keys는
`schemaVersion,createdAt,bootstrapInterpreter,absoluteInterpreter,interpreterBytes,
interpreterSha256,pythonImplementation,pythonVersion,environment,requirementsLock,
wheelhouseManifest,venvInventory,packages,imports,evaluationFixture,runtimeSetSha256`다.
`bootstrapInterpreter` exact keys는 `command,absolutePath,bytes,sha256,version`.
`absoluteInterpreter`은 profile의 interpreterRelativePath를 standalone root에 resolve한
exact realpath이고 regular file이어야 한다. `environment` exact keys/values는
`pythonPath=UNSET,pythonNoUserSite=true,isolatedMode=true,pythonDontWriteBytecode=true,
locale=C.UTF-8,timezone=UTC`다. requirements/wheelhouse/venv는 pin record다.
`packages`는 exact keys `numpy,pandas,pyarrow`; 각 값은
`version,modulePath,moduleBytes,moduleSha256,distributionRecordSetSha256` exact fields를
가진다. 버전은 1.26.4/2.2.3/19.0.1이다. `imports` exact keys는
`command,exitCode,stdoutSha256,stderrSha256,status`, evaluationFixture exact keys는
`inputSha256,outputSha256,status`다. runtimeSetSha256은 interpreter, requirements,
wheelhouse manifest, complete venv inventory와 fixture를 canonical digest한 값이다.

후속 모든 CLI는
`env -i HOME=RUNTIME_HOME PATH=VENV_ROOT/bin:/usr/bin:/bin PYTHONNOUSERSITE=1
PIP_CONFIG_FILE=/dev/null PYTHONDONTWRITEBYTECODE=1 LC_ALL=C.UTF-8 TZ=UTC
VENV_PYTHON -I` prefix로 실행한다. PYTHONPATH는 존재하면 BLOCK한다. 시작 전후
interpreter와 complete venv inventory를 다시 hash한다. WSL Python 3.12.3이나 전역 Python을
모델/evaluation runtime으로 사용하지 않는다.
profile의 `hostRuntime.lockRelativePath`는 server-receipt bundle root에 대해서만 resolve하며
canonical 결과는 위 exact child
`runtime/service-v1-b1-r4-host-runtime-lock.json`이어야 한다.

receipt review schema는 `feelm-service-v1-b1-r4-server-receipt-review/1`, exact keys는
`schemaVersion,status,runId,profileId,createdAt,reviewer,target,checks,decision,
dependencyFingerprint,publication`다. target exact keys는
`manifest,destinationInventory,hostProbe,maintenanceReservations,imageLoad,hostRuntime`이다.
maintenanceReservations는 receipt manifest와 같은 exact six-pin object이고 나머지는 pin record다.
checks exact keys는
`deliveryReviewPass,destinationRehashPass,filesystemPass,imageLoadPass,runtimePass,
hostPass,reservationPass,labelGatePreserved,namespacePass`이며 모두 boolean `true`다.
dependencyFingerprint와 publication은 8·12절과 같은 형식이다. PASS review의 decision exact 값은
`receiptIntegrity=PASS,publicPreflightEligible=true,fitEligible=false,
deploymentAuthorized=false`다.

public preflight CLI는 다음 인수를 required로 받고 expected SHA를 lowercase 64-hex로 검증한 뒤
어떤 학습/evaluation 데이터도 열기 전에 current-file rehash를 수행한다.

- `--server-receipt-manifest`
- `--expected-server-receipt-manifest-sha256`
- `--server-receipt-review`
- `--expected-server-receipt-review-sha256`
- `--host-runtime-lock`
- `--expected-host-runtime-lock-sha256`
- `--delivery-manifest`
- `--expected-delivery-manifest-sha256`
- `--delivery-review`
- `--expected-delivery-review-sha256`

canonical path, schema, decision, full dependency fingerprint와 current interpreter가 모두 맞아야
preflight host gate로 진행한다.

## 11. 동적 host gate

receipt는 수령 시점의 snapshot이다. 아래 검사는 **preflight dry, preflight full, fit, score,
calibrate-select, confirmation 직전마다** 새로 수행하고 각 bundle의 host-gate JSON에 넣는다.

dynamic gate schema는 `feelm-service-v1-b1-r4-dynamic-host-gate/2`; exact top-level keys는
`schemaVersion,status,runId,profileId,phase,checkedAt,maintenanceReservationId,
reservationEndsAt,maintenanceWindowRemainingSeconds,requiredWindowSeconds,architecture,
logicalCpu,memory,filesystem,docker,cgroup,supervisor,competingProcessCensus,
dockerRestartScheduled,hostRebootScheduled,namespaceCensus,runtime,probeCommands,
probeSetSha256,unknownFields`다.

- phase exact enum은 `preflight-dry|preflight-full|fit|score|calibrate-select|confirmation`.
  requiredWindowSeconds는 차례대로 16200/9000/32400/9000/16200/16200이다.
  `reservationEndsAt - checkedAt`의 floor integer가
  `maintenanceWindowRemainingSeconds`이고 required 이상이어야 한다.
- architecture는 `uname -m` stdout exact `x86_64`. logicalCpu는
  `getconf _NPROCESSORS_ONLN` base-10 integer >=8이다.
- memory exact keys는 `memTotalBytes,memAvailableBytes,source`. source는
  `/proc/meminfo-kib-times-1024`, 값은 각각 >=32,000,000,000와 >=25,769,803,776이다.
- filesystem exact keys는
  `scratchFreeBytes,outputFreeBytes,scratchFreeInodes,outputFreeInodes,scratchDevice,
  outputDevice,stageDevice,finalDevice,filesystemType,renameNoReplaceProbe`.
  Python `os.statvfs`와 `os.stat().st_dev`로 계산하고 thresholds는 profile과 같으며
  stageDevice=finalDevice다. same parent에서 두 새 파일을 사용해 libc
  `renameat2(RENAME_NOREPLACE)` 성공과 existing destination의 `EEXIST`를 모두 확인한다.
- docker exact keys는
  `serverVersion,daemonId,imageId,imageUnpackedSizeBytes,imageInspectSha256,
  runningB1Containers,restartPolicy`. `docker version --format {{json .Server}}`,
  `docker info --format {{json .}}`, exact-ID `docker image inspect` JSON을 canonicalize한다.
  image ID/Size는 profile과 같고 runningB1Containers는 빈 array, restartPolicy=`no`다.
- cgroup exact keys는 `version,controllers,probeContainerId,probeInitPid,cgroupPath,
  readableFiles,probeStatus`. `/sys/fs/cgroup/cgroup.controllers`가 존재하고
  cpu,memory,pids를 포함해야 한다. exact-cap 1초 probe container의 init PID를 Docker inspect로
  얻고 `/proc/PID/cgroup`의 `0::` entry로 cgroupPath를 정한다.
  `memory.current,memory.peak,memory.events,cpu.stat,pids.current`를 모두 읽어야 PASS다.
- supervisor exact keys는
  `kind,phaseUnitName,probeUnitName,phaseUnitExistsBefore,probeStartCommand,
  probeControlGroup,probeMainPid,probePidTree,
  cpuQuotaPercent,memoryMaxBytes,memorySwapMaxBytes,tasksMax,killMode,
  timeoutStopSeconds,status`. phase에 따라 reviewed profile의 runnerSupervisor 또는
  evaluationSupervisor exact unit/limit을 사용한다. phaseUnitExistsBefore=false이고 actual phase는
  아직 시작하지 않는다. `phaseUnitName`의 `.service` 앞에 `-probe`를 붙인 새 unit에서
  `/usr/bin/sleep 2`만 같은 property로 실행한다. active 2초 안에
  `systemctl show probeUnitName -p ControlGroup -p MainPID`로 경로/PID를 얻고 `/proc` PPid
  관계를 재귀 추적해 probePidTree를 PID 오름차순으로 기록한다. probe exit 0과 unit collect를
  확인한 뒤에만 status=PASS다. actual phase의 `systemd-run --unit phaseUnitName --collect
  --wait --pipe`는 전체 dynamic gate PASS 뒤 별도로 시작한다.
- competingProcessCensus 원소 exact keys는 `kind,pid,ppid,containerId,identitySha256`.
  `/proc/[0-9]*/cmdline`과 running Docker inspect를 읽어
  `spark-submit|org.apache.spark|service_v1_b1|run_service_v1_b1` identity를 찾고 현재
  supervisor/auditor PID tree를 제외해 정렬한다. 결과는 빈 array여야 한다.
- maintenanceReservationId/reservationEndsAt는 receipt의 현재 phase active reservation record와
  같아야 하며 gate는 그 단일 record의 current bytes/SHA도 rehash한다.
  `dockerRestartScheduled`와 `hostRebootScheduled`는 reservation의 false를 재확인한 뒤
  `systemctl list-jobs --no-legend`, `systemctl list-timers --all --no-legend`,
  `shutdown --show` 결과에서 docker/reboot/shutdown/poweroff 예정이 없을 때만 false다.
- namespaceCensus exact keys는 `completed,failures,reviews,claims,temps,scratches,
  containers,processes`; 값은 basename 또는 ID의 ASCII-sorted string array다.
- runtime exact keys는 `lock,interpreterSha256,venvRecordSetSha256,environment,
  importFixtureStatus`; lock은 pin, 두 digest는 lowercase SHA, environment는 10절 exact object,
  status=`PASS`다.
- probeCommands는 argv의 array-of-string array이며 위 순서로 고정한다. probeSetSha256은
  각 canonical stdout/stderr/exit record의 digest다. PASS에서 unknownFields는 빈 string array다.

어느 probe든 unavailable/unknown이면 status=`BLOCK`이다. container나 evaluator를 시작하지 않고
15절 immutable phase failure를 게시한다. fit/score/evaluation은 오래된 host gate를 재사용하지
않는다. preflight dry PASS 뒤 full 직전에 별도 gate를 만들며 full은 잔여 >=9,000초를 다시 요구한다.
WSL2 Ubuntu는 publication primitive의 ext4 검증에만 사용한다. 현재 WSL
`/sys/fs/cgroup/cgroup.controllers`가 없으므로 server receipt, dynamic host gate 또는 evaluator
cgroup-v2 PASS 근거로 사용할 수 없다.

### evaluator 전용 cgroup-v2 supervision

calibrate-select와 confirmation은 EC2 system manager의 transient service에서만 실행한다. runner는
각 phase dynamic gate PASS 뒤 reviewed profile의 selection/confirmation unit을
`systemd-run --collect --wait --pipe`로 시작하며 `CPUQuota=500%`,
`MemoryMax=21474836480`, `MemorySwapMax=0`, `TasksMax=4096`, `KillMode=control-group`,
`RuntimeMaxSec=14400`, `TimeoutStopSec=900`을 exact property로 전달한다. evaluator argv는 10절의
sealed absolute interpreter와 clean environment를 사용한다.

runner는 evaluator와 다른 reviewed monitor unit을 먼저 시작한다. monitor unit limit은
`CPUQuota=25%`, `MemoryMax=268435456`, `MemorySwapMax=0`, `TasksMax=64`,
`KillMode=control-group`이다. monitor는 `systemctl show UNIT --property=ControlGroup,MainPID`의
값과 `/proc` PPid를 사용해 target cgroup path와 전체 PID tree를 정하고 2초마다 cgroup-v2의
`memory.current`, `memory.peak`, `memory.events`, `cpu.stat`, `pids.current`를 읽는다. evaluator
cgroup 안에서 monitor를 실행하거나 evaluator가 자기 resource file을 쓰게 해서는 안 된다.

각 evaluation bundle의 `evaluator-resource.json` schema는
`feelm-service-v1-b1-r4-evaluator-resource/1`; exact top-level keys는
`schemaVersion,status,runId,profileId,phase,unitName,monitorUnitName,controlGroup,mainPid,
initialPidTree,limits,sampleIntervalSeconds,samples,peaks,finalEvents,termination,cleanup`다.
phase는 `calibrate-select|confirmation`; path/unit/status는 string, mainPid는 positive integer,
sample interval은 JSON number 2.0이다. PID record exact keys는
`pid,ppid,startTimeTicks,cmdlineSha256`이고 앞의 셋은 non-negative integer, SHA는 lowercase
64-hex다. `limits` exact keys는 `cpuQuotaPercent,memoryMaxBytes,memorySwapMaxBytes,tasksMax,
killMode,runtimeMaxSeconds,timeoutStopSeconds`이며 profile literal과 deep-equal이다.
sample exact keys는 `observedAt,monotonicNanos,memoryCurrentBytes,memoryPeakBytes,
memoryEvents,cpuUsageUsec,cpuUserUsec,cpuSystemUsec,pidsCurrent,pidTree`; 시각은 RFC3339 UTC,
수치는 non-negative integer, pidTree는 PID record array다. memoryEvents와 finalEvents exact keys는
`low,high,max,oom,oomKill,oomGroupKill`이며 값은 non-negative integer다. `peaks` exact keys는
`memoryPeakBytes,maxPidsCurrent,lastSampleAt`. `termination` exact keys는
`timedOut,oomKilled,exitCode,signal,stopRequestedAt,finalSampleAt`; 앞의 둘은 boolean,
exitCode는 integer, signal/세 시각은 string 또는 실행 경로상 허용된 JSON null이다. `cleanup`
exact keys는 `targetInactive,monitorExitCode,unitCollected,pidTreeEmpty,errors,complete`; 네 상태와
complete는 boolean, monitorExitCode는 integer, errors는 string array다.

PASS는 target exit 0, timeout/OOM 없음, 하나 이상의 sample, 알려진 peak, PID tree/cgroup 일치,
monitor exit 0과 complete cleanup을 모두 요구한다. timeout/OOM/monitor failure/unknown peak이면
마지막 sample 후 target unit을 stop하고 control-group 전체 종료·unit collect를 확인해 15절 failure를
게시한다. 이 실제 cgroup-v2 evidence는 EC2에서만 만들 수 있으며 WSL mock/test로 대체할 수 없다.

## 12. 모든 public producer와 reviewer의 원자 publication

`scripts/service_v1_b1_r4_publication.py` 하나가 delivery/receipt/preflight/fit/score/
selection/confirmation producer와 implementation/delivery/receipt/Spark/evaluation reviewer에
공통 API를 제공한다. design review만 6절의 bootstrap 예외다. Windows와 v9fs에서는 이 API가
`BLOCKED_UNSUPPORTED_FILESYSTEM`으로 끝나며 canonical bytes를 쓰지 않는다.

public API exact surface는 다음 네 함수다.

- `acquire_publication(role,phase,final_path,failure_path,expected_namespace) -> Lease`
- `publish_success(lease,staging_path,dependency_fingerprint,rehash_callback) -> Pin`
- `publish_handled_failure(lease,failure_payload,dependency_fingerprint,rehash_callback) -> Pin`
- `release_verified_claim(lease,published_pin,post_publication_fingerprint) -> None`

design bootstrap을 제외한 모든 public producer terminal manifest, handled failure, PASS review와
review failure의 `publication`은 schema `feelm-service-v1-b1-r4-publication-evidence/1`의 exact
object다. exact keys는 `schemaVersion,mode,role,finalPath,failurePath,claimPath,tempPath,token,
claimStDev,claimStIno,filesystemType,publisher,dependencyFingerprintAtAcquire,
dependencyFingerprintBeforeRename,renameNoReplaceProbe,requiredPostconditions`다.
mode=`LINUX_RENAME_NOREPLACE`, role=`PRODUCER|REVIEWER`, path/token/filesystem은 string,
stDev/stIno는 non-negative integer, publisher는 pin record, 두 fingerprint는 lowercase 64-hex,
renameNoReplaceProbe는 boolean `true`다. `requiredPostconditions` exact keys는
`publishedBytesRehashRequired,fileAndParentFsyncRequired,dependencyFingerprintStableRequired,
claimIdentityMatchRequired,claimRemovalRequired`이고 모두 boolean `true`다. producer directory의
finalPath는 directory absolute path이고 tempPath는 sibling directory다. 이 embedded object는
rename 전 관측값과 postcondition 의무만 담고 rename 뒤 rehash/fsync/claim 제거를 self-attest하지
않는다. failure payload도 같은 값을 precommit한다.

role은 `PRODUCER|REVIEWER`, phase는 문서에 열거한 exact phase enum이다. final path가
`A.json`이면 failure는 `A`에서 마지막 `.json`만 제거한 뒤 `-failure.json`을 붙인다.
directory producer는 13절의 exact producer-failure path를 명시적으로 전달한다. claim은
`.<final-basename>.claim`, temp는 `.<final-basename>.tmp-UUID`다.

1. API는 canonical parent가 Linux ext4/xfs임을 `statfs`로 확인하고 allowed namespace를 먼저
   exact 비교한다.
2. claim을 `openat(O_CREAT|O_EXCL|O_NOFOLLOW,0600)`으로 만든다.
3. claim schema `feelm-service-v1-b1-r4-publication-claim/2`의 exact keys
   `schemaVersion,role,runId,profileId,phase,finalPath,failurePath,token,pid,hostname,
   publisherPath,publisherSha256,startedAt`를 쓰고 file/parent를 fsync한다.
4. claim descriptor의 `st_dev,st_ino`와 UUID token을 메모리에 보존한다. 경쟁 loser는 temp를
   만들지 않고 `BLOCKED_NO_WRITE`다.
5. owner만 dependency 검증과 temp 작성에 들어간다. file temp와 directory의 모든 child/parent를
   fsync한다.
6. publication 직전 dependency fingerprint를 다시 계산해 acquire 시점과 같아야 한다.
7. `renameat2(RENAME_NOREPLACE)`로 success final 또는 handled failure 하나를 게시하고 parent를
   fsync한다.
8. publication 직후 published bytes/schema/inventory와 모든 dependency를 다시 rehash한다.
9. post-publication fingerprint가 직전 값과 같고, claim을 `O_NOFOLLOW`로 재open해 token,
   `st_dev,st_ino`, owner PID가 모두 같을 때만 claim을 unlink하고 parent를 fsync한다.

위 8~9의 실제 post-state는 artifact 안의 boolean으로 주장하지 않는다. 다음 consumer/auditor가
canonical final을 재hash하고 exact namespace에서 claim/temp 부재와 dependency 안정성을 확인해야
해당 artifact의 decision을 사용할 수 있다. 다음 consumer가 없는 confirmation review도 결과 해석
전에 별도 read-only closure check가 같은 검사를 수행한다. post-state 확인 전, 또는 drift/claim
잔존 시 embedded decision과 무관하게 BLOCK한다.

| 상태 | success final | handled failure | temp | claim | 후속 동작 |
| --- | --- | --- | --- | --- | --- |
| success + stable rehash | 정확히 1개 | 없음 | 없음 | verified unlink 뒤 없음 | 다음 gate 가능 |
| handled failure + stable rehash | 없음 | 정확히 1개 | 없음 | verified unlink 뒤 없음 | 현재 run terminal BLOCK |
| crash before publication | 없음 | 없음 | 0개 또는 1개 | 그대로 보존 | 자동 청소·재시도 금지 |
| crash after publication before unlink | 0개 또는 1개 | 0개 또는 1개 | 없음 | 그대로 보존 | 두 publication column 합계=1, incomplete BLOCK |
| post-publication dependency drift | 0개 또는 1개 | 0개 또는 1개 | 없음 | **반드시 보존** | 두 publication column 합계=1, drift BLOCK |
| concurrent loser | 변경 없음 | 변경 없음 | 생성 0개 | owner claim만 존재 | `BLOCKED_NO_WRITE` |

표의 “둘 중 정확히 1개”는 success final과 handled failure의 합집합 cardinality가 1이라는 뜻이다.
둘이 같이 있거나 temp/claim owner가 불명확하면 BLOCK이다. TTL, PID 부재 또는 게시 파일 존재를
이유로 claim을 자동 삭제하지 않는다. forensic 이후에도 같은 run/phase를 재사용하지 않고 새
ordinal 설계가 필요하다.

## 13. 단계별 exact namespace

namespace 검사는 output root에서 정확히
`b1-gbt120-s339-v1-r4-server5c20g-t28800-` prefix를 가진 direct child와 숨김
claim/temp/scratch를 대상으로 한다. r2/r3 ancestor는 별도 pinned set이고 r4 prefix 집합에 섞지 않는다.

### 정상 경로의 허용 direct children

| checkpoint | 존재해야 하는 exact completed children |
| --- | --- |
| design review 전 | 없음 |
| design review PASS 뒤 | `b1-gbt120-s339-v1-r4-server5c20g-t28800-design-result-review.json` |
| implementation review PASS 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-implementation-result-review.json` |
| delivery build 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-delivery-manifest.json` |
| delivery review PASS 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-delivery-manifest-result-review.json` |
| receipt build 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-server-receipt/` |
| receipt review PASS 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-server-receipt-result-review.json` |
| preflight producer 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-preflight/` |
| preflight review PASS 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-preflight-result-review.json` |
| fit producer 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-fit/` |
| fit review PASS 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-fit-result-review.json` |
| score producer 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-score/` |
| score review PASS 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-score-result-review.json` |
| selection producer 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-selection/` |
| selection review PASS 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-selection-result-review.json` |
| confirmation producer 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-confirmation/` |
| confirmation review PASS 뒤 | 앞 항목 + `b1-gbt120-s339-v1-r4-server5c20g-t28800-confirmation-result-review.json` |

각 producer 시작 전에는 현재 checkpoint 이전 행의 completed set만 존재해야 한다. 현재 단계의
final, `-failure.json`, `-result-review.json`, `-result-review-failure.json`, claim, temp,
scratch, Docker container와 host PID는 모두 없어야 한다. reviewer 시작 전에는 target final까지
포함한 completed set만 존재하고 해당 review/failure/claim/temp가 없어야 한다. 성공 뒤 hidden
child는 0개여야 한다.

failure 경로에서는 이전 completed set에 현재 phase의 exact `-failure.json` 하나만 추가하고
현재 phase final/review는 없다. audit failure 경로에서는 producer final과 exact
`-result-review-failure.json` 하나가 존재하며 PASS review는 없다. 어느 failure 경로도 같은
ordinal의 다음 phase를 허용하지 않는다.

| phase | producer failure | review failure |
| --- | --- | --- |
| design review bootstrap | 해당 없음 | `b1-gbt120-s339-v1-r4-server5c20g-t28800-design-result-review-failure.json` |
| implementation review | 해당 없음 | `b1-gbt120-s339-v1-r4-server5c20g-t28800-implementation-result-review-failure.json` |
| delivery | `b1-gbt120-s339-v1-r4-server5c20g-t28800-delivery-failure.json` | `b1-gbt120-s339-v1-r4-server5c20g-t28800-delivery-manifest-result-review-failure.json` |
| server receipt | `b1-gbt120-s339-v1-r4-server5c20g-t28800-server-receipt-failure.json` | `b1-gbt120-s339-v1-r4-server5c20g-t28800-server-receipt-result-review-failure.json` |
| preflight | `b1-gbt120-s339-v1-r4-server5c20g-t28800-preflight-failure.json` | `b1-gbt120-s339-v1-r4-server5c20g-t28800-preflight-result-review-failure.json` |
| fit | `b1-gbt120-s339-v1-r4-server5c20g-t28800-fit-failure.json` | `b1-gbt120-s339-v1-r4-server5c20g-t28800-fit-result-review-failure.json` |
| score | `b1-gbt120-s339-v1-r4-server5c20g-t28800-score-failure.json` | `b1-gbt120-s339-v1-r4-server5c20g-t28800-score-result-review-failure.json` |
| selection | `b1-gbt120-s339-v1-r4-server5c20g-t28800-selection-failure.json` | `b1-gbt120-s339-v1-r4-server5c20g-t28800-selection-result-review-failure.json` |
| confirmation | `b1-gbt120-s339-v1-r4-server5c20g-t28800-confirmation-failure.json` | `b1-gbt120-s339-v1-r4-server5c20g-t28800-confirmation-result-review-failure.json` |

### public bundle exact inventories

- preflight: `command.json,execution-profile.json,input-lock.json,manifest.json,
  partition-identity.json,recovery-reference.json,delivery-reference.json,
  server-receipt-reference.json,host-gate-dry.json,host-gate-full.json,resource.json,run.log`.
- fit: `command.json,execution-profile.json,input-lock.json,manifest.json,recovery-reference.json,
  delivery-reference.json,server-receipt-reference.json,preflight-reference.json,host-gate.json,
  partition-identity.json,resolved-estimator.json,model-file-inventory.json,
  threshold-fixtures.npz,fit-metrics.json,resource.json,run.log`과
  `model/native/` 아래 `model-file-inventory.json`이 선언한 exact regular-file inventory.
  r3 이름·payload를 유지하며 parity 결과는 r3처럼 `fit-metrics.json.portableParity`에 둔다.
- score: `command.json,execution-profile.json,score-input-lock.json,manifest.json,
  recovery-reference.json,delivery-reference.json,server-receipt-reference.json,
  fit-reference.json,host-gate.json,resource.json,run.log,score/analyzed-plan.txt`과
  single physical `score/predictions.parquet`. analyzed plan validation은 r3와 같다.
- selection: `evaluation-input-lock.json,truth-integrity.json,score-reference.json,
  delivery-reference.json,server-receipt-reference.json,host-gate.json,command.json,
  role-membership.csv,affine.json,metrics.json,bootstrap-summary.json,strata-census.json,
  gates.json,evaluator-resource.json,run.log,manifest.json`.
- confirmation: `selection-reference.json,score-reference.json,evaluation-input-lock.json,
  truth-integrity.json,delivery-reference.json,server-receipt-reference.json,host-gate.json,
  command.json,metrics.json,bootstrap-summary.json,strata-census.json,gates.json,run.log,
  evaluator-resource.json,manifest.json`.

regular file 외 symlink, junction, FIFO, socket, device, hard-link alias는 모든 source, bundle,
temp, final, review, claim에서 금지한다.

## 14. phase gate와 label-access 순서

### Public preflight

receipt review PASS, delivery review PASS와 새 dry host gate PASS가 먼저다. dry가 성공하고 review
대상 evidence에 들어간 뒤 새 full host gate를 수행한다. full은 source 4,997,069, logical
19,988,276, partition8, input identity, resource observation과 cleanup을 확인한다. dry는 full보다
먼저 terminal PASS여야 한다. preflight manifest는
`fitAuthorized=false,modelFitPerformed=false,scorePerformed=false,readyForService=false`다.
independent Spark review의 `decision.fitEligible=true`만 fit gate가 된다.

### Fit

fit 직전 dynamic host gate와 preflight manifest/review, delivery/receipt, 전체 ancestry를 현재
bytes에서 재검증한다. fit은 120-tree native model, exact inventory, threshold fixture portable
parity <=1e-6, no OOM/timeout, resource peak와 cleanup을 요구한다. manifest는
`status=AUDIT_PENDING,scoringAuthorized=false,readyForService=false`. independent fit review의
`decision.scoreEligible=true` 전에는 score를 실행하지 않는다.

### Score

score 직전 dynamic host gate와 audited fit chain을 재검증한다. 93,230행, 230 features,
row/order/finite/census를 확인하고 label/evaluation mount를 제공하지 않는다.
`labelProjected=false,evaluationTargetsRead=false,evaluationAuthorized=false,
readyForService=false`를 manifest에 기록한다. independent score review의
`decision.evaluationSelectionEligible=true` 전에는 evaluator가 evaluation input을 열지 않는다.

### Calibrate-select

evaluator CLI는 score manifest/review, delivery/receipt와 runtime lock을 먼저 rehash하고
score review gate를 확인한다. 그 전에는 labels, contexts, catalog, roles, metadata, ratings,
B0 predictions/seal, ALS factor의 path resolve·open·stat을 하지 않는다. gate PASS 뒤 새 dynamic
host gate를 거쳐 9절 입력을 열고 기존 role/label/axis/disjointness와 bootstrap 계산을 수행한다.
selection manifest는
`requiredSelectionGate=PASS,confirmationAuthorized=false,readyForService=false`다.
independent evaluation review의
`status=PASS,decision.confirmationEligible=true`와 logical state가 정확히 일치해야 confirmation을
시작한다.

### Confirmation

confirmation은 selection manifest/review gate를 evaluation source open 전에 검증하고 새 dynamic
host gate를 수행한다. selection에서 고정한 affine/policy만 confirmation 180명에 적용한다.
confirmation bundle과 independent review는 별도 namespace다. evaluation review PASS는 계산
무결성 판정이며 성능 gate PASS, 모델 채택 또는 배포 승인을 뜻하지 않는다.

## 15. self-contained phase failure /2

delivery-build, receipt-build, preflight, fit, score, calibrate-select, confirmation producer는
container 생성 전 host gate 실패를 포함해 모든 handled failure를 해당 phase의 immutable
`-failure.json`으로 닫는다. schema는 `feelm-service-v1-b1-r4-phase-failure/2`다.

exact top-level fields와 type은 다음과 같다.

| field | type과 내용 |
| --- | --- |
| `schemaVersion,status,runId,profileId,phase` | string; status는 `FAILED`, phase는 고정 enum |
| `attemptOrdinal` | integer 1 |
| `startedAt,failedAt` | RFC3339 UTC string |
| `elapsedSeconds` | finite JSON number >=0 |
| `failureStage,failureKind` | string enum; pre-container, host-gate, input-lock, container-create, worker, timeout, output-validation, publication, cleanup을 구분 |
| `error` | object exact `type,message,traceback`; 세 값 모두 string이며 traceback 전체를 내장 |
| `producer,profile,delivery,receipt` | 현재 bytes/SHA/path record. delivery와 receipt는 manifest/review/runtime-lock pin을 모두 포함 |
| `phaseInputLock` | 해당 input lock JSON full content; 파일이 만들어지기 전이면 host memory에서 구성한 intended lock full content |
| `ancestorClosure` | r2/r3/r4 reference의 full record inventory와 semantic fact object |
| `authorizationEvidence` | 이전 PASS review들의 full pin과 decision array |
| `hostGate` | 현재 dynamic host gate full content; gate 구성 중 실패면 수집된 값과 unknownFields array |
| `command` | intended/actual argv array, cwd, environment allow-list, Docker/Spark command full content |
| `logs` | exact `stdout,stderr,runnerLog,stdoutSha256,stderrSha256,runnerLogSha256`; cleanup될 temp log의 full string을 내장 |
| `resource` | object exact `status,pollIntervalSeconds,samples,peakMemoryBytes,lastMemoryBytes,cgroupVersion,events`. samples 원소 exact keys는 `observedAt,memoryCurrentBytes,memoryPeakBytes,cpuUsageUsec,oom,oomKill`; 각 sample을 JSON array에 lossless하게 내장 |
| `container` | exact `created,containerId,preStopInspect,finalInspect,exitCode,oomKilled`; 생성 전 값은 boolean false와 JSON null |
| `timeout` | exact `limitSeconds,timedOut,lastSampleBeforeStop,inspectBeforeStop,stopRequestedAt` |
| `cleanup` | exact `monitorStopped,containerStopped,containerRemoved,tempRemoved,scratchRemoved,errors,complete` |
| `namespaceCensus` | publication 전후 direct-child/hidden/container/PID exact arrays |
| `outputState` | exact `finalPublished,reviewPublished,modelWritten,predictionsWritten,nextPhaseBlocked` booleans |
| `dependencyFingerprintBefore,dependencyFingerprintAfter` | lowercase SHA-256 string; 계산 불가 시 원인까지 hash한 canonical failure-state digest |
| `publication` | 12절 shared publication evidence exact object |
| `readyForService,deploymentAuthorized` | boolean `false` |

모든 pin record의 exact shape는 `path` string, `bytes` non-negative integer, `sha256`
lowercase 64-hex string이다. `producer`와 `profile`은 pin record 하나다. `delivery`와
`receipt`의 exact keys는 `available,manifest,review,runtimeLock`; available은 boolean이고
나머지는 pin record 또는 JSON `null`이다. delivery-build 전 receipt와 아직 만들어지지 않은
record만 null일 수 있다. `command` exact keys는 `cwd,intendedArgv,actualArgv,
environmentAllowList,dockerArgv,sparkSubmitArgv`; cwd는 absolute string이고 나머지는 string
array이며 실행 전 failure에서는 actual/docker/spark array가 비어 있다.

`resource.status`는 `NOT_STARTED|OBSERVED|MEASUREMENT_FAILED`,
`cgroupVersion`은 `v1|v2|NOT_STARTED`, `events`는 string-to-non-negative-integer object다.
resource가 시작되지 않았으면 samples는 빈 array이고 peak/last bytes만 null이다. 시작 뒤 각 sample
timestamp는 RFC3339 UTC, 네 수치 필드는 non-negative integer, oom/oomKill은 boolean이다.
`container.created`가 false일 때 containerId, 두 inspect, exitCode, oomKilled는 null이다.
true이면 containerId는 non-empty string, inspect는 full JSON object, exitCode는 integer 또는
실행 중 null, oomKilled는 boolean이다. `failureStage` exact enum은
`PRE_CONTAINER|HOST_GATE|INPUT_LOCK|CONTAINER_CREATE|WORKER|TIMEOUT`,
`OUTPUT_VALIDATION|PUBLICATION|CLEANUP`을 합친 9개 값이다. `failureKind` exact enum은
`CONTRACT|RESOURCE|TIMEOUT|OOM|PROCESS|IO|AUDIT|CLEANUP`이다.
schema에 적은 object는 unknown/missing key를 거부하고 array는 원래 순서를 보존한다.

failure publisher도 no-replace, fsync와 namespace claim을 사용한다. timeout은 마지막 cgroup sample
→ pre-stop inspect → stop/terminate → final inspect → remove 순서를 지킨다. exit143과 OOM은 별도
필드로 기록한다. failure JSON 하나만으로 cleanup 뒤에도 입력, 명령, 로그, resource, ancestry,
delivery/receipt, namespace와 성공 산출물 부재를 재검증할 수 있어야 한다.

design review bootstrap을 제외한 auditor의 handled failure는 별도
`feelm-service-v1-b1-r4-review-failure/1`로 기록한다. exact keys는
`schemaVersion,status,runId,profileId,phase,createdAt,auditor,target,error,
dependencyFingerprintBefore,dependencyFingerprintAfter,namespaceCensus,
passReviewPublished,deploymentAuthorized,publication`; 마지막 두 boolean 값은 `false,false`이고
publication은 12절 exact object다. auditor는 6절 reviewer exact shape, target은 review 대상의
pin record array, error는 exact `type,message,traceback` string object다.

## 16. 구현 및 독립 검증 조건

r4 구현은 다음을 모두 통과해야 implementation review PASS 후보가 된다.

- 모든 JSON exact field/type/duplicate-key, UTF-8 LF, finite number 검사
- normal과 `python -O`의 전체 단위·계약 테스트
- py_compile, production AST `assert=0`
- r2/r3 pin 대상의 테스트 전후 SHA 불변
- r3 failure/review bytes/SHA/semantic fact/ancestry 누락·변조 negative
- model input/runtime/row/view/feature/seed/tree/partition/evaluation contract drift negative
- profile /2 missing/extra/type/null/duplicate key와 자원/timeout/window drift negative
- delivery missing/extra/moved/alias/special-file/digest/group overlap negative
- receipt destination rehash, actual roots, host/runtime/filesystem, stale receipt/review negative
- preflight CLI receipt/delivery expected SHA 누락·오타·noncanonical path negative
- label gate 이전 evaluation file open/stat/resolve 0건을 spy로 확인
- fit/score/selection/confirmation 직전 동적 host gate stale/부족 negative
- pre-container failure부터 timeout/OOM/cleanup/publication crash까지 failure /2 exact schema
- concurrent reviewer 최소 32 process에서 owner 1, canonical review 1, loser temp 0
- claim 생성, temp fsync, rename 직전, rename 직후, dependency 재검증, claim unlink 각 crash point
- claim unlink 전 UUID/`st_dev`/`st_ino` 교체 공격 negative
- Linux 실제 filesystem의 `renameat2(RENAME_NOREPLACE)`, multiprocessing, parent fsync integration
- evaluator/auditor normalized-core 치환 0개/2개, stale mutual pin, source mutation negative

Windows에서 Linux host probe와 `renameat2` 검증을 PASS로 표현하지 않는다. Windows에서는 source
편집, archive build와 비공개 test까지만 수행한다. 로컬 canonical design/implementation/delivery
review와 delivery staging은 6절 WSL ext4에서만 수행한다. 서버에서는 transfer 뒤 receipt/review가
먼저이며, 그 다음에도 public preflight만 조건부로 열린다.

## 17. 승인 상태 전이와 종결

| immutable evidence | 다음에 허용되는 단 하나의 작업 |
| --- | --- |
| independent design review PASS | r4 implementation |
| implementation review PASS | delivery manifest build |
| delivery review PASS | 서버로 exact files 전송 |
| server receipt review PASS + fresh host gate PASS | public preflight |
| preflight Spark review PASS + fresh host gate PASS | fit |
| fit Spark review PASS + fresh host gate PASS | score |
| score Spark review PASS + fresh host gate PASS | calibrate-select |
| selection evaluation review PASS + fresh host gate PASS | confirmation |
| confirmation evaluation review PASS | 결과 해석과 모델 채택 판단 |

어떤 evidence도 deployment를 승인하지 않는다. 어느 phase든 failure 또는 review failure가 생기면
같은 run/phase를 다시 실행하지 않는다. fit이 28,800초에도 timeout이면 r4 recovery는 terminal
failure다. 행·tree·평가를 바꾸려면 새 run ID와 새 실험 설계가 필요하다.
