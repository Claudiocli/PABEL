"""Google Gemini CLI's `BeforeTool` hook - geminicli.com/docs/hooks/reference/.
One generic event (`matcher` is a regex over the tool name, so a catch-all
`"*"` covers every tool, same spirit as Claude Code's no-matcher catch-all).

STATUS: BUILT-TO-SPEC, UNVERIFIED - no Gemini CLI install was available
this session (see connector/docs/coverage-matrix.md). Blocks via
`{"decision": "deny", "reason": ...}` on stdout with exit 0 - `reason` is
"sent to the agent as a tool error", which is the one call-scoped channel
back to the model. Gemini CLI does have a richer `additionalContext`
mechanism, but it belongs to a *different* hook (`BeforeAgent`, turn-scoped,
not wired to a specific blocked tool call) - so, like copilot_cli.py, the
relay's decrypted content is folded into `reason` itself rather than relying
on a channel that isn't actually attached to this event.

MCP tools are named `mcp_<server>_<tool>` (single underscore, per docs) -
recovered here assuming the server name itself has no underscore (true for
"pabel"); this would misparse if a server name containing an underscore
were ever used with this convention.
"""

import json
import re

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse
from .base import fold_content_into_reason

name = "gemini-cli"

MUTATING_TOOLS = {"write_file", "replace", "Write", "Edit"}
BASH_TOOL_NAMES = {"run_shell_command", "Bash"}
MCP_TOOL_NAME = re.compile(r"^mcp_([^_]+)_(.+)$")


def _mcp_target(tool_name):
    m = MCP_TOOL_NAME.match(tool_name)
    return (m.group(1), m.group(2)) if m else None


def parse(argv, stdin_bytes) -> NormalizedCall:
    payload = json.loads(stdin_bytes.decode("utf-8") or "{}")
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or payload.get("arguments") or {}
    is_write = tool_name in MUTATING_TOOLS
    return NormalizedCall(
        tool_name=tool_name,
        tool_input=tool_input,
        is_write=is_write,
        is_execute=tool_name in BASH_TOOL_NAMES,
        mcp_target=_mcp_target(tool_name),
        write_target=(tool_input.get("file_path") or tool_input.get("path"))
        if is_write else None,
    )


def render(decision: Decision) -> RenderedResponse:
    if decision.kind == DecisionKind.ALLOW:
        return RenderedResponse()

    reason = fold_content_into_reason(decision)
    return RenderedResponse(stdout=json.dumps({"decision": "deny", "reason": reason}))
