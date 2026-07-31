"""Windsurf/Cascade hooks - docs.windsurf.com/windsurf/cascade/hooks. Four
pre-hooks instead of one generic event: `pre_read_code`, `pre_write_code`,
`pre_run_command`, `pre_mcp_tool_use`. Each gets its own registry entry
("windsurf:pre_read_code" etc.).

Blocking mechanism here is fundamentally different from every other
adapter in this package: Windsurf blocks a pre-hook by **exit code 2**,
with **stderr** as the human-readable reason - not a JSON field on stdout.
render() therefore returns RenderedResponse(exit_code=2, stderr=...)
instead of writing JSON, and ALLOW is exit code 0 with no output.

STATUS: BUILT-TO-SPEC, UNVERIFIED, and the least-confirmed adapter in this
package (see connector/docs/coverage-matrix.md) - no Windsurf install was
available this session, and public docs did not surface the exact input
JSON schema for these four specific hook names, nor whether Windsurf
delivers stderr text back into the model's own context at all (versus
only into a human-visible log). This adapter's decrypted-content channel
(folding it into the stderr reason) is a best guess, not a confirmed
mechanism - flagged explicitly as the first thing to check in a real
install, since the whole "invisible relay" UX depends on it.

Input parsing is deliberately defensive (checks several plausible key
names, including a nested "tool_info" object, based on the shape shown
for a *different*, confirmed Windsurf hook's payload) rather than
committing to one exact schema no source actually confirmed for these
four hooks specifically.
"""

import json

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse

PABEL_TOOL_NAMES = {"whoami", "read_document"}


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
                           is_write=True)


def _parse_run_command(payload):
    command = _field(payload, "command") or ""
    return NormalizedCall(tool_name="Bash", tool_input={"command": command}, is_execute=True)


def _parse_mcp_tool_use(payload):
    tool_name = _field(payload, "tool_name") or ""
    arguments = _field(payload, "arguments", "tool_input") or {}
    mcp_target = ("pabel", tool_name) if tool_name in PABEL_TOOL_NAMES else None
    return NormalizedCall(tool_name=tool_name, tool_input=arguments, mcp_target=mcp_target)


pre_read_code = _WindsurfHook("windsurf:pre_read_code", _parse_read_code)
pre_write_code = _WindsurfHook("windsurf:pre_write_code", _parse_write_code)
pre_run_command = _WindsurfHook("windsurf:pre_run_command", _parse_run_command)
pre_mcp_tool_use = _WindsurfHook("windsurf:pre_mcp_tool_use", _parse_mcp_tool_use)
