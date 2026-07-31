"""Interactive human login for the PABEL relay hook.

Run this yourself, in your own terminal - never through an agent. It opens
the system browser at Keycloak's own hosted login page (Authorization Code
+ PKCE, MFA-capable) and saves the resulting tokens to
~/.pabel/session.json (or PABEL_PLUGIN_DATA_DIR if set). The relay hook
(hooks/pabel_relay_hook.py) picks this session up automatically and
refreshes it as needed - it is what lets the hook act on your behalf when
it transparently relays an .abe file to the deployed PABEL server.

This is a separate login from whatever OAuth session Claude Code's own
MCP client may hold for direct calls to this plugin's whoami tool - see
README.md.

Requires PABEL_KEYCLOAK_URL, PABEL_KEYCLOAK_REALM, PABEL_KEYCLOAK_CLIENT_ID
to be set (see README.md).

Usage:
  python login.py            log in (opens the system browser)
  python login.py --logout   drop the current session
"""

import sys

from pabel_connector.pabel_client import session
from pabel_connector.pabel_client.keycloak_client import AuthError


def do_login():
    print("Opening the system browser for Keycloak login...")
    try:
        session.login()
    except AuthError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1
    print(f"Logged in. Session saved to {session.SESSION_FILE}.")
    print("The PABEL relay hook will now act with this identity.")
    return 0


def do_logout():
    existed = session.logout()
    print("Session cleared." if existed else "No active session.")
    return 0


if __name__ == "__main__":
    sys.exit(do_logout() if "--logout" in sys.argv else do_login())
