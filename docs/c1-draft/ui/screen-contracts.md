# C1 Rating·Film 화면 계약

> 상태: `APPROVED`  
> 시각 참고 원본의 데이터 의미보다 본 문서와 승인될 OpenAPI가 우선한다.

## SCR-C1-001 — 감상 확인 대상

- 목업: `1a ⑥-3 알림 (앱 내)`의 “영화 잘 보셨나요?” 항목
- 접근: 로그인 사용자
- API: `listPendingWatchConfirmations`

### 표시·행동

- 확인 due 상태인 영화만 최신 due 순으로 표시한다.
- 영화 포스터·제목, OTT provider, 클릭 시각, “영화 잘 보셨나요?” 행동을 표시한다.
- 항목 선택은 `SCR-C1-002`로 이동한다.
- 알림 목록 전체를 C1이 소유하지 않는다. C1은 pending confirmation 영역/뱃지만 제공한다.

### 상태

| 상태 | 화면 |
| --- | --- |
| loading | 행 skeleton |
| empty | confirmation 영역 숨김 또는 “확인할 영화가 없어요” |
| recoverable error | 기존 목록 유지, 해당 영역 retry |
| unauthorized | 로그인 화면으로 이동하기 전 401 처리 |

## SCR-C1-002 — 실제 감상 여부 확인

- 목업: 전용 화면 없음; `⑥-4`의 “안 봤어요/나중에 평가” 의미를 분리
- API: `confirmWatchIntent`

### 표시·행동

| 행동 | 결과 |
| --- | --- |
| `봤어요` | `watched=true` 저장 후 Rating 선택을 같은 화면에 열거나 `SCR-C1-003` 이동 |
| `안 봤어요` | `watched=false` 사실 저장 후 pending 목록 복귀; 부정 취향으로 표시하지 않음 |
| `나중에`/닫기 | 상태를 바꾸지 않고 pending 유지 |

- mutation마다 새 Idempotency-Key를 만들고 retry에는 같은 key를 재사용한다.
- due 전·만료·이미 terminal이면 409 안내 후 pending 목록을 새로고침한다.
- 다른 기기에서 먼저 응답한 경우 terminal 결과를 반영하고 중복 ViewingRecord를 만들지 않는다.

## SCR-C1-003 — Rating 생성·수정

- 목업: `1a ⑥-4 평가페이지`
- API: `putMyRating`, 수정/삭제 진입 시 `listMyRatings`

### 표시

- 영화 포스터·제목, 감상 확인일·provider
- 정수 1~5 선택 UI와 `내 별점 {value}/5` label
- create flow의 `나중에 평가하기`
- update flow의 현재 값, 저장, 삭제 행동

목업의 `4.0/5.0`은 정수 값 `4/5`로 수정한다. “한 줄 감상”은 FR-26 계약 전 숨긴다.
“안 봤어요”는 Rating 값이 아니라 `SCR-C1-002`의 감상 확인 행동이다.

### 상태

| 상태 | 화면 |
| --- | --- |
| editing | 1~5 선택 전 완료 버튼 비활성 |
| saving | 중복 입력 방지, 선택 값 유지 |
| validation error | 1~5 정수 안내 |
| revision conflict | “다른 곳에서 평가가 변경됐어요” + 최신 값 다시 불러오기 |
| recoverable error | 선택 값 유지 + 같은 Idempotency-Key retry |
| deleted/not found | 목록으로 복귀하고 이미 변경된 상태 안내 |

delete 확인 modal은 “평가는 삭제되지만 감상 기록은 평가 안 남긴 영화에 유지됩니다”를 알린다. 성공하면
Frame·Popcorn이 제거되고 `SCR-C1-007`의 미평가 tab으로 이동한다. transaction 실패 시 기존 상세를 유지한다.

## SCR-C1-004 — Rating 완료·Film 반영

- 목업: `1a ⑥-5 평가 후 필름 기입`, `⑥-6 필름 추가 알림`
- API 입력: `putMyRating`의 committed response

transaction이 성공한 뒤에만 “필름에 추가됐어요”와 Frame 증가를 표시한다. frontend animation 종료를
성공으로 간주하지 않는다. retry replay 응답에서는 Frame이 다시 증가하는 animation을 반복해도 실제
count는 증가하지 않는다.

행동:

- `필름 보기` → `SCR-C1-005`
- `다음 영화 찾기` → C0 검색 또는 후속 Recommendation 화면
- 자동 전환은 접근성의 reduced motion 설정을 존중한다.

## SCR-C1-005 — 내 Film

- 목업: `⑪ 프로필`의 “내 취향 필름”; 전체 목록 전용 목업 없음
- API: `getMyFilm`

### 표시·행동

- 활성 Frame 전체 count와 cursor 목록
- 포스터, 제목, 내 별점, 감상 확인일
- Frame 선택 → `SCR-C1-006`
- 기간별 필터가 추가되어도 Film 총 count는 전체 활성 Frame 수다.

| 상태 | 화면 |
| --- | --- |
| loading | Frame skeleton |
| empty | “아직 필름에 추가된 영화가 없어요” + C0 영화 찾기 |
| next page loading | 기존 Frame 유지 + 하단 spinner |
| error | 로드한 page 유지 + retry |

## SCR-C1-006 — Frame 상세

- 근거: FR-08; 영화 상세 목업의 C1 소유 영역
- API: `getMyFrame`

표시:

- C0 영화 요약과 상세 이동
- 내 별점과 최종 수정 시각
- 감상 확인일과 확인에 사용한 provider
- Rating 수정·삭제 진입

C1에는 감상 메모·한줄평이 없고, 다른 사용자 정보도 없다. Frame이 Rating 삭제로 제거됐거나
다른 사용자의 것이면 같은 404 화면을 사용한다.

## SCR-C1-007 — 평가/평가 안 남긴 영화

- 목업: `1a ⑪-3 평가`, `⑪-4 평가 안 남긴 영화`
- API: `listMyRatings`, `listUnratedViewingRecords`

### 평가 tab

- 활성 Rating 목록, 수정일, 정수 별점 표시
- 선택 → `SCR-C1-003` update
- empty: “아직 남긴 평가가 없어요”

### 평가 안 남긴 영화 tab

- `WATCHED_CONFIRMED` ViewingRecord만 표시
- 감상 확인일·provider와 `평가하기` 행동
- 선택 → `SCR-C1-003` create
- empty: “평가를 기다리는 영화가 없어요”

두 tab의 count는 각각 활성 Rating 수와 Rating 없는 ViewingRecord 수다. 하나를 다른 값에서 빼서
추정하지 않는다.

## SCR-C1-008 — Popcorn Bucket

- 목업: `1a ⑨ 한눈에 보기`
- API: `getMyPopcornBucket`, 원천 상세은 `getMyTasteProfile`

### 표시

- 전체 Popcorn 수 = 활성 Frame 수
- flavor별 `count`와 `averageRating`을 별도 label로 표시
- `averageRating=null`이면 “평가 없음”이며 0점으로 표시하지 않음
- flavor 이름·색은 API의 versioned reference 값을 사용한다. v1 code는 8개 안정 코드지만 frontend는 mapping version 변경 가능성을 허용한다.

목업의 “100알의 취향”은 실제 totalCount를 사용한다. “49%” 같은 해석 문장은 계산 계약이 승인되기
전 C1에서 만들지 않는다.

## 공통 접근성

- 1~5 Rating은 키보드 arrow와 직접 선택이 가능하고 현재 값·척도를 screen reader가 읽을 수 있어야 한다.
- mutation 결과는 `aria-live`로 알리되 error와 성공을 동시에 읽지 않는다.
- 포스터 null은 C0 local placeholder를 재사용한다.
- 색만으로 flavor·상태·별점을 구분하지 않는다.
- 확인 modal은 focus trap, Escape, focus return을 제공한다.
