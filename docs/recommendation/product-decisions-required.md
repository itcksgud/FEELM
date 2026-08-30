# 추천·예상 별점 — 프로젝트 소유자 결정 요청

> 상태: `RECORDED_LOCAL_PRODUCT_APPROVAL` — evidence와 화면 패킷을 바탕으로 보수적 local 경계를 선택했다.  
> 판단 패킷: [product-decision-packet.md](./product-decision-packet.md)  
> 판단 자료 생성 계획: `decision-evidence-plan.md`

## 1. 확정된 방향

| ID | 상태 | 결정 |
| --- | --- | --- |
| `REC-PD-006` | `APPROVED` | 별도의 추천 만족도 설문을 MVP에 추가하지 않는다. 사용자별 rating scale, 추천 노출 이후 행동, 감상, 실제 평가를 연결해 `estimatedRecommendationUtility`를 자동 산출한다. 직접 관측한 만족도가 아니므로 UI와 발표에서 `추천 만족도`라고 단정하지 않는다. |

## 2. 제품 소유자가 선택한 local 경계

| ID | 결정 내용 | 현재 상태 | 선택과 남은 경계 |
| --- | --- | --- | --- |
| `REC-PD-001` | 예상 별점 숫자 노출 | `APPROVED_LOCAL_PRODUCT_BOUNDARY` | `HIDE_NOT_COMPUTED`; C1 paired-scale 전 숫자 비활성 |
| `REC-PD-003` | 온보딩 최대 입력 부담 | `APPROVED_LOCAL_PRODUCT_BOUNDARY` | `OPTIONAL_UP_TO_10_WITH_SKIP`; `DN-C4A-004`와 동일 경계 |
| `REC-PD-005` | 파티 정책 | `APPROVED_LOCAL_PRODUCT_BOUNDARY` | `KEEP_PARTY_PUBLIC_DISABLED`; Average는 local baseline만 |
| `REC-PD-007` | 추천 이유 노출 | `APPROVED_LOCAL_PRODUCT_BOUNDARY` | `SHOW_MAX_ONE_FAITHFUL_REASON`; 실제 contribution 없는 local baseline은 숨김 |

## 3. LLM이 결과로 정하고 보고할 기술 기준

아래 값은 소유자에게 감으로 고르게 하지 않는다. LLM이 Validation 결과와 재현 가능한 보고서를
만들고, 사전에 정의한 Gate에 따라 선택한다. 제품 경험을 크게 바꾸는 경우에만 승인받는다.

- `REC-PD-002`: 예상 별점 `HIGH/MEDIUM/LOW` 경계와 낮은 신뢰 숫자 노출 조건
- `REC-PD-004`: 2+1 구조 안의 탐험 NDCG 손실 허용치와 re-ranking 가중치
- `REC-PD-008`: Fold-in p95 측정에 따른 실시간 반영 SLA
- 탐험 추천의 NDCG 손실 허용치 `epsilon_relevance`
- 파티 추천의 평균 normalized utility 손실 허용치와 최저 효용 penalty 강도
- onboarding 권장 평가 편수: K3/K5/K10 중 실제 품질·이탈 비용 절충점
- 개인 추천 cache·Fold-in freshness 목표의 최종 운영값
- 자동 추론한 추천 결과 효용의 confidence 경계와 온라인 학습 시작 조건

REC-EV-003B 이후 잠근 중간 기술 기준:

- K0~K5: 예상 별점 숫자 노출 후보에서 제외하고 fallback·추천 이유 중심으로 UI 비교한다.
- K10 이상: 예상 별점 숫자 표시 `검토 가능` 상태다. 곧바로 HIGH confidence를 뜻하지 않는다.
- K20: 별점 오차는 가장 낮았지만 K10 대비 추가 입력 10개의 UX 비용이 아직 없어 필수값으로
  채택하지 않는다.
- 개인 추천 순위: K1~K20의 sampled 평가에서 Fold-in 최적 가중치가 모두 0이므로 Popularity를
  유지한다. 콘텐츠 Hybrid·full-catalog 평가 전에는 ALS 순위 개인화 개선을 주장하지 않는다.
- REC-EV-007 local-loopback: Spring outbound timeout `750 ms`, active Rating snapshot healthy-path
  freshness `3000 ms`를 배포 전 후보로 둔다. stale success는 비활성이고 운영 topology에서 재검증한다.

## 4. LLM이 실험으로 결정할 항목

다음은 소유자에게 알고리즘 선택을 떠넘기지 않는다.

- Bias, Popularity, ItemKNN, ALS, Hybrid 중 기준선을 이기는 모델
- ALS rank·regularization·iteration과 random seed
- calibration 방식과 confidence 경계 후보
- 콘텐츠 feature·embedding·candidate 합집합·re-ranking 가중치
- split 구현, negative candidate 정책, paired bootstrap 방법
- Spark worker 수와 batch 크기

단, LLM은 결과를 `README.md`의 형식으로 기록하고 제품 손실 예산을 넘는 모델을 임의 채택할 수
없다.

## 5. 승인 요청 형식

승인 요청에는 선택지만 보내지 않는다. 다음 항목을 한 묶음으로 제공한다.

```text
결정 ID
한 문장으로 된 제품 영향
비교 화면 또는 차트
동일 조건의 수치와 사용자 구간별 결과
권장안과 반대안의 손실
불확실성·MovieLens 한계
되돌리는 비용
```

이 자료가 없으면 상태를 `WAITING_FOR_EVIDENCE`로 유지한다.
