# ALS 결합 비교군

상태: **APPROVED — 사용자 요청에 따른 로컬 비교 명세 추가** · 2026-09-13.
비교 registry를 준비한 단계다. 새 모델 학습·결합 점수 계산·서비스 연결은 실행하지 않았다.
기본 노출은 GBT, 맞춤2+발견1·발견10×10·배치500→재정렬 정책을 유지한다.

## 1. 무엇을 비교하는가

**단독 GBT·FM·ALS에 ALS+GBT와 ALS+FM을 추가한다.** 각 결합에서 단순 전환과 실제 점수
혼합을 구분한다. 학습 모델은3개이며, 그 점수로 만드는 비교 경로가 총7개다.

| 비교 ID | 계산 | 묻는 질문 |
| --- | --- | --- |
| GBT | 보정 GBT | 현재 기본 모델 |
| FM | 보정 FM | 콘텐츠·사용자 반응 기반 단독 대안 |
| ALS | 보정 ALS, 미지원은null | 협업 신호만의 기준 |
| ALS_GBT_ROUTER | ALS가 계산되면 ALS, 아니면 GBT | 지원 구간을 ALS로 바꾸는 효과 |
| ALS_GBT_SHRINK100 | 양쪽 점수를 영화의 ALS 학습 근거량에 따라 혼합 | GBT에 ALS를 얼마나 보완할 수 있는가 |
| ALS_FM_ROUTER | ALS가 계산되면 ALS, 아니면 FM | FM 기준에서도 같은 전환이 유리한가 |
| ALS_FM_SHRINK100 | 같은 혼합 규칙에 FM을 사용 | ALS 보완 효과가 GBT/FM에 따라 달라지는가 |

실행 ID와 설정은 [comparison-registry.v1.json](comparison-registry.v1.json)에 둔다.
`config.v1.json.comparisonModels`는 단독 학습 모델 목록이며, 결합 정책은 위 registry의
`compositePolicies`를 추가로 읽는다. 기본 응답 모델을 registry의 최상위 성능으로 자동 바꾸지 않는다.

과거 ALS+GBT router와 shrink100의 **식을 재사용**한다. 새245열·보정45명·현재 후보에서의
성능까지 재현됐다는 뜻은 아니다. ALS+FM의 두 규칙은 이번에 대칭적으로 추가한 비교 명세다.
과거 rec047의 validation 선정 혼합비0.25를 새 모델의 최적 혼합비로 승계하지 않는다.

## 2. 전환과 혼합의 정확한 계산

다음에서 B는 GBT 또는 FM이다. a/b는 [MODELS](MODELS.md)의 동일 보정45명 역할에서
각 모델을 보정한 **clipping 전 calibratedScore**다. 합친 점수에 두 번째 affine을 맞추지 않는다.

```text
ROUTER, ALS 정상 지원: score = a
ROUTER, ALS 정상 미지원이고 B 지원: score = b

SHRINK100, 두 모델 모두 정상 지원:
  n = 해당 영화가 ALS factor 학습에 실제 사용된 MovieLens TRAIN 평가 수
  w = n/(n+100)
  score = w*a + (1-w)*b
```

n은 사용자 입력 K/H, TMDB 투표 수, KOBIS 관객 수가 아니다. 평가/확인 사용자의 평점을
n에 더하지 않는다. 해당 ALS factor와 동일 학습 버전의 영화별 count를 필수 산출물로 묶는다.
factor가 있는데 n이 누락·0·음수·비정수이면 COUNT_LINEAGE_MISMATCH이며 다른 출처 숫자로 채우지 않는다.
bool·NaN·±∞도 유효한 count가 아니다. n은 검증된 양의 정수여야 한다.
tau=100은 과거 비교에서 가져온 고정값이지 새 서비스나 FM에 최적인 값이라는 결론이 아니다.
이번 확장은 가중치 탐색·추가 학습 횟수를 늘리지 않는다.

| 행 상태 | 두 정책의 처리 | 로그 |
| --- | --- | --- |
| ALS 미지원, B 지원 | b를 그대로 복사 | branch=OTHER_ONLY, effectiveAlsWeight=0 |
| ALS 지원, B 미지원 | a를 그대로 복사 | branch=ALS_ONLY, effectiveAlsWeight=1 |
| 둘 다 미지원 | supported=false, score=null | branch=UNSUPPORTED |
| 둘 다 지원, ROUTER | a | branch=ROUTER_ALS, effectiveAlsWeight=1 |
| 둘 다 지원, SHRINK100 | 위 가중 평균 | branch=MIXED, effectiveAlsWeight=w |
| 모델/보정 파일 미준비·실행 오류·비유한 출력 | NOT_READY 또는 ERROR; 비교 실패로 집계 | 부분 결과를 성공 비교로 위장하지 않음 |

‘정상 미지원’은 NO_ITEM_FACTOR, NO_SUPPORTED_HISTORY, MODEL_INPUT_UNAVAILABLE처럼
데이터 지원 조건의 실패다. 모델 장애와 구분한다. 구조적 한쪽 미지원에서만 다른 점수를 쓴다.
0×NaN을 계산하지 않고 분기를 먼저 처리한다. 두 component가 모두 READY여야 비교 정책을
준비 완료로 등록한다. shadow 실패는 GBT 기본 응답을 실패시키지 않는다.

정렬은 합친 연속 score DESC/serviceMovieId ASC다. 예상 별점·MSE/MAE는 **합친 다음**
[0.5,5]로 clipping한다. 실제 별점으로 저장하거나0.5 단위로 반올림하지 않는다.

예: a=4.4,b=3.4,n=100이면 ROUTER=4.4, SHRINK100=3.9다.
ALS가 미지원이면 둘 다3.4다. 따라서 ALS가 모르는 영화의 오차 개선을 ‘ALS 결합 효과’라고
설명할 수 없다. 그 영화의 점수는 B가 만든 것이다.

## 3. 같은 조건에서 원인을 분리한다

모든 경로가 같은 원본 H10·snapshot·component 버전·보정 계수·candidate ID 집합을 쓴다.
GBT와 결합의 GBT component, FM과 결합의 FM component는 **같은 파일과 같은 점수**다.
legacy RH230와 새 KOBIS245를 한 주 비교 표에서 섞지 않는다. ALS 학습 영화 factor 지원과
현재 요청에서 ALS가 실제 계산되는지는 별도 boolean으로 기록한다.
각 대조는 해당 **두 경로의 공통 행·공통 사용자**를 쓴다. 일곱 경로 전부의 교집합만 사용해
ALS 미지원 영화를 통째로 제거하지 않는다. 비교 쌍마다 분모와 지원·오류 제외 수를 기록한다.

| 필수 대조 | 비교 구간·해석 |
| --- | --- |
| 각 결합 − 해당 B 단독 | 같은 행/사용자에서 ALS 추가 효과 |
| 각 결합 − ALS 단독 | ALS가 실제 계산되는 공통 행에서 B 추가/전환 효과 |
| SHRINK100 − 같은 B의 ROUTER | 지원 구간의 실제 점수 혼합이 단순 전환보다 나은가 |
| ALS 미지원에서 결합 − B | 같은 후보의 점수가 정확히 같아야 함; 차이는 구현/버전 불일치 |
| factor 없음 / factor 있음·입력 지원0 | 서로 다른 미지원 원인별 지원률과 B의 오차 |

먼저 `SAME_POOL`에서 GBT pool≤500·발견 후보≤100을 고정한다. ALS 미지원 영화의 개별 점수가
같아도 **전체 순위**는 ALS 지원 영화의 점수가 변하면 달라질 수 있다. 미지원 영화만의 같은
후보 재정렬은 동일하지만 전체 Top2에 들어오는 비율까지 동일하다고 주장하지 않는다.

후보 생성 효과는 별도 `END_TO_END`다. 동일 적격 전체 카탈로그를 각 비교 경로로 점수화해
자신의 pool500을 만들고 동일 재정렬·제외·2+1 조립을 적용한다. 결합을 GBT pool 안에서만
비교한 결과를 결합의 전체 후보 생성 성능이라고 쓰지 않는다. 발견의10×10 검색 후보는
같은 입력/정책에서 공통이고 최종 개인 점수만 바뀐다. 초기 K<10 경로는 공통 정책으로 표시한다.

MSE/MAE·맞춤@2/4/6/10·발견 첫1/누적·낮은 별점 비율·UNKNOWN·지원률·집중·시간은
[EVALUATION](EVALUATION.md)의 분모를 따른다. 새 결합은 기술적 비교로 등록하며,
B1/B2/B3 학습 안전 게이트나 Holm family에 몰래 추가하지 않는다. 결합 우위/노출 모델 교체는
이번 registry 등록으로 확정되지 않는다.

## 4. 실행·저장 계약

원본 사용자 입력으로 ALS/GBT/FM을 각각 한 번 계산하고, 검증한 component 결과를 네 결합에
재사용한다. 결합마다 다시 학습하거나 fold-in/모델 추론을 반복할 필요는 없다.
동일 모델·입력·snapshot의 캐시만 재사용하고 버전이 하나라도 다르면 재계산한다.

기본 추천의 ranking payload와 공개 `modelKind`/`rankingMode`는 바꾸지 않는다.
shadow 저장은 독립 비교 산출물이며 다음 필드를 가진다.

```text
comparisonRunId, comparisonRegistryVersion, comparisonPolicyId,
comparisonScope(SAME_POOL|END_TO_END), componentProfile,
userId, sessionId, rankingInputVersion, bundleId, candidateSetHash, serviceMovieId,
componentModelVersions, componentFeatureVersions, componentCalibrationHashes,
alsFactorVersion, alsTrainCountVersion, alsItemFactorPresent, alsSupportedHistoryCount,
componentSupport, componentUnsupportedReasons, componentErrors,
branch, effectiveAlsWeight, supported, calibratedScore, predictedRating, elapsedMs
```

branch별 비율과 실제 ALS 기여 가중치를 함께 요약한다. 같은 비교 ID에 다른 component를
덮어쓰지 않고 registry/version/manifest를 보존한다. 새 비교에 쓰는 KOBIS245 head는 아직
미학습이다. registry 등록 상태 CONFIGURED와 구현 READY·실행 완료를 구분한다.

## 5. 구현 검수 조건

아래는 아직 실행하지 않은 인수 조건이다. use case는 shadow/END_TO_END scorer 호출,
원본은 고정 component 결과+ALS train count, 산출물은 위 비교 행이다.

| 요구/검수 ID | 기대 결과 |
| --- | --- |
| H01 | 같은 a/b/n에서 두 계열 모두 동일 전환/혼합 수식 사용. a4.4,b3.4,n100→4.4/3.9 |
| H02 | ALS factor 없음/지원 이력0이면 B 점수 그대로. UNKNOWN/NaN과 산술 혼합하지 않음 |
| H03 | B 정상 미지원이면 ALS_ONLY, 둘 다 미지원이면 null. 오류/미준비는 별도 실패 |
| H04 | n은 factor의 TRAIN count. TMDB/KOBIS 숫자 대체와 평가 사용자 count 유입 거부 |
| H05 | a6,b4,n100→연속5,clip5. 먼저 a를5로 자른 잘못된4.5와 구별 |
| H06 | component 버전·보정·프로필/원 H/후보가 단독 대조와 정확히 일치 |
| H07 | SAME_POOL 미지원 행 score parity; 전체 순위 변화와 미지원 자체의 예측 개선 구분 |
| H08 | END_TO_END는 경로별 pool500, 발견 공통10×10·2+1·세션 중복0 유지 |
| H09 | shadow에7경로 결과/실패를 저장하되 기본 GBT 활성 포인터와 공개 응답을 변경하지 않음 |

## 기존 콘텐츠 결합과의 구분

예전 동일 가중 RRF60 ALS+콘텐츠는 두 **순위**의 역수를 더한 연구 비교다. 콘텐츠는 당시
장르/context/인물/키워드 structured 표현이며, 이번 GBT/FM 예상 별점이나 GKT131과 다르다.
RRF 수치를 예상 별점처럼 MSE에 넣지 않는다. 해당 연구는 legacy rank-only 참고로 보존하고
이번 주 비교7경로에 추가된 새 모델로 세지 않는다. 원 코드/출처 핀은 registry의 provenance에 둔다.
