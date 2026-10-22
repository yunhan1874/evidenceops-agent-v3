BEGIN;

CREATE TABLE IF NOT EXISTS run_events (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    event_type VARCHAR(32) NOT NULL,
    name VARCHAR(128) NOT NULL,
    parent_name VARCHAR(128),
    status VARCHAR(32) NOT NULL,
    duration_ms INTEGER,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_run_events_run_id ON run_events(run_id);
CREATE INDEX IF NOT EXISTS ix_run_events_event_type ON run_events(event_type);
CREATE INDEX IF NOT EXISTS ix_run_events_name ON run_events(name);
CREATE INDEX IF NOT EXISTS ix_run_events_status ON run_events(status);

COMMIT;

