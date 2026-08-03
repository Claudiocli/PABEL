"""Gemini CLI's `BeforeTool` hook - config in `.gemini/settings.json`
(project-level; confirmed location and shape per
connector/docs/coverage-matrix.md). `matcher: "*"` is documented as
matching every tool, giving the same catch-all coverage Claude Code's
no-matcher hook does.

STATUS: UNVERIFIED beyond the confirmed config shape/location - whether
folding the relay's content into `reason` (see adapters/gemini_cli.py)
actually surfaces it usefully to the model still needs a live check.
"""

from pathlib import Path

from . import base

name = "gemini-cli"
status = "unverified"

CONFIG_RELATIVE_PATH = Path(".gemini") / "settings.json"
HOOK_KEYS = ["gemini-cli"]


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return base_dir / CONFIG_RELATIVE_PATH


def install(base_dir: Path) -> str:
    path = config_path(base_dir)
    data = base.read_json(path)
    hooks = data.setdefault("hooks", {})
    before_tool = hooks.setdefault("BeforeTool", [])
    command = base.hook_command("gemini-cli")

    for entry in before_tool:
        if entry.get("matcher") == "*":
            entry["hooks"] = base.merge_hook_list(entry.get("hooks"), command,
                                                    extra_fields={"name": "pabel"})
            break
    else:
        before_tool.append({
            "matcher": "*",
            "hooks": [{"name": "pabel", "type": "command", "command": command,
                       "timeout": base.HOOK_TIMEOUT_SECONDS}],
        })

    base.write_json(path, data)
    return f"Wrote a catch-all BeforeTool hook to {path}."
