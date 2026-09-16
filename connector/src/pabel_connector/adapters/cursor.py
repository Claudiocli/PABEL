"""Cursor's hooks (v1.7+, beta) - three separate hook points instead of one
generic PreToolUse event: `beforeReadFile`, `beforeShellExecution`,
`beforeMCPExecution`. Each gets its own registry entry
("cursor:beforeReadFile" etc.) since their input payloads differ, but they
share one response shape: `{permission, agent_message, user_message}`,
snake_case. `agent_message` is the channel that reaches the model. An
earlier version guessed camelCase - plausible, wrong, and never caught
because no live install had been tried.

STATUS: BUILT-TO-SPEC, UNVERIFIED. The schema is confirmed on paper only;
nothing here has fired in a live session.

Accepted gap: Cursor has no pre-write-block hook, only the post-hoc
`afterFileEdit`. Not modelled, since there's no legitimate `.abe` write
path anyway.

Known limitation: no MCP server-name field was found in the
`beforeMCPExecution` payload, so `mcp_target` is inferred from the tool name
matching a known PABEL tool. A real fragility if another server ever exposes
a same-named tool; tighten once a real payload can be inspected.
"""

import json

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse
from .base import fold_content_into_reason

PABEL_TOOL_NAMES = {"whoami", "read_document"}


def _render(decision: Decision) -> RenderedResponse:
    if decision.kind == DecisionKind.ALLOW:
        return RenderedResponse(stdout=json.dumps({"permission": "allow"}))

    return RenderedResponse(stdout=json.dumps({
        "permission": "deny",
        "agent_message": fold_content_into_reason(decision),
        "user_message": "PABEL: direct .abe access blocked - relayed result provided to the agent.",
    }))


class _CursorHook:
    def __init__(self, name, parse_fn):
        self.name = name
        self._parse_fn = parse_fn

    def parse(self, argv, stdin_bytes) -> NormalizedCall:
        payload = json.loads(stdin_bytes.decode("utf-8") or "{}")
        return self._parse_fn(payload)

    def render(self, decision: Decision) -> RenderedResponse:
        return _render(decision)


def _parse_read_file(payload):
    file_path = payload.get("file_path") or payload.get("path") or ""
    return NormalizedCall(tool_name="Read", tool_input={"file_path": file_path})


def _parse_shell_execution(payload):
    command = payload.get("command") or ""
    return NormalizedCall(tool_name="Bash", tool_input={"command": command}, is_execute=True)


def _parse_mcp_execution(payload):
    tool_name = payload.get("tool_name") or ""
    # tool_input is the confirmed field name (cursor.com/docs/hooks); arguments
    # kept only as a defensive fallback in case an older/different payload shape
    # is ever seen live.
    arguments = payload.get("tool_input") or payload.get("arguments") or {}
    mcp_target = ("pabel", tool_name) if tool_name in PABEL_TOOL_NAMES else None
    return NormalizedCall(tool_name=tool_name, tool_input=arguments, mcp_target=mcp_target)


before_read_file = _CursorHook("cursor:beforeReadFile", _parse_read_file)
before_shell_execution = _CursorHook("cursor:beforeShellExecution", _parse_shell_execution)
before_mcp_execution = _CursorHook("cursor:beforeMCPExecution", _parse_mcp_execution)
