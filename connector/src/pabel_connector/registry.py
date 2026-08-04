"""Single source of truth mapping a registry key to its Adapter - the
Strategy pattern's dispatch table. hook.py and cli/main.py both go through
this, so adding a new agent means adding its adapter module plus one (or,
for a multi-hook-point agent, several) entries here - nothing else.

Most agents have one hook point and so one key equal to their agent name
(e.g. "claude-code"). Agents with several distinct hook points (Cursor,
Windsurf) register one key per hook point, formatted "<agent>:<hookpoint>",
each pointing at its own Adapter instance from that agent's module - they
share detection/relay logic through core/, but need distinct parse/render
pairs since each hook point's wire format differs.

STATUS column mirrors each adapter module's own docstring - see
docs/coverage-matrix.md for the full picture and sources. Only
"claude-code" has been confirmed against a real, live install; every
other entry is built from vendor documentation only.
"""

from .adapters import claude_code, copilot_cli, cursor, gemini_cli, vscode, windsurf

# codex-cli has no entry here (like cline/continue-dev in installers/registry.py) -
# its hooks feature is documented as not available on Windows at all, the same
# blocking criterion already applied to Cline; see installers/codex_cli.py and
# docs/known-gaps.md.
ADAPTERS = {
    "claude-code": claude_code,                                    # VERIFIED
    "vscode": vscode,                                              # UNVERIFIED
    "copilot-cli": copilot_cli,                                    # UNVERIFIED
    "cursor:beforeReadFile": cursor.before_read_file,               # UNVERIFIED
    "cursor:beforeShellExecution": cursor.before_shell_execution,   # UNVERIFIED
    "cursor:beforeMCPExecution": cursor.before_mcp_execution,       # UNVERIFIED
    "windsurf:pre_read_code": windsurf.pre_read_code,               # DEGRADED
    "windsurf:pre_write_code": windsurf.pre_write_code,             # DEGRADED
    "windsurf:pre_run_command": windsurf.pre_run_command,          # DEGRADED
    "windsurf:pre_mcp_tool_use": windsurf.pre_mcp_tool_use,         # DEGRADED
    "gemini-cli": gemini_cli,                                      # UNVERIFIED
}

# SessionEnd is not a tool call - no NormalizedCall/Decision shape fits it, so
# it gets its own small dispatch table instead of an ADAPTERS entry. Claude
# Code only, for now: the only agent this package has confirmed a SessionEnd-
# equivalent event for (see installers/claude_code.py, pabel_client/
# materialize.py).
SESSION_END_HANDLERS = {
    "claude-code:session-end": claude_code.handle_session_end,
}
