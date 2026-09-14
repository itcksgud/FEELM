# 텍스트 의미 정제 비교: 로컬 산출물 확인

이 문서는 이번 로컬 실행의 입력·코드·결과를 다시 확인하는 안내다. 원본 데이터와 기존
text339 산출물이 필요하므로 문서와 코드만 복사해 다른 서버에서 즉시 실행할 수 있다는 뜻은 아니다.

## 고정한 조건

- 기존 T3의 모델과 예측을 재사용하고 CLEAN FM 한 번만 추가 학습한다.
- 학습 4,997,069행·39,859명, 평가 270명·1,350 contexts·93,230개 채점 행을 유지한다.
- 구조 특징 230개와 기존 T2 특징 108개는 행마다 원본과 정확히 비교한다.
  CLEAN 모델이 사용하는 것은 구조 230개+정제 overview 108개+정제 Wiki 108개다.
  저장 파일의 T2 108개는 정합성 검사용이며 CLEAN의 모델 입력에는 들어가지 않는다.
- 원문 SHA가 같은 임베딩은 재사용하고, 바뀐 고유 본문 218개만 같은 E5 모델로 인코딩했다.
  기존 PCA 평균·축·스케일도 재사용했다.
- 동일한 FM 설정과 Spark 이미지, CPU 4개·JVM 8GiB·컨테이너 12GiB 조건을 사용한다.
  동일 seed라도 Spark 학습 경로와 최종 계수의 비트 단위 재현은 보장하지 않는다.

자세한 모델 값·채점 조건·판정은 [PLAN.md](PLAN.md)와 [config.json](config.json)에 있다.

## 필요한 파일

작업 위치는 `C:/higher/projects/FEELM-standalone`이다.

| 위치 | 내용 |
| --- | --- |
| `outputs/recommendation-evidence/text339` | 봉인된 기존 원문·임베딩·PCA·학습 행·입력 이력·평가 별점·T3 모델 및 예측 |
| `outputs/recommendation-evidence/text339-runtime` | 고정 E5 모델·토크나이저와 GPU 인코딩용 Python 환경 |
| `outputs/recommendation-evidence/text339-clean` | 이번 정제 원문·변경 원장·새 임베딩·특징·모델·평가 결과 |
| `docs/recommendation/experiments/text339-clean` | 계획·의미 검토·독립 검산·보고서 |
| `scripts/textclean_*.py`, `scripts/report_textclean.py` | 이번 실행 코드. 기존 text339 및 그 의존 코드도 필요 |

각 단계 봉인에 실제 파일 크기와 SHA-256이 있다. 기존 데이터의 하위 의존성은 기존 text339
봉인·설정에서 확인한다. 원본 평점·모델·Parquet·사용자별 결과는 로컬 자료다.

## 이미 완성한 파일 확인

PowerShell에서 저장소 루트로 이동한 뒤 다음 명령으로 실행 코드와 단계별 실제 파일을 확인한다.
아래 검증은 결과를 덮어쓰거나 새 모델을 학습하지 않는다.

```powershell
py -3.12 -B -X utf8 -c "import sys; sys.path.insert(0, 'scripts'); from textclean_run import guard, verify; guard(); [verify(n) for n in ['clean-seal.json', 'embedding-seal.json', 'prepared-seal.json', 'fit-seal.json', 'catalog-seal.json']]; print('PASS')"
```

단계가 아직 끝나지 않았다면 해당 단계의 봉인이 없어 실패하는 것이 정상이다. 해시 검사는
파일 일치 확인이며, 독립적인 수학·통계·의미 검토를 대신하지 않는다. 검토 결과는 이 폴더의
`source-review.json`, `embedding-review.json`, `prepared-review.json`, `prediction-review.json`,
`result-review.json`에서 구분한다.

## 실행 순서와 재실행 제한

이번 실행은 **원문 후보 검토 → 보존 방향 교정 → 정제 원문 검산 → 인코딩·검산 →
특징 준비·검산 → 학습 → 전체 카탈로그 점수·예측 검산 → 평가 → 보고·독립 검토** 순서다.
모델 학습 전 평가 정답으로 정제 구간이나 설정을 선택하지 않았다. 비교 대상 개발 라벨은
이전 연구에서 이미 사용했으므로 새로운 최종 test로 간주하지 않는다.

단계별 실행 진입점은 다음과 같다. **이미 산출물이 있는 현재 폴더에서 재실행하는 안내가 아니다.**
코드는 기존 파일이 있으면 중단하며, 독립 검토 기록의 실제 해시가 일치해야 다음 단계로 넘어간다.

```text
GPU Python 환경: scripts/textclean_run.py encode
Python 3.12: scripts/textclean_run.py prepare
Python 3.12: scripts/textclean_run.py fit
Python 3.12: scripts/textclean_run.py catalog
Python 3.12: scripts/textclean_evaluate.py
Python 3.12: scripts/report_textclean.py
```

새 반복 실험은 별도 출력 위치와 실험 식별자를 정하고, 변경된 경로·코드·설정에 대한 검토를
먼저 받아야 한다. 기존 봉인을 새 파일에 맞춰 임의로 갱신하거나 기존 모델을 덮어쓰지 않는다.
사용자 중단 시점의 미완성 학습 파일은 `prepare-interrupted-1`에 보관하고 전량 다시 준비했다.
의미 검토에서 수정된 두 이전 원문 적용본도 `source-attempt-1`, `source-attempt-2`에 보존했다.

학습 종료 여부는 `run-progress.json`, 실패 상세는 `logs/fit.log`, 실제 사용 자원은
`models/CLEAN/metrics.json`에서 확인한다. T3의 과거 12GiB+4,096바이트 최고 메모리 예외와
이번 CLEAN 결과를 구분한다.
