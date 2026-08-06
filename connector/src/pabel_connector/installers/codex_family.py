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

All four of `base.SHARED_ENV_VARS` (`PABEL_KEYCLOAK_URL`/`_REALM`/
`_CLIENT_ID`, `PABEL_SERVER_URL`) are captured the same way, straight into
the connector server's own `[mcp_servers.<name>.env]` table - confirmed
real, real syntax (`config.toml`'s own docs show exactly this shape for a
stdio server's env vars). This is not just a convenience: `relay.py`/
`keycloak_client.py` read these from `os.environ` *inside the
`mcp_local_server.py` subprocess Codex CLI/ChatGPT desktop spawn* - not from
whatever shell happened to run `pabel-connector install`, and not from
whatever the OS's persistent user/system environment says either, since a
GUI app already running when a persistent variable changes keeps its own
stale environment snapshot until fully restarted (confirmed live 2026-08:
an employee had all four set correctly and persistently, but ChatGPT
desktop/Codex CLI had already been running since before that, so neither
picked up the change without a full restart - a real, repeatable point of
confusion this closes entirely by making config.toml itself the only
source of truth these two products' own tool subprocess ever needs).
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

    # Captured from THIS process's own environment (the shell running
    # `pabel-connector install`), then written into the connector server's
    # own [env] table - not left for the OS-level environment to supply
    # later, which is exactly what broke live (see this module's docstring).
    captured = {name: os.environ[name] for name in base.SHARED_ENV_VARS if os.environ.get(name)}
    missing = [name for name in base.SHARED_ENV_VARS if name not in captured]
    entry = {"command": command, "args": args}
    if captured:
        entry["env"] = captured
    servers[connector_server_name] = entry

    server_url = captured.get("PABEL_SERVER_URL")
    if server_url:
        servers[DEPLOYED_SERVER_NAME] = {"url": server_url}

    if missing:
        env_note = (
            f"\n[!!] Not set in this shell, so not captured: {', '.join(missing)}. "
            f"pabel-connector's own tools need every one of these to actually run "
            f"(see pabel_client/relay.py, keycloak_client.py) - set them and re-run "
            f"install to add them to \"{connector_server_name}\"'s own [env] block."
            + (f" The deployed '{DEPLOYED_SERVER_NAME}' server entry was also skipped, "
               f"same reason (config.toml has no confirmed ${{VAR}}-expansion for a "
               f"server url - see this module's docstring)."
               if "PABEL_SERVER_URL" in missing else "")
        )
    else:
        env_note = ""

    base.write_toml(path, data)
    captured_note = (f"with {', '.join(captured)} captured directly into its own "
                     f"[env] block" if captured else "with no env vars captured (none "
                     f"were set in this shell)")
    return (
        f"Registered pabel-connector's whoami/read_document/materialize_document tools "
        f"as \"{connector_server_name}\" in {path} (shared with every other Codex "
        f"CLI/ChatGPT Desktop MCP config on this machine), {captured_note} - Codex "
        f"CLI/ChatGPT desktop inject these straight into the tool's own subprocess, so "
        f"nothing needs to be set in your own shell or Windows environment for this to "
        f"work, and no app restart is needed for a *new* install to take effect (only "
        f"for updating an already-running one - see 'Env vars' above).{env_note}\n"
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
