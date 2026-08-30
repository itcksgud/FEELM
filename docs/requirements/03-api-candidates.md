# API 후보

이 문서는 OpenAPI 작성 전 리소스와 행위를 확인하기 위한 초안이다. 경로, 메서드, 요청·응답 스키마는 아직 확정값이 아니다.

## 1. 공통 API 정책 선결 항목

- 기본 경로와 버전: 예) `/api/v1`
- 인증: 세션 쿠키 또는 Bearer 토큰
- 사용자 식별: URL에 `userId`를 노출할지, 본인 리소스는 `/me`로 통일할지
- 페이지네이션: cursor 또는 page/size
- 오류 형식: `code`, `message`, `details`, `traceId`
- 시간: ISO 8601, 서버 저장 UTC, 사용자 시간대 처리
- 멱등성: 회원가입, OTT 클릭 기록, 감상 완료, 파티 참여에 필요한 키
- 공개 범위: 비회원·회원·파티원·소유자별 인가

## 2. 1차 MVP 엔드포인트 후보

### Identity

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| POST | `/auth/sign-up` | 이메일·비밀번호·닉네임 회원가입 | FR-01, 추가 합의 기능 |
| POST | `/auth/login` | 로그인 | FR-01 |
| POST | `/auth/social/{provider}/login` | 구글·카카오·네이버 로그인 | 추가 합의 기능 |
| POST | `/auth/logout` | 로그아웃 | FR-01 |
| GET | `/me` | 로그인 사용자 기본 정보 | FR-01 |
| PATCH | `/me/profile` | 닉네임 등 프로필 수정 | 추가 합의 기능 |
| POST | `/auth/password-reset-requests` | 비밀번호 재설정 요청 | 추가 합의 기능 |
| PUT | `/me/password` | 로그인 사용자의 비밀번호 변경 | 추가 합의 기능 |
| DELETE | `/me` | 회원 탈퇴 | 추가 합의 기능 |

`provider`는 `GOOGLE`, `KAKAO`, `NAVER`를 지원한다. 세 제공자 모두 범위에 포함하되 구현 순서는 연동 난이도를 기준으로 정한다. 이메일 계정과 소셜 계정의 연결·중복 처리, 닉네임 중복·변경 정책은 아직 미정이다.

### Onboarding / Profile

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/onboarding/movies` | 초기 평가용 영화 목록 | FR-02 |
| PUT | `/onboarding/preferences` | 좋아요/싫어요 일괄 저장 | FR-02 |
| POST | `/onboarding/complete` | 초기 취향 생성 요청·완료 | FR-02 |
| GET | `/ott-providers` | 설정 가능한 OTT 목록 | FR-03 |
| GET | `/me/ott-subscriptions` | 내 구독 OTT 조회 | FR-03 |
| PUT | `/me/ott-subscriptions` | 내 구독 OTT 전체 교체 | FR-03 |
| GET | `/me/notification-settings` | 평가·추천·마케팅 알림 설정 조회 | 추가 합의 기능 |
| PUT | `/me/notification-settings` | 평가·추천·마케팅 알림 설정 변경 | 추가 합의 기능 |

온보딩 입력은 `LIKE/DISLIKE`만 저장한다. 드래그 거리의 좋아요·싫어요 판정은 프론트엔드 또는 별도 정책 계층에서 수행하며 임계치는 아직 미정이다.

### Catalog / Search / OTT

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/movies` | 키워드 검색·필터·정렬·페이지네이션 | FR-14, FR-15 |
| GET | `/movies/{movieId}` | 영화 상세 | FR-08, FR-11, FR-16 |
| GET | `/movies/{movieId}/similar` | 유사 영화 | FR-11 |
| GET | `/movies/{movieId}/ott-offers` | OTT 제공처와 구독 우선 정렬 링크 | FR-16 |

`GET /movies` 후보 파라미터: `query`, `genreIds`, `countryCodes`, `releaseYearFrom`, `releaseYearTo`, `ottProviderIds`, `sort`, `cursor`, `limit`.

### Viewing / Rating

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| POST | `/watch-intents` | OTT 이동 직전 링크 클릭 사실 기록 | FR-17 |
| GET | `/me/watch-intents/pending-confirmation` | 감상 확인이 필요한 항목 조회 | FR-18 |
| POST | `/watch-intents/{watchIntentId}/confirm-watched` | 실제 감상 사실만 먼저 확인 | FR-18, 추가 합의 |
| POST | `/watch-intents/{watchIntentId}/reject` | 실제로 감상하지 않았다고 응답 | FR-18에서 상태 확정 필요 |
| GET | `/me/viewing-records/unrated` | 감상 확인 후 아직 평가하지 않은 영화 목록 | 추가 합의 |
| GET | `/me/ratings` | 내 평가 목록 | FR-19, 추가 합의 |
| PUT | `/me/ratings/{movieId}` | 정수 1~5 평가 생성 또는 수정 | FR-19, 추가 합의 |
| DELETE | `/me/ratings/{movieId}` | 내 평가 삭제 | 추가 합의 |

감상 확인은 Rating 없이 `WATCHED_CONFIRMED` 상태를 만든다. 이후 정수 1~5 Rating을 저장하면 `RATED_COMPLETED`가 되고 `Frame`, `Popcorn`, 취향 집계를 함께 생성·갱신한다. 재감상은 별도 기록으로 관리하지 않는다. Rating 삭제 시 `ViewingRecord`는 유지하지만 Frame·Popcorn·취향 집계를 어떻게 되돌릴지는 추가 결정이 필요하다.

### Film / Taste

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/me/film` | 내 프레임 목록과 필름 요약 | FR-07 |
| GET | `/me/film/frames/{frameId}` | 프레임 상세 | FR-08 |
| GET | `/me/taste-profile` | 요소별 취향 분석 | FR-04 |
| GET | `/me/taste-keywords` | 취향 키워드 3~5개 | FR-05 |
| GET | `/me/popcorn-bucket` | 맛별 수량·평균 평점·발견 상태 | FR-06, FR-09 |

팝콘 버킷 응답에서는 `count`와 `averageRating`을 별도 필드로 제공해야 한다. 팝콘 맛은 8개로 구성하되 실제 데이터 분석 후 맛 명칭과 장르 매핑을 확정하므로 응답은 고정 문자열 enum 대신 `flavorId`를 사용한다.

### Recommendation / XAI

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/me/recommendations` | 맞춤·발견을 조합한 기본 추천 3개 | FR-10, FR-12 |
| GET | `/me/recommendations/personalized` | 개인 맞춤 추천 | FR-10 |
| GET | `/me/recommendations/discovery` | 새로운 맛 발견 추천 | FR-12 |

현재 기본 추천은 `BASELINE_THREE`이며 최초 3편 뒤 요청마다 최대 3편을 server-side collection에 누적한다. 2+1 discovery는 REC-EV-013 v1 Gate 실패 때문에 새 evidence·제품 결정 전 차단한다. 추천 응답의 예상 별점·이유 공개는 별도 결정이다. 구독하지 않은 OTT 영화도 후보에서 제외하지 않는다. 명시적 `NOT_INTERESTED` mutation은 Rating과 분리하며 Rating 제출 완료와 함께 목록·향후 후보 제외 사유가 된다. 감상 완료만으로는 기존 추천을 제거하지 않는다.

### Party

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| POST | `/parties` | 파티 생성 | FR-20 |
| GET | `/parties/{partyId}` | 파티와 구성원 조회 | FR-20 |
| GET | `/users/search?nickname={nickname}` | 닉네임으로 초대 대상 회원 조회 | 추가 합의 |
| POST | `/parties/{partyId}/invitations` | 앱 내 파티 초대 생성 | FR-20, 추가 합의 |
| GET | `/me/party-invitations` | 내가 받은 파티 초대 목록 | 추가 합의 |
| POST | `/party-invitations/{invitationId}/accept` | 파티 초대 수락 | 추가 합의 |
| POST | `/party-invitations/{invitationId}/reject` | 파티 초대 거절 | 추가 합의 |
| DELETE | `/parties/{partyId}/members/me` | 파티 나가기 | 원문에 없음 |
| GET | `/parties/{partyId}/taste-analysis` | 공통 취향과 차이점 | FR-21 |
| GET | `/parties/{partyId}/recommendations` | 파티 영화 추천 | FR-22 |

파티 최대 인원은 파티장을 포함해 4명이며 초대와 수락은 앱 안에서 처리한다. 초대 요청은 닉네임 검색 결과의 `userId`를 사용한다. 파티 삭제·종료, 파티장 위임, 초대 만료·재초대, 중복 참여는 추가 합의가 필요하다.

## 3. 2차 MVP 엔드포인트 후보

### Report / Share

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/me/taste-reports` | 내 반기별 리포트 목록 | FR-23 |
| GET | `/me/taste-reports/{reportId}` | 리포트 상세 | FR-23 |
| POST | `/me/taste-reports/{reportId}/shares` | 외부 공유용 카드·링크 생성 | FR-24 |
| GET | `/shared/taste-reports/{shareToken}` | 공유 리포트 조회 | FR-24 |
| DELETE | `/me/taste-reports/{reportId}/shares/{shareId}` | 공유 중단 후보 | FR-24에서 정책 확인 필요 |
| GET | `/me/taste-reports/{reportId}/download` | 리포트를 휴대폰에 저장할 파일 다운로드 | 추가 합의 |

리포트는 요청 시 즉석 계산하기보다 반기 배치 산출물로 저장하고 API가 결과를 조회하는 구조를 우선 검토한다. 공유 링크와 토큰은 서버에 저장하며 앱 밖 사용자가 로그인 없이 보는 용도다. 생성 시 `expiresAt = createdAt + 1개월`을 설정하고 만료된 링크는 조회할 수 없다. 다운로드 파일 형식과 리포트에 포함할 상세 내용은 아직 미정이다.

### Social / Public Taste

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/me/privacy-settings` | 내 공개 설정 조회 | FR-25의 선행 계약 |
| PUT | `/me/privacy-settings` | 필름·팝콘 버킷 공개 범위 설정 | FR-25의 선행 계약 |
| GET | `/users/{userId}` | 공개 범위가 적용된 사용자 프로필 조회 | FR-25의 선행 계약 |
| GET | `/users/{userId}/film` | 공개 사용자의 필름 조회 | FR-25 |
| GET | `/users/{userId}/popcorn-bucket` | 공개 사용자의 팝콘 버킷 조회 | FR-25 |
| GET | `/me/taste-comparisons/{targetUserId}` | 나와 공개 사용자의 취향 비교 | FR-25 |

앱 내 다른 사용자 진입점은 한줄평·댓글 작성자의 프로필 클릭을 우선 사용한다. 다른 사용자의 데이터는 명시적인 공개 읽기 모델로 분리하며 사용자가 프로필·필름·팝콘 공개 여부를 설정한다. 공개 단계와 취향 비교 허용 조건은 아직 미정이다.

### Short Review

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/movies/{movieId}/short-reviews` | 영화별 한줄평 목록 | FR-26 |
| PUT | `/movies/{movieId}/short-review` | 내 한줄평 작성 또는 교체 | FR-26 |
| DELETE | `/movies/{movieId}/short-review` | 내 한줄평 삭제 후보 | FR-26에서 정책 확인 필요 |

한줄평 응답은 작성자 프로필로 이동할 수 있도록 공개 가능한 `authorUserId`, `nickname`, 프로필 요약을 포함한다.

### Trend

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/trends/movies` | 최근 급상승 영화 조회 | FR-27 |

트렌드는 계산 시점과 집계 기간이 포함된 스냅샷 응답으로 제공하는 안을 검토한다.

## 4. 현재 범위 밖 확장 API

| 범위 | 후보 리소스 | 요구사항 |
|---|---|---|
| 사용자 랭킹 | `/rankings/users` | FR-28 |
| 탐험 배지 | `/me/badges` | FR-29 |
| 오늘의 영화 | `/me/recommendations/daily` | FR-30 |
| 커뮤니티 | `/community/posts`, `/community/posts/{id}/comments` | FR-31 |
| 발견 표시 | 기존 `/me/popcorn-bucket` 응답의 발견 필드 | FP-32 |

확장 범위의 내부 처리에는 사용자 랭킹 스냅샷 생성과 배지 조건 판정이 포함된다.

## 5. 내부 이벤트 후보

- `OnboardingCompleted`
- `OttLinkClicked`
- `WatchConfirmationBecameDue`
- `ViewingConfirmed`
- `WatchIntentExpired`
- `RatingUpserted`
- `RatingDeleted`
- `FrameCreated`
- `TasteProfileUpdated`
- `PartyMembershipChanged`
- `PartyInvitationCreated`
- `PartyInvitationResponded`
- `RecommendationCandidatesGenerated`
- `TasteReportGenerated`
- `TasteReportShared`
- `ShortReviewUpserted`
- `TrendSnapshotGenerated`

`ViewingConfirmed`는 감상 사실만 기록하고 `RatingUpserted`가 처음 발생할 때 Frame·Popcorn·취향 갱신을 시작한다. 중복 생성을 막기 위한 멱등 키와 사용자에게 읽기 모델이 보이는 시점을 명확히 해야 한다.

## 6. 내부 작업 후보

- 감상 확인 활성화: 링크 클릭 후 확정된 시간 경과 시 실행
- 감상 확인 만료: 클릭 후 7일 무응답 처리
- 사용자 vector fold-in
- 취향 프로필 및 맛별 집계 갱신
- ALS 학습 및 사용자별 Top-500 후보 생성
- 영화 임베딩·유사도 갱신
- 개인·발견·파티 추천 생성
- 추천 캐시 갱신·무효화
- 반기 취향 리포트 생성과 공유 카드 렌더링
- 급상승 영화 집계

이 작업들은 사용자용 OpenAPI에서 숨기고 운영·배치 인터페이스로 분리한다.
