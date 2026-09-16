"""doctor's hook-wiring check: a stored agent credential with no working
hook actually wired is exactly the silent failure mode found live with
vscode (agents_admin.py had a registered product, but this project
directory's .vscode/hooks.json never existed) - these tests exercise the
helper that's supposed to make that loud instead."""

from pathlib import Path

import pytest

import pabel_connector.cli.main as main_module
from pabel_connector.cli.main import _credential_problem, _hook_wiring_ok, main
from pabel_connector.installers import base, cursor
from pabel_connector.pabel_client.keycloak_client import AuthError, KeycloakUnreachable


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """_hook_wiring_ok now checks the machine-wide location first, which
    resolves against Path.home() - without this the tests below would read
    the developer's own ~/.cursor/hooks.json and pass or fail accordingly."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def test_hook_wiring_ok_when_installed_machine_wide(fake_home, tmp_path):
    cursor.install(tmp_path, global_=True)
    assert _hook_wiring_ok("cursor", tmp_path) is None


def test_hook_wiring_flags_an_install_that_only_covers_one_project(fake_home, tmp_path):
    """The hole that let opencode read a .abe untouched: enforcement wired
    into one directory, credential valid everywhere. Wired correctly *here*,
    so the old check returned None and doctor printed [ok]."""
    cursor.install(tmp_path)
    problem = _hook_wiring_ok("cursor", tmp_path)
    assert problem is not None
    assert "NOT machine-wide" in problem


def test_hook_wiring_not_ok_when_config_file_is_missing(fake_home, tmp_path):
    problem = _hook_wiring_ok("cursor", tmp_path)
    assert problem is not None
    assert "hooks.json" in problem


def test_hook_wiring_not_ok_when_config_exists_but_hook_was_removed(fake_home, tmp_path):
    cursor.install(tmp_path, global_=True)
    path = base.global_config_path(cursor.GLOBAL_CONFIG_RELATIVE_PATH)
    data = base.read_json(path)
    commands = {base.hook_command(k) for k in cursor.HOOK_KEYS}
    base.remove_matching_commands(data, commands)
    base.write_json(path, data)
    problem = _hook_wiring_ok("cursor", tmp_path)
    assert problem is not None
    assert "not wired" in problem


def test_hook_wiring_ok_skipped_for_installer_with_no_config_file(tmp_path):
    # cline/continue-dev are documented gaps with no config file at all -
    # nothing for this check to look at, so they must not be flagged as
    # broken. claude-code now writes real config like every other agent
    # (see installers/claude_code.py) and is covered by the same
    # hook-wiring check, not skipped anymore.
    assert _hook_wiring_ok("cline", tmp_path) is None


def test_hook_wiring_not_ok_for_unregistered_agent(tmp_path):
    problem = _hook_wiring_ok("not-a-real-agent", tmp_path)
    assert problem is not None


def test_hook_wiring_ok_skipped_for_mcp_only_installer_with_no_hook_keys(tmp_path):
    # codex-cli/chatgpt-desktop now have a real config_path (the shared
    # ~/.codex/config.toml) but no HOOK_KEYS at all - neither product has
    # any hook to check. Without the HOOK_KEYS guard in _hook_wiring_ok,
    # this would raise AttributeError instead of skipping cleanly.
    assert _hook_wiring_ok("codex-cli", tmp_path) is None
    assert _hook_wiring_ok("chatgpt-desktop", tmp_path) is None


# --- the other silent failure: a credential that no longer authenticates ---

def test_credential_problem_is_none_when_the_token_request_succeeds(monkeypatch):
    monkeypatch.setattr(main_module.agent_session, "access_token", lambda a: "tok")
    assert _credential_problem("cursor") is None


def test_credential_problem_reports_a_rejected_credential(monkeypatch):
    """Revoked installation, or a Keycloak rebuilt from scratch: the file on
    disk is still perfectly well-formed, so nothing else would notice."""
    def refuse(_agent):
        raise AuthError("client_credentials grant failed: 401 invalid_client")
    monkeypatch.setattr(main_module.agent_session, "access_token", refuse)
    problem = _credential_problem("cursor")
    assert problem is not None
    assert "no longer authenticates" in problem
    assert "create-installation cursor" in problem


def test_credential_problem_propagates_unreachable_rather_than_blaming_the_credential(monkeypatch):
    """A stopped Keycloak must not read as a stack of dead credentials -
    doctor catches this separately and reports it once."""
    def down(_agent):
        raise KeycloakUnreachable("cannot reach Keycloak: connection refused")
    monkeypatch.setattr(main_module.agent_session, "access_token", down)
    with pytest.raises(KeycloakUnreachable):
        _credential_problem("cursor")


def test_doctor_reports_unreachable_keycloak_once_not_per_installation(monkeypatch, capfd):
    monkeypatch.setattr(main_module.agent_session, "installations",
                        lambda: {"cursor": "cid-1", "vscode": "cid-2", "claude-code": "cid-3"})
    monkeypatch.setattr(main_module.session, "access_token", lambda: "human-tok")

    def down(_agent):
        raise KeycloakUnreachable("cannot reach Keycloak: connection refused")
    monkeypatch.setattr(main_module.agent_session, "access_token", down)
    main(["doctor", "--dir", "."])
    out = capfd.readouterr().out
    assert out.count("could not check whether the stored credentials") == 1
