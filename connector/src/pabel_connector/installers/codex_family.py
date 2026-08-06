"""Shared install/uninstall logic for Codex CLI and ChatGPT Desktop -
NOT a registered installer itself (no `name`/`status`, not in
installers/registry.py's INSTALLERS dict).

Confirmed 2026-08 against OpenAI's own docs (developers.openai.com/codex/mcp,
learn.chatgpt.com/docs/extend/mcp), not assumed: "The ChatGPT desktop app,
Codex CLI, and IDE extension share this configuration" - all three read the
exact same `~/.codex/config.toml`. They are not two independent integrations
to build; they are two product names that both end up mutating one file.
That file has no confirmed hook/tool-interception mechanism at all comparable
to Claude Code's `PreToolUse` - only `default_tools_approval_mode`/per-tool
`approval_mode` (prompt vs. auto-execute) and `disabled_tools` (a full
deny-list), none of which can substitute a blocked call's content the way
`core/decide.py` does for agents with a real hook. So, unlike every other
installer in this package, `install()` here can only ever register
`whoami`/`read_document`/`materialize_document` as directly-callable MCP
tools - zero enforcement, no interception of a direct encrypted-file read.
This is a real product limitation of Codex CLI/ChatGPT Desktop, not a
shortcut taken by this package - see docs/known-gaps.md.

Despite the shared file, codex_cli.py and chatgpt_desktop.py stay two
separate registry entries with two separate agent_ids (two separate Keycloak
client_credentials installations, per server/agents_admin.py) - an
organization may want to authorize one product but not the other, a real
distinction even though both mechanically land in one file.

Name collision this module exists to avoid: if both installers wrote a
server literally named "pabel-connector", installing the second product
would silently overwrite the first's `args` (which bakes in that product's
own agent_id - see base.mcp_server_command()). Each caller therefore passes
its own distinct `connector_server_name` (e.g. "pabel-connector-codex-cli")
so both can coexist in the same file. The "pabel" entry (the deployed HTTP
server) has no such problem - both callers write the exact same content for
it, an idempotent no-op collision, not a conflict.

`PABEL_SERVER_URL` is written as a literal value, not `${PABEL_SERVER_URL}`
the way .mcp.json does for Claude Code/VS Code: nothing found in Codex's own
config.toml docs confirms any `${VAR}`-style expansion for a server's `url`
field (only `bearer_token_env_var`, an env-var *name* indirection for an
auth token specifically - a different mechanism). Absent that confirmation,
this reads the real value from the installing shell's own environment at
install time and bakes it in - if the deployed server's URL ever changes,
re-run install to pick it up, rather than silently going stale.
"""

import os
from pathlib import Path

import tomlkit

from . import base

GLOBAL_CONFIG_RELATIVE_PATH = Path(".codex") / "config.toml"
DEPLOYED_SERVER_NAME = "pabel"


def config_path() -> Path:
    """Resolved fresh on every call, never cached at import time - unlike
    the relative-path constant above, `Path.home()` (inside
    base.global_config_path()) can be monkeypatched per-test, and a
    module-level constant would freeze whatever `Path.home()` returned the
    moment this module was first imported, silently ignoring any later
    monkeypatch (caught by a real test failure during this feature's own
    development - the printed report kept showing this machine's real
    home directory instead of a test's tmp_path)."""
    return base.global_config_path(GLOBAL_CONFIG_RELATIVE_PATH)


def install_mcp_registration(agent_id: str, connector_server_name: str) -> str:
    path = config_path()
    data = base.read_toml(path)
    servers = data.setdefault("mcp_servers", tomlkit.table())
    command, *args = base.mcp_server_command(agent_id)
    servers[connector_server_name] = {"command": command, "args": args}

    server_url = os.environ.get("PABEL_SERVER_URL")
    if server_url:
        servers[DEPLOYED_SERVER_NAME] = {"url": server_url}
        url_note = ""
    else:
        url_note = (
            f"\n[!!] PABEL_SERVER_URL is not set in this shell - the deployed "
            f"'{DEPLOYED_SERVER_NAME}' server was NOT registered (config.toml has no "
            f"confirmed variable-expansion syntax for a server url, so the real value "
            f"must be known at install time - see this module's docstring). Set "
            f"PABEL_SERVER_URL and re-run install to add it, or add it yourself:\n"
            f"  [mcp_servers.{DEPLOYED_SERVER_NAME}]\n  url = \"<the real deployed server URL>\""
        )

    base.write_toml(path, data)
    return (
        f"Registered pabel-connector's whoami/read_document/materialize_document tools "
        f"as \"{connector_server_name}\" in {path} (shared with every other Codex "
        f"CLI/ChatGPT Desktop MCP config on this machine).{url_note}\n"
        f"No enforcement exists for this product - no hook/interception mechanism is "
        f"confirmed to exist here at all. These tools must be called explicitly; a "
        f"direct read of an encrypted document is never blocked or substituted "
        f"automatically. See docs/known-gaps.md."
    )


def uninstall_mcp_registration(agent_id: str, connector_server_name: str) -> str:
    path = config_path()
    data = base.read_toml(path)
    servers = data.get("mcp_servers", {})
    removed = connector_server_name in servers
    if removed:
        del servers[connector_server_name]
        base.write_toml(path, data)
    return (
        f"Removed \"{connector_server_name}\" from {path}."
        if removed else
        f"No \"{connector_server_name}\" entry found in {path} - nothing to do."
    )
