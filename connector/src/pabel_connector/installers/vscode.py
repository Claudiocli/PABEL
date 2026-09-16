"""VS Code's native agent hooks (Preview).

STATUS: path/schema CONFIRMED (2026-08, checked against
code.visualstudio.com/docs/agent-customization/hooks after a real
first-attempt install was found to not fire at all - see
docs/phase2-engineering-notes.md and connector/docs/coverage-matrix.md);
still UNVERIFIED end-to-end against a real Copilot session.

Two real bugs shaped this file, both worth not repeating:

1. The first version guessed `.vscode/hooks.json` with Claude Code's nested
   `[{"matcher": ..., "hooks": [...]}]` shape - both wrong. The confirmed
   location is `.github/hooks/*.json` and the confirmed `PreToolUse` shape
   is a FLAT array of command objects. VS Code never read the file at all,
   for anyone who installed before the fix. It also parses but does not
   enforce `matcher`, so catch-all is the only mode that exists here.

2. VS Code executes `command` through `powershell -Command` on Windows,
   where `base.hook_command()`'s quoted path plus arguments is a parser
   error without the call operator. Fixed by also writing a `windows` field
   via `base.hook_command_windows()`.

Also registers mcp_local_server.py in `.vscode/mcp.json`, so the tools are
directly callable rather than only relayed reactively.

**No `--global` support**: no user-level file exists for VS Code's native
agent hooks, only the workspace-scoped one. Its "Agent Plugins" toggle is
UI state over internal storage, not a JSON file this package could write.
So there is no GLOBAL_CONFIG_RELATIVE_PATH and `--global` is rejected -
rather than guessing a path, which is the exact mistake above.
"""

from pathlib import Path

from . import base

name = "vscode"
status = "unverified"

CONFIG_RELATIVE_PATH = Path(".github") / "hooks" / "pabel.json"
MCP_CONFIG_RELATIVE_PATH = Path(".vscode") / "mcp.json"
HOOK_KEYS = ["vscode"]


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return base_dir / CONFIG_RELATIVE_PATH


def install(base_dir: Path) -> str:
    path = config_path(base_dir)
    data = base.read_json(path)
    base.install_windows_aware_hook(data, "PreToolUse", "vscode")
    base.write_json(path, data)

    mcp_path = base_dir / MCP_CONFIG_RELATIVE_PATH
    mcp_data = base.read_json(mcp_path)
    servers = mcp_data.setdefault("servers", {})
    command, *args = base.mcp_server_command(name)
    servers["pabel-connector"] = {"type": "stdio", "command": command, "args": args}
    base.write_json(mcp_path, mcp_data)

    return (
        f"Wrote a catch-all PreToolUse hook to {path}\n"
        f"(path/schema confirmed against code.visualstudio.com/docs/agent-customization/hooks; "
        f"whether this actually fires as expected in a live Copilot session is still unverified).\n"
        f"Registered mcp_local_server.py's whoami/read_document/login tools in {mcp_path}."
    )
