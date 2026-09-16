"""`pabel-connector install` with no agent named: pick several from a list.

The security-relevant property here is that each selected agent is still
prompted for its OWN credential - one credential reused across several
agents would hand out exactly the shared identity DENY_CONFIG_TAMPER and
server/core.py's resolve_agent() exist to prevent.
"""

from pathlib import Path

import pabel_connector.cli.main as main_module
from pabel_connector.cli.main import _parse_selection, main

KEYS = ["alpha", "beta", "gamma"]


def test_parse_selection_accepts_numbers():
    assert _parse_selection("1,3", KEYS) == ["alpha", "gamma"]


def test_parse_selection_accepts_keys_and_spaces():
    assert _parse_selection("beta gamma", KEYS) == ["beta", "gamma"]


def test_parse_selection_all():
    assert _parse_selection("all", KEYS) == KEYS


def test_parse_selection_deduplicates_and_keeps_order():
    assert _parse_selection("3,1,3", KEYS) == ["gamma", "alpha"]


def test_parse_selection_rejects_the_whole_input_on_a_bad_token(capsys):
    # Deliberately all-or-nothing: silently installing the recognised subset
    # would leave the employee believing an agent was wired when it wasn't.
    assert _parse_selection("1,nope", KEYS) == []
    assert "not an agent number or key" in capsys.readouterr().err


def test_parse_selection_rejects_an_out_of_range_number(capsys):
    assert _parse_selection("9", KEYS) == []
    assert "not an agent number or key" in capsys.readouterr().err


def _stub_io(monkeypatch, recorder, selection):
    """Scripts the selection prompt, then answers every later client_id
    prompt with a placeholder. store_credentials is stubbed because its
    CREDENTIALS_FILE resolves from the real Path.home() at import time -
    calling it for real would write test credentials into this machine's
    actual ~/.pabel/agent_credentials.json (same reasoning as
    test_cli_global.py)."""
    answers = iter([selection])
    monkeypatch.setattr(main_module, "input",
                        lambda _="": next(answers, "id"), raising=False)
    monkeypatch.setattr(main_module.getpass, "getpass", lambda _="": "secret")
    monkeypatch.setattr(main_module.agent_session, "store_credentials",
                        lambda agent, cid, secret: recorder.append((agent, cid, secret)))


def test_multi_select_installs_each_agent_with_its_own_credential(tmp_path, monkeypatch):
    stored = []
    _stub_io(monkeypatch, stored, "cursor,vscode")

    exit_code = main(["install", "--dir", str(tmp_path)])
    assert exit_code == 0
    assert [agent for agent, _, _ in stored] == ["cursor", "vscode"]
    assert (tmp_path / ".cursor" / "hooks.json").exists()
    assert (tmp_path / ".github" / "hooks" / "pabel.json").exists()


def test_multi_select_refuses_a_shared_client_id(tmp_path, capsys):
    """One credential across several agents is precisely the shared identity
    this project works to prevent, so the flags are refused, not reused."""
    exit_code = main(["install", "--dir", str(tmp_path), "--client-id", "x"])
    assert exit_code == 2
    assert "own credential" in capsys.readouterr().err


def test_multi_select_forces_global_for_a_global_only_agent(tmp_path, monkeypatch, capfd):
    """codex-cli/chatgpt-desktop have no project-scoped install at all, so an
    explicit --dir is reported as ignored rather than silently honoured."""
    stored = []
    _stub_io(monkeypatch, stored, "codex-cli")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    exit_code = main(["install", "--dir", str(tmp_path / "proj")])
    assert exit_code == 0
    assert (tmp_path / ".codex" / "config.toml").exists()
    out = capfd.readouterr().out
    assert "machine-wide" in out
    assert "--dir was ignored" in out


def test_multi_select_falls_back_to_dir_when_global_is_unsupported(tmp_path, monkeypatch, capfd):
    """A batch must not abort because one product has no global location -
    it installs per-project and says so."""
    stored = []
    _stub_io(monkeypatch, stored, "vscode")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    exit_code = main(["install", "--dir", str(tmp_path / "proj"), "--global"])
    assert exit_code == 0
    assert "no confirmed global location" in capfd.readouterr().out
    assert (tmp_path / "proj" / ".github" / "hooks" / "pabel.json").exists()


def test_empty_selection_installs_nothing(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(main_module, "input", lambda _="": "", raising=False)
    exit_code = main(["install", "--dir", str(tmp_path)])
    assert exit_code == 2
    assert "nothing selected" in capfd.readouterr().err


def test_naming_one_agent_still_works_exactly_as_before(tmp_path, monkeypatch):
    stored = []
    _stub_io(monkeypatch, stored, "unused")
    exit_code = main(["install", "cursor", "--dir", str(tmp_path),
                      "--client-id", "cid", "--client-secret", "sec"])
    assert exit_code == 0
    assert stored == [("cursor", "cid", "sec")]
