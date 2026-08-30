CREATE TABLE recommendation_exposure_batch (
    exposure_batch_id uuid PRIMARY KEY,
    source_request_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    recommendation_version varchar(160) NOT NULL,
    artifact_set_version varchar(160) NOT NULL,
    compatibility_id varchar(160) NOT NULL,
    policy_version varchar(160) NOT NULL,
    ranking_policy varchar(64) NOT NULL CHECK (ranking_policy = 'BAYESIAN_POPULARITY_ONLY'),
    ranking_alpha numeric(8,6) NOT NULL CHECK (ranking_alpha = 0),
    mapping_version varchar(160) NOT NULL,
    catalog_version varchar(128) NOT NULL REFERENCES catalog_version(public_version),
    candidate_set_version varchar(256) NOT NULL,
    input_version varchar(256) NOT NULL,
    bias_model_version varchar(160) NOT NULL,
    bias_payload_sha256 char(64) NOT NULL CHECK (bias_payload_sha256 ~ '^[a-f0-9]{64}$'),
    factors_model_version varchar(160) NOT NULL,
    factors_payload_sha256 char(64) NOT NULL CHECK (factors_payload_sha256 ~ '^[a-f0-9]{64}$'),
    calibration_model_version varchar(160) NOT NULL,
    calibration_payload_sha256 char(64) NOT NULL CHECK (calibration_payload_sha256 ~ '^[a-f0-9]{64}$'),
    mapping_model_version varchar(160) NOT NULL,
    mapping_payload_sha256 char(64) NOT NULL CHECK (mapping_payload_sha256 ~ '^[a-f0-9]{64}$'),
    attribution_policy_version varchar(160) NOT NULL,
    exposed_at timestamptz NOT NULL,
    item_count integer NOT NULL CHECK (item_count > 0),
    canonical_payload_sha256 char(64) NOT NULL CHECK (canonical_payload_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL,
    UNIQUE (exposure_batch_id, actor_user_id)
);

CREATE INDEX ix_recommendation_exposure_batch_actor_time
    ON recommendation_exposure_batch(actor_user_id, exposed_at DESC, exposure_batch_id);
CREATE INDEX ix_recommendation_exposure_batch_recommendation
    ON recommendation_exposure_batch(recommendation_version, exposed_at DESC);

CREATE TABLE recommendation_exposure_item (
    recommendation_item_id uuid PRIMARY KEY,
    exposure_batch_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    position integer NOT NULL CHECK (position >= 1),
    source_rank integer NOT NULL CHECK (source_rank >= 1),
    recommendation_type varchar(64) NOT NULL CHECK (recommendation_type = 'POPULARITY_BASELINE'),
    expected_star_status varchar(32) NOT NULL CHECK (expected_star_status = 'NOT_COMPUTED'),
    expected_star_value numeric(4,2),
    expected_star_display_eligible boolean NOT NULL CHECK (expected_star_display_eligible = false),
    expected_star_confidence varchar(64) NOT NULL CHECK (expected_star_confidence = 'NOT_EVALUATED'),
    expected_star_confidence_policy_version varchar(160),
    FOREIGN KEY (exposure_batch_id, actor_user_id)
        REFERENCES recommendation_exposure_batch(exposure_batch_id, actor_user_id) ON DELETE CASCADE,
    UNIQUE (exposure_batch_id, position),
    UNIQUE (exposure_batch_id, movie_id),
    CHECK (expected_star_value IS NULL),
    CHECK (expected_star_confidence_policy_version IS NULL)
);

CREATE INDEX ix_recommendation_exposure_item_actor_movie
    ON recommendation_exposure_item(actor_user_id, movie_id, recommendation_item_id);

CREATE OR REPLACE FUNCTION enforce_recommendation_exposure_item_count() RETURNS trigger AS $$
DECLARE
    target_batch uuid;
    declared_count integer;
    actual_count integer;
BEGIN
    target_batch := COALESCE(NEW.exposure_batch_id, OLD.exposure_batch_id);
    SELECT item_count INTO declared_count
      FROM recommendation_exposure_batch
     WHERE exposure_batch_id = target_batch;
    IF declared_count IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    SELECT count(*) INTO actual_count
      FROM recommendation_exposure_item
     WHERE exposure_batch_id = target_batch;
    IF actual_count <> declared_count THEN
        RAISE EXCEPTION 'recommendation exposure item count invariant violated' USING ERRCODE = '23514';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_recommendation_exposure_batch_count
    AFTER INSERT OR UPDATE OF item_count ON recommendation_exposure_batch
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_recommendation_exposure_item_count();

CREATE CONSTRAINT TRIGGER trg_recommendation_exposure_item_count
    AFTER INSERT OR UPDATE OR DELETE ON recommendation_exposure_item
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_recommendation_exposure_item_count();
