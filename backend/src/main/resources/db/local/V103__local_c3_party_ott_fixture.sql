INSERT INTO c3_local_fake_actor (actor_id, nickname, enabled) VALUES
    ('018f6826-4da1-7c38-a846-8f794cd8b0cf', 'local_owner', true),
    ('4d85e2ae-87ce-4f48-8ac1-fabf89bb1371', 'film_a', true),
    ('bb5799ab-7654-4e01-8e0f-c1fe583d340d', 'film_b', true),
    ('85b0fa76-5b3e-4fcb-8846-807b466e757d', 'film_c', true),
    ('83b8c4bd-7027-4b5a-86cc-82ccb574da64', 'local_other', true);

INSERT INTO c3_availability_materialization (
    id, catalog_version_id, public_catalog_version, region, monetization_type, status, created_at
) VALUES (
    '30000000-0000-0000-0000-000000000001',
    '10000000-0000-0000-0000-000000000002',
    'catalog-fixture-20260829-01', 'KR', 'FLATRATE', 'COMPLETE', '2026-08-29T06:30:00Z'
);

INSERT INTO c3_availability_provider (materialization_id, provider_id) VALUES
    ('30000000-0000-0000-0000-000000000001', 'd392a4d5-0428-4e06-aa41-aef899c06842'),
    ('30000000-0000-0000-0000-000000000001', '4f57022d-6d8e-40b2-b7be-4ac313ef6bd0'),
    ('30000000-0000-0000-0000-000000000001', '1f0c5888-f6f4-42a9-b661-a90cff45e303');

INSERT INTO c3_availability_membership (
    materialization_id, provider_id, movie_id, catalog_popularity_rank
) VALUES
    ('30000000-0000-0000-0000-000000000001', 'd392a4d5-0428-4e06-aa41-aef899c06842', 'cc3ddb45-0511-46ea-bf28-95b67c9fd20f', 1),
    ('30000000-0000-0000-0000-000000000001', '4f57022d-6d8e-40b2-b7be-4ac313ef6bd0', 'cc3ddb45-0511-46ea-bf28-95b67c9fd20f', 1),
    ('30000000-0000-0000-0000-000000000001', 'd392a4d5-0428-4e06-aa41-aef899c06842', '6b226903-0ca4-4f5a-9bf0-50d6cedd224c', 2),
    ('30000000-0000-0000-0000-000000000001', 'd392a4d5-0428-4e06-aa41-aef899c06842', 'e67778c9-7b2e-42d4-9d3e-a3026b2efea3', 3),
    ('30000000-0000-0000-0000-000000000001', '4f57022d-6d8e-40b2-b7be-4ac313ef6bd0', '19406c31-213f-4fe1-93f6-109f8570ec20', 4);
