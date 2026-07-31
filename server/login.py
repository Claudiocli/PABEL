"""Interactive human login for the PABEL MCP service.

Run this yourself, in your own terminal - never through an agent. It opens
the system browser at Keycloak's own hosted login page (Authorization Code
+ PKCE, MFA-capable - see oauth_browser.py) and writes the resulting
*tokens* (never a password) to .session.json next to this script, via
core.login_with_browser(). mcp_server.py picks that session up
automatically and re-verifies it on every tool call for as long as it
stays valid.

Usage:
  python login.py            log in (opens the system browser)
  python login.py --logout   drop the current session
  python login.py --whoami   show who the active session belongs to
"""

import sys

import core
from auth import AuthError


def do_login():
    print("Opening the system browser for Keycloak login...")
    try:
        with core.audit_op("cli", "login") as ctx:
            uname, attrs = core.login_with_browser()
            ctx["username"] = uname
    except AuthError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1
    print(f"Logged in as {uname!r} (attributes: {attrs}). "
         f"Session saved to {core.SESSION_FILE.name}.")
    print("mcp_server.py will now act with this identity.")
    return 0


def do_logout():
    with core.audit_op("cli", "logout") as ctx:
        existed = core.logout()
        ctx["detail"] = "session cleared" if existed else "no active session"
    if existed:
        print("Session cleared. No identity is active until you log in again "
             "(python login.py).")
    else:
        print("No active session.")
    return 0


def do_whoami():
    if not core.SESSION_FILE.exists():
        print("No active session. Run: python login.py")
        return 0
    try:
        with core.audit_op("cli", "whoami") as ctx:
            username, attrs, roles = core.current_identity()
            ctx["username"] = username
    except AuthError:
        print("A session file exists but the token is no longer valid "
             "(expired or revoked). Run 'python login.py' again.")
        return 1
    print(f"Active session: {username!r} (attributes: {attrs}, roles: {roles})")
    return 0


if __name__ == "__main__":
    if "--logout" in sys.argv:
        sys.exit(do_logout())
    elif "--whoami" in sys.argv:
        sys.exit(do_whoami())
    else:
        sys.exit(do_login())
