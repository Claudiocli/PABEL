"""Bridges Keycloak token verification into mcp.server.auth's resource-
server extension point, for when mcp_server.py runs over the
streamable-http transport (a remote, shared server) instead of stdio (a
local, per-session process).

mcp.server.auth.provider.TokenVerifier is exactly the hook the MCP SDK
provides for "verify a bearer token by delegating to an external IdP"
rather than this project having to be its own OAuth authorization server
- confirmed against the installed mcp==1.28.1 package before relying on
it. FastMCP's bearer-auth middleware calls verify_token() on every
request and, on success, makes the resulting AccessToken available via
mcp.server.auth.middleware.auth_context.get_access_token() for the
duration of that request - core.current_identity() reads it from there
when present, falling back to the stdio session file otherwise.
"""

from mcp.server.auth.provider import AccessToken, TokenVerifier

from auth import AuthError, KeycloakAuth

kc = KeycloakAuth()


class KeycloakTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = kc.verify(token)
        except AuthError:
            return None
        return AccessToken(
            token=token,
            client_id=claims.get("azp") or claims.get("preferred_username", "unknown"),
            scopes=[],
            expires_at=claims.get("exp"),
            subject=claims.get("preferred_username"),
            claims=claims,
        )
