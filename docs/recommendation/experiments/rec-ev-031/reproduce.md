# REC-EV-031 실행과 재현

상태: DRAFT — 실데이터 실행 상태는 run.yaml과 검토 기록을 확인한다.

작업 디렉터리는 `C:\higher\projects\FEELM-standalone`이다. 원본 자료와 완료한 과거 run을
변경하지 않는다. config.json은 원본 경로·bytes·SHA를 지정하며 자료가 없거나 다르면 중단한다.
별도 미사용 Test를 받거나 새로 내려받아 대체하지 않는다.

```powershell
python -m pytest scripts/tests/test_rec_ev_031_catalog_bridge.py -q
python scripts/rec_ev_031_catalog_bridge.py --phase prepare
python scripts/rec_ev_031_catalog_bridge.py --phase score
python scripts/rec_ev_031_catalog_bridge.py --phase evaluate
```

실데이터 명령은 implementation-review.json의 PASS와 현재 코드·config·설계 SHA 일치가
필요하다. 검토가 없거나 수정 후 해시가 달라지면 자동 실행하지 않는다. 정답 파일은 score-seal이
현재 입력·코드와 일치하는지 확인한 다음 evaluate에서만 읽는다.

완료 단계 재호출은 원래 봉인과 출력의 동일성을 확인하고 재사용한다. 완료 결과를 새 실행처럼
덮어쓰지 않는다. 불완전한 출력이나 변경된 의존성이 있으면 실패 기록과 함께 중단한다.
수정이 필요하면 기존 실패를 보존하고 재검토한 새 실행 버전을 사용한다.

현재 환경의 라이브러리 버전은 prepare-seal과 run.yaml에 기록한다. 이 실행은 기존 Python,
NumPy, pandas, SciPy, PyArrow와 pytest를 사용하며 새 모델 학습 라이브러리를 설치하지 않는다.
원본 대용량 자료는 Git에 포함되지 않으므로 코드만으로 다른 컴퓨터에서 재현된다고 주장하지 않는다.

주요 출력은 `outputs/recommendation-evidence/rec-ev-031`의 prepare-seal.json,
score-seal.json, evaluation-seal.json, rankings.npz, user-metrics.parquet, metrics.json,
cases.json이다. docs 폴더의 artifacts.json에서 실제 해시를 확인한다.

원본 실행 코드·config·README는 완료 후에도 감사된 bytes를 유지한다. config의 초기
`DRAFT_FOR_INDEPENDENT_DESIGN_REVIEW` 상태 문구는 고정 bytes의 일부이며,
실제 실행 허가/완료 상태는 implementation-review.json과 run.yaml을 따른다.

score-resources.json은 score 종료 시 자원 장부를 복사한 값이다. execution-budget.json은
마지막 프로세스의 최대 resident와 전체 누적 계산 시간을 가진다. 검토·문서 작업은 실행 시간에
포함하지 않는다. 이미 끝난 단계를 검증 재호출하면 장부의 검증 시간은 추가될 수 있지만
봉인된 원래 단계 시간과 결과를 덮어쓰지 않는다.

수치 연산은 기록된 NumPy/SciPy의 sparse 경로를 사용한다. dense 합산으로 바꾸는 등
연산 순서가 달라지면 극히 가까운 동률의 순서가 바뀔 수 있다. 독립 검토의 재현 범위와
경계 후보 차이는 comparison.md에 기록했다. 타 환경에서는 기존 ranking hash와 일치
여부를 확인하고, 다르면 동일 실험이라고 조용히 덮어쓰지 않는다.
