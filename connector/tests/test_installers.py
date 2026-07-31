import json
from pathlib import Path

from pabel_connector.installers import base
from pabel_connector.installers.registry import INSTALLERS


def test_merge_hook_list_is_idempotent():
    existing = []
    existing = base.merge_hook_list(existing, "cmd-a")
    existing = base.merge_hook_list(existing, "cmd-a")
    assert len(existing) == 1


def test_merge_hook_list_preserves_other_entries():
    existing = [{"type": "command", "command": "someone-elses-hook"}]
    existing = base.merge_hook_list(existing, "cmd-a")
    commands = {e["command"] for e in existing}
    assert commands == {"someone-elses-hook", "cmd-a"}


def test_remove_matching_commands_only_removes_named_ones():
    data = {"hooks": {"BeforeTool": [
        {"matcher": "write_file", "hooks": [{"command": "secret-scanner"}]},
        {"matcher": "*", "hooks": [{"command": "pabel-hook"}]},
    ]}}
    removed = base.remove_matching_commands(data, {"pabel-hook"})
    assert removed is True
    assert data["hooks"]["BeforeTool"][0]["hooks"] == [{"command": "secret-scanner"}]
    assert data["hooks"]["BeforeTool"][1]["hooks"] == []


def test_vscode_install_writes_catchall_pretooluse_hook(tmp_path):
    INSTALLERS["vscode"].install(tmp_path)
    data = json.loads((tmp_path / ".vscode" / "hooks.json").read_text())
    command = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "pabel_connector.hook vscode" in command


def test_vscode_install_is_idempotent(tmp_path):
    INSTALLERS["vscode"].install(tmp_path)
    INSTALLERS["vscode"].install(tmp_path)
    data = json.loads((tmp_path / ".vscode" / "hooks.json").read_text())
    assert len(data["hooks"]["PreToolUse"][0]["hooks"]) == 1


def test_cursor_install_writes_all_three_hook_points(tmp_path):
    INSTALLERS["cursor"].install(tmp_path)
    data = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
    assert set(data["hooks"]) == {"beforeReadFile", "beforeShellExecution", "beforeMCPExecution"}


def test_windsurf_install_writes_all_four_hook_points(tmp_path):
    INSTALLERS["windsurf"].install(tmp_path)
    data = json.loads((tmp_path / ".windsurf" / "hooks.json").read_text())
    assert set(data["hooks"]) == {"pre_read_code", "pre_write_code", "pre_run_command", "pre_mcp_tool_use"}


def test_gemini_cli_install_preserves_unrelated_hooks(tmp_path):
    config_path = tmp_path / ".gemini" / "settings.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({
        "hooks": {"BeforeTool": [{"matcher": "write_file", "hooks": [{"command": "secret-scanner"}]}]},
        "someOtherTeamSetting": True,
    }))
    INSTALLERS["gemini-cli"].install(tmp_path)
    data = json.loads(config_path.read_text())
    assert data["someOtherTeamSetting"] is True
    matchers = {e["matcher"] for e in data["hooks"]["BeforeTool"]}
    assert matchers == {"write_file", "*"}


def test_uninstall_removes_only_pabel_hooks(tmp_path):
    INSTALLERS["gemini-cli"].install(tmp_path)
    config_path = tmp_path / ".gemini" / "settings.json"
    data = json.loads(config_path.read_text())
    data["hooks"]["BeforeTool"].append({"matcher": "write_file", "hooks": [{"command": "secret-scanner"}]})
    config_path.write_text(json.dumps(data))

    path = INSTALLERS["gemini-cli"].config_path(tmp_path)
    current = base.read_json(path)
    commands = {base.hook_command(k) for k in INSTALLERS["gemini-cli"].HOOK_KEYS}
    removed = base.remove_matching_commands(current, commands)
    base.write_json(path, current)

    assert removed is True
    final = json.loads(config_path.read_text())
    all_commands = [h["command"] for entry in final["hooks"]["BeforeTool"] for h in entry.get("hooks", [])]
    assert "secret-scanner" in all_commands
    assert not any("pabel_connector.hook" in c for c in all_commands)


def test_codex_cli_install_sets_feature_flag_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    codex_cli = INSTALLERS["codex-cli"]

    first = codex_cli.install(tmp_path)
    assert "enabled" in first
    toml_text = (tmp_path / ".codex" / "config.toml").read_text()
    assert "hooks = true" in toml_text

    second = codex_cli.install(tmp_path)
    assert "already enabled" in second
    # No duplicate [features] section from a second install.
    assert (tmp_path / ".codex" / "config.toml").read_text().count("[features]") == 1


def test_claude_code_installer_prints_plugin_instructions_and_writes_nothing(tmp_path):
    summary = INSTALLERS["claude-code"].install(tmp_path)
    assert "/plugin install pabel" in summary
    assert list(tmp_path.iterdir()) == []


def test_gap_installers_do_not_crash_and_write_nothing(tmp_path):
    for key in ("cline", "continue-dev"):
        summary = INSTALLERS[key].install(tmp_path)
        assert "known-gaps.md" in summary
        assert list(tmp_path.iterdir()) == []
