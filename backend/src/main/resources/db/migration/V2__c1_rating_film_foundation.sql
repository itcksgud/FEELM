CREATE TABLE flavor_mapping_version (
    mapping_version varchar(64) PRIMARY KEY,
    status varchar(16) NOT NULL CHECK (status IN ('STAGING', 'ACTIVE', 'RETIRED')),
    created_at timestamptz NOT NULL,
    published_at timestamptz,
    CHECK ((status = 'STAGING' AND published_at IS NULL) OR (status <> 'STAGING' AND published_at IS NOT NULL))
);

CREATE UNIQUE INDEX ux_flavor_mapping_single_active
    ON flavor_mapping_version ((status)) WHERE status = 'ACTIVE';

CREATE TABLE popcorn_flavor (
    id uuid PRIMARY KEY,
    flavor_code varchar(32) NOT NULL UNIQUE CHECK (flavor_code IN (
        'ADRENALINE', 'WONDER', 'JOY', 'HEART', 'SHADOW', 'REAL', 'LEGACY', 'RHYTHM'
    )),
    display_name varchar(64) NOT NULL,
    color_token varchar(64) NOT NULL,
    active boolean NOT NULL DEFAULT true
);

CREATE TABLE flavor_genre_mapping (
    mapping_version varchar(64) NOT NULL REFERENCES flavor_mapping_version(mapping_version),
    source_genre_id integer NOT NULL CHECK (source_genre_id > 0),
    flavor_id uuid NOT NULL REFERENCES popcorn_flavor(id),
    source_display_order integer NOT NULL CHECK (source_display_order = 0),
    PRIMARY KEY (mapping_version, source_genre_id),
    UNIQUE (mapping_version, source_genre_id, flavor_id)
);

CREATE TABLE movie_flavor_assignment (
    mapping_version varchar(64) NOT NULL REFERENCES flavor_mapping_version(mapping_version),
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    flavor_id uuid NOT NULL REFERENCES popcorn_flavor(id),
    assignment_source varchar(32) NOT NULL CHECK (assignment_source = 'PRIMARY_TMDB_GENRE'),
    source_genre_id integer NOT NULL,
    source_display_order integer NOT NULL CHECK (source_display_order = 0),
    assigned_at timestamptz NOT NULL,
    PRIMARY KEY (mapping_version, movie_id),
    FOREIGN KEY (mapping_version, source_genre_id)
        REFERENCES flavor_genre_mapping(mapping_version, source_genre_id)
);

CREATE INDEX ix_movie_flavor_assignment_movie
    ON movie_flavor_assignment(movie_id, mapping_version);

CREATE TABLE watch_intent (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    provider_id uuid NOT NULL REFERENCES ott_provider(id),
    source_offer_id uuid REFERENCES movie_ott_offer(id) ON DELETE SET NULL,
    status varchar(32) NOT NULL CHECK (status IN (
        'LINK_CLICKED', 'CONFIRMATION_PENDING', 'CONFIRMED_WATCHED', 'CONFIRMED_NOT_WATCHED', 'EXPIRED'
    )),
    clicked_at timestamptz NOT NULL,
    confirmation_due_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    responded_at timestamptz,
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    CHECK (confirmation_due_at = clicked_at + interval '48 hours'),
    CHECK (expires_at = clicked_at + interval '7 days'),
    CHECK (confirmation_due_at < expires_at),
    CHECK (
        (status IN ('LINK_CLICKED', 'CONFIRMATION_PENDING') AND responded_at IS NULL)
        OR (status IN ('CONFIRMED_WATCHED', 'CONFIRMED_NOT_WATCHED', 'EXPIRED') AND responded_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX ux_watch_intent_active_user_movie
    ON watch_intent(user_id, movie_id)
    WHERE status IN ('LINK_CLICKED', 'CONFIRMATION_PENDING');
CREATE INDEX ix_watch_intent_pending_due
    ON watch_intent(user_id, confirmation_due_at, movie_id, id)
    WHERE status = 'CONFIRMATION_PENDING';
CREATE INDEX ix_watch_intent_expiry
    ON watch_intent(expires_at, id)
    WHERE status IN ('LINK_CLICKED', 'CONFIRMATION_PENDING');

CREATE TABLE viewing_record (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    source_watch_intent_id uuid NOT NULL UNIQUE REFERENCES watch_intent(id),
    provider_id uuid NOT NULL REFERENCES ott_provider(id),
    status varchar(32) NOT NULL CHECK (status IN ('WATCHED_CONFIRMED', 'RATED_COMPLETED')),
    watched_confirmed_at timestamptz NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    UNIQUE (user_id, movie_id)
);

CREATE INDEX ix_viewing_record_unrated
    ON viewing_record(user_id, watched_confirmed_at DESC, movie_id)
    WHERE status = 'WATCHED_CONFIRMED';

CREATE TABLE rating (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    viewing_record_id uuid NOT NULL REFERENCES viewing_record(id),
    value smallint NOT NULL CHECK (value BETWEEN 1 AND 5),
    logical_status varchar(16) NOT NULL CHECK (logical_status IN ('ACTIVE', 'DELETED')),
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    deleted_at timestamptz,
    deletion_trace_id varchar(128),
    CHECK (updated_at >= created_at),
    CHECK (
        (logical_status = 'ACTIVE' AND deleted_at IS NULL AND deletion_trace_id IS NULL)
        OR (logical_status = 'DELETED' AND deleted_at IS NOT NULL AND deletion_trace_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX ux_rating_active_viewing
    ON rating(viewing_record_id) WHERE logical_status = 'ACTIVE';
CREATE UNIQUE INDEX ux_rating_active_user_movie
    ON rating(user_id, movie_id) WHERE logical_status = 'ACTIVE';
CREATE INDEX ix_rating_active_user_updated
    ON rating(user_id, updated_at DESC, movie_id) WHERE logical_status = 'ACTIVE';

CREATE OR REPLACE FUNCTION enforce_rating_owner_invariant() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM viewing_record v
         WHERE v.id = NEW.viewing_record_id
           AND v.user_id = NEW.user_id
           AND v.movie_id = NEW.movie_id
    ) THEN
        RAISE EXCEPTION 'rating ownership invariant violated' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_rating_owner_invariant
    BEFORE INSERT OR UPDATE ON rating
    FOR EACH ROW EXECUTE FUNCTION enforce_rating_owner_invariant();

CREATE TABLE frame (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    viewing_record_id uuid NOT NULL UNIQUE REFERENCES viewing_record(id),
    rating_id uuid NOT NULL UNIQUE REFERENCES rating(id),
    derivation_version varchar(64) NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (user_id, movie_id),
    CHECK (updated_at >= created_at)
);

CREATE TABLE popcorn (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    frame_id uuid NOT NULL UNIQUE REFERENCES frame(id) ON DELETE CASCADE,
    rating_id uuid NOT NULL UNIQUE REFERENCES rating(id),
    flavor_id uuid NOT NULL REFERENCES popcorn_flavor(id),
    flavor_mapping_version varchar(64) NOT NULL REFERENCES flavor_mapping_version(mapping_version),
    created_at timestamptz NOT NULL
);

CREATE TABLE flavor_aggregate (
    user_id uuid NOT NULL,
    flavor_id uuid NOT NULL REFERENCES popcorn_flavor(id),
    popcorn_count integer NOT NULL DEFAULT 0 CHECK (popcorn_count >= 0),
    rating_count integer NOT NULL DEFAULT 0 CHECK (rating_count >= 0),
    rating_sum integer NOT NULL DEFAULT 0 CHECK (rating_sum >= 0),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, flavor_id),
    CHECK (rating_count = popcorn_count),
    CHECK (rating_sum <= 5 * rating_count),
    CHECK ((rating_count = 0 AND rating_sum = 0) OR (rating_count > 0 AND rating_sum >= rating_count))
);

CREATE TABLE rating_taste_contribution (
    rating_id uuid NOT NULL REFERENCES rating(id) ON DELETE CASCADE,
    dimension_type varchar(16) NOT NULL CHECK (dimension_type IN ('GENRE', 'COUNTRY', 'DIRECTOR')),
    dimension_key varchar(128) NOT NULL,
    rating_value smallint NOT NULL CHECK (rating_value BETWEEN 1 AND 5),
    catalog_version_id uuid NOT NULL REFERENCES catalog_version(id),
    flavor_mapping_version varchar(64) NOT NULL REFERENCES flavor_mapping_version(mapping_version),
    derivation_version varchar(64) NOT NULL,
    PRIMARY KEY (rating_id, dimension_type, dimension_key),
    CHECK (
        (dimension_type IN ('GENRE', 'DIRECTOR')
            AND dimension_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
        OR (dimension_type = 'COUNTRY' AND dimension_key ~ '^[A-Z]{2}$')
    )
);

CREATE TABLE taste_aggregate (
    user_id uuid NOT NULL,
    dimension_type varchar(16) NOT NULL CHECK (dimension_type IN ('GENRE', 'COUNTRY', 'DIRECTOR')),
    dimension_key varchar(128) NOT NULL,
    rating_count integer NOT NULL DEFAULT 0 CHECK (rating_count >= 0),
    rating_sum integer NOT NULL DEFAULT 0 CHECK (rating_sum >= 0),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, dimension_type, dimension_key),
    CHECK (
        (dimension_type IN ('GENRE', 'DIRECTOR')
            AND dimension_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
        OR (dimension_type = 'COUNTRY' AND dimension_key ~ '^[A-Z]{2}$')
    ),
    CHECK (rating_sum <= 5 * rating_count),
    CHECK ((rating_count = 0 AND rating_sum = 0) OR (rating_count > 0 AND rating_sum >= rating_count))
);

CREATE TABLE user_behavior_event (
    event_id uuid PRIMARY KEY,
    actor_user_id uuid NOT NULL,
    event_type varchar(48) NOT NULL CHECK (event_type IN (
        'OTT_LINK_CLICKED', 'WATCH_CONFIRMATION_RESPONDED', 'RATING_CREATED', 'RATING_UPDATED', 'RATING_DELETED'
    )),
    resource_type varchar(32) NOT NULL CHECK (resource_type IN ('WATCH_INTENT', 'RATING')),
    resource_id uuid NOT NULL,
    occurred_at timestamptz NOT NULL,
    trace_id varchar(128) NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version >= 1),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX ix_behavior_actor_occurred
    ON user_behavior_event(actor_user_id, occurred_at DESC, event_id);

CREATE TABLE domain_outbox (
    event_id uuid PRIMARY KEY,
    aggregate_type varchar(32) NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type varchar(64) NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version >= 1),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    occurred_at timestamptz NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PROCESSING', 'PROCESSED', 'FAILED')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz,
    processed_at timestamptz,
    CHECK ((status = 'PROCESSED' AND processed_at IS NOT NULL) OR status <> 'PROCESSED')
);

CREATE INDEX ix_domain_outbox_dispatch
    ON domain_outbox(status, next_attempt_at, occurred_at, event_id)
    WHERE status IN ('PENDING', 'FAILED');

CREATE TABLE idempotency_record (
    actor_user_id uuid NOT NULL,
    operation_code varchar(64) NOT NULL,
    idempotency_key varchar(128) NOT NULL CHECK (idempotency_key ~ '^[!-~]{8,128}$'),
    request_hash char(64) NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    response_status integer NOT NULL CHECK (response_status BETWEEN 100 AND 599),
    response_body jsonb NOT NULL,
    resource_id uuid,
    created_at timestamptz NOT NULL,
    expires_at timestamptz,
    PRIMARY KEY (actor_user_id, operation_code, idempotency_key),
    CHECK (expires_at IS NULL OR expires_at > created_at)
);

CREATE INDEX ix_idempotency_created
    ON idempotency_record(created_at, actor_user_id);

CREATE OR REPLACE FUNCTION enforce_frame_owner_invariant() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM rating r
         WHERE r.id = NEW.rating_id
           AND r.user_id = NEW.user_id
           AND r.movie_id = NEW.movie_id
           AND r.viewing_record_id = NEW.viewing_record_id
           AND r.logical_status = 'ACTIVE'
    ) THEN
        RAISE EXCEPTION 'frame ownership invariant violated' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_frame_owner_invariant
    BEFORE INSERT OR UPDATE ON frame
    FOR EACH ROW EXECUTE FUNCTION enforce_frame_owner_invariant();

CREATE OR REPLACE FUNCTION enforce_popcorn_owner_invariant() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM frame f
          JOIN rating r ON r.id = NEW.rating_id
          JOIN movie_flavor_assignment a
            ON a.mapping_version = NEW.flavor_mapping_version
           AND a.movie_id = f.movie_id
           AND a.flavor_id = NEW.flavor_id
         WHERE f.id = NEW.frame_id
           AND f.rating_id = NEW.rating_id
           AND f.user_id = NEW.user_id
           AND r.user_id = NEW.user_id
           AND r.logical_status = 'ACTIVE'
    ) THEN
        RAISE EXCEPTION 'popcorn ownership or flavor invariant violated' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_popcorn_owner_invariant
    BEFORE INSERT OR UPDATE ON popcorn
    FOR EACH ROW EXECUTE FUNCTION enforce_popcorn_owner_invariant();

CREATE VIEW c1_rating_eligible_movie AS
SELECT p.movie_id, cv.id AS catalog_version_id, mv.mapping_version, min(a.flavor_id::text)::uuid AS flavor_id
  FROM catalog_version cv
  JOIN movie_catalog_projection p ON p.catalog_version_id = cv.id
  CROSS JOIN flavor_mapping_version mv
  JOIN movie_flavor_assignment a
    ON a.mapping_version = mv.mapping_version AND a.movie_id = p.movie_id
 WHERE cv.status = 'ACTIVE'
   AND mv.status = 'ACTIVE'
   AND p.visibility_status = 'UI_READY'
   AND p.identity_status = 'IDENTITY_VERIFIED'
   AND p.deleted = false
 GROUP BY p.movie_id, cv.id, mv.mapping_version
HAVING count(a.flavor_id) = 1;

CREATE VIEW c1_projection_invariant AS
SELECT
    (SELECT count(*) FROM rating WHERE logical_status = 'ACTIVE') AS active_rating_count,
    (SELECT count(*) FROM frame) AS frame_count,
    (SELECT count(*) FROM popcorn) AS popcorn_count,
    (SELECT count(*) FROM frame f LEFT JOIN rating r ON r.id = f.rating_id AND r.logical_status = 'ACTIVE'
      WHERE r.id IS NULL) AS orphan_frame_count,
    (SELECT count(*) FROM popcorn p LEFT JOIN frame f ON f.id = p.frame_id
      WHERE f.id IS NULL) AS orphan_popcorn_count;

INSERT INTO flavor_mapping_version (mapping_version, status, created_at, published_at)
VALUES ('v1', 'ACTIVE', '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z');

-- UUIDv5 values are stable implementation identifiers; SHADOW and HEART use the approved fixture IDs.
INSERT INTO popcorn_flavor (id, flavor_code, display_name, color_token, active) VALUES
    ('097e2a26-3252-5fd6-a808-1c0ff03adbf5', 'ADRENALINE', '짜릿함', 'popcorn.adrenaline', true),
    ('f9c9fbf0-5851-5fdb-8112-d16e690ef61d', 'WONDER', '상상', 'popcorn.wonder', true),
    ('18703485-5953-5496-a041-f08109760e84', 'JOY', '유쾌함', 'popcorn.joy', true),
    ('50fb6f76-9ab2-4bb4-9a62-7f8b76af9822', 'HEART', '여운', 'popcorn.heart', true),
    ('18828763-1fd7-4ee4-a97f-1496db3c6490', 'SHADOW', '긴장', 'popcorn.shadow', true),
    ('e2cf62b4-947f-59cc-b796-726bf0c346a3', 'REAL', '현실', 'popcorn.real', true),
    ('d946ea43-8908-54e3-9016-903390c14178', 'LEGACY', '시대', 'popcorn.legacy', true),
    ('051296ef-f7c7-5f43-a6ba-7db51305c2ae', 'RHYTHM', '리듬', 'popcorn.rhythm', true);

INSERT INTO flavor_genre_mapping (mapping_version, source_genre_id, flavor_id, source_display_order) VALUES
    ('v1', 28, '097e2a26-3252-5fd6-a808-1c0ff03adbf5', 0),
    ('v1', 12, '097e2a26-3252-5fd6-a808-1c0ff03adbf5', 0),
    ('v1', 16, 'f9c9fbf0-5851-5fdb-8112-d16e690ef61d', 0),
    ('v1', 14, 'f9c9fbf0-5851-5fdb-8112-d16e690ef61d', 0),
    ('v1', 878, 'f9c9fbf0-5851-5fdb-8112-d16e690ef61d', 0),
    ('v1', 35, '18703485-5953-5496-a041-f08109760e84', 0),
    ('v1', 10751, '18703485-5953-5496-a041-f08109760e84', 0),
    ('v1', 18, '50fb6f76-9ab2-4bb4-9a62-7f8b76af9822', 0),
    ('v1', 10749, '50fb6f76-9ab2-4bb4-9a62-7f8b76af9822', 0),
    ('v1', 80, '18828763-1fd7-4ee4-a97f-1496db3c6490', 0),
    ('v1', 27, '18828763-1fd7-4ee4-a97f-1496db3c6490', 0),
    ('v1', 9648, '18828763-1fd7-4ee4-a97f-1496db3c6490', 0),
    ('v1', 53, '18828763-1fd7-4ee4-a97f-1496db3c6490', 0),
    ('v1', 99, 'e2cf62b4-947f-59cc-b796-726bf0c346a3', 0),
    ('v1', 36, 'd946ea43-8908-54e3-9016-903390c14178', 0),
    ('v1', 10752, 'd946ea43-8908-54e3-9016-903390c14178', 0),
    ('v1', 37, 'd946ea43-8908-54e3-9016-903390c14178', 0),
    ('v1', 10402, '051296ef-f7c7-5f43-a6ba-7db51305c2ae', 0),
    ('v1', 10770, '051296ef-f7c7-5f43-a6ba-7db51305c2ae', 0);
