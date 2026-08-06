"""Per-adapter parse()/render() round-trip tests against a hand-built
payload matching each vendor's own documented schema (see
docs/coverage-matrix.md for sources). These prove internal consistency and
catch obvious mistakes - they are explicitly NOT equivalent to a live
confirmation against the real agent; see each adapter module's own
docstring and the README's coverage table for verification status.
"""

import json

from pabel_connector.adapters import claude_code, copilot_cli, cursor, vscode, windsurf
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


def test_claude_code_parse_extracts_write_target_not_whole_payload():
    call = claude_code.parse([], _payload("Write", {"file_path": "docs/notes.md",
                                                      "content": "discusses documents/test.abe"}))
    assert call.write_target == "docs/notes.md"


def test_claude_code_parse_recovers_mcp_target():
    call = claude_code.parse([], _payload("mcp__pabel__read_document", {"name": "x.abe"}))
    assert call.mcp_target == ("pabel", "read_document")


def test_claude_code_render_allow_is_silent():
    resp = claude_code.render(Decision(DecisionKind.ALLOW))
    assert resp.stdout == "" and resp.exit_code == 0


def test_claude_code_render_allow_with_updated_input_injects_agent_token():
    """The one adapter that acts on Decision.updated_input today - a direct
    model call to pabel's own tools is allowed through with this
    installation's agent credential injected, via Claude Code's own
    hookSpecificOutput.updatedInput field, never visible to the model as a
    value it supplied itself."""
    updated = {"content": "ZGF0YQ==", "name": "x.abe", "agent_token": "secret-token"}
    resp = claude_code.render(Decision(DecisionKind.ALLOW, updated_input=updated))
    output = json.loads(resp.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"] == updated


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


# --- cursor (3 hook points, shared {permission, agent_message} shape) -----

def test_cursor_before_read_file_parses_file_path():
    call = cursor.before_read_file.parse([], json.dumps({"file_path": "/repo/secret.abe"}).encode())
    assert call.tool_name == "Read"
    assert call.tool_input["file_path"] == "/repo/secret.abe"


def test_cursor_before_shell_execution_parses_command():
    call = cursor.before_shell_execution.parse([], json.dumps({"command": "cat secret.abe"}).encode())
    assert call.is_execute
    assert call.tool_input["command"] == "cat secret.abe"


def test_cursor_before_mcp_execution_recovers_pabel_target():
    # tool_input is the confirmed field name (cursor.com/docs/hooks) - this
    # is the primary shape a real payload sends.
    call = cursor.before_mcp_execution.parse(
        [], json.dumps({"tool_name": "read_document", "tool_input": {"name": "x.abe"}}).encode())
    assert call.mcp_target == ("pabel", "read_document")


def test_cursor_before_mcp_execution_falls_back_to_arguments_field():
    call = cursor.before_mcp_execution.parse(
        [], json.dumps({"tool_name": "read_document", "arguments": {"name": "x.abe"}}).encode())
    assert call.mcp_target == ("pabel", "read_document")


def test_cursor_render_deny_uses_agent_message_for_content():
    content = {"sections": ["hello"]}
    resp = cursor.before_read_file.render(Decision(DecisionKind.DENY_WITH_RELAY, reason="blocked", content=content))
    output = json.loads(resp.stdout)
    assert output["permission"] == "deny"
    assert "hello" in output["agent_message"]


def test_cursor_render_allow():
    resp = cursor.before_read_file.render(Decision(DecisionKind.ALLOW))
    assert json.loads(resp.stdout) == {"permission": "allow"}


# --- windsurf (4 hook points, exit-code-2 + stderr blocking) --------------

def test_windsurf_pre_read_code_parses_file_path():
    call = windsurf.pre_read_code.parse([], json.dumps({"file_path": "/repo/secret.abe"}).encode())
    assert call.tool_name == "Read"


def test_windsurf_pre_run_command_parses_command_line():
    # command_line is the confirmed field name (docs.windsurf.com) - nested
    # under tool_info in the real envelope, but _field() also checks
    # top-level for defensiveness, which this test exercises directly.
    call = windsurf.pre_run_command.parse(
        [], json.dumps({"tool_info": {"command_line": "cat secret.abe"}}).encode())
    assert call.is_execute
    assert call.tool_input["command"] == "cat secret.abe"


def test_windsurf_pre_run_command_falls_back_to_command_field():
    call = windsurf.pre_run_command.parse([], json.dumps({"command": "cat secret.abe"}).encode())
    assert call.is_execute


def test_windsurf_pre_mcp_tool_use_matches_on_explicit_server_name():
    # mcp_server_name is a real, explicit field Windsurf provides (unlike
    # Cursor) - matched directly, not inferred from known tool names.
    call = windsurf.pre_mcp_tool_use.parse([], json.dumps({"tool_info": {
        "mcp_server_name": "pabel", "mcp_tool_name": "read_document",
        "mcp_tool_arguments": {"name": "x.abe"},
    }}).encode())
    assert call.mcp_target == ("pabel", "read_document")


def test_windsurf_pre_mcp_tool_use_ignores_other_servers():
    call = windsurf.pre_mcp_tool_use.parse([], json.dumps({"tool_info": {
        "mcp_server_name": "github", "mcp_tool_name": "create_issue", "mcp_tool_arguments": {},
    }}).encode())
    assert call.mcp_target is None


def test_windsurf_render_allow_is_exit_zero_no_output():
    resp = windsurf.pre_read_code.render(Decision(DecisionKind.ALLOW))
    assert resp.exit_code == 0 and resp.stdout == "" and resp.stderr == ""


def test_windsurf_render_deny_uses_exit_code_2_and_stderr():
    content = {"sections": ["hello"]}
    resp = windsurf.pre_read_code.render(Decision(DecisionKind.DENY_WITH_RELAY, reason="blocked", content=content))
    assert resp.exit_code == 2
    assert "hello" in resp.stderr
    assert resp.stdout == ""


# Gemini CLI's adapter/installer were removed entirely (deprecated by the
# vendor's own successor product, "Antigravity" - deliberately not
# supported either; see docs/phase2-engineering-notes.md).

# codex_cli/chatgpt_desktop have no adapter here (see installers/codex_cli.py,
# installers/chatgpt_desktop.py, docs/known-gaps.md) - neither product has any
# confirmed hook/tool-interception mechanism, only MCP tool registration
# (installers/registry.py, not this file).
