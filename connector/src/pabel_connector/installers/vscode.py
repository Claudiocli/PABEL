"""VS Code's native agent hooks (Preview).

STATUS: path/schema CONFIRMED (2026-08, checked against
code.visualstudio.com/docs/agent-customization/hooks after a real
first-attempt install was found to not fire at all - see
docs/phase2-engineering-notes.md and connector/docs/coverage-matrix.md);
still UNVERIFIED end-to-end against a real Copilot session.

The first version of this installer wrote `.vscode/hooks.json` using
Claude Code's nested `[{"matcher": ..., "hooks": [...]}]` shape - both
wrong. The confirmed workspace-scope location is `.github/hooks/*.json`,
and the confirmed `PreToolUse` shape is a FLAT array of hook command
objects directly (`{"type": "command", "command": ..., "timeout": ...}`) -
the nested matcher/hooks wrapper is only accepted when VS Code parses an
actual `.claude/settings.json`-shaped file for cross-tool compatibility,
not the native format for a file under `.github/hooks/`. VS Code is also
documented to parse but NOT ENFORCE any `matcher` field at all - every
hook in the array always runs regardless of tool name - so there was
never a reason to write one, catch-all is the only mode that exists here.

This bug meant the hook was never read by VS Code at all, for anyone who
installed it before this fix - not a schema mismatch VS Code silently
tolerated, a file it never looked at in the first place.

**Second real bug, found live 2026-08 with the path fix in place**: VS
Code's `command` field is executed via `powershell -Command` on Windows
(docs confirm a separate `windows` override field exists precisely because
`command`'s execution is OS-specific, and community sources confirm
PowerShell specifically for the Windows case). `base.hook_command()`'s
plain `"<python.exe>" -m pabel_connector.hook vscode` is invalid PowerShell
syntax without the call operator (`&`) - PowerShell parses a leading quoted
string as a standalone expression and rejects anything after it
(`Unexpected token '-m' in expression or statement.`), confirmed against a
real failing hook invocation. Fixed by also writing a `windows` field via
`base.hook_command_windows()`, which prefixes `&`.

Also registers mcp_local_server.py as a direct MCP server in
`.vscode/mcp.json` (confirmed schema: `{"servers": {"<name>": {"type":
"stdio", "command", "args"}}}`, code.visualstudio.com/docs/agent-
customization/mcp-servers) - whoami/read_document/login become directly
callable tools, not just something the hook relays reactively.

**No `--global` support** (unlike claude-code/cursor/gemini-cli/windsurf/
copilot-cli): a 2026-08 doc search found no confirmed user-profile-level
file for VS Code's *native agent hooks* specifically - only the
workspace-scoped `.github/hooks/*.json` above. VS Code's "Agent Plugins"
feature does support enabling/disabling a plugin globally vs. per-
workspace, but that's a UI toggle over internal settings storage, not a
raw JSON file this package could write to the way every other agent's
global location is a plain file. This installer therefore has no
GLOBAL_CONFIG_RELATIVE_PATH at all - `cli/main.py` rejects `--global` for
`vscode` with a clear error rather than silently guessing a path, which is
exactly the mistake (`.vscode/hooks.json`) this whole module already had
to recover from once.
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
