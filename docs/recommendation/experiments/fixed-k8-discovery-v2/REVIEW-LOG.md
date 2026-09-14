# Discovery v2 검토·교정 기록

상태: DRAFT — 실행 진행 중. 연구 완료/운영 채택 기록이 아니다.

원본 fixed-k8-discovery-20260912의 코드·모델·결과·봉인과 상위 8맛은 읽기 전용이다. 이번 작업은 별도 `research/fixed-k8-discovery-v2-20260913` worktree, 모델 구성 요소는 별도 `research/discovery-v2-predictor-20260913` worktree에서 작성했다. 기존 다른 변경은 보존했다.

## 실행 전 설계·코드 검토

Copernicus/design_review의 독립 검토 지적을 실행 전에 반영했다.

- DIRECT-PREFIX의 짧은 목록은 reference 실제 길이를 overlap 분모로 쓰고, 후보 공급/B와 분리했다. 빈 비교는 오차0으로 만들지 않는다.
- 예측기 보정 이후 저지원 노출은 분기 이름이 아니라 원래 ALS지원 여부와 영화 TRAIN 평가 수로 고정한다.
- 예측기 MSE는 사용자별 평균을 동일 가중한 macro가 기준이고, low-support 최소30행/10사용자를 요구한다. 미충족은 UNDERDETERMINED다.
- 정책별/Pareto/per-K 선택 순서, 동일조건 대조, multi4 질량/표본 hash와 작은 그룹 진단을 고정했다.
- 원본 입력의 서비스 매핑 실패가 사용자 평균에 영향을 주지 않도록 원본 별점/입력 개수와 mapping 후 벡터 입력을 분리했다.
- 원본 adult/video는 명시적 false만 후보로 쓰고, R/v의0·누락·null·모순을 분리했다. 실제 원본에서는 숫자 누락/null이 없지만 이를 미리 가정하지 않았다.
- 같은 사용자 안의 viewed 중복은 거부한다. E_u의 유효 대표만 실제 내적하고, DIRECT-PREFIX 평균/상위5 진단은 같은 내적 배열을 재사용한다.
- G/GKT 차원이 다른 반례를 넣고 장르 전용 대표와 프로필의 공간을 명시했다. 행렬 hash는 동결 원본 행렬의 계보다.

사용자가 실행 도중 추가로 강한 평가 수 보정을 제안했다. 새 수치 실행 전에 기본 m을300, 비교를100/300/1000으로 정했고, 예측기 RH 내부의 기존 m48은 보존했다. Q는 수축 점수이며 신뢰하한이나 개인 예상 별점이 아니다.

## r1 준비 결과와 역할 교정

r1은 prepare만 실행했다. 59.04초, peak RSS 약2.03GB, 상위237,817개 할당 전부 일치. 새 클러스터 학습·모델 추론·목표 기반 선택은 수행하지 않았다. 원본상 valid R/v121,656편, 현재 자격118,790편, C=6.314301027022476, m300.

부모 작업의 독립 역할 감사에서 v1 check180명 중57명이 재사용할 hybrid345 보정90명에 포함됨을 발견했다. 기존 코드 결과를 덮어쓰지 않고 r1 결과/봉인 및 `outputs/.../feelm-discovery-v2-r1/reviewed-contracts/prepare`의 정확한 코드·설정·문서 snapshot을 보존했다.

r2는 원본 final344의 calibration90을 selection, comparison180을 check로 사용한다. check와 기존 calibration의 교집합은0이다. 두 집단 모두 반복 사용한 개발 자료이며 새 최종 test가 아니다. selection90의 목표 별점은 기존 보정계수 추정에 사용되었다. 새 보정계수 학습은 하지 않는다. 역할 결함 교정으로 최대2회 중1회의 correction run을 사용했다. Q/검색/근사/품질 기준은 변경하지 않았다.

prepare 사전 검토 PASS fingerprint는 `prepare-execution-review.json`, cluster는 `cluster-execution-review.json`에 있다. 각 실행 버전의 `reviewed-contracts`에 당시 파일을 보존한다.

## 실제 입력·분류 감사

- Copernicus가 r1 전체 service/TMDb축, 원본 genre의 binary/정규화, 독립 거리식에 의한 top8, Q 전행과 정확한 정렬, 1,350 context 및 source pins를 검산했다.
- Pasteur/asset_audit가 원본237,817 details와237,803 keywords 파일을 전수 대조했다. metadata 불일치0, ID 연결/고정 top8 일치, service 전체 인덱스 RH230 독립 피처 비교 최대오차5.96e-8.
- 초기 감사 harness가 overview의 기존 strip 규칙을 누락해4행 차이를 만들었다. 기대식을 교정하고 독립 검토 후 재실행했으며, 실제 prepare 구현의 결함은 아니었다.
- r2의 catalog/content/genre/binary/top/base/Q/order/Q설정/Q민감도/contexts 11개 파일은 검산된 r1과 bytes/SHA가 동일하다. r2 역할은 final344 원본과 정확히 일치한다.
- 감사 때 contexts JSON 전체를 파싱하므로 메모리에는 미래 target별점도 들어온다. 이를 개별 검토·오차/순위 계산·선정에 사용하지 않았다는 뜻이며, '미래 별점 파일을 전혀 읽지 않았다'고 표현하지 않는다.

r1 prepare seal: `0d18af14e2f8d575427dc5f80e535591874acb731a8c9f4da3662682a12a92c0`.
r2 prepare seal: `42de5a1343e33fb6ce600d567248def48f6eb9a9c95dddfeb41d91cf408e3c76`.
r2 roles: `2aeba560539100fcf65ac95675784ffe8e366da32c54df2b6ce461414f03ad87`.

## Predictor 구성 요소

Pasteur가 ServicePredictor를 별도 worktree에 작성하고 그 아래 별도 검토자가 코드·합성51검사 및 재현11테스트를 검토했다. exact service→ML축, cap/seen/invalid 조건, 실제 후보별 필요한 head 연산, unclipped affine 순위와 clipped 별점오차를 분리했다.

동일한 원본 metadata/기존 피처109후보의 RH230 25,070개 값은 bit-exact였다. 이는 피처 parity이며, 실제 모델 예측 parity는 별도 bounded 실행·검토 결과를 요구한다. main predictor 선택 stage는 해당 PASS와 정확한 입력/코드 핀이 없으면 실행할 수 없다.

현재 새 최종 추천/수치/운영 채택 결론은 아직 작성 중이다. 이후 검토 지적, 실제 실행과 결과 감사는 이 문서에 추가한다.

## r2 실제 군집과 예측기 감사

전체 카탈로그에서6개 K ×8개 맛의48회 fit을 완료했다. 총 소요179.81초이며 그룹 수16·64·256·512·1,024·2,048을 모두 보존했다. 요청한 K를 줄인 parent는 없고 빈 회원 군집도 없다. 후보 자격을 갖춘 영화가0인 그룹은 K64에서1개, K128에서1개, K256에서9개이며 빈 군집과 구분한다. Copernicus의 독립 전수 분류/대표 검산에서 불일치0, inertia 최대차3.38e-9였다. 최상위237,817개 taste ID는 원본 그대로다.

예측기 실제 parity는324개 context/candidate 쌍 ×4 variant =1,296행을 검사했다. 원본 raw GBT 오차0, ALS/최종값 최대8.88e-16, 피처 최대1.192e-7이었다. 새 서비스 ID는 같은 metadata에 대해 동일값이며 미매핑 ID는 GBT fallback이다. 실제 head 호출 수도 일치했다. `predictor-parity-review.json`의 exact PASS를 통과한 뒤만 선택 단계가 실행됐다.

선택 단계는25.78초, 실제벡터 사용자62명, variant당 관측 평가1,829행을 사용했다. original과 세 대안은 동일 후보를 예측했다. 사전 순서의 첫 통과인 shrink100을 고정했으며 대안 중 최소MSE를 골랐다는 뜻이 아니다. 사용자 동일가중 macroMSE는0.60154838→0.59581076, 저지원 macroMSE는0.64104797→0.62707174였다. 반면 전체 평가 행 microMSE는0.61870945→0.62152071로0.454% 증가했고 저지원 microMSE도1.033% 증가했다. 원래 ALS가능·TRAIN<=20 영화의 Top1은35→20건으로42.86% 감소했다. GBT fallback을 포함한 모든 ML저지원 Top1이 없어졌다는 주장은 하지 않는다.

Pasteur가 observed7,316행/요청248건의 truth 연결, 오차, 동일후보, Top10순위, 실제 head 수 및 고정 저지원 cohort를 독립 재검산했다. 별도 원본/native 검산은3명/후보변형1,100행+관측변형104행에서 최대1.78e-15/4.44e-16였다. 원본 TRAIN4,997,069행의 최대 시각은1672530707로 원점1672531200보다 앞서고, TRAIN 사용자와 평가 사용자는 겹치지 않는다. metadata에서 service 축으로 다시 합한 TRAIN지원 수4,992,475와 원본 전체 TRAIN행 수는 매핑 범위가 달라 구분한다.

`dataset()`은 전체 context JSON을 파싱하므로 check 목표 배열도 메모리에 들어온다. 선택에 쓰인 predictor 입력/오차/선정 행에서 check 사용자는0이며, 모델 입력은 cap/history/stars/viewed뿐이다. 이 정확한 범위를 `predictor-stage-result-review.json`과 `predictor-source-audit-reproduction.json`에 남겼다.

## r2 추천 sweep 및 final 사전 검토

168개 주 비교와3개 고정 대조를90명에 실행한 sweep은338.14초에 완료했다. 정확한 실행 버전/자원/후보/실제 예측값은 r2 봉인에 있다. 결과 기준을 바꾸지 않고 사전 Pareto/비용 우선순위로 선택했다. 실제 gate/후보/결과 주장의 독립 감사와180명 check는 이어서 진행한다.

동결 runtime/final의 사전 독립 검토에서 전체 policy/predictor/date/runtime/tie/source까지 규칙 digest에 포함하고, 실제 비교 가능한3명 이상의 serving parity를 요구하도록 보완했다. 그보다 적으면 INSUFFICIENT를 저장한다. top8 이름은 원본 prepare seal에 있는 exact source로 검증한다. 별도 runtime7개 테스트와 anchor/tombstone/head-count 합성 반례가 통과했다. 이는 사전 코드 PASS이며 실제 final 결과 PASS와 구분한다.
