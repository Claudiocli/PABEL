"""Calls the deployed PABEL server's read_document/whoami MCP tools
directly, as its own MCP client - this is what lets a hook adapter (or
mcp_local_server.py's directly-callable tools) do the work the model
itself is not allowed to do (read raw ciphertext, construct a tool call
with it). Uses the `mcp` package's own streamable-http client, the same
library version (1.28.1) the server is built on.

Every call carries two credentials: the human's (session.py, connection-
level bearer auth - unchanged) and this installation's own
(agent_session.py, a tool argument - see server/core.py's resolve_agent()
for why a second, per-installation credential is required at all, not just
the human's).
"""

import base64
import json
import os

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .keycloak_client import AuthError
from . import agent_session, session


class RelayError(Exception):
    pass


async def _call_tool(server_url, token, tool_name, arguments):
    async with streamablehttp_client(
        server_url, headers={"Authorization": f"Bearer {token}"}
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as mcp_session:
            await mcp_session.initialize()
            result = await mcp_session.call_tool(tool_name, arguments)
    if result.isError:
        text = "; ".join(
            block.text for block in result.content if hasattr(block, "text"))
        raise RelayError(text or f"{tool_name} call failed")
    if result.structuredContent is not None:
        return result.structuredContent
    for block in result.content:
        if hasattr(block, "text"):
            return json.loads(block.text)
    raise RelayError(f"{tool_name} returned no usable content")


async def _relay_call_async(tool_name, arguments):
    """Shared plumbing behind read_document/whoami below: attach the
    logged-in human's bearer token at the connection level (unchanged
    across every tool) and call `tool_name` on the deployed PABEL server.
    Raises RelayError/AuthError - callers turn that into a denial message.

    An async coroutine, not a plain function that internally does
    anyio.run() - that would start a *second*, nested event loop whenever
    the caller is already inside one, which is exactly what
    mcp_local_server.py's tool handlers are (FastMCP's own event loop) -
    confirmed live via a real "Already running asyncio in this thread"
    crash. core/decide.py's hook path (a fresh, plain subprocess with no
    event loop of its own) uses read_document_with_login()'s sync wrapper
    instead, the only place anyio.run() is still called."""
    server_url = os.environ.get("PABEL_SERVER_URL")
    if not server_url:
        raise RelayError("PABEL_SERVER_URL is not set - see the connector's README.md")
    token = session.access_token()
    try:
        return await _call_tool(server_url, token, tool_name, arguments)
    except AuthError:
        raise
    except Exception as e:
        raise RelayError(f"relay call to {server_url} failed: {e}") from e


async def read_document_async(path, name, agent_id):
    """Read `path` off disk, base64-encode it, and relay it to the
    deployed PABEL server's read_document tool, authenticated as both the
    logged-in human and this installation of `agent_id`. No sync wrapper -
    every current caller (read_document_with_login_async below,
    mcp_local_server.py's tools) is already async; add one back only if a
    real sync caller ever needs it (see read_document_with_login()'s own
    wrapper for that pattern)."""
    try:
        content_b64 = base64.b64encode(open(path, "rb").read()).decode("ascii")
    except OSError as e:
        raise RelayError(f"could not read {path!r}: {e}") from e
    agent_token = agent_session.access_token(agent_id)
    return await _relay_call_async(
        "read_document", {"content": content_b64, "name": name, "agent_token": agent_token})


async def whoami_async(agent_id):
    """Relay to the deployed PABEL server's whoami tool: identity/ABE
    attributes of the logged-in human and this installation of `agent_id`,
    and whether this user is authorized to use it - the sanctioned way to
    check login/authorization status without reading any local file. No
    sync wrapper, same reasoning as read_document_async above."""
    agent_token = agent_session.access_token(agent_id)
    return await _relay_call_async("whoami", {"agent_token": agent_token})


async def read_document_with_login_async(path, name, agent_id):
    """The one guided flow this whole project is built around - "try to
    read, log in with the interactive browser+MFA flow if there's no valid
    human session yet, then the already-authenticated retry gets
    decrypted" - as a single deterministic operation, not something a
    caller (the hook, or a model calling read_document as an MCP tool
    directly) has to sequence itself by guessing what order to call things
    in. Both core/decide.py's hook path and mcp_local_server.py's direct
    tool-call path call this same function so their behavior can never
    drift apart.

    Only a first AuthError triggers login-and-retry, exactly once - a
    second AuthError (e.g. logged in but still lacking the required role)
    is not retried again, just re-raised with a message distinguishing it
    from "login itself failed". session.login() itself stays a plain
    blocking call even here (it's a real system-browser wait, not
    something to make concurrent) - fine for stdio MCP, which dispatches
    one tool call at a time anyway, so nothing else needs this thread
    during the wait."""
    try:
        return await read_document_async(path, name, agent_id)
    except AuthError:
        pass
    try:
        session.login()
    except AuthError as e:
        raise AuthError(f"automatic browser login could not complete: {e}") from e
    try:
        return await read_document_async(path, name, agent_id)
    except AuthError as e:
        raise AuthError(f"logged in, but still not authenticated: {e}") from e


def read_document_with_login(path, name, agent_id):
    """Sync wrapper - see read_document()'s docstring."""
    return anyio.run(read_document_with_login_async, path, name, agent_id)
