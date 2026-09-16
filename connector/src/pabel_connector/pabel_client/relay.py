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
    """Attach the human's bearer token at the connection level and call
    `tool_name` on the deployed server. Raises RelayError/AuthError, which
    callers turn into a denial message.

    A coroutine rather than a function that calls anyio.run() internally:
    that would start a nested event loop whenever the caller already has one,
    which is exactly mcp_local_server.py's situation under FastMCP. The hook
    path, a plain subprocess with no loop, uses the sync wrapper instead."""
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
    """Read `path`, base64-encode it, and relay it to the deployed server,
    authenticated as both the human and this installation. No sync wrapper:
    every current caller is already async."""
    try:
        content_b64 = base64.b64encode(open(path, "rb").read()).decode("ascii")
    except OSError as e:
        raise RelayError(f"could not read {path!r}: {e}") from e
    agent_token = agent_session.access_token(agent_id)
    return await _relay_call_async(
        "read_document", {"content": content_b64, "name": name, "agent_token": agent_token})


async def whoami_async(agent_id):
    """Identity/ABE attributes of the human and this installation, and
    whether this user is authorized for it - the sanctioned way to check
    login status without reading any local file."""
    agent_token = agent_session.access_token(agent_id)
    return await _relay_call_async("whoami", {"agent_token": agent_token})


async def read_document_with_login_async(path, name, agent_id):
    """The guided flow this project is built around - try to read, run the
    interactive browser+MFA login if there's no valid session, then retry -
    as one deterministic operation, so no caller has to guess the order.
    Both the hook path and the direct tool-call path go through here, so
    their behaviour can't drift apart.

    Only the first AuthError triggers login-and-retry, exactly once; a
    second (say, logged in but lacking the required role) is re-raised with
    a message distinguishing it from "login itself failed". session.login()
    stays a plain blocking call - it's a real browser wait, and stdio MCP
    dispatches one tool call at a time anyway."""
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


REPORT_DENIAL_TIMEOUT_SECONDS = 3
"""Much shorter than HOOK_TIMEOUT_SECONDS, which budgets for a human
completing a browser login. This is background bookkeeping for a call about
to be denied anyway: unbounded, every local denial would pay a full network
round-trip - or a hung connection - before the agent even sees the response.
Confirmed necessary live, when the test suite went from ~2s to ~27s the first
time this was wired in."""


async def report_denial_async(agent_id, decision_kind, reason, tool_name):
    """Best-effort audit trail for a call denied entirely client-side, which
    never reaches read_document/whoami and so would otherwise leave no record
    anywhere. The relay denials don't need this - they're already audited
    server-side by the real read_document attempt.

    Swallows every failure: a missing credential, no PABEL_SERVER_URL, no
    login, an unreachable server. decide() has already produced its Decision
    by the time this runs, so nothing here may change or delay what the agent
    gets back."""
    try:
        with anyio.fail_after(REPORT_DENIAL_TIMEOUT_SECONDS):
            agent_token = agent_session.access_token(agent_id)
            await _relay_call_async("report_denial", {
                "agent_token": agent_token, "decision_kind": decision_kind,
                "reason": reason, "tool_name": tool_name or ""})
    except Exception:
        pass


def report_denial(agent_id, decision_kind, reason, tool_name=""):
    """Sync wrapper. The outer try/except guards anyio.run() itself, e.g. if
    called from inside an existing loop; the coroutine already swallows
    everything it can reach."""
    try:
        anyio.run(report_denial_async, agent_id, decision_kind, reason, tool_name)
    except Exception:
        pass
