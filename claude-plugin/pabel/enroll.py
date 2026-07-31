"""One-time local provisioning for this installation's agent credential.

Run this yourself, in your own terminal - never through an agent. An admin
already created a Keycloak client_credentials client for this specific
installation (server/agents_admin.py create-installation claude-code ...)
and handed you its client_id/client_secret out of band; this script only
ever stores what you give it, in ~/.pabel/agent_credentials.json (or
PABEL_PLUGIN_DATA_DIR if set) - it never talks to Keycloak or the PABEL
server itself. The relay hook (hooks/pabel_relay_hook.py) picks this up
automatically on every subsequent call - see core/decide.py.

This is a separate credential from your own human login (login.py) - both
are required for a relay to succeed (see server/core.py's resolve_agent()).

Usage:
  python enroll.py CLIENT_ID CLIENT_SECRET
  python enroll.py CLIENT_ID          # prompts for the secret (hidden input)
"""

import getpass
import sys

from pabel_connector.pabel_client import agent_session

AGENT_ID = "claude-code"


def main():
    if len(sys.argv) < 2:
        print("usage: python enroll.py CLIENT_ID [CLIENT_SECRET]", file=sys.stderr)
        return 2
    client_id = sys.argv[1]
    client_secret = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass(
        "Agent installation client_secret (from your admin, hidden): ")
    agent_session.store_credentials(AGENT_ID, client_id, client_secret)
    print(f"Installation credentials for {AGENT_ID!r} saved to "
          f"{agent_session.CREDENTIALS_FILE}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
