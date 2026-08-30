# 추천 제품 결정 판단 자료 계획

> 상태: `APPROVED` — 선택지보다 판단 자료를 먼저 만든다.  
> 현재 결론: 오프라인 자료와 정적 화면 비교를 [제품 결정 패킷](./product-decision-packet.md)으로 조립했다. 실제 사용자 근거는 없으므로 보수적 권장안을 제시하되 결정은 제품 소유자 승인 대기다.

## 1. 현재 실제로 있는 자료

| 자료 | 확인 가능한 것 | 판단할 수 없는 것 |
| --- | --- | --- |
| [실제 데이터 감사](../research/movielens-tmdb-data-audit.md) | MovieLens 규모·희소성, TMDB 누락, OTT coverage | 어떤 모델과 UI가 실제로 더 나은지 |
| [추천 평가 설계](../research/movielens-recommendation-evaluation-design.md) | split, 사용자 정규화, 지표, 실험 순서 | 아직 실행하지 않은 MAE·NDCG·cold-start 결과 |
| [제품 격차 분석](../research/feelm-product-gap-analysis.md) | 경쟁 서비스 기능과 예상 별점의 장단점 | FEELM 화면에서 사용자가 어떤 표현을 이해하는지 |
| [결과 효용 추론 계약](./outcome-inference-contract.md) | 저장할 행동·예측·결과와 금지 해석 | 실제 FEELM 사용자의 온라인 결과 |
| [REC-EV-001 실제 결과](./evidence/REC-EV-001-rating-style.md) | 고정 time split, 사용자 rating-style, raw 4+ threshold 편향, warm coverage | 모델별 예상 별점·순위 성능 |
| [REC-EV-004 탐험 Pareto](./evidence/REC-EV-004-exploration-pareto.md) | sampled 동일 후보의 relevance·novelty·diversity·coverage trade-off와 Test budget 회귀 | full-catalog 우열, 제품 탐험 비율·2+1 구성, 온라인 만족 |
| [REC-EV-004B full-catalog 재검증](./evidence/REC-EV-004B-full-catalog-pareto.md) | 50,977 Train-known 전체 scan의 candidate recall@500, Top-10 trade-off, segment·paired CI | 서비스 production catalog 일반화, 제품 탐험 비율·2+1 구성, 온라인 만족 |
| [REC-EV-005 합성 파티 결과](./evidence/REC-EV-005-party-policy.md) | 2/3/4명 합성 그룹의 네 정책 효용·격차·coverage와 순위 반전 | 실제 파티 만족, 일반화 가능한 4인 정책, UI 이해도 |
| [REC-EV-006 reason faithfulness](./evidence/REC-EV-006-reason-faithfulness.md) | 실제 score contribution·ablation 기반 typed reason 후보 coverage | UI 문구·표시 개수·사용자 이해도 |
| [REC-EV-008 React 비교](./evidence/REC-EV-008-ui-comparison.md) | 네 표현안의 정적 정보 밀도·최소 조작 수·동일 viewport 화면 | 실제 사용자 선호·이탈·완료시간·만족도와 제품 기본값 |

REC-EV-001은 완료되어 공통 4점 threshold를 폐기할 실제 근거가 생겼다. REC-EV-002에서는
Validation 뒤 구간의 ALS 직접 coverage 11.74%, warm 보정 MAE 0.6268, 전체 Bias fallback 보정
MAE 0.7345를 관측했다. sampled ranking에서는 Popularity NDCG@10 0.4727, ALS 0.2595로 ALS가
기준선을 이기지 못했다. 예상 별점 화면·온보딩과 파티 실제 로그·화면 자료는 아직 없으므로 해당
제품 결정은 계속 `WAITING_FOR_EVIDENCE` 또는 `PARTIAL_EVIDENCE`다.

REC-EV-003/003B에서는 평가 사용자 3,014명을 학습에서 제외해 최초 K개만 Fold-in했다. 단독
Fold-in은 K20에서도 sampled Popularity 순위를 이기지 못했다. 별점·순위를 분리해 검증한 결과,
별점은 K1부터 통계적 개선이 있었지만 K0 대비 3% 실질 개선 Gate는 K10부터 통과했고, 순위의
최적 Fold-in 가중치는 모든 K에서 0이었다. 따라서 K10은 예상 별점 화면 비교 후보로만 올리고,
온보딩 최대 입력 수는 React K5/K10 비용 자료가 생길 때까지 `PARTIAL_EVIDENCE`로 둔다.

REC-EV-004에서는 Validation의 1% relevance-loss 후보가 held-out Test에서 1.59% NDCG 손실로
사전 candidate budget을 넘었다. 같은 후보는 novelty·diversity·catalog coverage·long-tail exposure를
개선했지만 sampled 범위이며, 이 결과를 보고 3% budget으로 사후 변경하지 않는다. 따라서 탐험 제품
정책은 계속 `PARTIAL_EVIDENCE`이고, REC-EV-006은 실제 scoring provenance만 후속 입력으로 사용한다.

REC-EV-004B는 같은 warm cohort와 잠긴 네 정책을 full Train-known universe에서 평가했다. held-out
positive를 강제 주입하지 않아 Popularity Test NDCG@10은 0.009382, candidate recall@500은 0.3080이었다.
genre 미상을 diversity로 보상하지 않은 Explore 5%의 NDCG@10은 0.005113이었고 paired 차이 CI
`[-0.006604, -0.002002]`는 0 아래였다. 이 full-catalog 후보는 Popularity보다 relevance가 낮았지만
서비스 Catalog 일반화·weight·2+1·champion은 여전히 자동 승인하지 않는다.

REC-EV-006은 40,000개 sampled Test 추천 위치에서 feature contribution과 single-feature ablation
rank effect를 함께 확인했다. active scoring feature가 아니거나 순위 효과가 없는 이유는 typed
`BLOCKED`로 분리했다. `EMITTABLE_CANDIDATE`도 UI 표시 승인이 아니며 REC-EV-008의 reason 화면
비교 입력만 열어 둔다.

REC-EV-008은 예상 별점 표시/숨김, K5/K10/skip, Average/Balanced, reason 1개/최대 3개를
동일 viewport로 만들었다. UI 최소 조작 수와 정보 밀도만 확인했으며 실제 사용자 연구가 아니므로
결과가 생겼다는 이유로 제품 UI·정책·공개 navigation을 승인하지 않는다.

## 2. 결정별로 생성할 패킷

### REC-PD-001 — 예상 별점 표시

생성할 자료:

- React 화면 A: `★ 예상 4.2 / 5`와 개인 척도·confidence 표시
- React 화면 B: 숫자를 숨기고 추천 이유·신뢰 문구만 표시
- 같은 영화에서 외부 TMDB 평점, FEELM 평균, 개인 예상 별점이 섞이지 않는지 검토표
- MovieLens Validation의 개인별 calibration plot과 K3/K5/K10 오차 분포
- 숫자를 표시할 수 없는 coverage 비율

판단 질문: 예상 별점이 즉시 보상을 주는 가치가 오해·낮은 신뢰 노출 위험보다 큰가?

### REC-PD-003 — 온보딩 입력 부담

생성할 자료:

- K0/K1/K3/K5/K10/K20의 사용자 정규화 NDCG, 예상 별점 MAE, coverage
- 각 K에서 개인 척도 추정이 안정된 사용자 비율
- React 온보딩 prototype의 K5/K10 완료 단계와 예상 조작 횟수
- 건너뛰기 시 popularity/content fallback 결과 예시

MovieLens에는 가입 이탈 데이터가 없으므로 실제 이탈률은 주장하지 않는다. 소유자는 품질곡선과
화면 부담을 함께 보고 최대 입력 수만 정한다.

### REC-PD-005 — 파티 공정성

생성할 자료:

- 2/3/4명, 유사/중간/상이 합성 파티의 후보 사례
- Average, Least Misery, Most Happiness, Balanced 정책별 평균·최저 효용·격차
- raw 별점 평균과 개인별 정규화 효용의 순위가 뒤집히는 사례
- 한 구성원이 매우 싫어하는 영화와 모두 무난한 영화 사이의 설명 가능한 비교표

판단 질문: 평균 효용을 얼마나 포기해 최저 구성원을 보호할 것인가? 실제 파티 만족이 아닌 합성
MovieLens 결과라는 한계를 패킷 첫 줄에 표시한다.

현재 REC-EV-005 결과에서는 Balanced의 Average 대비 평균 효용(-0.0013), 최저 효용(+0.0005),
격차(-0.0042) 차이의 paired-bootstrap 95% CI가 모두 0을 포함했다. 4인 Test 공통평가 coverage도
0.69%~1.02%에 불과하다. 따라서 비교 자료는 생성됐지만 개선·일반화 근거는 아니며 제품 정책은
계속 미승인이다.

### REC-PD-007 — 추천 이유 표시

생성할 자료:

- reason 1개, 최대 3개, 상세 펼치기의 React 화면 비교
- 실제 scoring feature에서 생성 가능한 reason coverage
- 추천 순위를 바꾼 근거와 단순 영화 설명을 구분한 faithfulness 검사
- 데이터 부족·fallback 상태에서 허위 이유가 생기지 않는 fixture

판단 질문: 화면 복잡도와 설명 충분성 사이에서 어느 수준이 적절한가?

## 3. LLM이 자동으로 만들 판단 자료

| 결과 | 산출물 | 선행 작업 |
| --- | --- | --- |
| rating-style profile | `evidence/REC-EV-001-rating-style.md` | 고정 time split |
| 예상 별점·confidence | `evidence/REC-EV-002-prediction-calibration.md` | Bias·ALS baseline |
| cold-start 곡선 | `evidence/REC-EV-003-cold-start.md` | K0~K20 simulation |
| 탐험 Pareto | `evidence/REC-EV-004-exploration-pareto.md` | Hybrid baseline |
| full-catalog 탐험 Pareto | `evidence/REC-EV-004B-full-catalog-pareto.md` | locked REC-EV-004 policies |
| 파티 정책 비교 | `evidence/REC-EV-005-party-policy.md` | 개인 relative utility |
| 추천 이유 coverage | `evidence/REC-EV-006-reason-faithfulness.md` | structured reason generator |
| Fold-in 성능 | `evidence/REC-EV-007-fold-in-benchmark.md` | FastAPI serving scaffold |
| UI 비교 | `evidence/REC-EV-008-ui-comparison.md` | React mock 연결 |

각 문서에는 실행 명령, code/data/protocol version, 전체 결과, 사용자 구간별 결과, 실패 사례,
한계, 권장안, rollback 비용을 포함한다. 결과 artifact가 없으면 `NOT_RUN`으로 표시한다.

## 4. 승인 시점

1. E0/E1에서 rating profile과 기준선을 만든다.
2. E2에서 예상 별점·cold-start 패킷을 만든다.
3. E3에서 탐험 Pareto를 만든다.
4. E4에서 파티 비교를 만든다.
5. React mock에서 표시안 비교를 만든다.
6. 필요한 패킷이 모두 `READY_FOR_DECISION`일 때만 소유자에게 한 번에 제시한다.

Test 결과를 본 뒤 기준을 고치는 것을 막기 위해 손실 예산은 Validation 패킷으로 승인하고 잠근다.
