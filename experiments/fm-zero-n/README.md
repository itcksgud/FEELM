# S15P21E106-623 FM 0~N 실험

상태: **10% 검증 실행 및 독립 검증기 완료, 최종 시험은 채택 결과에서 미사용**

이 실험은 GBT용 조밀 집계 벡터를 재사용하지 않고, Spark `FMRegressor`가 실제 쌍별 요인
상호작용을 학습하도록 MovieLens 장르 기반 희소 특징을 구성한다. 사용자 ID는 넣지 않으며,
학습에 없던 사용자도 후보와 이력의 콘텐츠가 있으면 점수를 계산한다. Spark FM은 CPU 실험이다.

## 입력과 특징 계약

- 622와 같은 초기화값 622, 역할 분할, 시간 창, 에피소드, 후보 계약을 재생성했다.
- 역할은 TRAIN 15,000 / VALIDATION 3,000 / FINAL_TEST 2,000명이다.
- 어휘와 정규화기는 학습 자료만으로 적합한다.
- 이름공간은 후보 장르, 양성 이력 장르, 음성 이력 장르, 통합 이력 장르,
  후보-이력 관계, 수치 이력으로 분리하며 각 범주 이름공간에 독립 미등록 값과 결측 위치가 있다.
- MovieLens `movies.csv`에 검증된 director/actor/keyword가 없으므로 해당 block은 0 채움 없이
  `UNAVAILABLE`로 선언하고 비활성화한다.
- KOBIS와 TMDB는 검증된 시점 자료와 연결표가 없어 사용하지 않는다.
- 지원하지 않는 후보는 0점으로 바꾸지 않고 `supported=false`, 이유, 빈 예측을 유지한다.
  이번 10% MovieLens 후보는 모두 지원돼 실제 미지원 행은 0이었다.
- `UNKNOWN_SAMPLED`는 관측 음성으로 바꾸거나 순위에서 제거하지 않는다.

Spark FM의 공개 학습 경로에서 표본 가중치 동작이 확립되지 않아, 학습은 사용자-대상마다
SHA-256으로 고른 N 변형 하나만 사용한다. 검증은 고정된 모든 N 앞부분 이력을 보존한다.

## 비교 프로필

| 프로필 | 추정기 | 특징 |
| --- | --- | --- |
| `movie_only_fm` | FM | 후보 장르·연도·장르 수 |
| `sparse_linear_only` | 선형 회귀 | 전체 FM과 정확히 같은 희소 벡터, 요인 상호작용 없음 |
| `legacy_aggregate_fm` | FM | 조밀 수치 집계에 가까운 legacy 기준선 |
| `sparse_pos_neg_history_fm` | FM | 후보 콘텐츠 + 양성·음성 장르 이력 |
| `sparse_history_content_fm` | FM | 위 특징 + 명시적 후보-이력 관계 |
| `unified_history_ablation_fm` | FM | 양성·음성을 통합 이력으로 합친 제거 실험 |
| `explicit_relation_block_removed_ablation_fm` | FM | 명시적 관계만 제거; 잠재 FM 상호작용은 유지 |
| `genre_content_block_removed_ablation_fm` | FM | 장르 묶음 제거, 연도·수치 이력만 유지 |

현재 장르만 사용할 수 있는 조건에서는 `sparse_pos_neg_history_fm`과
`explicit_relation_block_removed_ablation_fm`의 실제 위치 집합이 같다. 따라서 두 결과가 같은 것은
재현성 오류가 아니라 프로필 중복이며, 감독·배우·핵심어를 검증해 추가하기 전까지 독립 비교로
해석하면 안 된다.

## 실행 순서

대용량 입력과 결과는 Git 밖 `C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623`에 있다.
모든 명령은 기존 출력 디렉터리를 덮어쓰지 않는다.

```powershell
docker run --rm --network none `
  -v "${PWD}:/workspace:ro" `
  -v "C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623:/runs" `
  -v "C:\Users\SSAFY\cksgud\higher\projects\MM\data\raw\ml-32m:/movielens:ro" `
  -w /workspace feelm-fm-zero-n-spark:local `
  python3 scripts/fm_zero_n_prepare.py `
  --input-root /runs/ten-percent-input-regenerated-v1 `
  --movielens-root /movielens `
  --output-root /runs/ten-percent-prepared-v2 `
  --source-revision d227ab49f6f1cf00594bb36f6b79e5cd53b8f400

python scripts/fm_zero_n_run.py `
  --prepared-root C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623\ten-percent-prepared-v2 `
  --output-root C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623\ten-percent-fits-v1 `
  --seed 623
```

주 검증에서 요인 프로필 중 사용자 평균 MSE가 가장 낮은
`sparse_history_content_fm`만 초기화값 1623과 2623으로 재실행했다. 최종 평가기는 설정된 세 초기화값이
하나라도 없거나 예측 트리 해시가 다르면 실패한다.

```powershell
python scripts/fm_zero_n_run.py --append `
  --prepared-root C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623\ten-percent-prepared-v2 `
  --output-root C:\Users\SSAFY\cksgud\higher\runs\S15P21E106-623\ten-percent-fits-v1 `
  --seed 1623 --profiles sparse_history_content_fm
```

평가와 검증은 Docker 이미지 안의 pandas/pyarrow를 사용한다. 최종 검증기는 고정 입력,
소스·실행 환경, 준비 파일, 모델·예측 트리, 행 키 다중집합, 보고서 연결을 다시 계산한다.
검증된 결과와 해석은 [RESULTS.md](RESULTS.md), 환경 이전은 [LAPTOP-HANDOFF.md](LAPTOP-HANDOFF.md)에 있다.
