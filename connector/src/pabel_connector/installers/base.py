"""Shared install-time helpers: finding and read-merge-writing each agent's
own config file, and reporting which prerequisites still need setting.

A separate axis from adapters/ (the wire-format Strategy) - merging the two
would force every adapter to also know shell/OS install mechanics.

An installer is a plain module exposing `name`/`status`, `install(base_dir,
global_=False) -> str` and `required_env() -> List[str]`. Nothing enforces
that shape; cli/main.py reads attributes defensively because not every
installer has every one:
  - cline/continue_dev (documented gaps) have no `config_path`/`HOOK_KEYS`/
    `GLOBAL_CONFIG_RELATIVE_PATH` - `install()` explains why and writes
    nothing.
  - codex_cli/chatgpt_desktop have a `config_path` but no `HOOK_KEYS`:
    neither product has any interception mechanism, only MCP registration.
  - opencode has neither `HOOK_KEYS` nor a JSON-shaped hook, and supplies
    its own `uninstall()`/`hook_wiring_problem()` instead.
  - `GLOBAL_CONFIG_RELATIVE_PATH` exists only where a user-level location is
    confirmed; without it `global_` is meaningless.

Every agent needs PABEL_KEYCLOAK_URL/_REALM/_CLIENT_ID (the human's login)
and PABEL_SERVER_URL (one shared deployment for every agent on a machine),
plus its own client_id/client_secret - which is never an env var, but
persisted locally once at install time.
"""

import json
import sys
from pathlib import Path
from typing import List

import tomlkit

SHARED_ENV_VARS = [
    "PABEL_KEYCLOAK_URL",
    "PABEL_KEYCLOAK_REALM",
    "PABEL_KEYCLOAK_CLIENT_ID",
    "PABEL_SERVER_URL",
]

HOOK_TIMEOUT_SECONDS = 200
"""decide() blocks inside the hook to run an interactive browser+MFA login on
demand (oauth_browser.py waits 180s for the callback), so every installer
writes this as the hook entry's own timeout to stop the host killing the
subprocess first - a live VS Code session's login kept silently failing to
persist because of the agent's own ~60s default. Best-effort: a vendor may
cap it lower and nothing here can detect that."""


def hook_command(key: str) -> str:
    """Invocation for registry key `key`, via the same interpreter
    pabel-connector was installed into - deliberately not the bare
    `pabel-connector-hook` script, since whether an agent's hook subprocess
    inherits enough PATH to find it is unconfirmed for most targets."""
    return f'"{sys.executable}" -m pabel_connector.hook {key}'


def mcp_server_command(agent_id: str) -> List[str]:
    """argv for registering mcp_local_server.py as a stdio MCP server under
    this installation's own agent_id. A list rather than a shell string
    because every MCP schema targeted here takes command/args as separate
    fields, spawned with no shell - so no PowerShell quoting problem."""
    return [sys.executable, "-m", "pabel_connector.mcp_local_server", agent_id]


def hook_command_windows(key: str) -> str:
    """Same invocation as `hook_command`, prefixed with PowerShell's call
    operator `&` - confirmed necessary 2026-08 via a real, live VS Code
    Copilot session: VS Code's `windows`/default `command` field is executed
    through `powershell -Command` on Windows (per
    code.visualstudio.com/docs/agent-customization/hooks, "powershell maps
    to windows"), where a bare quoted-path-plus-arguments string like
    `"C:\\...\\python.exe" -m pabel_connector.hook vscode` is a parser
    error (`Unexpected token '-m' in expression or statement.`) - PowerShell
    parses the leading quoted string as a standalone expression and refuses
    to treat what follows as arguments unless `&` invokes it as a command.
    cmd.exe and POSIX shells don't need (or, for cmd.exe, even support) this
    prefix, which is why this is a separate Windows-only override rather
    than a change to `hook_command` itself."""
    return f"& {hook_command(key)}"


def global_config_path(relative_path: Path) -> Path:
    """For installers whose global location is just the workspace relative
    path rooted at home instead (Claude Code, Cursor). Those with a
    different shape (Windsurf, Copilot CLI) compute their own; those with no
    confirmed location (VS Code) have no GLOBAL_CONFIG_RELATIVE_PATH and
    don't support --global at all, rather than guess a path."""
    return Path.home() / relative_path


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_toml(path: Path):
    """TOML equivalent of read_json(), for codex_family.py. tomlkit rather
    than the stdlib's read-only tomllib because it round-trips: a
    hand-edited config.toml must survive install()/uninstall() unchanged
    apart from the pabel entries."""
    if not path.exists():
        return tomlkit.document()
    return tomlkit.parse(path.read_text(encoding="utf-8"))


def write_toml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(data), encoding="utf-8")


def remove_matching_commands(node, commands) -> bool:
    """Recursively remove hook entries whose "command" is in `commands`,
    mutating lists in place. Returns whether anything was removed, so
    uninstall() can report accurately instead of always claiming success."""
    removed = False
    if isinstance(node, dict):
        for value in node.values():
            if remove_matching_commands(value, commands):
                removed = True
    elif isinstance(node, list):
        before = len(node)
        node[:] = [item for item in node
                   if not (isinstance(item, dict) and item.get("command") in commands)]
        if len(node) != before:
            removed = True
        for item in node:
            if remove_matching_commands(item, commands):
                removed = True
    return removed


def install_windows_aware_hook(data: dict, event_key: str, agent_key: str) -> None:
    """Shared install() body for single-hook-point agents needing the
    PowerShell call-operator override on Windows (vscode, copilot-cli).
    Mutates data in place; the caller still owns reading/writing the file."""
    hooks = data.setdefault("hooks", {})
    hooks[event_key] = merge_hook_list(
        hooks.get(event_key), hook_command(agent_key),
        extra_fields={"windows": hook_command_windows(agent_key)})


def install_multi_point_hooks(data: dict, agent_key: str, hook_points) -> None:
    """Shared install() body for multi-hook-point agents (Cursor, Windsurf):
    each point keyed "<agent_key>:<point>", matching registry.py/hook.py's
    convention for resolving which agent_id a multi-point key belongs to."""
    hooks = data.setdefault("hooks", {})
    for point in hook_points:
        hooks[point] = merge_hook_list(hooks.get(point), hook_command(f"{agent_key}:{point}"))


def merge_hook_list(existing: list, command: str, extra_fields: dict = None) -> list:
    """Append `command` to a hooks array, keyed by exact command string, so
    install() is idempotent and never clobbers another tool's hook. Existing
    entries are upserted, not skipped: re-running install after this package
    fixes what it writes alongside `command` must repair an already-installed
    config, not leave the broken entry in place."""
    existing = list(existing or [])
    for entry in existing:
        if isinstance(entry, dict) and entry.get("command") == command:
            entry["timeout"] = HOOK_TIMEOUT_SECONDS
            if extra_fields:
                entry.update(extra_fields)
            return existing  # already installed
    entry = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}
    if extra_fields:
        entry.update(extra_fields)
    existing.append(entry)
    return existing
