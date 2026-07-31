"""Per-adapter parse()/render() round-trip tests against a hand-built
payload matching each vendor's own documented schema (see
docs/coverage-matrix.md for sources). These prove internal consistency and
catch obvious mistakes - they are explicitly NOT equivalent to a live
confirmation against the real agent; see each adapter module's own
docstring and the README's coverage table for verification status.
"""

import json

from pabel_connector.adapters import claude_code, codex_cli, copilot_cli, cursor, gemini_cli, vscode, windsurf
from pabel_connector.core.types import Decision, DecisionKind


def _payload(tool_name, tool_input):
    return json.dumps({"tool_name": tool_name, "tool_input": tool_input}).encode()


# --- claude_code (VERIFIED reference adapter) -----------------------------

def test_claude_code_parse_unrelated_read():
    call = claude_code.parse([], _payload("Read", {"file_path": "/repo/README.md"}))
    assert call.tool_name == "Read"
    assert not call.is_write
    assert not call.is_execute
    assert call.mcp_target is None


def test_claude_code_parse_recognizes_mutating_tools_and_bash():
    assert claude_code.parse([], _payload("Write", {})).is_write
    assert claude_code.parse([], _payload("Edit", {})).is_write
    assert claude_code.parse([], _payload("NotebookEdit", {})).is_write
    assert claude_code.parse([], _payload("Bash", {"command": "ls"})).is_execute


def test_claude_code_parse_recovers_mcp_target():
    call = claude_code.parse([], _payload("mcp__pabel__read_document", {"name": "x.abe"}))
    assert call.mcp_target == ("pabel", "read_document")


def test_claude_code_render_allow_is_silent():
    resp = claude_code.render(Decision(DecisionKind.ALLOW))
    assert resp.stdout == "" and resp.exit_code == 0


def test_claude_code_render_deny_with_relay_includes_additional_context():
    content = {"sections": ["hello"]}
    resp = claude_code.render(Decision(DecisionKind.DENY_WITH_RELAY, reason="blocked", content=content))
    output = json.loads(resp.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert json.loads(output["additionalContext"]) == content


# --- vscode (same schema as claude_code) ----------------------------------

def test_vscode_matches_claude_code_schema():
    resp = vscode.render(Decision(DecisionKind.DENY_MUTATING, reason="no writes"))
    output = json.loads(resp.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"


# --- copilot_cli (folds content into permissionDecisionReason) -----------

def test_copilot_cli_folds_content_into_reason_as_a_reliability_fallback():
    content = {"sections": ["hello"]}
    resp = copilot_cli.render(Decision(DecisionKind.DENY_WITH_RELAY, reason="blocked", content=content))
    output = json.loads(resp.stdout)["hookSpecificOutput"]
    assert "hello" in output["permissionDecisionReason"]
    # Also sets additionalContext as a harmless duplicate.
    assert json.loads(output["additionalContext"]) == content


def test_copilot_cli_allow_is_silent():
    assert copilot_cli.render(Decision(DecisionKind.ALLOW)).stdout == ""


# --- cursor (3 hook points, shared {permission, agentMessage} shape) ------

def test_cursor_before_read_file_parses_file_path():
    call = cursor.before_read_file.parse([], json.dumps({"file_path": "/repo/secret.abe"}).encode())
    assert call.tool_name == "Read"
    assert call.tool_input["file_path"] == "/repo/secret.abe"


def test_cursor_before_shell_execution_parses_command():
    call = cursor.before_shell_execution.parse([], json.dumps({"command": "cat secret.abe"}).encode())
    assert call.is_execute
    assert call.tool_input["command"] == "cat secret.abe"


def test_cursor_before_mcp_execution_recovers_pabel_target():
    call = cursor.before_mcp_execution.parse(
        [], json.dumps({"tool_name": "read_document", "arguments": {"name": "x.abe"}}).encode())
    assert call.mcp_target == ("pabel", "read_document")


def test_cursor_render_deny_uses_agent_message_for_content():
    content = {"sections": ["hello"]}
    resp = cursor.before_read_file.render(Decision(DecisionKind.DENY_WITH_RELAY, reason="blocked", content=content))
    output = json.loads(resp.stdout)
    assert output["permission"] == "deny"
    assert "hello" in output["agentMessage"]


def test_cursor_render_allow():
    resp = cursor.before_read_file.render(Decision(DecisionKind.ALLOW))
    assert json.loads(resp.stdout) == {"permission": "allow"}


# --- windsurf (4 hook points, exit-code-2 + stderr blocking) --------------

def test_windsurf_pre_read_code_parses_file_path():
    call = windsurf.pre_read_code.parse([], json.dumps({"file_path": "/repo/secret.abe"}).encode())
    assert call.tool_name == "Read"


def test_windsurf_pre_run_command_parses_command():
    call = windsurf.pre_run_command.parse([], json.dumps({"command": "cat secret.abe"}).encode())
    assert call.is_execute


def test_windsurf_render_allow_is_exit_zero_no_output():
    resp = windsurf.pre_read_code.render(Decision(DecisionKind.ALLOW))
    assert resp.exit_code == 0 and resp.stdout == "" and resp.stderr == ""


def test_windsurf_render_deny_uses_exit_code_2_and_stderr():
    content = {"sections": ["hello"]}
    resp = windsurf.pre_read_code.render(Decision(DecisionKind.DENY_WITH_RELAY, reason="blocked", content=content))
    assert resp.exit_code == 2
    assert "hello" in resp.stderr
    assert resp.stdout == ""


# --- gemini_cli (BeforeTool, {"decision": "deny", "reason": ...}) ---------

def test_gemini_cli_mcp_target_recovered_from_underscore_naming():
    call = gemini_cli.parse([], _payload("mcp_pabel_read_document", {"name": "x.abe"}))
    assert call.mcp_target == ("pabel", "read_document")


def test_gemini_cli_render_deny_folds_content_into_reason():
    content = {"sections": ["hello"]}
    resp = gemini_cli.render(Decision(DecisionKind.DENY_WITH_RELAY, reason="blocked", content=content))
    output = json.loads(resp.stdout)
    assert output["decision"] == "deny"
    assert "hello" in output["reason"]


# --- codex_cli (degraded: Bash-only, deny-only) ---------------------------

def test_codex_cli_parse_always_bash_and_execute():
    call = codex_cli.parse([], json.dumps({"tool_input": {"command": "cat secret.abe"}}).encode())
    assert call.tool_name == "Bash"
    assert call.is_execute


def test_codex_cli_render_deny_folds_content_into_reason():
    content = {"sections": ["hello"]}
    resp = codex_cli.render(Decision(DecisionKind.DENY_WITH_RELAY, reason="blocked", content=content))
    output = json.loads(resp.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "hello" in output["permissionDecisionReason"]
