CREATE TABLE c3_local_fake_actor (
    actor_id uuid PRIMARY KEY,
    nickname varchar(60) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    CHECK (length(trim(nickname)) > 0)
);

CREATE TABLE c3_availability_materialization (
    id uuid PRIMARY KEY,
    catalog_version_id uuid NOT NULL REFERENCES catalog_version(id),
    public_catalog_version varchar(128) NOT NULL,
    region char(2) NOT NULL CHECK (region = 'KR'),
    monetization_type varchar(16) NOT NULL CHECK (monetization_type = 'FLATRATE'),
    status varchar(16) NOT NULL CHECK (status IN ('BUILDING', 'COMPLETE', 'FAILED')),
    created_at timestamptz NOT NULL
);

CREATE UNIQUE INDEX uk_c3_complete_materialization
    ON c3_availability_materialization(region, monetization_type)
    WHERE status = 'COMPLETE';

CREATE TABLE c3_availability_provider (
    materialization_id uuid NOT NULL REFERENCES c3_availability_materialization(id) ON DELETE CASCADE,
    provider_id uuid NOT NULL REFERENCES ott_provider(id),
    PRIMARY KEY (materialization_id, provider_id)
);

CREATE TABLE c3_availability_membership (
    materialization_id uuid NOT NULL REFERENCES c3_availability_materialization(id) ON DELETE CASCADE,
    provider_id uuid NOT NULL REFERENCES ott_provider(id),
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    catalog_popularity_rank integer NOT NULL CHECK (catalog_popularity_rank >= 1),
    PRIMARY KEY (materialization_id, provider_id, movie_id)
);

CREATE TABLE c3_party (
    party_id uuid PRIMARY KEY,
    owner_actor_id uuid NOT NULL REFERENCES c3_local_fake_actor(actor_id),
    name varchar(60) NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE')),
    member_count integer NOT NULL CHECK (member_count BETWEEN 1 AND 4),
    revision integer NOT NULL CHECK (revision >= 1),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CHECK (length(trim(name)) > 0)
);

CREATE TABLE c3_party_member (
    party_id uuid NOT NULL REFERENCES c3_party(party_id) ON DELETE CASCADE,
    member_id uuid NOT NULL,
    actor_id uuid NOT NULL REFERENCES c3_local_fake_actor(actor_id),
    role varchar(16) NOT NULL CHECK (role IN ('OWNER', 'MEMBER')),
    joined_at timestamptz NOT NULL,
    PRIMARY KEY (party_id, member_id),
    UNIQUE (party_id, actor_id)
);

CREATE UNIQUE INDEX uk_c3_party_owner_member
    ON c3_party_member(party_id) WHERE role = 'OWNER';

CREATE TABLE c3_party_provider (
    party_id uuid NOT NULL REFERENCES c3_party(party_id) ON DELETE CASCADE,
    provider_id uuid NOT NULL REFERENCES ott_provider(id),
    PRIMARY KEY (party_id, provider_id)
);

CREATE TABLE c3_party_invitation (
    invitation_id uuid PRIMARY KEY,
    party_id uuid NOT NULL REFERENCES c3_party(party_id) ON DELETE CASCADE,
    inviter_actor_id uuid NOT NULL REFERENCES c3_local_fake_actor(actor_id),
    recipient_actor_id uuid NOT NULL REFERENCES c3_local_fake_actor(actor_id),
    status varchar(16) NOT NULL CHECK (status IN ('PENDING', 'ACCEPTED')),
    revision integer NOT NULL CHECK (revision >= 1),
    created_at timestamptz NOT NULL,
    accepted_at timestamptz,
    CHECK (inviter_actor_id <> recipient_actor_id),
    CHECK ((status = 'PENDING' AND accepted_at IS NULL) OR (status = 'ACCEPTED' AND accepted_at IS NOT NULL))
);

CREATE UNIQUE INDEX uk_c3_pending_party_recipient
    ON c3_party_invitation(party_id, recipient_actor_id) WHERE status = 'PENDING';

CREATE TABLE c3_ott_catalog_comparison (
    comparison_id uuid PRIMARY KEY,
    owner_actor_id uuid NOT NULL REFERENCES c3_local_fake_actor(actor_id),
    materialization_id uuid NOT NULL REFERENCES c3_availability_materialization(id),
    status varchar(16) NOT NULL CHECK (status = 'READY'),
    created_at timestamptz NOT NULL
);

CREATE TABLE c3_ott_catalog_provider (
    comparison_id uuid NOT NULL REFERENCES c3_ott_catalog_comparison(comparison_id) ON DELETE CASCADE,
    provider_id uuid NOT NULL REFERENCES ott_provider(id),
    movie_count integer NOT NULL CHECK (movie_count >= 0),
    PRIMARY KEY (comparison_id, provider_id)
);

CREATE TABLE c3_ott_catalog_movie (
    comparison_id uuid NOT NULL,
    provider_id uuid NOT NULL,
    movie_id uuid NOT NULL REFERENCES movie_identity(id),
    display_title varchar(500) NOT NULL,
    poster_url text,
    release_year integer CHECK (release_year IS NULL OR release_year BETWEEN 1870 AND 2100),
    available_provider_ids uuid[] NOT NULL,
    popularity_rank integer NOT NULL CHECK (popularity_rank >= 1),
    PRIMARY KEY (comparison_id, provider_id, movie_id),
    FOREIGN KEY (comparison_id, provider_id)
        REFERENCES c3_ott_catalog_provider(comparison_id, provider_id) ON DELETE CASCADE,
    CHECK (cardinality(available_provider_ids) BETWEEN 1 AND 4),
    CHECK (provider_id = ANY(available_provider_ids))
);

CREATE TABLE c3_idempotency_result (
    actor_id uuid NOT NULL REFERENCES c3_local_fake_actor(actor_id),
    operation varchar(80) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_sha256 char(64) NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
    response_status integer NOT NULL CHECK (response_status BETWEEN 200 AND 299),
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (actor_id, operation, idempotency_key),
    CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    CHECK (idempotency_key ~ '^[!-~]+$')
);

CREATE INDEX ix_c3_party_member_actor ON c3_party_member(actor_id, joined_at DESC);
CREATE INDEX ix_c3_invitation_recipient ON c3_party_invitation(recipient_actor_id, created_at DESC);
CREATE INDEX ix_c3_comparison_owner ON c3_ott_catalog_comparison(owner_actor_id, created_at DESC);
