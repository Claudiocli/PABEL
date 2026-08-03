import pabel_connector.pabel_client.relay as relay_module
from pabel_connector.pabel_client.keycloak_client import AuthError
from pabel_connector.pabel_client.relay import RelayError, read_document_with_login

AGENT_ID = "claude-code"

# Patched at read_document_async (not the sync read_document) - that's what
# read_document_with_login_async actually calls internally. The sync
# read_document_with_login used here is still a valid entry point: it's
# just anyio.run() over the same async chain, for core/decide.py's
# subprocess/hook use case (see relay.py's docstrings for why
# mcp_local_server.py must call the _async versions directly instead).


def test_returns_result_immediately_when_already_authenticated(monkeypatch):
    async def fake_read_document_async(path, name, agent_id):
        return {"sections": ["ok"]}

    monkeypatch.setattr(relay_module, "read_document_async", fake_read_document_async)
    result = read_document_with_login("test.abe", "test.abe", AGENT_ID)
    assert result == {"sections": ["ok"]}


def test_relay_error_is_not_retried_or_treated_as_a_login_problem(monkeypatch):
    async def raise_relay_error(path, name, agent_id):
        raise RelayError("connection refused")

    def fail_if_called():
        raise AssertionError("login() must not be called for a RelayError")

    monkeypatch.setattr(relay_module, "read_document_async", raise_relay_error)
    monkeypatch.setattr(relay_module.session, "login", fail_if_called)
    try:
        read_document_with_login("test.abe", "test.abe", AGENT_ID)
        assert False, "expected RelayError to propagate"
    except RelayError:
        pass


def test_auth_error_triggers_login_and_retries_once(monkeypatch):
    calls = {"n": 0}

    async def read_document_fails_once_then_succeeds(path, name, agent_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AuthError("not logged in")
        return {"sections": ["ok"]}

    monkeypatch.setattr(relay_module, "read_document_async", read_document_fails_once_then_succeeds)
    monkeypatch.setattr(relay_module.session, "login", lambda: None)
    result = read_document_with_login("test.abe", "test.abe", AGENT_ID)
    assert result == {"sections": ["ok"]}
    assert calls["n"] == 2


def test_login_failure_is_reported_distinctly_and_not_retried(monkeypatch):
    async def raise_auth_error(path, name, agent_id):
        raise AuthError("not logged in")

    def raise_login_timeout():
        raise AuthError("browser login timed out")

    monkeypatch.setattr(relay_module, "read_document_async", raise_auth_error)
    monkeypatch.setattr(relay_module.session, "login", raise_login_timeout)
    try:
        read_document_with_login("test.abe", "test.abe", AGENT_ID)
        assert False, "expected AuthError to propagate"
    except AuthError as e:
        assert "automatic browser login could not complete" in str(e)


def test_still_unauthenticated_after_successful_login_is_reported_distinctly(monkeypatch):
    # Login itself completes, but the retried read_document call still
    # raises AuthError (e.g. the human logged in but still lacks the
    # required role) - surfaced as its own distinct message, not retried
    # again forever.
    async def always_raise_auth_error(path, name, agent_id):
        raise AuthError("still not authorized")

    monkeypatch.setattr(relay_module, "read_document_async", always_raise_auth_error)
    monkeypatch.setattr(relay_module.session, "login", lambda: None)
    try:
        read_document_with_login("test.abe", "test.abe", AGENT_ID)
        assert False, "expected AuthError to propagate"
    except AuthError as e:
        assert "logged in, but still not authenticated" in str(e)


# The "no nested event loop" regression (mcp_local_server.py's tool
# handlers must call read_document_with_login_async/whoami_async directly,
# never the sync anyio.run()-based wrappers) is already covered by
# test_mcp_local_server.py, which patches the _async names specifically
# and calls the tool handlers the way FastMCP actually does - no separate
# test needed here for the same scenario.
