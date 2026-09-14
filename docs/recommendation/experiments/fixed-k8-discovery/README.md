# 고정8맛과 발견 추천 연구

상태: DRAFT 개인 연구. 상위8맛은 사용자 확정 조건이고, 하위 개수는1/2/4/8/16/32/64/128/256 실험이다. 결과 전에 [DESIGN.md](DESIGN.md)와 [config.json](config.json)을 독립 검토하고 [execution-review.json](execution-review.json)에 코드 해시를 고정했다.

작업 브랜치 `research/fixed-k8-discovery-20260912`, base `e38d662`. 전체 코드·결과는 새 worktree에 있고 기존 개인/팀 작업을 수정하지 않는다. 별도 Jira/팀 API/DB 계약 변경 없이 로컬 내부 인터페이스를 검증한다.

실행 위치:

```powershell
Set-Location 'C:\higher\projects\FEELM-standalone\.codex-tmp\fixed-k8-discovery-20260912'
$k8Python = 'C:\higher\projects\FEELM-standalone\outputs\recommendation-evidence\text339-runtime\venv\Scripts\python.exe'
& $k8Python scripts/k8_tests.py
& $k8Python -u scripts/k8_prepare.py
& $k8Python -u scripts/k8_train.py
& $k8Python -u scripts/k8_recommend.py
& $k8Python -u scripts/k8_finalize.py
& $k8Python scripts/report_fixed_k8.py
& $k8Python scripts/plot_fixed_k8.py
& $k8Python scripts/check_fixed_k8_serving.py
```

완료된 단계는 덮어쓰지 않는다. 별도 재실험은 새 worktree/output에서 독립 검토 후 실행한다. 원자료와 기존ALS는 읽기 전용이며 config의 경로·해시로 확인한다. 각 다음 단계는 이전 산출물 전체 파일목록/해시와 실행 코드 지문을 검증한다. 데이터가 달라지거나 코드를 바꾸면 기존 결과를 같은 실험으로 덮어쓰지 않는다.

대용량·사용자별 산출물은 ignored `outputs/fixed-k8-discovery/`에 있다. 주요 단계:

- `prepare/`: 전체 서비스 카탈로그, 본문 ID/가용량/매핑 집계, 원본 체크섬.
- `train/`: 전처리, 공통 콘텐츠 복원 비교, 선택, seed 안정성, 상위 중심과 하위 K 탐색·계층 후보.
- `recommend/`: train 통계/ALS 정렬 모델, 전체 과거 이력 연결, 고정 역할, 후보/품질/시간 곡선, 선택 및 검증 사용자 결과.
- `final/`: 전체 배정 Parquet/CSV.gz, freeze bundle, manifest와 전수 불변성 결과.

신규 영화는 정상화 JSON 배열로 입력한다. `genre_ids`와 `keyword_ids`는 TMDb 정수 ID 목록, `overview`는 원문 문자열이다. 필드 누락/null은 빈 값으로 취급하며 제목·ID·평점은 분류 입력에 쓰지 않는다.

CLI의 `service_movie_id`는 **모든 행에 제공하거나 모든 행에서 생략**한다. 일부 행에만 ID를 넣는 혼합 입력은 지원하지 않는다. 서비스 목록과 연결할 때는 각 행에 유효한 ID를 제공한다. 콘텐츠 필드의 행별 누락/null은 지원한다.

```powershell
& $k8Python scripts/k8_assign.py --bundle outputs/fixed-k8-discovery/final --input new-movies.json --output assigned-new-movies.json
```

이 경로는 학습을 호출하지 않는다. 모델 해시, 추론 코드 해시, Python/NumPy/SciPy/sklearn 버전을 검사한다. 배정 추가/삭제에 따라 중심·번호·이름을 재계산하지 않는다. 이름 또는 규칙 변경은 새 모델 버전이 필요하다.

보장 대상은 디스크 모델 해시와 전처리 수치 규칙·배정이다. sklearn의 비의미적 `_stop_words_id` 프로세스 캐시는 첫 추론에서 바뀔 수 있으므로 서로 다른 프로세스의 전체 in-memory pickle 바이트까지 같다는 보장은 하지 않는다. 독립 검토에서 이 캐시 외의 규칙과 재배정, 디스크 bundle 해시는 동일했다.

`serve_fixed_k8.py`의 `CatalogRuntime`은 로컬 연동용 연구 어댑터다. `upsert(records)`와 `remove(service_ids)`는 기존 중심·ID·학습 통계를 재계산하지 않고 후보 목록만 갱신한다. 삭제는 tombstone으로 기록해 과거 평가와 고정 prior를 보존한다. 새 영화는0개 학습 평점/ALS 미지원에서 시작하며 고정 콘텐츠 fallback을 사용한다. `recommend(history, mode='group' 또는 'flat')`은 최신순 history의 cap부터 정하고 연결 가능한 입력만 사용한다. 전체 알려진 시청 영화는 cap과 관계없이 제외하며 모든 후보를 봤거나 활성 영화가 없으면 빈 결과를 반환한다. 초기 적재·인덱스 갱신 비용은 실험의 warm 요청 시간에 포함되지 않는다.

그룹 모드는 실험 재현을 위한 옵션이다. [결과](RESULT.md)의 실제 이력 사용자 성능 때문에 개인화 서비스의 기본 정책으로 채택하라는 제안은 아니다. 현 단계 적용 제안은 무그룹 기준선 유지, 고정 분류와 실험용 그룹 경로 제공이다.

[품질·시간 그림](quality-and-latency.png) · [전체 분류와 근거 부족 그림](catalog-evidence.png) · [한국 익숙한 영화의 ID 검증 예시](KOREAN-EXAMPLES.md) · [전체 하위 그룹](subgroups.csv).

현재 원본은2026-09-09 export/09-10 mapping이다. 추천의 과거 평점 cutoff는2023-01-01,180일 목표이며 현재 메타데이터를 쓰는 snapshot-assisted 개발 평가다.270명은 기존 연구 재사용 사용자이므로 새 최종test로 부르지 않는다. MovieLens 이용자와 서비스 이용자는 별개이고 미평가는 UNKNOWN이다. TMDb 및 GroupLens 출처·이용조건은 원본 확보README와 저장소 데이터 계약을 따른다.
