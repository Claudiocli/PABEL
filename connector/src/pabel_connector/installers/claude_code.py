"""Claude Code already has a fully-built, tested, VERIFIED plugin
(claude-plugin/pabel/) using Claude Code's own marketplace/plugin
distribution mechanism (see docs/phase2-engineering-notes.md sec 9-10) -
writing a hooks.json by hand here would just be a second, parallel way to
reach the same result, with none of the plugin packaging benefits
(versioning, `/plugin install`, bundled README). This installer therefore
writes no config; it points at the existing plugin's own install steps.
"""

from pathlib import Path

name = "claude-code"
status = "verified"


def required_env():
    return []


def install(base_dir: Path) -> str:
    return (
        "Claude Code already has a dedicated, tested plugin - install that "
        "instead of writing hooks by hand:\n"
        "  /plugin marketplace add <path-or-git-url-to-claude-plugin>\n"
        "  /plugin install pabel@pabel-marketplace\n"
        "See claude-plugin/pabel/README.md for full configuration."
    )
