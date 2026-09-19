# S15P21E106-622 노트북 인계

## 저장소

GBT 로컬 실험에는 개인 저장소만 필요하다.

```powershell
git clone https://github.com/itcksgud/FEELM.git
cd FEELM
git fetch origin
git checkout codex/S15P21E106-622-gbt-completion
```

PR이 main에 병합된 뒤에는 main을 사용한다. E106 팀 저장소는 계약을 다시 확인하거나 이후
서비스 통합 Jira를 수행할 때만 별도로 clone한다. 이 학습은 E106 서버, HDFS, CI/CD를 사용하지
않는다.

## 원본과 runtime

MovieLens 32M 압축 해제본에 다음 파일이 있어야 한다.

- `ratings.csv`: SHA-256 `91159850e41ee59c86231165a688709647e2726cab2e7ba9faf04001bd5261ee`
- `movies.csv`: SHA-256 `b37ca1abc7798de741138ed252b62f69f7e37c84b8a8fab1b82d409b4c6c5cc2`

Python 3.12와 `pyarrow`가 필요하다. Spark는 Docker image `feelm-rec046-spark:local`, image ID
`sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8`를 사용한다. 데스크톱에서
`docker save`한 tar를 Git 밖으로 옮겨 `docker load`하는 편이 안전하다. 다시 빌드해 image ID가
달라지면 기존 결과를 덮어쓰지 말고 새 run ID로 실행한다.

```powershell
docker image inspect feelm-rec046-spark:local --format "{{.Id}}"
py -3.12 -m unittest discover -s scripts -p 'test_gbt_zero_n_*.py'
```

## 데스크톱 결과

Git에서 제외된 현재 결과 디렉터리는 다음과 같다.

- `ten-percent-input-v7`: 입력 episode와 후보
- `ten-percent-prepared-v7`: 학습·validation parquet와 고정 manifest
- `ten-percent-fits-v7`: 세 회귀 profile, validation 보고서, 전수 검증
- `ten-percent-sensitivity-v1`: seed 1622·2622 민감도
- `ten-percent-rank-v2`: 후보마다 동일한 3개 anchor를 쓴 pairwise ranking 학습과 비교
- `ten-percent-legacy-audit-v1.json`: `GBT120_s339` 감사 결과
- `ten-percent-completion-v4`: 최종 증거 완성·승격 보류 판정

결과를 그대로 이어서 볼 필요가 있으면 위 디렉터리를 Git 밖에서 복사한다. 복사 후 README의
전수 verifier와 completion verifier를 다시 실행한다. 재현만 필요하면 MovieLens 원본에서 새 run
ID로 생성한다. `FINAL_TEST`는 열지 않는다.

KOBIS 2,176행은 확정 crosswalk가 없어 이번 profile에서 제외했다. TMDB/KOBIS 인기도와 title
우선순위는 최종 사용자 평가 직전 별도 rerank/profile에서 검증한다.

## 이어서 사용할 프롬프트

```text
S15P21E106-622 GBT 0~N 실험 결과를 인계받아 확인해줘.
먼저 experiments/gbt-zero-n/README.md, RESULTS.md, LAPTOP-HANDOFF.md와 branch/status를 확인해.
MovieLens ratings.csv·movies.csv와 Docker image ID를 문서의 hash와 대조하고 단위 테스트를 실행해.
기존 ten-percent-*-v7/v1 결과가 있으면 수정하거나 덮어쓰지 말고 verifier로 검사해.
없으면 README 명령으로 fixture와 새 run ID부터 만들고 통과한 뒤에만 10% 실행을 해.
FINAL_TEST는 열지 말고 TRAIN·VALIDATION만 사용해.
서버·HDFS·E106 CI/CD는 이 로컬 실험에 사용하지 마.
현재 completion 판정은 DO_NOT_PROMOTE다. 이를 성공으로 바꾸거나 숨기지 말고,
seed 1622의 N=1/2 악화, pairwise rank 악화, 실제 저이력 표본 부족을 그대로 확인해.
새 모델 후보가 필요하면 기존 output과 분리하고 저이력 표본, seed 안정성, 추천 순위를 모두 검증해.
MovieLens는 약한 근거로 해석하고 KOBIS는 검증된 crosswalk 전까지 학습에 결합하지 마.
```
