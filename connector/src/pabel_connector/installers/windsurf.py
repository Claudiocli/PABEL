"""Windsurf/Cascade hooks - four pre-hooks in one `.windsurf/hooks.json`
(workspace-level, CONFIRMED - docs.windsurf.com/windsurf/cascade/hooks).

STATUS: DEGRADED, not just UNVERIFIED - a 2026-08 doc re-check confirmed
Windsurf's pre-hooks block *only* via exit code 2 + stderr, with stderr
documented as reaching a human-visible log in the Cascade UI, never the
model's own context. There is no way for this adapter to transparently
relay decrypted content back to the *model* here - a real vendor ceiling
a future live test cannot lift, the same category as codex_cli's
Bash-only coverage. See adapters/windsurf.py for the full finding and the
per-hook input field names this also confirmed
(tool_info.command_line, tool_info.mcp_server_name/mcp_tool_name/
mcp_tool_arguments - all different from what earlier, unconfirmed
versions of that file guessed).
"""

from pathlib import Path

from . import base

name = "windsurf"
status = "degraded"

CONFIG_RELATIVE_PATH = Path(".windsurf") / "hooks.json"
HOOK_POINTS = ("pre_read_code", "pre_write_code", "pre_run_command", "pre_mcp_tool_use")
HOOK_KEYS = [f"windsurf:{point}" for point in HOOK_POINTS]


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return base_dir / CONFIG_RELATIVE_PATH


def install(base_dir: Path) -> str:
    path = config_path(base_dir)
    data = base.read_json(path)
    hooks = data.setdefault("hooks", {})
    for point in HOOK_POINTS:
        hooks[point] = base.merge_hook_list(hooks.get(point), base.hook_command(f"windsurf:{point}"))
    base.write_json(path, data)
    return (
        f"Wrote pre_read_code/pre_write_code/pre_run_command/pre_mcp_tool_use hooks "
        f"to {path}\n(UNVERIFIED whether Windsurf surfaces the relayed "
        f"content back to the model at all - see adapters/windsurf.py)."
    )
