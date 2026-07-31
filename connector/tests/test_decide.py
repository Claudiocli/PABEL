import pabel_connector.core.decide as decide_module
from pabel_connector.core.decide import decide
from pabel_connector.core.types import DecisionKind, NormalizedCall
from pabel_connector.pabel_client.keycloak_client import AuthError
from pabel_connector.pabel_client.relay import RelayError


def test_allow_for_unrelated_call():
    call = NormalizedCall(tool_name="Read", tool_input={"file_path": "/repo/README.md"})
    assert decide(call).kind == DecisionKind.ALLOW


def test_allow_for_direct_call_to_pabel_own_mcp_tool_even_if_abe_mentioned():
    """Regression test for the self-conflict bug found during the
    multi-agent refactor: a direct, legitimate call to the pabel MCP
    server's own read_document (whose `name` argument happens to end in
    .abe) must never be denied by the same detection meant to catch
    everything else."""
    call = NormalizedCall(
        tool_name="mcp__pabel__read_document",
        tool_input={"content": "ZGF0YQ==", "name": "something.abe"},
        mcp_target=("pabel", "read_document"),
    )
    assert decide(call).kind == DecisionKind.ALLOW


def test_deny_oabe_binary_invocation():
    call = NormalizedCall(tool_name="Bash", tool_input={"command": "oabe_dec -k k.key -i c.abe"},
                           is_execute=True)
    assert decide(call).kind == DecisionKind.DENY_OABE_BINARY


def test_deny_mutating_tool_on_abe_file():
    call = NormalizedCall(tool_name="Write", tool_input={"file_path": "C:/x/test.abe"}, is_write=True)
    assert decide(call).kind == DecisionKind.DENY_MUTATING


def test_deny_ambiguous_when_no_concrete_file_found():
    call = NormalizedCall(tool_name="Glob", tool_input={"pattern": "*.abe", "path": "/repo/documents"})
    assert decide(call).kind == DecisionKind.DENY_AMBIGUOUS


def test_deny_with_relay_on_successful_read(tmp_path, monkeypatch):
    target = tmp_path / "test.abe"
    target.write_text("ciphertext")
    fake_result = {"sections": ["ok"]}
    monkeypatch.setattr(decide_module, "read_document", lambda path, name: fake_result)

    call = NormalizedCall(tool_name="Read", tool_input={"file_path": str(target)})
    decision = decide(call)
    assert decision.kind == DecisionKind.DENY_WITH_RELAY
    assert decision.content == fake_result


def test_deny_auth_error_when_not_logged_in(tmp_path, monkeypatch):
    target = tmp_path / "test.abe"
    target.write_text("ciphertext")

    def raise_auth_error(path, name):
        raise AuthError("not logged in")

    monkeypatch.setattr(decide_module, "read_document", raise_auth_error)
    call = NormalizedCall(tool_name="Read", tool_input={"file_path": str(target)})
    assert decide(call).kind == DecisionKind.DENY_AUTH_ERROR


def test_deny_relay_error_when_server_unreachable(tmp_path, monkeypatch):
    target = tmp_path / "test.abe"
    target.write_text("ciphertext")

    def raise_relay_error(path, name):
        raise RelayError("connection refused")

    monkeypatch.setattr(decide_module, "read_document", raise_relay_error)
    call = NormalizedCall(tool_name="Read", tool_input={"file_path": str(target)})
    assert decide(call).kind == DecisionKind.DENY_RELAY_ERROR
