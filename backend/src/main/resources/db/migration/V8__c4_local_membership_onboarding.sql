CREATE TABLE c4_user_account (
    user_id uuid PRIMARY KEY,
    membership_status varchar(40) NOT NULL CHECK (membership_status IN ('PENDING_EMAIL_VERIFICATION', 'ACTIVE')),
    created_at timestamptz NOT NULL,
    activated_at timestamptz,
    pending_purge_at timestamptz NOT NULL,
    CHECK ((membership_status = 'ACTIVE') = (activated_at IS NOT NULL))
);

CREATE TABLE c4_email_credential (
    user_id uuid PRIMARY KEY REFERENCES c4_user_account(user_id) ON DELETE CASCADE,
    email_normalized varchar(320) NOT NULL UNIQUE,
    password_phc text NOT NULL CHECK (password_phc LIKE '$argon2id$%'),
    created_at timestamptz NOT NULL
);

CREATE TABLE c4_user_profile (
    user_id uuid PRIMARY KEY REFERENCES c4_user_account(user_id) ON DELETE CASCADE,
    nickname varchar(20) NOT NULL,
    nickname_normalized varchar(20) NOT NULL UNIQUE,
    normalization_version varchar(32) NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    nickname_changed_at timestamptz NOT NULL
);

CREATE TABLE c4_email_signup_flow (
    signup_id uuid PRIMARY KEY,
    flow_kind varchar(8) NOT NULL CHECK (flow_kind IN ('REAL', 'DECOY')),
    user_id uuid REFERENCES c4_user_account(user_id) ON DELETE CASCADE,
    email_masked varchar(320) NOT NULL,
    flow_status varchar(16) NOT NULL CHECK (flow_status IN ('OPEN', 'VERIFIED', 'EXPIRED', 'EXHAUSTED')),
    current_challenge_id uuid,
    failed_attempt_count integer NOT NULL DEFAULT 0 CHECK (failed_attempt_count BETWEEN 0 AND 5),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL,
    flow_expires_at timestamptz NOT NULL,
    verification_expires_at timestamptz NOT NULL,
    resend_available_at timestamptz NOT NULL,
    CHECK ((flow_kind = 'REAL') = (user_id IS NOT NULL)),
    CHECK (flow_kind = 'REAL' OR current_challenge_id IS NULL)
);

CREATE UNIQUE INDEX ux_c4_open_real_flow_per_user
    ON c4_email_signup_flow(user_id) WHERE flow_kind = 'REAL' AND flow_status = 'OPEN';

CREATE TABLE c4_email_verification_challenge (
    challenge_id uuid PRIMARY KEY,
    signup_id uuid NOT NULL REFERENCES c4_email_signup_flow(signup_id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES c4_user_account(user_id) ON DELETE CASCADE,
    challenge_version integer NOT NULL CHECK (challenge_version >= 1),
    secret_sha256 char(64) NOT NULL CHECK (secret_sha256 ~ '^[a-f0-9]{64}$'),
    challenge_status varchar(16) NOT NULL CHECK (challenge_status IN ('ACTIVE', 'CONSUMED', 'SUPERSEDED', 'EXPIRED')),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (signup_id, challenge_version)
);

ALTER TABLE c4_email_signup_flow
    ADD CONSTRAINT fk_c4_flow_current_challenge
    FOREIGN KEY (current_challenge_id) REFERENCES c4_email_verification_challenge(challenge_id);

CREATE UNIQUE INDEX ux_c4_active_challenge_per_flow
    ON c4_email_verification_challenge(signup_id) WHERE challenge_status = 'ACTIVE';

CREATE TABLE c4_verification_delivery_material (
    material_id uuid PRIMARY KEY,
    challenge_id uuid NOT NULL UNIQUE REFERENCES c4_email_verification_challenge(challenge_id) ON DELETE CASCADE,
    ciphertext bytea NOT NULL,
    nonce bytea NOT NULL CHECK (octet_length(nonce) = 12),
    key_version varchar(32) NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE c4_mail_outbox (
    outbox_id uuid PRIMARY KEY,
    challenge_id uuid NOT NULL UNIQUE REFERENCES c4_email_verification_challenge(challenge_id) ON DELETE CASCADE,
    material_id uuid NOT NULL UNIQUE,
    delivery_status varchar(16) NOT NULL CHECK (delivery_status IN ('PENDING', 'DELIVERED', 'FAILED')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error_code varchar(64),
    created_at timestamptz NOT NULL,
    delivered_at timestamptz
);

CREATE TABLE c4_auth_session (
    session_id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES c4_user_account(user_id) ON DELETE CASCADE,
    session_status varchar(16) NOT NULL CHECK (session_status IN ('ACTIVE', 'REVOKED', 'EXPIRED')),
    csrf_sha256 char(64) NOT NULL CHECK (csrf_sha256 ~ '^[a-f0-9]{64}$'),
    current_generation integer NOT NULL CHECK (current_generation >= 1),
    created_at timestamptz NOT NULL,
    idle_expires_at timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    terminal_at timestamptz
);

CREATE TABLE c4_refresh_token (
    token_id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES c4_auth_session(session_id) ON DELETE CASCADE,
    generation integer NOT NULL CHECK (generation >= 1),
    token_sha256 char(64) NOT NULL UNIQUE CHECK (token_sha256 ~ '^[a-f0-9]{64}$'),
    csrf_sha256 char(64) NOT NULL CHECK (csrf_sha256 ~ '^[a-f0-9]{64}$'),
    token_status varchar(16) NOT NULL CHECK (token_status IN ('ACTIVE', 'ROTATED', 'REVOKED')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    rotated_at timestamptz,
    UNIQUE (session_id, generation)
);

CREATE UNIQUE INDEX ux_c4_active_refresh_per_session
    ON c4_refresh_token(session_id) WHERE token_status = 'ACTIVE';

CREATE TABLE c4_onboarding_journey (
    user_id uuid PRIMARY KEY REFERENCES c4_user_account(user_id) ON DELETE CASCADE,
    journey_status varchar(16) NOT NULL CHECK (journey_status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'SKIPPED')),
    catalog_version varchar(128),
    selection_policy_version varchar(128),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    recommendation_projection varchar(16) NOT NULL DEFAULT 'NOT_REQUESTED'
        CHECK (recommendation_projection IN ('NOT_REQUESTED', 'PENDING', 'READY', 'FAILED')),
    updated_at timestamptz NOT NULL
);

CREATE TABLE c4_onboarding_preference (
    user_id uuid NOT NULL REFERENCES c4_user_account(user_id) ON DELETE CASCADE,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    preference varchar(8) NOT NULL CHECK (preference IN ('LIKE', 'DISLIKE')),
    selected_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, movie_id)
);

CREATE TABLE c4_ott_subscription_set (
    user_id uuid PRIMARY KEY REFERENCES c4_user_account(user_id) ON DELETE CASCADE,
    selection_status varchar(20) NOT NULL CHECK (selection_status IN ('NOT_CONFIGURED', 'CONFIGURED', 'SKIPPED')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at timestamptz NOT NULL
);

CREATE TABLE c4_user_ott_subscription (
    user_id uuid NOT NULL REFERENCES c4_user_account(user_id) ON DELETE CASCADE,
    provider_id uuid NOT NULL REFERENCES ott_provider(id),
    selected_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, provider_id)
);

CREATE TABLE c4_idempotency_result (
    scope_id uuid NOT NULL,
    operation_code varchar(80) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_hmac char(64) NOT NULL CHECK (request_hmac ~ '^[a-f0-9]{64}$'),
    response_status integer NOT NULL CHECK (response_status BETWEEN 200 AND 299),
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (scope_id, operation_code, idempotency_key),
    CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    CHECK (idempotency_key ~ '^[!-~]+$')
);

CREATE TABLE c4_auth_rate_counter (
    counter_key char(64) NOT NULL,
    window_started_at timestamptz NOT NULL,
    counter_value integer NOT NULL CHECK (counter_value >= 1),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (counter_key, window_started_at)
);

CREATE INDEX ix_c4_flow_expiry ON c4_email_signup_flow(flow_expires_at);
CREATE INDEX ix_c4_refresh_session ON c4_refresh_token(session_id, generation DESC);
CREATE INDEX ix_c4_preference_user ON c4_onboarding_preference(user_id);
