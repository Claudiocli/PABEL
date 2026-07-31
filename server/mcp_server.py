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


if __name__ == "__main__":
    mcp.run(transport=os.environ.get("PABEL_TRANSPORT", "stdio"))
