-- PABEL service schema.
--
-- user_keys / agent_keys are caches: "hash the current attribute string,
-- reuse the cached key if it still matches, regenerate otherwise" - the
-- only practical way to get key revocation given OpenABE has none natively
-- (see server/core.py). agents is the admin-managed registry of which AI
-- agent products may contribute attributes to a combined key, and is only
-- ever written by server/agents_admin.py, never by the running service.
-- audit_log is a queryable mirror of audit.jsonl.
--
-- Apply with: python -c "import db; db.init_schema()"  (see server/README.md)

CREATE TABLE IF NOT EXISTS agents (
    agent_id      TEXT PRIMARY KEY,          -- MUST match the Keycloak client_id exactly
    display_name  TEXT NOT NULL,
    attributes    TEXT NOT NULL,             -- '|'-joined tokens, e.g. 'agent_claude_code'
    required_role TEXT NOT NULL,             -- realm role a user's token must carry to receive
                                              -- these attributes (server/setup_keycloak_agent.py
                                              -- creates both the client and this role together)
    enabled       BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_keys (
    username        TEXT PRIMARY KEY,
    attributes_hash TEXT NOT NULL,           -- sha256 of the exact '|'-joined attribute string
    key_material    BYTEA NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_keys (
    username        TEXT NOT NULL,
    agent_id        TEXT NOT NULL REFERENCES agents(agent_id),
    attributes_hash TEXT NOT NULL,           -- sha256 of the combined '|'-joined string
    key_material    BYTEA NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (username, agent_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    client      TEXT NOT NULL,               -- 'mcp' | 'cli'
    operation   TEXT NOT NULL,               -- e.g. 'read_document', 'login'
    username    TEXT,
    agent_id    TEXT,
    auth_source TEXT,
    path        TEXT,
    result      TEXT NOT NULL,               -- 'ok' | 'denied' | 'error'
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log (ts);
CREATE INDEX IF NOT EXISTS audit_log_username_idx ON audit_log (username);
CREATE INDEX IF NOT EXISTS audit_log_agent_id_idx ON audit_log (agent_id);
