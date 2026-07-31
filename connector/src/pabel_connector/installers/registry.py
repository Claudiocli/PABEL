"""Installer registry: one entry per agent (not per hook point - unlike
adapters/registry.py, an agent with several hook points still has exactly
one install() that writes all of them at once, e.g. cursor.install()
writes all three of Cursor's hooks into its one hooks.json)."""

from . import (
    claude_code,
    cline,
    codex_cli,
    continue_dev,
    copilot_cli,
    cursor,
    gemini_cli,
    vscode,
    windsurf,
)

INSTALLERS = {
    "claude-code": claude_code,
    "vscode": vscode,
    "copilot-cli": copilot_cli,
    "cursor": cursor,
    "windsurf": windsurf,
    "gemini-cli": gemini_cli,
    "codex-cli": codex_cli,
    "cline": cline,
    "continue-dev": continue_dev,
}
