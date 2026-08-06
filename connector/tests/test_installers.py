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


def test_merge_hook_list_upserts_extra_fields_on_rerun():
    # A prior install() wrote the entry without a "windows" override; a
    # later install() (after this package added one, e.g. the PowerShell
    # call-operator fix) must repair it in place, not leave the stale entry
    # untouched just because "command" already matched.
    existing = [{"type": "command", "command": "cmd-a"}]
    existing = base.merge_hook_list(existing, "cmd-a", extra_fields={"windows": "& cmd-a"})
    assert existing == [{"type": "command", "command": "cmd-a", "windows": "& cmd-a",
                          "timeout": base.HOOK_TIMEOUT_SECONDS}]


def test_merge_hook_list_writes_generous_timeout():
    # core/decide.py now blocks inside the hook to run an interactive
    # browser+MFA login on demand rather than just denying - every written
    # hook entry needs a "timeout" comfortably above oauth_browser.py's
    # own 180s callback wait, or the agent's own (often ~60s) default hook
    # timeout kills the subprocess before a human can finish logging in.
    existing = base.merge_hook_list([], "cmd-a")
    assert existing[0]["timeout"] == base.HOOK_TIMEOUT_SECONDS
    assert base.HOOK_TIMEOUT_SECONDS > 180


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
    # .github/hooks/*.json, flat PreToolUse array - confirmed against
    # code.visualstudio.com/docs/agent-customization/hooks after the
    # original .vscode/hooks.json guess turned out to never be read by VS
    # Code at all (see docs/phase2-engineering-notes.md).
    INSTALLERS["vscode"].install(tmp_path)
    data = json.loads((tmp_path / ".github" / "hooks" / "pabel.json").read_text())
    entry = data["hooks"]["PreToolUse"][0]
    assert "pabel_connector.hook vscode" in entry["command"]
    # PowerShell (what VS Code's "command"/"windows" field runs through on
    # Windows) rejects a bare quoted-path-plus-args string without the "&"
    # call operator - confirmed live 2026-08 via a real failing hook
    # invocation. The "windows" override must carry it.
    assert entry["windows"].startswith("& ")
    assert "pabel_connector.hook vscode" in entry["windows"]
    assert entry["timeout"] == base.HOOK_TIMEOUT_SECONDS


def test_vscode_install_is_idempotent(tmp_path):
    INSTALLERS["vscode"].install(tmp_path)
    INSTALLERS["vscode"].install(tmp_path)
    data = json.loads((tmp_path / ".github" / "hooks" / "pabel.json").read_text())
    assert len(data["hooks"]["PreToolUse"]) == 1


def test_vscode_install_registers_mcp_local_server(tmp_path):
    # whoami/read_document/login become directly callable tools, not just
    # something the hook relays reactively - confirmed schema:
    # code.visualstudio.com/docs/agent-customization/mcp-servers.
    INSTALLERS["vscode"].install(tmp_path)
    data = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    server = data["servers"]["pabel-connector"]
    assert server["type"] == "stdio"
    assert server["args"][-2:] == ["pabel_connector.mcp_local_server", "vscode"]


def test_copilot_cli_install_writes_windows_override(tmp_path):
    # Same .github/hooks/*.json execution path as vscode, so it inherits
    # the same PowerShell call-operator requirement on Windows - see
    # installers/copilot_cli.py's docstring.
    INSTALLERS["copilot-cli"].install(tmp_path)
    data = json.loads((tmp_path / ".github" / "hooks" / "pabel-copilot-cli.json").read_text())
    entry = data["hooks"]["preToolUse"][0]
    assert entry["windows"].startswith("& ")


def test_cursor_install_writes_all_three_hook_points(tmp_path):
    INSTALLERS["cursor"].install(tmp_path)
    data = json.loads((tmp_path / ".cursor" / "hooks.json").read_text())
    assert set(data["hooks"]) == {"beforeReadFile", "beforeShellExecution", "beforeMCPExecution"}


def test_windsurf_install_writes_all_four_hook_points(tmp_path):
    INSTALLERS["windsurf"].install(tmp_path)
    data = json.loads((tmp_path / ".windsurf" / "hooks.json").read_text())
    assert set(data["hooks"]) == {"pre_read_code", "pre_write_code", "pre_run_command", "pre_mcp_tool_use"}


# Gemini CLI's install/uninstall tests were removed along with the adapter
# itself - deprecated by the vendor's own successor product ("Antigravity"),
# deliberately not supported either. See docs/phase2-engineering-notes.md.


def _codex_home_toml(tmp_path):
    import tomlkit as _tomlkit
    path = tmp_path / ".codex" / "config.toml"
    return _tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else {}


def test_codex_cli_status_is_mcp_only_not_gap(tmp_path):
    # Codex CLI's hooks feature is still documented as unavailable on
    # Windows at all (see installers/codex_cli.py) - but unlike cline/
    # continue-dev, there IS a real install action: MCP tool registration
    # in the config.toml it shares with the ChatGPT desktop app.
    assert INSTALLERS["codex-cli"].status == "mcp-only"


def test_codex_cli_install_requires_global(tmp_path):
    from pabel_connector.cli.main import main as cli_main
    exit_code = cli_main(["install", "codex-cli", "--dir", str(tmp_path),
                          "--client-id", "x", "--client-secret", "y"])
    assert exit_code == 2
    assert not (tmp_path / ".codex").exists()


def test_codex_cli_install_registers_its_own_mcp_server(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PABEL_SERVER_URL", "http://localhost:8001/mcp")
    INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    data = _codex_home_toml(tmp_path)
    servers = data["mcp_servers"]
    assert servers["pabel-connector-codex-cli"]["args"][-2:] == \
        ["pabel_connector.mcp_local_server", "codex-cli"]
    assert servers["pabel"]["url"] == "http://localhost:8001/mcp"


def test_codex_cli_install_without_server_url_warns_and_skips_pabel_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PABEL_SERVER_URL", raising=False)
    message = INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    assert "PABEL_SERVER_URL is not set" in message
    data = _codex_home_toml(tmp_path)
    assert "pabel" not in data["mcp_servers"]
    assert "pabel-connector-codex-cli" in data["mcp_servers"]


def test_codex_family_shared_file_does_not_collide(tmp_path, monkeypatch):
    # The whole reason for per-agent server names (pabel-connector-codex-cli
    # vs. pabel-connector-chatgpt-desktop) - installing both into the one
    # shared ~/.codex/config.toml must not let the second overwrite the
    # first's entry, in either install order.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PABEL_SERVER_URL", "http://localhost:8001/mcp")
    INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    INSTALLERS["chatgpt-desktop"].install(tmp_path, global_=True)
    data = _codex_home_toml(tmp_path)
    servers = data["mcp_servers"]
    assert "pabel-connector-codex-cli" in servers
    assert "pabel-connector-chatgpt-desktop" in servers
    assert servers["pabel-connector-codex-cli"]["args"][-1] == "codex-cli"
    assert servers["pabel-connector-chatgpt-desktop"]["args"][-1] == "chatgpt-desktop"
    assert servers["pabel"]["url"] == "http://localhost:8001/mcp"


def test_codex_cli_uninstall_removes_only_its_own_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PABEL_SERVER_URL", "http://localhost:8001/mcp")
    INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    INSTALLERS["chatgpt-desktop"].install(tmp_path, global_=True)
    INSTALLERS["codex-cli"].uninstall(tmp_path, global_=True)
    data = _codex_home_toml(tmp_path)
    servers = data["mcp_servers"]
    assert "pabel-connector-codex-cli" not in servers
    assert "pabel-connector-chatgpt-desktop" in servers
    assert "pabel" in servers  # uninstall never touches the shared deployed-server entry


def test_chatgpt_desktop_status_is_mcp_only(tmp_path):
    assert INSTALLERS["chatgpt-desktop"].status == "mcp-only"


def test_chatgpt_desktop_install_requires_global(tmp_path):
    from pabel_connector.cli.main import main as cli_main
    exit_code = cli_main(["install", "chatgpt-desktop", "--dir", str(tmp_path),
                          "--client-id", "x", "--client-secret", "y"])
    assert exit_code == 2
    assert not (tmp_path / ".codex").exists()


def test_claude_code_install_writes_nested_pretooluse_hook(tmp_path):
    # Claude Code gets no privileged installation path - the exact same
    # `pabel-connector install claude-code --dir .` every other agent uses,
    # writing .claude/settings.json directly (nested {"hooks": [...]} shape,
    # confirmed live in this repo's own working config - see
    # installers/claude_code.py's docstring).
    INSTALLERS["claude-code"].install(tmp_path)
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    block = data["hooks"]["PreToolUse"][0]
    entry = block["hooks"][0]
    assert "pabel_connector.hook claude-code" in entry["command"]
    assert entry["timeout"] == base.HOOK_TIMEOUT_SECONDS
    assert "windows" not in entry  # proven unnecessary by this repo's own working config


def test_claude_code_install_is_idempotent(tmp_path):
    INSTALLERS["claude-code"].install(tmp_path)
    INSTALLERS["claude-code"].install(tmp_path)
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert len(data["hooks"]["PreToolUse"]) == 1


def test_claude_code_install_registers_both_mcp_servers(tmp_path):
    INSTALLERS["claude-code"].install(tmp_path)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["pabel"] == {"type": "http", "url": "${PABEL_SERVER_URL}"}
    assert data["mcpServers"]["pabel-connector"]["args"][-2:] == \
        ["pabel_connector.mcp_local_server", "claude-code"]


def test_gap_installers_do_not_crash_and_write_nothing(tmp_path):
    for key in ("cline", "continue-dev"):
        summary = INSTALLERS[key].install(tmp_path)
        assert "known-gaps.md" in summary
        assert list(tmp_path.iterdir()) == []


# --global: only offered where a real vendor doc confirmed a user-level
# location (2026-08) - vscode has none confirmed and must not silently
# guess one, exactly the mistake its workspace path already made once.

def test_global_supported_agents_match_confirmed_docs():
    supported = {key for key, installer in INSTALLERS.items()
                if hasattr(installer, "GLOBAL_CONFIG_RELATIVE_PATH")}
    assert supported == {"claude-code", "cursor", "windsurf", "copilot-cli",
                         "codex-cli", "chatgpt-desktop"}
    assert "vscode" not in supported


def test_global_only_agents_are_exactly_the_codex_family():
    from pabel_connector.cli.main import _global_only
    global_only = {key for key, installer in INSTALLERS.items() if _global_only(installer)}
    assert global_only == {"codex-cli", "chatgpt-desktop"}


def test_claude_code_global_install_writes_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    INSTALLERS["claude-code"].install(tmp_path / "some-project", global_=True)
    assert (tmp_path / ".claude" / "settings.json").exists()


def test_cursor_global_install_writes_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    INSTALLERS["cursor"].install(tmp_path / "some-project", global_=True)
    assert (tmp_path / ".cursor" / "hooks.json").exists()


def test_windsurf_global_install_uses_codeium_path_not_dot_windsurf(tmp_path, monkeypatch):
    # Windsurf's confirmed global path is ~/.codeium/windsurf/hooks.json,
    # NOT ~/.windsurf/hooks.json - a genuinely different relative shape
    # from its workspace path, unlike cursor/claude-code.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    INSTALLERS["windsurf"].install(tmp_path / "some-project", global_=True)
    assert (tmp_path / ".codeium" / "windsurf" / "hooks.json").exists()
    assert not (tmp_path / ".windsurf").exists()


def test_copilot_cli_global_install_writes_to_hooks_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    INSTALLERS["copilot-cli"].install(tmp_path / "some-project", global_=True)
    assert (tmp_path / ".copilot" / "hooks" / "pabel-copilot-cli.json").exists()
