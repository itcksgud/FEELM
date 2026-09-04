# REC-EV-019D 동일 미래 구간 prefix ablation 사전등록

> 상태: `APPROVED_FOR_BOUNDED_REAL_VALIDATION`
> 실행 역할: `VALIDATION_019D`
> 결과 확인 전 고정 계약: `docs/recommendation/contracts/rec-ev-019d-prefix-ablation-artifacts.json`
> `locked_test_used=false`, `champion=null`, `product_policy_updated=false`

## 질문과 집단

REC-EV-019C의 K5와 K10은 적격 사용자와 미래 구간이 달라 직접 비교할 수 없었다. 019D는 기존 K10
strict Validation 사용자 1,479명을 고정하고, 각 사용자의 K10 prefix에서 앞 5개와 앞 10개만 파생한다.
두 arm은 동일한 K10 evaluation window 10행을 사용한다. 입력 순서는 사용자별 `input_rank=1..10`,
`source_position`의 엄격 증가, timestamp 비감소, first5가 first10의 정확한 prefix임을 직접 검증한다.

기존 K5·K10 tuning panel 사용자 키의 합집합을 K10 cohort와 교차한 426명은 confirmatory 집합에서
제외한다. 남은 1,053명에서는 tuning, threshold, 모델, seed를 바꾸지 않는다.

## 고정 모델과 두 estimand

두 arm 모두 B8 LightFM T003, seed 17과 B0 Bayesian rating T003 fallback, 후보 41,625편을 사용한다.
019C의 hash-verified T003 seed-17 item representation을 공통 사용한다. 캐시가 무결하면 `--resume`로
재사용하고, 없거나 무결성 검증에 실패한 경우에만 같은 고정 config로 한 번 base fit한 뒤 양 arm에
공유한다. 현재 캐시와 원자료는 ignore된 로컬 artifact이며 외부 URI가 없어 commit만 받은 제3자는
재현할 수 없다. 정확한 경로·크기·SHA-256·config는 계약과 실행 전 source manifest에 잠근다.

primary estimand는 두 arm 모두 K10 prefix의 candidate-valid 10편 전체를 추천 후보에서 제외하고 fold-in
profile만 first5와 first10으로 바꾸는 profile information ablation이다. 후보 제외에 K10 정보를 쓰는 통제
실험이므로 end-to-end K5 serving 효과로 해석하지 않는다. secondary estimand는 arm별 first5/first10 seen
mask를 쓰는 end-to-end 진단이다.

## 지표와 사전 판정

confirmatory primary는 사용자별 paired `NDCG@10(K10-K5)`다. Recall@10, MRR@10,
candidate recall@500, Harm@2, fallback 전이와 적용 strata는 보조 지표다. 사용자 단위 percentile
bootstrap 10,000회, seed `20260924`를 사용한다. NDCG는 two-sided 95% 구간, Harm@2 증가는
one-sided 95% upper를 계산한다.

판정 우선순위는 다음과 같다.

1. Harm@2 증가 upper가 `0.005`를 넘으면 `FAIL`.
2. NDCG 95% CI upper가 0보다 작으면 `FAIL`.
3. 평균 NDCG delta가 `0.005` 이상이고 NDCG lower가 0보다 크며 Harm upper가 `0.005` 이하이면 `PASS`.
4. 그 밖은 `INCONCLUSIVE`.

계약 hash, 입력 경로·크기·hash, cohort·제외 규칙, arm·mask, 모델·seed, 지표·bootstrap·판정을
`protocol-lock.json`에 기록한 뒤에만 future label을 결합하고 score를 분석한다. 실행 후 계약 변경은
허용하지 않는다.

## 019C prediction을 재사용하지 않는 이유

019C K5 prediction에는 K10 cohort 사용자 226명이 없고, K5와 K10의 미래 창은 전원 다르다. seen mask와
선택 과정도 019D와 다르며 저장 예측은 Top-500뿐이라 새 full-catalog mask 아래의 순위를 복원할 수 없다.
따라서 019D는 고정 base representation만 재사용하고 예측은 새로 계산한다.

## 해석 경계

이 실험은 MovieLens Validation 대리 환경의 prefix 정보량 비교다. 한국 사용자 만족, 한국 영화 문제,
최신 영화 품질, 제품 champion이나 onboarding 정책을 검증하거나 변경하지 않는다.
