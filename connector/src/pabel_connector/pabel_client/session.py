"""Token lifecycle for the relay hook: persisted to a file (this hook runs
as a fresh process on every tool call, so in-memory state alone wouldn't
survive between calls) - same pattern as server/core.py's Session, but a
separate file, since this is a distinct login from whatever OAuth session
an agent's own MCP client may separately hold for direct tool calls
(whoami/read_document called by the model itself, not through the hook).
"""

import json
import os
import time
from pathlib import Path

from .keycloak_client import AuthError, KeycloakClient
from .oauth_browser import login_with_browser

DATA_DIR = Path(os.environ.get("PABEL_PLUGIN_DATA_DIR") or Path.home() / ".pabel")
SESSION_FILE = DATA_DIR / "session.json"


def login():
    """Run the browser login flow and persist the resulting tokens."""
    tokens = login_with_browser()
    _persist(tokens)


def logout():
    existed = SESSION_FILE.exists()
    if existed:
        SESSION_FILE.unlink()
    return existed


def _persist(tokens):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": tokens.get("expires_in", 60),
        "obtained_at": time.time(),
    }, indent=2), encoding="utf-8")


def access_token():
    """A bearer token believed to still be valid - refreshing first if the
    cached one is close to expiry. The remote server re-verifies it fully
    regardless; this is only local bookkeeping to avoid sending a token
    already known to be stale."""
    if not SESSION_FILE.exists():
        raise AuthError("not logged in to PABEL yet - run the connector's "
                        "login command first (see README.md)")
    data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    if time.time() - data["obtained_at"] > data.get("expires_in", 60) - 5:
        if not data.get("refresh_token"):
            raise AuthError("session expired and no refresh token available - log in again")
        kc = KeycloakClient.from_env()
        tokens = kc.refresh(data["refresh_token"])
        _persist(tokens)
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    return data["access_token"]
