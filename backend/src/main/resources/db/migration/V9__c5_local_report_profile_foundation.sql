CREATE TABLE c5_taste_report_revision (
    report_id uuid PRIMARY KEY,
    owner_user_id uuid NOT NULL REFERENCES c4_user_account(user_id),
    period_start date NOT NULL,
    period_end date NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    status varchar(24) NOT NULL CHECK (status IN ('READY', 'EMPTY_NO_ACTIVITY', 'SUPERSEDED')),
    source_watermark timestamptz NOT NULL,
    viewing_count integer NOT NULL CHECK (viewing_count >= 0),
    rated_count integer NOT NULL CHECK (rated_count >= 0),
    rating_sum integer NOT NULL CHECK (rating_sum >= 0),
    created_at timestamptz NOT NULL,
    superseded_at timestamptz,
    UNIQUE (owner_user_id, period_start, revision),
    UNIQUE (report_id, owner_user_id),
    CHECK (period_end >= period_start),
    CHECK (rated_count <= viewing_count),
    CHECK (rating_sum <= 5 * rated_count),
    CHECK ((rated_count = 0 AND rating_sum = 0) OR (rated_count > 0 AND rating_sum >= rated_count)),
    CHECK ((status = 'SUPERSEDED' AND superseded_at IS NOT NULL) OR (status <> 'SUPERSEDED' AND superseded_at IS NULL)),
    CHECK ((viewing_count = 0 AND status IN ('EMPTY_NO_ACTIVITY', 'SUPERSEDED')) OR viewing_count > 0)
);

CREATE INDEX ix_c5_report_owner_period
    ON c5_taste_report_revision(owner_user_id, period_start DESC, revision DESC);

CREATE TABLE c5_taste_report_period_item (
    report_id uuid NOT NULL REFERENCES c5_taste_report_revision(report_id) ON DELETE CASCADE,
    position integer NOT NULL CHECK (position >= 1),
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    viewing_record_id uuid NOT NULL,
    viewing_revision integer NOT NULL CHECK (viewing_revision >= 1),
    rating_id uuid,
    rating_revision integer CHECK (rating_revision IS NULL OR rating_revision >= 1),
    rating_value smallint CHECK (rating_value IS NULL OR rating_value BETWEEN 1 AND 5),
    display_title varchar(500) NOT NULL,
    poster_url text,
    watched_at timestamptz NOT NULL,
    PRIMARY KEY (report_id, position),
    UNIQUE (report_id, viewing_record_id),
    CHECK ((rating_id IS NULL AND rating_revision IS NULL AND rating_value IS NULL)
        OR (rating_id IS NOT NULL AND rating_revision IS NOT NULL AND rating_value IS NOT NULL))
);

CREATE TABLE c5_report_export_job (
    export_id uuid PRIMARY KEY,
    owner_user_id uuid NOT NULL REFERENCES c4_user_account(user_id),
    report_id uuid NOT NULL REFERENCES c5_taste_report_revision(report_id),
    status varchar(16) NOT NULL CHECK (status IN ('PENDING', 'READY', 'FAILED', 'EXPIRED')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    failure_code varchar(80),
    CHECK (expires_at <= created_at + interval '24 hours'),
    CHECK (updated_at >= created_at),
    CHECK ((status = 'FAILED' AND failure_code IS NOT NULL) OR status <> 'FAILED'),
    UNIQUE (export_id, expires_at),
    FOREIGN KEY (report_id, owner_user_id)
        REFERENCES c5_taste_report_revision(report_id, owner_user_id)
);

CREATE TABLE c5_report_export_artifact (
    export_id uuid PRIMARY KEY REFERENCES c5_report_export_job(export_id) ON DELETE CASCADE,
    opaque_path text NOT NULL UNIQUE,
    content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    content_size bigint NOT NULL CHECK (content_size > 0),
    media_type varchar(32) NOT NULL CHECK (media_type = 'application/pdf'),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    CHECK (expires_at <= created_at + interval '24 hours'),
    FOREIGN KEY (export_id, expires_at)
        REFERENCES c5_report_export_job(export_id, expires_at)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE c5_public_profile_capability (
    owner_user_id uuid PRIMARY KEY REFERENCES c4_user_account(user_id),
    public_profile_id uuid NOT NULL UNIQUE,
    revision integer NOT NULL CHECK (revision >= 1),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CHECK (updated_at >= created_at)
);

CREATE TABLE c5_user_privacy_setting (
    owner_user_id uuid NOT NULL REFERENCES c5_public_profile_capability(owner_user_id) ON DELETE CASCADE,
    resource varchar(16) NOT NULL CHECK (resource IN ('PROFILE', 'FILM', 'POPCORN')),
    visibility varchar(16) NOT NULL CHECK (visibility IN ('PRIVATE', 'PUBLIC')),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (owner_user_id, resource)
);

CREATE TABLE c5_report_share_grant (
    share_id uuid PRIMARY KEY,
    owner_user_id uuid NOT NULL REFERENCES c4_user_account(user_id),
    report_id uuid NOT NULL REFERENCES c5_taste_report_revision(report_id),
    token_sha256 char(64) NOT NULL UNIQUE CHECK (token_sha256 ~ '^[a-f0-9]{64}$'),
    status varchar(16) NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED', 'EXPIRED')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    terminal_at timestamptz,
    CHECK (expires_at > created_at),
    CHECK ((status = 'ACTIVE' AND terminal_at IS NULL) OR (status <> 'ACTIVE' AND terminal_at IS NOT NULL)),
    FOREIGN KEY (report_id, owner_user_id)
        REFERENCES c5_taste_report_revision(report_id, owner_user_id)
);

CREATE INDEX ix_c5_share_owner_active
    ON c5_report_share_grant(owner_user_id, created_at DESC) WHERE status = 'ACTIVE';

CREATE TABLE c5_report_share_viewer_session (
    session_id uuid PRIMARY KEY,
    share_id uuid NOT NULL REFERENCES c5_report_share_grant(share_id) ON DELETE CASCADE,
    session_sha256 char(64) NOT NULL UNIQUE CHECK (session_sha256 ~ '^[a-f0-9]{64}$'),
    status varchar(16) NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED', 'EXPIRED')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    terminal_at timestamptz,
    CHECK (expires_at <= created_at + interval '15 minutes'),
    CHECK ((status = 'ACTIVE' AND terminal_at IS NULL) OR (status <> 'ACTIVE' AND terminal_at IS NOT NULL))
);

CREATE TABLE c5_user_notification_setting (
    owner_user_id uuid PRIMARY KEY REFERENCES c4_user_account(user_id),
    watch_confirmation_due_enabled boolean NOT NULL DEFAULT false,
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at timestamptz NOT NULL
);

CREATE TABLE c5_in_app_notification (
    notification_id uuid PRIMARY KEY,
    owner_user_id uuid NOT NULL REFERENCES c4_user_account(user_id),
    category varchar(40) NOT NULL CHECK (category = 'WATCH_CONFIRMATION_DUE'),
    source_type varchar(32) NOT NULL CHECK (source_type = 'WATCH_INTENT'),
    source_id uuid NOT NULL,
    source_revision integer NOT NULL CHECK (source_revision >= 1),
    state varchar(16) NOT NULL CHECK (state IN ('UNREAD', 'READ', 'DISMISSED')),
    message varchar(500) NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    terminal_at timestamptz,
    UNIQUE (owner_user_id, source_type, source_id, source_revision),
    CHECK (expires_at <= created_at + interval '30 days'),
    CHECK ((state = 'DISMISSED' AND terminal_at IS NOT NULL) OR (state <> 'DISMISSED' AND terminal_at IS NULL))
);

CREATE INDEX ix_c5_notification_owner_state
    ON c5_in_app_notification(owner_user_id, state, created_at DESC);

CREATE TABLE c5_idempotency_result (
    actor_user_id uuid NOT NULL REFERENCES c4_user_account(user_id),
    operation varchar(80) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_sha256 char(64) NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
    response_status integer NOT NULL CHECK (response_status BETWEEN 200 AND 299),
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (actor_user_id, operation, idempotency_key),
    CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    CHECK (idempotency_key ~ '^[!-~]+$')
);

CREATE OR REPLACE FUNCTION enforce_c5_report_revision_snapshot_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.report_id IS DISTINCT FROM OLD.report_id
       OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
       OR NEW.period_start IS DISTINCT FROM OLD.period_start
       OR NEW.period_end IS DISTINCT FROM OLD.period_end
       OR NEW.revision IS DISTINCT FROM OLD.revision
       OR NEW.source_watermark IS DISTINCT FROM OLD.source_watermark
       OR NEW.viewing_count IS DISTINCT FROM OLD.viewing_count
       OR NEW.rated_count IS DISTINCT FROM OLD.rated_count
       OR NEW.rating_sum IS DISTINCT FROM OLD.rating_sum
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'C5 report snapshot is immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status = 'SUPERSEDED' AND (NEW.status IS DISTINCT FROM OLD.status
        OR NEW.superseded_at IS DISTINCT FROM OLD.superseded_at) THEN
        RAISE EXCEPTION 'C5 superseded report is terminal' USING ERRCODE = '23514';
    END IF;
    IF OLD.status <> 'SUPERSEDED' AND NEW.status NOT IN (OLD.status, 'SUPERSEDED') THEN
        RAISE EXCEPTION 'C5 report status transition is invalid' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_c5_report_revision_snapshot_immutable
    BEFORE UPDATE OR DELETE ON c5_taste_report_revision
    FOR EACH ROW EXECUTE FUNCTION enforce_c5_report_revision_snapshot_immutable();

CREATE OR REPLACE FUNCTION enforce_c5_report_item_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'C5 report period item is immutable' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_c5_report_item_immutable
    BEFORE UPDATE OR DELETE ON c5_taste_report_period_item
    FOR EACH ROW EXECUTE FUNCTION enforce_c5_report_item_immutable();

CREATE OR REPLACE FUNCTION validate_c5_report_snapshot_aggregate() RETURNS trigger AS $$
DECLARE
    target_report_id uuid;
    summary_status varchar(24);
    summary_viewing_count integer;
    summary_rated_count integer;
    summary_rating_sum integer;
    actual_viewing_count integer;
    actual_rated_count integer;
    actual_rating_sum integer;
    first_position integer;
    last_position integer;
BEGIN
    target_report_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.report_id ELSE NEW.report_id END;

    SELECT status, viewing_count, rated_count, rating_sum
      INTO summary_status, summary_viewing_count, summary_rated_count, summary_rating_sum
      FROM c5_taste_report_revision
     WHERE report_id = target_report_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    SELECT count(*)::integer,
           count(rating_value)::integer,
           coalesce(sum(rating_value), 0)::integer,
           min(position),
           max(position)
      INTO actual_viewing_count, actual_rated_count, actual_rating_sum, first_position, last_position
      FROM c5_taste_report_period_item
     WHERE report_id = target_report_id;

    IF summary_viewing_count <> actual_viewing_count
       OR summary_rated_count <> actual_rated_count
       OR summary_rating_sum <> actual_rating_sum
       OR (actual_viewing_count = 0 AND summary_status NOT IN ('EMPTY_NO_ACTIVITY', 'SUPERSEDED'))
       OR (actual_viewing_count > 0 AND summary_status = 'EMPTY_NO_ACTIVITY')
       OR (actual_viewing_count > 0 AND (first_position <> 1 OR last_position <> actual_viewing_count)) THEN
        RAISE EXCEPTION 'C5 report snapshot aggregate mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_c5_report_snapshot_aggregate_from_summary
    AFTER INSERT OR UPDATE ON c5_taste_report_revision
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_c5_report_snapshot_aggregate();

CREATE CONSTRAINT TRIGGER trg_c5_report_snapshot_aggregate_from_item
    AFTER INSERT OR UPDATE OR DELETE ON c5_taste_report_period_item
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_c5_report_snapshot_aggregate();
