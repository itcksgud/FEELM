# REC-EV-019C — 실제 실행 전 계산량 사전점검

> 상태: `PASS_RESOURCE_CONTRACT_RUNNER_IMPLEMENTATION_READY`
> 실행일: 2026-09-02
> 읽은 범위: 7개 Parquet의 schema·행 수·파일 크기가 든 footer만 읽음
> 읽지 않은 것: 평점 행, 영화 feature vector, Locked Test 파일

## 한 줄 결론

첫 점검에서 LightFM 약 61.5억 학습 update, 전체 모델 약 75.5억 사용자×영화 점수 계산과 미정 BPR pair를
찾았다. 그 결과를 보고 계약을 두 단계 실행으로 고쳤다. 재점검 결과는 약 15.8억 score, B8 최대 12.3억
update, B4 최대 3.93억 관측 pair update로 정해 둔 상한 안에 들어왔다. 실제 모델 실행은 아직 막혀 있지만,
이제 이 상한을 지키는 runner를 구현할 수 있다.

## 무엇을 확인했나

데이터 값을 읽지 않고 Parquet footer만 열어 실제 파일의 행 수와 계약의 trial 수를 곱했다. RRF는 이미
생성된 Top-500 순위만 합치므로 전체 41,625편을 다시 점수화하는 계산에서는 제외했다.

| 항목 | 확인값 |
| --- | ---: |
| 최종 공통 후보 | 41,625편 |
| Validation K0·K5·K10 context 합 | 4,767개 |
| 개인화 K5·K10 context 합 | 3,093개 |
| 최초 계약의 전체 후보 점수 계산 | 7,547,569,875회 |
| 수정 계약의 전체 후보 점수 계산 | 1,577,421,000회 |
| 최초 B8 LightFM 학습 update 상한 | 6,152,743,200회 |
| 수정 B8 LightFM 학습 update 상한 | 1,230,548,640회 |
| 수정 B4 관측 pair update 상한 | 392,607,360회 |
| RRF rank 합산 상한 | 14,644,500회 |
| selected Top-500 예측 저장 예상 | 11,662,500행 |
| 한 score buffer 상한 | 1 MiB |

마지막 1 MiB는 전체 행렬을 메모리에 올리지 않고 64명×4,096편 block으로 계산한다는 뜻이다. 메모리
buffer는 작지만, 같은 full catalog scan과 학습을 너무 많이 반복하는 것이 병목이다.

## 처음 발견한 네 가지 차단점과 해결

| 차단점 | 쉬운 설명 | 다음 수정 |
| --- | --- | --- |
| `B4_PAIR_SAMPLING_UNDEFINED` | BPR 한 epoch의 pair 수가 없어 실행량과 재현 결과가 달라질 수 있음 | 사용자당 epoch 최대 16쌍, hash 순서·비복원 추출 고정 |
| `STOCHASTIC_GRID_REPEATS_ALL_FIVE_SEEDS` | B4·B8의 모든 후보 설정을 seed 5개로 반복함 | grid는 seed 17, 선택 trial만 5-seed 패널 안정성 확인 |
| `B8_WORST_CASE_UPDATE_BUDGET_UNBOUNDED` | LightFM 최대 update가 61.5억 회 | epoch 10, 최대 12 fit·13억 update 상한 |
| `FULL_CATALOG_SCORE_BUDGET_UNAPPROVED` | 약 75.5억 개 점수를 계산 | K별 256명 tuning panel + 선택 trial 전체 Validation, 16억 상한 |

재실행 결과 `budget_checks` 7개가 모두 PASS했고 열린 차단점은 0개다. seed는 좋은 결과가 나온 것을 고르는
대상이 아니다. 사전에 고정한 seed 17이 전체 Validation과 나중의 Locked Test에서 primary가 되며, 나머지
4개 seed는 고정 tuning panel에서 결과가 심하게 흔들리는지만 확인한다.

## 이 결과가 의미하지 않는 것

- 어떤 추천 모델이 좋거나 나쁘다는 성능 결과가 아니다.
- 평점 행이나 TMDB vector를 분석한 결과가 아니다.
- Locked Test를 열거나 개인화 champion을 선택한 것이 아니다.
- 현재 서비스의 popularity-only 정책을 바꾼 것이 아니다.

오히려 “모델을 많이 돌리면 더 과학적이다”라는 가정을 실행 전에 반박한 결과다. 같은 데이터로 선택할
hyperparameter까지 매번 seed 5개와 전체 41,625편에 반복하면 계산비는 커지지만 독립적인 정답이 늘지는
않는다.

## 다음 Gate

1. 이 계산 단계와 상한을 그대로 구현하는 실제 runner를 작성한다.
2. runner 단위·합성 검사에서 panel 선택, B4 pair, seed 단계, hard-limit checkpoint를 확인한다.
3. 실제 데이터 값은 runner 코드 검토와 별도 실행 Gate가 열리기 전까지 읽지 않는다.
4. 실제 실행 중 16시간 hard limit에 닿으면 checkpoint 후 selection 없이 멈춘다.

## 재현

```powershell
npm run recommendation:019c:resource:run
npm run recommendation:019c:resource:check
npm run recommendation:vnext:readiness:check
```

기계 판독 결과는 다음 두 파일에 있다.

- `docs/recommendation/evidence/results/rec-ev-019c-resource-dry-run.json`
- `docs/recommendation/evidence/manifests/rec-ev-019c-resource-dry-run.json`
