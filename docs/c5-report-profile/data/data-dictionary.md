# C5 local data dictionary

| Entity | 필수 field | security·retention 불변식 |
| --- | --- | --- |
| `TASTE_REPORT_REVISION` | id, owner_id, period_start/end, revision, status, source_watermark, payload, created_at | immutable; factual allowlist only; superseded 400d local fixture |
| `TASTE_REPORT_PERIOD_ITEM` | report_id, position, movie_id, viewing_source_id/revision, rating_snapshot | report/position·source unique; actual period 전체 |
| `REPORT_EXPORT_JOB` | id, owner_id, report_id, status, attempts, created/expires | attempts≤3; owner only |
| `REPORT_EXPORT_ARTIFACT` | job_id, opaque_path, sha256, size, expires | local path는 API/log 비공개; 24h 이내 삭제 |
| `USER_PRIVACY_SETTING` | owner_id, public_profile_id, resource, visibility, revision | PROFILE/FILM/POPCORN exactly 3; row 없음=PRIVATE |
| `REPORT_SHARE_GRANT` | id, owner_id, report_id, token_sha256, status, expires | raw token 저장 금지; owner active≤3 |
| `REPORT_SHARE_VIEWER_SESSION` | id, grant_id, session_sha256, status, expires | report-only; raw session 저장 금지; 15m |
| `USER_NOTIFICATION_SETTING` | owner_id, watch_confirmation_due_enabled, revision | default false; external channel field 없음 |
| `IN_APP_NOTIFICATION` | id, owner_id, category, source_type/id/revision, state, created/expires | source tuple unique; unread 30d, terminal 7d |
| `C5_IDEMPOTENCY_RESULT` | actor_id, operation, key, request_sha256, response | raw request/token 없음; same body replay only |

금지 column/JSON key: `expectedStar`, `satisfaction`, `tasteDiagnosis`, `tasteComparison`, raw token/password/email,
external provider delivery identifier.

