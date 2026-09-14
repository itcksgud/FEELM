# 고정 8맛 발견 추천 v2

상태: DRAFT — 개인 연구 결과 묶음. 운영 계약 또는 배포본이 아니다. 실험 완료 판정은 `FINAL-DECISION.md`, 실제 수치는 `RESULT.md`, 사례는 `KOREAN-EXAMPLES.md`에서 확인한다.

사용자가 요청한 범위는 상위 8맛을 그대로 두고 내부 그룹을 더 세분화한 뒤, 덜 본 그룹에서 개인 취향과 맞는 후보를 찾고 평가 수를 반영한 대중 평점으로 추린 실제 추천까지 비교하는 것이다. K는 **맛 하나당** 하위 그룹 수다. K128은 총1,024개, K256은 총2,048개이며, 전체 서비스 영화237,817편을 분류한다.

## 파일과 재현 위치

- 로컬 worktree: `C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-v2-20260913`
- 브랜치: `research/fixed-k8-discovery-v2-20260913`; 기준 commit `e38d662e6be65949f99ab40bbaab137fc5e1218f`
- 현재 결과: `outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2`
- 원본 v1: `C:/higher/projects/FEELM-standalone/.codex-tmp/fixed-k8-discovery-20260912`; 읽기 전용
- 원본 데이터/모델: `C:/higher/projects/FEELM-standalone/outputs/recommendation-evidence`와 설정에 명시된 서비스 snapshot
- 실행 계약: `EXECUTION.md`, 설정: `config.json`, 독립 검토 기록: `REVIEW-LOG.md` 및 `*-review.json`
- 코드: worktree의 `scripts/dv2_*.py`; 실행 때 정확한 코드·설정·문서 사본은 각 버전 `reviewed-contracts/<stage>`에 보존한다.

대용량 원본·모델·Parquet·픽클은 Git 공유 대상이 아니다. 모든 단계는 결과 파일 크기와 SHA256을 `*-seal.json`에 기록한다. 보존한 전체 환경이 있어야 같은 결과를 검산할 수 있다. 소형 문서만으로 외부에서 모델 추론까지 재현할 수 있다고 주장하지 않는다.

## 실행과 검산

PowerShell, 위 worktree에서 실행한다. 런타임은 Python3.12.5, NumPy1.26.4, SciPy1.15.2, scikit-learn1.9.0, pandas2.2.3이며 두 계산 스레드를 사용한다. 원본 모델의 필요한 라이브러리와 원본 모듈들도 exact pins로 검사한다.

```powershell
$dv2Python = 'C:\higher\projects\FEELM-standalone\outputs\recommendation-evidence\text339-runtime\venv\Scripts\python.exe'
& $dv2Python -m unittest discover -s scripts -p 'test_dv2_*.py'
```

완료된 결과를 덮어쓰는 재실행은 거부한다. 아래는 단계 순서이며, 새 재현 버전은 별도 worktree/run과 정확한 사전 검토를 준비해야 한다. 파일을 지워 재실행하지 않는다.

```powershell
& $dv2Python -u scripts/dv2_prepare.py
& $dv2Python -u scripts/dv2_cluster.py
& $dv2Python -u scripts/dv2_experiment.py predictor
& $dv2Python -u scripts/dv2_experiment.py sweep
& $dv2Python -u scripts/dv2_experiment.py check
& $dv2Python -u scripts/dv2_freeze.py
& $dv2Python -u scripts/dv2_report.py
```

`dv2_common.verify(stage)`는 저장된 결과 전 파일을 다시 hash 검증한다. 예측기 단계는 독립 실제 예측 parity PASS와 그 소스 핀도 요구한다. 예측기 검산 재현 코드는 `audit_dv2_predictor_stage.py`, `dv2_predictor_parity.py` 및 검토 기록의 별도 source-audit 코드다.

## 점수와 추천 절차

TMDb의 평균 평점R·평가 수v에 대해 `Q=(vR+mC)/(v+m)`를 쓴다. 기본값은 m300, C6.314301027022476이다. C는 현재 자격을 갖춘118,790편의 영화별 평균을 다시 평균한 고정값이다. m100·300·1000과 투표 수 가중 C를 비교한다. m은 평균으로 당기는 강도다. Q는 신뢰하한도 개인 예상 별점도 아니다. 낮은 R도 C 쪽으로 올라오므로 '모든 저평가 수 영화에 벌점을 준다'는 설명은 부정확하다.

먼저 전체 관측 시청 이력에서 하위 그룹 시청 수가2 이하이고 비중20% 이하인 그룹을 구한다. 현재 조건에 맞는 미시청 후보가 있는 그룹만 E_u에 남긴다. 그 뒤 사용자 콘텐츠 방향과 동결 대표를 비교한다. 가까운 그룹부터 미시청 Q 상위10편 또는25편씩 예측 예산50·100·200편까지 모은다. 실제로 모은 후보만 같은 예측기로 점수를 계산해 최대10편을 반환한다. E_u가 고갈되면 짧거나 빈 결과를 그대로 반환한다.

개인 예측기는 기존 보정 ALS와 GBT fallback을 재사용한다. 학습 평가 수가 적은 ALS 점수에 GBT를 섞는 보정은 TMDb의 Q와 별개다. 순위에는 원본 보정 후 값을 사용하고, 화면/별점오차 계산만0.5~5점으로 제한한다. 예측 예산B는 영화 수이며, 혼합 예측은 영화 하나에서 두 head를 계산할 수 있어 실제 ALS/GBT 연산 행 수도 따로 기록한다.

## 동결 로컬 추론

`dv2_runtime.FrozenCatalog`는 준비 계약을 만족하는 정규화 metadata DataFrame과 `final/bundle.pkl`을 받는 연구용 인터페이스다. 원시 TMDb JSON을 바로 받는 운영 API가 아니다. 실제로 신뢰하는 로컬 픽클만 읽는다.

- `recommend(ratings, viewed_service_ids, cap=10, budget=None)`: 별점은 최신순,0.5 단위이며 전체 시청 ID에는 별점을 준 영화도 포함해야 한다. cap은 매핑 전에 적용한다. 기본 예산보다 낮은 예산만 요청할 수 있다.
- `append(frame)`: 새로운 서비스 ID만 허용한다. 저장된 전처리·중심·대표·C/m으로 분류하고 후보 인덱스만 갱신한다.
- `remove(service_ids)`: 후보를 비활성화하되 과거 시청/별점의 벡터와 metadata는 보존한다.
- 결과의 `review_status`는 selection 후보/근사 대안, check gate, predictor 상태, 사람의 발견 만족도 미측정을 구분한다. 동결 파일이 있다는 이유로 운영 채택으로 바뀌지 않는다.

## 자료의 해석 범위

270명은 MovieLens 개발 사용자이며 FEELM 실사용자나 한국 사용자 만족도 표본이 아니다. 선택90명은 기존 예측기 calibration90명, check180명은 그와 겹치지 않는 comparison180명이다. 모두 반복 사용한 개발 자료다. 현재 TMDb 집계와 과거 이력을 결합한 현재 카탈로그 시뮬레이션이며, 영화별 신뢰할 수 있는 fetched timestamp가 없어 역사적으로 완전히 누수 없는 추천 평가라고 부를 수 없다.

미평가 영화는 UNKNOWN이다. 미래 MovieLens NDCG/recall은 설명용이며 발견 정책 선택에 쓰지 않는다. 실제 평점 오차는 예측기 진단이고, 같은 콘텐츠 공간의 근사 정확도는 기술 검증이다. 한국 영화 사례와 독립 AI metadata 검토도 설명 오류를 찾기 위한 자료이며 사람이 낯선 영화를 만족스럽게 발견했다는 증거가 아니다.
