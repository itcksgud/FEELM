# FEELM C0 Catalog + C1 Rating·Film E2E

이미 실행 중인 로컬 Compose의 React(`http://127.0.0.1:5173`)와 Spring API·PostgreSQL 연결을
Chromium으로 검증한다. 이 프로젝트는 Compose를 시작하거나 종료하지 않는다.

## 범위

- 검색 홈 → 결과 → 영화 상세 → OTT 정보 → 유사 영화 상세
- 검색 결과 없음
- 실제 API의 잘못된 연도 범위 validation 오류와 필터 복구
- 브라우저에서 재현한 Catalog 503 오류와 재시도
- nginx를 통한 영화 상세 SPA direct route와 새로고침
- 감상 확인 → 정수 별점 → Film/Frame → Popcorn의 C1 실제 mutation 흐름
- 지연 평가, Rating 수정·삭제, 감상 기록 유지와 aggregate 감소
- OTT 기록 실패 시 외부 이동 금지, C1 401·409·503 UI
- C1 SPA direct route·reload와 완료 route redirect

고정 fixture와 예상 문구는 `docs/testing/fixtures.md`, `docs/testing/acceptance-tests.md`,
`docs/ui/screen-contracts.md`와 `docs/c1-draft/**`를 따른다. 정상·빈 상태·validation·direct route와
C1 mutation은 실제 Compose API를 사용한다. 401·409·503 같은 장애 UI만 실패를 결정적으로 만들기
위해 해당 브라우저 요청을 가로챈다.

## C1 데이터 격리와 실행 순서

`c1-rating-film.spec.ts`는 `USER-C1-OWNER`의 Film/Popcorn aggregate를 함께 변경하므로 파일 내부를
명시적으로 `serial` 실행한다. 각 mutation test는 서로 다른 승인 fixture를 소유한다.

1. `WI-PENDING` / `MOV-NONE-LISTED`: 감상 확인 후 즉시 평가
2. `WI-PENDING-E2E` / `MOV-OTT-UNKNOWN`: 독립된 감상 확인 후 나중에 평가
3. `RATING-ONE` / `MOV-KO-FULL`: 수정 후 삭제, unrated 복귀

각 검증은 고정 절대 count 대신 mutation 직전·직후 delta를 비교한다. 테스트는 DB를 reset하거나
fixture를 다시 넣지 않는다. 따라서 C1 mutation 그룹은 새 local migration fixture에서 한 번 실행하며,
중간 상태에서 재실행하면 fixture precondition을 명시적으로 실패시킨다. 두 due WatchIntent는 서로 다른
영화를 사용하므로 즉시 평가와 지연 평가가 서로의 상태를 소비하지 않는다.

## 실행

먼저 저장소 루트의 별도 터미널에서 서비스를 기동하고 health 상태를 확인한다.

```powershell
cd C:\higher\projects\FEELM-standalone
docker compose up --build --wait
docker compose ps
```

그다음 E2E 의존성과 Chromium을 설치하고 실행한다.

```powershell
cd C:\higher\projects\FEELM-standalone\e2e
npm ci
npm run install:browsers
npm test
```

C0 또는 C1만 선택하거나, backend endpoint 준비 전에 TypeScript/수집 상태만 확인할 수 있다.

```powershell
npm run test:c0
npm run test:c1
npm run test:list
```

기본 웹 주소가 아닌 이미 실행 중인 환경을 검증할 때는 `E2E_BASE_URL`만 바꾼다.

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:5173"
npm test
```

실패 산출물은 `e2e/test-results/`, HTML 보고서는 `e2e/playwright-report/`에 생성되며 Git에서
제외된다. Compose 종료는 서비스 소유자가 결정한다.
