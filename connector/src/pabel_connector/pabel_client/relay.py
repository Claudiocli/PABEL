"""Calls the deployed PABEL server's read_document MCP tool directly, as
its own MCP client - this is what lets a hook adapter do the work the
model itself is not allowed to do (read raw ciphertext, construct a tool
call with it). Uses the `mcp` package's own streamable-http client, the
same library version (1.28.1) the server is built on.

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


async def _call_read_document(server_url, token, content_b64, name, agent_token):
    async with streamablehttp_client(
        server_url, headers={"Authorization": f"Bearer {token}"}
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as mcp_session:
            await mcp_session.initialize()
            result = await mcp_session.call_tool(
                "read_document",
                {"content": content_b64, "name": name, "agent_token": agent_token})
    if result.isError:
        text = "; ".join(
            block.text for block in result.content if hasattr(block, "text"))
        raise RelayError(text or "read_document call failed")
    if result.structuredContent is not None:
        return result.structuredContent
    for block in result.content:
        if hasattr(block, "text"):
            return json.loads(block.text)
    raise RelayError("read_document returned no usable content")


def read_document(path, name, agent_id):
    """Read `path` off disk, base64-encode it, and relay it to the
    deployed PABEL server's read_document tool, authenticated as both the
    logged-in human and this installation of `agent_id`. Raises
    RelayError/AuthError on failure - callers turn that into a denial
    message."""
    server_url = os.environ.get("PABEL_SERVER_URL")
    if not server_url:
        raise RelayError("PABEL_SERVER_URL is not set - see the connector's README.md")
    try:
        content_b64 = base64.b64encode(open(path, "rb").read()).decode("ascii")
    except OSError as e:
        raise RelayError(f"could not read {path!r}: {e}") from e
    token = session.access_token()
    agent_token = agent_session.access_token(agent_id)
    try:
        return anyio.run(
            _call_read_document, server_url, token, content_b64, name, agent_token)
    except AuthError:
        raise
    except Exception as e:
        raise RelayError(f"relay call to {server_url} failed: {e}") from e
