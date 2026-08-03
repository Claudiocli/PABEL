"""OpenAI Codex CLI - NO ADAPTER (documented gap), same category as Cline.

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
load. Kept registered here (unlike a silent removal) so `pabel-connector
install codex-cli` explains this rather than erroring confusingly.
"""

from pathlib import Path

name = "codex-cli"
status = "gap"


def required_env():
    return []


def install(base_dir: Path) -> str:
    return (
        "No adapter for Codex CLI: its hooks feature is experimental and "
        "explicitly documented as not available on Windows at all - the same "
        "employee-machines-can't-be-assumed-non-Windows reasoning that already "
        "rules out Cline applies here too, even though Codex CLI's hooks (where "
        "they do load) would only ever cover the Bash tool anyway. See "
        "connector/docs/known-gaps.md.\n"
        "Codex CLI can still be pointed at the deployed PABEL MCP server "
        "directly (whoami/read_document become normal callable tools, same as "
        "any MCP client) - ask your admin for the deployed server's connection "
        "details."
    )
