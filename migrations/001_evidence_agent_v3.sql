BEGIN;

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS idempotency_key_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS request_fingerprint VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_user_idempotency
    ON agent_runs (user_id, idempotency_key_hash)
    WHERE idempotency_key_hash IS NOT NULL;

COMMIT;
