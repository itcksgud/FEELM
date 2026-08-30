# FEELM C1 Rating·Film 제품 범위

> 상태: `APPROVED` — C1 구현 범위  
> 기준일: 2026-08-29

## 1. 목표

C1은 OTT 이동 이후 실제 감상 사실과 평가를 분리해 기록하고, 평가가 완료된 영화만 사용자의
Frame·Film·Popcorn·취향 집계에 정확히 한 번 반영하는 수직 기능이다.

```text
OTT 외부 이동 직전 클릭 기록
→ 확인 가능 시점 도달
→ 실제 감상 여부 확인
→ 감상했다면 지금 평가 또는 지연 평가
→ 정수 1~5 Rating 저장
→ Frame·Popcorn·취향 집계를 한 transaction으로 반영
→ Film·Popcorn Bucket·평가 기록 조회
```

추천 시스템 장애는 위 흐름을 막지 않는다. 추천·fold-in 갱신은 transaction에 기록된 outbox를
소비하는 후속 작업이며, C1 핵심 쓰기의 성공 조건이 아니다.

## 2. Actor

| Actor | C1에서 할 수 있는 일 |
| --- | --- |
| 로그인 사용자 | 본인의 OTT 클릭·감상 응답·평가 생성/수정/삭제·Film·Popcorn·취향 조회 |
| 스케줄러 | 확인 시점이 된 intent 활성화, 응답 없는 intent 만료 |
| 내부 projection worker | transaction outbox를 이용한 추천/분석 후속 갱신 |
| 비회원 | C1 개인 데이터 접근 불가; 401 |

다른 사용자의 Film·Popcorn 공개 조회는 FR-25이며 C1 범위가 아니다.

## 3. 포함 범위

| Capability | Source | C1 결과 |
| --- | --- | --- |
| OTT 링크 클릭 기록 | FR-17, 사용자 추가 지시 | 외부 이동 전 WatchIntent와 행동 event를 멱등 저장 |
| 감상 확인·만료 | FR-18, DN-C1-001 | 최초 active 클릭+48시간부터 확인, 최초 active 클릭+7일 무응답 만료 |
| 지연 평가 | D-01/Q-01 | `WATCHED_CONFIRMED` 목록에서 나중에 평가 가능 |
| Rating 생성·수정·삭제 | FR-19, D-06/Q-06 | 정수 1~5, 사용자·영화당 활성 Rating 최대 1개 |
| Frame·Film | FR-07, FR-08 | 평가 완료 영화의 Frame과 전체 Film 조회 |
| Popcorn·Bucket | FR-06, FR-09, DN-C1-003 | Frame과 1:1 Popcorn, 승인된 v1 8개 맛별 count와 평균 평점 분리 |
| 취향 원천 집계 | FR-04 | 장르·국가·감독 등 확정 가능한 원천별 count/rating aggregate |
| 평가·행동 기록 | 주요 데이터, 사용자 추가 지시 | append-only behavior event와 변경 추적; 공개 activity feed 아님 |
| 정합성·보안 | NFR-05~NFR-07 | 원자성, 멱등성, 추천 장애 격리, 소유자 전용 접근 |

## 4. 명시적 제외

- 인증·회원가입 구현과 실제 OAuth 연동
- C0 Catalog 영화·OTT 수집 방식 변경
- 한줄평·다른 사용자 감상평(FR-26), 감상 메모의 제품 계약
- 수동 감상 기록과 재감상 회차 관리
- Film·Popcorn 공개, 비교, 리포트, 공유
- v1 맛 매핑을 최적 모델이라고 주장하거나 승인 없이 mapping version을 변경하는 일
- 취향 키워드 3~5개 선택식과 개인 추천 모델 갱신 구현
- 푸시 알림 provider 연동; 앱 내 pending 목록까지만 계약
- `안 봤어요` 응답을 싫어요·부정 취향 신호로 사용하는 일
- Rating/행동 원시 이력의 운영 보존 기간 확정

## 5. 화면

| Screen ID | 목업 대응 | 역할 |
| --- | --- | --- |
| `SCR-C1-001` | `⑥-3 알림` | 감상 확인 대상 진입 |
| `SCR-C1-002` | 신규 계약 화면 | 실제 감상 여부 확인 |
| `SCR-C1-003` | `⑥-4 평가페이지` | 정수 1~5 평가 생성·수정, 지연 평가 선택 |
| `SCR-C1-004` | `⑥-5`, `⑥-6` | Rating transaction 완료와 Film 반영 확인 |
| `SCR-C1-005` | `⑪ 내 취향 필름` | 전체 Frame 목록·요약 |
| `SCR-C1-006` | FR-08, 상세 목업 일부 | Frame의 영화·평가·감상 사실 조회 |
| `SCR-C1-007` | `⑪-3`, `⑪-4` | 평가 완료/미평가 감상 목록과 수정 진입 |
| `SCR-C1-008` | `⑨ 한눈에 보기` | 맛별 Popcorn 수와 평균 평점 조회 |

## 6. 품질 Gate

| 항목 | Gate |
| --- | --- |
| 중복 방지 | 같은 actor·operation·Idempotency-Key의 동일 요청은 상태를 두 번 변경하지 않음 |
| 원자성 | Rating과 Frame·Popcorn·동기 aggregate 중 하나라도 실패하면 전부 rollback |
| 불변식 | 활성 Frame 수 = 활성 Popcorn 수 = 파생 대상 활성 Rating 수 |
| 소유권 | 다른 사용자의 식별자로 C1 개인 리소스를 읽거나 수정할 수 없음 |
| 장애 격리 | 추천/FastAPI/Spark 중단 중에도 감상·평가 쓰기와 C1 조회 성공 |
| 추적성 | Rating·WatchIntent 변경은 안전한 behavior event와 traceId로 추적 가능 |
| 개인정보 | raw token, 외부 응답, 사용자 ID를 로그 message에 기록하지 않음 |

## 7. 핵심 시나리오

| ID | 시나리오 |
| --- | --- |
| `SCN-C1-001` | C0 OTT offer 선택 → WatchIntent commit → 외부 이동 |
| `SCN-C1-002` | due intent → 봤어요 → Rating은 나중에 → unrated 목록 |
| `SCN-C1-003` | confirmed viewing → Rating create/update → Frame·Popcorn·aggregate 원자 반영 |
| `SCN-C1-004` | Film→Frame 상세→Rating 수정과 Popcorn Bucket 조회 |
| `SCN-C1-005` | Rating 삭제 → soft-delete → Frame·Popcorn 제거와 집계 역산 → 감상 확인 상태 복귀 |
| `SCN-C1-006` | 추천 장애·DB failure·중복 request 중 정합성과 rollback |

## 8. 승인 완료 조건

- `DN-C1-001` 반복 클릭·확인 시각 정책 반영 완료
- `DN-C1-002` Rating 삭제 시 파생 데이터 정책 반영 완료
- `DN-C1-003` rating-eligible 영화의 v1 단일 flavor assignment 정책 반영 완료
- OpenAPI에서 P0 차단 marker 제거 완료
- 삭제·rollback·aggregate acceptance test 기대값 확정 완료
- C1 required bearer 계약과 fake auth fixture 정의 완료
- 모든 operation을 entity, AC, task, automated test ID에 연결 완료
