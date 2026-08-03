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
    name: str
    status: str  # "verified" | "unverified" | "degraded" | "gap"

    def install(self, base_dir: Path) -> str:
        """Read-merge-write this agent's own hook-config file so it invokes
        pabel-connector-hook. Returns a human-readable summary of what was
        written and what the user still needs to do (env vars, login)."""
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


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
