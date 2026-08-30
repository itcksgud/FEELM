CREATE TABLE c4_local_selection_policy (
    policy_version varchar(128) PRIMARY KEY,
    target_count integer NOT NULL CHECK (target_count BETWEEN 0 AND 10),
    active boolean NOT NULL DEFAULT false
);

CREATE UNIQUE INDEX ux_c4_local_active_selection_policy
    ON c4_local_selection_policy(active) WHERE active;

INSERT INTO c4_local_selection_policy (policy_version, target_count, active)
VALUES ('c4-local-ui-ready-popularity-v1', 10, true);
