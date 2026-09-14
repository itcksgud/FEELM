# Service v1 B1 사전 학습 계획

상태: **DRAFT — 독립 실행 전 검토 필요** · 2026-09-13.

## 판단할 질문

KOBIS 연결이 준비되지 않은 상태에서, 기존 RH230 GBT에 TMDB 대중 채널의 인위적 결측을
학습시키는 B1이 결측 입력에 더 안전한가를 확인한다. B1은 230열 비교 모델이며 최종 목표인
KOBIS245 모델이나 서비스 기본 모델로 간주하지 않는다.

현재 KOBIS 확대 자료에는 검증 완료된 KOBIS↔TMDB 연결이 0개다. 따라서 B2·B3·FM245,
KOBIS q99와 보정 계수는 이 계획에서 만들지 않는다.

## 고정 입력

| 입력 | 고정 조건 |
| --- | --- |
| `text339/ratings.parquet` | 4,997,069행; 목표 사용자·영화·별점·시각 |
| `text339/episodes.parquet` | 사용자·episode별 cap과 목표 범위 |
| `text339/catalog.parquet` | MovieLens 정수 영화 ID의 정렬 축 |
| `text339/prepared-seal.json` | SHA-256 `d27709a42bd0d5511a36f3740365637325d59b521c7f62eae04850523782fdd8` |
| `foundation340/RH/train.parquet` | 4,997,069행·RH230; SHA-256 `9d8d33a252991c032704d4072003b4fb9f400136f3c411a2698ae5fee592fa45` |
| `rec-ev-045/metadata.parquet` | 기존 85,517편 메타데이터; SHA-256 `4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d` |
| 팀 `training-recipe.v1.json` | `origin/develop` `96a4b27d0ce5c0d4e4fe0348b6c01de0b06f6f7e`; SHA-256 `d403a27fab09453f98b9988fcfb3867b83e41ae217f9f5f3be5976321eef8d7b` |
| 팀 `service-v1.json` artifact manifest | SHA-256 `1a9ba0cd0101f6d065227c37ee642a3fc75c2c69b34c0da8fa6dacff351c2343` |
| `scripts/foundation340_features.py` | `Histories` 정의; SHA-256 `83e2586d9acc8bfb8aec8dc47b8133f4bc0da8678ba0d82bdd295b5c7bf13cf9` |

입력 하나라도 바뀌면 기존 산출물을 이어 쓰지 않는다. 새 버전으로 처음부터 생성한다.

## 1단계: 행 계보 복원

`row_id`는 ratings와 RH230 train의 같은 0기반 행이다. 각 행에서 다음을 복원한다.

- `row_id`, `uid`, `target_ml_movie_id`, `target_timestamp`, `label`, `original_cap`
- 목표 시각보다 엄격히 이전인 최대 30편의 `history_ml_ids`, `history_stars`,
  `history_timestamps`
- `history_length`와 `canonical_target_key=ml:<MovieLens movie_id>`

동일 시각의 평점은 과거 입력에 넣지 않는다. 목표 영화 자체가 이력에 나타나면 실패한다.
MovieLens ID를 서비스 ID로 바꾸거나 서비스 매핑이 없다는 이유로 행을 제거하지 않는다.

전 행에서 RH230 원본의 `row_id`, `uid`, `label`과 ratings가 일치해야 한다. 4,997,069행,
39,859명, `row_id=0..4,997,068`, 이력쌍 41,750,890개, 이력 0개 행 1,051,625개를
다시 확인한다. cap별 행 수는 `0=998,592`, `1=986,527`, `5=1,002,302`,
`10=1,009,507`, `30=1,000,141`로 고정한다. `(uid, movie_id)` 중복이 있거나 한 행이라도
불일치하면 B1 입력 생성과 학습을 중단한다.

출력은 immutable 디렉터리에 원자적으로 게시한다. Parquet list 열은 실제 이력 길이만
저장하고, 입력·코드·runtime·행/사용자/이력쌍 수·각 파일 bytes/SHA-256을 manifest에 기록한다.

## 2단계: B1 네 view

정규 영화 키는 `ml:<movie_id의 10진수>`다. 학습 mask는 다음 정수 비교로 고정한다.

```text
d = int(SHA256("train_mask:339:ml:" + movie_id), 16)
masked = 5*d < 2^256
```

평가 자료는 별도 prefix `eval_mask:339:ml:`를 사용한다. 정확히 영화 20%를 맞추려고 재표본화하지
않는다. 결과를 본 뒤 seed나 mask를 바꾸지 않는다.

| view | B1에서의 실제 입력 | GBT weight |
| --- | --- | ---: |
| `NATURAL` | 기존 RH230 | 0.25 |
| `TMDB_MASKED` | mask 대상 영화의 TMDB 평균·투표 수·popularity를 결측으로 재계산 | 0.25 |
| `KOBIS_MASKED` | B1에는 KOBIS 열이 없으므로 NATURAL과 동일 | 0.25 |
| `BOTH_MASKED` | B1에는 KOBIS 열이 없으므로 TMDB_MASKED와 동일 | 0.25 |

mask는 후보 영화와 그 행의 모든 과거 이력 영화에 각각 적용한다. 목표 별점·원 이력·cap·시각,
장르·국가·인물·시대·러닝타임은 바꾸지 않는다. 자연 상태에서 확정한 TMDB prior
`mean=6.173088067675869`, `mass=48`을 mask 이후에도 유지하며, 남은 영화로 prior를 다시
추정하지 않는다.

TMDB 결측은 RH230의 `x200..x229`만 바꿀 수 있다. `x000..x199`와 목표/이력 정체성은 자연
view와 전 행 일치해야 한다. 채널 value는 0, present는 0으로 만들고 후보·이력의 fraction,
평균, 분산, 근거량, 공분산, delta와 교차항을 같은 masked metadata에서 다시 계산한다.

물리 저장은 4배 중복을 피하기 위해 두 feature 축만 둔다.

- `natural-rh230.parquet`: 봉인된 기존 RH230을 해시로 참조한다.
- `tmdb-masked-rh230.parquet`: 새로 계산한 4,997,069행.
- `views-manifest.json`: NATURAL/KOBIS_MASKED는 natural을, TMDB_MASKED/BOTH_MASKED는
  masked를 각각 한 번 읽도록 정의한다.

학습 DataFrame은 논리적으로 19,988,276행이어야 하고 각 `row_id`에 네 view가 정확히 하나씩
있어야 한다. GBT에만 `view_weight=0.25`를 전달한다. B1에서 FM을 학습하지 않는다.

## 3단계: 학습과 판정

Spark 4.1.3·Java 21, CPU4, container12GiB, driver8GiB, 8 partition, AQE off,
`row_id/view_id` 정렬, 한 모델씩 순차 실행을 고정한다. GBT는 seed339, 120 trees, depth5,
maxBins32, stepSize0.05, subsampling0.8, feature subset sqrt와 squared loss를 쓴다.

90분 또는 메모리 한도를 넘으면 `RESOURCE_STOP`으로 끝내고 행 축소·자동 증설·다른 seed 재시도를
하지 않는다. 완료 시 native model, resolved estimator, feature/view manifest, Spark/runtime/resource
로그, fit 시간과 모든 파일 해시를 원자적으로 봉인한다.

그 뒤 기존 90명 풀을 `SHA256("cal-select-v1:"+user_id)` 순으로 45명 calibration과 45명
selection으로 나누고 기존 180명을 confirmation으로 유지한다. B0와 B1을 같은 역할·목표·후보에서
비교하고 MSE, NDCG@2/4/6/8/10, Top2 저평점 노출과 지원률을 본다. confirmation을 보고 계수나
모델을 다시 맞추지 않는다. 이 자료는 이미 열린 개발 자료이며 새 blind test라고 부르지 않는다.

## 실행 게이트

다음이 모두 PASS일 때만 B1 fit을 시작한다.

1. 별도 검토자가 이 문서와 정확한 실행 코드 해시를 승인한다.
2. 계보 전 행·입력 pin·엄격 과거·목표 제외 검사가 PASS다.
3. 자연 view의 RH230 전 행 parity가 허용오차 `2e-6` 이하다.
4. 각 원행 네 view, 논리 행 수 19,988,276, weight 합 1이 일치한다.
5. KOBIS 값을 읽거나 제안 링크를 확정 링크로 승격한 행이 0이다.
6. 합성 fixture와 소규모 실제 row group dry-run이 일반/`python -O`에서 통과한다.

실행 CLI에는 입력 해시 검사를 우회하는 옵션을 두지 않는다. 합성 fixture는 테스트 코드가
명시적으로 주입하는 별도 `ContractExpectation`만 사용하며, 운영 CLI는 위 고정 해시·행 수·사용자
수·이력 census·cap census를 항상 적용한다. 입력 Parquet과 출력 writer는 성공·실패에서 모두
명시적으로 닫고, 쓰기·종료·게시 실패 시 임시 디렉터리가 남지 않아야 한다.

이 계획은 로컬 준비다. Jira·GitLab·Notion·EC2를 수정하지 않으며, 학습 성공만으로 실제 서비스
품질이나 2026년 한국 사용자 적합성을 주장하지 않는다.
