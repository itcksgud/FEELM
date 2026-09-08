# REC034 — 같은 ALS 순위에서 두8맛 안의 슬롯 공급 진단

상태: DRAFT. 실데이터 실행 전 독립 검토가 필요한 연구 계약. 2026-09-08.

이 실행의 목적·타입·후보 순서는 독립 검토한 [공급 설계](../../plans/service-policy-redesign/stage-4-supply-design.md)와
[역할 제안](../../plans/service-policy-redesign/stage-4-policy-proposal.md)을 따른다. 이 두 문서도 실행 fingerprint에 포함한다.
후보 공급의 기술 진단1회이며 추천 품질 실험·최종 분류 선택·서비스 K 결정이 아니다.

## 고정 범위

REC032 R0의2,180명, 관측 원별점30개, 같은 ALS factor32차원·reg0.1을 재사용한다.
REC033 A/B의 같은85,517편 배정과 TMDB 구조 장르/감독/키워드 ID를 사용한다.
config는 모든 입력 bytes/hash와 상위 completion/prepare/train 봉인을 고정한다.
REC033 독립 결과 검산 PASS가 있어야 시작한다. 새 학습·임베딩·캐시/네트워크 조회는 없다.

원천 hash·상위 봉인 확인 뒤, 자료 역할과 요청 크기를 먼저 봉인하고 나서 입력 profile를 해석한다.
prepared.npz에서는 item_ids와 학습 prior만 디코딩한다. training_user_ids·counts·평가 H는 입력하지 않는다.
profiles에서는 고유 user_key와30개 movie ID/평점 index만 읽는다. E·정답값·평가 histogram·raw archive를 열지 않는다.
이미 평가한 개발 사용자이며 새 독립 확인 집합이 아니다. 출력 user_key/영화 목록은 로컬 감사용이고 보고는 집계만 쓴다.

## 계산과 경계

별점은 index0~9를0.5~5.0으로 정확히 변환한다. factor가 있는 관측 영화만 raw fold-in에 사용한다.
`solve(Y_O.T @ Y_O + 0.1*n_factor*I, Y_O.T @ ratings)` 후 같은 전체 영화 행렬과 내적한다.
float64, BLAS thread1, 동점 movie_id 오름차순이다. 비유한 factor는 원천 실패다.
관측 factor0·solve실패·비유한 user vector/점수·norm≤1e-12는 사용자 비활성 사유로 남기고 분모를 유지한다.
비활성일 때 P0나 다른 모델로 대체하지 않는다. 비활성 사례가 있으면 기존 ALS exact-score hash 대조는
해당 여부를 기록하고 건너뛰며, 정상 사용자만 별도 검산한다. 비활성0이면 전체 점수 hash를 기존 ALS-only n30과 대조한다.

맛별 상대 weight는 검토된 feelm_policy_states.relative_weights와 같은 관측30개·학습 prior를 사용한다.
A/B 각각 맛별 평균>0이 긍정 맛이고, 그 안의 weight>0 입력 영화만 anchor다. 관측 입력의 맛은 경험 proxy에 포함한다.
현재 user vector나 평가값으로 영화 맛 배정을 바꾸지 않는다. A미배정 입력은 누락 수를 남기고 고유 입력30 분모를 유지한다.

T/D mask를 전체 카탈로그에 계산한다. 연결은 같은 종류의 장르·감독·키워드 ID 교집합이며 한 사실 이상이다.
배우·인기도·임베딩 유사도는 연결 조건에 추가하지 않는다. D는 긍정/관측경험 맛이 아니어야 한다.
연결 사실은 채택된 D의 anchor movie ID와 종류/ID까지 저장한다. 사실 검증이지 사용자 납득 점수가 아니다.

원래 순위에서 제외 전 ALS500 →그중 T/D 적격만 →상위100 →관측 입력30개 제외 →T2/D1을 계산한다.
타입 없는 후보를 T로 보충하지 않는다. T2 부족은 실패, D없음이면 T3 가능 여부를 별도로 기록한다.
같은 규칙을 전체 ALS 지원 목록과500 적격 목록에도 적용해 후보 절단의 상한을 비교한다.
실제 서비스 후보 크기를 바꾸거나20편 버퍼/추가추천·실제 감상 제외 전체를 구현하는 실행이 아니다.

## 산출물과 종료

- role-seal.json: 입력 역할·원천·고정2180×30·평가 정답 접근 없음.
- ranking-prefixes.npz: 제외 전 raw ALS500의 movie IDs와 점수. 부족/비활성은-1/NaN padding과 길이로 구분.
- user-supply.parquet: 사용자×A/B. 입력/긍정/경험proxy/anchor/미배정 수, 각 단계 T/D 공급, 최초 슬롯 상태와 손실 원인.
- slots.parquet: 사용자×A/B×전체/500/100의 슬롯 결과와 D 연결 근거. 미채점이며 선호 정답을 부착하지 않는다.
- summary.json: 전체2,180명 분모의 공급 상태·원인·A/B 짝 비교. 발견 성공률·품질 우승·K 선택 필드는 false.
- score-seal.json, budget.json, completion-seal.json: 정확한 필수 출력·상위 의존성과 실행 자원 기록.

30분·프로세스 트리12GiB를 원천 확인부터 완료 봉인 전까지 감시한다. 실패/부분 산출물은 보존하고 자동 재실행하지 않는다.
완료본은 고정 원천·필수출력·상위 봉인·review를 검증하고 scoring 없이 재사용한다.
반복 n·seed·분류법·후보수 탐색을 하지 않는다. 해당 진단이 완료되면 남은 제품 선택·자료 공백과 다음 검증을 한 문서로 정리한다.

합성 검사는 sparse 연결 mask와 상태 모듈의 의미 일치, 같은 namespace ID, 긍정 anchor 조건,
500 전 입력 제외 금지·100 전 타입적격 처리·부족·미배정·inactive·완료 재사용 보존을 확인한다.
실행 후 독립 검산은 재학습 없이 저장된500 순위·full fold-in 대조, 사용자별 공급/슬롯/집계와 hash를 확인한다.
실행 명령은 [runbook](../../../runbook/local-development.md)의 REC034 절에 기록한다.
