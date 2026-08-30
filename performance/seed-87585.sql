\set ON_ERROR_STOP on

BEGIN;

INSERT INTO genre (id, code, display_name_ko, display_order, active)
VALUES ('81000000-0000-0000-0000-000000000001', 'PERFORMANCE', '성능 장르', 0, true);

INSERT INTO movie_identity (id, created_at)
SELECT ('82000000-0000-0000-0000-' || lpad(to_hex(n), 12, '0'))::uuid,
       '2026-08-29T00:00:00Z'::timestamptz
  FROM generate_series(1, 87585) AS generated(n);

INSERT INTO catalog_version (id, public_version, status, published_at, source_hash)
VALUES (
    '80000000-0000-0000-0000-000000000001',
    'catalog-performance-87585-v1',
    'STAGING',
    NULL,
    'performance-generate-series-87585-v1'
);

INSERT INTO movie_catalog_projection (
    catalog_version_id,
    movie_id,
    media_type,
    identity_status,
    visibility_status,
    original_title,
    original_language,
    release_date,
    runtime_minutes,
    poster_path,
    backdrop_path,
    tmdb_vote_average,
    tmdb_vote_count,
    metadata_fetched_at,
    deleted
)
SELECT
    '80000000-0000-0000-0000-000000000001'::uuid,
    ('82000000-0000-0000-0000-' || lpad(to_hex(n), 12, '0'))::uuid,
    'MOVIE',
    'IDENTITY_VERIFIED',
    'UI_READY',
    'Performance Movie ' || lpad(n::text, 6, '0'),
    'en',
    make_date(1950 + (n % 75), 1 + (n % 12), 1 + (n % 28)),
    80 + (n % 81),
    '/performance/' || n || '.jpg',
    '/performance/backdrop/' || n || '.jpg',
    round((5 + (n % 50) / 10.0)::numeric, 2),
    100000 - n,
    '2026-08-29T00:00:00Z'::timestamptz,
    false
  FROM generate_series(1, 87585) AS generated(n);

INSERT INTO movie_localization (
    catalog_version_id, movie_id, locale, title, overview, source, fetched_at
)
SELECT
    '80000000-0000-0000-0000-000000000001'::uuid,
    ('82000000-0000-0000-0000-' || lpad(to_hex(n), 12, '0'))::uuid,
    'ko-KR',
    '성능 영화 ' || lpad(n::text, 6, '0'),
    '87,585편 Catalog 성능 Gate를 위한 결정적 영화 설명 ' || n,
    'PERFORMANCE_FIXTURE',
    '2026-08-29T00:00:00Z'::timestamptz
  FROM generate_series(1, 87585) AS generated(n);

INSERT INTO movie_genre (catalog_version_id, movie_id, genre_id, display_order)
SELECT
    '80000000-0000-0000-0000-000000000001'::uuid,
    ('82000000-0000-0000-0000-' || lpad(to_hex(n), 12, '0'))::uuid,
    '81000000-0000-0000-0000-000000000001'::uuid,
    0
  FROM generate_series(1, 87585) AS generated(n);

INSERT INTO movie_search_document (
    catalog_version_id,
    movie_id,
    normalized_title_terms,
    normalized_person_terms,
    search_vector,
    popularity_score,
    built_at
)
SELECT
    '80000000-0000-0000-0000-000000000001'::uuid,
    ('82000000-0000-0000-0000-' || lpad(to_hex(n), 12, '0'))::uuid,
    lower(
        'Performance Movie ' || lpad(n::text, 6, '0') || ' 성능 영화 ' || lpad(n::text, 6, '0') ||
        CASE WHEN n % 1000 = 0 THEN ' needle' ELSE '' END
    ),
    '',
    to_tsvector(
        'simple',
        lower(
            'Performance Movie ' || lpad(n::text, 6, '0') || ' 성능 영화 ' || lpad(n::text, 6, '0') ||
            CASE WHEN n % 1000 = 0 THEN ' needle' ELSE '' END
        )
    ),
    (100000 - n)::numeric,
    '2026-08-29T00:00:00Z'::timestamptz
  FROM generate_series(1, 87585) AS generated(n);

UPDATE catalog_version
   SET status = 'ACTIVE', published_at = '2026-08-29T00:00:00Z'::timestamptz
 WHERE id = '80000000-0000-0000-0000-000000000001';

COMMIT;

SELECT
    (SELECT count(*) FROM movie_identity) AS movie_identity_count,
    (SELECT count(*) FROM movie_catalog_projection) AS projection_count,
    (SELECT count(*) FROM movie_localization) AS localization_count,
    (SELECT count(*) FROM movie_genre) AS movie_genre_count,
    (SELECT count(*) FROM movie_search_document) AS search_document_count;

