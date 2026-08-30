INSERT INTO user_behavior_event (
    event_id, actor_user_id, event_type, resource_type, resource_id,
    occurred_at, trace_id, schema_version, payload
) VALUES (
    'c2020000-0000-4000-8000-000000000001',
    '018f6826-4da1-7c38-a846-8f794cd8b0cf',
    'RATING_UPDATED', 'RATING', '0527c943-fb46-4aa5-aea2-130bdc752e75',
    '2026-08-29T05:10:00Z', 'local-compose-c2-outbox-fixture', 1,
    '{"movieId":"6b226903-0ca4-4f5a-9bf0-50d6cedd224c","ratingRevision":2}'::jsonb
);

INSERT INTO domain_outbox (
    event_id, aggregate_type, aggregate_id, event_type, schema_version,
    payload, occurred_at, status, attempt_count
) VALUES (
    'c2020000-0000-4000-8000-000000000001',
    'RATING', '0527c943-fb46-4aa5-aea2-130bdc752e75', 'RATING_UPDATED', 1,
    '{"movieId":"6b226903-0ca4-4f5a-9bf0-50d6cedd224c","ratingRevision":2}'::jsonb,
    '2026-08-29T05:10:00Z', 'PENDING', 0
);
