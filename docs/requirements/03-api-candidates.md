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
- 공개 범위: 영화 검색·상세는 비회원 공개, 사용자 데이터는 공개 프로필·회원·파티원·소유자별 인가

## 2. 1차 MVP 엔드포인트 후보

### Identity

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| POST | `/auth/sign-up` | 이메일·비밀번호·닉네임 가입 요청과 인증 메일 발송 | FR-01, AR-01.1, AR-01.3, AR-03.1~AR-03.2 |
| POST | `/auth/email-verifications/confirm` | 이메일 인증 완료 및 계정 활성화 | AR-01.2 |
| POST | `/auth/email-verifications/resend` | 이메일 인증 메일 재발송 | AR-01.2 |
| POST | `/auth/login` | 로그인 | FR-01 |
| POST | `/auth/social/{provider}/login` | 구글·카카오·네이버 소셜 가입 또는 로그인 | AR-02.1~AR-02.3 |
| POST | `/me/social-identities/{provider}` | 로그인한 기존 계정에 소셜 계정 연결 | AR-02.4 |
| DELETE | `/me/social-identities/{provider}` | 다른 로그인 수단이 있을 때 소셜 연결 해제 | AR-02.3~AR-02.4 |
| POST | `/auth/logout` | 로그아웃 | FR-01 |
| GET | `/me` | 로그인 사용자 기본 정보 | FR-01 |
| PATCH | `/me/profile` | 고유 닉네임 등 프로필 수정 | AR-03.1~AR-03.2, AR-04.4 |
| POST | `/auth/password-reset-requests` | 비밀번호 재설정 요청 | AR-04.1 |
| PUT | `/me/password` | 로그인 사용자의 비밀번호 변경 | AR-04.2 |
| DELETE | `/me` | 회원 탈퇴 | AR-04.3 |

`provider`는 `GOOGLE`, `KAKAO`, `NAVER`를 지원한다. 소셜 신규 가입은 새 `User`와 `(provider, providerUserId)` `SocialIdentity`를 함께 만든다. 각 소셜 식별자는 정확히 한 User에만 귀속하므로 소셜 가입은 단일 귀속 제약을 위반하지 않는다. 한 User는 이메일 로그인과 제공자별 소셜 식별자를 함께 가질 수 있다. 소셜 이메일과 동일한 기존 User가 있으면 새 계정이나 자동 통합을 만들지 않고, 사용자가 기존 로그인 수단으로 로그인한 뒤 설정에서 소셜 계정을 연결한다.

### Onboarding / Profile

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/onboarding/movies` | 초기 평가용 영화 목록과 건너뛰기 후 새 후보 | FR-02, AR-05.2 |
| PUT | `/onboarding/preferences` | 명시적으로 선택한 좋아요/싫어요 일괄 저장 | FR-02, AR-05.1~AR-05.2 |
| POST | `/onboarding/complete` | 초기 취향 생성 요청·완료 | FR-02 |
| GET | `/ott-providers` | 설정 가능한 OTT 목록 | FR-03 |
| GET | `/me/ott-subscriptions` | 내 구독 OTT 조회 | FR-03 |
| PUT | `/me/ott-subscriptions` | 내 구독 OTT 전체 교체 | FR-03 |
| GET | `/me/notification-settings` | 평가·추천·마케팅 알림 설정 조회 | AR-04.6 |
| PUT | `/me/notification-settings` | 평가·추천·마케팅 알림 설정 변경 | AR-04.6 |

온보딩 입력은 `LIKE/DISLIKE`만 저장한다. UI의 명시적인 선택 영역 진입 또는 버튼 선택으로 값을 확정하며, 미선택 카드는 서버에 저장하지 않고 건너뛴 뒤 새 후보를 요청한다.

### Catalog / Search / OTT

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/movies` | 비회원 키워드 검색·필터·정렬·페이지네이션 | FR-14, FR-15, AR-10.1 |
| GET | `/movies/{movieId}` | 비회원 영화 상세와 로그인 사용자 차단 필터 | FR-08, FR-11, FR-16, AR-10.2, AR-09.5 |
| GET | `/movies/{movieId}/ratings` | 영화 상세용 공개 평가 목록에서 차단 작성자 제외 | AR-09.5, AR-11.3 |
| GET | `/movies/{movieId}/similar` | 유사 영화 | FR-11 |
| GET | `/movies/{movieId}/ott-offers` | OTT 제공처와 구독 우선 정렬 링크 | FR-16 |

`GET /movies` 후보 파라미터: `query`, `genreIds`, `countryCodes`, `releaseYearFrom`, `releaseYearTo`, `ottProviderIds`, `sort`, `cursor`, `limit`.

`GET /movies`, `GET /movies/{movieId}`, 유사 영화와 OTT 제공처 조회는 비회원에게도 공개한다. 사용자별 구독 우선 정렬처럼 개인화된 응답이 필요하면 선택적 인증을 적용한다.

### Viewing / Rating

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| POST | `/watch-intents` | OTT 이동 직전 링크 클릭 사실 기록 | FR-17 |
| GET | `/me/watch-intents/pending-confirmation` | 감상 확인이 필요한 항목 조회 | FR-18 |
| POST | `/watch-intents/{watchIntentId}/confirm-watched` | 실제 감상 사실을 확인하고 `WATCHED_UNRATED`로 전환 | FR-18, AR-06.1, AR-06.4 |
| POST | `/watch-intents/{watchIntentId}/reject` | 실제로 감상하지 않았다고 응답 | FR-18에서 상태 확정 필요 |
| GET | `/me/viewing-records/unrated` | 감상 확인 후 아직 평가하지 않은 영화 목록 | AR-06.1, AR-06.4 |
| GET | `/me/ratings` | 내 평가 목록 | FR-19, AR-06.1 |
| PUT | `/me/ratings/{movieId}` | 정수 1~5 평가 생성 또는 수정 | FR-19, AR-06.1~AR-06.2 |
| DELETE | `/me/ratings/{movieId}` | 평가와 파생 Frame·Popcorn 삭제 | AR-06.3~AR-06.5 |

감상 확인은 Rating 없이 `WATCHED_UNRATED` 상태를 만든다. 이후 정수 1~5 Rating을 처음 저장하면 `RATED`가 되고 `Frame`, `Popcorn`, 취향 집계를 함께 생성한다. Rating 삭제 시 해당 Frame·Popcorn과 취향 반영을 삭제하고 `WATCHED_UNRATED`로 돌아간다. `ViewingRecord`는 유지하며 추천 후보 필터는 Rating이 아니라 ViewingRecord를 기준으로 하므로 삭제한 영화가 다시 추천되지 않는다. 재평가하면 Frame·Popcorn을 다시 생성한다. 재감상은 별도 기록으로 관리하지 않는다.

### Film / Taste

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/me/film` | 현재 Rating 기반 내 프레임 목록과 필름 요약 | FR-07, AR-06.2~AR-06.3 |
| GET | `/me/film/frames/{frameId}` | 프레임 상세 | FR-08 |
| GET | `/me/taste-profile` | 요소별 취향 분석 | FR-04 |
| GET | `/me/taste-keywords` | 취향 키워드 3~5개 | FR-05 |
| GET | `/me/popcorn-bucket` | 현재 Rating 기반 맛별 수량·평균 평점·발견 상태 | FR-06, FR-09, AR-06.2~AR-06.3 |

팝콘 버킷 응답에서는 `count`와 `averageRating`을 별도 필드로 제공해야 한다. 팝콘 맛은 8개로 구성하되 실제 데이터 분석 후 맛 명칭과 장르 매핑을 확정하므로 응답은 고정 문자열 enum 대신 `flavorId`를 사용한다.

### Recommendation / XAI

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/me/recommendations` | 유지되는 결과 버전의 기본 추천 3개 | FR-10, FR-12, AR-06.5, AR-07.1~AR-07.3 |
| GET | `/me/recommendations/personalized` | 개인 맞춤 추천 | FR-10 |
| GET | `/me/recommendations/discovery` | 새로운 맛 발견 추천 | FR-12 |

기본 추천은 개인 맞춤 2개와 발견 1개로 구성한다. 발견 후보가 없으면 개인 맞춤 3개로 채운다. 각 항목은 `recommendationType: PERSONALIZED | DISCOVERY`를 필수로 포함한다. `ViewingRecord`가 있는 감상 영화는 Rating 삭제 여부와 무관하게 후보에서 제외한다. 미구독 OTT 영화는 제외하지 않는다. Rating 생성·수정·삭제 등 추천 입력이 바뀌지 않으면 같은 `resultVersion`과 세 영화를 반환하며 단순 재조회·화면 새로고침으로 교체하지 않는다.

세 후보가 모두 마음에 들지 않을 때의 후속 동작은 미정이다. 현재 `관심없음` 제외 정책을 그대로 적용하면 사용자는 추천 입력을 바꾸기 전까지 새 추천을 받을 수 없다. 무변경 종료, 부정 피드백을 저장하지 않는 `다른 추천 보기`, `관심없음` 재도입 중 하나를 결정하기 전에는 추가 추천·피드백 API를 확정하지 않는다.

### Party

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| POST | `/parties` | 최대 4명 파티 생성 | FR-20, AR-08.1 |
| GET | `/parties/{partyId}` | 파티와 구성원 조회 | FR-20 |
| GET | `/users/search?nickname={nickname}` | 닉네임으로 초대 대상 회원 조회 | AR-08.2, AR-09.3 |
| POST | `/parties/{partyId}/invitations` | 앱 내 파티 초대·재초대 생성 | FR-20, AR-08.2~AR-08.4 |
| GET | `/me/party-invitations` | 내가 받은 파티 초대 목록 | AR-08.3~AR-08.4 |
| POST | `/party-invitations/{invitationId}/accept` | 파티 초대 수락 | AR-08.3 |
| POST | `/party-invitations/{invitationId}/reject` | 파티 초대 거절 | AR-08.3~AR-08.4 |
| DELETE | `/parties/{partyId}/members/me` | 파티 나가기 및 활성 파티 종료 | AR-09.1 |
| POST | `/parties/{partyId}/end` | 파티장이 파티 명시 종료 | AR-09.1 |
| GET | `/parties/{partyId}/taste-analysis` | 공통 취향과 차이점 | FR-21 |
| GET | `/parties/{partyId}/recommendations` | 파티 영화 추천 | FR-22 |

파티 최대 인원은 파티장을 포함해 4명이며 초대와 수락은 앱 안에서 처리한다. 초대 요청은 닉네임 검색 결과의 `userId`를 사용한다. 초대는 만료되지 않으며 거절된 사용자에게 다시 초대할 수 있다. `RECRUITING` 중 멤버를 모으고, 최초 결과 생성 시 `ACTIVE`와 구성원 스냅샷을 고정한다. 이후 가입·탈퇴·강퇴·차단·회원 탈퇴로 구성이 바뀌면 현재 파티를 `ENDED`로 전환한다. 파티장 위임과 중복 참여 정책은 추가 합의가 필요하다.

## 3. 2차 MVP 엔드포인트 후보

### Report / Share

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/me/taste-reports` | 내 반기별 리포트 목록 | FR-23 |
| GET | `/me/taste-reports/{reportId}` | 리포트 상세 | FR-23 |
| POST | `/me/taste-reports/{reportId}/shares` | 외부 공유용 카드·링크 생성 | FR-24 |
| GET | `/shared/taste-reports/{shareToken}` | 공유 리포트 조회 | FR-24 |
| DELETE | `/me/taste-reports/{reportId}/shares/{shareId}` | 공유 중단 후보 | FR-24에서 정책 확인 필요 |
| GET | `/me/taste-reports/{reportId}/download` | PNG 리포트 단기 다운로드 URL 발급 | AR-12.1~AR-12.4 |

리포트는 요청 시 즉석 계산하기보다 반기 배치 산출물로 저장하고 API가 결과를 조회한다. 저장·공유 파일은 PNG 하나로 렌더링해 비공개 S3 호환 객체 스토리지에 저장하며 PDF는 생성하지 않는다. DB에는 `objectKey`, `contentType: image/png`, `size`, `checksum`, `generationStatus`를 둔다. 저장 구현은 MinIO와 관리형 객체 저장소를 바꿀 수 있도록 S3 호환 인터페이스 뒤에 둔다. 다운로드 API는 짧은 유효기간의 URL을 반환하며 정확한 시간은 운영 보안 정책에서 정한다. 공유 링크 토큰은 앱 밖 사용자가 로그인 없이 보는 용도로 서버에 저장하며 `expiresAt = createdAt + 1개월` 뒤 조회할 수 없다. 반기 리포트에 포함할 상세 내용은 아직 미정이다.

### Social / Public Taste

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/me/privacy-settings` | 내 프로필 공개 설정 조회 | AR-04.5, AR-11.1 |
| PUT | `/me/privacy-settings` | 프로필·평가·필름·팝콘 묶음 공개 범위 설정 | AR-04.5, AR-11.1 |
| GET | `/users/{userId}` | 공개 범위가 적용된 사용자 프로필 조회 | AR-11.2 |
| GET | `/users/{userId}/ratings` | 공개 사용자의 평가 목록 조회 | AR-11.3, AR-09.5 |
| GET | `/users/{userId}/film` | 공개 사용자의 필름 조회 | FR-25, AR-11.4, AR-09.4 |
| GET | `/users/{userId}/popcorn-bucket` | 공개 사용자의 팝콘 버킷 조회 | FR-25, AR-11.5, AR-09.4 |
| GET | `/me/taste-comparisons/{targetUserId}` | 나와 공개 사용자의 취향 비교 | FR-25, AR-11.6, AR-09.4 |

앱 내 다른 사용자 진입점은 한줄평·댓글 작성자의 프로필 클릭을 우선 사용한다. `privacy-settings`의 `profileVisibility`는 `PRIVATE/PUBLIC`이며 기본값은 `PRIVATE`다. `PUBLIC`이면 비회원도 프로필·평가·필름·팝콘을 조회할 수 있다. 취향 비교는 로그인 회원만 실행할 수 있고 대상 사용자가 `PUBLIC`이어야 한다.

### User Block

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/me/blocked-users` | 내가 차단한 사용자 목록 | AR-09.2 |
| POST | `/me/blocked-users/{userId}` | 사용자 차단 | AR-09.2~AR-09.5 |
| DELETE | `/me/blocked-users/{userId}` | 사용자 차단 해제 | AR-09.2 |

차단 관계에서는 서로를 닉네임 검색과 초대 후보에서 제외하고 로그인 상태의 프로필·필름·팝콘 접근을 막는다. 영화 상세 응답에 평가·한줄평 등 작성자 콘텐츠가 포함되거나 별도 목록 API를 호출할 때도 차단 관계의 작성자를 필터링한다. 차단으로 활성 파티 구성이 달라지면 그 파티를 종료한다.

### Short Review

| Method | Path | 목적 | 요구사항 |
|---|---|---|---|
| GET | `/movies/{movieId}/short-reviews` | 차단 작성자를 제외한 영화별 한줄평 목록 | FR-26, AR-09.5 |
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
- `FrameDeleted`
- `PopcornDeleted`
- `TasteProfileUpdated`
- `PartyMembershipChanged`
- `PartyInvitationCreated`
- `PartyInvitationResponded`
- `PartyEnded`
- `UserBlocked`
- `UserUnblocked`
- `RecommendationCandidatesGenerated`
- `TasteReportGenerated`
- `TasteReportShared`
- `ShortReviewUpserted`
- `TrendSnapshotGenerated`

`ViewingConfirmed`는 감상 사실과 `WATCHED_UNRATED`만 기록하고 `RatingUpserted`가 처음 발생할 때 Frame·Popcorn·취향 갱신을 시작한다. `RatingDeleted`는 같은 트랜잭션 또는 멱등 비동기 처리로 Frame·Popcorn과 취향 반영을 제거하되 ViewingRecord는 유지한다. 추천 후보 제외는 ViewingRecord를 기준으로 한다. 중복 생성·삭제를 막기 위한 멱등 키와 사용자에게 읽기 모델이 보이는 시점을 명확히 해야 한다.

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
