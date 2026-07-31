"""GitHub Copilot CLI's `preToolUse` hook.

STATUS: UNVERIFIED. Config file location wasn't pinned down precisely by
public docs found this session - `~/.copilot/hooks.json` (user-level, this
is a global CLI tool rather than a per-workspace one) is a best guess.
Verify against a real Copilot CLI install before relying on this.
"""

from pathlib import Path

from . import base

name = "copilot-cli"
status = "unverified"
HOOK_KEYS = ["copilot-cli"]


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return Path.home() / ".copilot" / "hooks.json"


def install(base_dir: Path) -> str:
    path = config_path(base_dir)
    data = base.read_json(path)
    hooks = data.setdefault("hooks", {})
    pre_tool_use = hooks.setdefault("preToolUse", [])
    hooks["preToolUse"] = base.merge_hook_list(pre_tool_use, base.hook_command("copilot-cli"))
    base.write_json(path, data)
    return (
        f"Wrote a preToolUse hook to {path}\n"
        f"(UNVERIFIED path/schema - confirm against a real Copilot CLI install).\n"
        f"Known vendor limitation: additionalContext delivery for preToolUse is "
        f"currently unreliable (github/copilot-cli#2585) - the relay's decrypted "
        f"content is folded into permissionDecisionReason as a robust fallback."
    )
