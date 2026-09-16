"""MCP server gating ABE-encrypted documents behind two principals: the
human operator (Keycloak, MFA-capable browser login only - see core.py's
module docstring) and the calling agent's own per-installation identity (a
Keycloak client_credentials token, verified fresh on every call - see
core.resolve_agent()). A document section decrypts only when both
principals' attributes together satisfy its policy; core.agent_session_key()
is what actually combines them into one ABE key.

A single shared deployment: every agent product and installation talks to
this same instance, so "which agent is calling" is never inferred from
topology or a self-declared value. Every call carries its own agent_token,
verified like the human's bearer token - signature/issuer/expiry against
Keycloak, then resolved through the admin-managed agent_installations
registry. A user lacking the product's required role isn't blocked outright:
the agent contributes no attribute, so agent-gated sections fail the same
implicit way an unrecognized installation would.

Both principals are re-verified on every call; nothing is cached as
"authenticated" beyond one call. Strictly read-only - no way to write or
encrypt anything.

Deliberately stateless about documents: it only decrypts bytes a caller
hands it, for that one call. So a materialized local copy
(connector/pabel_client/materialize.py) is out of reach the moment it lands
on a caller's disk; the mitigation is bounding its lifetime client-side, not
chasing enforcement this server structurally cannot perform.

Supports both transports from the same code. The auth/token_verifier wiring
below is only exercised by streamable-http, but is harmless under stdio.

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
# What Keycloak stamps into a token's `iss` - the hostname the browser used,
# not necessarily this container's own route to reach Keycloak.
KEYCLOAK_ISSUER_URL = os.environ.get("KEYCLOAK_ISSUER_URL", KEYCLOAK_URL)
PUBLIC_URL = os.environ.get("PABEL_PUBLIC_URL", "http://localhost:8000")

mcp = FastMCP(
    "pabel",
    token_verifier=KeycloakTokenVerifier(),
    auth=AuthSettings(
        issuer_url=f"{KEYCLOAK_ISSUER_URL.rstrip('/')}/realms/{REALM}",
        resource_server_url=PUBLIC_URL,
    ),
    # Must be passed explicitly: FastMCP forwards its own 127.0.0.1 default
    # into Settings(), shadowing FASTMCP_HOST entirely. A container needs
    # 0.0.0.0 or port mapping can't reach it.
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

    `content` is the .abe file's raw text, base64-encoded - the agent already
    has the file, this server keeps no document store, and base64 stops the
    JSON-shaped .abe text being misread as a structured argument in transit.
    `agent_token` is this installation's own client_credentials access token.
    `name` is only a label for the response and audit log, never a path.

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
    """Records a call the connector's own local enforcement denied entirely
    client-side, one that never reaches read_document/whoami at all. Without
    this, such an attempt would leave no record anywhere.

    Deliberately more lenient than the other tools about the human
    principal: an attempt to read the credential store or tamper with a hook
    config can happen before anyone has logged in, so a missing session is
    recorded as username=None rather than rejecting the call - this tool must
    never fail to log an attempt because identity resolution failed.
    `agent_token` is still required and verified: reporting as an
    installation that doesn't check out deserves an audit entry, not a
    silent no-op."""
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
