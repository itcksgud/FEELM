INSERT INTO catalog_sync_run (
    id, job_type, status, started_at, finished_at, source_version, metrics
) VALUES (
    '10000000-0000-0000-0000-000000000001', 'LOCAL_FIXTURE', 'SUCCEEDED',
    '2026-08-29T04:00:00Z', '2026-08-29T05:00:00Z', 'fixture-v1', '{"movieCount": 8}'::jsonb
);

INSERT INTO catalog_version (id, public_version, sync_run_id, status, published_at, source_hash) VALUES (
    '10000000-0000-0000-0000-000000000002', 'catalog-fixture-20260829-01',
    '10000000-0000-0000-0000-000000000001', 'ACTIVE', '2026-08-29T05:00:00Z', 'local-fixture-v1'
);

INSERT INTO movie_identity (id, created_at) VALUES
    ('6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '2026-08-29T04:00:00Z'),
    ('19406c31-213f-4fe1-93f6-109f8570ec20', '2026-08-29T04:00:00Z'),
    ('97204ea5-e6e5-4417-a13f-bc8197660705', '2026-08-29T04:00:00Z'),
    ('e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490', '2026-08-29T04:00:00Z'),
    ('1958ba3a-3d8c-4a4f-8845-124c0b12373e', '2026-08-29T04:00:00Z'),
    ('0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89', '2026-08-29T04:00:00Z'),
    ('e67778c9-7b2e-42d4-9d3e-a3026b2efea3', '2026-08-29T04:00:00Z'),
    ('cc3ddb45-0511-46ea-bf28-95b67c9fd20f', '2026-08-29T04:00:00Z');

INSERT INTO movie_external_id (movie_id, source, external_id, verification_status, verified_at) VALUES
    ('6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'TMDB', '75656', 'VERIFIED', '2026-08-29T04:00:00Z'),
    ('19406c31-213f-4fe1-93f6-109f8570ec20', 'TMDB', '900001', 'VERIFIED', '2026-08-29T04:00:00Z'),
    ('97204ea5-e6e5-4417-a13f-bc8197660705', 'TMDB', '900002', 'VERIFIED', '2026-08-29T04:00:00Z'),
    ('e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490', 'TMDB', '900003', 'VERIFIED', '2026-08-29T04:00:00Z'),
    ('1958ba3a-3d8c-4a4f-8845-124c0b12373e', 'TMDB', '900004', 'VERIFIED', '2026-08-29T04:00:00Z'),
    ('0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89', 'TMDB', '900005', 'VERIFIED', '2026-08-29T04:00:00Z'),
    ('e67778c9-7b2e-42d4-9d3e-a3026b2efea3', 'TMDB', '900006', 'VERIFIED', '2026-08-29T04:00:00Z'),
    ('cc3ddb45-0511-46ea-bf28-95b67c9fd20f', 'TMDB', '900007', 'VERIFIED', '2026-08-29T04:00:00Z');

INSERT INTO movie_catalog_projection (
    catalog_version_id, movie_id, media_type, identity_status, visibility_status,
    original_title, original_language, release_date, runtime_minutes, poster_path, backdrop_path,
    tmdb_vote_average, tmdb_vote_count, metadata_fetched_at, deleted
) VALUES
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'MOVIE', 'IDENTITY_VERIFIED', 'UI_READY',
     'Now You See Me', 'en', '2013-05-29', 115, '/now-you-see-me.jpg', '/backdrop-now-you-see-me.jpg', 7.30, 12800, '2026-08-29T05:00:00Z', false),
    ('10000000-0000-0000-0000-000000000002', '19406c31-213f-4fe1-93f6-109f8570ec20', 'MOVIE', 'IDENTITY_VERIFIED', 'UI_READY',
     'The English Fallback', 'en', '2018-03-01', 102, '/en-fallback.jpg', '/backdrop-en.jpg', 7.10, 5000, '2026-08-29T05:00:00Z', false),
    ('10000000-0000-0000-0000-000000000002', '97204ea5-e6e5-4417-a13f-bc8197660705', 'MOVIE', 'IDENTITY_VERIFIED', 'CATALOG_VISIBLE',
     'No Poster Movie', 'en', '2012-07-10', 95, NULL, '/backdrop-no-poster.jpg', 6.80, 1200, '2026-08-29T05:00:00Z', false),
    ('10000000-0000-0000-0000-000000000002', 'e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490', 'MOVIE', 'IDENTITY_VERIFIED', 'UI_READY',
     'Nothing Listed', 'en', '2020-01-01', 99, '/none-listed.jpg', '/backdrop-none.jpg', 6.90, 2200, '2026-08-29T05:00:00Z', false),
    ('10000000-0000-0000-0000-000000000002', '1958ba3a-3d8c-4a4f-8845-124c0b12373e', 'MOVIE', 'IDENTITY_VERIFIED', 'UI_READY',
     'OTT Unknown', 'en', '2021-02-02', 101, '/unknown.jpg', '/backdrop-unknown.jpg', 7.00, 1800, '2026-08-29T05:00:00Z', false),
    ('10000000-0000-0000-0000-000000000002', '0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89', 'MOVIE', 'IDENTITY_VERIFIED', 'UI_READY',
     'Stale OTT', 'en', '2019-04-03', 105, '/stale.jpg', '/backdrop-stale.jpg', 7.20, 3100, '2026-08-29T05:00:00Z', false),
    ('10000000-0000-0000-0000-000000000002', 'e67778c9-7b2e-42d4-9d3e-a3026b2efea3', 'MOVIE', 'IDENTITY_VERIFIED', 'UI_READY',
     'Inside Man', 'en', '2006-03-24', 129, '/similar-1.jpg', '/backdrop-similar-1.jpg', 7.40, 8000, '2026-08-29T05:00:00Z', false),
    ('10000000-0000-0000-0000-000000000002', 'cc3ddb45-0511-46ea-bf28-95b67c9fd20f', 'MOVIE', 'IDENTITY_VERIFIED', 'UI_READY',
     'The Prestige', 'en', '2006-10-20', 130, '/similar-2.jpg', '/backdrop-similar-2.jpg', 8.10, 14000, '2026-08-29T05:00:00Z', false);

INSERT INTO movie_localization (catalog_version_id, movie_id, locale, title, overview, source, fetched_at) VALUES
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'ko-KR', '나우 유 씨 미', '마술사들이 펼치는 완벽한 범죄.', 'TMDB', '2026-08-29T05:00:00Z'),
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'en-US', 'Now You See Me', 'A team of illusionists pull off an impossible heist.', 'TMDB', '2026-08-29T05:00:00Z'),
    ('10000000-0000-0000-0000-000000000002', '19406c31-213f-4fe1-93f6-109f8570ec20', 'en-US', 'The English Fallback', 'English overview fallback.', 'TMDB', '2026-08-29T05:00:00Z'),
    ('10000000-0000-0000-0000-000000000002', '97204ea5-e6e5-4417-a13f-bc8197660705', 'ko-KR', '포스터 없는 영화', '포스터가 없어도 상세를 볼 수 있다.', 'TMDB', '2026-08-29T05:00:00Z'),
    ('10000000-0000-0000-0000-000000000002', 'e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490', 'ko-KR', '현재 제공처 없음', '최근 성공 조회에 제공처가 없다.', 'TMDB', '2026-08-29T05:00:00Z'),
    ('10000000-0000-0000-0000-000000000002', '1958ba3a-3d8c-4a4f-8845-124c0b12373e', 'ko-KR', '시청 옵션 미확인', '성공한 시청 옵션 스냅샷이 없다.', 'TMDB', '2026-08-29T05:00:00Z'),
    ('10000000-0000-0000-0000-000000000002', '0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89', 'ko-KR', '오래된 시청 옵션', '마지막 정상 데이터를 제한적으로 제공한다.', 'TMDB', '2026-08-29T05:00:00Z'),
    ('10000000-0000-0000-0000-000000000002', 'e67778c9-7b2e-42d4-9d3e-a3026b2efea3', 'ko-KR', '인사이드 맨', '범죄의 이면을 파고드는 영화.', 'TMDB', '2026-08-29T05:00:00Z'),
    ('10000000-0000-0000-0000-000000000002', 'cc3ddb45-0511-46ea-bf28-95b67c9fd20f', 'ko-KR', '프레스티지', '두 마술사의 집요한 대결.', 'TMDB', '2026-08-29T05:00:00Z');

INSERT INTO genre (id, code, display_name_ko, display_order, active) VALUES
    ('2d07d5d3-486f-4638-9d58-49331e798c76', 'CRIME', '범죄', 10, true),
    ('475dc158-d914-46ec-a59c-a48791e6ae8f', 'THRILLER', '스릴러', 20, true),
    ('165a3c6f-9b81-4420-9713-c59303d5bb92', 'DRAMA', '드라마', 30, true);

INSERT INTO country (code, display_name_ko, display_name_en) VALUES
    ('US', '미국', 'United States'), ('FR', '프랑스', 'France'), ('KR', '대한민국', 'South Korea');

INSERT INTO movie_genre (catalog_version_id, movie_id, genre_id, display_order) VALUES
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '2d07d5d3-486f-4638-9d58-49331e798c76', 0),
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '475dc158-d914-46ec-a59c-a48791e6ae8f', 1),
    ('10000000-0000-0000-0000-000000000002', '19406c31-213f-4fe1-93f6-109f8570ec20', '165a3c6f-9b81-4420-9713-c59303d5bb92', 0),
    ('10000000-0000-0000-0000-000000000002', '97204ea5-e6e5-4417-a13f-bc8197660705', '165a3c6f-9b81-4420-9713-c59303d5bb92', 0),
    ('10000000-0000-0000-0000-000000000002', 'e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490', '165a3c6f-9b81-4420-9713-c59303d5bb92', 0),
    ('10000000-0000-0000-0000-000000000002', '1958ba3a-3d8c-4a4f-8845-124c0b12373e', '475dc158-d914-46ec-a59c-a48791e6ae8f', 0),
    ('10000000-0000-0000-0000-000000000002', '0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89', '475dc158-d914-46ec-a59c-a48791e6ae8f', 0),
    ('10000000-0000-0000-0000-000000000002', 'e67778c9-7b2e-42d4-9d3e-a3026b2efea3', '2d07d5d3-486f-4638-9d58-49331e798c76', 0),
    ('10000000-0000-0000-0000-000000000002', 'cc3ddb45-0511-46ea-bf28-95b67c9fd20f', '2d07d5d3-486f-4638-9d58-49331e798c76', 0);

INSERT INTO movie_country (catalog_version_id, movie_id, country_code, display_order)
SELECT '10000000-0000-0000-0000-000000000002', id, 'US', 0
  FROM movie_identity
 WHERE id IN (
    '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '19406c31-213f-4fe1-93f6-109f8570ec20',
    '1958ba3a-3d8c-4a4f-8845-124c0b12373e', '0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89',
    'e67778c9-7b2e-42d4-9d3e-a3026b2efea3', 'cc3ddb45-0511-46ea-bf28-95b67c9fd20f'
 );
INSERT INTO movie_country VALUES
    ('10000000-0000-0000-0000-000000000002', '97204ea5-e6e5-4417-a13f-bc8197660705', 'KR', 0),
    ('10000000-0000-0000-0000-000000000002', 'e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490', 'KR', 0),
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'FR', 1);

INSERT INTO person (id, tmdb_person_id, display_name) VALUES
    ('88bc6285-b82b-491d-9cae-ab17c3d7a9cf', 9340, 'Louis Leterrier'),
    ('336ef1c3-2df8-4c24-9139-58beac956ad4', 44735, 'Jesse Eisenberg'),
    ('aa60da55-46b0-4e51-a604-75e54b73d711', 103, 'Mark Ruffalo'),
    ('1e6d9d1a-2c83-4498-a7bb-f1e31b93dbd2', 999999, 'Fixture Director');

INSERT INTO movie_credit (
    catalog_version_id, movie_id, person_id, credit_type, job, character_name, credit_order
) VALUES
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '88bc6285-b82b-491d-9cae-ab17c3d7a9cf', 'DIRECTOR', 'Director', '', 0),
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', '336ef1c3-2df8-4c24-9139-58beac956ad4', 'CAST', '', 'J. Daniel Atlas', 0),
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'aa60da55-46b0-4e51-a604-75e54b73d711', 'CAST', '', 'Dylan Rhodes', 1);
INSERT INTO movie_credit
SELECT '10000000-0000-0000-0000-000000000002', p.movie_id,
       '1e6d9d1a-2c83-4498-a7bb-f1e31b93dbd2', 'DIRECTOR', 'Director', '', 0
  FROM movie_catalog_projection p
 WHERE p.catalog_version_id = '10000000-0000-0000-0000-000000000002'
   AND p.movie_id <> '6b226903-0ca4-4f5a-9bf0-50d6cedd224c';

INSERT INTO movie_search_document (
    catalog_version_id, movie_id, normalized_title_terms, normalized_person_terms, search_vector,
    popularity_score, built_at
)
SELECT p.catalog_version_id, p.movie_id,
       lower(concat_ws(' ', p.original_title, l.title)),
       CASE WHEN p.movie_id = '6b226903-0ca4-4f5a-9bf0-50d6cedd224c'
            THEN 'louis leterrier jesse eisenberg mark ruffalo' ELSE 'fixture director' END,
       to_tsvector('simple', lower(concat_ws(' ', p.original_title, l.title))),
       p.tmdb_vote_count::numeric,
       '2026-08-29T05:00:00Z'
  FROM movie_catalog_projection p
  JOIN LATERAL (
      SELECT title FROM movie_localization ml
       WHERE ml.catalog_version_id = p.catalog_version_id AND ml.movie_id = p.movie_id
       ORDER BY CASE ml.locale WHEN 'ko-KR' THEN 0 WHEN 'en-US' THEN 1 ELSE 2 END
       LIMIT 1
  ) l ON true
 WHERE p.catalog_version_id = '10000000-0000-0000-0000-000000000002';

INSERT INTO ott_provider (
    id, tmdb_provider_id, provider_code, display_name, logo_path, display_priority, active
) VALUES
    ('d392a4d5-0428-4e06-aa41-aef899c06842', 8, 'NETFLIX', 'Netflix', '/netflix.jpg', 10, true),
    ('4f57022d-6d8e-40b2-b7be-4ac313ef6bd0', 97, 'WATCHA', 'Watcha', '/watcha.jpg', 20, true),
    ('1f0c5888-f6f4-42a9-b661-a90cff45e303', 356, 'WAVVE', 'wavve', '/wavve.jpg', 30, true),
    ('7012659c-f25e-429b-9fda-21528dc6cd1b', 3, 'GOOGLE_PLAY', 'Google Play Movies', '/google-play.jpg', 40, true);

INSERT INTO movie_availability_snapshot (
    id, catalog_version_id, movie_id, region, fetch_status, source, aggregator_url,
    fetched_at, fresh_until, serve_until
) VALUES
    ('20000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'KR', 'SUCCESS_LISTED', 'TMDB_JUSTWATCH', 'https://www.themoviedb.org/movie/75656/watch', '2026-08-29T06:00:00Z', '2026-08-30T06:00:00Z', '2026-09-05T06:00:00Z'),
    ('20000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000002', '19406c31-213f-4fe1-93f6-109f8570ec20', 'KR', 'SUCCESS_LISTED', 'TMDB_JUSTWATCH', 'https://www.themoviedb.org/movie/900001/watch', '2026-08-29T06:00:00Z', '2026-08-30T06:00:00Z', '2026-09-05T06:00:00Z'),
    ('20000000-0000-0000-0000-000000000003', '10000000-0000-0000-0000-000000000002', 'e8f7cf02-9bc4-4ff7-87b7-12fb02dd2490', 'KR', 'SUCCESS_EMPTY', 'TMDB_JUSTWATCH', NULL, '2026-08-29T06:00:00Z', '2026-08-30T06:00:00Z', '2026-09-05T06:00:00Z'),
    ('20000000-0000-0000-0000-000000000004', '10000000-0000-0000-0000-000000000002', '0437c1c0-06d5-4cdf-a7d1-5d5f1dc42e89', 'KR', 'SUCCESS_LISTED', 'TMDB_JUSTWATCH', 'https://www.themoviedb.org/movie/900005/watch', '2026-08-26T12:00:00Z', '2026-08-27T12:00:00Z', '2026-09-02T12:00:00Z');

INSERT INTO movie_ott_offer (
    id, snapshot_id, provider_id, monetization_type, link_type, landing_url, source_display_priority
) VALUES
    ('4c411f48-9990-4938-9f6c-cf17b42ce4cb', '20000000-0000-0000-0000-000000000001', 'd392a4d5-0428-4e06-aa41-aef899c06842', 'FLATRATE', 'AGGREGATOR', 'https://www.themoviedb.org/movie/75656/watch', 10),
    ('82d84bfc-a318-4dd6-9c22-fd84945ac88a', '20000000-0000-0000-0000-000000000001', '1f0c5888-f6f4-42a9-b661-a90cff45e303', 'FLATRATE', 'AGGREGATOR', 'https://www.themoviedb.org/movie/75656/watch', 30),
    ('5e779354-bc51-43c4-abbe-e80063301098', '20000000-0000-0000-0000-000000000001', '7012659c-f25e-429b-9fda-21528dc6cd1b', 'RENT', 'AGGREGATOR', 'https://www.themoviedb.org/movie/75656/watch', 40),
    ('780702d1-a92d-4f78-9d0c-f327748b6281', '20000000-0000-0000-0000-000000000002', '4f57022d-6d8e-40b2-b7be-4ac313ef6bd0', 'FLATRATE', 'AGGREGATOR', 'https://www.themoviedb.org/movie/900001/watch', 20),
    ('afaa874e-20d0-42de-a143-f89ee8f706d5', '20000000-0000-0000-0000-000000000004', 'd392a4d5-0428-4e06-aa41-aef899c06842', 'FLATRATE', 'AGGREGATOR', 'https://www.themoviedb.org/movie/900005/watch', 10);

INSERT INTO movie_similarity (
    catalog_version_id, source_movie_id, target_movie_id, similarity_version, rank, score, reasons, generated_at
) VALUES
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'e67778c9-7b2e-42d4-9d3e-a3026b2efea3', 'sim-fixture-v1', 1, 0.91, '[{"code":"SHARED_GENRE","label":"같은 범죄 장르"},{"code":"SHARED_DIRECTOR","label":"같은 감독"}]', '2026-08-29T05:00:00Z'),
    ('10000000-0000-0000-0000-000000000002', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 'cc3ddb45-0511-46ea-bf28-95b67c9fd20f', 'sim-fixture-v1', 2, 0.82, '[{"code":"SHARED_GENRE","label":"같은 범죄 장르"},{"code":"SHARED_KEYWORD","label":"마술 소재"}]', '2026-08-29T05:00:00Z');
