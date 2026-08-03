"""doctor's hook-wiring check: a stored agent credential with no working
hook actually wired is exactly the silent failure mode found live with
vscode (agents_admin.py had a registered product, but this project
directory's .vscode/hooks.json never existed) - these tests exercise the
helper that's supposed to make that loud instead."""

from pabel_connector.cli.main import _hook_wiring_ok
from pabel_connector.installers import base, cursor


def test_hook_wiring_ok_when_installer_wrote_the_expected_commands(tmp_path):
    cursor.install(tmp_path)
    assert _hook_wiring_ok("cursor", tmp_path) is None


def test_hook_wiring_not_ok_when_config_file_is_missing(tmp_path):
    problem = _hook_wiring_ok("cursor", tmp_path)
    assert problem is not None
    assert "hooks.json" in problem


def test_hook_wiring_not_ok_when_config_exists_but_hook_was_removed(tmp_path):
    cursor.install(tmp_path)
    path = cursor.config_path(tmp_path)
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
