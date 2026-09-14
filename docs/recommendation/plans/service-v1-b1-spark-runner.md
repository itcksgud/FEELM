# Service v1 B1 Spark GBT 실행 계획

상태: **DRAFT — masked-data gate 충족, runner·scorer·평가기 구현과 독립 검토 전에는 실행 금지** · 2026-09-13.

## 목적과 판단 범위

감사된 B1 TMDB 결측 view를 사용해 Spark GBT120 하나를 처음부터 학습하고, 기존 B0 GBT120과
같은 자연 상태 관측 목표에서 비교한다. 학습기는 원본 4,997,069행마다 네 `view_id`를 실제로
만들고 각 행에 `view_weight=0.25`를 전달해야 한다. 두 물리 feature 축을 2배 행으로만 읽거나
동일한 축을 제거해 9,994,138행으로 줄이는 구현은 이 계약을 충족하지 않는다.

이 단계는 B1의 자원 실행 가능성과 자연 입력 안전성을 판정한다. KOBIS 특징, FM, ALS 결합,
서비스 전체 후보 점수화, 배치500, 발견10×10, 맞춤2+발견1 및 EC2 배포는 실행하지 않는다.

## 실행 전 고정 입력

아래 경로는 `C:\higher\projects\FEELM-standalone` 기준이다. 팀 계약 파일들은
`C:\higher\projects\S15P21E106`의 병합 완료 `origin/develop`
`96a4b27d0ce5c0d4e4fe0348b6c01de0b06f6f7e`에서 읽고 컨테이너에 read-only로 mount한다.
실행 lock에는 host의 실제 파일을 해시한 값을 기록한다.

| 역할 | 물리 입력 | 허용 단계 | 고정 조건 |
| --- | --- | --- | --- |
| 학습 계약 | `pipeline/configs/service-v1/training-recipe.v1.json` | preflight·fit | 15,764 bytes; SHA-256 `d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b` |
| 산출물 계약 | `pipeline/artifacts/service-v1.json` | preflight·fit·평가 | 15,504 bytes; SHA-256 `1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343` |
| 모델 계약 | `pipeline/docs/service-v1/MODELS.md` | preflight·fit | 25,079 bytes; SHA-256 `5ad4c852b89ca02da00c30f0fc184aa6018db4abf1808c057772a4908d376701` |
| 평가 계약 | `pipeline/docs/service-v1/EVALUATION.md` | 평가 | 12,466 bytes; SHA-256 `db7bd86f6b72b2edee7b6a65610bdb177c65937b518288417e3c6bdbb02bf3e3` |
| feature 계약 | `pipeline/configs/service-v1/feature-schema.v1.json` | preflight·fit·scoring | 39,243 bytes; SHA-256 `fda2be4f40b76e46b88dbb53523ef404bbf8a13c68bbf012acc58da9a63948ca` |
| 자연 학습 축 | `outputs/recommendation-evidence/foundation340/RH/train.parquet` | dry-run·preflight·fit | 4,997,069행·599 row group·832,717,601 bytes; SHA-256 `9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45` |
| masked 학습 축 | `outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1/tmdb-masked-rh230.parquet` | dry-run·preflight·fit | 4,997,069행·891,461,814 bytes; SHA-256 `27aee771597ba230654b2e99c1eea047fd25e3265161c51b6873474d47c65f01` |
| view 정의 | 같은 디렉터리의 `views-manifest.json` | dry-run·preflight·fit | 1,710 bytes; SHA-256 `df8dd8bbfea4a71e506958c5b7e1499b5010350a326cbfac2c9bfe23a607792a` |
| masked build 증거 | 같은 디렉터리의 `manifest.json` | dry-run·preflight·fit | 5,176 bytes; SHA-256 `82eb3635f1ae914a056c2813c4786dfa2de56abbb37aaf5ce852685f6eb6a557`; 정확히 세 파일을 게시한 immutable build, `modelFitPerformed=false` |
| masked 독립 감사 | `outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-masked-views-v1-result-review.json` | dry-run·preflight·fit | 10,174 bytes; SHA-256 `f7e322206d95c8ad418926a08394625f84a25b611f4e9595c97dd03cca6c7b1c`; `status=PASS`, `allRowsChecked=true` |
| 자연 평가 축 | `outputs/recommendation-evidence/foundation340/RH/score.parquet` | scoring | 93,230행·2,985,357 bytes; SHA-256 `9832425537943823f524ae6730aa1059b77d2c0464664f1dc29357bf95078f9b` |
| 학습 사용자 축 | `outputs/recommendation-evidence/text339/ratings.parquet` | **평가 전용 UID 비중복 검사** | 4,997,069행·32,950,407 bytes; SHA-256 `28b46687abec2e0edb3a892ec4f4dbd9d5cca701bf220b2812f9e7cf9b905a63`; 고유 uid 39,859명 |
| 평가 문맥 | `outputs/recommendation-evidence/text339/contexts.json` | 평가 전용 | 12,290,713 bytes; SHA-256 `951fc2464bd3aea25ea486c79c33084f6241f7846a3ed626e9d5fc3907a7aab7` |
| 영화 ID 축 | `outputs/recommendation-evidence/text339/catalog.parquet` | 평가 전용 | 85,517행·728,100 bytes; SHA-256 `0bde668e0e26f5f82b5c41d90d62c7569fd350bf2a2fd4b59bb4402f438c5947` |
| 평가 정답 | `outputs/recommendation-evidence/text339/labels.parquet` | **평가 전용** | 18,959행·75,490 bytes; SHA-256 `e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8` |
| 평가 정답 봉인 | `outputs/recommendation-evidence/text339/evaluation-seal.json` | **평가 전용** | 4,890 bytes; SHA-256 `0ab420fb3e50cf770d9dd7a24d64e5ce8896a4c17b38a7ba5df73398ad6fb9c7` |
| 평가 사용자 원 역할 | `outputs/recommendation-evidence/final344/roles.csv` | 평가 전용 | 270명·5,806 bytes; SHA-256 `466b7cede9cb2d67f2bd7fca4fcdada770943bdd9d5924c35ccfdbe6a4cf2cc9` |
| 층 메타데이터 | `outputs/recommendation-evidence/rec-ev-045/metadata.parquet` | 평가 전용 | 85,517행·6,891,831 bytes; SHA-256 `4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d` |
| B0 raw 예측 | `outputs/recommendation-evidence/final344/GBT120_s339/predictions.npy` | 평가 전용 | 93,230개·745,968 bytes; SHA-256 `6520b9094c89824fa2ed833da7618851536d9a7ce3f74d20d2233c71e34cea4f` |
| B0 fit 봉인 | `outputs/recommendation-evidence/final344/GBT120_s339-seal.json` | 평가 전용 | 6,911 bytes; SHA-256 `ba9be1a4769d9cd77a8d8b0a11b437ada10254a030656e98cfa7f7f07d7ffee7` |
| ALS 지원 영화 축 | `outputs/recommendation-evidence/combination340/ALS/item-factors/` | 평가 전용 | 팀 `service-v1.json`에 열거된 파일이 전부 일치하고 고유 영화45,074편이어야 함 |

masked build의 세 파일과 sibling 독립 감사는 위 bytes/SHA-256으로 immutable 봉인되었고,
감사는 `status=PASS`, `allRowsChecked=true`로 build manifest·views manifest·masked Parquet을
고정한다. 따라서 **masked-data gate는 충족**되었다. 이후 단계도 이 네 파일을 매번 다시 해시하며,
하나라도 달라지면 기존 PASS를 재사용하지 않는다.

프로세스 경계도 입력 계약이다. dry-run·preflight·fit 컨테이너에는 학습 계약과 두 학습 축,
masked bundle·감사, host outer wrapper와 Spark worker만 read-only mount한다. scoring 컨테이너에는 **독립 감사 PASS인 fit
bundle·그 sibling review**, 자연 평가 축, feature 계약, scorer만 read-only mount한다. 평가
컨테이너에는 **독립 감사 PASS인 score bundle·그 sibling review**와 평가 전용 입력만 mount한다.
fit과 scoring 컨테이너 모두 `labels.parquet`와
`evaluation-seal.json` 경로를 mount하지 않고 CLI에도 이를 받는 옵션이 없어야 한다. 접근 추적
합성 검사에서 fit/scoring 코드가 두 basename을 열려고 하면 실패시킨다. 평가 컨테이너만 두 파일을
정확한 파일 단위 read-only mount할 수 있다. fit `input-lock.json`, `score-input-lock.json`,
`evaluation-input-lock.json`은 서로 분리하고, 각 프로세스는 자기 lock에 없는 파일을 읽으면 실패한다.
preflight와 fit의 공통 학습 원본·계약·plan·outer wrapper·Spark worker·production dependency·test·image
ID만 경로 ASC canonical bytes로 해시한 값을 `trainingSourceSetSha256`으로 둔다. fit에서 새로 생기는
preflight manifest·review·그 sibling은 공통 학습 원본이 아니라 phase별 `controlReferences`로 따로
열거·해시한다. 각 phase의 `inputSetSha256`은 공통 source와 자기 control reference를 모두 포함하므로
preflight와 fit 사이에 달라도 된다. 두 단계가 동등성을 요구하는 값은 `trainingSourceSetSha256`이며,
fit이 실제로 읽는 control reference도 fit input lock 밖의 예외 입력으로 두지 않는다.
selection과 confirmation은 각자 자기 immutable bundle 안에 별도의 `evaluation-input-lock.json`을
만든다. 두 lock의 phase 전체 `inputSetSha256`은 confirmation의 selection reference 때문에 달라도
된다. 대신 공통 원본만 경로 ASC의 `relative_path NUL bytes NUL sha256 LF`로 해시한
`evaluationSourceSetSha256`을 별도 필드로 둔다. 여기에는 평가 계약, labels/seal, training ratings,
contexts, catalog, roles, metadata, B0 prediction/seal, ALS 지원 축, 감사된 score manifest/review/predictions와 그 fit
chain을 포함하고 selection 산출물은 제외한다. confirmation의 `evaluationSourceSetSha256`과 truth
census는 selection 값과 정확히 같아야 한다.
모든 lock은 시작 직전과 게시 직전에 실제 파일 bytes/SHA-256을 다시 계산한다. outer wrapper도
컨테이너 시작 전에 Docker mount source의 resolved path를 단계별 allowlist와 대조한다.
dry-run·preflight·fit·scoring에 두 평가 전용 파일 또는 그 상위 `text339` 디렉터리가 보이면
SparkSession 생성 전 중단한다.

## 입력 스키마와 네 논리 view

두 학습 Parquet은 다음 233개 열만 가져야 한다.

```text
row_id:int64, uid:int32, label:float64, x000:float32 ... x229:float32
```

각 물리 파일에서 `row_id=0..4,997,068`이 정확히 한 번씩, 엄격 오름차순으로 존재해야 한다.
같은 `row_id`의 `uid`와 `label`은 두 축에서 비트 단위로 같고, label은 0.5부터5.0까지 0.5
단위이며 모든 특징과 label은 finite여야 한다. masked build 독립 감사가 확인한
`x000..x199` 자연 축 parity와 `x200..x229` 재계산 계약도 입력 lock에 연결한다.

Spark에서는 다음 네 DataFrame을 각각 만들고 `unionByName`한다. `view_id`는 `int32`,
`view_weight`는 `float64` literal `0.25`다.

| view_id | 이름 | feature 물리 축 | 행 수 | weight |
| ---: | --- | --- | ---: | ---: |
| 0 | NATURAL | 자연 RH230 | 4,997,069 | 0.25 |
| 1 | TMDB_MASKED | masked RH230 | 4,997,069 | 0.25 |
| 2 | KOBIS_MASKED | 자연 RH230 | 4,997,069 | 0.25 |
| 3 | BOTH_MASKED | masked RH230 | 4,997,069 | 0.25 |

논리 결과는 정확히 19,988,276행, 고유 `row_id` 4,997,069개다. 모든 원행에 view0·1·2·3이
각각 한 번, weight 합1이어야 한다. view0과2의 230열, view1과3의 230열은 각각 비트 단위로
같아야 한다. 같은 값이라는 이유로 중복 제거, 캐시 축약, weight0.5의 두 행으로 변경하지 않는다.

`VectorAssembler(inputCols=[x000..x229], outputCol="features", handleInvalid="error")`만 사용한다.
`row_id`, `uid`, `view_id`, `view_weight`는 feature에 넣지 않는다. 조립한 frame은
`repartition(8,"row_id").sortWithinPartitions("row_id","view_id")` 후
`StorageLevel.DISK_ONLY`로 persist하고, 학습에는 `label,features,view_weight`만 전달한다.

## 실제 first-row-group 비게시 Spark dry-run

2026-09-13의 첫 실행 ID `b1-gbt120-s339-v1`은 모델 학습 전에 runner가 자연 입력,
masked 3파일, sibling review를 모두 `/input`에 놓아 worker의 exact-3 inventory gate가 의도대로
중단시켰다. 원본 failure는
`b1-gbt120-s339-v1-preflight-failure.json`(6,720 bytes, SHA-256
`b59a09fbe741472f05d774d829faf809de1c29957812a7eeece8ba1803b093d2`)으로 그대로 보존한다.
수정 실행은 모델 사양을 바꾸지 않고 실행 ID만 `b1-gbt120-s339-v1-r2`로 올린다. masked host
디렉터리는 정확한 3파일인지 host에서 확인한 뒤 `/masked`에 하나의 read-only directory bind로
mount하고, 자연 입력은 `/input`, sibling review는 `/review`에 분리한다. worker와 독립 auditor가
같은 exact-3 inventory를 각각 다시 검사한다. r2는 새 runner·worker·test hash를 input lock에
봉인하며 v1 경로의 failure나 산출물을 덮어쓰지 않는다.

합성 fixture 검사가 모두 통과한 다음, outer wrapper의 공개 `preflight` action 한 번 안에서
canonical 실제 자료로 `dry-run-first-row-group`을 먼저 실행하고 PASS·완전 cleanup을 확인한 뒤에만
full-data preflight worker를 이어 실행한다. 별도 공개 dry-run action이나 full-data preflight만 바로
부르는 우회 action은 두지 않는다. 이 첫 내부 단계는 모델을 fit·저장·scoring하지 않으며 증거
디렉터리도 게시하지 않는다.

1. full-file SHA-256과 masked build·감사 연결을 먼저 확인한다. 그 다음 PyArrow footer가 정의한
   자연·masked Parquet의 **물리 row group index 0**만 각각 scratch로 읽는다. 자연 RG0은 정확히
   8,231행, masked RG0은 정확히4,096행이어야 한다. 두 물리 row group의 크기가 같다고 가정하지
   않고, 임의 `limit`, sampling, Spark partition 첫 조각으로 대신하지 않는다.
2. 두 RG0의 실제 schema가 각각 정확히233열·고정 dtype인지 확인한다. 전체 natural RG0의 row_id
   domain은 정확히 `0..8,230` 8,231개, 전체 masked RG0는 정확히 `0..4,095` 4,096개이며 각 축에서
   유일·엄격 오름차순이어야 한다. natural-only domain도 정확히 `4,096..8,230` 4,135개인지 먼저
   확인한다. 그 다음 각 축을 `row_id<=4,095`로 필터해 canonical 공통 창을 만들고 **두 필터 결과
   사이**의 양방향 anti-join이 모두0이며 교집합이 정확히 `0..4,095` 4,096개인지 확인한다. 공통
   key의 `(row_id,uid,label)`은 행별 비트 일치하고 label과230개 feature는 finite여야 한다. 제외한
   natural-only 4,135행은 stdout에 명시한다.
3. canonical image의 Spark `local[4]`에서 두 공통4,096행 scratch Parquet에 같은 production view
   builder와 assembler를 호출한다. 결과가 정확히16,384행·고유 row_id4,096개이고, 원행마다
   view0·1·2·3이 정확히 한 번, weight가 각각0.25·합1인지 확인한다. view0=2, view1=3 feature bit
   parity와 view0의 `(row_id,uid,label)`이 자연 공통 창 원본과 정확히 같은지도 확인한다.
4. dry-run은 별도 무작위 temp 경로만 write mount한다. 정상·예외 모두 Spark를 stop하고 Arrow
   handle을 닫은 뒤 추출 Parquet, checkpoint, local-dir, event/log 파일을 삭제한다. temp root가
   비었고 preflight·fit·score·selection·confirmation 경로에 새 파일이 없음을 확인하지 못하면
   실패다.

outer `preflight`의 `command.json`과 `run.log`는 dry-run container 시작·종료·PASS·cleanup을
full-data materialization 시작보다 앞선 순서로 기록한다. dry-run 실패·timeout·OOM·cleanup 실패 시
같은 호출은 full-data SparkSession을 만들지 않고 preflight bundle도 게시하지 않는다.

이 검사는 실제 schema/view/weight/identity 경로의 조기 결함만 찾는다. stdout의 요약이나 임시
digest는 immutable 증거, full preflight PASS 또는 fit 허가로 재사용하지 않는다.
공통4,096행 제한은 이 비게시 검사에만 적용되며, 아래 full preflight와 fit의4,997,069개 원행·
19,988,276개 논리 행 계약을 축소하지 않는다.

## fit 이전 전체 행 identity 검사

persist를 실제 materialize한 뒤 한 번의 partition 순회로 다음을 확인한다.

1. partition이 정확히8개이며 논리 행 수는19,988,276이다.
2. 각 partition에서 `row_id`는 비감소하고, 서로 다른 원행 사이에서는 엄격히 증가하며, 한
   `row_id` 안에서는 `view_id=0,1,2,3` 순서다.
3. 네 행의 `uid`, `label`, weight가 같고 weight는 모두0.25다.
4. view0 feature=view2 feature, view1 feature=view3 feature다.
5. view0만 골라 `row_id`를 little-endian signed int64로 이어 SHA-256한 값과 기존 B0의 다음
   partition identity가 일치한다.

| partition | 고유 원행 | 논리 행 | view0 `row_id` SHA-256 |
| ---: | ---: | ---: | --- |
| 0 | 624,054 | 2,496,216 | `21fbf39b23490f46a935ff54dcdab20a1f38361351840780f278a27c4c8de4ea` |
| 1 | 624,438 | 2,497,752 | `b54c60df6c9ebf0b1268c60124f9908b2d86a7bbb9f2fceffce520a344123891` |
| 2 | 624,481 | 2,497,924 | `5c072bdc8ec9396af8e6dad574d95b13c20988765acf1177d46da6e2c82500d8` |
| 3 | 625,449 | 2,501,796 | `84692cce25046923f3a23751f3c1ca2902324f0e44650140dcdf607322cb8f7d` |
| 4 | 624,427 | 2,497,708 | `51e8100ac0aba07e8b3a0852f375924ae15a6baa2c81bb80f5953396efd2d2a1` |
| 5 | 624,773 | 2,499,092 | `14b8332eebabecb4cdb90975220967a2c258f7b2aae1ebd20bcbcc410ac6d4d0` |
| 6 | 624,390 | 2,497,560 | `442632597415d7f1e194fb6133a1675997a0e5d741bc9242dded088a0bb35923` |
| 7 | 625,057 | 2,500,228 | `2f51a1ffa91329f80d2802843df72e11ad387b2e304f82a653fcee3294336344` |

논리 identity도 각 행마다 아래 바이트를 이어 별도로 SHA-256하고 `partition-identity.json`에
기록한다. preflight와 fit은 독립 프로세스에서 같은 digest를 만들어야 한다.

```text
row_id signed-int64 LE || view_id signed-int32 LE || uid signed-int32 LE ||
label IEEE754-float64 LE || view_weight IEEE754-float64 LE
```

preflight는 모델을 fit하지 않고 위 identity와 모든 census를
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r2-preflight/`에
봉인한다. 같은 volume의 `.b1-gbt120-s339-v1-r2-preflight.tmp-<uuid>`에서 다음 파일을 만들고,
`manifest.json`을 마지막에 쓴 뒤 file/directory flush를 완료하여 최종 경로로 한 번만 atomic
rename한다. 최종 경로나 같은 이름의 감사 파일이 이미 있으면 덮어쓰지 않고 중단한다.

```text
input-lock.json
recovery-reference.json
command.json
partition-identity.json
resource.json
run.log
manifest.json
```

preflight `input-lock.json`은 평가 전용 파일을 포함하지 않는다. 학습 물리 입력·계약·masked 감사,
현재 host outer wrapper·Spark worker·production dependency·test 파일과 Docker image ID의
bytes/SHA-256을 각각 열거하고,
경로 ASC의 `relative_path NUL bytes NUL sha256 LF`를 SHA-256한 `trainingSourceSetSha256`을 기록한다.
preflight의 `controlReferences`는 보존된 v1 preflight failure 한 파일의 path·bytes·SHA-256만
포함한다. `recovery-reference.json`은 그 pin, 이전/현재 실행 ID, 이전/현재 runner·worker hash,
실패 분류, model fit과 full preflight가 시작되지 않았다는 사실을 기록한다. source와 이 control을
합친 canonical digest를 `inputSetSha256`으로 기록한다.
`manifest.json`은 자신을 제외한 위 여섯 sibling의 bytes/SHA-256, 모든 census와 identity,
`outerRunnerSha256`, `sparkWorkerSha256`, 두 구현 파일 pin을 같은 순서로 이어 계산한
`implementationSetSha256`, `trainingSourceSetSha256`, `inputSetSha256`, `modelFitPerformed=false`,
`status=B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW`, `fitAuthorized=false`를 기록한다. 어느 파일도
rename 뒤 수정하지 않는다.

독립 검토자는 preflight 디렉터리를 수정하지 않고 sibling
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r2-preflight-result-review.json`
을 별도 temp에서 원자 게시한다. review에는 최소한 아래 target을 **실제 bytes/SHA-256**으로
고정해야 한다.

```text
status = PASS
target.manifest = {path, bytes, sha256}
target.input_lock = {path, bytes, sha256, trainingSourceSetSha256, inputSetSha256}
target.recovery_reference = {path, bytes, sha256}
target.partition_identity = {path, bytes, sha256}
target.command = {path, bytes, sha256}
target.resource = {path, bytes, sha256}
target.run_log = {path, bytes, sha256}
target.outer_runner = {path, bytes, sha256}
target.spark_worker = {path, bytes, sha256}
```

review의 각 target은 manifest 내부 sibling hash와 서로 일치해야 한다. `PASS` 문자열만 있거나,
outer wrapper·Spark worker·training source set·phase input set·manifest 중 하나라도 정확한 target
hash가 없으면 fit 허가가 아니다.

production fit CLI는 `--preflight-manifest`, `--preflight-review`,
`--expected-outer-runner-sha256`, `--expected-spark-worker-sha256`,
`--expected-training-source-set-sha256`를 필수로 받으며 기본값·skip·force 옵션을
두지 않는다. SparkSession 생성과 학습 파일 open보다 먼저 (a) review status가 `PASS`, (b) review가
가리킨 manifest와 모든 sibling의 현재 bytes/SHA-256, (c) 현재 실행 중인 outer wrapper와 Spark
worker 각각의 bytes/SHA-256 및 구현 집합 hash,
(d) 현재 mount된 공통 학습 source의 개별 hash와 재계산한 `trainingSourceSetSha256`, (e) fit
`controlReferences`로 열거한 preflight manifest·review·sibling의 개별 hash와 phase
`inputSetSha256`, (f) masked 감사 target 및 image ID가 모두 manifest·review·필수 CLI 값과 같은지
검사한다. preflight와 fit의 phase `inputSetSha256` 자체가 같다고 요구하지 않는다. fit action은 그 뒤 같은 전체
identity를 다시 계산해 preflight와 비교한다. 하나라도 다르거나 파일이 추가·누락되면 Spark를
시작하지 않고 실패하며 새 preflight와 새 독립 검토 없이는 재실행할 수 없다.

## 고정 Spark GBT fit

Spark `4.1.3`, Java21, master `local[4]`, shuffle partition8, AQE off를 요구한다. estimator는
팀 recipe의 B1 GBT map과 완전히 같아야 한다.

```text
GBTRegressor(
  seed=339, maxDepth=5, maxBins=32, minInstancesPerNode=1,
  minInfoGain=0.0, maxMemoryInMB=128, cacheNodeIds=false,
  subsamplingRate=0.8, checkpointInterval=10, lossType="squared",
  maxIter=120, stepSize=0.05, featureSubsetStrategy="sqrt",
  impurity="variance", validationTol=0.01, minWeightFractionPerNode=0.0,
  featuresCol="features", labelCol="label", predictionCol="prediction",
  weightCol="view_weight", leafCol=""
)
```

실제 resolved map은 팀 JSON 객체와 키·값이 정확히 같아야 한다. 추가 기본값을 임의로
덮어쓰지 않고, seed339 한 번만
처음부터 fit한다. hyperparameter 탐색, seed344/345, checkpoint 재개, 부분 행 학습은 하지 않는다.
완료한 native model의 tree 수가120이 아니면 성공으로 봉인하지 않는다.

학습 진단으로 네 view 전체의 weighted raw RMSE를
`sqrt(sum(view_weight*(prediction-label)^2)/sum(view_weight))`로 계산한다. 이것은 관측 사용자
평가나 모델 선택 지표가 아니다. fit 전에 평가 label이나 B0/B1 결과를 읽어 설정을 바꾸지 않는다.

## 감사된 fit을 사용한 자연 상태 scoring

fit bundle과 sibling fit result-review가 각각 immutable 게시되고 독립 감사 `status=PASS`가 된 뒤에만
새 scoring 프로세스를 연다. production scoring CLI는 `--fit-manifest`, `--fit-review`,
`--expected-fit-manifest-sha256`, `--expected-fit-review-sha256`, `--expected-fit-model-inventory-sha256`,
`--expected-outer-runner-sha256`, `--expected-spark-worker-sha256`를 필수로 받고 skip·force·기본 경로를
두지 않는다. SparkSession 생성과 model/score file open 전에
review의 PASS, review target manifest/model inventory hash, 현재 fit manifest/review/model 파일의
bytes/SHA-256을 모두 다시 비교한다. 불일치하면 scoring bundle을 만들지 않는다.

그 다음 자연 `score.parquet`을
`row_id:int64,uid:int32,x000:float32..x229:float32`의 명시적 projection schema로 읽는다. 물리
Parquet에 존재하는 `label` 열은 schema·select·analyzed Spark plan 어디에도 나타나면 안 된다.
scoring 시작 전에 mount allowlist, fit manifest/result-review/model inventory, score file과 scorer의
hash를 `score-input-lock.json`으로 봉인한다. 자연 score 축의 `(row_id,uid)`를 별도로 읽어
`row_id=0..93,229`가 정확히 한 번씩 유일·엄격 오름차순인지 먼저 확인한다. B1 raw prediction은
finite인93,230개여야 하며, 저장 직전과 단일 파일 재개봉 뒤 출력 `(row_id,uid)`가 자연 score 축과
행별로 정확히 같아야 한다. duplicate+missing 상쇄나 UID swap은 실패다.
`row_id` 오름차순으로 `row_id:int64,uid:int32,prediction:float64`를 저장한다. 산출물은 Spark가
정렬·전체 census를 끝낸 뒤 같은-stage scratch의 임시 단일 파일로 모아 재검증하고 atomic rename한
**한 개의 물리 Parquet 파일** `score/predictions.parquet`이어야 한다. Spark part-file 디렉터리나
glob에 의존하는 inventory는 허용하지 않는다. score bundle이
게시·독립 감사되기 전에는 이 예측을 평가에 넘기지 않는다. B0는 위에서 고정한 raw prediction 배열의
배열 위치를 `row_id`로 정의하여 `B0[row_id]`로만 연결한다. 길이93,230·finite와 자연 score 축의 정확한
row_id domain을 모두 통과하지 않으면 위치 연결을 수행하지 않는다. B0의 기존90명 affine 결과는 쓰지 않는다.

## 감사된 score를 사용한 단계별 평가

원 역할90명은 다음 순서로 다시 나눈다.

```text
key(uid) = SHA256(UTF8("cal-select-v1:" + decimal_uid))
기존 role=calibration 90명을 (key bytes ASC, uid ASC)로 정렬
앞45명=CALIBRATION, 뒤45명=SELECTION
기존 role=comparison 180명=CONFIRMATION
```

`uid ASC`로 `decimal_uid,ROLE\n`을 이어 계산한 membership digest는
`cf92b686ab21a950611084682e46bfd319426d5dbc3c4f58836a4836b3f416c6`이어야 한다. 인원은
45/45/180이고 세 집합은 서로 겹치지 않아야 한다. 평가 전용 프로세스는 위에서 pin한
`ratings.parquet`의 `uid` 열만 projection해 전체4,997,069행·고유 학습 사용자39,859명을 재검산하고,
세 역할270명의 uid와 교집합이0인지 강제한다. ratings의 다른 열은 이 검사에 읽지 않는다.
`ratings.parquet`와 그 상위 `text339` 디렉터리는 dry-run·preflight·fit·scoring에 mount하지 않는다.

### 평가 정답 integrity와 격리

score bundle과 sibling score result-review가 immutable 게시되고 독립 감사 `status=PASS`가 된 뒤에만
별도 `evaluation-input-check` 프로세스가 selection bundle의 temp 디렉터리 안에서 평가 전용 mount를
연다. 이 프로세스는 `evaluation-input-lock.json`과 `truth-integrity.json`을 그 staging bundle의
파일로 만들고, score review가 pin한 fit manifest/review까지 역으로 확인해 fit→score chain이
끊기지 않았는지도 검사한다. 먼저
`evaluation-seal.json` 자체가 4,890 bytes와 SHA-256
`0ab420fb3e50cf770d9dd7a24d64e5ce8896a4c17b38a7ba5df73398ad6fb9c7`인지 확인하고, seal 안의
`files["labels.parquet"]`가 정확히 75,490 bytes와 SHA-256
`e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8`을 pin하는지 확인한다.
`labels_previously_opened=true`도 기록한다. 이 자료에 대해 fresh blind holdout이라고 주장하지 않는다.

정답 join은 다음 조건을 모두 만족해야 한다.

1. `labels.parquet` schema는 `uid:int64,movie_id:int64,rating:float64`, 전체 행 수18,959이고
   `(uid,movie_id)`가 전부 유일해야 한다. 모든 rating은 finite, `0.5<=rating<=5.0`,
   `rating*2`가 정확한 정수여야 한다.
2. contexts에서 `cap=10`인270개 문맥을 정확히 하나씩 고르고, 각 문맥의 **모든** `ei`에 대해
   고정 catalog 물리 축의 `catalog.movie_id[ei]`를 복원한다. `ei`가 `[0,85,516]` 밖이거나 catalog의
   movie_id가 유일하지 않으면 실패한다.
3. 그렇게 만든 모든 목표를 key `(uid,catalog.movie_id[ei])`로만 label에 join한다. row_id, title,
   TMDB ID, 배열 위치끼리의 암묵 join을 허용하지 않는다. 목표는 정확히18,646행이고 key가 모두
   유일해야 하며, join 결과도 정확히18,646행·누락0·중복0·각 key당 label1개여야 한다. 즉 cap10
   목표 **18,646개 전부 observed**여야 한다.
4. label 전체18,959행 중 cap10 목표가 아닌313행은 개수와 제외 사유를 기록하되 목표를 보충하거나
   분모를 바꾸는 데 쓰지 않는다. 위 수치나 hash 하나라도 다르면 평가를 시작하지 않는다.

검증 결과와 각 입력의 bytes/SHA-256은 `evaluation-input-lock.json`에 봉인한다. 이 검증은 이미
봉인된 model·raw prediction·scoring 설정을 변경할 수 없는 평가 전용 프로세스에서만 수행한다.
평가용 원본 mount는 fit/scoring 프로세스와 공유하지 않으며, fit/scoring 결과 manifest에 두 평가
전용 파일을 입력으로 기록해서 사후 접근을 정당화할 수도 없다.

평가는 두 명령으로 분리한다.

1. `calibrate-select`: 검증된 정답 중 CALIBRATION과 SELECTION 사용자 행만 읽는다. 주 cap10 공통 목표에서
   B0와 B1 각각 사용자 동일 가중 affine `a+b*raw`, `b>=0`를 맞춘다. 사용자 u의 각 행 weight는
   `1/n_u`; raw 분산이 `1e-12` 이하이거나 유효 행이 없으면 `CALIBRATION_UNAVAILABLE`다.
   계수를 고정한 다음 SELECTION45 결과와 gate를 별도 immutable selection bundle로 게시하고 독립
   감사받는다.
2. `confirm`: immutable selection bundle의 필수 gate가 `PASS`이고 그 sibling review도 감사
   `status=PASS`일 때만 CONFIRMATION180의 검증된 정답 행으로 metric을 계산한다. 보정 계수·특징·
   모델·분모를 바꾸지 않고 같은 판정을 반복한다. selection gate가 실패·판단 불가이거나 review가
   PASS가 아니면 confirmation bundle을 만들지 않는다.

production confirmation CLI는 `--selection-manifest`, `--selection-review`,
`--expected-selection-manifest-sha256`, `--expected-selection-review-sha256`를 필수로 받고 skip·force
옵션을 두지 않는다. 평가 입력을 열기 전에 selection review가 manifest·evaluation input lock·
score reference·affine·metrics·bootstrap·census·gates·evaluator의 실제 hash를 모두 pin하는지,
현재 파일이 그 target과 같은지, required selection gate가 `PASS`인지 확인한다. confirmation은
selection bundle과 review를 파일 단위 read-only mount하고 어떤 파일도 수정·교체·추가하지 않는다.
그 뒤 평가 입력의 hash와18,646건 truth integrity를 다시 검사하여 confirmation의
`evaluation-input-lock.json`과 `truth-integrity.json`을 만들며, 두 파일의 canonical input-set digest와
census를 selection bundle과 비교한다. phase 전체 input-set digest는 서로 달라도 되지만 공통 원본의
`evaluationSourceSetSha256`과 truth census가 다르면 confirmation을 중단한다.

입력 integrity 단계가 모든18,646개 rating의 형식과 join 가능성을 확인하므로 confirmation 값 자체를
처음 여는 blind 절차는 아니다. 단계 분리의 의미는 confirmation 성능을 selection, 보정 또는 모델
설정에 되먹임하지 않는 데 있다.

두 단계는 자연 상태 cap10 관측 목표, 동일 사용자·동일 목표행에서 B1−B0를 비교한다. contexts의
`ei`를 고정 catalog의 MovieLens ID로 복원하고 원 H 영화를 제외한다. 점수 정렬은 calibrated raw
DESC, MovieLens movie ID ASC다. 순위에는 clipping 전 연속값을 사용하고 MSE/MAE에만
`[0.5,5]` clipping을 적용한다. 예측과 실제 별점을0.5 단위로 반올림하지 않는다.

각 단계에서 사용자 macro MSE/MAE, micro MSE, movie-macro MSE, NDCG@2/4/6/10, 라운드
(1~2),(3~4),(5~6)…의 실제 평균과 `rating<=2.0` 비율을 계산한다. 모든 표에 사용자·행·고유
영화 수와 제외 사유를 기록한다. B1 필수 MSE 층은 `ALL`, `ALS_TARGET_SUPPORTED`,
`ALS_TARGET_UNSUPPORTED`이며 ALS factor 존재만으로 두 지원 층을 나눈다. 각 층은 단계별
30사용자·30고유 목표영화·200행 이상이어야 하고, NDCG@2/low는 두 모델이2개를 채우고 IDCG>0인
공통 사용자30명 이상이어야 한다. 부족하면 `INSUFFICIENT`이며 통과가 아니다.

불확실성과 판정은 팀 `EVALUATION.md`를 그대로 구현한다.

- 사용자 block paired bootstrap 10,000회, 각 단계마다 user ID ASC에서 새 PCG64 seed339,
  2.5/97.5 percentile linear interpolation.
- 별점 오차의 영화 block 민감도 10,000회, movie ID ASC, 새 PCG64 seed340.
- B1/B2/B3 사용자 macro MSE 우위 family에서 아직 없는 B2·B3 p는1로 유지하고, B1은 같은
  PCG64 seed341 sign-flip 10,000회와 Holm step-down family alpha0.05를 적용한다. B1의 사용자별
  `delta=MSE_B1-MSE_B0` 부호를 같은 draw의 독립 ±1로 바꾸고,
  `p=(1+count(permuted_mean<=observed_mean))/10001`로 계산한다.
- 안전 gate는 NDCG@2 차이95% CI 하한 `>=-0.01`, Top2 low 차이 CI 상한 `<=+0.02`, 세 필수
  층 각각의 상대 MSE 변화 CI 상한 `<=+0.05`다. 하나라도 실패·비유한·표본 부족이면 PASS가 아니다.

인위적 `eval_mask:339:ml:` 자료는 현재 물리 입력에 없으며 위 활성화 평가에 사용하지 않는다.
이를 추가로 만들더라도 자연 상태 gate와 분리한 robustness 진단으로만 기록한다.

## 자원과 실행 격리

컨테이너 image는 `feelm-rec046-spark:local`, image ID는
`sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8`로 고정한다.
호스트 입력과 outer wrapper·Spark worker는 read-only mount, 출력과 Spark local scratch만 write mount하며
`--network none --cpus 4 --memory 12g --memory-swap 12g`를 사용한다. `spark-submit`은
`--master local[4] --driver-memory 8g`, shuffle partition8, AQE/UI off로 실행한다. dry-run, preflight,
fit, scoring은 서로 다른 고유 container name과 scratch를 쓰고 동시에 실행하지 않는다.

각 Spark 단계의 outer wrapper가 그 단계 시작부터5,400초를 재며 제한을 넘기면 정확한 해당
container만 stop 후 kill하고 실행 중이 아님을 확인한다. cgroup v2 `memory.peak`, 없으면 v1
`memory.max_usage_in_bytes`, Docker inspect의 OOMKilled/exit code를 모두 기록한다. peak를 읽지
못하면 자원 판정은 `UNKNOWN`이고 해당 단계에서 후속 단계로 진행할 수 없다. peak가12,884,901,888 bytes를 넘거나
OOM/timeout이면 해당 단계는 `RESOURCE_STOP`이다. preflight, fit, score는 각자의 `resource.json`,
container name, command, 시작·종료 시간, peak, exit/OOM/timeout을 기록한다. 후속 단계의 자원 기록을
이전 단계 파일에 덧붙이지 않는다. 결과를 보고 memory·partition·행 수·seed를 바꿔 자동 재시도하지
않는다.

기존 B0 GBT120 seed339는 4,997,069행에서도 peak12,884,905,984 bytes로 한도를4,096 bytes
넘어 `resource_status=EXCEPTION`이었다. B1은19,988,276 논리 행이므로 동일12GiB 성공은 현재
입증되지 않았으며, 이 위험을 숨기기 위해 행을 축약하지 않는다.

## immutable 출력과 실패 처리

preflight 이후의 fit, scoring, selection, confirmation은 네 개의 독립 산출물이다. 각 단계는 같은
volume의 `.<final-name>.tmp-<uuid>`에서 파일을 만들고 `manifest.json`을 마지막에 기록·flush한 뒤
비어 있는 최종 경로로 한 번만 atomic rename한다. 각 result-review도 대상 디렉터리를 수정하지 않고
별도 temp 파일에서 sibling 경로로 atomic rename한다. 이미 존재하는 bundle이나 review를 덮어쓰지
않으며, 다음 단계 실패가 감사 완료된 이전 단계를 변경하지 않는다.

### 1. fit bundle과 review

fit 출력 경로는
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r2-fit/`이고 파일
inventory는 다음과 같다.

```text
input-lock.json
preflight-reference.json
command.json
partition-identity.json
resolved-estimator.json
model/native/**
model-file-inventory.json
threshold-fixtures.npz
fit-metrics.json
resource.json
run.log
manifest.json
```

threshold fixture는 각 non-leaf split의 threshold 바로 아래·같음·바로 위 값을 포함하며 Spark
native prediction과 독립 portable tree reader가 최대 절대차 `1e-6` 이하여야 한다.
`model-file-inventory.json`은 native model 파일을 상대경로 ASC로 열거하고 각 bytes/SHA-256 및
canonical inventory digest를 기록한다. `preflight-reference.json`은 fit CLI가 통과시킨 preflight
manifest/review와 그 sibling control references, outer wrapper, Spark worker,
`implementationSetSha256`, 공통 `trainingSourceSetSha256`, fit phase `inputSetSha256`의 실제
bytes/SHA-256을 pin한다. fit manifest는 자신을
제외한 모든 파일, Spark/Java/Python, resolved map, tree 수120, fit 시간·전체 census·자원 판정을
고정하고 `status=B1_MODEL_FIT_COMPLETE_AUDIT_PENDING`, `scoringAuthorized=false`,
`readyForService=false`로 끝난다. score 입력이나 prediction은 이 bundle에 넣지 않는다.

sibling review 경로는
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r2-fit-result-review.json`
이다. review의 `status=PASS`는 `target.manifest`, `target.input_lock`,
`target.preflight_reference`, `target.command`, `target.partition_identity`, `target.resolved_estimator`,
`target.model_file_inventory`, `target.threshold_fixtures`, `target.fit_metrics`, `target.resource`,
`target.run_log`, `target.outer_runner`, `target.spark_worker`의 path·bytes·SHA-256이 현재 파일 및
fit manifest와 모두 같을 때만
가능하다. native model의 모든 파일 hash와 inventory digest도 다시 계산한다. review PASS 뒤의
논리 상태만 `B1_MODEL_FIT_AUDITED_SCORING_PENDING`이며 fit manifest 자체를 고쳐 쓰지 않는다.

### 2. score bundle과 review

fit review가 PASS인 뒤 scoring 출력은
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r2-score/`에 다음
inventory로 게시한다.

```text
fit-reference.json
score-input-lock.json
command.json
score/analyzed-plan.txt
score/predictions.parquet
resource.json
run.log
manifest.json
```

`fit-reference.json`은 fit manifest와 PASS review 각각의 path·bytes·SHA-256, model inventory
파일과 digest, scorer가 실제로 연 model 파일 전부의 bytes/SHA-256을 pin한다. `score-input-lock.json`과
score manifest는 현재 outer wrapper와 Spark worker를 별도 path·bytes·SHA-256 및
`implementationSetSha256`으로 pin한다. analyzed plan에는
허용한232개 열과 assembled features만 있고 평가 `label`은 없어야 한다. score manifest는 자신을
제외한 전 파일과93,230행 schema/order/finite census를 고정하고
`status=B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING`, `evaluationAuthorized=false`,
`readyForService=false`로 끝난다.

sibling review
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-gbt120-s339-v1-r2-score-result-review.json`
은 `status=PASS`와 함께 `target.manifest`, `target.fit_reference`, `target.score_input_lock`,
`target.command`, `target.analyzed_plan`, `target.predictions`, `target.resource`, `target.run_log`,
`target.outer_runner`, `target.spark_worker`의
path·bytes·SHA-256을 고정하고 fit reference가 현재 PASS fit review까지 정확히 잇는지 재검사한다.
review PASS 뒤의 논리 상태만 `B1_NATURAL_SCORE_AUDITED_SELECTION_PENDING`이며 score bundle을
수정하지 않는다.

### 3. selection bundle과 review

PASS score review를 입력으로 `calibrate-select`가 만드는 출력은
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-vs-b0-selection-v1/`이다.

```text
evaluation-input-lock.json
truth-integrity.json
score-reference.json
command.json
role-membership.csv
affine.json
metrics.json
bootstrap-summary.json
strata-census.json
gates.json
run.log
manifest.json
```

`score-reference.json`은 score manifest/PASS review와 그 안의 fit chain을 실제 bytes/SHA-256으로
pin한다. manifest는 자신을 제외한 전 파일, 평가기 SHA-256, CALIBRATION45/SELECTION45의 계수·
분모·metrics·불확실성·gate를 고정하고 `status=B1_SELECTION_COMPLETE_AUDIT_PENDING`,
`requiredSelectionGate=PASS|FAIL|INSUFFICIENT`, `confirmationAuthorized=false`,
`readyForService=false`를 기록한다.

sibling review
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-vs-b0-selection-v1-result-review.json`
은 `status=PASS`와 함께 `target.manifest`, `target.evaluation_input_lock`, `target.truth_integrity`,
`target.score_reference`, `target.command`, `target.role_membership`, `target.affine`, `target.metrics`,
`target.bootstrap_summary`, `target.strata_census`, `target.gates`, `target.run_log`,
`target.evaluator`의 path·bytes·SHA-256을 명시한다. 감사 `status=PASS`는 계산과 봉인이 계약대로라는
뜻이며 selection gate PASS와 별개다. confirmation은 **감사 status와 required selection gate가
둘 다 PASS**일 때만 허용된다.

### 4. confirmation bundle과 review

confirmation 출력 경로는
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-vs-b0-confirmation-v1/`이다.

```text
selection-reference.json
score-reference.json
evaluation-input-lock.json
truth-integrity.json
command.json
metrics.json
bootstrap-summary.json
strata-census.json
gates.json
run.log
manifest.json
```

`selection-reference.json`은 PASS selection review와 selection manifest 각각의 path·bytes·SHA-256,
그리고 그 manifest가 pin한 evaluation input lock, score reference, affine, gates, evaluator의 hash를
모두 기록한다. confirmation은 selection의 affine·분모·gate 정의를 그대로 읽으며 복사본을 수정해
대체하지 않는다. confirmation의 `score-reference.json`도 selection이 pin한 것과 같은 score
manifest·PASS review·prediction hash를 가리켜야 하며 다른 B1 prediction으로 바꿀 수 없다.
confirmation manifest는 자신을 제외한 전 파일과 CONFIRMATION180 결과를 고정하고
`status=B1_CONFIRMATION_COMPLETE_AUDIT_PENDING`,
`requiredConfirmationGate=PASS|FAIL|INSUFFICIENT`, `readyForService=false`로 끝난다.

sibling review
`outputs/recommendation-evidence/service-v1-pretraining-20260913/b1-vs-b0-confirmation-v1-result-review.json`
은 `status=PASS`와 함께 `target.manifest`, `target.selection_reference`, `target.score_reference`,
`target.evaluation_input_lock`, `target.truth_integrity`, `target.command`, `target.metrics`,
`target.bootstrap_summary`, `target.strata_census`, `target.gates`, `target.run_log`,
`target.evaluator`의 path·bytes·SHA-256을 고정한다. selection과 confirmation의 필수 gate가 모두
PASS이고 두 bundle의 독립 review도 PASS일 때만 최종 논리 상태를 `B1_EVALUATED_NONINFERIOR`로
보고할 수 있다. 어느 manifest나 review도 상태 전이를 위해 수정하지 않는다.

입력 drift, schema/identity/view/weight 오류, 비유한 값, estimator drift, tree 수 부족, prediction
행 오류, portable parity 실패, 자원 초과, 종료 실패 중 하나라도 생기면 해당 단계의 성공 bundle을
게시하지 않는다. Spark/Arrow/file handle을 닫고 해당 temp만 지운 뒤 단계에 맞는 sibling
`b1-gbt120-s339-v1-r2-preflight-failure.json`, `b1-gbt120-s339-v1-r2-fit-failure.json`,
`b1-gbt120-s339-v1-r2-score-failure.json`, `b1-vs-b0-selection-v1-failure.json` 또는
`b1-vs-b0-confirmation-v1-failure.json`에 단계·오류·자원·정리 여부만 원자 기록한다. selection
계산이 정상 완료됐지만 required gate가 FAIL/INSUFFICIENT인 것은 실행 실패가 아니므로 selection
bundle과 review는 보존하고 confirmation bundle을 만들지 않는다. 정리 실패는 다음 실행을 막는다.

## 구현과 독립 검토 순서

1. runner·outer wrapper·평가기를 작성하고 합성 fixture에서 일반 Python과 `python -O`를 모두
   검사한다. production 경로에는 `assert`를 사용하지 않는다.
2. mask manifest 변조, 물리 축 교환, view 누락/중복, weight 변경, row/label/feature 불일치,
   estimator drift, 입력 mutation, 평가 전용 파일 접근, score duplicate/missing·UID swap,
   timeout/OOM, writer/save/publish 실패를 각각 주입해 실패를 확인한다.
3. outer wrapper의 공개 `preflight` action을 실행한다. 이 한 호출은 canonical 실제 자연 RG0
   8,231행과 masked RG0 4,096행에서 공통 `row_id=0..4,095` 4,096개를 골라 비게시 Spark dry-run을
   먼저 수행한다. 논리16,384행의 schema/view/weight/identity와 완전 cleanup이 PASS인 뒤에만 같은
   호출이 full-data preflight를 실행해 전체 논리 identity와 census를 원자 봉인한다. 모델 fit은 하지 않는다.
4. 구현자와 다른 검토자가 정확한 outer wrapper·Spark worker·implementation set·training source
   set·phase input set·preflight sibling SHA를 검토하고 target hash가 완전한 sibling result-review를
   `PASS`로 원자 게시한다.
5. 필수 CLI gate에서 현재 outer wrapper·Spark worker·입력과 PASS review의 hash가 모두 같을 때만
   seed339 B1 fit 한 번을 실행해 fit-only bundle을 원자 게시한다.
6. 별도 검토자가 fit manifest, model inventory, portable parity, hash, resource를 감사하고 sibling
   fit review를 게시한다. fit review가 PASS가 아니면 scoring을 시작하지 않는다.
7. scoring CLI가 현재 fit manifest/PASS review/model inventory와 두 실행 파일 hash를 먼저 검증한 뒤 자연 축만
   score하여 score-only bundle을 원자 게시한다.
8. 별도 검토자가 score manifest, fit reference, label 없는 projection, prediction census/hash를
   감사하고 sibling score review를 게시한다. score review가 PASS가 아니면 평가 입력을 열지 않는다.
9. PASS score chain을 고정하고 평가 전용 입력 integrity를 검사한 뒤 calibration-selection만 실행해
   selection-only bundle을 게시한다.
10. 별도 검토자가 selection bundle을 감사한다. sibling review의 감사 status와 required selection
   gate가 모두 PASS가 아니면 confirmation을 시작하지 않는다.
11. confirmation CLI가 현재 selection manifest/PASS review와 모든 gate·affine·분모 hash를 먼저
   검증한 뒤 CONFIRMATION180만 계산해 별도 confirmation bundle을 게시한다. selection은 수정하지 않는다.
12. 별도 검토자가 confirmation bundle을 감사하고 sibling review를 게시한다. 두 단계 gate와 두
    review가 모두 PASS일 때만 `B1_EVALUATED_NONINFERIOR`를 보고한다.

## 명시적 비주장과 현재 blocker

- B1−B0는 결측 처리만의 순수 효과가 아니다. B1은 4view, weight,20% mask와 subsampling 경로가
  모두 달라진 새 fit이다.
- 자연 MovieLens 관측 목표의 비열등성은 2026년 한국 사용자 만족도, 전체 서비스 후보 품질,
  신규 영화 품질 또는 발견 추천 품질을 증명하지 않는다.
- B1은230열이며 KOBIS233/245, 최종 B3, FM245 또는 ALS 결합 준비를 뜻하지 않는다.
- 학습 성공·낮은 RMSE·gate PASS 어느 것도 서비스 활성 포인터 변경이나 EC2 배포 승인이 아니다.
- 현재 full fit 전 blocker는 runner·scorer·평가기 구현 및 독립 검토 미완료와, 내부 실제
  first-row-group Spark dry-run을 포함한 full-data preflight 및 그 독립 review 미실행이다.
  masked-data gate는 이미 충족되었지만 이 blocker 중 하나라도 남으면 full fit을 시작하지 않는다.
  12GiB 내 fit 가능성은 fit 전 승인조건이 아니라, 모든 선행 gate를 통과한 뒤 허용되는 고정 1회
  fit에서 `PASS` 또는 `RESOURCE_STOP`으로 판정할 미해결 실행 불확실성이다.
