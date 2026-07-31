"""Minimal Keycloak Authorization Code + PKCE client - login/refresh only.

Unlike server/auth.py's KeycloakAuth, this never verifies a token's
signature: this code only ever needs to *hold* a valid bearer token to
attach to a request against the already-deployed PABEL server, which
re-verifies it fully on every call (see server/token_verifier.py). No jwt
library, no JWKS fetch - this process trusts Keycloak's HTTPS response,
same as any OAuth client does for its own tokens.
"""

import os
from urllib.parse import urlencode

import requests


class AuthError(Exception):
    pass


class KeycloakClient:
    def __init__(self, base_url, realm, client_id):
        self.client_id = client_id
        issuer = f"{base_url.rstrip('/')}/realms/{realm}"
        self.authorization_endpoint = f"{issuer}/protocol/openid-connect/auth"
        self.token_endpoint = f"{issuer}/protocol/openid-connect/token"

    @classmethod
    def from_env(cls):
        base_url = os.environ.get("PABEL_KEYCLOAK_URL")
        realm = os.environ.get("PABEL_KEYCLOAK_REALM")
        client_id = os.environ.get("PABEL_KEYCLOAK_CLIENT_ID")
        missing = [n for n, v in (("PABEL_KEYCLOAK_URL", base_url),
                                   ("PABEL_KEYCLOAK_REALM", realm),
                                   ("PABEL_KEYCLOAK_CLIENT_ID", client_id)) if not v]
        if missing:
            raise AuthError(f"missing required env var(s): {', '.join(missing)}")
        return cls(base_url, realm, client_id)

    def authorize_url(self, redirect_uri, state, code_challenge, scope="openid"):
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.authorization_endpoint}?{urlencode(params)}"

    def exchange_code(self, code, redirect_uri, code_verifier):
        try:
            resp = requests.post(self.token_endpoint, data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }, timeout=10)
        except requests.RequestException as e:
            raise AuthError(f"cannot reach Keycloak: {e}") from e
        if resp.status_code != 200:
            raise AuthError(f"code exchange failed: {resp.status_code} {resp.text}")
        return resp.json()

    def refresh(self, refresh_token):
        try:
            resp = requests.post(self.token_endpoint, data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": refresh_token,
            }, timeout=10)
        except requests.RequestException as e:
            raise AuthError(f"cannot reach Keycloak: {e}") from e
        if resp.status_code != 200:
            raise AuthError(f"token refresh failed: {resp.status_code} {resp.text}")
        return resp.json()
