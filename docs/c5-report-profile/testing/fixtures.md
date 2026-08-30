# C5 deterministic local fixtures

- actor: C4 local owner `018f6826-4da1-7c38-a846-8f794cd8b0cf`
- current clock: `2026-08-29T12:00:00Z` (`2026-08-29 21:00 Asia/Seoul`)
- eligible period: `2026-01-01..2026-06-30`; report create 후 revision 1
- source: C1 V101 viewing/rating/frame/popcorn rows; 없는 활동은 `EMPTY_NO_ACTIVITY`
- privacy: PROFILE/FILM/POPCORN 모두 PRIVATE, revision 1
- notification: WATCH_CONFIRMATION_DUE OFF, revision 1
- share/export row는 seed하지 않고 E2E가 생성한다.

fixture에 raw share/session token, password, 실제 email, 예상 별점, 만족도, taste diagnosis를 넣지 않는다.

