# REC-EV-031 독립 검토와 보완 기록

상태: DRAFT. 날짜: 2026-09-07. 제품 정책 승인이 아니다.

## 설계 검토

검토자 `/root/review_rec031_design`은 대화 이력 없이(`fork_turns=none`) README.md,
config.json, evidence-audit.md와 관련 기존 계약·코드를 읽었다. raw 평점·평가 라벨을 열지 않았다.
판정은 **구조적 차단 결함 없음, runner 구현·합성 검증으로 진행 가능**이었다.
calibration/eval 분리, 관측 K개만 seen 제외, 동일 사용자/target20, 미평가 unknown,
인기도 두 기준선·500개 예산, 탐색 통계와 연구/제품 경계가 타당하다고 판단했다.

| 지적 | 처리 |
| --- | --- |
| 관측 NDCG gain과 IDCG 미정 | 반영: gain=1[q≥0.8], log2 discount, positive 개수로 IDCG, IDCG0은 null·평균 제외. |
| candidate Recall 열세를 대체 순위기 전체 기각으로 확대할 수 있음 | 반영: primary CI 상한<0이면 단독 후보 생성기만 기각. 상위3 해석은 별도 관측 노출 범위로 제한. |
| macro Recall 2%p를 pooled 50개당1편으로 해석할 위험 | 반영: 사용자 평균 Recall 2%p라는 임시 연구 비용 기준으로 수정. |

사례 영화명은 기존 archive의 movies.csv를 표시용으로 읽는다고 명시했다. 후보와 점수에는 사용하지 않는다.
제안 반영은 점수 생성·채점 전에 완료했다. 다음 검토는 구현의 실제 읽기 범위·봉인 의존성·지표·후보 보장이다.

## 구현 검토 1차: 실행 차단 후 수정

검토자 `/root/review_rec031_implementation`은 대화 이력 없이 문서·소스·합성 테스트를
검토했다. 21개 테스트와 별도 donor batch/RRF 합성 검증은 통과했으나 다음 이유로
실데이터 실행을 허가하지 않았다. 이때 실제 raw/profile/label payload는 열지 않았다.

| 지적 | 처리 |
| --- | --- |
| 자원 제한 검사가 봉인 뒤에 있어 실패한 실행의 봉인이 남을 수 있음 | 반영: 봉인 직전 검사, 기록 뒤 추가 guard 제거. 모든 완료 봉인에 시간/메모리 실패 테스트 추가. |
| 30분이 단계마다 초기화됨 | 반영: 별도 CLI에도 누적 실행 장부를 이어 쓰며 3단계 합산 30분. 검토 대기는 제외. README 명확화. |
| 감사 상태와 코드/설계/설정 hash를 생성자에서만 확인 | 반영: 단계 시작, 진행 점검, 라벨 접근 직전, 산출물/봉인 기록 직전 재검사. 변경·감사 철회 합성 검증 추가. |
| 비허용 사용자에서 continue하면 진행 검사를 건너뜀 | 반영: user ID 선필터 이전 행수 기준 검사. 비허용 100만 행 합성 검증 추가. |

1차 검토 code SHA `61daee4882ad54c1766bd27c057b022185f7a749fae880ea3c6c350063a7dbb5`,
config SHA `5e20c04beb2030edfe1b0701da65f89e3c28e692cb4872d71214d3a5fa98d6ce`,
design SHA `52735ce2c8eef3b7bdccaeb9043dc1cd873927d7511b77ce287af9d0b929a506`.
수정 후 루트 합성 테스트 30개 통과. 실제 실행 전 동일 독립 검토자에게 변경 부분을 재검토 요청했다.

## 구현 재검토: 탐색 실행 허가

동일 독립 검토자가 수정 부분을 확인하고 합성 테스트 30개를 재실행했다.
잔여 차단 결함 없음, **PASS_FOR_EXPLORATORY_EXECUTION**. 실제 payload 접근은 없었다.
최종 code/config/design hash와 판정 범위는 implementation-review.json에 기록했다.
결과 해석과 제품 채택 승인은 이 판정에 포함되지 않는다.

## 실제 실행과 결과 독립 검토

prepare·score·evaluate는 각각 첫 실데이터 실행에서 성공했다. 시간은31.219초,
131.141초,8.500초, 합산170.860초다. 실패한 실데이터 phase는 없었다.
평가자와 calibration 교집합0, calibration46,376명/7,355,439평점, common85,517편,
평가2,180명이 사전 조건과 일치했다. 모든 점수 봉인 후 이미 공개된 SELECTION R0만 채점했다.

`/root/review_rec031_results`는 대화 이력 없이(`fork_turns=none`) 산출물을 독립 검토했다.
runner 함수를 호출하지 않은 별도 공식으로 사용자별 지표32,700행×18열과 primary4개·
diagnostic47개 bootstrap을 재계산했다. 지표 최대 오차1.11e-16, CI는1e-14 내 일치했다.
모든 목록의 고유500·관측K seen 제외를 확인하고 BAYES/COUNT13,080목록 전량,
콘텐츠계열108목록 표본을 독립 재구성했다. 사례 선택과135개 출력의 ID/q/UNKNOWN도 일치했다.

| 결과 검토 지적/주의 | 처리 |
| --- | --- |
| 추천 재생성 표본을 전량 검증처럼 읽을 수 있는 문구 | 반영: 지표 전량32,700행과 순위 재구성13,080+108표본의 범위를 구분. |
| dense/sparse 합산 차이가 근접동률에 영향 | 반영: 수치환경·표본 범위·최대오차·경계후보 차이를 comparison/reproduce에 명시. 완료 점수는 변경하지 않음. |
| 불호 diagnostic의 improved_fraction은 d>0이라는 필드 의미 | 반영: 불호는 증가/횟수로 해석. primary Recall 개선 비율과 구분. |
| 공통 카탈로그 counts합과 전체 calibration집계의 분모가 다름 | 반영: 후보 밖19,690개 평점 차이를 설명. |

최종 정책·비교·기존 결론 해석까지 **PASS, 남은 수정 요구 없음**이다. 검토한 세 문서의
SHA와 접근/재현 한계는 result-review.json에 기록했다. raw calibration 집계와 원본 영화명은
결과 검토자가 재개봉하지 않았으며 이 제한을 완료 범위에 명시했다.

사후 사례 표시용 임시 읽기에서 Windows 기본cp949로 UTF-8을 읽다 실패한1건은 인코딩을
명시해 해결했다. 원본 결과나 실험을 변경/재실행한 실패가 아니며 run.yaml에도 기록했다.

## 사용자 지적 후 제품 해석 정정

이후 사용자 지적에 따라 정책 연결의 검토 실패를 인정하고 POLICY-RETRACTION.md를 추가했다.
policy-proposal.md와 insight.md의 당시 검토본은 reviewed-snapshot에 정확한 bytes로 보존했다.
현재 두 경로는 SUPERSEDED 안내이며 result-review.json의 이전 SHA는 snapshot에 대응한다.
숫자 검증 결과와 제품 정책의 타당성을 분리한다. 현재 분류/설계 기준은
`docs/recommendation/plans/service-policy-redesign/`이며 별도 독립 설계 검토를 수행한다.
