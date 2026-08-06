"""Authorization Code + PKCE login via the system browser.

Ported from server/oauth_browser.py (same flow, same reasoning: real MFA
only ever runs on Keycloak's own hosted login page, which only a
browser-based flow reaches) - trimmed to use keycloak_client.KeycloakClient
instead of the server's full auth.KeycloakAuth (no local token
verification needed here, see keycloak_client.py's docstring).
"""

import base64
import hashlib
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .keycloak_client import AuthError, KeycloakClient

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
        body = (b"<html><body><p>PABEL login complete. You can close this tab.</p></body></html>"
               if "code" in params else
               b"<html><body><p>Login failed or was cancelled - try again.</p></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _wait_for_callback(timeout, url):
    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    server.callback_result = None
    server.timeout = timeout
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()
    if server.callback_result is None:
        # webbrowser.open() only tells us a browser process was launched, not
        # that it stayed up or ever reached Keycloak - a browser that crashes
        # or gets closed after opening looks identical to this same timeout,
        # so the manual URL has to be included here too, not just in the
        # "couldn't launch a browser at all" case below - found live 2026-08
        # when ChatGPT desktop's own bundled Chrome build crashed immediately
        # after webbrowser.open() reported success, leaving no other way to
        # recover the URL.
        raise AuthError(
            "browser login timed out - the redirect back from Keycloak was "
            "never received (login not completed in time, the browser "
            "crashed/closed after opening, or nothing is listening on "
            f"{REDIRECT_URI}). Open this URL yourself in a working browser "
            f"to log in: {url}")
    return server.callback_result


def login_with_browser(timeout=180):
    """Run the full Authorization Code + PKCE flow and return the raw
    token set (access_token/refresh_token/expires_in)."""
    kc = KeycloakClient.from_env()

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    url = kc.authorize_url(REDIRECT_URI, state, challenge)

    if not webbrowser.open(url):
        raise AuthError(
            f"could not open a system browser automatically - open this "
            f"URL yourself to log in: {url}")

    params = _wait_for_callback(timeout, url)
    if params.get("state") != state:
        raise AuthError("browser login failed: state mismatch "
                        "(possible cross-site request, or a stale redirect)")
    if "code" not in params:
        error = params.get("error_description") or params.get("error") or "no code returned"
        raise AuthError(f"browser login failed: {error}")

    return kc.exchange_code(params["code"], REDIRECT_URI, verifier)
