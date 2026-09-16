"""Shared identity/decryption core for the PABEL MCP service.

Two principals must check out before any content is returned:

  1. The human, via current_identity(): re-verifies a Keycloak bearer
     token's signature/issuer/expiry on every call, never cached as "logged
     in". The token comes from the auth middleware under streamable-http, or
     from .session.json under stdio - written only by a real Authorization
     Code + PKCE login through Keycloak's hosted page, so realm MFA always
     applies. There is no weaker fallback: no verified token means no
     identity, full stop.
  2. The agent, via resolve_agent(): every request carries its own
     per-installation client_credentials token, verified identically, whose
     `azp` claim resolves through an admin-managed agent_installations row
     to an agent_id. Nothing is trusted by deployment topology - one shared
     server serves every product, and which agent is calling is proven
     fresh per call. A forged/expired token, an unknown or revoked
     installation, and a disabled product are hard errors; a known
     installation whose required role the user lacks is soft, contributing
     zero attributes instead.

agent_session_key() combines both principals' attributes into one ABE key,
so a section decrypts only when the human *and* the agent together satisfy
its policy. An agent contributing no attribute fails such a policy
implicitly - there is no special-case deny logic anywhere here.

Every operation is wrapped in audit_op(), which writes one record per call
to audit.jsonl and the Postgres mirror. That trail is what the project
exists to provide: "who or what did this" must be answerable from the log
alone.
"""

import contextlib
import datetime
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import abe
import db
import document
import env
import oauth_browser
from auth import AuthError, KeycloakAuth

env.load()
kc = KeycloakAuth()

SERVICE_DIR = Path(__file__).resolve().parent
SESSION_FILE = SERVICE_DIR / ".session.json"
# Overridable because the containerized deployment (compose.yml) runs this
# file from /app, a path with nothing mounted back to the host by default -
# writes there would go to the container's own throwaway filesystem and
# vanish on the next `compose up`. compose.yml mounts a host directory and
# points this at a file inside it; every non-container run (stdio, dev
# scripts, tests) keeps today's behavior, a file next to this one.
AUDIT_LOG = Path(os.environ.get("PABEL_AUDIT_LOG_PATH", str(SERVICE_DIR / "audit.jsonl")))


def audit(client, operation, username=None, agent_id=None, auth_source=None,
         path=None, result="ok", detail=None):
    """Append one accountability record to audit.jsonl and the Postgres
    mirror. Never raises: a logging failure must not be the reason a
    request itself fails or succeeds wrongly."""
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "client": client,          # "mcp" | "cli"
        "operation": operation,    # e.g. "read_document", "login"
        "username": username,      # None when identity was never established
        "agent_id": agent_id,      # None for CLI human-only operations
        "auth_source": auth_source,
        "path": path,
        "result": result,          # "ok" | "denied" | "error"
        "detail": detail,
    }
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    db.insert_audit(client=client, operation=operation, username=username,
                    agent_id=agent_id, auth_source=auth_source, path=path,
                    result=result, detail=detail)  # itself never raises


@contextlib.contextmanager
def audit_op(client, operation, path=None):
    """Wrap one operation with exactly one audit record.

    Usage:
        with audit_op("mcp", "read_document", path=path) as ctx:
            username, _ = current_identity()
            ctx["username"] = username             # once known
            ctx["agent_id"] = agent_id             # once known
            ctx["detail"] = "2/3 sections readable"  # optional
            return {...}

    AuthError and PermissionError log as "denied", anything else as "error";
    all are re-raised unchanged, since this only observes. It accounts only
    for calls that go through here - code with direct access to this process
    bypasses it, the same way it bypasses current_identity(). That is a
    local-code-execution boundary, not something logging can close.
    """
    ctx = {"username": None, "agent_id": None, "auth_source": None, "detail": None}
    try:
        yield ctx
    except (AuthError, PermissionError) as e:
        audit(client, operation, username=ctx["username"], agent_id=ctx["agent_id"],
              auth_source=ctx["auth_source"], path=path, result="denied", detail=str(e))
        raise
    except Exception as e:
        audit(client, operation, username=ctx["username"], agent_id=ctx["agent_id"],
              auth_source=ctx["auth_source"], path=path, result="error", detail=str(e))
        raise
    else:
        audit(client, operation, username=ctx["username"], agent_id=ctx["agent_id"],
              auth_source=ctx["auth_source"], path=path, result="ok", detail=ctx["detail"])


class Session:
    """A Keycloak token pair sourced only from .session.json. Re-read
    whenever its mtime changes, so logging in as someone else, or out, takes
    effect on the next call with no process restart."""

    def __init__(self):
        self.tokens = None
        self.obtained_at = 0
        self._session_mtime = None

    def _read_session_file(self):
        mtime = SESSION_FILE.stat().st_mtime
        if mtime == self._session_mtime:
            return  # unchanged since last read
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        self.tokens = {"access_token": data["access_token"],
                       "refresh_token": data.get("refresh_token"),
                       "expires_in": data.get("expires_in", 60)}
        self.obtained_at = data["obtained_at"]
        self._session_mtime = mtime

    def source(self):
        """Constant today - sessions only ever come from the MFA-capable
        browser flow - but kept as an explicit audit field so an auditor
        doesn't have to assume it."""
        return "browser_session"

    def access_token(self):
        """A token believed to still be valid; verify() is the real check."""
        if not SESSION_FILE.exists():
            raise AuthError("not logged in; run: python login.py")
        self._read_session_file()
        if time.time() - self.obtained_at > self.tokens.get("expires_in", 60) - 5:
            try:
                self.tokens = kc.refresh(self.tokens["refresh_token"])
                self.obtained_at = time.time()
            except AuthError:
                raise AuthError(
                    "the logged-in session has expired or been revoked; "
                    "log in again (python login.py)") from None
        return self.tokens["access_token"]

    def invalidate(self):
        self.tokens = None
        self.obtained_at = 0
        self._session_mtime = None


session = Session()


def _persist_session(tokens, claims):
    SESSION_FILE.write_text(json.dumps({
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": tokens.get("expires_in", 60),
        "obtained_at": time.time(),
        "username": KeycloakAuth.username_of(claims),
    }, indent=2), encoding="utf-8")
    session.invalidate()  # force a fresh read of the file on next use


def login_with_browser(timeout=180):
    """Authorization Code + PKCE login through the system browser -
    Keycloak's own hosted login page runs, so whatever MFA the realm
    requires applies with nothing here able to bypass it. Persists the
    session to .session.json; mcp_server.py picks it up automatically."""
    tokens = oauth_browser.login_with_browser(timeout=timeout)
    claims = kc.verify(tokens["access_token"])
    _persist_session(tokens, claims)
    return KeycloakAuth.username_of(claims), KeycloakAuth.attributes_of(claims)


def logout():
    existed = SESSION_FILE.exists()
    if existed:
        SESSION_FILE.unlink()
    session.invalidate()
    return existed


def current_identity():
    """Re-verify the bearer token and return (username, attributes, roles).
    Runs at the start of every operation: this is the access-control
    checkpoint, not a one-time login. `roles` feeds resolve_agent(), kept
    here rather than verifying the token a second time to get it.

    Prefers the per-request token the auth middleware already verified
    (streamable-http), falling back to the session file when there's no such
    request context - which includes the whole stdio transport."""
    from mcp.server.auth.middleware.auth_context import get_access_token
    request_token = get_access_token()
    if request_token is not None and request_token.claims:
        claims = request_token.claims
    else:
        claims = kc.verify(session.access_token())
    return (KeycloakAuth.username_of(claims), KeycloakAuth.attributes_of(claims),
            KeycloakAuth.roles_of(claims))


def resolve_agent(agent_token, user_roles):
    """Verify a per-installation agent credential and return (agent_id,
    attributes) - attributes is "" if this user isn't authorized for it.

    Note the agent_id comes out of the installations table *after* the token
    verifies; it is never a parameter, so no caller can claim one.

    Unlike the human's, this token arrives as an explicit tool argument
    rather than the connection's bearer token, so it's verified here
    directly - the same kc.verify() current_identity() runs.

    Hard AuthError if the token doesn't verify, if its `azp` has no
    installation row or a revoked one, or if the product is unknown or
    disabled. Soft if the installation is valid but user_roles lacks the
    product's required_role: returns "" instead of raising, so the agent
    contributes nothing to the combined key. whoami still succeeds; only
    agent-gated sections come back "[ACCESS DENIED]"."""
    try:
        claims = kc.verify(agent_token)
    except AuthError as e:
        raise AuthError(f"agent credential rejected: {e}") from e
    client_id = KeycloakAuth.client_id_of(claims)
    installation = db.get_agent_installation(client_id)
    if installation is None or installation["revoked"]:
        raise AuthError(f"unrecognized or revoked agent installation: {client_id!r}")
    agent = db.get_agent(installation["agent_id"])
    if agent is None or not agent["enabled"]:
        raise AuthError(f"agent product {installation['agent_id']!r} is disabled")
    if agent["required_role"] not in user_roles:
        return installation["agent_id"], ""
    return installation["agent_id"], agent["attributes"]


def agent_session_key(username, user_attributes, agent_id, agent_attributes):
    """The combined (user, agent) ABE key, satisfying only policies both sets
    of attributes together satisfy. Cached per (username, agent_id) and
    regenerated whenever the hashed attribute string drifts: OpenABE has no
    native key revocation, so this hash-and-compare is the only way a
    Keycloak-side attribute change ever takes effect.

    agent_attributes may be "" - the key then carries only the user's own
    attributes, so any agent-gated policy fails as if the agent didn't
    exist."""
    if not user_attributes:
        raise AuthError(
            f"{username!r} has no ABE attributes assigned - ask a Keycloak "
            "realm admin to add some (User Profile: abe_attributes)")
    tokens = list(user_attributes)
    if agent_attributes:
        tokens.append(agent_attributes)
    combined = "|".join(tokens)
    attributes_hash = db.hash_attributes(combined)
    cached = db.get_agent_key(username, agent_id)
    if cached and cached[0] == attributes_hash:
        return cached[1]
    key_bytes = abe.keygen(combined)
    db.store_agent_key(username, agent_id, attributes_hash, key_bytes)
    return key_bytes


def _decrypt_groups(groups, key_bytes):
    """Decrypt every policy group in parallel: process startup dominates
    the cost of each oabe_dec call, so decrypting groups one at a time
    turns a sub-second operation into several seconds on a document with a
    few distinct policies - enough to blow past an MCP client's timeout."""
    with ThreadPoolExecutor(max_workers=min(len(groups), os.cpu_count() or 4) or 1) as pool:
        results = list(pool.map(lambda g: g.decrypt_with(key_bytes), groups))
    for g, result in zip(groups, results):
        if result is not None:
            for s, text in zip(g.sections, result):
                s.text = text


def decrypt_document(content, key_bytes):
    """[{"name","policy","accessible","text"}, ...] for the given .abe
    document's raw text - handed in by the caller (see mcp_server.py's
    read_document), not read from a server-side path."""
    sections, groups = document.load_abe(content)
    _decrypt_groups(groups, key_bytes)
    return [
        {"name": s.name, "policy": s.policy, "accessible": s.accessible,
         "text": s.text if s.accessible else "[ACCESS DENIED]"}
        for s in sections
    ]
