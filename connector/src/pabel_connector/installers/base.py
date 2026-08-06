"""The Strategy interface every per-agent installer implements: discovering
and read-merge-writing that agent's own hook-config file, plus reporting
which prerequisites (env vars, feature flags) still need setting. Kept as
a separate axis from adapters/ (the wire-format Strategy) because
install-time concerns - where a config file lives, how to merge into
existing JSON, what env vars to print - are a genuinely different concern
that would force every adapter module to also know shell/OS install
mechanics if the two were merged.

Shared env vars every agent needs, on top of whatever install-specific
ones an installer's required_env() adds:
  PABEL_KEYCLOAK_URL, PABEL_KEYCLOAK_REALM, PABEL_KEYCLOAK_CLIENT_ID - the
  human's own login (see pabel_client/keycloak_client.py + session.py).
  PABEL_SERVER_URL - the deployed PABEL server's streamable-http URL. One
  shared server serves every agent product and every installation of it
  (see server/compose.yml) - genuinely one global value, the same for
  every agent installed on a given machine.

Besides these env vars, each installation also needs its own agent
credential (client_id/client_secret an admin already created via
server/agents_admin.py create-installation) - see cli/main.py's
`install` command and pabel_client/agent_session.py. That credential is
never an env var: it's persisted locally once, at install time.
"""

import json
import sys
from pathlib import Path
from typing import List, Protocol

import tomlkit

SHARED_ENV_VARS = [
    "PABEL_KEYCLOAK_URL",
    "PABEL_KEYCLOAK_REALM",
    "PABEL_KEYCLOAK_CLIENT_ID",
    "PABEL_SERVER_URL",
]

HOOK_TIMEOUT_SECONDS = 200
"""core/decide.py now blocks inside the hook to run an interactive browser
+MFA login on demand (oauth_browser.py's own callback wait is 180s) rather
than just denying and telling a human to run a separate CLI command later -
found necessary 2026-08 after a live VS Code Copilot session's login kept
silently failing to persist, root-caused to the *agent's own* default hook
timeout (commonly ~60s) killing the hook subprocess before a human could
finish the browser flow. Every installer below writes this value as the
hook entry's own "timeout" field so the host doesn't kill it first - a
number comfortably above 180s, not exact per-vendor tuning. Best-effort:
some vendors may cap this lower themselves; nothing here can detect or
override that, only ask for enough room."""


class Installer(Protocol):
    """Not every attribute/method here is present on every installer - this
    documents the common shape, not an enforced interface (nothing in this
    package actually type-checks against it):
      - The two documented-gap installers (cline, continue_dev) have no
        `config_path`/`HOOK_KEYS`/`GLOBAL_CONFIG_RELATIVE_PATH` at all -
        `install()` just explains why and writes nothing (see cli/main.py's
        `hasattr(installer, "config_path")` checks).
      - codex_cli/chatgpt_desktop have a `config_path` (both point at the
        same shared `~/.codex/config.toml` - see installers/codex_family.py)
        but no `HOOK_KEYS` at all: neither product has any hook/interception
        mechanism, only MCP tool registration - cli/main.py's
        `_hook_wiring_ok` skips them for exactly this reason rather than
        crashing on a `HOOK_KEYS` that doesn't exist.
      - `GLOBAL_CONFIG_RELATIVE_PATH` only exists on installers with a
        confirmed user-level location (see global_config_path()'s
        docstring) - `install()`'s `global_` parameter is meaningless
        without it.
    """
    name: str
    status: str  # "verified" | "unverified" | "degraded" | "gap"

    def install(self, base_dir: Path, global_: bool = False) -> str:
        """Read-merge-write this agent's own hook-config file so it invokes
        pabel-connector-hook. Returns a human-readable summary of what was
        written and what the user still needs to do (env vars, login).
        `global_` writes to GLOBAL_CONFIG_RELATIVE_PATH instead of
        `base_dir` where supported - see cli/main.py's `_supports_global()`."""
        ...

    def required_env(self) -> List[str]:
        """Env var names this agent's hook subprocess needs, beyond
        SHARED_ENV_VARS."""
        ...


def hook_command(key: str) -> str:
    """The fully-resolved invocation for registry key `key`, run via the
    same interpreter pabel-connector was installed into - deliberately not
    the bare `pabel-connector-hook` console script name, since whether an
    agent's hook subprocess inherits enough of the installing shell's PATH
    to find it is unconfirmed for most targets in this package (notably on
    Windows) - see connector/docs/coverage-matrix.md."""
    return f'"{sys.executable}" -m pabel_connector.hook {key}'


def mcp_server_command(agent_id: str) -> List[str]:
    """The argv (command + args) for registering mcp_local_server.py as a
    stdio MCP server under this installation's own agent_id - the same
    interpreter pabel-connector was installed into, same reasoning as
    hook_command() above. Returned as a list (not a shell string like
    hook_command()) because every confirmed MCP-registration schema this
    package targets (Claude Code's/Cursor's mcpServers, VS Code's servers)
    takes "command"/"args" as separate JSON fields, spawned directly with
    no shell involved - so, unlike hooks, there's no PowerShell
    call-operator quoting problem to work around here at all."""
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
    """Home-relative equivalent of a workspace CONFIG_RELATIVE_PATH, for
    installers whose confirmed global/user-level hook location is simply
    "the same relative path, rooted at the user's home directory instead
    of a project" (Claude Code's `~/.claude/settings.json`, Cursor's
    `~/.cursor/hooks.json` - both confirmed 2026-08 against real vendor
    docs, not guessed). Installers
    whose global location has a genuinely different shape (Windsurf's
    `~/.codeium/windsurf/hooks.json`, Copilot CLI's `~/.copilot/hooks/`
    directory) compute their own path instead of using this helper;
    installers with no confirmed global location at all (VS Code - no
    user-level file found for its native agent hooks, only workspace
    `.github/hooks/*.json`) have no GLOBAL_CONFIG_RELATIVE_PATH and don't
    support `--global` - see docs/coverage-matrix.md."""
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
    """TOML equivalent of read_json(), for installers/codex_family.py - the
    only consumer today (Codex CLI/ChatGPT Desktop's shared
    `~/.codex/config.toml`). Uses tomlkit rather than the stdlib's
    read-only `tomllib` specifically because it round-trips: a real
    config.toml an employee already hand-edited (other MCP servers, model
    settings, comments) must survive an install()/uninstall() pass
    unchanged apart from the pabel entries themselves, the same
    merge-not-clobber discipline read_json()/write_json() already give
    every JSON-based installer."""
    if not path.exists():
        return tomlkit.document()
    return tomlkit.parse(path.read_text(encoding="utf-8"))


def write_toml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(data), encoding="utf-8")


def remove_matching_commands(node, commands) -> bool:
    """Recursively remove hook-entry dicts whose "command" is in `commands`
    from any list found within `node` (mutates lists in place). Returns
    True if anything was removed - lets uninstall() report accurately
    instead of always claiming success."""
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
    """Shared install() body for single-hook-point agents whose Windows
    execution needs the PowerShell call-operator override (vscode,
    copilot-cli - both go through the same `.github/hooks/*.json`
    convention and execution path). Mutates `data["hooks"][event_key]` in
    place; the caller still owns reading/writing the file and any
    agent-specific top-level keys (e.g. copilot-cli's own "version": 1)."""
    hooks = data.setdefault("hooks", {})
    hooks[event_key] = merge_hook_list(
        hooks.get(event_key), hook_command(agent_key),
        extra_fields={"windows": hook_command_windows(agent_key)})


def install_multi_point_hooks(data: dict, agent_key: str, hook_points) -> None:
    """Shared install() body for multi-hook-point agents (Cursor, Windsurf):
    each hook point gets its own merge_hook_list() call keyed
    "<agent_key>:<point>", matching registry.py's/hook.py's convention for
    resolving which agent_id a multi-point key belongs to. Mutates
    `data["hooks"]` in place; the caller still owns reading/writing the
    file and any agent-specific top-level keys (e.g. Cursor's own
    "version": 1)."""
    hooks = data.setdefault("hooks", {})
    for point in hook_points:
        hooks[point] = merge_hook_list(hooks.get(point), hook_command(f"{agent_key}:{point}"))


def merge_hook_list(existing: list, command: str, extra_fields: dict = None) -> list:
    """Append `command` to a hooks-array-of-objects config, keyed by exact
    command string - makes install() idempotent and never duplicates or
    clobbers a hook some other tool already put there. If an entry for this
    exact command already exists, `extra_fields` (and `timeout`) are still
    merged into it (upsert, not skip) - re-running install() after this
    package fixes a bug in what it writes alongside `command` (e.g. adding
    a `windows` override, or raising HOOK_TIMEOUT_SECONDS) must repair an
    already-installed config, not silently leave the old, broken entry in
    place."""
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
