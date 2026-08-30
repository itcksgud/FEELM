# C2B 제품 범위

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`

## 로컬 baseline 승인 범위

- required bearer 사용자의 비개인화 인기 baseline delivery 조회
- C2A internal rank, active Rating input snapshot, candidate/artifact version 재사용
- UI_READY 후보가 충분할 때 최초 3편, 추가 요청마다 최대 3편의 typed movie card
- C2A가 반환한 정렬된 Top500을 현재 Catalog·C1 상태로 재검증하고 순서를 보존해 server-side collection에 누적 backfill
- 추가 추천에도 기존 active item 유지, 재진입 시 복구, 평가 완료·명시적 `관심 없음` 뒤 목록과 향후 후보 제외
- Top500 request/response exact set·cardinality·sourceRank boundary와 selection count invariant
- 재진입 GET으로 복구되는 active collection과 10분 TTL 후보의 signed append cursor
- 명시적 `관심 없음` dismissal과 append/dismiss 멱등 replay
- Catalog와 active Rating 상태를 재검증하는 fail-closed baseline
- stale, PARTIAL, EMPTY, upstream unavailable, cross-owner 상태

## 구현·공개 차단

아래는 decision/evidence가 끝날 때까지 구현하지 않는다.

- K10 alpha 0.2를 public champion으로 채택하거나 `내 취향 맞춤` label을 표시
- REC-EV-013에서 feasible policy가 없는데 2+1 또는 발견 슬롯을 구성
- REC-EV-004B Explore05 재사용
- 예상 별점 숫자·confidence·displayEligible 활성화
- 공개 추천 이유 문구 또는 이유 1/3개 선택
- 사건 없음·클릭 없음·미평가를 dislike/negative/satisfaction으로 추론
- 최종 observed utility 공식이나 satisfaction KPI
- exposure acknowledgement와 recommendation action/outcome attribution operation
- 개인화 eligibility·적용 상태·예상 별점·추천 사유를 공개 response에 추가
- stale cache를 200 성공으로 반환
- production activation, 배포, 운영 credential 사용
- `관심 없음`을 Rating 값·싫어요·만족 실패로 변환하거나 무클릭·미평가·감상 완료에서 추론
- 현 C1 응답의 WatchIntent ID/과거 clickedAt만으로 ACTIVE_REUSED·ALREADY_WATCHED를 현재 click에 귀속
- C1 click과 C2B action을 한 transaction으로 묶거나 C2B 실패로 C1 mutation을 rollback

## 기존 Slice 재사용

| 기존 자산 | 재사용 | 변경 금지 경계 |
| --- | --- | --- |
| C0 Catalog | active version, UI_READY movie card | stale/deleted movie 노출 금지 |
| C1 Rating·Film | active Rating input, WatchIntent→Viewing→Rating chain | C1 mutation 성공을 추천 장애가 막지 않음 |
| C2A internal | candidate, rank, strict client, exposure snapshot | internal service credential·alpha 0 기본 유지 |
| C2B local baseline | delivery/append/dismiss | exposure/action과 production activation 금지 |

## `DN-C2B-002` 승인 경계

- token: `BASELINE_THREE_CUMULATIVE_LOAD_MORE_RATED_OR_EXPLICIT_DISMISS`
- 최초 3편, 추가 요청당 최대 3편, Top500까지 session duplicate 금지
- 추가 요청은 기존 item을 교체·재정렬·삭제하지 않는다.
- item 종료 사유는 평가 제출 완료 `COMPLETED_RATED` 또는 명시적 `NOT_INTERESTED`다. 감상만으로는 제거하지 않는다.
- Catalog 비활성·삭제·UI_READY 상실은 사용자 관심과 무관한 `RETIRED_CATALOG` 안전 상태다.
- opaque signed cursor와 expected revision을 쓰며 stale append 409가 기존 collection을 지우지 않는다.
- 사용자 별도 승인으로 세 baseline operation의 로컬 구현 권위만 생겼다. React와 production은 현 범위 밖이다.

## 요구사항

- `FR-10`: 개인 맞춤 추천 — 현재 offline 후보까지만 존재
- `FR-12`: 취향 발견 추천 — REC-EV-013 selection Gate 실패로 차단
- `FR-13`: 추천 설명 — REC-PD-007 전 UI 차단
- `NFR-01`: 빠른 추천 — local REC-EV-007 값은 production SLA 아님
- `NFR-04`: Spring/FastAPI 독립 경계
- `NFR-05`: 추천 장애가 Catalog·Rating을 막지 않음
- `NFR-07`: required bearer, actor isolation, privacy-safe logging
