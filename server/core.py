"""Shared identity/decryption core for the PABEL MCP service.

Two principals must check out before any document content is returned -
see mcp_server.py, the only thing that calls into this module:

  1. The human, via current_identity(): re-verifies a Keycloak bearer
     token's signature/issuer/expiry fresh on every single call (never
     cached as "logged in"). Two transports, two sources for that token,
     same verification either way:
       - streamable-http (a remote, shared server - see mcp_server.py):
         the token mcp.server.auth's bearer-auth middleware already
         verified for this request via token_verifier.py's
         KeycloakTokenVerifier, read back out via
         mcp.server.auth.middleware.auth_context.get_access_token().
       - stdio (a local, per-session process): .session.json, written
         only by login_with_browser() below, i.e. only after a real
         Authorization Code + PKCE login through Keycloak's own hosted
         page, which enforces whatever MFA the realm requires.
     There is no weaker fallback identity of any kind in either case: no
     verified token and no session file means no identity, full stop.
  2. The agent, via resolve_agent(): each agent product is its own
     container instance with a fixed PABEL_AGENT_ID (not a per-request
     claim), and a Postgres registry entry (server/agents_admin.py,
     admin-run only) names a Keycloak realm role a user's token must
     carry to be allowed to use it. This is deliberately two different
     failure modes: an agent_id this server has never heard of at all is
     a hard AuthError (nothing legitimate talks to this server as an
     unregistered agent); a *known* agent whose required role the current
     user's token lacks contributes zero attributes rather than erroring
     - see below.

read_document (mcp_server.py) combines both principals' ABE attributes into
one key via agent_session_key() - a document section decrypts only when
its policy is satisfied by the human *and* the agent together. An agent
product with no registry row, or whose required role this user's token
doesn't carry, contributes no attribute at all, so it fails such a policy
implicitly, with no special-case deny logic anywhere here.

Every operation is wrapped in audit_op() below, which appends one record
per call to both audit.jsonl and the Postgres audit_log mirror (db.py) -
who (once known), which agent, what, on what path, and the outcome. This
is the accountability trail the whole project exists to provide: "who or
what did this" must be answerable from the log alone.
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
AUDIT_LOG = SERVICE_DIR / "audit.jsonl"


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

    AuthError (bad/missing/expired identity or agent key) and
    PermissionError (a valid identity asking for something it isn't
    entitled to) are logged as "denied"; any other exception as "error";
    both re-raised unchanged - this only observes, it never changes
    behavior. This only accounts for calls that go through here in the
    first place - code with direct access to this process bypasses it
    entirely, same as it bypasses current_identity(); that is a
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
    """A Keycloak token pair sourced only from .session.json (see module
    docstring - there is no fallback identity). Re-read whenever the
    file's mtime changes, so logging in as someone else (or logging out)
    takes effect on the very next call, no process restart needed."""

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
        """Recorded in every audit row: this project only ever produces a
        session via the MFA-capable browser flow, so this is constant
        today - kept as an explicit field rather than dropped, so an
        auditor reading audit_log doesn't have to assume it."""
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
    Called at the start of every operation: this is the actual
    access-control checkpoint, not a one-time login. roles feeds
    resolve_agent() below - it's kept alongside attributes here rather
    than re-verifying the token a second time to get it.

    Prefers the per-request token mcp.server.auth already verified
    (streamable-http transport); falls back to the stdio session file
    only when there's no such request context (get_access_token()
    returns None outside of a streamable-http request, including the
    entire stdio transport - see token_verifier.py)."""
    from mcp.server.auth.middleware.auth_context import get_access_token
    request_token = get_access_token()
    if request_token is not None and request_token.claims:
        claims = request_token.claims
    else:
        claims = kc.verify(session.access_token())
    return (KeycloakAuth.username_of(claims), KeycloakAuth.attributes_of(claims),
            KeycloakAuth.roles_of(claims))


def resolve_agent(agent_id, user_roles):
    """The agent's attribute string - or "" if this user isn't authorized
    to use it. agent_id is this server instance's own fixed identity (one
    container per agent product, see server/README.md), not a per-request
    claim, so there's nothing here for a caller to spoof.

    Two different failure modes, deliberately:
      - agent_id isn't in the registry at all (or is disabled): raises
        AuthError. Nothing legitimate talks to this server under an
        agent_id it was never configured with.
      - agent_id is registered, but user_roles doesn't include its
        required_role (server/agents_admin.py, admin-assigned per user):
        returns "" rather than raising. This is a *known* agent the
        current user simply isn't authorized for, so it contributes no
        attribute to the combined key - the same implicit,
        section-by-section cryptographic denial as an unregistered
        agent, just scoped to one user instead of everyone. whoami
        still succeeds; only agent-gated sections of read_document
        come back "[ACCESS DENIED]"."""
    agent = db.get_agent(agent_id)
    if agent is None or not agent["enabled"]:
        raise AuthError(f"this server is not configured as a known agent: {agent_id!r}")
    if agent["required_role"] not in user_roles:
        return ""
    return agent["attributes"]


def user_key(username, user_attributes):
    """The ABE key for username's current Keycloak attributes, regenerating
    it when they've drifted since the last call. OpenABE has no native key
    revocation (confirmed against its source), so this hash-and-compare
    against db.get_user_key()'s cache is the only way a Keycloak-side
    attribute change ever takes effect."""
    if not user_attributes:
        raise AuthError(
            f"{username!r} has no ABE attributes assigned - ask a Keycloak "
            "realm admin to add some (User Profile: abe_attributes)")
    joined = "|".join(user_attributes)
    attributes_hash = db.hash_attributes(joined)
    cached = db.get_user_key(username)
    if cached and cached[0] == attributes_hash:
        return cached[1]
    key_bytes = abe.keygen(joined)
    db.store_user_key(username, attributes_hash, key_bytes)
    return key_bytes


def agent_session_key(username, user_attributes, agent_id, agent_attributes):
    """The combined (user, agent) ABE key: satisfies only a policy that
    both the human's and the agent's attributes together satisfy. Same
    drift-detection/regeneration pattern as user_key(), cached per pair.

    agent_attributes may be "" (resolve_agent() found a known agent this
    user isn't authorized for) - the combined key then carries only the
    user's own attributes, so any policy requiring an agent attribute
    fails, same as if the agent didn't exist at all."""
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
