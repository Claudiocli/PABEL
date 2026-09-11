"""MCP server gating ABE-encrypted documents behind two principals: the
human operator (Keycloak, MFA-capable browser login only - see core.py's
module docstring) and the calling agent's own per-installation identity (a
Keycloak client_credentials token, verified fresh on every call - see
core.resolve_agent()). A document section decrypts only when both
principals' attributes together satisfy its policy; core.agent_session_key()
is what actually combines them into one ABE key.

This is a single, shared deployment: every agent product, and every
installation of it, talks to the same server instance (see
server/README.md and compose.yml) - "which agent is calling" is never
inferred from deployment topology or a self-declared value. Every
whoami/read_document call carries its own agent_token argument, verified by
core.resolve_agent() exactly like the human's bearer token is verified by
core.current_identity() - signature/issuer/expiry against Keycloak, then
resolved through the admin-managed agent_installations registry
(server/agents_admin.py) to find which agent_id product it belongs to, and
whether *this user* is authorized to use it at all. A user lacking the
product's required role isn't blocked outright; the agent simply
contributes no attribute, so agent-gated sections fail the same implicit
way an unrecognized installation would.

Every tool call re-verifies both principals fresh - core.current_identity()
for the human, core.resolve_agent() for the agent - nothing is cached as
"authenticated" beyond a single call. This server is strictly read-only: it
exposes no way to write or encrypt anything.

Deliberately stateless with respect to documents themselves: this server keeps
no store of its own to reach into or keep in sync (see read_document's
docstring) - it only ever decrypts bytes a caller hands it, for that one call.
That boundary was reaffirmed rather than a gap in 2026-08 when a client-side
"materialize a local decrypted copy" feature was designed
(connector/pabel_client/materialize.py): keeping this server able to enforce
freshness on such a copy after the fact would mean it starts owning a document
store and a way to reach a specific device unprompted, neither of which exist
today - see docs/phase2-engineering-notes.md for the reasoning. A materialized
copy is out of this server's - and this whole project's - reach the moment it
lands on a caller's disk, the same way any other plaintext a legitimate caller
receives is; the mitigation is bounding that copy's lifetime client-side
(deleted at session end), not chasing continuous server-side enforcement of
something the server structurally cannot see.

Supports both transports from the same code: PABEL_TRANSPORT=stdio (the
default - a local, per-session process, as Phase 1 used) or
PABEL_TRANSPORT=streamable-http (a remote, shared server - see compose.yml).
The auth/token_verifier wiring below is only ever exercised by the
streamable-http path (see token_verifier.py and core.current_identity()'s
docstring); it's harmless to construct regardless of transport.

Usage:
  python mcp_server.py                             # PABEL_TRANSPORT=stdio
  PABEL_TRANSPORT=streamable-http python mcp_server.py
"""

import base64
import os

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

import abe
import core
import env
from auth import AuthError
from token_verifier import KeycloakTokenVerifier

abe.cleanup_stale_temp_files()  # crash-recovery sweep, see abe.py

KEYCLOAK_URL, REALM = env.require("KEYCLOAK_URL", "REALM")
# What Keycloak actually stamps into a token's `iss` claim - whatever
# hostname the human's browser used to log in, not necessarily this
# container's own (different) route to reach Keycloak. Same value as
# KEYCLOAK_URL unless a deployment sets KEYCLOAK_ISSUER_URL - see
# auth.py's KeycloakAuth docstring for why these can differ.
KEYCLOAK_ISSUER_URL = os.environ.get("KEYCLOAK_ISSUER_URL", KEYCLOAK_URL)
PUBLIC_URL = os.environ.get("PABEL_PUBLIC_URL", "http://localhost:8000")

mcp = FastMCP(
    "pabel",
    token_verifier=KeycloakTokenVerifier(),
    auth=AuthSettings(
        issuer_url=f"{KEYCLOAK_ISSUER_URL.rstrip('/')}/realms/{REALM}",
        resource_server_url=PUBLIC_URL,
    ),
    # FastMCP's own host="127.0.0.1" default is always explicitly forwarded
    # to its internal Settings(), which shadows FASTMCP_HOST entirely - it
    # must be passed here to take effect. 127.0.0.1 is correct for stdio
    # (no HTTP server at all) and fine for streamable-http run locally, but
    # a container needs 0.0.0.0 or Docker's port mapping can't reach it.
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
)


@mcp.tool()
def whoami(agent_token: str) -> dict:
    """Identity and ABE attributes of the authenticated user, the calling
    agent installation, and whether this user is currently authorized to
    use it. Useful to debug why a section came back access-denied.

    `agent_token` is this installation's own Keycloak client_credentials
    access token (see core.resolve_agent()) - never a value this call
    invents or infers."""
    with core.audit_op("mcp", "whoami") as ctx:
        username, user_attributes, user_roles = core.current_identity()
        ctx["username"] = username
        ctx["auth_source"] = core.session.source()
        agent_id, agent_attributes = core.resolve_agent(agent_token, user_roles)
        ctx["agent_id"] = agent_id
        return {"username": username, "user_attributes": user_attributes,
                "agent_id": agent_id, "authorized_for_agent": bool(agent_attributes),
                "agent_attributes": agent_attributes or None}


@mcp.tool()
def read_document(content: str, agent_token: str, name: str = "document") -> dict:
    """Read an encrypted .abe document as the combination of the
    authenticated user's and the calling agent installation's attributes
    allows.

    `content` is the .abe file's raw text, base64-encoded (the agent
    already has the file - wherever it found it; this server keeps no
    document store of its own to reach into or keep in sync - and
    base64 keeps the JSON-shaped .abe text from being misread as a
    structured argument in transit). `agent_token` is this installation's
    own Keycloak client_credentials access token (see core.resolve_agent()).
    `name` is just a label for the response and audit log, not a path - it
    isn't resolved anywhere.

    Every section whose policy that combination satisfies is returned in
    full; every other section comes back as "[ACCESS DENIED]" - the same
    section list every caller sees, with different content, never fewer
    entries (so the document's shape is not itself a secret).
    """
    with core.audit_op("mcp", "read_document", path=name) as ctx:
        username, user_attributes, user_roles = core.current_identity()
        ctx["username"] = username
        ctx["auth_source"] = core.session.source()
        agent_id, agent_attributes = core.resolve_agent(agent_token, user_roles)
        ctx["agent_id"] = agent_id
        key_bytes = core.agent_session_key(
            username, user_attributes, agent_id, agent_attributes)
        sections = core.decrypt_document(base64.b64decode(content).decode("utf-8"), key_bytes)
        readable = sum(1 for s in sections if s["accessible"])
        ctx["detail"] = f"{readable}/{len(sections)} sections readable"
        return {"document": name, "user": username, "agent": agent_id,
                "readable_sections": readable, "total_sections": len(sections),
                "sections": sections}


@mcp.tool()
def report_denial(agent_token: str, decision_kind: str, reason: str, tool_name: str = "") -> dict:
    """Records, in the same audit trail as every other operation, a tool
    call this installation's own local enforcement (connector's
    core/decide.py) denied entirely client-side - one that never reaches
    read_document/whoami at all (DENY_CREDENTIAL_ACCESS, DENY_HOOK_BYPASS,
    DENY_CONFIG_TAMPER, DENY_MUTATING, DENY_OABE_BINARY, DENY_AMBIGUOUS).
    Without this tool, such an attempt would leave no record anywhere: the
    connector's own deny response only ever reaches the calling agent
    locally on every one of those paths.

    Deliberately more lenient than whoami/read_document about the human
    principal: several of the decisions worth reporting here (e.g. an
    attempt to read the credential store, or to tamper with this
    installation's own hook config) can happen before any human has ever
    logged in. A missing/invalid human session is recorded as
    username=None rather than rejecting the call outright - the one thing
    this tool must never do is fail to log an attempt just because
    identity resolution failed. `agent_token` (this installation's own
    Keycloak client_credentials token) is still required and still
    verified exactly like every other tool here: an attempt to report a
    denial as an installation that doesn't check out is itself worth an
    audit "denied" entry, not a silent no-op."""
    with core.audit_op("mcp", f"local_deny:{decision_kind}", path=tool_name or None) as ctx:
        try:
            username, _, user_roles = core.current_identity()
            ctx["username"] = username
            ctx["auth_source"] = core.session.source()
        except AuthError:
            user_roles = []
        agent_id, _ = core.resolve_agent(agent_token, user_roles)
        ctx["agent_id"] = agent_id
        ctx["detail"] = reason
        return {"recorded": True}


if __name__ == "__main__":
    mcp.run(transport=os.environ.get("PABEL_TRANSPORT", "stdio"))
