"""`pabel-connector generate-managed-settings`: writes managed-settings.json/
managed-mcp.json to --out-dir, deploys nothing itself (no registry write, no
Program Files write) - deployment is deliberately a separate manual step
(deploy/Deploy-ManagedSettings.ps1), so these tests only check file output."""

import json

from pabel_connector.cli.main import main


def test_writes_both_files_with_default_out_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    exit_code = main(["generate-managed-settings"])
    assert exit_code == 0
    assert (tmp_path / "managed-settings.json").exists()
    assert (tmp_path / "managed-mcp.json").exists()
    assert "Neither file has been deployed anywhere yet" in capsys.readouterr().out


def test_warns_when_python_path_omitted(tmp_path, capsys):
    exit_code = main(["generate-managed-settings", "--out-dir", str(tmp_path)])
    assert exit_code == 0
    assert "almost certainly wrong for a fleet" in capsys.readouterr().out


def test_no_warning_when_python_path_given(tmp_path, capsys):
    exit_code = main([
        "generate-managed-settings", "--out-dir", str(tmp_path),
        "--python-path", "C:\\Fleet\\python.exe",
    ])
    assert exit_code == 0
    assert "almost certainly wrong" not in capsys.readouterr().out
    settings = json.loads((tmp_path / "managed-settings.json").read_text(encoding="utf-8"))
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command.startswith('"C:\\Fleet\\python.exe"')


def test_custom_server_url_is_used(tmp_path):
    main([
        "generate-managed-settings", "--out-dir", str(tmp_path),
        "--python-path", "C:\\Fleet\\python.exe",
        "--server-url", "https://pabel.example.com/mcp",
    ])
    mcp = json.loads((tmp_path / "managed-mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["pabel"]["url"] == "https://pabel.example.com/mcp"
