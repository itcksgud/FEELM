# S15P21E106-623 FM 노트북 인계

## Git 상태

- 작업 트리: `C:\Users\SSAFY\cksgud\higher\projects\FEELM-S15P21E106-623-fm-zero-n`
- 브랜치: `codex/S15P21E106-623-fm-zero-n`
- 기준: `origin/main`의 `d227ab49f6f1cf00594bb36f6b79e5cd53b8f400`(GitHub PR #1 병합 뒤 개정)
- 원격 저장소: `https://github.com/itcksgud/FEELM.git`
- 커밋·푸시·PR은 만들지 않았다. 파일은 의도적으로 Git 미추적 상태다.
- 팀 저장소 `S15P21E106`은 latest `origin/develop` 계약 확인에만 썼고 수정하지 않았다.

요청의 `C:\higher`는 이 노트북에 없어 실제 루트 `C:\Users\SSAFY\cksgud\higher`를 사용했다.
기존 checkout과 미커밋 파일은 reset/stash/삭제하지 않았다.

## 환경

- Windows 11 Enterprise 10.0.26200
- Intel Core Ultra 9 185H, 코어 16개 / 논리 처리기 22개
- 물리 메모리 68,186,861,568바이트(약 63.5 GiB)
- NVIDIA GeForce RTX 4070 노트북 GPU 8,188 MiB, 드라이버 591.44
- Python 3.12.12, 호스트 Java 17.0.10, Git 2.55.0
- Docker 클라이언트·서버 29.6.1, Docker Desktop 4.81
- Spark 4.1.3, 컨테이너 Java 21, CPU `local[6]`
- 기록 당시 C: 여유 공간 1,692,558,204,928바이트

GPU는 사용하지 않았다. 서버, HDFS, GitLab CI/CD에서도 학습하지 않았다.

## 실행 환경과 원본

Docker 이미지:

```text
feelm-fm-zero-n-spark:local
sha256:5a469f86c21f950cbce0a47e8fe2068f631619d276adfbb82dac245e43eba6a3
```

Dockerfile은 `performance/fm-zero-n.Dockerfile`이다. 이미지를 다시 빌드하면 기반 태그 변화로 ID가
달라질 수 있으므로 기존 결과에 이어 쓰지 말고 새 실행 ID와 실행 환경 검토를 사용한다.

MovieLens 경로:
`C:\Users\SSAFY\cksgud\higher\projects\MM\data\raw\ml-32m`

```text
ratings.csv  91159850e41ee59c86231165a688709647e2726cab2e7ba9faf04001bd5261ee
movies.csv   b37ca1abc7798de741138ed252b62f69f7e37c84b8a8fab1b82d409b4c6c5cc2
```

## Git 밖 실행 결과

루트: `C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623`

| 디렉터리 | 용도 | 크기 |
| --- | --- | ---: |
| `fixture-input-v1` | 결정적 점검용 고정 입력 | 0.1381 GiB |
| `fixture-prepared-v4` | 최종 소스 점검용 준비물 | 0.0040 GiB |
| `fixture-fits-v4` | 프로필 8개 + 선택 프로필 초기화값 3개 + 검증기 | 0.0216 GiB |
| `ten-percent-input-regenerated-v1` | 622 계약 재생성 입력 | 3.6245 GiB |
| `ten-percent-prepared-v2` | 최종 10% 희소 벡터 | 0.0443 GiB |
| `ten-percent-fits-v1` | 모델·예측·검증·재검증 | 0.2683 GiB |

수정 전 `ten-percent-prepared-v1`과 fixture v1~v3는 재사용하지 않는다. run을 지우지 않았으며
기존 결과를 덮어쓰지 않았다.

## 재검증

먼저 이미지와 원본 해시를 대조하고 단위 테스트를 실행한다.

```powershell
docker image inspect feelm-fm-zero-n-spark:local --format "{{.Id}}"
Get-FileHash C:\Users\SSAFY\cksgud\higher\projects\MM\data\raw\ml-32m\ratings.csv -Algorithm SHA256
Get-FileHash C:\Users\SSAFY\cksgud\higher\projects\MM\data\raw\ml-32m\movies.csv -Algorithm SHA256

docker run --rm --network none `
  -v "${PWD}:/workspace:ro" -w /workspace `
  feelm-fm-zero-n-spark:local `
  python3 -m unittest discover -s scripts -p "test_fm_zero_n_*.py" -v
```

복사한 결과는 새 출력 이름으로 검증기를 다시 실행한다. `verification-report.json`을 덮어쓰지
않도록 `verification-report-recheck.json` 같은 새 이름을 쓴다.

```powershell
docker run --rm --network none `
  -v "${PWD}:/workspace:ro" `
  -v "C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623:/runs" `
  -w /workspace feelm-fm-zero-n-spark:local `
  python3 scripts/fm_zero_n_verify.py `
  --input-root /runs/ten-percent-input-regenerated-v1 `
  --prepared-root /runs/ten-percent-prepared-v2 `
  --fits-root /runs/ten-percent-fits-v1 `
  --output /runs/ten-percent-fits-v1/verification-report-recheck.json
```

최종 검증기 통과 결과의 산출물 목록 digest는
`70cb8b4a4e1929d9247c005426732bfd114a79e2ee1d8f35563d8cb7d6f6336d`다.

## 다음 단계

전체 데이터 학습은 현재 권고하지 않는다. 희소 선형 모델이 요인 FM보다 좋고 N=1~9 짝지은 성능이
악화됐기 때문이다. 다음 작업은 결과 독립 리뷰와 S15P21E106-625 실제 사용자 평가다. 사용자가
명시적으로 승인하기 전에는 FINAL_TEST를 채점하거나 commit/push/PR/Jira 변경을 하지 않는다.

이어갈 때 사용할 프롬프트:

```text
S15P21E106-623 FM 0~N 실험을 이어서 검토해줘.
먼저 experiments/fm-zero-n/README.md, RESULTS.md, LAPTOP-HANDOFF.md와 branch/status를 확인해.
MovieLens 해시, Docker 이미지 ID, ten-percent-prepared-v2 소스 묶음과
ten-percent-fits-v1 검증 산출물 digest를 대조하고 새 이름으로 검증기를 실행해.
최종 시험은 열지 말고, 희소 선형 모델이 요인 FM보다 나았던 원인과 N=1~9 짝지은 악화를
독립적으로 리뷰해. 전체 데이터 학습보다 S15P21E106-625 실제 사용자 평가 설계를 우선해.
KOBIS/TMDB는 검증된 시점 자료와 연결표가 생기기 전까지 결합하지 마.
```
