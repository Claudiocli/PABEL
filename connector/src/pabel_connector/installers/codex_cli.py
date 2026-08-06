"""OpenAI Codex CLI - `status = "mcp-only"`, not a full adapter.

Previously shipped as DEGRADED (Bash-only coverage, hooks feature-flagged
on). A 2026-08 re-check of current docs/issue trackers (prompted by
installers/vscode.py's own path guess turning out simply wrong, which led
to re-verifying every "built to spec" adapter in this package rather than
trusting the original research unverified) found a fact the original
research missed: **Codex CLI's hooks feature is explicitly documented as
"experimental (disabled by default, not available on Windows)"** - not a
niche edge case, a platform this feature does not run on at all. This is
the exact same blocking criterion already applied to Cline
(connector/docs/known-gaps.md): employee machines can't be assumed
non-Windows (this project's own dev machine is Windows), so a hook
surface unavailable there isn't a workable adapter, regardless of how good
its (already Bash-only) coverage would be on the platforms where it does
load.

That still leaves a real install action, though: Codex CLI shares its
`~/.codex/config.toml` MCP configuration with the ChatGPT desktop app (see
installers/codex_family.py's docstring for the confirmed source) - so
`install()` here registers `whoami`/`read_document`/`materialize_document`
as directly-callable MCP tools, same as every other agent's "Direct MCP
tools" column, just without any hook underneath it. No enforcement: a
direct read of an encrypted document is never blocked or substituted -
these tools only run if explicitly called. See docs/known-gaps.md.

GLOBAL_ONLY: unlike every hook-based installer, there is no meaningful
per-project variant here - `~/.codex/config.toml` is the one file both
Codex CLI and the ChatGPT desktop app read, always. cli/main.py rejects
`install codex-cli --dir .` without `--global` rather than silently writing
somewhere neither product would ever look.

Also installs an informational skill (see codex_family.install_skill()) -
same non-enforcing content as Claude Code's, adapted for the fact that
nothing here relays a blocked read automatically.
"""

from pathlib import Path

from . import codex_family

name = "codex-cli"
status = "mcp-only"
GLOBAL_ONLY = True

GLOBAL_CONFIG_RELATIVE_PATH = Path(".codex") / "config.toml"
CONNECTOR_SERVER_NAME = "pabel-connector-codex-cli"


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return codex_family.config_path()  # base_dir ignored - GLOBAL_ONLY, one shared file


def install(base_dir: Path, global_: bool = False) -> str:
    mcp_message = codex_family.install_mcp_registration(name, CONNECTOR_SERVER_NAME)
    skill_message = codex_family.install_skill()
    return f"{mcp_message}\n{skill_message}"


def uninstall(base_dir: Path, global_: bool = False) -> str:
    return codex_family.uninstall_mcp_registration(name, CONNECTOR_SERVER_NAME)
