"""A local, stdio MCP server bundled with pabel_connector itself, so any
agent supporting local MCP registration gets whoami/read_document/login as
directly callable tools the moment `pabel-connector install <agent>` runs
- no PABEL server repo checkout needed, only this already-installed
package. It forwards to the already-deployed shared server over the
network via pabel_client/relay.py - the exact same relay code core/
decide.py's hook path uses, so a model calling read_document directly here
behaves identically to a blocked file read getting relayed automatically.

Registered under the server name "pabel-connector" (see
core/detection.py's PABEL_CONNECTOR_MCP_SERVER_NAME) - distinct from the
deployed server's own "pabel" name - so decide() can always allow calls to
it unconditionally, with no agent_token to inject: this process already
knows its own agent_id, baked in at registration time (see
installers/base.py's mcp_server_command()), never a value the model
supplies or sees.

These tools are a debugging/explicit-recovery aid, not the primary path:
the golden path is still "just try to read the file" and let the hook
relay it automatically. Their docstrings say so explicitly, since an MCP
tool's docstring is the only "instructions" a model actually reads before
deciding whether and how to call it - the model should never have to guess
the intended file -> login-if-needed -> MCP-decrypts order.

Usage: python -m pabel_connector.mcp_local_server <agent_id>

Every tool here is `async def`, calling relay.py's `_async` functions
directly via `await` - never the plain `read_document()`/`whoami()`/
`read_document_with_login()` sync wrappers, which call `anyio.run()`
internally. This server already runs inside its own event loop (FastMCP);
a live 2026-08 test crashed with "Already running asyncio in this thread"
the one time a tool handler here called a sync wrapper instead, which
tried to start a second, nested event loop. See pabel_client/relay.py's
docstrings for the full explanation.
"""

import sys

from mcp.server.fastmcp import FastMCP

from .pabel_client import materialize, relay, session
from .pabel_client.keycloak_client import AuthError

AGENT_ID = None  # set by main() - see the module docstring's usage line
mcp = FastMCP("pabel-connector")


@mcp.tool()
async def login() -> dict:
    """Run the interactive browser+MFA login for the current human user
    and persist the resulting session locally. Blocks until the browser
    flow completes or times out (about 3 minutes) - but only if not
    already logged in with a still-valid session; calling this when
    already logged in is a harmless no-op, never a second browser popup.

    You do not need to call this before read_document - it already runs
    this automatically if needed. Call it directly only to log in
    proactively, or to retry after whoami/read_document reported not
    being authenticated."""
    try:
        session.access_token()
        return {"ok": True, "already_logged_in": True}
    except AuthError:
        pass
    try:
        session.login()
    except AuthError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


@mcp.tool()
async def whoami() -> dict:
    """Identity and ABE attributes of the currently logged-in human and
    this installation's own agent identity, and whether this user is
    authorized to use it at all. Call this to check login/authorization
    status, or to debug why a document section came back access-denied -
    never by reading this package's own local credential files directly,
    which is denied unconditionally."""
    try:
        return await relay.whoami_async(AGENT_ID)
    except (AuthError, relay.RelayError) as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def read_document(path: str, name: str = "document") -> dict:
    """Read an ABE-encrypted document at `path`, as the combination of the
    logged-in human's and this agent installation's attributes allows. If
    there's no valid login session yet, this runs the interactive
    browser+MFA login automatically and retries once - "file -> login if
    needed -> MCP decrypts" as a single guided operation, nothing to
    sequence yourself.

    Prefer just reading the file directly first - a hook normally
    intercepts and relays a blocked read the same way, automatically. Call
    this tool explicitly only when no hook fired (e.g. the file is outside
    the current workspace) or you're deliberately re-checking access."""
    try:
        return await relay.read_document_with_login_async(path, name, AGENT_ID)
    except (AuthError, relay.RelayError) as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def materialize_document(path: str, name: str = "document") -> dict:
    """Read an ABE-encrypted document at `path` and write its decrypted
    content to a local file under this installation's own PABEL cache
    directory - use this when explicitly asked to read AND copy a document,
    not for a normal read (just reading the file directly, or calling
    read_document, is enough for that - a hook normally handles it
    automatically).

    Once written, PABEL no longer governs this file: it is deleted
    automatically when the session ends, but it is NOT re-verified or kept in
    sync with the source while the session is open. Treat it as any other
    local file for that window - if you need a copy that reflects a
    since-changed source or revoked access, call this again rather than
    trusting an old one."""
    try:
        return await materialize.create_async(path, name, AGENT_ID)
    except (AuthError, relay.RelayError) as e:
        return {"ok": False, "error": str(e)}


def main(argv=None):
    global AGENT_ID
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.stderr.write("usage: python -m pabel_connector.mcp_local_server <agent_id>\n")
        return 2
    AGENT_ID = argv[0]
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
