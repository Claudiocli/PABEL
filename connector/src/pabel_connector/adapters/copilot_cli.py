"""GitHub Copilot CLI's `preToolUse` hook - docs.github.com/en/copilot/reference/hooks-reference.

STATUS: BUILT-TO-SPEC, UNVERIFIED - no Copilot CLI install was available
this session (see connector/docs/coverage-matrix.md). Uses the same
tool_name/tool_input stdin shape and permissionDecision/
permissionDecisionReason blocking mechanism as Claude Code, but with one
deliberate difference: multiple open vendor issues (github/copilot-cli
#2585, #2980) confirm `additionalContext` is not reliably delivered into
the model's context for `preToolUse` today - only the denial itself (with
its `permissionDecisionReason` text) is reliable. So render() folds the
relay's decrypted content directly into `permissionDecisionReason` (the
one guaranteed channel back to the model, since a denied tool call's
reason is surfaced to it as the tool-error text) and *also* sets
additionalContext as a harmless best-effort duplicate, so this adapter
degrades gracefully instead of silently losing the content the day the
vendor bug is fixed upstream.

Open question for the first live test: whether the runtime expects the
literal lowerCamelCase event name "preToolUse" here (native Copilot CLI,
not going through VS Code's PascalCase conversion layer) - kept as
lowerCamelCase per the CLI's own docs, distinct from vscode.py's
"PreToolUse".
"""

import json
import re

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse

name = "copilot-cli"

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

    reason = decision.reason
    if decision.content is not None:
        # The guaranteed channel: fold the relay result into the reason
        # text itself, since additionalContext delivery is unreliable here.
        reason = f"{reason}\n\nread_document result:\n{json.dumps(decision.content)}"

    output = {
        "hookSpecificOutput": {
            "hookEventName": "preToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    if decision.content is not None:
        output["hookSpecificOutput"]["additionalContext"] = json.dumps(decision.content)
    return RenderedResponse(stdout=json.dumps(output))
