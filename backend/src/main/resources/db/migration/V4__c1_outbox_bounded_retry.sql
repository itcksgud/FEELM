ALTER TABLE domain_outbox
    DROP CONSTRAINT domain_outbox_status_check;

ALTER TABLE domain_outbox
    ADD CONSTRAINT domain_outbox_status_check
    CHECK (status IN ('PENDING', 'PROCESSING', 'PROCESSED', 'FAILED', 'DEAD_LETTER'));
