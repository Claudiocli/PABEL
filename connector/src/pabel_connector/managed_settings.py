"""Generates the exact `managed-settings.json`/`managed-mcp.json` content
`connector/docs/managed-settings.md` tells an admin to deploy - built from
this package's own `installers.base`/`installers.claude_code` source of
truth (the same `hook_command()`/`mcp_server_command()` calls `install()`
itself uses), not hand-typed JSON that could silently drift from what the
hook/MCP registration actually expect after a future change here.

This module only ever *generates* file content, in the current working
directory or wherever `--out-dir` points - it never writes to a registry
key or to `C:\\Program Files\\ClaudeCode\\`. Those are machine-wide,
admin-only destinations reachable only with elevated privileges, on
whichever machine is actually being managed (not necessarily this one) -
deliberately left as a manual step the admin runs themselves (see
`deploy/Deploy-ManagedSettings.ps1`), the same "admin action, run locally,
never automated" split `server/agents_admin.py` already draws for
installation credentials.

Claude Code only, same scope as `managed-settings.md` - this is the only
agent with a confirmed managed-settings mechanism at all.
"""

import sys

from .installers import base, claude_code

DEFAULT_SERVER_URL = "${PABEL_SERVER_URL}"
"""Matches `.mcp.json`'s own existing convention (see claude_code.install) -
expands from each managed machine's own environment, so no PABEL secret or
per-deployment value needs to live in a file every user on the machine can
read."""


def _resolve_python_path(python_path: str = None) -> str:
    """`sys.executable` (this interpreter) is correct for a single
    developer's own `pabel-connector install`, but almost certainly wrong
    for a fleet-wide managed-settings deployment - see managed-settings.md's
    "same <path-to-python> caveat" note. Passing `python_path` explicitly is
    the normal case for real deployment; the default is only a convenience
    for previewing the shape of the output, not a value to actually ship."""
    return python_path or sys.executable


def generate_managed_settings(python_path: str = None) -> dict:
    """The full `managed-settings.json` content: `allowManagedHooksOnly` +
    `allowManagedPermissionRulesOnly`, plus one nested hook entry per key in
    `installers.claude_code.HOOK_KEYS` (currently PreToolUse + SessionEnd) -
    whatever that list contains, not a value hardcoded here twice."""
    resolved = _resolve_python_path(python_path)
    hooks = {}
    for key in claude_code.HOOK_KEYS:
        event = "SessionEnd" if key.endswith(":session-end") else "PreToolUse"
        command = base.hook_command(key).replace(f'"{sys.executable}"', f'"{resolved}"')
        hooks.setdefault(event, []).append({
            "hooks": [{
                "type": "command",
                "command": command,
                "timeout": base.HOOK_TIMEOUT_SECONDS,
            }]
        })
    return {
        "allowManagedHooksOnly": True,
        "allowManagedPermissionRulesOnly": True,
        "hooks": hooks,
    }


def generate_managed_mcp(python_path: str = None, server_url: str = DEFAULT_SERVER_URL) -> dict:
    """The full `managed-mcp.json` content, exclusive-control mode - pins
    exactly the two servers `claude_code.install()` itself would have
    registered (`pabel`/`pabel-connector`), same reasoning as
    `generate_managed_settings` above. Deliberately does NOT use the softer
    `allowedMcpServers` allowlist shape - see managed-settings.md's own
    section on why (a confirmed, permanently-accepted CLI-flag bypass,
    `anthropics/claude-code#31508`)."""
    resolved = _resolve_python_path(python_path)
    command, *args = base.mcp_server_command(claude_code.name)
    command = resolved if command == sys.executable else command
    return {
        "mcpServers": {
            "pabel": {"type": "http", "url": server_url},
            "pabel-connector": {"type": "stdio", "command": command, "args": args},
        }
    }
