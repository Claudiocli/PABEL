"""OpenAI's ChatGPT desktop app - `status = "mcp-only"`, not a full adapter.

New this session (2026-08), never previously considered in this package.
Confirmed against OpenAI's own docs (learn.chatgpt.com/docs/extend/mcp),
not assumed: the desktop app's MCP server list lives in the exact same
`~/.codex/config.toml` Codex CLI reads ("The ChatGPT desktop app, Codex
CLI, and IDE extension share this configuration") - see
installers/codex_family.py's docstring for the full finding, including why
that makes a naming collision a real concern this module and codex_cli.py
both have to avoid.

The desktop app has no hook/tool-interception mechanism at all - not a
Windows-specific gap like Codex CLI's hooks (which at least exist,
disabled, on other platforms), simply a mechanism this product has never
had. What it does have (`default_tools_approval_mode`/per-tool
`approval_mode`, `disabled_tools`) controls whether a tool prompts or is
blocked outright, never what content a call returns - nothing that could
substitute a blocked read the way `core/decide.py` does for a real hook.
So, exactly like codex_cli.py, `install()` here can only register
`whoami`/`read_document`/`materialize_document` as directly-callable MCP
tools - zero enforcement. See docs/known-gaps.md.

Kept as a separate registry entry from codex-cli (rather than one
"install both at once" action) even though they share a file: an
organization may authorize one product but not the other via
server/agents_admin.py's per-product `required_role`, a real distinction
regardless of the shared config mechanics.

GLOBAL_ONLY: the desktop app has no concept of a project directory at all
- `~/.codex/config.toml` is the only place it ever looks. Same rejection
as codex_cli.py for `install chatgpt-desktop --dir .` without `--global`.

Also installs an informational skill (see codex_family.install_skill()) -
the exact same file codex_cli.py installs (one shared skill for this
package, not one per product), since Agent Skills are a cross-vendor
standard both products read from the same location.
"""

from pathlib import Path

from . import codex_family

name = "chatgpt-desktop"
status = "mcp-only"
GLOBAL_ONLY = True

GLOBAL_CONFIG_RELATIVE_PATH = Path(".codex") / "config.toml"
CONNECTOR_SERVER_NAME = "pabel-connector-chatgpt-desktop"


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
