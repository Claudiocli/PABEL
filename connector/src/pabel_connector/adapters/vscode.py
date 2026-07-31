"""VS Code's native agent hooks (Preview) - code.visualstudio.com/docs/agents/reference/hooks-reference.

STATUS: BUILT-TO-SPEC, UNVERIFIED (no paid Copilot subscription was
available this session to test against the real thing - see
connector/docs/coverage-matrix.md). Documented as sharing the exact same
`PreToolUse`/`hookSpecificOutput` JSON schema as Claude Code, including
`additionalContext` actually reaching the model - VS Code is even
documented to auto-convert GitHub Copilot CLI's lowerCamelCase hook
config into this same PascalCase shape. Because of that, this adapter is
intentionally implemented as a thin subclass-in-spirit of claude_code.py
rather than a divergent reimplementation - if a live test finds a
difference, fix it here without assuming the two must stay identical.

Open question for the first live test: whether VS Code's own MCP tool
naming convention for `mcp_target` matches Claude Code's `mcp__<server>__
<tool>` exactly - unconfirmed from docs, so the own-tool allowlist (see
core/decide.py) may not fire correctly here until checked.
"""

import json
import re

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse

name = "vscode"

MUTATING_TOOLS = {"Write", "Edit", "NotebookEdit"}
MCP_TOOL_NAME = re.compile(r"^mcp__(.+?)__(.+)$")


def _mcp_target(tool_name):
    m = MCP_TOOL_NAME.match(tool_name)
    return (m.group(1), m.group(2)) if m else None


def parse(argv, stdin_bytes) -> NormalizedCall:
    payload = json.loads(stdin_bytes.decode("utf-8") or "{}")
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    return NormalizedCall(
        tool_name=tool_name,
        tool_input=tool_input,
        is_write=tool_name in MUTATING_TOOLS,
        is_execute=tool_name == "Bash",
        mcp_target=_mcp_target(tool_name),
    )


def render(decision: Decision) -> RenderedResponse:
    if decision.kind == DecisionKind.ALLOW:
        return RenderedResponse()

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
