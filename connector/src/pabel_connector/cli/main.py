"""Console-script `pabel-connector`: the one CLI an employee runs to wire
PABEL enforcement into whichever agent(s) they use, log in, and check
everything is configured. `install`/`uninstall`/`list` dispatch through
installers/registry.py; `login`/`logout` are agent-independent, thin
wrappers over pabel_client.session (the same OAuth session every adapter's
relay call uses). `doctor` checks both: env vars/login are agent-
independent, but it also re-checks, per installed agent, that its hook
config still actually contains the pabel hook command - a stored
credential with no working hook wired (install never run in this project
directory, or its config file edited/reverted since) means nothing
enforces PABEL for that agent at all, silently; doctor is where that
becomes loud instead of only discoverable by noticing a missing audit
trail after the fact.
"""

import argparse
import getpass
import json
import sys
from pathlib import Path

from .. import managed_settings
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
        global_note = " [supports --global]" if _supports_global(installer) else ""
        print(f"{key:15s} {STATUS_LABELS.get(installer.status, installer.status)}{global_note}")
    return 0


def _supports_global(installer) -> bool:
    return hasattr(installer, "GLOBAL_CONFIG_RELATIVE_PATH")


def cmd_install(args) -> int:
    installer = INSTALLERS.get(args.agent)
    if installer is None:
        sys.stderr.write(f"pabel-connector: unknown agent {args.agent!r}. "
                          f"See `pabel-connector list`.\n")
        return 2
    if args.global_ and not _supports_global(installer):
        sys.stderr.write(
            f"pabel-connector: {args.agent!r} has no confirmed global/user-level hook "
            f"location - install per-project with --dir instead of --global. See "
            f"connector/docs/coverage-matrix.md for why (not guessed, so not offered "
            f"here rather than risk writing to a path nothing reads).\n")
        return 2
    base_dir = Path(args.dir).resolve()
    print(installer.install(base_dir, global_=args.global_) if args.global_
          else installer.install(base_dir))
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
    if args.global_ and not _supports_global(installer):
        sys.stderr.write(f"pabel-connector: {args.agent!r} has no confirmed global/user-level "
                          f"hook location - nothing to uninstall with --global.\n")
        return 2
    path = (base.global_config_path(installer.GLOBAL_CONFIG_RELATIVE_PATH) if args.global_
            else installer.config_path(Path(args.dir).resolve()))
    data = base.read_json(path)
    commands = {base.hook_command(k) for k in installer.HOOK_KEYS}
    if base.remove_matching_commands(data, commands):
        base.write_json(path, data)
        print(f"Removed pabel hooks from {path}")
    else:
        print(f"No pabel hooks found in {path} - nothing to do.")

    if hasattr(installer, "SKILL_RELATIVE_PATH"):
        skill_path = (base.global_config_path(installer.SKILL_RELATIVE_PATH) if args.global_
                      else Path(args.dir).resolve() / installer.SKILL_RELATIVE_PATH)
        if skill_path.exists():
            skill_path.unlink()
            print(f"Removed {skill_path}")
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


def _hook_wiring_ok(agent_id: str, base_dir: Path) -> "str | None":
    """None if agent_id's own hook config actually contains the pabel hook
    command(s) it's supposed to - the thing that turns a stored credential
    into real enforcement. A credential can exist with no working hook at
    all (exactly what happened with vscode - install was never run in this
    project directory) with nothing else ever surfacing that; doctor is
    where it should be loud instead of silent. Returns a one-line problem
    description otherwise; installers with no config file at all (the
    documented-gap agents - cline, continue-dev) are skipped, not flagged."""
    installer = INSTALLERS.get(agent_id)
    if installer is None:
        return f"no installer registered for {agent_id!r} - can't check its hook wiring"
    if not hasattr(installer, "config_path"):
        return None  # e.g. claude-code: no config file of its own to check
    path = installer.config_path(base_dir)
    data = base.read_json(path)
    commands = {base.hook_command(k) for k in installer.HOOK_KEYS}
    found = {c for c in commands if _command_present(data, c)}
    if found == commands:
        return None
    return (f"hook config at {path} is missing or doesn't call the pabel hook "
            f"({len(commands) - len(found)}/{len(commands)} hook point(s) not wired) - "
            f"nothing will actually enforce PABEL for {agent_id!r} until this is fixed. "
            f"Run: pabel-connector install {agent_id} --dir {base_dir}")


def _command_present(node, command: str) -> bool:
    if isinstance(node, dict):
        return any(_command_present(v, command) for v in node.values())
    if isinstance(node, list):
        return any(
            (isinstance(item, dict) and item.get("command") == command)
            or _command_present(item, command)
            for item in node)
    return False


def cmd_doctor(args) -> int:
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
        base_dir = Path(args.dir).resolve()
        for agent_id, client_id in installed.items():
            problem = _hook_wiring_ok(agent_id, base_dir)
            if problem is None:
                print(f"  [ok] agent installation for {agent_id!r}: {client_id}")
            else:
                print(f"  [!!] agent installation for {agent_id!r} ({client_id}) exists, "
                      f"but: {problem}")
                ok = False
    else:
        print("  [!!] no agent installation credentials stored yet - run "
              "`pabel-connector install <agent>`")
        ok = False
    return 0 if ok else 1


def cmd_generate_managed_settings(args) -> int:
    """Admin-facing, not part of an employee's own `install` - writes the
    two files connector/docs/managed-settings.md tells an admin to deploy
    to a managed machine, built from the same source `install()` itself
    uses so they can't silently drift from what the hook/MCP registration
    actually expect. Never touches the registry or `C:\\Program
    Files\\ClaudeCode\\` itself - see deploy/Deploy-ManagedSettings.ps1 for
    that step, run separately and deliberately by whoever has admin rights
    on the target machine."""
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    settings_path = out_dir / "managed-settings.json"
    mcp_path = out_dir / "managed-mcp.json"
    settings_path.write_text(
        json.dumps(managed_settings.generate_managed_settings(args.python_path), indent=2) + "\n",
        encoding="utf-8")
    mcp_path.write_text(
        json.dumps(managed_settings.generate_managed_mcp(args.python_path, args.server_url), indent=2) + "\n",
        encoding="utf-8")
    if not args.python_path:
        print(f"[!!] --python-path not given - used this interpreter's own path "
              f"({sys.executable}). That's almost certainly wrong for a fleet "
              f"deployment (see managed-settings.md); re-run with "
              f"--python-path pointing at a machine-wide interpreter every "
              f"managed device actually has before deploying either file.")
    print(f"Wrote {settings_path}")
    print(f"Wrote {mcp_path}")
    print("\nNeither file has been deployed anywhere yet. Deploy them with "
          "deploy/Deploy-ManagedSettings.ps1 (run as Administrator on the "
          "target machine), or manually per connector/docs/managed-settings.md. "
          "After deploying, verify with `/status` inside Claude Code and "
          "`claude mcp list` - see that document for what a correct result "
          "looks like and the one silent-failure mode to check for.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pabel-connector")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list every registered agent and its coverage status")
    p_list.set_defaults(func=cmd_list)

    p_install = sub.add_parser("install", help="wire the PABEL relay hook into an agent")
    p_install.add_argument("agent", help="agent key, see `pabel-connector list`")
    p_install.add_argument("--dir", default=".", help="project directory to install into (default: cwd)")
    p_install.add_argument("--global", dest="global_", action="store_true",
                           help="install to this agent's user-level/global config instead of "
                                "--dir, applying to every project - only offered for agents with "
                                "a confirmed global location (see `pabel-connector list` and "
                                "connector/docs/coverage-matrix.md); rejected otherwise rather "
                                "than guessing a path")
    p_install.add_argument("--client-id", default=None,
                           help="this installation's Keycloak client_id (from your admin) - prompted if omitted")
    p_install.add_argument("--client-secret", default=None,
                           help="this installation's Keycloak client_secret (from your admin) - "
                                "prompted with hidden input if omitted")
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="remove the PABEL relay hook from an agent")
    p_uninstall.add_argument("agent", help="agent key, see `pabel-connector list`")
    p_uninstall.add_argument("--dir", default=".", help="project directory to uninstall from (default: cwd)")
    p_uninstall.add_argument("--global", dest="global_", action="store_true",
                             help="uninstall from this agent's user-level/global config instead of --dir")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_login = sub.add_parser("login", help="log in to PABEL via the system browser")
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", help="clear the saved PABEL session")
    p_logout.set_defaults(func=cmd_logout)

    p_doctor = sub.add_parser("doctor", help="check env vars, login status, and hook wiring")
    p_doctor.add_argument("--dir", default=".",
                          help="project directory each installed agent's hook config "
                               "was written into (default: cwd) - used to verify the "
                               "hook is actually wired, not just that a credential exists")
    p_doctor.set_defaults(func=cmd_doctor)

    p_managed = sub.add_parser(
        "generate-managed-settings",
        help="admin-only: generate managed-settings.json/managed-mcp.json for Claude Code "
             "(see connector/docs/managed-settings.md) - writes files only, deploys nothing")
    p_managed.add_argument("--out-dir", default=".",
                           help="directory to write managed-settings.json/managed-mcp.json into "
                                "(default: cwd)")
    p_managed.add_argument("--python-path", default=None,
                           help="machine-wide Python interpreter path every managed device has "
                                "pabel-connector installed into - required for a real fleet "
                                "deployment; defaults to this interpreter's own path otherwise "
                                "(fine for previewing the output shape, wrong to actually ship)")
    p_managed.add_argument("--server-url", default=managed_settings.DEFAULT_SERVER_URL,
                           help="value for the 'pabel' MCP server's url field - defaults to "
                                "${PABEL_SERVER_URL}, expanded from each managed machine's own "
                                "environment, matching .mcp.json's existing convention")
    p_managed.set_defaults(func=cmd_generate_managed_settings)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
