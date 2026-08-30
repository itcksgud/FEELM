CREATE TABLE c2_rating_input_snapshot (
    user_id uuid PRIMARY KEY,
    input_policy_version varchar(64) NOT NULL,
    input_version varchar(128) NOT NULL,
    rating_count integer NOT NULL CHECK (rating_count >= 0),
    source_event_id uuid NOT NULL REFERENCES domain_outbox(event_id),
    projection_revision bigint NOT NULL DEFAULT 1 CHECK (projection_revision >= 1),
    rebuilt_at timestamptz NOT NULL,
    CHECK (input_policy_version = 'c2-active-rating-input-v1'),
    CHECK (input_version ~ '^c2-active-rating-input-v1:sha256:[a-f0-9]{64}$')
);

CREATE INDEX ix_c2_rating_input_snapshot_version
    ON c2_rating_input_snapshot(input_version);

CREATE TABLE c2_rating_input_item (
    user_id uuid NOT NULL REFERENCES c2_rating_input_snapshot(user_id) ON DELETE CASCADE,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    rating_value smallint NOT NULL CHECK (rating_value BETWEEN 1 AND 5),
    rating_revision integer NOT NULL CHECK (rating_revision >= 1),
    canonical_order integer NOT NULL CHECK (canonical_order >= 0),
    PRIMARY KEY (user_id, movie_id),
    UNIQUE (user_id, canonical_order)
);

CREATE TABLE c2_rating_input_event_application (
    event_id uuid PRIMARY KEY REFERENCES domain_outbox(event_id),
    user_id uuid NOT NULL,
    input_version varchar(128) NOT NULL,
    applied_at timestamptz NOT NULL,
    CHECK (input_version ~ '^c2-active-rating-input-v1:sha256:[a-f0-9]{64}$'),
    FOREIGN KEY (user_id) REFERENCES c2_rating_input_snapshot(user_id) ON DELETE CASCADE
);

CREATE INDEX ix_c2_rating_input_event_application_user
    ON c2_rating_input_event_application(user_id, applied_at DESC, event_id);
