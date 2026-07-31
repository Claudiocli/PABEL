"""Console-script `pabel-connector`: the one CLI an employee runs to wire
PABEL enforcement into whichever agent(s) they use, log in, and check
everything is configured. `install`/`uninstall`/`list` dispatch through
installers/registry.py; `login`/`logout`/`doctor` are agent-independent,
thin wrappers over pabel_client.session (the same OAuth session every
adapter's relay call uses).
"""

import argparse
import getpass
import sys
from pathlib import Path

from ..installers import base
from ..installers.registry import INSTALLERS
from ..pabel_client import agent_session, session
from ..pabel_client.keycloak_client import AuthError

STATUS_LABELS = {
    "verified": "VERIFIED",
    "unverified": "UNVERIFIED - built to vendor docs, not yet tried against a real install",
    "degraded": "DEGRADED - real vendor limitation, partial coverage only",
    "gap": "NO ADAPTER - documented gap, see docs/known-gaps.md",
}


def cmd_list(_args) -> int:
    for key, installer in sorted(INSTALLERS.items()):
        print(f"{key:15s} {STATUS_LABELS.get(installer.status, installer.status)}")
    return 0


def cmd_install(args) -> int:
    installer = INSTALLERS.get(args.agent)
    if installer is None:
        sys.stderr.write(f"pabel-connector: unknown agent {args.agent!r}. "
                          f"See `pabel-connector list`.\n")
        return 2
    base_dir = Path(args.dir).resolve()
    print(installer.install(base_dir))
    env_needed = base.SHARED_ENV_VARS + installer.required_env()
    print("\nEnv vars needed (ask your admin for the deployed values):")
    for var in env_needed:
        print(f"  {var}")

    # This installation's own agent credential - never self-generated here:
    # an admin already created it (server/agents_admin.py create-installation)
    # and handed it over out of band. --client-secret is deliberately also
    # promptable (hidden input via getpass) rather than only a CLI flag, so
    # it doesn't have to sit in shell history or a process list.
    client_id = args.client_id or input(
        "Agent installation client_id (from your admin): ").strip()
    client_secret = args.client_secret or getpass.getpass(
        "Agent installation client_secret (from your admin, hidden): ").strip()
    if not client_id or not client_secret:
        sys.stderr.write(
            "pabel-connector: client_id/client_secret are required - ask your admin "
            f"to run `agents_admin.py create-installation {args.agent}` and hand you "
            "the result.\n")
        return 2
    agent_session.store_credentials(args.agent, client_id, client_secret)
    print(f"\nInstallation credentials for {args.agent!r} saved to "
          f"{agent_session.CREDENTIALS_FILE}.")
    return 0


def cmd_uninstall(args) -> int:
    installer = INSTALLERS.get(args.agent)
    if installer is None:
        sys.stderr.write(f"pabel-connector: unknown agent {args.agent!r}.\n")
        return 2
    if not hasattr(installer, "config_path"):
        print(f"Nothing to uninstall for {args.agent!r} - see `pabel-connector install "
              f"{args.agent}` for what this agent actually needs.")
        return 0
    base_dir = Path(args.dir).resolve()
    path = installer.config_path(base_dir)
    data = base.read_json(path)
    commands = {base.hook_command(k) for k in installer.HOOK_KEYS}
    if base.remove_matching_commands(data, commands):
        base.write_json(path, data)
        print(f"Removed pabel hooks from {path}")
    else:
        print(f"No pabel hooks found in {path} - nothing to do.")
    return 0


def cmd_login(_args) -> int:
    print("Opening the system browser for Keycloak login...")
    try:
        session.login()
    except AuthError as e:
        sys.stderr.write(f"Login failed: {e}\n")
        return 1
    print(f"Logged in. Session saved to {session.SESSION_FILE}.")
    return 0


def cmd_logout(_args) -> int:
    existed = session.logout()
    print("Session cleared." if existed else "No active session.")
    return 0


def cmd_doctor(_args) -> int:
    ok = True
    for var in base.SHARED_ENV_VARS:
        import os
        if os.environ.get(var):
            print(f"  [ok] {var} is set")
        else:
            print(f"  [!!] {var} is NOT set")
            ok = False
    try:
        session.access_token()
        print("  [ok] logged in (a usable PABEL session was found)")
    except AuthError as e:
        print(f"  [!!] not logged in: {e}")
        ok = False
    installed = agent_session.installations()
    if installed:
        for agent_id, client_id in installed.items():
            print(f"  [ok] agent installation for {agent_id!r}: {client_id}")
    else:
        print("  [!!] no agent installation credentials stored yet - run "
              "`pabel-connector install <agent>`")
        ok = False
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pabel-connector")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list every registered agent and its coverage status")
    p_list.set_defaults(func=cmd_list)

    p_install = sub.add_parser("install", help="wire the PABEL relay hook into an agent")
    p_install.add_argument("agent", help="agent key, see `pabel-connector list`")
    p_install.add_argument("--dir", default=".", help="project directory to install into (default: cwd)")
    p_install.add_argument("--client-id", default=None,
                           help="this installation's Keycloak client_id (from your admin) - prompted if omitted")
    p_install.add_argument("--client-secret", default=None,
                           help="this installation's Keycloak client_secret (from your admin) - "
                                "prompted with hidden input if omitted")
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="remove the PABEL relay hook from an agent")
    p_uninstall.add_argument("agent", help="agent key, see `pabel-connector list`")
    p_uninstall.add_argument("--dir", default=".", help="project directory to uninstall from (default: cwd)")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_login = sub.add_parser("login", help="log in to PABEL via the system browser")
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", help="clear the saved PABEL session")
    p_logout.set_defaults(func=cmd_logout)

    p_doctor = sub.add_parser("doctor", help="check env vars and login status")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
