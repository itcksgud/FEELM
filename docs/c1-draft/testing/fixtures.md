# C1 Rating·Film 고정 Fixture

> 상태: `APPROVED`  
> 고정 시계: `2026-08-29T12:00:00Z`  
> UUID는 초안 내 backend·frontend·contract test에서 동일하게 사용한다.

## 1. 사용자와 인증

| Fixture | UUID/token | 의미 |
| --- | --- | --- |
| `USER-C1-OWNER` | `018f6826-4da1-7c38-a846-8f794cd8b0cf` | C0 Netflix 구독 fixture와 같은 서비스 사용자 |
| `USER-C1-OTHER` | `5f93a51d-a6f1-41dc-8d86-6b570d53bd82` | 소유권 거부 검증 사용자 |
| owner token | `test-c1-owner-token` | test fake decoder만 인식 |
| other token | `test-c1-other-token` | test fake decoder만 인식 |
| invalid token | `test-c1-invalid-token` | 401 |

실제 JWT secret이나 운영 token을 넣지 않는다.

## 2. C0 참조

| Fixture | UUID | C1 사용 |
| --- | --- | --- |
| `MOV-KO-FULL` | `6b226903-0ca4-4f5a-9bf0-50d6cedd224c` | active Rating·Frame·Popcorn |
| `MOV-EN-FALLBACK` | `19406c31-213f-4fe1-93f6-109f8570ec20` | watched but unrated |
| `MOV-NONE-LISTED` | `e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490` | invalid/retired offer test |
| `MOV-OTT-UNKNOWN` | `1958ba3a-3d8c-4a4f-8845-124c0b12373e` | 두 번째 독립 감상 확인·평가 E2E |
| Netflix provider | `d392a4d5-0428-4e06-aa41-aef899c06842` | direct/aggregator click source |

C1 fixture는 C0 Movie identity와 provider를 복제하지 않고 FK로 참조한다.

## 3. WatchIntent

| Fixture | UUID | 상태·시각 |
| --- | --- | --- |
| `WI-LINK` | `27e3f19b-3f01-4b22-9d1e-52c692f0ca8b` | LINK_CLICKED, clickedAt `2026-08-28T10:00:00Z`, dueAt `2026-08-30T10:00:00Z`, expiresAt `2026-09-04T10:00:00Z`, revision 1 |
| `WI-PENDING` | `2dfa8b82-9f40-452d-a63f-18347483f7b7` | CONFIRMATION_PENDING, clickedAt `2026-08-27T11:00:00Z`, dueAt `2026-08-29T11:00:00Z`, expiresAt `2026-09-03T11:00:00Z`, revision 1 |
| `WI-PENDING-E2E` | `8b7f4a21-4bc4-4c5e-93cb-4e348abcae02` | CONFIRMATION_PENDING, clickedAt `2026-08-27T10:30:00Z`, dueAt `2026-08-29T10:30:00Z`, expiresAt `2026-09-03T10:30:00Z`, revision 1 |
| `WI-CONFIRMED` | `541bf21a-b9ef-40b4-ad74-c56084a99095` | CONFIRMED_WATCHED, linked to `VR-RATED`, revision 2 |
| `WI-NOT-WATCHED` | `f3bd331b-25ec-4d35-b1ab-e3eb79ad889b` | CONFIRMED_NOT_WATCHED, ViewingRecord 없음 |
| `WI-EXPIRED` | `78a0c941-ffec-4fcc-8132-f052d19e96f4` | EXPIRED, 선호·감상 기여 없음 |
| `WI-OTHER` | `aef9c2be-1e46-4778-8c6c-9873989fd672` | USER-C1-OTHER 소유 |

경계 fixture는 `CLICK-T0=2026-08-29T12:00:00Z`, `DUE-T0=2026-08-31T12:00:00Z`,
`EXPIRY-T0=2026-09-05T12:00:00Z`를 사용한다. `DUE-T0-1ms/DUE-T0/DUE-T0+1ms`와
`EXPIRY-T0-1ms/EXPIRY-T0/EXPIRY-T0+1ms`를 고정 시계로 검증한다.

## 4. Viewing·Rating·Frame·Popcorn

| Fixture | UUID | 상태 |
| --- | --- | --- |
| `VR-RATED` | `54eb733a-80e6-475d-aeef-e16b165d3215` | MOV-KO-FULL, RATED_COMPLETED |
| `VR-UNRATED` | `531a4e1d-2da8-48f1-a702-79fd875793d3` | MOV-EN-FALLBACK, WATCHED_CONFIRMED |
| `RATING-ONE` | `0527c943-fb46-4aa5-aea2-130bdc752e75` | MOV-KO-FULL, value=4, revision=2 |
| `FRAME-ONE` | `2b480314-590c-4d9a-b5df-1ef745c15e76` | RATING-ONE projection |
| `POPCORN-ONE` | `6de3b230-3c32-4917-a9d7-f18c9c0ab79b` | FRAME-ONE 1:1 |
| `FLAVOR-SHADOW` | `18828763-1fd7-4ee4-a97f-1496db3c6490` | `SHADOW`, `긴장`, MOV-KO-FULL primary TMDB genre 80 |
| `FLAVOR-HEART` | `50fb6f76-9ab2-4bb4-9a62-7f8b76af9822` | `HEART`, `여운`, 빈 flavor aggregate test |

v1 assignment:

| Movie | Catalog state | primary genre | v1 assignment |
| --- | --- | --- | --- |
| `MOV-KO-FULL` | active projection, visibilityStatus=UI_READY | TMDB 80, displayOrder 0 | `SHADOW` exactly 1 |
| `MOV-EN-FALLBACK` | active projection, visibilityStatus=UI_READY | TMDB 18, displayOrder 0 | `HEART` exactly 1 |
| `MOV-FLAVOR-UNKNOWN` | UI_READY 후보 | TMDB 999999 | quality Gate failure |
| `MOV-FLAVOR-ZERO` | UI_READY 후보 | genre 없음 | quality Gate failure |
| `MOV-FLAVOR-MULTIPLE` | UI_READY 후보 | assignment 2개 | quality Gate failure |

초기 aggregate:

```text
Film totalCount = 1
Popcorn totalCount = 1
SHADOW popcornCount=1, ratingCount=1, ratingSum=4, averageRating=4.0
HEART popcornCount=0, ratingCount=0, ratingSum=0, averageRating=null
```

## 5. Idempotency·실패 주입

| Fixture | 값 | 기대 |
| --- | --- | --- |
| `IDEMP-SAME` | `c1-rating-create-0001` + 동일 body | 최초 response replay, event/Frame/Popcorn 추가 없음 |
| `IDEMP-REUSED` | `c1-rating-create-0002` + 다른 body | 409 IDEMPOTENCY_KEY_REUSED |
| `FAIL-AFTER-RATING` | Frame insert 전에 exception | Rating 포함 전부 rollback |
| `FAIL-AFTER-POPCORN` | aggregate update 전에 exception | Rating·Frame·Popcorn 포함 전부 rollback |
| `RECOMMENDER-DOWN` | outbox consumer 연결 실패 | C1 transaction 성공, outbox retry |
| `POSTGRES-DOWN` | transaction 시작 불가 | 503, partial state 없음 |

## 6. Behavior event 기대

- RATING-ONE create/update마다 실제 상태 변경에 대응하는 event 한 건
- 다른 key의 active intent 재클릭과 ALREADY_WATCHED 클릭은 각각 OTT_LINK_CLICKED 한 건
- idempotent replay event 0건
- event payload에 Authorization, user email, destination URL, 자유 감상문 없음
- Rating delete 뒤 RATING-ONE은 DELETED, deletionTraceId 존재, VR-RATED는 WATCHED_CONFIRMED,
  FRAME-ONE·POPCORN-ONE·기여 row는 없고 SHADOW aggregate는 count=0/ratingSum=0
- deleted Rating value는 behavior payload에 포함하지 않으며 장기 보존은 DN-C1-006에서 정함
