"""Per-installation agent credential lifecycle.

Unlike the human's browser session (session.py), these credentials are
never obtained by logging in: an admin creates them once, out of band
(server/agents_admin.py create-installation), and hands them to this
installation; `pabel-connector install <agent> --client-id ... --client-secret
...` only ever stores what it's given - this module never talks to the
PABEL server to "register" anything, only to Keycloak's own token endpoint,
and only once a credential already exists locally.

Keyed per agent product (not one flat file like session.py), because one
machine can have several agent products installed side by side (e.g. both
Claude Code and Cursor), each its own independently enrolled installation
with its own client_id/client_secret.
"""

import json
import os
import time
from pathlib import Path

from .keycloak_client import AuthError, KeycloakClient

DATA_DIR = Path(os.environ.get("PABEL_PLUGIN_DATA_DIR") or Path.home() / ".pabel")
CREDENTIALS_FILE = DATA_DIR / "agent_credentials.json"


def _read_all():
    if not CREDENTIALS_FILE.exists():
        return {}
    return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))


def _write_all(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def store_credentials(agent_id, client_id, client_secret):
    """Persist a client_id/client_secret an admin already created for this
    installation - the only way an entry for agent_id ever comes to exist.
    Replaces any previous entry outright (so a stale cached access_token
    from an old credential is never carried over) and never contacts
    Keycloak or the PABEL server: purely a local write."""
    data = _read_all()
    data[agent_id] = {"client_id": client_id, "client_secret": client_secret}
    _write_all(data)


def installations():
    """{agent_id: client_id, ...} - for `pabel-connector doctor`."""
    return {agent_id: entry["client_id"] for agent_id, entry in _read_all().items()}


def _keycloak_client(client_id):
    base_url = os.environ.get("PABEL_KEYCLOAK_URL")
    realm = os.environ.get("PABEL_KEYCLOAK_REALM")
    if not base_url or not realm:
        raise AuthError("PABEL_KEYCLOAK_URL/PABEL_KEYCLOAK_REALM are not set - "
                        "see the connector's README.md")
    return KeycloakClient(base_url, realm, client_id)


def access_token(agent_id):
    """A client_credentials access token for this installation, believed
    to still be valid - requesting a fresh one first if the cached one is
    missing or close to expiry (same margin logic as session.py). The
    remote server re-verifies it fully regardless; this is only local
    bookkeeping to avoid sending a token already known to be stale."""
    data = _read_all()
    entry = data.get(agent_id)
    if entry is None:
        raise AuthError(
            f"no installation credentials stored for agent {agent_id!r} - run: "
            f"pabel-connector install {agent_id} --client-id ... --client-secret ...")
    if "access_token" not in entry or \
            time.time() - entry.get("obtained_at", 0) > entry.get("expires_in", 60) - 5:
        kc = _keycloak_client(entry["client_id"])
        tokens = kc.client_credentials(entry["client_secret"])
        entry["access_token"] = tokens["access_token"]
        entry["expires_in"] = tokens.get("expires_in", 60)
        entry["obtained_at"] = time.time()
        data[agent_id] = entry
        _write_all(data)
    return entry["access_token"]
