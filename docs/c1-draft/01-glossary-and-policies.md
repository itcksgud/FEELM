# C1 Rating·Film 용어와 공통 정책

> 상태: `APPROVED`

## 1. 용어

| 용어 | C1 계약 정의 |
| --- | --- |
| WatchIntent | 사용자가 OTT 외부 이동을 선택했다는 사실과 감상 확인 시각을 추적하는 소유자 전용 상태 |
| Idempotency Key | 네트워크 재시도가 같은 mutation을 중복 적용하지 않도록 client가 보내는 불투명 키 |
| ViewingRecord | 사용자가 실제로 감상했다고 확인한 영화 사실. Rating 없이 존재할 수 있음 |
| Rating | 감상 후 입력하는 정수 1~5 만족도. 온보딩 LIKE/DISLIKE와 다른 데이터 |
| 지연 평가 | 감상 확인은 완료했지만 Rating은 아직 없는 상태와 이후 입력 절차 |
| Frame | Rating이 활성인 ViewingRecord 한 건을 Film에 표시하는 파생 기록 단위 |
| Film | 한 사용자의 현재 활성 Frame 전체 모음. 월간·최근 목록이 아님 |
| Popcorn | Frame과 1:1인 취향 시각화 단위. Frame 없이 존재할 수 없음 |
| Popcorn Flavor | rating-eligible 영화당 active mapping version에서 하나 배정되는 versioned 맛 기준. v1은 승인된 8개 안정 코드와 표시명을 사용 |
| Flavor count | 해당 flavor의 활성 Popcorn 수. 감상량 지표 |
| Flavor average rating | 해당 flavor 활성 Rating의 평균. 선호도 지표이며 count와 다른 의미 |
| Taste aggregate | 장르·국가·감독 등 dimension 값별 활성 Rating 수·합·평균의 원천 집계 |
| UserBehaviorEvent | 사용자의 평가·감상 관련 행동을 append-only로 추적하는 내부 event. 도메인 상태의 source of truth가 아님 |
| Outbox event | 같은 DB transaction에서 기록되어 추천·분석 후속 처리를 안정적으로 전달하는 내부 event |

## 2. 식별자와 시간

- C1 공개 식별자는 UUID 문자열이며 C0 `movieId`를 그대로 참조한다.
- 본인 리소스는 `/api/v1/me/*`로만 노출하고 request body의 `userId`를 신뢰하지 않는다.
- 모든 instant는 ISO 8601 UTC다. scheduler 경계도 UTC instant로 비교한다.
- Rating 값은 JSON integer `1|2|3|4|5`; 0.5와 decimal은 validation error다.
- `Idempotency-Key`는 mutation별 필수이며 8~128자의 printable ASCII 불투명 문자열이다.
- 같은 key와 같은 canonical request hash는 최초 결과를 replay하고, 다른 body는 409다.
- WatchIntent 시각은 최초 active 클릭을 기준으로 `confirmationDueAt=clickedAt+48h`, `expiresAt=clickedAt+7d`다.

## 3. 누락과 0의 의미

| 상황 | 의미 | 금지할 해석 |
| --- | --- | --- |
| Rating 없음 | 감상 확인 후 아직 평가하지 않음 | 0점·싫어요 |
| `ratingCount=0` | 평균을 계산할 입력 없음 | `averageRating=0` |
| Popcorn 없음 | 활성 Rating에서 파생된 Popcorn 없음 | 영화 미감상 전체 |
| WatchIntent 만료 | 확인 응답 없음 | 감상하지 않음·싫어요 |
| `안 봤어요` 응답 | 사용자가 감상하지 않았다고 답한 사실 | 부정 취향 신호(DN-C1-005 전) |
| aggregate row 없음 | 해당 dimension의 활성 Rating 없음 | 낮은 선호도 |

평균 입력이 0개면 `averageRating=null`이다.

## 4. Popcorn flavor v1

TMDB genre 목록의 `displayOrder=0` 항목을 primary genre로 사용하며 active mapping version은 `v1`이다.

| 안정 코드 | 표시명 | TMDB genre ID |
| --- | --- | --- |
| `ADRENALINE` | 짜릿함 | 28, 12 |
| `WONDER` | 상상 | 16, 14, 878 |
| `JOY` | 유쾌함 | 35, 10751 |
| `HEART` | 여운 | 18, 10749 |
| `SHADOW` | 긴장 | 80, 27, 9648, 53 |
| `REAL` | 현실 | 99 |
| `LEGACY` | 시대 | 36, 10752, 37 |
| `RHYTHM` | 리듬 | 10402, 10770 |

active projection의 `visibilityStatus=UI_READY` 영화는 active `v1`에서 assignment가 정확히 하나여야
rating-eligible이다. C0에서 `UI_READY`는 catalog-visible 최소 요건을 상위 충족하므로 별도의 동시
visibility 상태를 요구하지 않는다.
primary genre unknown, genre 0개, assignment 0개 또는 복수면 Catalog quality Gate 실패다. 이 표는 v1의
재현 가능한 기준이지 최적의 취향 분류라는 주장이 아니며 후속 개선은 새 mapping version으로 수행한다.

## 5. 접근 정책

- 모든 C1 API는 유효한 bearer 인증이 필요하다.
- token 없음·무효는 401, 다른 사용자의 소유 resource는 존재 여부를 숨기는 404를 사용한다.
- user ID, raw Authorization, Rating 원문을 일반 application log message에 넣지 않는다.
- 내부 운영자·분석 접근과 behavior retention은 C1 사용자 API 권한과 별도 정책으로 승인한다.

## 6. 표시 정책

- 사용자가 입력한 값은 `내 별점 4/5`처럼 출처와 5점 척도를 표시한다.
- TMDB 평점, FEELM 전체 평균, 개인 예상 별점과 같은 필드나 label로 합치지 않는다.
- 목업의 `4.0/5.0`을 0.5점 입력으로 해석하지 않는다. 저장·선택 UI는 정수 5단계다.
- 한줄 감상 입력은 C1에서 숨긴다. C1 Frame은 Rating과 감상 사실만 표시한다.
