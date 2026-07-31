"""OpenAI Codex CLI's `PreToolUse` hook.

STATUS: DEGRADED, UNVERIFIED. Two install-time specifics on top of the
usual hook-config write, both real vendor requirements:
  1. Hooks are opt-in - `~/.codex/config.toml` needs `[features]
     hooks = true` (or the newer `hooks = true` key, per Codex's own
     deprecation notes) or the hook engine silently no-ops. Patched here
     with a plain text append rather than a TOML library dependency, since
     the only thing being added is one flag under one section.
  2. `PreToolUse` only fires for the Bash tool - Read/Write/Edit/MCP calls
     are not interceptable at all today (see adapters/codex_cli.py and
     connector/docs/known-gaps.md). This installer still wires up Bash
     coverage, which is genuinely better than nothing, but must never be
     represented as equivalent to the other adapters.

The hook-config file location/shape for Codex CLI itself (as opposed to
the config.toml feature flag) was not pinned down precisely by public
docs found this session - `~/.codex/hooks.json`, in a Claude-Code-like
shape, is a best guess pending a real install to check against.

Paths are resolved lazily (functions, not module-level constants) so
`Path.home()` is read at call time - this matters for testability
(monkeypatching `Path.home` works without needing a module reload).
"""

from pathlib import Path

from . import base

name = "codex-cli"
status = "degraded"

HOOK_KEYS = ["codex-cli"]


def required_env():
    return []


def _config_toml_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _hooks_json_path() -> Path:
    return Path.home() / ".codex" / "hooks.json"


def config_path(base_dir: Path) -> Path:
    return _hooks_json_path()


def _ensure_feature_flag() -> str:
    toml_path = _config_toml_path()
    toml_path.parent.mkdir(parents=True, exist_ok=True)
    text = toml_path.read_text(encoding="utf-8") if toml_path.exists() else ""
    if "hooks = true" in text or "codex_hooks = true" in text:
        return "already enabled"
    if "[features]" in text:
        text = text.replace("[features]", "[features]\nhooks = true", 1)
    else:
        text = text.rstrip("\n") + "\n\n[features]\nhooks = true\n"
    toml_path.write_text(text, encoding="utf-8")
    return "enabled"


def install(base_dir: Path) -> str:
    flag_status = _ensure_feature_flag()
    hooks_json_path = _hooks_json_path()
    data = base.read_json(hooks_json_path)
    hooks = data.setdefault("hooks", {})
    pre_tool_use = hooks.setdefault("PreToolUse", [{}])
    entry = pre_tool_use[0] if pre_tool_use and isinstance(pre_tool_use[0], dict) else {}
    if not pre_tool_use:
        pre_tool_use.append(entry)
    entry["hooks"] = base.merge_hook_list(entry.get("hooks"), base.hook_command("codex-cli"))
    pre_tool_use[0] = entry
    base.write_json(hooks_json_path, data)
    return (
        f"[features] hooks = true {flag_status} in {_config_toml_path()}\n"
        f"Wrote a PreToolUse (Bash-only) hook to {hooks_json_path}\n"
        f"DEGRADED: only Bash-mediated .abe access is interceptable today - "
        f"see connector/docs/known-gaps.md."
    )
