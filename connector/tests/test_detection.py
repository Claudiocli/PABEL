from pabel_connector.core.detection import (
    find_relayable_file,
    invokes_oabe_binary,
    invokes_pabel_connector_internals,
    is_pabel_hook_config_target,
    mentions_target,
    touches_pabel_credential_store,
)
from pabel_connector.pabel_client import agent_session, session


def test_mentions_target_matches_abe_file():
    assert mentions_target({"file_path": "C:/docs/secret.abe"})


def test_mentions_target_matches_documents_folder():
    assert mentions_target({"pattern": "*.abe", "path": "/repo/documents"})


def test_mentions_target_ignores_unrelated_input():
    assert not mentions_target({"file_path": "/repo/README.md"})


def test_invokes_oabe_binary():
    assert invokes_oabe_binary({"command": "oabe_dec -k k.key -i c.abe"})
    assert not invokes_oabe_binary({"command": "cat notes.txt"})


def test_find_relayable_file_returns_none_for_directory_pattern(tmp_path):
    docs = tmp_path / "documents"
    docs.mkdir()
    assert find_relayable_file({"pattern": "*.abe", "path": str(docs)}) is None


def test_find_relayable_file_finds_a_real_file(tmp_path):
    target = tmp_path / "test.abe"
    target.write_text("ciphertext")
    found = find_relayable_file({"file_path": str(target)})
    assert found == target


def test_find_relayable_file_handles_spaced_paths(tmp_path):
    spaced_dir = tmp_path / "NTT DATA EMEAL"
    spaced_dir.mkdir()
    target = spaced_dir / "test.abe"
    target.write_text("ciphertext")
    # Whole-string field (Read/Edit's file_path) - the original bug this
    # project hit: a naive regex-substring approach breaks on the space.
    found = find_relayable_file({"file_path": str(target)})
    assert found == target


def test_find_relayable_file_handles_quoted_spaced_paths_in_free_text(tmp_path):
    spaced_dir = tmp_path / "NTT DATA EMEAL"
    spaced_dir.mkdir()
    target = spaced_dir / "test.abe"
    target.write_text("ciphertext")
    command = f'cat "{target}"'
    found = find_relayable_file({"command": command})
    assert found == target


def test_find_relayable_file_none_when_file_does_not_exist():
    assert find_relayable_file({"file_path": "Z:/nonexistent/ghost.abe"}) is None


def test_touches_pabel_credential_store_matches_session_file():
    assert touches_pabel_credential_store({"file_path": str(session.SESSION_FILE)})


def test_touches_pabel_credential_store_matches_agent_credentials_file():
    assert touches_pabel_credential_store(
        {"command": f'cat "{agent_session.CREDENTIALS_FILE}"'})


def test_touches_pabel_credential_store_ignores_unrelated_input():
    assert not touches_pabel_credential_store({"file_path": "/repo/README.md"})


def test_invokes_pabel_connector_internals_blocked_outside_source_checkout(
        tmp_path, monkeypatch):
    """The exact bypass found live: a Bash one-liner importing
    pabel_connector's own modules directly, in a normal downstream project
    that has no connector/src/pabel_connector of its own."""
    monkeypatch.chdir(tmp_path)
    command = ("python -c \"from pabel_connector.pabel_client import agent_session; "
              "print(agent_session.access_token('claude-code'))\"")
    assert invokes_pabel_connector_internals({"command": command})


def test_invokes_pabel_connector_internals_allowed_inside_source_checkout():
    # Running from this repo's own root (pytest's cwd) - connector/src/
    # pabel_connector genuinely exists here, so this is legitimate
    # development work (tests, agents_admin.py, doctor), not a bypass.
    command = "python -m pabel_connector.cli.main doctor --dir ."
    assert not invokes_pabel_connector_internals({"command": command})


def test_invokes_pabel_connector_internals_blocked_for_the_hyphenated_hook_binary(
        tmp_path, monkeypatch):
    """Regression test for the gap found while explaining why hardcoding an
    adapter's own agent_id is safe once DENY_CONFIG_TAMPER exists: running
    the *installed console script* `pabel-connector-hook` directly with a
    hand-picked key achieves the same bypass as importing pabel_connector's
    modules does, but the underscore-only regex used to miss it entirely -
    a hyphen is a non-word character, so "pabel-connector-hook" never
    contains the substring "pabel_connector" at all."""
    monkeypatch.chdir(tmp_path)
    command = "pabel-connector-hook cursor:beforeReadFile"
    assert invokes_pabel_connector_internals({"command": command})


def test_invokes_pabel_connector_internals_allows_the_plain_cli_everywhere():
    # install/uninstall/login/doctor need a real, already-admin-issued
    # client_secret to matter and never let a caller pick decide()'s
    # agent_id at call time - unlike -hook, folding this in would just
    # block routine CLI usage.
    assert not invokes_pabel_connector_internals({"command": "pabel-connector doctor"})


def test_invokes_pabel_connector_internals_ignores_unrelated_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not invokes_pabel_connector_internals({"command": "ls -la"})


def test_is_pabel_hook_config_target_matches_every_hook_based_installer():
    # One per hook-based installer's own CONFIG_RELATIVE_PATH/
    # MCP_CONFIG_RELATIVE_PATH/GLOBAL_CONFIG_RELATIVE_PATH - codex_family.py
    # (Codex CLI/ChatGPT desktop) deliberately absent, same reason it has no
    # ADAPTERS entry (see this function's own docstring).
    for path in [
        ".claude/settings.json",
        ".mcp.json",
        ".github/hooks/pabel.json",
        ".vscode/mcp.json",
        ".github/hooks/pabel-copilot-cli.json",
        ".copilot/hooks/pabel-copilot-cli.json",
        ".cursor/hooks.json",
        ".windsurf/hooks.json",
        ".codeium/windsurf/hooks.json",
    ]:
        assert is_pabel_hook_config_target(path), path


def test_is_pabel_hook_config_target_matches_windows_separators():
    assert is_pabel_hook_config_target("C:\\repo\\.claude\\settings.json")


def test_is_pabel_hook_config_target_matches_a_global_home_relative_path():
    # A --global install's absolute path still contains the same relative
    # fragment - see this function's own docstring for why that's enough
    # without decide() ever needing to know base_dir.
    assert is_pabel_hook_config_target("C:\\Users\\alice\\.claude\\settings.json")


def test_is_pabel_hook_config_target_ignores_unrelated_paths():
    assert not is_pabel_hook_config_target("/repo/README.md")
    assert not is_pabel_hook_config_target("")
    assert not is_pabel_hook_config_target(None)
