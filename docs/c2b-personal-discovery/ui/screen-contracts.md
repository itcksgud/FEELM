# C2B React 화면 계약

> 상태: `APPROVED_LOCAL_BASELINE_WITH_BLOCKED_EXTENSIONS`  
> 구현: 없음. 승인 전 production route/navigation에 추가 금지.

## SCR-C2B-001 — 홈 개인·발견 추천 누적 collection

### 목적

사용자가 최초 3편을 보고, 원할 때 최대 3편씩 더 받아 기존 목록과 함께 비교한 뒤 영화 상세로 이동한다.

### 표시

- section heading: 현재 `인기 기준 추천`
- movie poster/title/year/genre. 최초 최대 3개, 추가 요청마다 최대 3개를 기존 card 뒤에 append
- `추가 추천` button: `hasMore=true`일 때만 활성, loading 중 중복 요청 방지
- 각 card의 `관심 없음`: 성공한 그 card만 제거하고 나머지 순서·DOM identity 유지
- 예상 별점 숫자·confidence 없음
- public reason 영역 없음
- page `COMPLETE`: 이번 요청에서 정확히 3개 append. 제외 issue가 있어도 최종 3개면 COMPLETE
- page `PARTIAL`: Top500 소진 뒤 이번 요청 1~2개. 기존 card는 유지
- page `EMPTY`: 새 card 없음. 기존 card는 유지하고 `더 추천할 영화가 없어요`
- append `503`: retry button, 기존 active collection 유지. 실패한 delta를 성공처럼 붙이지 않음
- stale `409`: collection GET으로 refresh하되 기존 유효 card를 조용히 비우지 않음
- 응답은 `Cache-Control: private, no-store`; raw cursor는 URL·analytics·DOM attribute에 노출하지 않음

### 개인화 label

- K<10: `인기 기준 추천`; 입력 부족 수치를 사용자에게 강요하는 copy 금지.
- K≥10이더라도 DN-C2B-001 전 같은 label.
- `내 평가를 반영한 추천`은 personalizationApplied=true인 승인 version에만 표시한다.
- `새로운 취향`, `발견` badge는 DN-C2B-002 전 표시하지 않는다.

### Exposure

React가 card DOM render를 commit한 뒤 실제 render된 deliveryItemId와 연속 position만 한 번
acknowledgement한다. fetch 성공만으로 exposure를 기록하지 않는다. ack 실패 시 card는 볼 수 있지만 action
전송은 recommendationItemId가 없으므로 attribution unavailable 상태로 두고 무한 retry하지 않는다.
client exposedAt은 보내지 않으며 서버가 시각을 기록한다.

### Retention·exit

- 추가 추천 성공은 기존 card의 key·순서·내용을 대체하지 않는다.
- Rating commit 성공 또는 `관심 없음` commit 성공 시에만 해당 card를 목록에서 제거한다.
- 감상 완료·무클릭·미평가·낮은 별점 자체는 자동 제거 조건이 아니다.
- 평가 완료와 관심 없음은 별도 typed 상태이며 어느 쪽도 추천 만족/실패 UI를 만들지 않는다.
- Catalog 퇴역으로 숨긴 card는 두 사용자 상태로 표현하지 않는다.
- dismiss 실패 시 card를 낙관적으로 영구 제거하지 않고 복구 가능한 오류 상태로 둔다.

## SCR-C2B-002 — 추천에서 연 영화 상세

기존 C0 영화 상세 화면을 재사용한다. navigation state의 recommendationItemId는 화면에 표시하지 않고
같은 actor/movie일 때만 DETAIL_OPENED action을 멱등 전송한다.

- action 실패가 Catalog 상세 표시를 막지 않는다.
- 다른 owner item, stale/deleted movie는 action 404/409이며 상세의 C0 자체 결과와 분리한다.
- 추천 이유나 예상 별점을 상세에 새로 만들지 않는다.

## SCR-C2B-003 — OTT·감상·평가 attribution 연속성

기존 C1 OTT click/confirmation/Rating UI를 재사용한다.

1. 현재 C1 응답은 current click behaviorEventId를 주지 않으므로 OTT recommendation action은 차단한다.
2. `TASK-C2B-011` 승인 뒤 C1 `createWatchIntent`가 반환한 current behaviorEventId로만
   `OTT_OPTION_OPENED`를 보낸다. client click 시각/WatchIntent ID/destination은 action body에 넣지 않는다.
3. action 실패가 WatchIntent나 외부 OTT 이동 성공을 rollback하지 않는다.
4. CREATED click만 이후 ViewingRecord·Rating을 server projector가 연결한다. ACTIVE_REUSED와
   ALREADY_WATCHED는 click-only이며 과거 outcome을 새 추천에 복제하지 않는다.
5. UI는 `추천 만족`, `추천 성공`, observed utility 숫자를 표시하지 않는다.

## 공통 접근성·privacy

- card는 keyboard focus와 accessible movie title을 가진다.
- loading skeleton은 card 수를 개인화/선호 신호로 말하지 않는다.
- error copy에 UUID, token, Rating 값, artifact/version 원문을 표시하지 않는다.
- ErrorResponse path는 route template으로만 취급하고 실제 UUID/query/upstream/filesystem path를 render/analytics에 남기지 않는다.
- analytics DOM attribute에 recommendationItemId/movieId를 노출하지 않는다.
