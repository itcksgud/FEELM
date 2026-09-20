# S15P21E106-622 GBT 0~N 최종 실험 결과

실행·재감사일: 2026-09-20

상태: **CONTROLLED_PREFIX_COMPLETE / HARD_SWITCH_REJECTED_THROUGH_K50 /
POPULARITY_80_GBT_20_OFFLINE_VALIDATED_CANDIDATE /
ACTUAL_LOW_HISTORY_NOT_EVALUATED / DO_NOT_PROMOTE / FINAL_TEST 미개봉**

## 결론

1. GBT는 K=0, 1, 2, 5, 10, 20, 40, 50의 입력을 처리한다. 세 seed에서 평점 예측 오차는
   K=1부터 K=0보다 개선됐다.
2. 평점 예측 개선이 추천 순위 개선을 뜻하지는 않았다. 순수 GBT로 바꾸는 hard switch는
   세 seed 모두 K=5, 10, 20, 40, 50에서 실패했다. 채택 가능한 전환 임계값 K*는 없다.
3. 인기도 80% + GBT 20% percentile 혼합은 분리된 CONFIRM 사용자 500명에서 세 seed와 모든
   K의 사전 조건을 통과했다. 이는 same-pool rerank 후보 근거이지 운영 승격 근거가 아니다.
4. MovieLens 32M은 사용자당 최소 20개 평가를 요구한다. 이번 controlled prefix는 정보량 K의
   효과를 측정하지만, 전체 이력이 K개뿐인 실제 신규 사용자 모집단은 평가하지 못했다.
5. 실제 후보 생성, 서비스 사용자 분포, 온라인 품질은 검증하지 않았고 FINAL_TEST도 열지 않았다.

따라서 현재 증거가 지지하는 정책은 “K 이상이면 GBT 단독으로 전환”이 아니라 “모든 테스트 K에서
인기도를 주 신호로 두고 GBT를 20%만 섞는 후보”다.

## 과도한 데이터 통제에 대한 정정

이전 문서의 “저이력 사용자 부족은 고정 cutoff가 만든 왜곡일 뿐”이라는 표현은 과했다. 다음 두
사실을 분리해야 한다.

- MovieLens의 생애 총 이력은 모든 사용자에게 최소 20개이므로 실제 total-history K<20 모집단이 없다.
- 고정 cutoff 이전 이력은 0~19개가 될 수 있지만, 이 코호트는 K뿐 아니라 가입시점과 시간대 효과도
  함께 바뀐다.

따라서 고정 cutoff만으로 K 정책을 정하면 과도한 통제와 코호트 혼동이 생긴다. 반대로 성숙 사용자의
과거를 잘라 만든 controlled prefix도 실제 신규 사용자를 대신하지 않는다. 이번 v16은 같은 사용자,
같은 미래 판단, 같은 후보에서 제공 정보량만 K로 바꾸어 **K 정보 곡선**을 추정한다. 실제 저이력
정책 검증 상태는 명시적으로 `NOT_EVALUATED_CONTROLLED_PREFIX`다.

또한 v12 재감사에서 anchor 연도만으로 target 적격성을 판정해, 연도 경계에서 `prediction_at`보다
나중에 개봉한 영화가 포함될 수 있는 문제를 발견했다. v16은 `prediction_at`의 UTC 연도를 직접
사용하고, 모든 history·target·후보의 개봉 연도가 그 연도 이하인지 전수 검사한다.

## v16 설계와 표본

| 항목 | 값 |
| --- | --- |
| TRAIN 사용자 | 5,000 |
| VALIDATION 사용자 | 1,000; v11 사용자 1,000명과 disjoint |
| 정책 확인 | SELECTION 500명 / CONFIRM 500명 |
| exact K | 0, 1, 2, 5, 10, 20, 40, 50 |
| 사용자당 미래 판단 | 10개 |
| TRAIN target 행 | 400,000 |
| VALIDATION target 행 | 80,000 |
| 입력 episode 행 | 480,000 |
| 입력 candidate 행 | 3,998,090 |
| 예측 candidate 행 | seed별 5,318,240 |
| 모델 seed | 622, 1622, 2622 |
| FINAL_TEST | 0명, `PHYSICALLY_OMITTED` |

`total_history_count`는 anchor 앞의 숨긴 과거까지 포함하고, `provided_history_count`와
`supported_history_count`만 exact K다. controlled prefix는 K=50에서도 원래 과거가 더 많으면
`is_full_history=false`다. 모델 feature는 제공된 K개만 사용한다.

popularity는 선택된 TRAIN 사용자의 1,216,454개 평가를 episode별 `prediction_at` 직전까지
누적해 계산했다. validation의 미래 label은 popularity feature나 기준선 계산에 사용하지 않았다.

## GBT 예측과 추천 순위의 차이

세 seed 모두 K=1 이상에서 K=0 대비 user-macro MSE가 개선됐다. K=1의 개선 폭은
-0.03197~-0.04674, 가장 큰 개선은 seed별 -0.13187~-0.13890 범위였다. 이는 입력 신호를 모델이
활용한다는 근거다.

하지만 추천에서는 후보의 상대 순위가 중요하다. 순수 GBT로 바꾸는 구간만 떼어 순수 popularity와
paired bootstrap한 결과는 다음과 같다. 표는 세 seed의 점추정 범위다.

| GBT 적용 시작 K | Recall@10 delta 범위 | NDCG@10 delta 범위 | 판정 |
| ---: | ---: | ---: | --- |
| 5 | -0.32405 ~ -0.22949 | -0.23183 ~ -0.16627 | REJECT |
| 10 | -0.33270 ~ -0.23425 | -0.23806 ~ -0.16765 | REJECT |
| 20 | -0.33878 ~ -0.23360 | -0.24355 ~ -0.16655 | REJECT |
| 40 | -0.34404 ~ -0.23724 | -0.24879 ~ -0.16988 | REJECT |
| 50 | -0.34762 ~ -0.23434 | -0.24966 ~ -0.17181 | REJECT |

모든 95% 신뢰구간 상한이 0 아래였다. 따라서
`HARD_SWITCH_THRESHOLD=null`, `HARD_SWITCH_STATUS=REJECTED_THROUGH_K50`이다.

## 80:20 혼합 정책의 독립 확인

점수는 episode 내부 percentile로 보정한 뒤
`0.80 * POPULAR_COUNT + 0.20 * GBT`로 합쳤다. alpha=0.20은 CONFIRM 코호트를 열기 전에
고정했고 이후 조정하지 않았다. 아래 값은 CONFIRM에서 alpha=0.20과 순수 popularity의
user-paired delta가 세 seed에서 보인 범위다.

| K | Recall@10 delta 범위 | NDCG@10 delta 범위 | 저평점 노출 delta 범위 | 판정 |
| ---: | ---: | ---: | ---: | --- |
| 0 | +0.03063 ~ +0.04846 | +0.03677 ~ +0.05399 | -0.02080 ~ -0.01536 | PASS |
| 1 | +0.03195 ~ +0.04101 | +0.04073 ~ +0.04762 | -0.01818 ~ -0.01572 | PASS |
| 2 | +0.03119 ~ +0.03951 | +0.03817 ~ +0.04631 | -0.01947 ~ -0.01322 | PASS |
| 5 | +0.02409 ~ +0.03422 | +0.03522 ~ +0.04444 | -0.02034 ~ -0.01217 | PASS |
| 10 | +0.02404 ~ +0.03113 | +0.03900 ~ +0.04060 | -0.01725 ~ -0.01635 | PASS |
| 20 | +0.02371 ~ +0.03615 | +0.03715 ~ +0.04538 | -0.01775 ~ -0.01539 | PASS |
| 40 | +0.03136 ~ +0.03456 | +0.04137 ~ +0.04341 | -0.01750 ~ -0.01563 | PASS |
| 50 | +0.02832 ~ +0.03976 | +0.04411 ~ +0.04560 | -0.01891 ~ -0.01611 | PASS |

24개 seed×K 셀 전체가 Recall과 NDCG의 -0.02 비열등 조건을 통과했고 저평점 노출 점추정이
감소했다. 가장 보수적인 95% CI 하한도 Recall +0.01040, NDCG +0.02452였고, 저평점 노출
CI의 가장 큰 상한도 -0.00549로 0 아래였다.

판정은 `POPULARITY_80_GBT_20=OFFLINE_VALIDATED_CANDIDATE`다. K=0까지 통과했다는 사실은
0개 평가로 개인화가 가능하다는 뜻이 아니다. 해당 셀에서는 GBT의 비개인화 영화·인기도 feature가
보조 신호로 작동했을 뿐이다.

## 재현성과 전수 검증

- input manifest SHA-256:
  `dc176a1496678e2240c3d13d00429dfca397f3fa78300f0a5c0c6e0b0a4ff8ba`
- prepared manifest SHA-256:
  `05972f5479c4af3c7e2eed6b685934fe1af115396021ede0440dc347317664ca`
- runtime source bundle digest:
  `69865139a221d35cf90b551924fb041b6c025b34aaa974d7f000f4ff7ff09418`
- 세 seed candidate identity digest:
  `9b991a8c31c55bdd23caa25b7aa42cd089570012653b74ed34f269d340113ace`
- MovieLens `ratings.csv` SHA-256:
  `91159850e41ee59c86231165a688709647e2726cab2e7ba9faf04001bd5261ee`
- MovieLens `movies.csv` SHA-256:
  `b37ca1abc7798de741138ed252b62f69f7e37c84b8a8fab1b82d409b4c6c5cc2`

세 seed 모두 전수 verifier `PASS`다. verifier는 exact K, nested recent suffix, 제공/전체 이력
분리, history/target 시간 순서, 개봉 연도 적격성, K별 동일 후보, SELECTION/CONFIRM 분리,
이전 validation 사용자 제외, UNKNOWN 가중치, source/config/schema 해시와 FINAL_TEST 미개봉을
검사했다. 각 결과 보고서의 SHA-256은 [policy-v16-confirmation.json](policy-v16-confirmation.json)에
고정한다.

## 한계와 최종 판정

- 실제 total-history K<20 사용자 모집단은 평가하지 않았다.
- target을 sampled candidate pool에 강제로 넣어 후보 생성 Recall을 평가하지 않았다.
- `UNKNOWN_SAMPLED`는 미평가이며 지표는 관측된 판단에 한정한다.
- MovieLens와 FEELM의 콘텐츠·사용자 분포가 다르다.
- 실제 서비스 A/B, shadow, onboarding replay를 수행하지 않았다.

따라서 Jira 622의 기술·offline 정책 실험은 완료할 수 있지만 운영 승격은 하지 않는다. 후속 검증은
실제 후보 생성기에서 target 강제 삽입 없이 Recall@M과 최종 Top-10을 연결해 측정하고, 실제
온보딩 또는 서비스 replay의 저이력 코호트에서 80:20 혼합을 확인해야 한다. 그 전까지 정책은
`CANDIDATE`, 실제 저이력 근거는 `NOT_EVALUATED`, 운영 판정은 `DO_NOT_PROMOTE`다.
