# MR !82 검증 기록과 수정 요청의 근거

상태: **DRAFT** · 개인 검토 기록 · 2026-09-07

## 검토 대상

- [GitLab MR !82](https://lab.ssafy.com/s15-bigdata-dist-sub1/S15P21E106/-/merge_requests/82): `feat/S15P21E106-267-als-input-validation` → `develop`, 열림 상태.
- MR 커밋: `aa5857fe8ec34ef563077f2f879be24db794b386`.
- [Jira 267](https://ssafy.atlassian.net/browse/S15P21E106-267): 입력 검증·실행 설정·fixture 구성, 진행 중. 데이터 분할·학습·평가는 후속 범위이며 서비스 미매핑 평점 보존이 명시돼 있음.
- 118번 전처리: `origin/feat/S15P21E106-118-movielens-tmdb-preprocessing`, 커밋 `90b3c8212a9459c4f181baed8ec454ffbfc9c0ae`.
- 비교한 최신 develop: `2c8e96a9d065ccaf91783344773e5325b38109b1`. `git merge-tree --write-tree`로 충돌 없이 병합 가능한 것을 확인함. 실제 브랜치를 병합하지 않음.

원본 팀 저장소의 코드를 수정하지 않았으며, MR·Jira에 글을 게시하거나 승인·병합하지 않았습니다. 소스의 고정 커밋을 임시 폴더로 추출하고 Docker에 읽기 전용으로 연결했습니다.

## 앞선 설명에서 바로잡은 내용

첫 검토에서는 MR 본문과 Jira 인증이 되지 않아 Git으로 가져온 코드만 확인했습니다. 이후 사용자가 열어 둔 브라우저에서 본문과 Jira에 접근할 수 있었습니다.

기존 MR에는 이미 다음 내용이 있습니다.

- 서비스 미매핑 평점을 보존하는 이유: 학습 신호 유지. 서비스 노출 후보는 후속 단계에서 MATCHED 매핑으로 제한.
- 118번 CSV fixture를 전처리한 Parquet을 이 MR 진입점으로 읽은 결과: 2 / 3 / 1.
- 267번 선병합과 118번 계약 유지·담당자 간 조율 순서.
- 전체 23개 테스트 통과, 정적·포맷 검사, CLI 도움말 확인.
- MovieLens 32M 검증은 실제 export 준비 후 119번에서 수행한다는 한계.

따라서 ‘연계 검증이 없다’, ‘미매핑 보존 이유가 없다’, ‘학습까지 구현하지 않았다’를 반려 사유로 사용하면 안 됩니다. 이 기록에서도 기존 MR의 작성자가 AI를 사용했다고 단정하지 않습니다.

## 독립 실행 결과

환경: 로컬에 있던 공식 `apache/spark:4.1.3` 이미지, Python 3.10.12, Java 21. 임시 컨테이너에 pytest 8.4.2와 Ruff 0.12.12를 설치했습니다. 저장소 의존성 파일이나 시스템 Python은 변경하지 않았습니다.

프로젝트 전체 실행 결과:

```text
23 passed in 16.22s
All checks passed!
19 files already formatted
```

`python3 -m jobs.batch.als_baseline --help`도 정상 종료했습니다.

독립 스크립트는 118번의 실제 `build_pipeline`과 `write_pipeline`을 사용해 저장소의 CSV fixture를 Parquet으로 만들었습니다. 이를 별도의 프로세스에서 MR의 CLI에 입력하고, 종료 코드와 출력 문자열·출력 경로 미생성을 검증했습니다.

```text
ALS input validation completed: ratings=2, service_movies=3, matched_service_movies=1
CONFIRMED actual CLI exited 0 and did not create the supplied output URI
Independent review checks completed.
```

기존 MR의 118번 연계 검증 결과를 같은 건수로 재현했습니다. 이것은 소규모 fixture 검증이며 실제 운영 데이터 실행을 의미하지 않습니다.

추가 입력 검증:

| 검증 입력 | 실제 결과 |
| --- | --- |
| 스키마를 유지한 빈 ratings | `ratings is empty` |
| 사용자 ID 0 | `ratings contains 60 invalid rows` |
| rating을 double로 변경 | 타입 불일치로 실패 |
| 스키마를 유지한 빈 서비스 매핑 | `service-movie-id-map is empty` |
| 모든 매핑이 비MATCHED | `service-movie-id-map contains no MATCHED movies` |
| 중복 서비스 ID | 중복 키 오류 |
| 정상 ratings에 추가 컬럼 | 허용, 필수 컬럼만 반환 |
| epoch와 불일치하는 UTC 값 | 허용: 현 검증 범위의 한계 |
| MATCHED 영화 ID를 평점에 전혀 없는 양의 ID로 변경 | 매핑 검증 통과: 평점과의 교집합은 검사하지 않음 |

마지막 두 항목은 확인한 Jira 완료 조건만으로 구현 결함이라고 단정하지 않았습니다. ‘검증된 입력’이 보장하는 범위를 설명하기 위한 관찰입니다.

## 실제 수정 요청이 가능한 두 건

### 1. CLI 우선순위와 구현의 불일치

관찰한 실행 결과:

```text
invalid env seed + valid CLI: REJECTED invalid literal for int() with base 10: 'invalid'
invalid env ratio + valid CLI: REJECTED could not convert string to float: 'invalid'
```

조건은 `RECOMMENDATION_ALS_SEED=invalid`와 `--seed 11`, 또는 `RECOMMENDATION_TRAIN_RATIO=invalid`와 `--train-ratio 0.8`입니다. `from_sources()`가 `parse_args()` 전에 환경변수에 int/float 변환을 실행하므로 발생합니다.

MR과 README가 약속한 CLI 우선순위를 근거로 수정 요청할 수 있습니다. 수정 뒤 같은 사례에서 CLI 값이 사용되는 테스트를 추가하면 해결 여부를 판단할 수 있습니다. 정상적인 숫자 환경변수의 덮어쓰기는 기존 코드에서도 동작합니다.

### 2. 유한하지 않은 ALS regParam 통과

관찰한 실행 결과:

```text
NaN regParam: ACCEPTED reg_param=nan
infinite regParam: ACCEPTED reg_param=inf
```

`_parse_als_config()`는 0 미만인지만 검사하며 유한성을 검사하지 않습니다. 이번 MR의 역할이 학습 설정을 검증해 후속 작업에 전달하는 것이므로, NaN/양의 무한대가 유효한 설정으로 반환되는 것을 수정 요청할 수 있습니다. 음의 값과 다른 숫자 범위 검사는 일부 이미 존재합니다. 실제 학습 실패나 운영 장애까지 관찰한 것은 아닙니다.

## 설명을 보완할 사항과 반려하면 안 되는 사항

| 의견 | 근거 확인 결과 | 적절한 처리 |
| --- | --- | --- |
| ‘실제 전처리와 연결된 증거가 없다’ | 본문에 있고 독립 재현 성공 | 이 이유로 반려하지 않음 |
| ‘모델을 학습하지 않았으니 미완성이다’ | Jira가 검증 단계로 분리 | 이 이유로 반려하지 않음 |
| ‘미매핑 평점 보존은 임의 결정이다’ | Jira와 MR에 명시 | 필요하면 기존 요구사항 변경 논의로 다룸 |
| ‘32M 성능 검증이 없으므로 이번 작업을 반려한다’ | 전체 데이터 검증은 119번 범위 | 한계는 유지하되 현재 업무의 미충족으로 단정하지 않음 |
| ‘정상 CLI 값이 있는데 환경변수 때문에 실패한다’ | 문서와 어긋나는 동작 재현 | 구현 수정 및 회귀 테스트 요청 |
| ‘NaN·inf 설정을 유효하다고 반환한다’ | 실제 설정 파서에서 재현 | 구현 수정 및 회귀 테스트 요청 |
| ‘MATCHED가 0편이면 왜 여기서 멈추는가’ | 코드에서 실패함. 확인한 문서에는 선택 배경 없음 | 운영 순서를 바탕으로 근거 설명 요청. 오류로 단정하지 않음 |
| ‘초록색 CI가 Spark 테스트 성공을 증명한다’ | 실행 Job은 backend-mr-test 하나 | CI와 별도 Spark 검증 결과를 구분해 기재 |

현재 자료로 구현 방향 전체가 잘못됐다고 결론낼 근거는 찾지 못했습니다. 설정 결함 두 건의 수정 요청은 재현 근거가 있고, MATCHED 0편 실패 조건은 사람이 판단할 수 있도록 설명을 보완할 필요가 있습니다.

## 재현 스크립트

같은 폴더의 `verify_mr82.py`를 사용했습니다. 스크립트는 MR 스냅샷의 `pipeline/spark`를 작업 디렉터리로, 118번 스냅샷의 `data/`를 `/review/data`로 두고 실행합니다. 두 소스 루트는 Python import 경로에 포함해야 합니다. Parquet은 컨테이너 임시 디렉터리에만 생성하고 종료 시 제거합니다.

Docker 내부 실행 명령은 다음과 같습니다. 소스와 스크립트가 `/review`에 읽기 전용으로 연결됐다는 전제입니다.

```bash
python3 -m pip install --target /tmp/mr82-deps pytest==8.4.2 ruff==0.12.12
export PYTHONPATH="/tmp/mr82-deps:/opt/spark/python:$(find /opt/spark/python/lib -name 'py4j-*-src.zip' -print -quit):/review/pipeline/spark:/review"
export PYTHONDONTWRITEBYTECODE=1 SPARK_LOCAL_IP=127.0.0.1
cd /review/pipeline/spark
python3 -m pytest -q -p no:cacheprovider
python3 -m ruff check . --no-cache
python3 -m ruff format --check . --no-cache
python3 -m jobs.batch.als_baseline --help
python3 /review/verify_mr82.py
```

## 참고 자료

- [원 MR 본문](https://lab.ssafy.com/s15-bigdata-dist-sub1/S15P21E106/-/merge_requests/82)
- [Jira 267](https://ssafy.atlassian.net/browse/S15P21E106-267)
- [입력 검증 코드](https://lab.ssafy.com/s15-bigdata-dist-sub1/S15P21E106/-/blob/aa5857fe8ec34ef563077f2f879be24db794b386/pipeline/spark/batch/train/inputs.py)
- [설정 코드](https://lab.ssafy.com/s15-bigdata-dist-sub1/S15P21E106/-/blob/aa5857fe8ec34ef563077f2f879be24db794b386/pipeline/spark/batch/train/settings.py)
- [118번 전처리 코드](https://lab.ssafy.com/s15-bigdata-dist-sub1/S15P21E106/-/blob/90b3c8212a9459c4f181baed8ec454ffbfc9c0ae/data/pipelines/movie_id_mapping/pipeline.py)
- [실행된 CI 파이프라인](https://lab.ssafy.com/s15-bigdata-dist-sub1/S15P21E106/-/pipelines/181122)

이 문서는 반려 댓글 초안을 게시하기 위한 승인을 대신하지 않습니다. 실제 수정 요청·승인·병합은 수행하지 않았습니다.
