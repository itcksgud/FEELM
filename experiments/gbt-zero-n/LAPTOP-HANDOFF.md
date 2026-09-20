# S15P21E106-622 GBT 노트북 인계

## 저장소와 범위

GBT 로컬 실험은 개인 GitHub 저장소 `itcksgud/FEELM`에서 수행한다. E106 팀 GitLab의 서버,
HDFS, CI/CD를 사용하지 않는다. 최신 PR이 병합되기 전에는 PR 브랜치를, 병합된 뒤에는 `main`을
사용한다.

```powershell
git clone https://github.com/itcksgud/FEELM.git
cd FEELM
git fetch origin
git checkout main
```

현재 판정은 다음 셋을 분리한다.

- controlled prefix 0~N 입력과 평점 예측 정보 곡선: `PASS`
- 인기도에서 순수 GBT로 바꾸는 K 임계값: `REJECTED_THROUGH_K50`
- 인기도 80% + GBT 20% same-pool rerank: `OFFLINE_VALIDATED_CANDIDATE`
- 실제 total-history 저이력 사용자: `NOT_EVALUATED_CONTROLLED_PREFIX`
- 운영 승격: `DO_NOT_PROMOTE`

## 원본과 runtime

MovieLens 32M 압축 해제본에 다음 파일이 있어야 한다.

- `ratings.csv`: SHA-256 `91159850e41ee59c86231165a688709647e2726cab2e7ba9faf04001bd5261ee`
- `movies.csv`: SHA-256 `b37ca1abc7798de741138ed252b62f69f7e37c84b8a8fab1b82d409b4c6c5cc2`

Python 3.12와 `pyarrow`가 필요하다. Spark는 Docker image `feelm-rec046-spark:local`, image ID
`sha256:de611d62db982bb833b0737a7ddd72a7cd49ab1a7dc9128669cdabae39f46df8`를 사용한다. image ID가
달라지면 기존 결과를 덮어쓰지 말고 새 run ID로 실행한다.

```powershell
docker image inspect feelm-rec046-spark:local --format "{{.Id}}"
py -3.12 -m unittest discover -s scripts -p 'test_gbt_zero_n_*.py'
```

## v16 로컬 결과

Git에서 제외된 최신 결과 디렉터리는 다음과 같다.

- `prefix-input-v16-contract`: exact-K 입력, 후보, manifest
- `prefix-prepared-v16-contract`: 학습·validation parquet, source bundle
- `prefix-fits-v16-seed622`: seed 622 GBT와 validation 예측
- `prefix-fits-v16-seed1622`: seed 1622 GBT와 validation 예측
- `prefix-fits-v16-seed2622`: seed 2622 GBT와 validation 예측
- `prefix-policy-v16-contract`: hard-switch SELECTION과 80:20 blend CONFIRM 보고서
- `prefix-verification-v16-seed*.json`: seed별 전체 계약 검증 보고서

큰 결과 파일은 Git에 올리지 않는다. 복사할 때는
`policy-v16-confirmation.json`의 크기와 SHA-256을 대조한다. runtime source bundle digest는
`69865139a221d35cf90b551924fb041b6c025b34aaa974d7f000f4ff7ff09418`, 세 seed candidate
identity digest는 `9b991a8c31c55bdd23caa25b7aa42cd089570012653b74ed34f269d340113ace`다.

실패·기각된 v13/v14 run은 감사 흔적으로 로컬에 보존했지만 결과 근거로 사용하지 않는다.

## 해석 시 주의

MovieLens는 사용자당 최소 20개 평가가 있으므로 실제 total-history K<20 사용자를 제공하지 않는다.
v16은 성숙 사용자의 과거를 잘라 같은 사용자·미래·후보에서 K 정보량만 비교한다. 따라서
K=0~2 셀의 통과를 실제 신규 사용자 성능으로 표현하면 안 된다.

target은 candidate pool에 강제로 들어가며 `UNKNOWN_SAMPLED`는 미평가다. 결과는 same-pool
rerank 근거일 뿐 후보 생성이나 온라인 추천 품질 근거가 아니다. FINAL_TEST는 열지 않는다.

## 이어서 사용할 프롬프트

```text
S15P21E106-622 GBT v16 결과를 인계받아 확인해줘.
먼저 experiments/gbt-zero-n/README.md, RESULTS.md, LAPTOP-HANDOFF.md,
promotion-policy.json, policy-v16-confirmation.json과 branch/status를 확인해.
MovieLens ratings.csv·movies.csv와 Docker image ID를 문서의 hash와 대조하고 단위 테스트를 실행해.
로컬 prefix-*-v16 결과가 있으면 수정하거나 덮어쓰지 말고 seed별 verifier로 검사해.
없으면 README 명령으로 새 run ID를 만들어 재현해.
FINAL_TEST는 열지 말고 TRAIN·VALIDATION만 사용해. 서버·HDFS·E106 CI/CD는 사용하지 마.
순수 GBT hard switch는 K=50까지 기각됐고, 80:20 혼합만 offline candidate라는 결론을 유지해.
controlled prefix를 실제 저이력 사용자로 표현하지 말고 actual low-history는 미평가로 남겨.
후속 실험은 실제 서비스 replay/onboarding 저이력 코호트와 end-to-end 후보 생성 평가로 분리해.
```
