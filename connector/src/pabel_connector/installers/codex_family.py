"""Shared install/uninstall for Codex CLI and ChatGPT Desktop - not a
registered installer itself (no `name`/`status`, not in INSTALLERS).

Confirmed against OpenAI's docs, not assumed: the desktop app, Codex CLI and
the IDE extension all read the same `~/.codex/config.toml`. Two product
names mutating one file, not two integrations.

Neither product has any interception mechanism comparable to a PreToolUse
hook - only `approval_mode` (prompt vs. auto-execute) and `disabled_tools`
(a deny-list), neither of which can substitute a blocked call's content. So
`install()` here only ever registers MCP tools: zero enforcement, no
blocking of a direct encrypted-file read. A real product limitation, not a
shortcut - see docs/known-gaps.md.

They stay two registry entries with two agent_ids regardless, since an
organization may authorize one product and not the other. Each therefore
passes its own `connector_server_name`: writing one shared
"pabel-connector" would make the second install silently overwrite the
first's `args`, which bake in that product's own agent_id.

All four SHARED_ENV_VARS are captured into the server's own `[env]` table
rather than left to the OS environment. This is not convenience: a GUI app
already running when a persistent variable changes keeps its stale snapshot
until fully restarted, which confused a real employee whose values were all
set correctly. `PABEL_SERVER_URL` is baked in literally because nothing in
Codex's docs confirms `${VAR}` expansion for a server url - re-run install
if the deployment URL changes.

Also installs an informational skill. Agent Skills are a cross-vendor
standard both products read natively from `$HOME/.agents/skills/`, using the
same `SKILL.md` shape already built for Claude Code - so unlike the earlier
rejected `AGENTS.md` nudge, there was nothing new to build or maintain. The
content is still explicitly non-enforcing.
"""

import os
from importlib import resources
from pathlib import Path

import tomlkit

from . import base

GLOBAL_CONFIG_RELATIVE_PATH = Path(".codex") / "config.toml"
DEPLOYED_SERVER_NAME = "pabel"
SKILL_RELATIVE_PATH = Path(".agents") / "skills" / "pabel" / "SKILL.md"


def config_path() -> Path:
    """Resolved fresh on every call, never cached at import time: a
    module-level constant would freeze whatever `Path.home()` returned on
    first import, silently ignoring any later monkeypatch."""
    return base.global_config_path(GLOBAL_CONFIG_RELATIVE_PATH)


def install_mcp_registration(agent_id: str, connector_server_name: str) -> str:
    path = config_path()
    data = base.read_toml(path)
    servers = data.setdefault("mcp_servers", tomlkit.table())
    command, *args = base.mcp_server_command(agent_id)

    # From this process's own environment, written into the server's [env]
    # table rather than left to the OS to supply later - see the docstring.
    captured ={name: os.environ[name] for name in base.SHARED_ENV_VARS if os.environ.get(name)}
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


def install_skill() -> str:
    """Copy the bundled skills/pabel-codex/SKILL.md to the user-scoped
    `$HOME/.agents/skills/pabel/SKILL.md`, always overwriting so a re-install
    picks up this version's text. Both products share one file - installing
    the second just rewrites identical content, not a conflict."""
    skill_path = base.global_config_path(SKILL_RELATIVE_PATH)
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    source = resources.files("pabel_connector").joinpath("skills", "pabel-codex", "SKILL.md")
    skill_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return (
        f"Installed an informational skill (not a security control - see "
        f"that file's own closing section) to {skill_path}, read natively by "
        f"Codex CLI, the ChatGPT desktop app, and the IDE extension alike "
        f"(one shared, user-scoped location, same as the config.toml entry "
        f"above)."
    )


def uninstall_mcp_registration(agent_id: str, connector_server_name: str) -> str:
    """Removes only this product's own MCP entry. Never the shared "pabel"
    deployed-server entry or the shared skill file, since the other product
    may still be installed and would just write them back."""
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
