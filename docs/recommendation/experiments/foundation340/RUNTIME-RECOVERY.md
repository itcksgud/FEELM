# cgroup 메모리 계측 호환 복구

상태: DRAFT — 실행 중 발생한 계측 오류와 복구 기록. 과거 봉인·과학적 비교 조건을 바꾸지 않는다.

B의 FM 학습, native 모델·portable 계수·93,230개 예측 및 실제 행 partition 저장은 완료됐다.
마지막 metrics 구성에서 `/sys/fs/cgroup/memory.peak`가 없어 프로세스가 종료1로 끝났다.
이 컴퓨터는 이전 실행 코드에서 지원하던 cgroup v1 경로도 확인해야 한다.

Hubble은 보존된 계수3,911개 전부, 예측93,230행 전부(max error3.552713678800501e-15),
실제8partition의 개수·정렬row_id 해시를 독립 검산했다. 새로 학습하거나 미래 정답을 읽지 않았다.
B의 학습 함수 시간·원 train RMSE·최고 메모리 계측값은 저장되지 않았으므로 **UNKNOWN**이다.
로그 시각이나 중간 Docker 메모리 표본을 최고값/학습시간으로 바꿔 쓰지 않는다.

복구는 새 `foundation340_resume.py`와 `foundation340_worker_v2.py`를 독립 검토한 뒤 수행한다.
원 worker·prepare/fit 코드·준비파일·원본 봉인은 그대로 보존한다.
v2 worker의 변경은 메모리 지표 수집뿐이다. v2 경로가 없으면
`/sys/fs/cgroup/memory/memory.max_usage_in_bytes`를 읽고, 둘 다 없으면 UNKNOWN으로 남긴다.
변수, 사용자/목표/특징, Spark partition, FM 설정과 모델 seed는 변경하지 않는다.

B는 검산된 기존 산출물에 누락 계측을 명시한 metrics와 정렬 예측·모델 봉인만 추가한다. 재학습하지 않는다.
R/H/RH만 v2 worker로 한 번씩 실행해 총4개 FM 학습 예산을 유지한다.
각 모델 폴더에 실제 실행 코드·복구 명세·독립 검토 기록의 복사본과 해시를 포함해 모델 봉인으로 보호한다.
기존 model-seal의 fingerprint는 같은 준비자료/과학적 실행 명세의 지문이고,
실제 계측 버전은 `runtime-sources` 및 `runtime-manifest.json`에 명시한다.
최종 결과에서 수치 검산 PASS와 B 자원 계측 미확보를 분리한다. 12GiB 준수 PASS로 채우지 않는다.
