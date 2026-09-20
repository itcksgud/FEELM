# FM-v2 교정 실험

FM-v1의 결과를 폐기하지 않고 설계 결함 가능성을 분리하기 위한 새 변경 금지 실행이다. 고정 설계는
[DESIGN.md](DESIGN.md), 검증된 결론은 [RESULTS.md](RESULTS.md)에 있다. 기존
`experiments/fm-zero-n`과 Git 밖 FM-v1 run은 수정하지 않았다.

## 경로

Git 밖 실행 루트: `C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623`

- `fm-v2-synthetic-v1.json`
- `fixture-prepared-fmv2-v1`
- `fixture-fits-fmv2-v1`
- `ten-percent-prepared-fmv2-v1`
- `ten-percent-fits-fmv2-v1`

Docker 이미지는 FM-v1과 같은
`feelm-fm-zero-n-spark:local` / `sha256:5a469f86...e3eba6a3`이다. GPU, 서버, HDFS,
FINAL_TEST를 사용하지 않았다.

## 재현 순서

아래 명령은 기존 출력을 덮어쓰지 않는다. 재실행할 때는 새 디렉터리 이름을 사용한다.

```powershell
$runs = 'C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623'
$ml = 'C:\Users\SSAFY\cksgud\higher\projects\MM\data\raw\ml-32m'
$revision = (git rev-parse HEAD).Trim()

docker run --rm --network none `
  -v "${PWD}:/workspace:ro" -v "${runs}:/runs" -w /workspace `
  feelm-fm-zero-n-spark:local python3 scripts/fm_v2_synthetic.py `
  --config /workspace/experiments/fm-v2/config.json `
  --output /runs/fm-v2-synthetic-NEW.json

docker run --rm --network none `
  -v "${PWD}:/workspace:ro" -v "${runs}:/runs" -v "${ml}:/movielens:ro" `
  -w /workspace feelm-fm-zero-n-spark:local python3 scripts/fm_v2_prepare.py `
  --input-root /runs/fixture-input-v1 --movielens-root /movielens `
  --output-root /runs/fixture-prepared-fmv2-NEW --source-revision $revision

python scripts/fm_v2_run.py `
  --prepared-root "$runs\fixture-prepared-fmv2-NEW" `
  --output-root "$runs\fixture-fits-fmv2-NEW"
```

점검용 평가기와 검증기가 통과한 뒤에만 10% 실행을 시작한다. `fm_v2_run.py`와 컨테이너 학습기
양쪽이 점검용 검증 보고서를 요구하므로 우회 실행은 실패한다.

```powershell
docker run --rm --network none `
  -v "${PWD}:/workspace:ro" -v "${runs}:/runs" -w /workspace `
  feelm-fm-zero-n-spark:local python3 scripts/fm_v2_evaluate.py `
  --fits-root /runs/fixture-fits-fmv2-NEW `
  --config /workspace/experiments/fm-v2/config.json `
  --output /runs/fixture-fits-fmv2-NEW/validation-report.json

docker run --rm --network none `
  -v "${PWD}:/workspace:ro" -v "${runs}:/runs" -w /workspace `
  feelm-fm-zero-n-spark:local python3 scripts/fm_v2_verify.py `
  --input-root /runs/fixture-input-v1 `
  --prepared-root /runs/fixture-prepared-fmv2-NEW `
  --fits-root /runs/fixture-fits-fmv2-NEW `
  --synthetic-report /runs/fm-v2-synthetic-NEW.json `
  --config /workspace/experiments/fm-v2/config.json `
  --output /runs/fixture-fits-fmv2-NEW/verification-report.json

python scripts/fm_v2_run.py `
  --prepared-root "$runs\ten-percent-prepared-fmv2-NEW" `
  --output-root "$runs\ten-percent-fits-fmv2-NEW" `
  --fixture-verification "$runs\fixture-fits-fmv2-NEW\verification-report.json"
```

10% 준비는 점검용 실행과 같은 `fm_v2_prepare.py`에
`ten-percent-input-regenerated-v1`을 입력해 새 출력을 만든다. 마지막 평가기·검증기도 점검용 실행과
같으며 10% 검증기에는 `--fixture-verification`을 추가한다.

## 현재 판정

실험 산출물은 수용한다. 다만 콘텐츠 FM의 선형 모델 대비 MSE 차이는 통계적으로 확정되지 않았고,
혼합 FM은 선형 모델로 되돌아갔다. 전체 데이터 학습, 서비스 채택, 커밋·푸시·PR·Jira 게시를 수행하지
않았다.
