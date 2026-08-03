"""cli/main.py's --global flag: only offered for agents with a confirmed
user-level config location (installers/base.py's global_config_path()) -
rejecting cleanly for the rest (vscode) rather than guessing a path, the
same mistake vscode's workspace path already made once (see
docs/phase2-engineering-notes.md)."""

from pathlib import Path

import pabel_connector.cli.main as main_module
from pabel_connector.cli.main import main


def test_install_global_rejected_for_unsupported_agent(tmp_path, capsys):
    exit_code = main(["install", "vscode", "--dir", str(tmp_path), "--global"])
    assert exit_code == 2
    assert "no confirmed global" in capsys.readouterr().err
    assert not (tmp_path / ".vscode").exists()


def test_install_global_writes_to_home_for_supported_agent(tmp_path, monkeypatch):
    # store_credentials() is stubbed out here deliberately: its own
    # CREDENTIALS_FILE is resolved once at import time from the *real*
    # Path.home(), so calling it for real in a test would write a fake
    # test credential straight into this machine's actual
    # ~/.pabel/agent_credentials.json - this test only needs to verify
    # the installer's own file-writing, not agent_session's storage.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(main_module.agent_session, "store_credentials", lambda *a, **k: None)
    exit_code = main([
        "install", "cursor", "--dir", str(tmp_path / "some-project"), "--global",
        "--client-id", "test-client", "--client-secret", "test-secret",
    ])
    assert exit_code == 0
    assert (tmp_path / ".cursor" / "hooks.json").exists()


def test_uninstall_global_rejected_for_unsupported_agent(tmp_path, capsys):
    exit_code = main(["uninstall", "vscode", "--dir", str(tmp_path), "--global"])
    assert exit_code == 2
    assert "no confirmed global" in capsys.readouterr().err
