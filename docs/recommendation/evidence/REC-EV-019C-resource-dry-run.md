# REC-EV-019C — 실제 실행 전 계산량 사전점검

> 상태: `PASS_METADATA_AUDIT_IMPLEMENTATION_BLOCKED`
> 실행일: 2026-09-02
> 읽은 범위: 7개 Parquet의 schema·행 수·파일 크기가 든 footer만 읽음
> 읽지 않은 것: 평점 행, 영화 feature vector, Locked Test 파일

## 한 줄 결론

현재 설계를 그대로 실행하면 결과가 나오기 전에 계산량이 과도해질 가능성이 높다. 특히 LightFM은 최악의
경우 약 61.5억 번의 학습 update, 전체 모델 비교는 약 75.5억 번의 사용자×영화 점수 계산이 필요하다.
또 BPR이 epoch마다 관측 LIKE/DISLIKE 쌍을 몇 개 만들지 정해져 있지 않다. 따라서 실제 모델 실행은 아직
막고, pair 수·seed 반복·전체 후보 점수 계산 예산을 먼저 고정한다.

## 무엇을 확인했나

데이터 값을 읽지 않고 Parquet footer만 열어 실제 파일의 행 수와 계약의 trial 수를 곱했다. RRF는 이미
생성된 Top-500 순위만 합치므로 전체 41,625편을 다시 점수화하는 계산에서는 제외했다.

| 항목 | 확인값 |
| --- | ---: |
| 최종 공통 후보 | 41,625편 |
| Validation K0·K5·K10 context 합 | 4,767개 |
| 개인화 K5·K10 context 합 | 3,093개 |
| 현 계약의 전체 후보 점수 계산 | 7,547,569,875회 |
| B8 LightFM 학습 update 상한 | 6,152,743,200회 |
| selected Top-500 예측 저장 예상 | 11,662,500행 |
| 한 score buffer 상한 | 1 MiB |

마지막 1 MiB는 전체 행렬을 메모리에 올리지 않고 64명×4,096편 block으로 계산한다는 뜻이다. 메모리
buffer는 작지만, 같은 full catalog scan과 학습을 너무 많이 반복하는 것이 병목이다.

## 발견한 네 가지 차단점

| 차단점 | 쉬운 설명 | 다음 수정 |
| --- | --- | --- |
| `B4_PAIR_SAMPLING_UNDEFINED` | BPR 한 epoch의 pair 수가 없어 실행량과 재현 결과가 달라질 수 있음 | 사용자별 pair 생성 순서와 최대 개수 고정 |
| `STOCHASTIC_GRID_REPEATS_ALL_FIVE_SEEDS` | B4·B8의 모든 후보 설정을 seed 5개로 반복함 | 탐색 seed와 최종 안정성 seed를 분리 |
| `B8_WORST_CASE_UPDATE_BUDGET_UNBOUNDED` | LightFM 최대 update가 61.5억 회 | epoch·trial·early-stop 또는 pilot 예산 고정 |
| `FULL_CATALOG_SCORE_BUDGET_UNAPPROVED` | 약 75.5억 개 점수를 계산 | 작은 고정 tuning panel과 선택 모델의 전체 Validation을 분리 |

## 이 결과가 의미하지 않는 것

- 어떤 추천 모델이 좋거나 나쁘다는 성능 결과가 아니다.
- 평점 행이나 TMDB vector를 분석한 결과가 아니다.
- Locked Test를 열거나 개인화 champion을 선택한 것이 아니다.
- 현재 서비스의 popularity-only 정책을 바꾼 것이 아니다.

오히려 “모델을 많이 돌리면 더 과학적이다”라는 가정을 실행 전에 반박한 결과다. 같은 데이터로 선택할
hyperparameter까지 매번 seed 5개와 전체 41,625편에 반복하면 계산비는 커지지만 독립적인 정답이 늘지는
않는다.

## 다음 Gate

1. B4의 관측 LIKE>DISLIKE pair 생성 규칙과 사용자별 상한을 고정한다.
2. 모든 trial은 한 고정 seed·고정 tuning panel에서 비교하고, 선택된 trial만 seed 5개와 전체 Validation에서
   안정성을 확인한다.
3. B8 update와 전체 후보 score의 상한을 계약에 적고, 사전점검 검증기가 초과 시 실패하게 한다.
4. 변경된 계약으로 합성 검사·Linux dependency smoke·자원 사전점검을 다시 실행한다.
5. 네 차단점이 모두 사라지기 전에는 실제 Validation을 실행하지 않는다.

## 재현

```powershell
npm run recommendation:019c:resource:run
npm run recommendation:019c:resource:check
npm run recommendation:vnext:readiness:check
```

기계 판독 결과는 다음 두 파일에 있다.

- `docs/recommendation/evidence/results/rec-ev-019c-resource-dry-run.json`
- `docs/recommendation/evidence/manifests/rec-ev-019c-resource-dry-run.json`
