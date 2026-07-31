"""Authorization Code + PKCE login via the system browser.

This is the only login flow this project supports (see auth.py's
docstring for why a direct username/password grant isn't offered here):
real MFA (an authenticator app, WebAuthn, or anything else registered as a
CONFIGURE_TOTP/WebAuthn required action in Keycloak) only ever happens on
Keycloak's own hosted login page, which only a browser-based flow reaches.

The flow, in order:
  1. Generate a PKCE verifier/challenge pair and a random state value.
  2. Open the system browser at KeycloakAuth.authorize_url(...) - the user
     logs in there, including any MFA step Keycloak requires.
  3. Keycloak redirects the browser to http://127.0.0.1:<port>/callback
     with ?code=...&state=...; a short-lived local HTTP server (bound to
     loopback only) catches that one request.
  4. Exchange the code for tokens via KeycloakAuth.exchange_code(),
     completing entirely over the back channel (never through the
     browser), same as any Authorization Code flow.

The Keycloak client (CLIENT_ID, e.g. "pabel") has
directAccessGrantsEnabled=false, so it can only ever be used through a real
browser, never password-only - a login always goes through whatever MFA
the realm requires, with nothing to bypass it.
"""

import base64
import hashlib
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import env
from auth import AuthError, KeycloakAuth

CALLBACK_PORT = int(os.environ.get("PABEL_CALLBACK_PORT", "8766"))
CALLBACK_PATH = "/callback"
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}"


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.callback_result = params
        body = (b"<html><body><p>Login complete. You can close this tab.</p></body></html>"
               if "code" in params else
               b"<html><body><p>Login failed or was cancelled - try again.</p></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _wait_for_callback(timeout):
    """Serve exactly one request on the loopback callback port and return
    its query parameters, or raise AuthError on timeout."""
    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    server.callback_result = None
    server.timeout = timeout
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()
    if server.callback_result is None:
        raise AuthError(
            "browser login timed out - the redirect back from Keycloak "
            "was never received (login not completed in time, or "
            f"nothing is listening on {REDIRECT_URI})")
    return server.callback_result


def login_with_browser(timeout=180):
    """Run the full Authorization Code + PKCE flow and return the raw
    token set (access_token/refresh_token/expires_in)."""
    base_url, realm, client_id = env.require("KEYCLOAK_URL", "REALM", "CLIENT_ID")
    kc = KeycloakAuth(base_url=base_url, realm=realm, client_id=client_id)

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    url = kc.authorize_url(REDIRECT_URI, state, challenge)

    if not webbrowser.open(url):
        raise AuthError(
            f"could not open a system browser automatically - open this "
            f"URL yourself to log in: {url}")

    params = _wait_for_callback(timeout)
    if params.get("state") != state:
        raise AuthError("browser login failed: state mismatch "
                        "(possible cross-site request, or a stale redirect)")
    if "code" not in params:
        error = params.get("error_description") or params.get("error") or "no code returned"
        raise AuthError(f"browser login failed: {error}")

    return kc.exchange_code(params["code"], REDIRECT_URI, verifier)
