"""Keycloak authentication for the PABEL MCP service.

Only the Authorization Code + PKCE flow is supported (see
oauth_browser.py): Keycloak's own hosted login page runs, so whatever the
realm's browser flow requires - password, an authenticator app (TOTP:
Google Authenticator, Okta Verify, etc.), WebAuthn - applies exactly as it
would logging into the Keycloak account console, with no client-side code
needed to special-case any of it. There is deliberately no Resource Owner
Password Credentials (direct username/password) grant here: it cannot run
realm MFA at all (required actions like "configure OTP" and anything
WebAuthn-based are a browser-flow concept a direct grant skips entirely),
and every operation this service performs is on behalf of an AI agent
acting for a human - that combination must never be backed by a weaker
login than a human would use directly.

KeycloakAuth.verify() validates a bearer token's signature (against
Keycloak's published JWKS), issuer and expiry. core.py calls this on every
tool invocation - nothing is cached as "logged in"; the token itself is
re-checked each time, and its abe_attributes claim is what decides which
sections a document read may return.
"""

import os
import time
from urllib.parse import urlencode

import jwt
import requests
from jwt import PyJWKClient

import env


class AuthError(Exception):
    pass


class KeycloakAuth:
    def __init__(self, base_url=None, realm=None, client_id=None, issuer_base_url=None):
        """base_url is where THIS process actually reaches Keycloak over
        the network (JWKS fetch, token exchange) - inside a container,
        that's the compose service name (e.g. http://keycloak:8080).
        issuer_base_url is what Keycloak stamps into a token's `iss` claim,
        which reflects whatever hostname the ORIGINAL browser login used
        (e.g. http://localhost:8080) - not necessarily reachable from
        inside a container at all. These are the same value in Phase 1's
        single-host stdio setup (the default here), but must be split for
        a container that reaches Keycloak by a different name than the
        human who logged in did - see KEYCLOAK_ISSUER_URL, only needed
        when they differ."""
        if base_url is None or realm is None or client_id is None:
            base_url, realm, client_id = env.require(
                "KEYCLOAK_URL", "REALM", "CLIENT_ID")
        if issuer_base_url is None:
            issuer_base_url = os.environ.get("KEYCLOAK_ISSUER_URL", base_url)
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.issuer = f"{issuer_base_url.rstrip('/')}/realms/{self.realm}"
        self.authorization_endpoint = f"{self.issuer}/protocol/openid-connect/auth"
        self.token_endpoint = f"{self.issuer}/protocol/openid-connect/token"
        self.jwks_uri = f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/certs"
        self._jwks_client = None

    def authorize_url(self, redirect_uri, state, code_challenge, scope="openid"):
        """The URL to open in a browser to start Authorization Code + PKCE.
        Keycloak's own login page runs at this URL - MFA included."""
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
        """Trade an authorization code (from the browser redirect) for
        tokens. No client secret: this is a public client, and PKCE's
        code_verifier is what proves this exchange belongs to the same
        party that started the authorize_url() request."""
        try:
            resp = requests.post(self.token_endpoint, data={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }, timeout=10)
        except requests.RequestException as e:
            raise AuthError(f"cannot reach Keycloak at {self.base_url}: {e}") from e
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
            raise AuthError(f"cannot reach Keycloak at {self.base_url}: {e}") from e
        if resp.status_code != 200:
            raise AuthError(f"token refresh failed: {resp.status_code} {resp.text}")
        return resp.json()

    def verify(self, access_token):
        """Validate signature, issuer and expiry; return the token claims."""
        try:
            if self._jwks_client is None:
                self._jwks_client = PyJWKClient(self.jwks_uri)
            signing_key = self._jwks_client.get_signing_key_from_jwt(access_token)
            claims = jwt.decode(
                access_token, signing_key.key,
                algorithms=["RS256"], issuer=self.issuer,
                options={"verify_aud": False})
        except jwt.PyJWTError as e:
            raise AuthError(f"invalid token: {e}") from e
        except requests.RequestException as e:
            raise AuthError(f"cannot reach Keycloak at {self.base_url}: {e}") from e
        if claims.get("exp", 0) < time.time():
            raise AuthError("token expired")
        return claims

    @staticmethod
    def username_of(claims):
        name = claims.get("preferred_username")
        if not name:
            raise AuthError("token has no preferred_username claim")
        return name

    @staticmethod
    def attributes_of(claims):
        """The user's ABE attributes: a free-form list of '|'-joinable
        tokens (plain tags like 'dev', or 'name=value' numeric pairs like
        'livello=3'), sourced from the realm's multivalued 'abe_attributes'
        user attribute via its protocol mapper. Not a fixed set of named
        fields - a Keycloak admin can add or remove any token for any user
        without a code or mapper change."""
        return list(claims.get("abe_attributes", []))

    @staticmethod
    def roles_of(claims):
        """Realm roles from the token's standard realm_access.roles claim
        (Keycloak includes this by default, no protocol mapper needed).
        Used to gate which agents this user may use - see
        core.resolve_agent()."""
        return list(claims.get("realm_access", {}).get("roles", []))
