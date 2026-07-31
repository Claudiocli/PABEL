-- PABEL service schema.
--
-- user_keys / agent_keys are caches: "hash the current attribute string,
-- reuse the cached key if it still matches, regenerate otherwise" - the
-- only practical way to get key revocation given OpenABE has none natively
-- (see server/core.py). agents is the admin-managed registry of which AI
-- agent product may contribute attributes to a combined key; agent_installations
-- is the admin-managed registry of which real, per-installation Keycloak client
-- may act as a given agent_id. Both are only ever written by
-- server/agents_admin.py, never by the running service. audit_log is a
-- queryable mirror of audit.jsonl.
--
-- Apply with: python -c "import db; db.init_schema()"  (see server/README.md)

CREATE TABLE IF NOT EXISTS agents (
    agent_id      TEXT PRIMARY KEY,          -- an admin-chosen product slug, e.g. 'claude-code' -
                                              -- NOT a Keycloak client_id (see agent_installations
                                              -- below for the real, per-installation identity)
    display_name  TEXT NOT NULL,
    attributes    TEXT NOT NULL,             -- '|'-joined tokens, e.g. 'agent_claude_code'
    required_role TEXT NOT NULL,             -- realm role a user's token must carry to receive
                                              -- these attributes
    enabled       BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per real, per-installation Keycloak client_credentials client - the
-- actual cryptographic identity a running connector presents on every request
-- (core.resolve_agent's azp lookup). Many installations belong to one agent_id
-- product; each is independently revocable without affecting any other. Rows
-- here are only ever written by an admin (agents_admin.py create-installation,
-- or by hand in the Keycloak admin console + agents_admin.py register), never
-- by a network-reachable code path - an employee's own machine never creates
-- its own credential, it only ever receives one an admin already created.
CREATE TABLE IF NOT EXISTS agent_installations (
    client_id    TEXT PRIMARY KEY,          -- this installation's own Keycloak client_id -
                                             -- == the verified `azp` claim on its tokens
    agent_id     TEXT NOT NULL REFERENCES agents(agent_id),
    label        TEXT,                      -- optional admin free text (e.g. a hostname) - display only
    enrolled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked      BOOLEAN NOT NULL DEFAULT false,
    revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS agent_installations_agent_id_idx ON agent_installations (agent_id);

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
