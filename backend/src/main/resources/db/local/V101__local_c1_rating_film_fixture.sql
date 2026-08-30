INSERT INTO movie_flavor_assignment (
    mapping_version, movie_id, flavor_id, assignment_source, source_genre_id, source_display_order, assigned_at
) VALUES
    ('v1', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '18828763-1fd7-4ee4-a97f-1496db3c6490', 'PRIMARY_TMDB_GENRE', 80, 0, '2026-08-29T05:00:00Z'),
    ('v1', '19406c31-213f-4fe1-93f6-109f8570ec20', '50fb6f76-9ab2-4bb4-9a62-7f8b76af9822', 'PRIMARY_TMDB_GENRE', 18, 0, '2026-08-29T05:00:00Z'),
    ('v1', 'e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490', '50fb6f76-9ab2-4bb4-9a62-7f8b76af9822', 'PRIMARY_TMDB_GENRE', 18, 0, '2026-08-29T05:00:00Z'),
    ('v1', '1958ba3a-3d8c-4a4f-8845-124c0b12373e', '18828763-1fd7-4ee4-a97f-1496db3c6490', 'PRIMARY_TMDB_GENRE', 53, 0, '2026-08-29T05:00:00Z'),
    ('v1', '0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89', '18828763-1fd7-4ee4-a97f-1496db3c6490', 'PRIMARY_TMDB_GENRE', 53, 0, '2026-08-29T05:00:00Z'),
    ('v1', 'e67778c9-7b2e-42d4-9d3e-a3026b2efea3', '18828763-1fd7-4ee4-a97f-1496db3c6490', 'PRIMARY_TMDB_GENRE', 80, 0, '2026-08-29T05:00:00Z'),
    ('v1', 'cc3ddb45-0511-46ea-bf28-95b67c9fd20f', '18828763-1fd7-4ee4-a97f-1496db3c6490', 'PRIMARY_TMDB_GENRE', 80, 0, '2026-08-29T05:00:00Z');

INSERT INTO watch_intent (
    id, user_id, movie_id, provider_id, source_offer_id, status,
    clicked_at, confirmation_due_at, expires_at, responded_at, revision
) VALUES
    ('541bf21a-b9ef-40b4-ad74-c56084a99095', '018f6826-4da1-7c38-a846-8f794cd8b0cf',
     '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'd392a4d5-0428-4e06-aa41-aef899c06842',
     '4c411f48-9990-4938-9f6c-cf17b42ce4cb', 'CONFIRMED_WATCHED',
     '2026-08-20T10:00:00Z', '2026-08-22T10:00:00Z', '2026-08-27T10:00:00Z', '2026-08-23T10:00:00Z', 2),
    ('61970d1b-a3cc-4cd3-91cf-4df404211ff2', '018f6826-4da1-7c38-a846-8f794cd8b0cf',
     '19406c31-213f-4fe1-93f6-109f8570ec20', '4f57022d-6d8e-40b2-b7be-4ac313ef6bd0',
     '780702d1-a92d-4f78-9d0c-f327748b6281', 'CONFIRMED_WATCHED',
     '2026-08-20T11:00:00Z', '2026-08-22T11:00:00Z', '2026-08-27T11:00:00Z', '2026-08-23T11:00:00Z', 2),
    ('2dfa8b82-9f40-452d-a63f-18347483f7b7', '018f6826-4da1-7c38-a846-8f794cd8b0cf',
     'e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490', 'd392a4d5-0428-4e06-aa41-aef899c06842',
     NULL, 'CONFIRMATION_PENDING',
     '2026-08-27T11:00:00Z', '2026-08-29T11:00:00Z', '2026-09-03T11:00:00Z', NULL, 1),
    ('8b7f4a21-4bc4-4c5e-93cb-4e348abcae02', '018f6826-4da1-7c38-a846-8f794cd8b0cf',
     '1958ba3a-3d8c-4a4f-8845-124c0b12373e', 'd392a4d5-0428-4e06-aa41-aef899c06842',
     NULL, 'CONFIRMATION_PENDING',
     '2026-08-27T10:30:00Z', '2026-08-29T10:30:00Z', '2026-09-03T10:30:00Z', NULL, 1),
    ('aef9c2be-1e46-4778-8c6c-9873989fd672', '5f93a51d-a6f1-41dc-8d86-6b570d53bd82',
     '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'd392a4d5-0428-4e06-aa41-aef899c06842',
     '4c411f48-9990-4938-9f6c-cf17b42ce4cb', 'CONFIRMED_NOT_WATCHED',
     '2026-08-19T10:00:00Z', '2026-08-21T10:00:00Z', '2026-08-26T10:00:00Z', '2026-08-22T10:00:00Z', 2);

INSERT INTO viewing_record (
    id, user_id, movie_id, source_watch_intent_id, provider_id, status, watched_confirmed_at, revision
) VALUES
    ('54eb733a-80e6-475d-aeef-e16b165d3215', '018f6826-4da1-7c38-a846-8f794cd8b0cf',
     '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '541bf21a-b9ef-40b4-ad74-c56084a99095',
     'd392a4d5-0428-4e06-aa41-aef899c06842', 'RATED_COMPLETED', '2026-08-23T10:00:00Z', 2),
    ('531a4e1d-2da8-48f1-a702-79fd875793d3', '018f6826-4da1-7c38-a846-8f794cd8b0cf',
     '19406c31-213f-4fe1-93f6-109f8570ec20', '61970d1b-a3cc-4cd3-91cf-4df404211ff2',
     '4f57022d-6d8e-40b2-b7be-4ac313ef6bd0', 'WATCHED_CONFIRMED', '2026-08-23T11:00:00Z', 1);

INSERT INTO rating (
    id, user_id, movie_id, viewing_record_id, value, logical_status, revision,
    created_at, updated_at, deleted_at, deletion_trace_id
) VALUES (
    '0527c943-fb46-4aa5-aea2-130bdc752e75', '018f6826-4da1-7c38-a846-8f794cd8b0cf',
    '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '54eb733a-80e6-475d-aeef-e16b165d3215',
    4, 'ACTIVE', 2, '2026-08-23T10:05:00Z', '2026-08-24T10:05:00Z', NULL, NULL
);

INSERT INTO frame (
    id, user_id, movie_id, viewing_record_id, rating_id, derivation_version, created_at, updated_at
) VALUES (
    '2b480314-590c-4d9a-b5df-1ef745c15e76', '018f6826-4da1-7c38-a846-8f794cd8b0cf',
    '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '54eb733a-80e6-475d-aeef-e16b165d3215',
    '0527c943-fb46-4aa5-aea2-130bdc752e75', 'c1-v1', '2026-08-23T10:05:00Z', '2026-08-24T10:05:00Z'
);

INSERT INTO popcorn (
    id, user_id, frame_id, rating_id, flavor_id, flavor_mapping_version, created_at
) VALUES (
    '6de3b230-3c32-4917-a9d7-f18c9c0ab79b', '018f6826-4da1-7c38-a846-8f794cd8b0cf',
    '2b480314-590c-4d9a-b5df-1ef745c15e76', '0527c943-fb46-4aa5-aea2-130bdc752e75',
    '18828763-1fd7-4ee4-a97f-1496db3c6490', 'v1', '2026-08-23T10:05:00Z'
);

INSERT INTO flavor_aggregate (
    user_id, flavor_id, popcorn_count, rating_count, rating_sum, revision, updated_at
) VALUES (
    '018f6826-4da1-7c38-a846-8f794cd8b0cf', '18828763-1fd7-4ee4-a97f-1496db3c6490',
    1, 1, 4, 1, '2026-08-24T10:05:00Z'
);

INSERT INTO rating_taste_contribution (
    rating_id, dimension_type, dimension_key, rating_value,
    catalog_version_id, flavor_mapping_version, derivation_version
) VALUES
    ('0527c943-fb46-4aa5-aea2-130bdc752e75', 'GENRE', '2d07d5d3-486f-4638-9d58-49331e798c76', 4,
     '10000000-0000-0000-0000-000000000002', 'v1', 'c1-v1'),
    ('0527c943-fb46-4aa5-aea2-130bdc752e75', 'COUNTRY', 'US', 4,
     '10000000-0000-0000-0000-000000000002', 'v1', 'c1-v1'),
    ('0527c943-fb46-4aa5-aea2-130bdc752e75', 'DIRECTOR', '88bc6285-b82b-491d-9cae-ab17c3d7a9cf', 4,
     '10000000-0000-0000-0000-000000000002', 'v1', 'c1-v1');

INSERT INTO taste_aggregate (
    user_id, dimension_type, dimension_key, rating_count, rating_sum, revision, updated_at
) VALUES
    ('018f6826-4da1-7c38-a846-8f794cd8b0cf', 'GENRE', '2d07d5d3-486f-4638-9d58-49331e798c76', 1, 4, 1, '2026-08-24T10:05:00Z'),
    ('018f6826-4da1-7c38-a846-8f794cd8b0cf', 'COUNTRY', 'US', 1, 4, 1, '2026-08-24T10:05:00Z'),
    ('018f6826-4da1-7c38-a846-8f794cd8b0cf', 'DIRECTOR', '88bc6285-b82b-491d-9cae-ab17c3d7a9cf', 1, 4, 1, '2026-08-24T10:05:00Z');
