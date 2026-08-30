# C5 local screen contracts

세부 route와 상태는 [local 실행 계약](../local-contract.md#5-screen-contract)을 따른다.

- 모든 owner 화면은 C4 local login이 없으면 login으로 이동하고 logout 뒤 cached private payload를 지운다.
- report 상세는 factual metric과 실제 period 영화 전체 pagination을 분리해 표시한다.
- privacy는 PROFILE/FILM/POPCORN 각각의 현재 PRIVATE/PUBLIC과 공개 영향을 명시한다.
- share 화면은 raw token을 생성 직후 한 번만 보여주고 복사 후 재조회하지 않는다.
- shared-report 진입은 `location.hash`를 먼저 `history.replaceState`로 지운 뒤 교환한다.
- notification은 외부 발송을 암시하지 않고 “앱 안에서만”이라고 표시한다.
- loading, empty, ready, unauthorized, private/not-found, conflict, expired를 semantic role로 구분한다.
- 320px 단일 열, keyboard focus, accessible name, `aria-live` status를 제공한다.
- 예상 별점·만족도·취향 향상/진단·비교 문구를 렌더링하지 않는다.

