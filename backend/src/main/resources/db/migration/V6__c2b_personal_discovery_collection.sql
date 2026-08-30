CREATE TABLE recommendation_delivery (
    id uuid PRIMARY KEY,
    actor_user_id uuid NOT NULL,
    status varchar(16) NOT NULL CHECK (status = 'ACTIVE'),
    revision integer NOT NULL CHECK (revision >= 1),
    label varchar(32) NOT NULL CHECK (label = 'POPULARITY_BASELINE'),
    composition varchar(32) NOT NULL CHECK (composition = 'BASELINE_THREE'),
    recommendation_version varchar(160) NOT NULL,
    policy_version varchar(160) NOT NULL,
    mapping_version varchar(160) NOT NULL,
    catalog_version varchar(128) NOT NULL REFERENCES catalog_version(public_version),
    candidate_set_version varchar(256) NOT NULL,
    input_version varchar(256) NOT NULL,
    candidate_count integer NOT NULL CHECK (candidate_count BETWEEN 0 AND 500),
    scan_offset integer NOT NULL CHECK (scan_offset BETWEEN 0 AND candidate_count),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (id, actor_user_id),
    CHECK (length(trim(mapping_version)) > 0)
);

CREATE UNIQUE INDEX uk_recommendation_delivery_active_actor
    ON recommendation_delivery(actor_user_id) WHERE status = 'ACTIVE';

CREATE TABLE recommendation_delivery_item (
    id uuid PRIMARY KEY,
    delivery_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    sequence_position integer NOT NULL CHECK (sequence_position BETWEEN 1 AND 500),
    source_rank integer NOT NULL CHECK (source_rank BETWEEN 1 AND 500),
    recommendation_type varchar(32) NOT NULL CHECK (recommendation_type = 'POPULARITY_BASELINE'),
    status varchar(32) NOT NULL CHECK (
        status IN ('ACTIVE', 'COMPLETED_RATED', 'DISMISSED_NOT_INTERESTED')
    ),
    display_title varchar(500) NOT NULL,
    poster_url text,
    release_year integer CHECK (release_year IS NULL OR release_year BETWEEN 1870 AND 2100),
    genre_labels text NOT NULL,
    created_at timestamptz NOT NULL,
    terminal_at timestamptz,
    terminal_event_id uuid,
    completion_rating_id uuid REFERENCES rating(id),
    completion_rating_revision integer,
    FOREIGN KEY (delivery_id, actor_user_id)
        REFERENCES recommendation_delivery(id, actor_user_id) ON DELETE CASCADE,
    UNIQUE (delivery_id, sequence_position),
    UNIQUE (delivery_id, movie_id),
    CHECK (
        (status = 'ACTIVE'
            AND terminal_at IS NULL
            AND terminal_event_id IS NULL
            AND completion_rating_id IS NULL
            AND completion_rating_revision IS NULL)
        OR
        (status = 'COMPLETED_RATED'
            AND terminal_at IS NOT NULL
            AND terminal_event_id IS NULL
            AND completion_rating_id IS NOT NULL
            AND completion_rating_revision >= 1)
        OR
        (status = 'DISMISSED_NOT_INTERESTED'
            AND terminal_at IS NOT NULL
            AND terminal_event_id IS NOT NULL
            AND completion_rating_id IS NULL
            AND completion_rating_revision IS NULL)
    )
);

CREATE INDEX ix_recommendation_delivery_item_owner_status_position
    ON recommendation_delivery_item(actor_user_id, status, delivery_id, sequence_position);
CREATE INDEX ix_recommendation_delivery_item_owner_movie
    ON recommendation_delivery_item(actor_user_id, movie_id, status);

CREATE TABLE recommendation_append_event (
    append_event_id uuid PRIMARY KEY,
    actor_user_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    canonical_request_sha256 char(64) NOT NULL CHECK (canonical_request_sha256 ~ '^[a-f0-9]{64}$'),
    result_revision integer NOT NULL CHECK (result_revision >= 1),
    appended_item_count integer NOT NULL CHECK (appended_item_count BETWEEN 0 AND 3),
    response_body jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    FOREIGN KEY (delivery_id, actor_user_id)
        REFERENCES recommendation_delivery(id, actor_user_id) ON DELETE CASCADE
);

CREATE INDEX ix_recommendation_append_event_owner_delivery
    ON recommendation_append_event(actor_user_id, delivery_id, occurred_at DESC);

CREATE TABLE recommendation_dismissal_event (
    dismissal_event_id uuid PRIMARY KEY,
    actor_user_id uuid NOT NULL,
    delivery_item_id uuid NOT NULL REFERENCES recommendation_delivery_item(id),
    canonical_request_sha256 char(64) NOT NULL CHECK (canonical_request_sha256 ~ '^[a-f0-9]{64}$'),
    result_revision integer NOT NULL CHECK (result_revision >= 1),
    occurred_at timestamptz NOT NULL,
    response_body jsonb NOT NULL,
    UNIQUE (delivery_item_id)
);

CREATE INDEX ix_recommendation_dismissal_event_owner
    ON recommendation_dismissal_event(actor_user_id, occurred_at DESC);
