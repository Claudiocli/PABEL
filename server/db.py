"""PostgreSQL-backed storage for the PABEL service.

Persists: cached user keys (auto-derived from live Keycloak attributes, see
core.user_key), cached agent-combined keys (see core.agent_session_key), the
agent product registry and the agent installation registry (both admin-managed
only, via agents_admin.py - never written by the running service itself), and
a queryable mirror of the audit trail (core.py also appends to audit.jsonl;
neither store's failure blocks the other or the request itself).

Connection string comes from PABEL_DB_DSN (server/.env.example), e.g.:
  postgresql://pabel:pabel@localhost:5432/pabel
"""

import hashlib
from pathlib import Path

import psycopg

import env

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _dsn():
    return env.require("PABEL_DB_DSN")[0]


def connect():
    return psycopg.connect(_dsn(), autocommit=True)


def init_schema():
    """Create the tables in schema.sql if they don't already exist."""
    with connect() as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def hash_attributes(attribute_string):
    return hashlib.sha256(attribute_string.encode("utf-8")).hexdigest()


# --- user keys: auto-derived cache, see core.user_key() ---------------------

def get_user_key(username):
    """(attributes_hash, key_material) or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT attributes_hash, key_material FROM user_keys WHERE username = %s",
            (username,)).fetchone()
        return tuple(row) if row else None


def store_user_key(username, attributes_hash, key_material):
    with connect() as conn:
        conn.execute(
            "INSERT INTO user_keys (username, attributes_hash, key_material) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (username) DO UPDATE SET "
            "  attributes_hash = EXCLUDED.attributes_hash, "
            "  key_material = EXCLUDED.key_material, "
            "  updated_at = now()",
            (username, attributes_hash, key_material))


# --- agent registry: written only by agents_admin.py -------------------------

def get_agent(agent_id):
    """{'attributes', 'required_role', 'enabled'} or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT attributes, required_role, enabled FROM agents WHERE agent_id = %s",
            (agent_id,)).fetchone()
        if row is None:
            return None
        return {"attributes": row[0], "required_role": row[1], "enabled": row[2]}


def add_agent(agent_id, display_name, attributes, required_role):
    with connect() as conn:
        conn.execute(
            "INSERT INTO agents (agent_id, display_name, attributes, required_role) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (agent_id) DO UPDATE SET "
            "  display_name = EXCLUDED.display_name, "
            "  attributes = EXCLUDED.attributes, "
            "  required_role = EXCLUDED.required_role, "
            "  updated_at = now()",
            (agent_id, display_name, attributes, required_role))


def list_agents():
    """[(agent_id, display_name, attributes, enabled, created_at, updated_at), ...]."""
    with connect() as conn:
        return conn.execute(
            "SELECT agent_id, display_name, attributes, enabled, created_at, updated_at "
            "FROM agents ORDER BY agent_id").fetchall()


def set_agent_enabled(agent_id, enabled):
    """True if a row was updated, False if agent_id doesn't exist."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE agents SET enabled = %s, updated_at = now() WHERE agent_id = %s",
            (enabled, agent_id))
        return cur.rowcount > 0


# --- agent installations: the real per-install identity, written only by
# agents_admin.py (never by the running service, never by a network-reachable
# path - see schema.sql) --------------------------------------------------

def add_agent_installation(client_id, agent_id, label=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO agent_installations (client_id, agent_id, label) "
            "VALUES (%s, %s, %s)",
            (client_id, agent_id, label))


def get_agent_installation(client_id):
    """{'agent_id', 'revoked'} or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT agent_id, revoked FROM agent_installations WHERE client_id = %s",
            (client_id,)).fetchone()
        if row is None:
            return None
        return {"agent_id": row[0], "revoked": row[1]}


def list_agent_installations(agent_id=None):
    """[(client_id, agent_id, label, revoked, enrolled_at, revoked_at), ...],
    optionally filtered to one product."""
    with connect() as conn:
        if agent_id is None:
            return conn.execute(
                "SELECT client_id, agent_id, label, revoked, enrolled_at, revoked_at "
                "FROM agent_installations ORDER BY enrolled_at").fetchall()
        return conn.execute(
            "SELECT client_id, agent_id, label, revoked, enrolled_at, revoked_at "
            "FROM agent_installations WHERE agent_id = %s ORDER BY enrolled_at",
            (agent_id,)).fetchall()


def set_installation_revoked(client_id, revoked):
    """True if a row was updated, False if client_id doesn't exist."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE agent_installations SET revoked = %s, "
            "  revoked_at = CASE WHEN %s THEN now() ELSE NULL END "
            "WHERE client_id = %s",
            (revoked, revoked, client_id))
        return cur.rowcount > 0


# --- agent-combined keys: auto-derived cache, see core.agent_session_key() --

def get_agent_key(username, agent_id):
    """(attributes_hash, key_material) or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT attributes_hash, key_material FROM agent_keys "
            "WHERE username = %s AND agent_id = %s",
            (username, agent_id)).fetchone()
        return tuple(row) if row else None


def store_agent_key(username, agent_id, attributes_hash, key_material):
    with connect() as conn:
        conn.execute(
            "INSERT INTO agent_keys (username, agent_id, attributes_hash, key_material) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (username, agent_id) DO UPDATE SET "
            "  attributes_hash = EXCLUDED.attributes_hash, "
            "  key_material = EXCLUDED.key_material, "
            "  updated_at = now()",
            (username, agent_id, attributes_hash, key_material))


# --- audit mirror -------------------------------------------------------------

def insert_audit(**fields):
    """Append one row to audit_log. Never raises: this is a mirror of
    audit.jsonl, not the primary record - a DB outage must not be the
    reason a request fails, nor silently lose the local log."""
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (client, operation, username, agent_id, "
                "  auth_source, path, result, detail) "
                "VALUES (%(client)s, %(operation)s, %(username)s, %(agent_id)s, "
                "  %(auth_source)s, %(path)s, %(result)s, %(detail)s)",
                fields)
    except psycopg.Error:
        pass
