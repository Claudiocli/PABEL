"""VS Code's native agent hooks (Preview).

STATUS: UNVERIFIED. The exact config file path/name for this feature was
not confirmed by public docs found this session (code.visualstudio.com's
own reference page describes the schema, not a canonical file location) -
`.vscode/hooks.json` (workspace-level) is a best guess following the same
`.{agent}/hooks.json` convention several other tools in this package use,
NOT a confirmed path. Verify against a real VS Code install (with a paid
Copilot subscription, per connector/docs/coverage-matrix.md) before
relying on this.
"""

from pathlib import Path

from . import base

name = "vscode"
status = "unverified"

CONFIG_RELATIVE_PATH = Path(".vscode") / "hooks.json"
HOOK_KEYS = ["vscode"]


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return base_dir / CONFIG_RELATIVE_PATH


def install(base_dir: Path) -> str:
    path = config_path(base_dir)
    data = base.read_json(path)
    hooks = data.setdefault("hooks", {})
    pre_tool_use = hooks.setdefault("PreToolUse", [{}])
    entry = pre_tool_use[0] if pre_tool_use and isinstance(pre_tool_use[0], dict) else {}
    if not pre_tool_use:
        pre_tool_use.append(entry)
    entry["hooks"] = base.merge_hook_list(entry.get("hooks"), base.hook_command("vscode"))
    pre_tool_use[0] = entry
    base.write_json(path, data)
    return (
        f"Wrote a catch-all PreToolUse hook to {path}\n"
        f"(UNVERIFIED path/schema - confirm against a real VS Code install)."
    )
