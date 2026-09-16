import json
from pathlib import Path

import pytest

from pabel_connector.installers import base
from pabel_connector.installers.registry import INSTALLERS


@pytest.fixture(autouse=True)
def _clean_pabel_env(monkeypatch):
    # installers/codex_family.py reads base.SHARED_ENV_VARS from os.environ
    # at install time - without this, tests here would silently pick up
    # whatever real PABEL_* values happen to be set persistently on the
    # machine actually running the suite (confirmed to happen in real use -
    # see docs/phase2-engineering-notes.md), making pass/fail depend on the
    # runner's own machine instead of what each test explicitly sets.
    for var in base.SHARED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


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


@pytest.mark.parametrize("agent", ["codex-cli", "chatgpt-desktop"])
def test_codex_family_status_is_mcp_only_not_gap(agent):
    # Unlike cline/continue-dev there IS a real install action for these two:
    # MCP tool registration in the config.toml they share.
    assert INSTALLERS[agent].status == "mcp-only"


def test_codex_cli_install_registers_its_own_mcp_server(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PABEL_SERVER_URL", "http://localhost:8001/mcp")
    INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    data = _codex_home_toml(tmp_path)
    servers = data["mcp_servers"]
    assert servers["pabel-connector-codex-cli"]["args"][-2:] == \
        ["pabel_connector.mcp_local_server", "codex-cli"]
    assert servers["pabel"]["url"] == "http://localhost:8001/mcp"


def test_codex_cli_install_captures_all_shared_env_vars_into_own_env_block(tmp_path, monkeypatch):
    # relay.py/keycloak_client.py read these from os.environ *inside the
    # mcp_local_server.py subprocess Codex CLI spawns* - captured here so
    # nothing needs to be set in the user's own shell or the OS's
    # persistent environment for the tools to actually work (found live
    # 2026-08 - see docs/phase2-engineering-notes.md).
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PABEL_KEYCLOAK_URL", "http://localhost:8080")
    monkeypatch.setenv("PABEL_KEYCLOAK_REALM", "pabel")
    monkeypatch.setenv("PABEL_KEYCLOAK_CLIENT_ID", "pabel")
    monkeypatch.setenv("PABEL_SERVER_URL", "http://localhost:8001/mcp")
    INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    env = _codex_home_toml(tmp_path)["mcp_servers"]["pabel-connector-codex-cli"]["env"]
    assert dict(env) == {
        "PABEL_KEYCLOAK_URL": "http://localhost:8080",
        "PABEL_KEYCLOAK_REALM": "pabel",
        "PABEL_KEYCLOAK_CLIENT_ID": "pabel",
        "PABEL_SERVER_URL": "http://localhost:8001/mcp",
    }


def test_codex_cli_install_without_server_url_warns_and_skips_pabel_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    message = INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    assert "Not set in this shell, so not captured" in message
    assert "PABEL_SERVER_URL" in message
    data = _codex_home_toml(tmp_path)
    assert "pabel" not in data["mcp_servers"]
    assert "pabel-connector-codex-cli" in data["mcp_servers"]
    assert "env" not in data["mcp_servers"]["pabel-connector-codex-cli"]


def test_codex_cli_install_captures_only_the_env_vars_that_are_set(tmp_path, monkeypatch):
    # Partial capture: some set, some not - the ones present still get
    # written, missing ones are named in the warning, not a hard failure.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PABEL_KEYCLOAK_URL", "http://localhost:8080")
    message = INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    assert "PABEL_KEYCLOAK_REALM" in message
    assert "PABEL_KEYCLOAK_CLIENT_ID" in message
    assert "PABEL_SERVER_URL" in message
    env = _codex_home_toml(tmp_path)["mcp_servers"]["pabel-connector-codex-cli"]["env"]
    assert dict(env) == {"PABEL_KEYCLOAK_URL": "http://localhost:8080"}


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


def _codex_skill_path(tmp_path):
    return tmp_path / ".agents" / "skills" / "pabel" / "SKILL.md"


def test_codex_cli_install_writes_the_shared_skill_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    text = _codex_skill_path(tmp_path).read_text()
    assert "name: pabel" in text
    # Content must differ from Claude Code's: no hook exists here, so the
    # skill has to tell the model to call read_document itself.
    assert "call `read_document` yourself" in text


def test_chatgpt_desktop_install_writes_the_identical_shared_skill(tmp_path, monkeypatch):
    # One skill for this package, not one per product - installing either
    # product writes the exact same file to the exact same shared location.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PABEL_SERVER_URL", "http://localhost:8001/mcp")
    INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    after_codex_cli = _codex_skill_path(tmp_path).read_text()
    INSTALLERS["chatgpt-desktop"].install(tmp_path, global_=True)
    after_chatgpt_desktop = _codex_skill_path(tmp_path).read_text()
    assert after_codex_cli == after_chatgpt_desktop


def test_codex_family_uninstall_does_not_remove_the_shared_skill_file(tmp_path, monkeypatch):
    # The skill is shared by both products (unlike each one's own MCP server
    # entry) - uninstalling just one must not pull it out from under the
    # other still-installed product.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    INSTALLERS["codex-cli"].install(tmp_path, global_=True)
    INSTALLERS["chatgpt-desktop"].install(tmp_path, global_=True)
    INSTALLERS["codex-cli"].uninstall(tmp_path, global_=True)
    assert _codex_skill_path(tmp_path).exists()


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


def test_claude_code_install_writes_session_end_hook(tmp_path):
    # A real, distinct Claude Code event (fires once per session, not once
    # per turn like Stop) - purges anything materialize_document left
    # behind this session. See pabel_client/materialize.py.
    INSTALLERS["claude-code"].install(tmp_path)
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    block = data["hooks"]["SessionEnd"][0]
    entry = block["hooks"][0]
    assert "pabel_connector.hook claude-code:session-end" in entry["command"]
    assert entry["timeout"] == base.HOOK_TIMEOUT_SECONDS


def test_claude_code_install_is_idempotent_for_session_end_too(tmp_path):
    INSTALLERS["claude-code"].install(tmp_path)
    INSTALLERS["claude-code"].install(tmp_path)
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert len(data["hooks"]["SessionEnd"]) == 1


def test_claude_code_install_writes_the_informational_skill(tmp_path):
    INSTALLERS["claude-code"].install(tmp_path)
    skill_path = tmp_path / ".claude" / "skills" / "pabel" / "SKILL.md"
    assert skill_path.exists()
    text = skill_path.read_text()
    # Never mistakable for a security control - see docs/phase2-engineering-notes.md 19.2.
    assert "security_relevant: false" in text
    assert "grants and\nenforces nothing itself" in text


def test_claude_code_uninstall_also_removes_the_skill_file(tmp_path):
    from pabel_connector.cli.main import main as cli_main

    INSTALLERS["claude-code"].install(tmp_path)
    skill_path = tmp_path / ".claude" / "skills" / "pabel" / "SKILL.md"
    assert skill_path.exists()
    exit_code = cli_main(["uninstall", "claude-code", "--dir", str(tmp_path)])
    assert exit_code == 0
    assert not skill_path.exists()


def test_claude_code_global_install_writes_skill_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    INSTALLERS["claude-code"].install(tmp_path / "some-project", global_=True)
    assert (tmp_path / ".claude" / "skills" / "pabel" / "SKILL.md").exists()


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
                         "codex-cli", "chatgpt-desktop", "opencode"}
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


# --- opencode -------------------------------------------------------------
# The only installer that writes executable code (a .js bridge) rather than
# a command string in a config - see installers/opencode.py's docstring.

def test_opencode_install_writes_the_bridge_plugin_and_mcp_entry(tmp_path):
    opencode = INSTALLERS["opencode"]
    opencode.install(tmp_path)
    plugin = tmp_path / ".opencode" / "plugins" / "pabel.js"
    assert plugin.exists()
    config = json.loads((tmp_path / "opencode.json").read_text())
    assert config["mcp"]["pabel-connector"]["type"] == "local"
    assert config["mcp"]["pabel-connector"]["enabled"] is True


def test_opencode_install_substitutes_every_template_token(tmp_path):
    """A leftover __PABEL_*__ token would be a syntax error in the shipped
    plugin - opencode would fail to load it and enforcement would silently
    not exist."""
    INSTALLERS["opencode"].install(tmp_path)
    text = (tmp_path / ".opencode" / "plugins" / "pabel.js").read_text()
    assert "__PABEL_" not in text
    assert "pabel_connector.hook" in text
    assert str(base.HOOK_TIMEOUT_SECONDS * 1000) in text


def test_opencode_install_uses_plural_plugins_directory_not_singular(tmp_path):
    # opencode accepts both, but plural is the current form - writing the
    # singular one would be the installers/vscode.py mistake all over again.
    INSTALLERS["opencode"].install(tmp_path)
    assert (tmp_path / ".opencode" / "plugins").is_dir()
    assert not (tmp_path / ".opencode" / "plugin").exists()


def test_opencode_global_install_uses_dot_config_opencode(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    INSTALLERS["opencode"].install(tmp_path / "some-project", global_=True)
    assert (tmp_path / ".config" / "opencode" / "plugins" / "pabel.js").exists()
    assert (tmp_path / ".config" / "opencode" / "opencode.json").exists()


def test_opencode_install_preserves_unrelated_config(tmp_path):
    config = tmp_path / "opencode.json"
    config.write_text(json.dumps({
        "model": "anthropic/claude-opus-5",
        "mcp": {"someone-elses": {"type": "local", "command": ["x"]}},
    }))
    INSTALLERS["opencode"].install(tmp_path)
    data = json.loads(config.read_text())
    assert data["model"] == "anthropic/claude-opus-5"
    assert "someone-elses" in data["mcp"]
    assert "pabel-connector" in data["mcp"]


def test_opencode_install_writes_into_an_existing_jsonc_not_a_new_json(tmp_path):
    """opencode reads both opencode.json and opencode.jsonc and its docs
    don't say which wins when both exist - so writing a second file next to
    one the user already has could land the registration somewhere opencode
    never looks. Found 2026-09-15 on this project's own dev machine, which
    already had a `.jsonc`."""
    existing = tmp_path / "opencode.jsonc"
    existing.write_text('{"$schema": "https://opencode.ai/config.json"}')
    INSTALLERS["opencode"].install(tmp_path)
    assert not (tmp_path / "opencode.json").exists()
    data = json.loads(existing.read_text())
    assert "pabel-connector" in data["mcp"]
    assert data["$schema"] == "https://opencode.ai/config.json"


def test_opencode_install_refuses_to_destroy_comments_in_a_jsonc(tmp_path):
    """json.dumps can't round-trip comments, so a commented config is left
    strictly untouched and the entry is printed to paste instead."""
    existing = tmp_path / "opencode.jsonc"
    original = '{\n  // my notes\n  "$schema": "https://opencode.ai/config.json"\n}'
    existing.write_text(original)
    message = INSTALLERS["opencode"].install(tmp_path)
    assert existing.read_text() == original
    assert "contains comments" in message
    assert "pabel-connector" in message
    # The plugin - the part that actually enforces - is written regardless.
    assert (tmp_path / ".opencode" / "plugins" / "pabel.js").exists()


def test_opencode_uninstall_removes_every_artifact_it_created(tmp_path, monkeypatch):
    """Found while moving a real install from project to machine-wide scope:
    uninstall removed "pabel-connector" but left the deployed "pabel" entry
    and an empty .opencode/plugins/ behind, in a file it had just reported as
    cleaned."""
    monkeypatch.setenv("PABEL_SERVER_URL", "http://localhost:8001/mcp")
    opencode = INSTALLERS["opencode"]
    opencode.install(tmp_path)
    assert set(json.loads((tmp_path / "opencode.json").read_text())["mcp"]) == {
        "pabel-connector", "pabel"}

    opencode.uninstall(tmp_path)
    assert not (tmp_path / "opencode.json").exists()
    assert not (tmp_path / ".opencode").exists()


def test_opencode_uninstall_keeps_a_config_that_holds_anything_else(tmp_path, monkeypatch):
    monkeypatch.setenv("PABEL_SERVER_URL", "http://localhost:8001/mcp")
    config = tmp_path / "opencode.json"
    config.write_text(json.dumps({"model": "anthropic/claude-opus-5",
                                  "mcp": {"someone-elses": {"type": "local"}}}))
    INSTALLERS["opencode"].install(tmp_path)
    INSTALLERS["opencode"].uninstall(tmp_path)
    data = json.loads(config.read_text())
    assert data["model"] == "anthropic/claude-opus-5"
    assert set(data["mcp"]) == {"someone-elses"}


def test_opencode_uninstall_is_clean_when_nothing_installed(tmp_path):
    assert "nothing to do" in INSTALLERS["opencode"].uninstall(tmp_path)


def test_opencode_hook_wiring_problem_flags_a_missing_plugin(tmp_path):
    problem = INSTALLERS["opencode"].hook_wiring_problem(tmp_path)
    assert problem is not None and "nothing will actually enforce" in problem


def test_opencode_hook_wiring_problem_is_none_after_install(tmp_path):
    opencode = INSTALLERS["opencode"]
    opencode.install(tmp_path)
    assert opencode.hook_wiring_problem(tmp_path) is None


def test_opencode_hook_wiring_problem_flags_a_plugin_from_another_interpreter(tmp_path):
    opencode = INSTALLERS["opencode"]
    opencode.install(tmp_path)
    plugin = tmp_path / ".opencode" / "plugins" / "pabel.js"
    plugin.write_text(plugin.read_text().replace(
        json.dumps(opencode.hook_argv()), '["/some/other/python","-m","x","opencode"]'))
    problem = opencode.hook_wiring_problem(tmp_path)
    assert problem is not None and "different interpreter" in problem
