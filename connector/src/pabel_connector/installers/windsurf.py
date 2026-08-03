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

Supports `--global`: confirmed 2026-08 via docs.devin.ai/desktop/cascade/
hooks (the current redirect target of docs.windsurf.com/windsurf/cascade/
hooks) - unlike Cursor/Gemini CLI/Claude Code, the user-level path is NOT
just this same relative path rooted at $HOME; it's `~/.codeium/windsurf/
hooks.json`, documented as applying across all platforms with no Windows-
specific variation. Windsurf also documents a third, system-level tier
(`C:\\ProgramData\\Windsurf\\hooks.json` on Windows) this package has no
reason to write to (an org-wide admin policy, not a per-employee install);
system/user/workspace hooks are documented to merge and each fire
independently, so writing only the user-level file here is enough for
--global coverage.
"""

from pathlib import Path

from . import base

name = "windsurf"
status = "degraded"

CONFIG_RELATIVE_PATH = Path(".windsurf") / "hooks.json"
GLOBAL_CONFIG_RELATIVE_PATH = Path(".codeium") / "windsurf" / "hooks.json"
HOOK_POINTS = ("pre_read_code", "pre_write_code", "pre_run_command", "pre_mcp_tool_use")
HOOK_KEYS = [f"windsurf:{point}" for point in HOOK_POINTS]


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return base_dir / CONFIG_RELATIVE_PATH


def install(base_dir: Path, global_: bool = False) -> str:
    path = base.global_config_path(GLOBAL_CONFIG_RELATIVE_PATH) if global_ else config_path(base_dir)
    data = base.read_json(path)
    base.install_multi_point_hooks(data, "windsurf", HOOK_POINTS)
    base.write_json(path, data)
    scope = " (global - applies to every project)" if global_ else ""
    return (
        f"Wrote pre_read_code/pre_write_code/pre_run_command/pre_mcp_tool_use hooks "
        f"to {path}{scope}\n(UNVERIFIED whether Windsurf surfaces the relayed "
        f"content back to the model at all - see adapters/windsurf.py)."
    )
