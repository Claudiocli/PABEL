"""Windsurf/Cascade hooks - four pre-hooks in one `.windsurf/hooks.json`
(workspace-level; Windsurf is documented as only loading workspace-level
hooks from this exact path, per connector/docs/coverage-matrix.md).

STATUS: UNVERIFIED - the config *location* is reasonably well documented,
but the exact per-hook input schema and, critically, whether Windsurf
delivers the relay's decrypted content (folded into stderr by
adapters/windsurf.py) back into the model's own context at all, versus
only into a human-visible log, is NOT confirmed. This is the least-trusted
adapter in this package - verify first.
"""

from pathlib import Path

from . import base

name = "windsurf"
status = "unverified"

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
