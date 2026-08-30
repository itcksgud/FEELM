# C3 Local React Screen Contracts

> 상태: `APPROVED` — `LOCAL_MVP_ONLY`

## 공통

- 화면 상단에 `로컬 테스트 사용자`임을 표시하고 production 로그인으로 표현하지 않는다.
- loading, empty, 400, 401, hidden 404, 409, 503을 구분한다.
- 320px에서 card 1열, keyboard focus visible, status/error live region을 제공한다.
- “추천”에는 인기·OTT 제공 범위 기준임을 붙이고 만족도·예상 별점·개인 취향 문구를 쓰지 않는다.

## SCR-C3-001~003 — OTT catalog comparison

- 2~4 provider checkbox와 선택 count를 제공한다.
- summary는 provider 이름, 실제 영화 수, `전체 영화 보기 (N)` link만 표시한다.
- 목록 item은 실제 title/poster/year와 `선택 OTT 중 제공처` badge를 표시한다.
- 더 보기로 `totalCount`까지 전부 접근하고 상세 뒤로가기는 loaded page/scroll을 복원한다.
- 대표 영화 carousel, Rating mean, taste score, freshness/trust badge는 없다.
- materialization 503은 empty 0건으로 렌더링하지 않고 retry한다.

## SCR-C3-004~006 — Party·invitation

- Party create는 이름과 provider 2~4개를 받고 owner `1 / 4`를 표시한다.
- owner는 fixture fake actor select로 초대한다. email/nickname search input은 없다.
- invitation에는 Party, inviter/recipient fake nickname, PENDING/ACCEPTED 상태를 표시한다.
- recipient만 `수락`을 보고, decline/cancel/leave/close/kick/transfer UI는 없다.
- capacity 409는 최신 Party를 refetch하고 `이미 4명이에요`로 복구한다.
- mutation retry는 동일 Idempotency-Key와 expected revision을 사용한다.

## SCR-C3-008 — deterministic Party baseline

- 실제 영화 card와 제공 provider badge를 끝까지 cursor pagination한다.
- 설명은 `선택한 2개 OTT 중 2개에서 볼 수 있어요`, `인기 기준 1위`와 policy label이다.
- 같은 provider set의 Party가 같은 순서임을 테스트한다.
- utility, tasteDifference, expected star, satisfaction/fairness, member별 근거는 DOM/API에 없다.
- empty는 “선택한 OTT에서 보여줄 영화가 없어요”, dependency 503은 retry 가능한 별도 상태다.

## Local actor control

`/dev/actors`는 fixture allowlist를 선택하는 local-only 개발 화면이다. production build/profile에는 포함하지
않으며 non-loopback server에서는 backend 자체가 시작하지 않는다. unknown actor 401은 actor 선택 화면으로
복구하지만 실제 login/OAuth를 invent하지 않는다.

