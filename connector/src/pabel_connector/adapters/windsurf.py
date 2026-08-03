"""Windsurf/Cascade hooks - docs.windsurf.com/windsurf/cascade/hooks. Four
pre-hooks instead of one generic event: `pre_read_code`, `pre_write_code`,
`pre_run_command`, `pre_mcp_tool_use`. Each gets its own registry entry
("windsurf:pre_read_code" etc.).

Blocking mechanism here is fundamentally different from every other
adapter in this package: Windsurf blocks a pre-hook by **exit code 2**,
with **stderr** as the human-readable reason - not a JSON field on stdout.
render() therefore returns RenderedResponse(exit_code=2, stderr=...)
instead of writing JSON, and ALLOW is exit code 0 with no output.

STATUS: DEGRADED, CONFIRMED (2026-08 doc re-check, prompted by
installers/vscode.py's own guessed path/schema turning out wrong - every
"built to spec" adapter in this package got re-checked against current
official docs rather than trusting the original research unverified).
Two things are now settled, not just guessed:

1. Input payload schema for all four hooks - a common envelope
   (agent_action_name, trajectory_id, execution_id, timestamp, model_name)
   plus a per-hook `tool_info` object:
     pre_read_code:    tool_info.file_path
     pre_write_code:   tool_info.file_path, tool_info.edits ([{old_string,
                       new_string}] - no plain "content" field exists)
     pre_run_command:  tool_info.command_line (NOT "command")
     pre_mcp_tool_use: tool_info.mcp_server_name, tool_info.mcp_tool_name,
                       tool_info.mcp_tool_arguments
   `mcp_server_name` being an actual, explicit field (unlike Cursor, which
   has no equivalent) means `mcp_target` here no longer needs the
   tool-name-heuristic fragility flagged in earlier versions of this file
   - matched by server name directly against
   core/detection.py's PABEL_MCP_SERVER_NAME.

2. The blocking channel is CONFIRMED exit-code-2-plus-stderr only, with NO
   structured JSON response mechanism at all, and stderr is explicitly
   documented as reaching the **human** in the Cascade UI, not the model's
   context. This settles what was previously this package's single
   biggest open question for Windsurf (coverage-matrix.md) - and settles
   it negatively: there is no channel to transparently hand decrypted
   content back to the *model* here, full stop, not something a future
   verification attempt could still find. That's a real vendor ceiling,
   the same category of limitation as codex_cli's Bash-only hook coverage
   - hence DEGRADED rather than a plain UNVERIFIED that a live test could
   still fully clear. The content-in-stderr fold-in below is kept anyway
   since a human watching the Cascade UI can still read it there, even
   though the model driving the session never will.

Still genuinely unverified: whether this actually fires as expected
against a real Cascade session (no install was available to test), and
whether Windows specifically needs the command under this schema's
documented `"powershell"` per-hook field instead of (or as well as)
`"command"` - the confirmed schema shows both as valid keys with no
platform-selection rule found; this package writes only `"command"`
today, unconfirmed whether Cascade on Windows honors that alone.
"""

import json

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse

PABEL_MCP_SERVER_NAME = "pabel"


def _field(payload, *keys):
    for key in keys:
        if key in payload:
            return payload[key]
    tool_info = payload.get("tool_info") or {}
    for key in keys:
        if key in tool_info:
            return tool_info[key]
    return None


def _render(decision: Decision) -> RenderedResponse:
    if decision.kind == DecisionKind.ALLOW:
        return RenderedResponse(exit_code=0)

    reason = decision.reason
    if decision.content is not None:
        reason = f"{reason}\n\nread_document result:\n{json.dumps(decision.content)}"
    return RenderedResponse(exit_code=2, stderr=reason)


class _WindsurfHook:
    def __init__(self, name, parse_fn):
        self.name = name
        self._parse_fn = parse_fn

    def parse(self, argv, stdin_bytes) -> NormalizedCall:
        payload = json.loads(stdin_bytes.decode("utf-8") or "{}")
        return self._parse_fn(payload)

    def render(self, decision: Decision) -> RenderedResponse:
        return _render(decision)


def _parse_read_code(payload):
    file_path = _field(payload, "file_path", "path") or ""
    return NormalizedCall(tool_name="Read", tool_input={"file_path": file_path})


def _parse_write_code(payload):
    file_path = _field(payload, "file_path", "path") or ""
    content = _field(payload, "content") or ""
    return NormalizedCall(tool_name="Write", tool_input={"file_path": file_path, "content": content},
                           is_write=True, write_target=file_path)


def _parse_run_command(payload):
    # command_line is the confirmed field name (docs.windsurf.com); command
    # kept only as a defensive fallback in case an older/different payload
    # shape is ever seen live.
    command = _field(payload, "command_line", "command") or ""
    return NormalizedCall(tool_name="Bash", tool_input={"command": command}, is_execute=True)


def _parse_mcp_tool_use(payload):
    # mcp_server_name/mcp_tool_name/mcp_tool_arguments are the confirmed
    # field names - unlike Cursor, Windsurf's payload names the calling MCP
    # server explicitly, so mcp_target is a direct match, not a heuristic
    # over known tool names.
    server_name = _field(payload, "mcp_server_name") or ""
    tool_name = _field(payload, "mcp_tool_name", "tool_name") or ""
    arguments = _field(payload, "mcp_tool_arguments", "tool_input", "arguments") or {}
    mcp_target = (server_name, tool_name) if server_name == PABEL_MCP_SERVER_NAME else None
    return NormalizedCall(tool_name=tool_name, tool_input=arguments, mcp_target=mcp_target)


pre_read_code = _WindsurfHook("windsurf:pre_read_code", _parse_read_code)
pre_write_code = _WindsurfHook("windsurf:pre_write_code", _parse_write_code)
pre_run_command = _WindsurfHook("windsurf:pre_run_command", _parse_run_command)
pre_mcp_tool_use = _WindsurfHook("windsurf:pre_mcp_tool_use", _parse_mcp_tool_use)
