# GBT 전환을 위한 실험 파일 안내

2026-09-22 사용자 결정은 **개인 추천과 파티 추천을 모두 GBT로 교체**하는 것이다.
이 디렉터리는 그 결정에 사용할 로컬 실험 코드·집계 근거를 정리한 것이며, 서비스 전환 완료나
추천 품질 검증 통과를 뜻하지 않는다. FM은 비교·재현용 과거 결과로 보존한다.

## 먼저 볼 파일

| 파일 | 내용 |
| --- | --- |
| [RESULTS.md](RESULTS.md) | 실제 학습 범위, 개선되지 않은 수치, 검증과 남은 일 |
| [training-metrics.json](training-metrics.json) | 기존 학습 run의 집계 지표·설정·특징 순서 원문 |
| [artifact-index.json](artifact-index.json) | 게시한 소스 25개·테스트 4개 및 로컬 핵심 산출물의 bytes/SHA-256 |
| [requirements.txt](requirements.txt) | 기존 Windows Python 3.12.10 검증에 사용한 패키지 버전 |

이름에 v1~v7이나 user8이 남아 있는 일부 모듈은 최신 실행의 import 의존성이다.
과거 실행 진입점을 새 서비스 정책으로 채택한 것이 아니다. 예를 들어 과거 비교 페이지의
한글 제목 조건이나 K 버튼은 아래 `service_model_v12.py`의 입력 조건이 아니다.

## 파일별 역할과 실행 순서

| 단계 | 기준 소스 | 역할 |
| --- | --- | --- |
| ID 연결 | [build_ml32_tmdb_kobis_source_v1.py](../../scripts/build_ml32_tmdb_kobis_source_v1.py) | MovieLens↔TMDB↔서비스 영화 연결과 KOBIS 메타데이터 출처 확인 |
| 시간순 입력 | [build_ml32_temporal_examples_v1.py](../../scripts/build_ml32_temporal_examples_v1.py) | 관측 평점·이력의 시간순 사례 구성 |
| 학습 행 구성 | [build_strict_dataset_v12.py](../../scripts/build_strict_dataset_v12.py) | 공개근거·직접 관계별 표본 구성, 실제 관측 정답과 표본 가중치 |
| 직접 관계 | [strict_evidence_v12.py](../../scripts/strict_evidence_v12.py) | 시리즈·감독·배우 등 직접 근거 및 공개 자격 함수 |
| 학습 | [train_strict_models_v12.py](../../scripts/train_strict_models_v12.py) | 고정 v12 GBT/FM 학습 및 기존 v9와 개발 자료 비교 |
| 서비스 입력 core | [service_model_v12.py](../../scripts/service_model_v12.py) | 전체 평가, 관심없음 1점/실평점 우선, 감상 제외, 개인 후보 점수 |
| 추론 번들 추출 | [export_service_bundle_v12.py](../../scripts/export_service_bundle_v12.py) | 학습된 가중치와 동일 AST 추론 함수·메타데이터 묶기 |
| 검증 ZIP | [prepare_server_handoff_v12.py](../../scripts/prepare_server_handoff_v12.py), [verify_server_handoff_v12.py](../../scripts/verify_server_handoff_v12.py) | 재학습 출처, K 분포, 합성 추론 fixture, 파일 무결성 |

소스를 이번 게시에서 재설계하지 않았다. `artifact-index.json`에 원본과 같은 bytes/SHA를 남겼다.
학습 정답은 MovieLens의 실제 관측 평점이다. 미시청 FEELM 후보를 가짜 부정 라벨로 만들지 않는다.
현재 TMDB/KOBIS snapshot을 과거 MovieLens에 붙인 회고 분석이며 당시 이용 가능했던 정보만으로
검증한 결과는 아니다. 검증 자료 재사용과 이 분포 차이를 결과 해석에서 유지한다.

## 재현할 수 있는 범위

Git에는 모델 가중치·학습 Parquet·원본 MovieLens/TMDB/KOBIS·FEELM 평가·사용자별 HTML을 넣지 않았다.
따라서 **이 저장소만 clone해서 전체 학습이나 기존 추천을 재현할 수는 없다**.
`artifact-index.json`의 외부 산출물은 파일을 식별하는 색인이지 다운로드 주소가 아니다.
라이선스·접근 권한을 확인한 별도 파일 인계가 필요하다. source data 및 과거 v9 baseline도 필요하다.

새 가상 환경에서 문법/소형 합성 검증을 실행한다. 운영 환경에 아래 패키지를 덮어 설치하지 않는다.

```sh
python -m pip install -r experiments/strict-v12-publication/requirements.txt
python -B -m pytest -q tests/test_service_model_v12.py tests/test_server_handoff_v12.py tests/test_strict_evidence_v12.py tests/test_strict_multiple_links_policy.py
python -B scripts/train_strict_models_v12.py --help
python -B scripts/prepare_server_handoff_v12.py --help
```

학습 명령의 `--dataset`, `--bridge`, `--catalog`, `--references`, `--baseline`, `--output`은
원본 run에 맞는 파일 세트를 사용해야 한다. 같은 이름의 임의 파일로 대체하지 않는다.
`--baseline`은 이전 v9 GBT/FM 디렉터리의 부모 경로다. 과거 run을 덮어쓰지 않고 새 output을 쓴다.
이 게시를 위해 학습·최종 시험·새 holdout을 다시 실행하지 않았다.

## 현재 보유 전달본과 최종 서비스 번들의 차이

로컬 보관 파일: `outputs/model-serving/feelm-gbt-fm-server-handoff-20260922.zip`.
23,331,069 bytes / 내부 43파일 / SHA-256
`0cb4332da474363541e831955019547569ce83db388e2a93fcbbf515ce73eb3c`.

이 ZIP은 **GBT+FM 개인 추론 재현본**이다. 기존 manifest가 양 모델 파일을 요구하므로
FM 파일만 지우고 GBT 전용 운영 번들이라고 부르면 무결성이 깨진다. 원본은 보존한다.
`--model gbt`로 GBT 개인 추론을 선택할 수 있지만, 이 ZIP에는 파티 GBT 실행기와
TASTE ON / DISCOVERY OFF / PARTY ON 유형별 연결이 없다. 현재 개인 자격 함수를
발견 추천에 그대로 적용하지 않는다.

최종 GBT 전용 번들은 Jira626에서, 개인 scorer/스냅샷은627에서, 파티 GBT는747에서 연결한다.
K=0은 개인화 추론 대신 `NEEDS_HOST_COLD_START`를 반환하며483의 별도 공급 경로가 필요하다.
Linux·실서버·Redis/Kafka·전체 사용자 재게시·rollback은644/628/735의 검증 범위다.
API/DB/Consumer 현행 계약은 팀 GitLab이 기준이며 이 연구 소스가 자동으로 대체하지 않는다.

## 이전 결과의 분류

- 현재 전환 참고: 위 v12 개인 core, 특징/정규화/정책 함수, 검증 도구 및 hash 고정 원본.
- 비교 이력: [FM-v6 결과](../fm-v6/RESULTS.md), [GBT 0~N 결과](../gbt-zero-n/RESULTS.md), v6~v11 및 ALS+콘텐츠 연구.
- 전달 금지: 이름이 `REVOKED-`인 인계 ZIP, 실패 run, v12 bundle r1. 삭제하지 않고 원래 위치에 보존한다.
- 비공개 로컬: 원평가, 원본 데이터, 학습/예측 행, 사용자별 비교 HTML, 예약 holdout.

원본 파일은 이동·삭제·덮어쓰지 않았다. 이번 게시 위치가 새 자료의 읽기 시작점이다.
