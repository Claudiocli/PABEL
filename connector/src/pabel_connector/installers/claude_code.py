"""Claude Code's PreToolUse hook + MCP server registration - written by
`pabel-connector install claude-code --dir .`, the exact same command
every other agent uses. Claude Code doesn't get a privileged installation
path: this project is for every agent equally. An earlier version of this
package shipped a separate marketplace plugin (claude-plugin/pabel/, with
its own install steps and a hand-written hook script) that only Claude
Code got - exactly the kind of special-casing every other agent's
installer already avoided - so it was removed entirely once this
installer could do the same job the same way as everyone else.

Config: `.claude/settings.json`, `hooks.PreToolUse` - CONFIRMED nested
shape (`[{"hooks": [{"type": "command", "command": ..., "timeout": ...}]}]`,
"matcher" omitted for catch-all coverage, since Claude Code fires this hook
for every tool regardless) - this is the exact shape already live and
end-to-end VERIFIED in this repo's own `.claude/settings.json`. Unlike
installers/vscode.py, no PowerShell `windows` override is needed here:
this repo's own working config proves Claude Code's hook execution accepts
a plain quoted-path command on Windows without a call operator (VS Code's
own execution mechanism is confirmed different, not a universal Windows
rule - see installers/vscode.py's docstring).

Also registers, in `.mcp.json`: the deployed server's own "pabel" tools
(http, confirmed working via core/decide.py's agent_token-injection
branch) and mcp_local_server.py's "pabel-connector" tools (stdio, whoami/
read_document/login, needing no injection).

Supports `--global` (writes `~/.claude/settings.json`, confirmed real
Claude Code user-level settings location - applies to every project, not
just the one `--dir` points at). `.mcp.json` registration always stays
per-project regardless: Claude Code's own user-scope MCP mechanism (`claude
mcp add --scope user`) is a CLI-managed store, not a plain JSON file this
package can confirm the shape of - unattempted, not just unconfirmed.
"""

from pathlib import Path

from . import base

name = "claude-code"
status = "verified"

CONFIG_RELATIVE_PATH = Path(".claude") / "settings.json"
GLOBAL_CONFIG_RELATIVE_PATH = CONFIG_RELATIVE_PATH
MCP_CONFIG_RELATIVE_PATH = Path(".mcp.json")
HOOK_KEYS = ["claude-code"]


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return base_dir / CONFIG_RELATIVE_PATH


def _merge_nested_hook_list(existing: list, command: str) -> list:
    """Claude Code's PreToolUse entries are each wrapped in a
    `{"hooks": [...]}` block (optionally with a "matcher") rather than the
    flat list every other installer's config uses - see base.py's
    merge_hook_list, which this mirrors but for the nested shape."""
    existing = list(existing or [])
    for block in existing:
        for entry in block.get("hooks", []) if isinstance(block, dict) else []:
            if isinstance(entry, dict) and entry.get("command") == command:
                entry["timeout"] = base.HOOK_TIMEOUT_SECONDS
                return existing  # already installed
    existing.append({"hooks": [{"type": "command", "command": command,
                                  "timeout": base.HOOK_TIMEOUT_SECONDS}]})
    return existing


def install(base_dir: Path, global_: bool = False) -> str:
    path = base.global_config_path(GLOBAL_CONFIG_RELATIVE_PATH) if global_ else config_path(base_dir)
    data = base.read_json(path)
    hooks = data.setdefault("hooks", {})
    hooks["PreToolUse"] = _merge_nested_hook_list(hooks.get("PreToolUse"),
                                                  base.hook_command("claude-code"))
    base.write_json(path, data)

    mcp_path = base_dir / MCP_CONFIG_RELATIVE_PATH
    mcp_data = base.read_json(mcp_path)
    servers = mcp_data.setdefault("mcpServers", {})
    servers.setdefault("pabel", {"type": "http", "url": "${PABEL_SERVER_URL}"})
    command, *args = base.mcp_server_command(name)
    servers["pabel-connector"] = {"command": command, "args": args}
    base.write_json(mcp_path, mcp_data)

    scope = " (global - applies to every project, not just this directory)" if global_ else ""
    return (
        f"Wrote a catch-all PreToolUse hook to {path}{scope}\n"
        f"Registered the deployed server's own tools and mcp_local_server.py's "
        f"whoami/read_document/login tools in {mcp_path} (always per-project - "
        f"see this module's docstring for why).\n"
        f"(VERIFIED end-to-end - see docs/phase2-engineering-notes.md.)"
    )
