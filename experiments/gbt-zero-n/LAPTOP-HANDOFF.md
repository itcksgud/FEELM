# S15P21E106-622 노트북 인계

## 무엇을 clone하는가

GBT 로컬 실험 실행에는 개인 저장소만 필요하다.

```powershell
git clone https://github.com/itcksgud/FEELM.git
cd FEELM
```

현재 작업은 `codex/S15P21E106-622-gbt-zero-n` 브랜치에서 준비했다. 이 브랜치가 commit·push된
뒤 노트북에서 checkout한다. E106 팀 저장소는 `pipeline/contracts/zero-n-learning-evaluation.md`와
621 readiness를 다시 확인하거나 이후 Jira·서비스 통합 작업을 할 때만 별도로 clone한다. GBT 학습
스크립트는 E106 checkout, 서버, HDFS를 사용하지 않는다.

## 로컬 입력과 runtime

MovieLens 32M 압축 해제본에 다음 파일이 있어야 한다.

- `ratings.csv`: SHA-256 `91159850e41ee59c86231165a688709647e2726cab2e7ba9faf04001bd5261ee`
- `movies.csv`: SHA-256 `b37ca1abc7798de741138ed252b62f69f7e37c84b8a8fab1b82d409b4c6c5cc2`

Python 3.12와 `pyarrow`가 필요하다. Spark 학습은 Docker image
`feelm-rec046-spark:local`, image ID
`sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8`를 사용한다.
가능하면 데스크톱에서 `docker save`한 tar를 Git 밖으로 옮겨 `docker load`한다. Dockerfile로
재빌드하면 base tag 변화로 image ID가 달라질 수 있으므로 기존 결과를 덮어쓰지 말고 새 run ID와
새 runtime 검토를 사용한다.

```powershell
docker image inspect feelm-rec046-spark:local --format "{{.Id}}"
py -3.12 -m unittest discover -s scripts -p 'test_gbt_zero_n_*.py'
```

## 기존 데스크톱 결과

Git에서 제외된 로컬 결과는 다음과 같다.

- 입력: `ten-percent-input-v6`, 약 3.62 GiB
- 준비물: `ten-percent-prepared-v6`, 약 0.02 GiB
- 모델·검증: `ten-percent-fits-v6`, 약 0.04 GiB

재현만 필요하면 MovieLens 원본에서 README 명령으로 새 ID를 생성한다. 결과 자체가 필요하면 위 세
디렉터리를 Git 밖에서 복사하고, 복사 후 `gbt_zero_n_verify.py`를 실행한다. `FINAL_TEST`는 열지 않는다.

KOBIS 2,176행은 확정 crosswalk가 0행이라 이 profile에서 제외했다. TMDB/KOBIS 인기 신호와 title
우선순위는 최종 사용자 평가 직전의 별도 rerank/profile 비교에서 적용한다.

## 이어서 작업할 때 사용할 프롬프트

```text
S15P21E106-622 GBT 0~N 실험을 이어서 진행해줘.
먼저 experiments/gbt-zero-n/README.md, RESULTS.md, LAPTOP-HANDOFF.md와 현재 branch/status를 확인해.
MovieLens ratings.csv·movies.csv와 Docker image ID를 config의 hash와 대조하고 단위 테스트를 실행해.
기존 ten-percent-input-v6 / prepared-v6 / fits-v6가 있으면 수정하거나 덮어쓰지 말고 verifier로 검사해.
없으면 fixture부터 새 run ID로 만들고 통과한 뒤에만 10% 실행을 해.
FINAL_TEST는 열지 말고 TRAIN·VALIDATION만으로 판단해.
서버·HDFS·E106 CI/CD는 이 로컬 실험에 사용하지 마.
MovieLens 지표는 약한 근거로 해석하고 KOBIS는 검증된 crosswalk가 생기기 전까지 결합하지 마.
작업 결과에는 입력/코드/runtime hash, 역할·행 수, N별 지표, peak memory, 남은 한계를 기록해.
```
