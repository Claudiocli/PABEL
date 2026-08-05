"""managed_settings.py's job is to never drift from what
installers/claude_code.py's own install() actually writes - these tests
compare its output against installers.base.hook_command()/
mcp_server_command() directly, the same functions install() calls, rather
than against a second hand-copied expectation that could itself go stale."""

import sys

from pabel_connector import managed_settings
from pabel_connector.installers import base, claude_code


def test_generate_managed_settings_pins_every_hook_key():
    result = managed_settings.generate_managed_settings()
    assert result["allowManagedHooksOnly"] is True
    assert result["allowManagedPermissionRulesOnly"] is True
    pre_commands = {h["hooks"][0]["command"] for h in result["hooks"]["PreToolUse"]}
    end_commands = {h["hooks"][0]["command"] for h in result["hooks"]["SessionEnd"]}
    assert pre_commands == {base.hook_command("claude-code")}
    assert end_commands == {base.hook_command("claude-code:session-end")}


def test_generate_managed_settings_uses_provided_python_path():
    result = managed_settings.generate_managed_settings(python_path="C:\\Fleet\\python.exe")
    command = result["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == '"C:\\Fleet\\python.exe" -m pabel_connector.hook claude-code'
    assert sys.executable not in command


def test_generate_managed_settings_defaults_to_this_interpreter():
    result = managed_settings.generate_managed_settings()
    command = result["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert sys.executable in command


def test_generate_managed_mcp_pins_both_servers():
    result = managed_settings.generate_managed_mcp()
    servers = result["mcpServers"]
    assert servers["pabel"] == {"type": "http", "url": managed_settings.DEFAULT_SERVER_URL}
    assert servers["pabel-connector"]["type"] == "stdio"
    expected_command, *expected_args = base.mcp_server_command(claude_code.name)
    assert servers["pabel-connector"]["args"] == expected_args
    assert servers["pabel-connector"]["command"] == expected_command


def test_generate_managed_mcp_uses_provided_python_path_and_server_url():
    result = managed_settings.generate_managed_mcp(
        python_path="C:\\Fleet\\python.exe", server_url="https://pabel.example.com/mcp")
    servers = result["mcpServers"]
    assert servers["pabel"]["url"] == "https://pabel.example.com/mcp"
    assert servers["pabel-connector"]["command"] == "C:\\Fleet\\python.exe"
