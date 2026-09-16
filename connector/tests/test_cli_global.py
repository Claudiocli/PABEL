"""cli/main.py's install/uninstall scope.

Machine-wide is the DEFAULT wherever a confirmed user-level config location
exists (installers/base.py's global_config_path()); `--dir` confines an
install to one project and has to be asked for. Agents with no confirmed
global location (vscode) are still rejected for --global rather than sent to
a guessed path - the same mistake vscode's workspace path already made once
(see docs/phase2-engineering-notes.md)."""

from pathlib import Path

import pabel_connector.cli.main as main_module
from pabel_connector.cli.main import main
from pabel_connector.installers import cursor


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


def test_global_only_agent_installs_globally_and_says_dir_was_ignored(tmp_path, monkeypatch, capsys):
    # codex-cli/chatgpt-desktop share one file with no project-scoped variant
    # at all. There is exactly one place the install can go, so --dir is
    # reported as ignored rather than failing the run over a missing flag.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(main_module.agent_session, "store_credentials", lambda *a, **k: None)
    exit_code = main(["install", "codex-cli", "--dir", str(tmp_path / "some-project"),
                      "--client-id", "x", "--client-secret", "y"])
    assert exit_code == 0
    assert "--dir was ignored" in capsys.readouterr().out
    assert (tmp_path / ".codex" / "config.toml").exists()
    assert not (tmp_path / "some-project" / ".codex").exists()


def test_install_and_uninstall_global_only_agent_via_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(main_module.agent_session, "store_credentials", lambda *a, **k: None)
    monkeypatch.setenv("PABEL_SERVER_URL", "http://localhost:8001/mcp")
    exit_code = main([
        "install", "codex-cli", "--dir", str(tmp_path / "some-project"), "--global",
        "--client-id", "test-client", "--client-secret", "test-secret",
    ])
    assert exit_code == 0
    assert (tmp_path / ".codex" / "config.toml").exists()

    exit_code = main(["uninstall", "codex-cli", "--dir", str(tmp_path / "some-project"), "--global"])
    assert exit_code == 0
    import tomlkit
    data = tomlkit.parse((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert "pabel-connector-codex-cli" not in data["mcp_servers"]


def test_uninstall_without_flags_removes_the_machine_wide_install(tmp_path, monkeypatch):
    """uninstall's default scope must match install's. When they disagreed,
    `uninstall` reported success while the real wiring stayed in place."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(main_module.agent_session, "store_credentials", lambda *a, **k: None)
    main(["install", "cursor", "--client-id", "x", "--client-secret", "y"])
    assert (tmp_path / ".cursor" / "hooks.json").exists()

    assert main(["uninstall", "cursor"]) == 0
    data = main_module.base.read_json(tmp_path / ".cursor" / "hooks.json")
    assert not any(main_module._command_present(data, main_module.base.hook_command(k))
                   for k in cursor.HOOK_KEYS)


def test_install_with_no_flags_goes_machine_wide_not_into_the_cwd(tmp_path, monkeypatch):
    """The default that matters: PABEL is provisioned once per machine. An
    install scoped to whatever directory the admin happened to be standing in
    would stop enforcing the moment the agent is started anywhere else, while
    the credential in ~/.pabel keeps working from everywhere."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(main_module.agent_session, "store_credentials", lambda *a, **k: None)
    project = tmp_path / "some-project"
    project.mkdir()
    monkeypatch.chdir(project)

    assert main(["install", "cursor", "--client-id", "x", "--client-secret", "y"]) == 0
    assert (tmp_path / ".cursor" / "hooks.json").exists()
    assert not (project / ".cursor").exists()
