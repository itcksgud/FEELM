# 추천 판단 자료 상태

> 상태: `APPROVED` — 생성된 결과만 `READY_FOR_DECISION`으로 바꾼다.

> 제품 결정 패킷: `APPROVED_LOCAL_PRODUCT_BOUNDARIES` — [REC-PD-001/003/005/007 패킷](../product-decision-packet.md), production/champion 권위는 계속 `NO`

| Evidence ID | 상태 | 결정 연결 |
| --- | --- | --- |
| `REC-EV-001` rating style | `COMPLETED` | 공통 기반 |
| `REC-EV-002` prediction calibration | `COMPLETED` | REC-PD-001, REC-PD-002 |
| `REC-EV-003` cold start | `COMPLETED` | REC-PD-002, REC-PD-003 |
| `REC-EV-003B` cold-start dual-head blend | `COMPLETED` | REC-PD-002, REC-PD-003 |
| `REC-EV-003C` MovieLens→C1 rating scale alignment | `COMPLETED_FAIL_CLOSED` | DN-C2-008; paired C1 validation 전 star-disabled |
| `REC-EV-004` exploration Pareto | `COMPLETED_SAMPLED_DIAGNOSTIC` | REC-PD-004; full-catalog·제품 weight 미승인 |
| `REC-EV-004B` full-catalog exploration Pareto | `COMPLETED_FULL_CATALOG_OFFLINE_EVIDENCE` | REC-PD-004; 제품 weight·2+1·champion 미승인 |
| `REC-EV-005` party policy | `COMPLETED_OFFLINE_EVIDENCE` | REC-PD-005 local 경계 선택; public Party champion은 없음 |
| `REC-EV-006` reason faithfulness | `COMPLETED_OFFLINE_EVIDENCE` | REC-PD-007 최대 1개 경계 선택; 실제 contribution·copy 전 UI 비활성 |
| `REC-EV-007` FastAPI serving·Fold-in benchmark | `COMPLETED_LOCAL_PROVISIONAL` | REC-PD-008 / DN-C2-005 |
| `REC-EV-011` cold Fold-in full-catalog ranking | `COMPLETED_FULL_CATALOG_EVIDENCE` | K5/K10 offline candidate; champion·expected-star·UI 미승인 |
| `REC-EV-012` factor-similarity variants | `SKIPPED_BY_PREDECLARED_GATE` | REC-EV-011 K10 nonzero candidate로 조건 불성립 |
| `REC-EV-013` constrained 2+1 | `COMPLETED_FULL_CATALOG_EVIDENCE_NULL_POLICY` | 모든 후보 relevance budget 실패; 2+1·discovery null |
| `REC-EV-008` React UI comparison | `COMPLETED_UI_COMPARISON_EVIDENCE` | 네 local 제품 경계 선택 근거; production UI/champion 승인이 아님 |
| `REC-EV-014` local interpretation lab | `LOCAL_INTEGRATION_PASS_PRODUCT_DECISION_PENDING` | 예상 별점·개인 ECDF·취향 관측 근거를 local-only로 연결; 제품 채택 `NO` |
| `REC-EV-015` discrete relative utility | `COMPLETED_OFFLINE_EVIDENCE` | quantized-midrank ECDF v2를 C6 local experiment에 채택; 만족도·제품 노출 `NO` |

실험을 실행하지 않은 상태에서 빈 결과 문서를 만들어 수치가 있는 것처럼 보이게 하지 않는다.
`TASK-REC-EV-009`까지와 REC-EV-015가 완료됐다. 현 개인 프로젝트 범위에서 실사용자 수집은 하지
않으며, MovieLens offline 실험으로 개선 기록을 계속 남긴다. 따라서 현재 선택을 실사용자 성능·만족도
주장으로 확대하지 않고 제품 노출 Gate는 닫힌 상태로 둔다. 실제 결과는
[REC-EV-001 보고서](./REC-EV-001-rating-style.md),
[REC-EV-002 보고서](./REC-EV-002-prediction-calibration.md),
[REC-EV-003 보고서](./REC-EV-003-cold-start.md),
[REC-EV-003B 보고서](./REC-EV-003B-cold-start-blend.md),
[REC-EV-003C 보고서](./REC-EV-003C-rating-scale-alignment.md),
[REC-EV-004 보고서](./REC-EV-004-exploration-pareto.md),
[REC-EV-004B 보고서](./REC-EV-004B-full-catalog-pareto.md),
[REC-EV-005 보고서](./REC-EV-005-party-policy.md),
[REC-EV-006 보고서](./REC-EV-006-reason-faithfulness.md),
[REC-EV-007 보고서](./REC-EV-007-fold-in-benchmark.md),
[REC-EV-008 보고서](./REC-EV-008-ui-comparison.md),
[REC-EV-011 보고서](./REC-EV-011-cold-foldin-full-catalog.md),
[REC-EV-013 보고서](./REC-EV-013-constrained-two-plus-one.md),
[REC-EV-014 로컬 실험실](./REC-EV-014-local-interpretation-lab.md),
[REC-EV-015 상대 효용 정책](./REC-EV-015-relative-utility.md),
[고정 split manifest](./manifests/global-time-v1.json),
[REC-EV-002 manifest](./manifests/rec-ev-002.json),
[REC-EV-004 manifest](./manifests/rec-ev-004.json),
[REC-EV-004B manifest](./manifests/rec-ev-004b.json),
[REC-EV-005 manifest](./manifests/rec-ev-005.json),
[REC-EV-006 manifest](./manifests/rec-ev-006.json),
[REC-EV-008 manifest](./manifests/rec-ev-008.json)에 있다.

Cold-start 평가에서는 K1부터 별점 MAE가 통계적으로 줄었지만, K0 대비 3% 실질 개선 Gate는
K10부터 통과했다. 초기 sampled ranking에서는 모든 K의 최적 Fold-in 가중치가 0이었으나,
REC-EV-011 full-catalog에서는 K10 alpha 0.2가 Popularity 대비 NDCG@10과 candidate recall@500을
양의 paired CI로 개선해 `K10_FULL_CATALOG_OFFLINE_CANDIDATE`가 됐다. 이는 여전히 public champion,
예상 별점, UI 승인이 아니며 C2B 제품 결정을 자동으로 열지 않는다.

REC-EV-003C는 REC-EV-003B의 동일 held-out 167,194행에서 as-is, clamp, round, affine을 비교했다.
MovieLens 0.5 간격 label은 C1 integer 1~5 결과의 calibration 근거가 아니므로 clamp/round를 기각하고
affine을 보류했다. `c1-product-star-alignment-pairs-v1`의 시간 분리 paired evidence가 생길 때까지
expected-star product adapter는 fail-closed다. 이 결정은 Popularity ranking이나 UI 숫자 승인이 아니다.

REC-EV-015는 이 동일 Validation tail과 사용자의 과거 평점만 사용해 C6 상대 효용 공식을 따로
검증했다. 연속 예측값에 `<=`를 바로 적용하던 v1보다, 0.5 평점 격자로 quantize하고 동점 midrank를
쓰는 v2가 K1·K3·K5·K10·K20 전부에서 MAE를 23.13%~24.43% 줄였고 bias·Spearman·평점 성향
4분위 Gate도 모두 통과했다. 이 결과로 v2를 C6 local experiment에만 채택했으며, C1의 정수 척도
calibration·실사용자 만족도·제품 별점 노출은 계속 차단한다.

REC-EV-004는 사용자별 1 positive + 199 deterministic negatives의 sampled 범위에서 Popularity,
genre content, Hybrid, exploration 후보를 비교했다. Validation 1% loss 후보는 held-out Test에서 실제
NDCG 손실 1.59%로 budget을 벗어났다. 이를 보고한 뒤 budget을 3%로 바꾸지 않으며 full-catalog
재검증과 제품 승인이 있기 전에는 exploration weight·2+1 구성·개인 ranking champion을 확정하지 않는다.
구조화 scoring-feature provenance만 REC-EV-006 입력으로 열었다.

REC-EV-004B는 sampled 결과를 재명명하지 않고 동일 warm cohort를 별도 2-phase protocol로
재검증했다. 각 사용자에서 Train-known 50,977편을 모두 score scan하고 Train-seen을 제외했으며
positive를 강제 주입하지 않았다. Test Popularity의 candidate recall@500은 0.3080, NDCG@10은
0.009382였다. 잠긴 Explore 5% 후보는 같은 Top-500 recall에서 NDCG@10 0.005113이었고 paired
차이의 95% CI는 `[-0.006604, -0.002002]`였다. genre 미상 zero-vector는 diversity 근거로
보상하지 않았고 Explore list/pair genre coverage는 각각 0.90185/0.803722였다. full catalog는
MovieLens Train-known 범위일 뿐 서비스 production catalog coverage가 아니며 weight·2+1·champion은 계속 미승인이다.

REC-EV-006은 REC-EV-004 sampled Test의 40,000개 추천 위치에서 실제 score contribution과
Popularity/novelty/diversity 단일-feature ablation을 비교했다. positive contribution만으로 reason을
허용하지 않고 rank/position 효과까지 있는 경우만 `EMITTABLE_CANDIDATE`로 분류했다. 이 상태는
REC-EV-008 화면 비교 입력일 뿐 UI 문구·표시 개수·공개 reason 승인이 아니다.

REC-EV-007은 실제 Uvicorn Popularity-only HTTP와 별도 비활성 Fold-in core를 분리 측정했다. 사전
local Gate는 모두 통과했고 timeout `750 ms`, healthy freshness `3000 ms`를 배포 전 후보로 선택했다.
원격 네트워크·Spring·DB·container contention을 포함하지 않았으므로 운영 SLA가 아니며 expected-star
또는 confidence 경계를 열지 않는다.

REC-EV-005는 Validation에서 선택한 Balanced 후보를 held-out Test의 270개 합성 party에서
Average·Least Misery·Most Happiness와 비교했다. Average 대비 평균 효용, 최저 효용, 격차의
paired-bootstrap 95% CI가 모두 0을 포함해 개선 근거가 아니었다. 4인 공통평가 후보의 Test 평가
가능 coverage도 0.69%~1.02%뿐이라 observation bias가 심하다. 실제 파티 만족도를 관측하지 않았고
`party_aggregation` champion과 PARTY_BALANCED_V1·공개 API·UI는 계속 미승인이다.

REC-EV-008은 REC-EV-003B/005/006 근거를 개발 전용 React evidence lab에서 네 가지로 비교했다.
네 화면은 동일 1440×1200 viewport로 캡처했지만 실제 사용자 연구는 하지 않았다. 예상 별점,
온보딩 K, Balanced, 추천 이유 문구/개수 어느 것도 제품 정책이나 공개 navigation으로 승인하지 않았다.
