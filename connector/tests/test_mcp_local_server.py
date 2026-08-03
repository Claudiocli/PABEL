import anyio

import pabel_connector.mcp_local_server as mcp_local_server
from pabel_connector.pabel_client.keycloak_client import AuthError
from pabel_connector.pabel_client.relay import RelayError


def run(coro):
    return anyio.run(lambda: coro)


def test_main_requires_agent_id_argument(capsys):
    assert mcp_local_server.main(argv=[]) == 2
    assert "usage:" in capsys.readouterr().err


def test_whoami_relays_to_deployed_server(monkeypatch):
    # Must go through whoami_async, not the sync whoami() - calling the
    # sync wrapper's anyio.run() from inside FastMCP's own already-running
    # event loop is exactly the "Already running asyncio in this thread"
    # crash found live.
    monkeypatch.setattr(mcp_local_server, "AGENT_ID", "claude-code")

    async def fake_whoami_async(agent_id):
        return {"username": "alice", "agent_id": agent_id}

    monkeypatch.setattr(mcp_local_server.relay, "whoami_async", fake_whoami_async)
    assert run(mcp_local_server.whoami()) == {"username": "alice", "agent_id": "claude-code"}


def test_whoami_reports_auth_error_instead_of_raising(monkeypatch):
    monkeypatch.setattr(mcp_local_server, "AGENT_ID", "claude-code")

    async def raise_auth_error(agent_id):
        raise AuthError("not logged in")

    monkeypatch.setattr(mcp_local_server.relay, "whoami_async", raise_auth_error)
    result = run(mcp_local_server.whoami())
    assert result["ok"] is False
    assert "not logged in" in result["error"]


def test_read_document_uses_the_guided_login_flow(monkeypatch):
    # This tool must go through read_document_with_login_async, not the
    # sync read_document_with_login - same nested-event-loop reasoning as
    # whoami above.
    monkeypatch.setattr(mcp_local_server, "AGENT_ID", "claude-code")
    calls = []

    async def fake_read_document_with_login_async(path, name, agent_id):
        calls.append((path, name, agent_id))
        return {"sections": ["ok"]}

    monkeypatch.setattr(mcp_local_server.relay, "read_document_with_login_async",
                        fake_read_document_with_login_async)
    result = run(mcp_local_server.read_document("test.abe"))
    assert result == {"sections": ["ok"]}
    assert calls == [("test.abe", "document", "claude-code")]


def test_read_document_reports_relay_error_instead_of_raising(monkeypatch):
    monkeypatch.setattr(mcp_local_server, "AGENT_ID", "claude-code")

    async def raise_relay_error(path, name, agent_id):
        raise RelayError("connection refused")

    monkeypatch.setattr(mcp_local_server.relay, "read_document_with_login_async", raise_relay_error)
    result = run(mcp_local_server.read_document("test.abe"))
    assert result["ok"] is False
    assert "connection refused" in result["error"]


def test_login_skips_browser_when_already_logged_in(monkeypatch):
    # Regression test: login() used to call session.login() unconditionally,
    # opening the browser again even with an already-valid session - found
    # live ("mi ha fatto loggare 2 volte").
    monkeypatch.setattr(mcp_local_server.session, "access_token", lambda: "still-valid-token")

    def fail_if_called():
        raise AssertionError("session.login() must not be called when already logged in")

    monkeypatch.setattr(mcp_local_server.session, "login", fail_if_called)
    result = run(mcp_local_server.login())
    assert result == {"ok": True, "already_logged_in": True}


def test_login_runs_browser_flow_when_not_logged_in(monkeypatch):
    def raise_auth_error():
        raise AuthError("not logged in yet")

    monkeypatch.setattr(mcp_local_server.session, "access_token", raise_auth_error)
    monkeypatch.setattr(mcp_local_server.session, "login", lambda: None)
    assert run(mcp_local_server.login()) == {"ok": True}


def test_login_reports_failure_instead_of_raising(monkeypatch):
    def raise_auth_error_no_session():
        raise AuthError("not logged in yet")

    def raise_auth_error_on_login():
        raise AuthError("browser login timed out")

    monkeypatch.setattr(mcp_local_server.session, "access_token", raise_auth_error_no_session)
    monkeypatch.setattr(mcp_local_server.session, "login", raise_auth_error_on_login)
    result = run(mcp_local_server.login())
    assert result["ok"] is False
    assert "timed out" in result["error"]
