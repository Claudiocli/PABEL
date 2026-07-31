import json

import pytest

import pabel_connector.pabel_client.agent_session as agent_session
from pabel_connector.pabel_client.keycloak_client import AuthError


@pytest.fixture(autouse=True)
def isolated_credentials_file(tmp_path, monkeypatch):
    """Every test gets its own, empty credentials file - never the real
    ~/.pabel/agent_credentials.json."""
    monkeypatch.setattr(agent_session, "DATA_DIR", tmp_path)
    monkeypatch.setattr(agent_session, "CREDENTIALS_FILE", tmp_path / "agent_credentials.json")


def test_store_then_installations_reflects_it():
    agent_session.store_credentials("claude-code", "client-abc", "secret-1")
    assert agent_session.installations() == {"claude-code": "client-abc"}


def test_store_is_keyed_per_agent_product():
    agent_session.store_credentials("claude-code", "client-abc", "secret-1")
    agent_session.store_credentials("cursor", "client-xyz", "secret-2")
    assert agent_session.installations() == {"claude-code": "client-abc", "cursor": "client-xyz"}


def test_store_replaces_only_the_named_agent():
    agent_session.store_credentials("claude-code", "client-abc", "secret-1")
    agent_session.store_credentials("cursor", "client-xyz", "secret-2")
    agent_session.store_credentials("claude-code", "client-new", "secret-3")
    assert agent_session.installations() == {"claude-code": "client-new", "cursor": "client-xyz"}


def test_store_clears_any_previously_cached_access_token():
    data = {"claude-code": {"client_id": "client-abc", "client_secret": "secret-1",
                            "access_token": "stale", "expires_in": 300, "obtained_at": 0}}
    agent_session.CREDENTIALS_FILE.write_text(json.dumps(data))
    agent_session.store_credentials("claude-code", "client-abc", "secret-1")
    on_disk = json.loads(agent_session.CREDENTIALS_FILE.read_text())
    assert "access_token" not in on_disk["claude-code"]


def test_access_token_raises_when_agent_never_installed():
    with pytest.raises(AuthError):
        agent_session.access_token("claude-code")


def test_access_token_fetches_and_persists_a_fresh_token(monkeypatch):
    agent_session.store_credentials("claude-code", "client-abc", "secret-1")
    monkeypatch.setenv("PABEL_KEYCLOAK_URL", "http://keycloak.example")
    monkeypatch.setenv("PABEL_KEYCLOAK_REALM", "pabel")

    calls = []

    class FakeKeycloakClient:
        def __init__(self, base_url, realm, client_id):
            calls.append((base_url, realm, client_id))

        def client_credentials(self, client_secret):
            assert client_secret == "secret-1"
            return {"access_token": "fresh-token", "expires_in": 300}

    monkeypatch.setattr(agent_session, "KeycloakClient", FakeKeycloakClient)

    token = agent_session.access_token("claude-code")
    assert token == "fresh-token"
    assert calls == [("http://keycloak.example", "pabel", "client-abc")]
    on_disk = json.loads(agent_session.CREDENTIALS_FILE.read_text())
    assert on_disk["claude-code"]["access_token"] == "fresh-token"


def test_access_token_reuses_a_still_valid_cached_token(monkeypatch):
    agent_session.store_credentials("claude-code", "client-abc", "secret-1")
    data = json.loads(agent_session.CREDENTIALS_FILE.read_text())
    data["claude-code"].update(
        {"access_token": "cached-token", "expires_in": 300, "obtained_at": __import__("time").time()})
    agent_session.CREDENTIALS_FILE.write_text(json.dumps(data))

    def fail_if_called(*a, **kw):
        raise AssertionError("should not request a new token while the cached one is valid")
    monkeypatch.setattr(agent_session, "KeycloakClient", fail_if_called)

    assert agent_session.access_token("claude-code") == "cached-token"


def test_access_token_refreshes_a_near_expiry_cached_token(monkeypatch):
    agent_session.store_credentials("claude-code", "client-abc", "secret-1")
    data = json.loads(agent_session.CREDENTIALS_FILE.read_text())
    data["claude-code"].update(
        {"access_token": "stale-token", "expires_in": 60, "obtained_at": 0})
    agent_session.CREDENTIALS_FILE.write_text(json.dumps(data))
    monkeypatch.setenv("PABEL_KEYCLOAK_URL", "http://keycloak.example")
    monkeypatch.setenv("PABEL_KEYCLOAK_REALM", "pabel")

    class FakeKeycloakClient:
        def __init__(self, base_url, realm, client_id):
            pass

        def client_credentials(self, client_secret):
            return {"access_token": "renewed-token", "expires_in": 300}

    monkeypatch.setattr(agent_session, "KeycloakClient", FakeKeycloakClient)
    assert agent_session.access_token("claude-code") == "renewed-token"
