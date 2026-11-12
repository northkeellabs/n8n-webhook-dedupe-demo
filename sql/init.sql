-- Schema for the n8n webhook dedupe demo. All data written here is fictional.

-- Destination table: one row per processed event. Deliberately NO unique
-- constraint on event_key, so duplicate processing is visible as duplicate rows.
CREATE TABLE IF NOT EXISTS records (
    id          BIGSERIAL PRIMARY KEY,
    workflow    TEXT        NOT NULL,
    event_key   TEXT        NOT NULL,
    payload     JSONB,
    llm_summary TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS records_event_key_idx ON records (event_key);

-- Idempotency ledger: one row per event key.
CREATE TABLE IF NOT EXISTS processed_events (
    event_key  TEXT        PRIMARY KEY,
    status     TEXT        NOT NULL CHECK (status IN ('processing', 'done', 'failed')),
    attempts   INTEGER     NOT NULL DEFAULT 1,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
