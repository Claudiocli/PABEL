"""opencode's `tool.execute.before` hook.

STATUS: BUILT-TO-SPEC, UNVERIFIED, with one load-bearing assumption that is
itself unconfirmed upstream - see below and docs/known-gaps.md.

opencode loads JS/TS plugins into its own runtime rather than invoking an
external command from a config file, so this adapter is reached through
`plugins/opencode/pabel.js` (written by installers/opencode.py), which spawns
the hook and speaks this package's own protocol:

  stdin : {"tool": "<name>", "args": {...}}
  stdout: {"permission": "allow"}                        - proceed untouched
          {"permission": "allow", "updated_args": {...}} - proceed, rewritten
          {"permission": "deny", "message": "..."}       - bridge throws this

**The unconfirmed assumption**: opencode blocks only by *throwing* and has no
deny-with-content field, so the relayed result is folded into the thrown
message. Whether a thrown message reaches the *model* is an open upstream
question (acropolis_mcp#126). If it doesn't, blocking still works - the direct
.abe read never happens - but the relay degrades to an unexplained denial.

Because `tool.execute.before` may mutate `output.args`, this is the only
adapter besides claude_code that can act on `Decision.updated_input`.
"""

import json

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse
from .base import fold_content_into_reason

name = "opencode"

# Only the three classifications decide() branches on are modelled; everything
# else (grep/glob/list/webfetch/task) falls through to mentions_target().
MUTATING_TOOLS = {"write", "edit", "patch"}
EXECUTE_TOOLS = {"bash"}

PABEL_TOOL_NAMES = {"whoami", "read_document", "materialize_document", "login"}
KNOWN_MCP_SERVERS = {"pabel", "pabel-connector"}
MCP_NAME_SEPARATORS = ("_", ".", "*")


def _mcp_target(tool_name):
    """(server, tool) if this targets one of PABEL's own MCP servers.

    How opencode namespaces MCP tool names isn't documented, so several
    separators are accepted, but only when the prefix is a known PABEL server.
    The bare-name fallback is a real fragility if another MCP server ever
    exposes a `whoami`/`read_document` - same accepted limitation as cursor.py.
    """
    for separator in MCP_NAME_SEPARATORS:
        server, found, tool = tool_name.partition(separator)
        if found and server in KNOWN_MCP_SERVERS and tool:
            return (server, tool)
    if tool_name in PABEL_TOOL_NAMES:
        return ("pabel", tool_name)
    return None


def parse(argv, stdin_bytes) -> NormalizedCall:
    payload = json.loads(stdin_bytes.decode("utf-8") or "{}")
    tool_name = payload.get("tool") or ""
    args = payload.get("args") or {}
    is_write = tool_name in MUTATING_TOOLS
    return NormalizedCall(
        tool_name=tool_name,
        tool_input=args,
        is_write=is_write,
        is_execute=tool_name in EXECUTE_TOOLS,
        mcp_target=_mcp_target(tool_name),
        # filePath is opencode's own field name; the others are fallbacks.
        write_target=(args.get("filePath") or args.get("path") or args.get("file_path"))
        if is_write else None,
    )


def render(decision: Decision) -> RenderedResponse:
    if decision.kind == DecisionKind.ALLOW:
        response = {"permission": "allow"}
        if decision.updated_input is not None:
            response["updated_args"] = decision.updated_input
        return RenderedResponse(stdout=json.dumps(response))

    return RenderedResponse(stdout=json.dumps({
        "permission": "deny",
        "message": fold_content_into_reason(decision),
    }))
