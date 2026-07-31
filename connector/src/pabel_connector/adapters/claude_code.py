"""Claude Code's PreToolUse hook adapter.

STATUS: VERIFIED. This is a behavior-identical port of the hook already
shipped and manually tested end-to-end in
claude-plugin/pabel/hooks/pabel_relay_hook.py (see
docs/phase2-engineering-notes.md sec 9 for the verification history: real
reads/writes/greps/globs/bash-cat against a real deployed server,
including the two bugs found and fixed - spaced paths and the
QUOTED_SEGMENT group-None case - both of which live in core/detection.py
now, inherited by every adapter). Every other adapter in this package is
built from vendor documentation only and is explicitly UNVERIFIED until
tried against a real install - this one is the sole exception.

Wire format: stdin is one JSON object `{"tool_name": ..., "tool_input":
{...}}`; response is JSON on stdout with exit 0 - the model is expected to
proceed normally on empty stdout, or be denied per
`hookSpecificOutput.permissionDecision`. MCP tool calls arrive with
`tool_name` of the form `mcp__<server>__<tool>`.
"""

import json
import re

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse

name = "claude-code"

MUTATING_TOOLS = {"Write", "Edit", "NotebookEdit"}
MCP_TOOL_NAME = re.compile(r"^mcp__(.+?)__(.+)$")


def _mcp_target(tool_name):
    m = MCP_TOOL_NAME.match(tool_name)
    return (m.group(1), m.group(2)) if m else None


def parse(argv, stdin_bytes) -> NormalizedCall:
    payload = json.loads(stdin_bytes.decode("utf-8") or "{}")
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    is_write = tool_name in MUTATING_TOOLS
    return NormalizedCall(
        tool_name=tool_name,
        tool_input=tool_input,
        is_write=is_write,
        is_execute=tool_name == "Bash",
        mcp_target=_mcp_target(tool_name),
        write_target=(tool_input.get("file_path") or tool_input.get("notebook_path"))
        if is_write else None,
    )


def render(decision: Decision) -> RenderedResponse:
    if decision.kind == DecisionKind.ALLOW:
        return RenderedResponse()  # no output at all - the tool call proceeds untouched

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.reason,
        }
    }
    if decision.content is not None:
        output["hookSpecificOutput"]["additionalContext"] = json.dumps(decision.content)
    return RenderedResponse(stdout=json.dumps(output))
