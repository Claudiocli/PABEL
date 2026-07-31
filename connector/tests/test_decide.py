import pabel_connector.core.decide as decide_module
from pabel_connector.core.decide import decide
from pabel_connector.core.types import DecisionKind, NormalizedCall
from pabel_connector.pabel_client.keycloak_client import AuthError
from pabel_connector.pabel_client.relay import RelayError

AGENT_ID = "claude-code"


def test_allow_for_unrelated_call():
    call = NormalizedCall(tool_name="Read", tool_input={"file_path": "/repo/README.md"})
    assert decide(call, AGENT_ID).kind == DecisionKind.ALLOW


def test_allow_with_injected_agent_token_for_direct_call_to_pabel_own_mcp_tool(monkeypatch):
    """Regression test for the self-conflict bug found during the
    multi-agent refactor: a direct, legitimate call to the pabel MCP
    server's own read_document (whose `name` argument happens to end in
    .abe) must never be denied by the same detection meant to catch
    everything else. Since the server now requires an agent_token argument
    the model can never legitimately supply itself, the hook must inject
    this installation's own credential rather than just allowing the call
    through unmodified - see core/decide.py's mcp_target branch."""
    monkeypatch.setattr(decide_module.agent_session, "access_token",
                        lambda agent_id: "fake-agent-token")
    call = NormalizedCall(
        tool_name="mcp__pabel__read_document",
        tool_input={"content": "ZGF0YQ==", "name": "something.abe"},
        mcp_target=("pabel", "read_document"),
    )
    decision = decide(call, AGENT_ID)
    assert decision.kind == DecisionKind.ALLOW
    assert decision.updated_input == {
        "content": "ZGF0YQ==", "name": "something.abe", "agent_token": "fake-agent-token"}


def test_allow_unmodified_when_no_agent_credential_stored(monkeypatch):
    """If this installation has no stored agent credential at all (never
    provisioned via `pabel-connector install`), the direct-call injection
    path must not raise - it falls back to a plain, unmodified ALLOW,
    letting the server's own rejection of the missing agent_token explain
    the problem instead."""
    def raise_auth_error(agent_id):
        raise AuthError("no installation credentials stored")
    monkeypatch.setattr(decide_module.agent_session, "access_token", raise_auth_error)
    call = NormalizedCall(
        tool_name="mcp__pabel__whoami", tool_input={}, mcp_target=("pabel", "whoami"))
    decision = decide(call, AGENT_ID)
    assert decision.kind == DecisionKind.ALLOW
    assert decision.updated_input is None


def test_deny_oabe_binary_invocation():
    call = NormalizedCall(tool_name="Bash", tool_input={"command": "oabe_dec -k k.key -i c.abe"},
                           is_execute=True)
    assert decide(call, AGENT_ID).kind == DecisionKind.DENY_OABE_BINARY


def test_deny_mutating_tool_on_abe_file():
    call = NormalizedCall(tool_name="Write", tool_input={"file_path": "C:/x/test.abe"},
                           is_write=True, write_target="C:/x/test.abe")
    assert decide(call, AGENT_ID).kind == DecisionKind.DENY_MUTATING


def test_allow_write_whose_content_merely_mentions_an_abe_path():
    """Regression test for a real false-positive found while wiring this
    project's own dev-repo hook onto the shared core: writing documentation
    that discusses an .abe path (e.g. this very test file) must not be
    treated as writing *to* one - only write_target, never the write's
    content, decides DENY_MUTATING."""
    call = NormalizedCall(
        tool_name="Write",
        tool_input={"file_path": "docs/notes.md", "content": "see documents/test.abe for the fixture"},
        is_write=True,
        write_target="docs/notes.md",
    )
    assert decide(call, AGENT_ID).kind == DecisionKind.ALLOW


def test_allow_write_with_no_write_target_set():
    """An adapter that (for whatever reason) can't identify a write target
    defaults to write_target=None - must not be treated as a match."""
    call = NormalizedCall(tool_name="Write", tool_input={"content": "mentions test.abe"}, is_write=True)
    assert decide(call, AGENT_ID).kind == DecisionKind.ALLOW


def test_deny_ambiguous_when_no_concrete_file_found():
    call = NormalizedCall(tool_name="Glob", tool_input={"pattern": "*.abe", "path": "/repo/documents"})
    assert decide(call, AGENT_ID).kind == DecisionKind.DENY_AMBIGUOUS


def test_deny_with_relay_on_successful_read(tmp_path, monkeypatch):
    target = tmp_path / "test.abe"
    target.write_text("ciphertext")
    fake_result = {"sections": ["ok"]}
    monkeypatch.setattr(decide_module, "read_document",
                        lambda path, name, agent_id: fake_result)

    call = NormalizedCall(tool_name="Read", tool_input={"file_path": str(target)})
    decision = decide(call, AGENT_ID)
    assert decision.kind == DecisionKind.DENY_WITH_RELAY
    assert decision.content == fake_result


def test_deny_auth_error_when_not_logged_in(tmp_path, monkeypatch):
    target = tmp_path / "test.abe"
    target.write_text("ciphertext")

    def raise_auth_error(path, name, agent_id):
        raise AuthError("not logged in")

    monkeypatch.setattr(decide_module, "read_document", raise_auth_error)
    call = NormalizedCall(tool_name="Read", tool_input={"file_path": str(target)})
    assert decide(call, AGENT_ID).kind == DecisionKind.DENY_AUTH_ERROR


def test_deny_relay_error_when_server_unreachable(tmp_path, monkeypatch):
    target = tmp_path / "test.abe"
    target.write_text("ciphertext")

    def raise_relay_error(path, name, agent_id):
        raise RelayError("connection refused")

    monkeypatch.setattr(decide_module, "read_document", raise_relay_error)
    call = NormalizedCall(tool_name="Read", tool_input={"file_path": str(target)})
    assert decide(call, AGENT_ID).kind == DecisionKind.DENY_RELAY_ERROR
