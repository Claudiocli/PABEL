import pytest

from auth import AuthError, KeycloakAuth


def test_client_id_of_returns_the_azp_claim():
    assert KeycloakAuth.client_id_of({"azp": "pabel-agent-claude-code-abc123"}) == \
        "pabel-agent-claude-code-abc123"


def test_client_id_of_raises_when_azp_is_missing():
    """A token with no azp claim can't identify which installation is
    calling - core.resolve_agent()'s entire trust chain starts here, so
    this must fail loudly, never fall back to any other claim."""
    with pytest.raises(AuthError):
        KeycloakAuth.client_id_of({"preferred_username": "someone"})
