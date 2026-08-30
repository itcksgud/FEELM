CREATE TABLE catalog_sync_run (
    id uuid PRIMARY KEY,
    job_type varchar(64) NOT NULL,
    status varchar(32) NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'REJECTED')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    source_version varchar(128),
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure_summary text
);

CREATE TABLE catalog_version (
    id uuid PRIMARY KEY,
    public_version varchar(128) NOT NULL UNIQUE,
    sync_run_id uuid REFERENCES catalog_sync_run(id),
    status varchar(32) NOT NULL CHECK (status IN ('STAGING', 'ACTIVE', 'RETIRED', 'REJECTED')),
    published_at timestamptz,
    source_hash varchar(128) NOT NULL
);

CREATE UNIQUE INDEX ux_catalog_version_single_active
    ON catalog_version ((status)) WHERE status = 'ACTIVE';

CREATE TABLE movie_identity (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL
);

CREATE TABLE movie_external_id (
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    source varchar(32) NOT NULL CHECK (source IN ('MOVIELENS', 'TMDB', 'IMDB', 'WIKIDATA')),
    external_id varchar(64) NOT NULL,
    verification_status varchar(32) NOT NULL CHECK (verification_status IN ('VERIFIED', 'RECOVERED', 'UNVERIFIED')),
    verified_at timestamptz,
    PRIMARY KEY (movie_id, source, external_id),
    UNIQUE (source, external_id)
);

CREATE TABLE movie_catalog_projection (
    catalog_version_id uuid NOT NULL REFERENCES catalog_version(id) ON DELETE CASCADE,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    media_type varchar(16) NOT NULL CHECK (media_type = 'MOVIE'),
    identity_status varchar(32) NOT NULL CHECK (identity_status IN (
        'IDENTITY_VERIFIED', 'TYPE_MISMATCH_TV', 'TMDB_NOT_FOUND', 'IDENTITY_REVIEW_REQUIRED', 'SOURCE_REMOVED'
    )),
    visibility_status varchar(32) NOT NULL CHECK (visibility_status IN ('UI_READY', 'CATALOG_VISIBLE', 'UI_INCOMPLETE')),
    original_title varchar(500) NOT NULL,
    original_language varchar(16) NOT NULL,
    release_date date,
    runtime_minutes integer CHECK (runtime_minutes IS NULL OR runtime_minutes > 0),
    poster_path varchar(1024),
    backdrop_path varchar(1024),
    tmdb_vote_average numeric(4,2) CHECK (tmdb_vote_average IS NULL OR tmdb_vote_average BETWEEN 0 AND 10),
    tmdb_vote_count bigint NOT NULL DEFAULT 0 CHECK (tmdb_vote_count >= 0),
    metadata_fetched_at timestamptz NOT NULL,
    deleted boolean NOT NULL DEFAULT false,
    PRIMARY KEY (catalog_version_id, movie_id)
);

CREATE TABLE movie_localization (
    catalog_version_id uuid NOT NULL,
    movie_id uuid NOT NULL,
    locale varchar(16) NOT NULL,
    title varchar(500),
    overview text,
    source varchar(32) NOT NULL,
    fetched_at timestamptz NOT NULL,
    PRIMARY KEY (catalog_version_id, movie_id, locale),
    FOREIGN KEY (catalog_version_id, movie_id)
        REFERENCES movie_catalog_projection(catalog_version_id, movie_id) ON DELETE CASCADE
);

CREATE TABLE genre (
    id uuid PRIMARY KEY,
    code varchar(64) NOT NULL UNIQUE,
    display_name_ko varchar(128) NOT NULL,
    display_order integer NOT NULL CHECK (display_order >= 0),
    active boolean NOT NULL DEFAULT true
);

CREATE TABLE movie_genre (
    catalog_version_id uuid NOT NULL,
    movie_id uuid NOT NULL,
    genre_id uuid NOT NULL REFERENCES genre(id),
    display_order integer NOT NULL CHECK (display_order >= 0),
    PRIMARY KEY (catalog_version_id, movie_id, genre_id),
    FOREIGN KEY (catalog_version_id, movie_id)
        REFERENCES movie_catalog_projection(catalog_version_id, movie_id) ON DELETE CASCADE
);

CREATE TABLE country (
    code char(2) PRIMARY KEY CHECK (code ~ '^[A-Z]{2}$'),
    display_name_ko varchar(128) NOT NULL,
    display_name_en varchar(128) NOT NULL
);

CREATE TABLE movie_country (
    catalog_version_id uuid NOT NULL,
    movie_id uuid NOT NULL,
    country_code char(2) NOT NULL REFERENCES country(code),
    display_order integer NOT NULL CHECK (display_order >= 0),
    PRIMARY KEY (catalog_version_id, movie_id, country_code),
    FOREIGN KEY (catalog_version_id, movie_id)
        REFERENCES movie_catalog_projection(catalog_version_id, movie_id) ON DELETE CASCADE
);

CREATE TABLE person (
    id uuid PRIMARY KEY,
    tmdb_person_id bigint UNIQUE,
    display_name varchar(500) NOT NULL,
    profile_path varchar(1024)
);

CREATE TABLE movie_credit (
    catalog_version_id uuid NOT NULL,
    movie_id uuid NOT NULL,
    person_id uuid NOT NULL REFERENCES person(id),
    credit_type varchar(16) NOT NULL CHECK (credit_type IN ('DIRECTOR', 'CAST')),
    job varchar(128) NOT NULL DEFAULT '',
    character_name varchar(500) NOT NULL DEFAULT '',
    credit_order integer NOT NULL CHECK (credit_order >= 0),
    PRIMARY KEY (catalog_version_id, movie_id, person_id, credit_type, job, character_name),
    FOREIGN KEY (catalog_version_id, movie_id)
        REFERENCES movie_catalog_projection(catalog_version_id, movie_id) ON DELETE CASCADE
);

CREATE TABLE movie_search_document (
    catalog_version_id uuid NOT NULL,
    movie_id uuid NOT NULL,
    normalized_title_terms text NOT NULL DEFAULT '',
    normalized_person_terms text NOT NULL DEFAULT '',
    search_vector tsvector,
    popularity_score numeric(20,8) NOT NULL DEFAULT 0,
    built_at timestamptz NOT NULL,
    PRIMARY KEY (catalog_version_id, movie_id),
    FOREIGN KEY (catalog_version_id, movie_id)
        REFERENCES movie_catalog_projection(catalog_version_id, movie_id) ON DELETE CASCADE
);

CREATE INDEX ix_movie_search_document_vector ON movie_search_document USING gin(search_vector);

CREATE TABLE movie_similarity (
    catalog_version_id uuid NOT NULL,
    source_movie_id uuid NOT NULL,
    target_movie_id uuid NOT NULL,
    similarity_version varchar(128) NOT NULL,
    rank integer NOT NULL CHECK (rank >= 0),
    score numeric(20,10) NOT NULL,
    reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    generated_at timestamptz NOT NULL,
    PRIMARY KEY (catalog_version_id, source_movie_id, similarity_version, rank),
    UNIQUE (catalog_version_id, source_movie_id, target_movie_id, similarity_version),
    CHECK (source_movie_id <> target_movie_id),
    FOREIGN KEY (catalog_version_id, source_movie_id)
        REFERENCES movie_catalog_projection(catalog_version_id, movie_id) ON DELETE CASCADE,
    FOREIGN KEY (catalog_version_id, target_movie_id)
        REFERENCES movie_catalog_projection(catalog_version_id, movie_id) ON DELETE CASCADE
);

CREATE TABLE ott_provider (
    id uuid PRIMARY KEY,
    tmdb_provider_id bigint NOT NULL UNIQUE,
    provider_code varchar(64) NOT NULL UNIQUE,
    display_name varchar(256) NOT NULL,
    logo_path varchar(1024),
    display_priority integer NOT NULL CHECK (display_priority >= 0),
    active boolean NOT NULL DEFAULT true
);

CREATE TABLE movie_availability_snapshot (
    id uuid PRIMARY KEY,
    catalog_version_id uuid NOT NULL,
    movie_id uuid NOT NULL,
    region char(2) NOT NULL CHECK (region = 'KR'),
    fetch_status varchar(32) NOT NULL CHECK (fetch_status IN ('SUCCESS_LISTED', 'SUCCESS_EMPTY', 'FAILED')),
    source varchar(64) NOT NULL,
    aggregator_url varchar(2048),
    fetched_at timestamptz NOT NULL,
    fresh_until timestamptz NOT NULL,
    serve_until timestamptz NOT NULL,
    failure_code varchar(128),
    CHECK (fresh_until = fetched_at + interval '24 hours'),
    CHECK (serve_until = fetched_at + interval '7 days'),
    FOREIGN KEY (catalog_version_id, movie_id)
        REFERENCES movie_catalog_projection(catalog_version_id, movie_id) ON DELETE CASCADE
);

CREATE INDEX ix_availability_latest_success
    ON movie_availability_snapshot(catalog_version_id, movie_id, region, fetched_at DESC)
    WHERE fetch_status IN ('SUCCESS_LISTED', 'SUCCESS_EMPTY');

CREATE TABLE movie_ott_offer (
    id uuid PRIMARY KEY,
    snapshot_id uuid NOT NULL REFERENCES movie_availability_snapshot(id) ON DELETE CASCADE,
    provider_id uuid NOT NULL REFERENCES ott_provider(id),
    monetization_type varchar(16) NOT NULL CHECK (monetization_type IN ('FLATRATE', 'RENT', 'BUY', 'FREE', 'ADS')),
    link_type varchar(16) NOT NULL CHECK (link_type IN ('AGGREGATOR', 'DIRECT')),
    landing_url varchar(2048),
    source_display_priority integer NOT NULL DEFAULT 0 CHECK (source_display_priority >= 0),
    UNIQUE (snapshot_id, provider_id, monetization_type)
);

CREATE TABLE user_ott_subscription (
    user_id uuid NOT NULL,
    provider_id uuid NOT NULL REFERENCES ott_provider(id),
    selected_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, provider_id)
);
