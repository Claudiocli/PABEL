"""VS Code's native agent hooks (Preview) - code.visualstudio.com/docs/agents/reference/hooks-reference.

STATUS: BUILT-TO-SPEC, UNVERIFIED (no paid Copilot subscription was
available to test against the real thing - see
connector/docs/coverage-matrix.md). `PreToolUse`/`hookSpecificOutput` JSON
shape is genuinely shared with Claude Code (confirmed), but this module's
tool *names* were originally guessed as identical to Claude Code's too
(`Read`/`Write`/`Edit`/`Bash`) - a 2026-08 doc/bug-report re-check (after
installers/vscode.py's config *path* turned out to be simply wrong,
prompting a full re-verification of every VS Code-specific assumption in
this package) found real, different tool names: `editFiles`/`createFile`/
`deleteFile` for writes (not `Write`/`Edit`), and `runTerminalCommand` -
also reported in the wild as `run_in_terminal`, a documented
inconsistency, not a typo here - for shell execution (not `Bash`). Fixed
below. Still unconfirmed: the exact tool name for a plain file *read* (no
official source found naming one - possibly folded into a generic tool,
possibly undocumented) and whether `editFiles`' input shape
(`{"files": [...]}`, an array) is exactly what a real install sends.
Because of that remaining uncertainty, this stays UNVERIFIED even though
the specific bugs found this round are fixed.

Open question carried over unchanged: whether VS Code's MCP tool naming
for `mcp_target` matches Claude Code's `mcp__<server>__<tool>` exactly -
still unconfirmed from docs.
"""

import json
import re

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse

name = "vscode"

MUTATING_TOOLS = {"editFiles", "createFile", "deleteFile"}
EXECUTE_TOOLS = {"runTerminalCommand", "run_in_terminal"}
MCP_TOOL_NAME = re.compile(r"^mcp__(.+?)__(.+)$")


def _mcp_target(tool_name):
    m = MCP_TOOL_NAME.match(tool_name)
    return (m.group(1), m.group(2)) if m else None


def _write_target(tool_input):
    # editFiles' confirmed input shape is {"files": [...]} - a list, not a
    # single file_path string like Claude Code's Write/Edit. Best-effort:
    # the first entry, if any; createFile/deleteFile's exact shape is
    # unconfirmed, so file_path is also checked as a fallback.
    files = tool_input.get("files")
    if isinstance(files, list) and files:
        return files[0]
    return tool_input.get("file_path")


def parse(argv, stdin_bytes) -> NormalizedCall:
    payload = json.loads(stdin_bytes.decode("utf-8") or "{}")
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    is_write = tool_name in MUTATING_TOOLS
    return NormalizedCall(
        tool_name=tool_name,
        tool_input=tool_input,
        is_write=is_write,
        is_execute=tool_name in EXECUTE_TOOLS,
        mcp_target=_mcp_target(tool_name),
        write_target=_write_target(tool_input) if is_write else None,
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
