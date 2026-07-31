"""core.resolve_agent() is the entire trust chain for "which agent
installation is calling" - these tests stub the two things it actually
calls out to (kc.verify() and db.*) rather than requiring a live Keycloak/
Postgres, mirroring connector/tests' monkeypatch style.
"""

import pytest

import core
from auth import AuthError


def test_resolve_agent_happy_path(monkeypatch):
    monkeypatch.setattr(core.kc, "verify", lambda token: {"azp": "install-1"})
    monkeypatch.setattr(core.db, "get_agent_installation",
                        lambda client_id: {"agent_id": "claude-code", "revoked": False})
    monkeypatch.setattr(core.db, "get_agent",
                        lambda agent_id: {"attributes": "agent_claude_code",
                                          "required_role": "agent_claude_code_user",
                                          "enabled": True})
    agent_id, attributes = core.resolve_agent("fake-token", ["agent_claude_code_user"])
    assert agent_id == "claude-code"
    assert attributes == "agent_claude_code"


def test_resolve_agent_rejects_a_token_that_does_not_verify(monkeypatch):
    def raise_auth_error(token):
        raise AuthError("bad signature")
    monkeypatch.setattr(core.kc, "verify", raise_auth_error)
    with pytest.raises(AuthError):
        core.resolve_agent("garbage", [])


def test_resolve_agent_rejects_unrecognized_installation(monkeypatch):
    monkeypatch.setattr(core.kc, "verify", lambda token: {"azp": "unknown-client"})
    monkeypatch.setattr(core.db, "get_agent_installation", lambda client_id: None)
    with pytest.raises(AuthError):
        core.resolve_agent("fake-token", [])


def test_resolve_agent_rejects_revoked_installation(monkeypatch):
    monkeypatch.setattr(core.kc, "verify", lambda token: {"azp": "install-1"})
    monkeypatch.setattr(core.db, "get_agent_installation",
                        lambda client_id: {"agent_id": "claude-code", "revoked": True})
    with pytest.raises(AuthError):
        core.resolve_agent("fake-token", [])


def test_resolve_agent_rejects_unknown_agent_product(monkeypatch):
    monkeypatch.setattr(core.kc, "verify", lambda token: {"azp": "install-1"})
    monkeypatch.setattr(core.db, "get_agent_installation",
                        lambda client_id: {"agent_id": "claude-code", "revoked": False})
    monkeypatch.setattr(core.db, "get_agent", lambda agent_id: None)
    with pytest.raises(AuthError):
        core.resolve_agent("fake-token", [])


def test_resolve_agent_rejects_disabled_agent_product(monkeypatch):
    monkeypatch.setattr(core.kc, "verify", lambda token: {"azp": "install-1"})
    monkeypatch.setattr(core.db, "get_agent_installation",
                        lambda client_id: {"agent_id": "claude-code", "revoked": False})
    monkeypatch.setattr(core.db, "get_agent",
                        lambda agent_id: {"attributes": "agent_claude_code",
                                          "required_role": "agent_claude_code_user",
                                          "enabled": False})
    with pytest.raises(AuthError):
        core.resolve_agent("fake-token", [])


def test_resolve_agent_missing_required_role_is_soft_not_an_error(monkeypatch):
    """A known, un-revoked installation whose product's required_role the
    current user's token lacks contributes zero attributes rather than
    raising - the same implicit, section-by-section cryptographic denial
    as an unrecognized installation, just scoped to one user. Regression
    check: this must stay a soft "" outcome through the refactor from a
    trusted env var to a verified per-installation token."""
    monkeypatch.setattr(core.kc, "verify", lambda token: {"azp": "install-1"})
    monkeypatch.setattr(core.db, "get_agent_installation",
                        lambda client_id: {"agent_id": "claude-code", "revoked": False})
    monkeypatch.setattr(core.db, "get_agent",
                        lambda agent_id: {"attributes": "agent_claude_code",
                                          "required_role": "agent_claude_code_user",
                                          "enabled": True})
    agent_id, attributes = core.resolve_agent("fake-token", ["some_other_role"])
    assert agent_id == "claude-code"
    assert attributes == ""
