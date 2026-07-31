"""Cursor's hooks (v1.7+, beta) - three separate hook points instead of one
generic PreToolUse event: `beforeReadFile`, `beforeShellExecution`,
`beforeMCPExecution`. Each gets its own registry entry
("cursor:beforeReadFile" etc.) since their input payloads differ, but they
share one response shape: `{permission: "allow"|"deny"|"ask", agentMessage,
userMessage}` - `agentMessage` is the channel that reaches the model
(the role `additionalContext` plays for Claude Code/VS Code).

STATUS: BUILT-TO-SPEC, UNVERIFIED - no Cursor install was available this
session (see connector/docs/coverage-matrix.md). Field names below
(`file_path`, `command`, `tool_name`/`arguments`) are best-effort readings
of incomplete public docs and need confirming against a real payload
before relying on this in production.

Known, accepted gap: Cursor has no pre-write-block hook (only the
post-hoc `afterFileEdit`) - not modeled here, since this project has no
legitimate `.abe` write path anyway (see core/decide.py's DENY_MUTATING).

Known limitation: no separate "MCP server name" field was found in
Cursor's `beforeMCPExecution` payload docs (only `tool_name`, `arguments`,
`command` - the server's own launch command, `workspace_roots`) - so
`mcp_target` here is inferred heuristically by checking whether
`tool_name` matches one of this project's own known tool names
(`whoami`/`read_document`), not from an explicit server identifier. This
is a real fragility if another MCP server ever exposes a same-named tool;
tighten this once a real payload can be inspected.
"""

import json

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse

PABEL_TOOL_NAMES = {"whoami", "read_document"}


def _render(decision: Decision) -> RenderedResponse:
    if decision.kind == DecisionKind.ALLOW:
        return RenderedResponse(stdout=json.dumps({"permission": "allow"}))

    agent_message = decision.reason
    if decision.content is not None:
        agent_message = f"{decision.reason}\n\nread_document result:\n{json.dumps(decision.content)}"

    return RenderedResponse(stdout=json.dumps({
        "permission": "deny",
        "agentMessage": agent_message,
        "userMessage": "PABEL: direct .abe access blocked - relayed result provided to the agent.",
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
    arguments = payload.get("arguments") or payload.get("tool_input") or {}
    mcp_target = ("pabel", tool_name) if tool_name in PABEL_TOOL_NAMES else None
    return NormalizedCall(tool_name=tool_name, tool_input=arguments, mcp_target=mcp_target)


before_read_file = _CursorHook("cursor:beforeReadFile", _parse_read_file)
before_shell_execution = _CursorHook("cursor:beforeShellExecution", _parse_shell_execution)
before_mcp_execution = _CursorHook("cursor:beforeMCPExecution", _parse_mcp_execution)
