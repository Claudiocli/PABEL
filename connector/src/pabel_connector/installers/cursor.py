"""Cursor's hooks (v1.7+, beta) - three separate hook points registered in
one `hooks.json`, confirmed format:
```
{"version": 1, "hooks": {"beforeReadFile": [...], "beforeShellExecution": [...], "beforeMCPExecution": [...]}}
```
at project (`<project>/hooks.json`... actually `.cursor/hooks.json`) or
user level (`~/.cursor/hooks.json`). This installer writes the
project-level file so it travels with the repo/workspace, matching this
package's other project-scoped installers.

STATUS: UNVERIFIED beyond the config *shape and location*, which the
docs do confirm - whether the three hook keys used here exactly match a
live Cursor install's expectations for content and matching is still to
be checked (see connector/docs/coverage-matrix.md and adapters/cursor.py).
"""

from pathlib import Path

from . import base

name = "cursor"
status = "unverified"

CONFIG_RELATIVE_PATH = Path(".cursor") / "hooks.json"
HOOK_POINTS = ("beforeReadFile", "beforeShellExecution", "beforeMCPExecution")
HOOK_KEYS = [f"cursor:{point}" for point in HOOK_POINTS]


def required_env():
    return []


def config_path(base_dir: Path) -> Path:
    return base_dir / CONFIG_RELATIVE_PATH


def install(base_dir: Path) -> str:
    path = config_path(base_dir)
    data = base.read_json(path)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    for point in HOOK_POINTS:
        hooks[point] = base.merge_hook_list(hooks.get(point), base.hook_command(f"cursor:{point}"))
    base.write_json(path, data)
    return (
        f"Wrote beforeReadFile/beforeShellExecution/beforeMCPExecution hooks to "
        f"{path}\n(config shape/location confirmed by Cursor's own docs; "
        f"exact payload handling is UNVERIFIED - see adapters/cursor.py)."
    )
