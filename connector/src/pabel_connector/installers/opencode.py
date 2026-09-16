"""opencode (opencode.ai) - the only agent in this package whose enforcement
is delivered as a JS file rather than a command string in a JSON config.

STATUS: BUILT-TO-SPEC, UNVERIFIED. Paths and schemas below are confirmed
against opencode's own docs (opencode.ai/docs/config, /docs/plugins,
/docs/mcp-servers, 2026-09); what is NOT confirmed is that a thrown message
from `tool.execute.before` reaches the model - see adapters/opencode.py's
"The assumption this adapter rests on" and docs/known-gaps.md.

Two artifacts get written, not one:

1. **The bridge plugin** - this package's `plugins/opencode/pabel.js` with
   its two tokens substituted - into `.opencode/plugins/` (project) or
   `~/.config/opencode/plugins/` (global). opencode also accepts the
   singular `plugin/`, but only the current plural form is written.

   The hook argv goes in as a JSON array, not a shell string like
   `base.hook_command()` produces: the bridge passes it to `spawn()` with no
   shell, so vscode.py's PowerShell quoting problem can't arise here.

2. **The MCP registration** in the project root or `~/.config/opencode/`.
   Which *file* that is gets resolved against disk, never assumed - opencode
   reads both `opencode.json` and `opencode.jsonc` with no documented
   precedence. A commented `.jsonc` is never rewritten (`_read_config()`).

Deliberately does not capture env vars into an `environment` block the way
codex_family.py does: opencode is a CLI that inherits the invoking shell's
environment, and this is often a *project* file that may get committed.

No `HOOK_KEYS` - cli/main.py's generic wiring check and uninstall both scan
a JSON config for a `command` string, which this enforcement isn't. Own
`uninstall()` and `hook_wiring_problem()` are supplied instead so `doctor`
doesn't silently skip this agent.
"""

import json
import os
import re
import sys
from importlib import resources
from pathlib import Path

from . import base

name = "opencode"
status = "unverified"

HOOK_KEY = "opencode"
CONFIG_RELATIVE_PATH = Path("opencode.json")
PLUGIN_RELATIVE_PATH = Path(".opencode") / "plugins" / "pabel.js"
GLOBAL_CONFIG_RELATIVE_PATH = Path(".config") / "opencode" / "opencode.json"
GLOBAL_PLUGIN_RELATIVE_PATH = Path(".config") / "opencode" / "plugins" / "pabel.js"

CONNECTOR_SERVER_NAME = "pabel-connector"
DEPLOYED_SERVER_NAME = "pabel"


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return _resolved_config_path(base_dir, global_=False)


def plugin_path(base_dir: Path, global_: bool = False) -> Path:
    return (base.global_config_path(GLOBAL_PLUGIN_RELATIVE_PATH) if global_
            else base_dir / PLUGIN_RELATIVE_PATH)


def _resolved_config_path(base_dir: Path, global_: bool) -> Path:
    """The config file to write, preferring one that already exists.

    opencode reads both `opencode.json` and `opencode.jsonc` and documents no
    precedence, so always writing `.json` would be a coin flip on a machine
    that already has a `.jsonc` - the registration could land in a file
    opencode never reads, with no error anywhere."""
    directory = (base.global_config_path(GLOBAL_CONFIG_RELATIVE_PATH).parent if global_
                 else base_dir)
    for basename in ("opencode.jsonc", "opencode.json"):
        candidate = directory / basename
        if candidate.exists():
            return candidate
    return directory / "opencode.json"


# Matches a complete JSON string literal first so that a `//` or `/*` inside
# one is passed through untouched, then either comment form.
_JSONC_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)


def _read_config(path: Path):
    """(data, had_comments) for a .json or .jsonc config. Comments are
    stripped only to parse; json.dumps can't write them back, so install()
    refuses to rewrite a commented file rather than destroy annotations."""
    if not path.exists():
        return {}, False
    text = path.read_text(encoding="utf-8")
    stripped = _JSONC_COMMENT.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text)
    had_comments = stripped != text
    return (json.loads(stripped) if stripped.strip() else {}), had_comments


def hook_argv():
    """argv (not a shell string) invoking this package's hook through the
    same interpreter pabel-connector was installed into."""
    return [sys.executable, "-m", "pabel_connector.hook", HOOK_KEY]


def render_plugin() -> str:
    source = resources.files("pabel_connector").joinpath(
        "plugins", "opencode", "pabel.js").read_text(encoding="utf-8")
    return (source
            .replace("__PABEL_HOOK_ARGV__", json.dumps(hook_argv()))
            .replace("__PABEL_TIMEOUT_MS__", str(base.HOOK_TIMEOUT_SECONDS * 1000)))


def install(base_dir: Path, global_: bool = False) -> str:
    target_plugin = plugin_path(base_dir, global_)
    target_plugin.parent.mkdir(parents=True, exist_ok=True)
    target_plugin.write_text(render_plugin(), encoding="utf-8")

    path = _resolved_config_path(base_dir, global_)
    data, had_comments = _read_config(path)
    servers = data.setdefault("mcp", {})
    command = base.mcp_server_command(name)
    servers[CONNECTOR_SERVER_NAME] = {
        "type": "local", "command": command, "enabled": True}

    server_url = os.environ.get("PABEL_SERVER_URL")
    if server_url:
        servers[DEPLOYED_SERVER_NAME] = {
            "type": "remote", "url": server_url, "enabled": True}

    if had_comments:
        return (
            f"Wrote the PABEL bridge plugin to {target_plugin}\n"
            f"[!!] {path} contains comments, which cannot survive being rewritten "
            f"as JSON - it was left untouched. Add this to its \"mcp\" object "
            f"by hand:\n\n{json.dumps(servers, indent=2)}\n\n"
            f"Enforcement is already active regardless (that's the plugin above); "
            f"this entry only adds the directly-callable MCP tools."
        )

    base.write_json(path, data)

    deployed_note = (
        f"Registered the deployed PABEL server as \"{DEPLOYED_SERVER_NAME}\" too."
        if server_url else
        f"[!!] PABEL_SERVER_URL is not set in this shell, so the deployed "
        f"\"{DEPLOYED_SERVER_NAME}\" server entry was skipped - set it and re-run "
        f"install to add it.")

    return (
        f"Wrote the PABEL bridge plugin to {target_plugin}\n"
        f"(opencode loads plugins from this directory natively; paths confirmed "
        f"against opencode.ai/docs/plugins).\n"
        f"Registered mcp_local_server.py's whoami/read_document/materialize_document "
        f"tools as \"{CONNECTOR_SERVER_NAME}\" in {path}. {deployed_note}\n"
        f"[!!] UNVERIFIED: opencode blocks a tool call by throwing, and whether the "
        f"thrown message reaches the *model* is not confirmed upstream. Blocking "
        f"works either way; the relayed read_document result may or may not be "
        f"delivered. See connector/docs/known-gaps.md."
    )


def uninstall(base_dir: Path, global_: bool = False) -> str:
    """Own implementation: cli/main.py's generic path removes hook entries by
    matching a `command` string in the JSON, and this enforcement is a .js
    file instead."""
    removed = []
    target_plugin = plugin_path(base_dir, global_)
    if target_plugin.exists():
        target_plugin.unlink()
        removed.append(str(target_plugin))
    # install() created these; an empty `.opencode/plugins/` left behind reads
    # like a plugin is still wired when nothing is. Best-effort only: a
    # syncing OneDrive folder holds a lock and raises PermissionError here,
    # and tidying must never abort the MCP-entry removal below, which is the
    # part that actually matters.
    for directory in (target_plugin.parent, target_plugin.parent.parent):
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            break

    path = _resolved_config_path(base_dir, global_)
    data, had_comments = _read_config(path)
    servers = data.get("mcp", {})
    # Both entries, not just the connector: install() writes the deployed
    # server too, and removing only one left the other orphaned in a file the
    # uninstall had just reported as cleaned.
    present = [n for n in (CONNECTOR_SERVER_NAME, DEPLOYED_SERVER_NAME) if n in servers]
    if present and not had_comments:
        for server_name in present:
            del servers[server_name]
        if not servers:
            del data["mcp"]
        if data:
            base.write_json(path, data)
        else:
            path.unlink()  # install() created it and nothing else uses it
        removed.append(", ".join(f'"{n}"' for n in present) + f" in {path}")
    elif present:
        removed.append(f"(left {', '.join(repr(n) for n in present)} in {path} - it has "
                       f"comments that a rewrite would destroy; remove them by hand)")

    return ("Removed " + ", ".join(removed) + "." if removed
            else f"No PABEL plugin or MCP entry found for opencode - nothing to do.")


def hook_wiring_problem(base_dir: Path, global_: bool = False) -> "str | None":
    """None if the bridge plugin is present and points at this interpreter.
    A stored credential with no working enforcement must be loud, not
    silent - see cli/main.py's `_hook_wiring_ok`."""
    target_plugin = plugin_path(base_dir, global_)
    if not target_plugin.exists():
        return (f"no PABEL bridge plugin at {target_plugin} - nothing will actually "
                f"enforce PABEL for 'opencode' until this is fixed. "
                f"Run: pabel-connector install opencode --dir {base_dir}")
    if json.dumps(hook_argv()) not in target_plugin.read_text(encoding="utf-8"):
        return (f"the bridge plugin at {target_plugin} invokes a different interpreter "
                f"than this one - it was likely installed from another Python "
                f"environment. Re-run: pabel-connector install opencode --dir {base_dir}")
    return None
