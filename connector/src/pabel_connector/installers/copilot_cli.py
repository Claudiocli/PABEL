"""GitHub Copilot CLI's `preToolUse` hook.

STATUS: config path CONFIRMED (2026-08 doc re-check, prompted by
installers/vscode.py's own path guess turning out wrong - every "built to
spec" installer in this package got re-checked against current official
docs rather than trusting the original research unverified); still
UNVERIFIED end-to-end against a real Copilot CLI install.

The original version of this installer wrote a single `~/.copilot/
hooks.json` file (user-level, global to the whole machine). Confirmed docs
(docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/
use-hooks) show that's not how Copilot CLI actually loads hooks: user-level
hooks come from `*.json` files inside a `~/.copilot/hooks/` *directory*
(`%USERPROFILE%\\.copilot\\hooks\\` on Windows) - a single file directly
under `~/.copilot/` was never read, same class of bug as vscode's wrong
path (a location nothing looks at, not a schema mismatch it tolerates).
Confirmed docs also show a project-scoped alternative this package's own
`--dir`-based install convention fits better: `.github/hooks/*.json`
(repository-level - the same convention VS Code uses, which is documented
to auto-convert this exact lowerCamelCase shape). Switched to that, with
this product's own filename so it never collides with vscode's
`.github/hooks/pabel.json` if both are installed into the same project.

Because this is the same `.github/hooks/*.json` convention and command
execution path VS Code uses, it also inherits the same Windows bug found
live 2026-08 in `installers/vscode.py`: `command` is invalid PowerShell
syntax without the `&` call operator. Fixed the same way - a `windows`
override field via `base.hook_command_windows()`. Unconfirmed whether
Copilot CLI's own execution engine actually reads `windows` the same way
VS Code's does (no independent source found naming it for this product
specifically), but writing it is harmless either way and matches the
documented "auto-converts VS Code's shape" behavior.
"""

from pathlib import Path

from . import base

name = "copilot-cli"
status = "unverified"
HOOK_KEYS = ["copilot-cli"]

CONFIG_RELATIVE_PATH = Path(".github") / "hooks" / "pabel-copilot-cli.json"


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return base_dir / CONFIG_RELATIVE_PATH


def install(base_dir: Path) -> str:
    path = config_path(base_dir)
    data = base.read_json(path)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    pre_tool_use = hooks.setdefault("preToolUse", [])
    hooks["preToolUse"] = base.merge_hook_list(
        pre_tool_use,
        base.hook_command("copilot-cli"),
        extra_fields={"windows": base.hook_command_windows("copilot-cli")},
    )
    base.write_json(path, data)
    return (
        f"Wrote a preToolUse hook to {path}\n"
        f"(path confirmed against docs.github.com's Copilot CLI hooks guide; "
        f"whether this actually fires as expected in a live session is still unverified).\n"
        f"Known vendor limitation: additionalContext delivery for preToolUse is "
        f"currently unreliable (github/copilot-cli#2585) - the relay's decrypted "
        f"content is folded into permissionDecisionReason as a robust fallback."
    )
