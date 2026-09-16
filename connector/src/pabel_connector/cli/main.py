"""Console-script `pabel-connector`: the one CLI an employee runs to wire
PABEL enforcement into whichever agent(s) they use, log in, and check
everything is configured. `install`/`uninstall`/`list` dispatch through
installers/registry.py; `login`/`logout` are agent-independent, thin
wrappers over pabel_client.session (the same OAuth session every adapter's
relay call uses). `doctor` checks both: env vars/login are agent-
independent, but it also re-checks two things per installed agent, both of
which can break silently: that its hook config still actually contains the
pabel hook command, and that its stored credential still authenticates. A
credential that no longer works (revoked installation, rebuilt Keycloak)
looks perfectly healthy on disk and fails only when a real document read is
attempted; a missing hook means nothing enforces PABEL at all. doctor is
where both become loud instead of being discovered from a missing audit
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
from ..pabel_client import agent_session, audit_log, session
from ..pabel_client.keycloak_client import AuthError, KeycloakUnreachable

STATUS_LABELS = {
    "verified": "VERIFIED",
    "unverified": "UNVERIFIED - built to vendor docs, not yet tried against a real install",
    "degraded": "DEGRADED - real vendor limitation, partial coverage only",
    "gap": "NO ADAPTER - documented gap, see docs/known-gaps.md",
    "mcp-only": "MCP TOOLS ONLY - no hook, no enforcement (see docs/known-gaps.md)",
}


def cmd_list(_args) -> int:
    for key, installer in sorted(INSTALLERS.items()):
        if _global_only(installer):
            global_note = " [global only]"
        elif _supports_global(installer):
            global_note = " [supports --global]"
        else:
            global_note = ""
        print(f"{key:15s} {STATUS_LABELS.get(installer.status, installer.status)}{global_note}")
    return 0


def _supports_global(installer) -> bool:
    return hasattr(installer, "GLOBAL_CONFIG_RELATIVE_PATH")


def _global_only(installer) -> bool:
    """True for codex-cli/chatgpt-desktop: both write into the one shared
    ~/.codex/config.toml Codex CLI and the ChatGPT desktop app both read
    (see installers/codex_family.py) - there is no meaningful per-project
    variant to fall back to, unlike every hook-based installer. cmd_install/
    cmd_uninstall reject a call without --global instead of silently
    writing to a --dir path neither product would ever look at."""
    return getattr(installer, "GLOBAL_ONLY", False)


def _base_dir(args) -> Path:
    """`--dir` is now opt-in, so it may be unset - see `_resolve_scope`."""
    return Path(args.dir).resolve() if getattr(args, "dir", None) else Path.cwd()


def _resolve_scope(installer, args) -> "tuple[bool, str | None]":
    """(install globally?, a note to print when that isn't what was asked for).

    Global is the DEFAULT wherever a confirmed user-level location exists.
    Enforcement installed into one project directory is enforcement an agent
    leaves behind by starting anywhere else, while the credential it
    authenticates with lives in ~/.pabel/agent_credentials.json and keeps
    working from every directory on the machine. That asymmetry - authorised
    everywhere, constrained in one folder - is a hole, and it fails open and
    silently, which is the worst way for a security control to fail. PABEL is
    meant to be wired once per machine by whoever administers it, not
    re-installed by each user in each checkout.

    `--dir` remains available to deliberately scope an install to one project;
    it just has to be asked for now.
    """
    if _global_only(installer):
        note = ("this agent only ever installs to its shared user-level config, "
                "so --dir was ignored") if args.dir else None
        return True, note
    if args.global_ and _supports_global(installer):
        return True, None  # explicit, and it outranks --dir
    if not _supports_global(installer):
        # vscode: no confirmed user-level hook location, so a project install
        # is all there is. Said out loud rather than left to look global.
        return False, ("no confirmed global location for this agent, so this is a "
                       "PROJECT-ONLY install - it does not apply outside "
                       f"{_base_dir(args)}. See connector/docs/coverage-matrix.md.")
    if args.dir:
        return False, None
    return True, None


def _install_one(agent: str, installer, base_dir: Path, global_: bool,
                 client_id: str = None, client_secret: str = None) -> int:
    """Wire one agent and store its own credential. Shared by the
    single-agent and multi-select paths - scope (`global_`) is already
    resolved by the caller, since the two paths resolve it differently
    (single: reject a bad combination; multi: adjust per agent and say so)."""
    print(installer.install(base_dir, global_=global_) if global_
          else installer.install(base_dir))
    if not _global_only(installer):
        # codex-cli/chatgpt-desktop capture SHARED_ENV_VARS straight into
        # their own config.toml [env] block at install time (see
        # installers/codex_family.py) - printing "you still need to set
        # these" for them would be actively wrong, not just redundant.
        print("\nEnv vars needed (ask your admin for the deployed values):")
        for var in base.SHARED_ENV_VARS + installer.required_env():
            print(f"  {var}")

    # This installation's own agent credential - never self-generated here:
    # an admin already created it (server/agents_admin.py create-installation)
    # and handed it over out of band. --client-secret is deliberately also
    # promptable (hidden input via getpass) rather than only a CLI flag, so
    # it doesn't have to sit in shell history or a process list.
    client_id = client_id or input(
        f"[{agent}] Agent installation client_id (from your admin): ").strip()
    client_secret = client_secret or getpass.getpass(
        f"[{agent}] Agent installation client_secret (from your admin, hidden): ").strip()
    if not client_id or not client_secret:
        sys.stderr.write(
            "pabel-connector: client_id/client_secret are required - ask your admin "
            f"to run `agents_admin.py create-installation {agent}` and hand you "
            "the result.\n")
        return 2
    agent_session.store_credentials(agent, client_id, client_secret)
    print(f"\nInstallation credentials for {agent!r} saved to "
          f"{agent_session.CREDENTIALS_FILE}.")
    return 0


def _install_audit_key(args) -> None:
    """Opt-in, company-wide (not per-agent, not per-machine): a copy of this
    only enables the *local*, confidentiality-only audit log
    (pabel_client/audit_log.py). Installed once per run, never once per
    selected agent - there is exactly one key per machine."""
    if not args.audit_public_key:
        return
    path = audit_log.install_public_key(args.audit_public_key)
    print(f"Audit-log public key installed to {path} - PABEL-relevant "
          f"decisions will now also be recorded, encrypted, in "
          f"{audit_log.LOG_FILE} (see docs/known-gaps.md's "
          f"\"Client-side audit log\" section for what this does and "
          f"does not guarantee).")


def _parse_selection(raw: str, keys: "list[str]") -> "list[str]":
    """Selection by 1-based number or by agent key, comma/space separated;
    `all` picks everything. Order-preserving and de-duplicated. Returns []
    on any unrecognised token rather than silently installing a subset."""
    raw = raw.strip().lower()
    if raw in ("all", "*"):
        return list(keys)
    selected = []
    for token in raw.replace(",", " ").split():
        if token.isdigit() and 1 <= int(token) <= len(keys):
            key = keys[int(token) - 1]
        elif token in keys:
            key = token
        else:
            sys.stderr.write(f"pabel-connector: not an agent number or key: {token!r}\n")
            return []
        if key not in selected:
            selected.append(key)
    return selected


def cmd_install(args) -> int:
    if args.agent is None:
        return _cmd_install_selected(args)

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

    global_, note = _resolve_scope(installer, args)
    if note:
        print(f"[!!] {note}")
    rc = _install_one(args.agent, installer, _base_dir(args), global_,
                      args.client_id, args.client_secret)
    if rc == 0:
        _install_audit_key(args)
    return rc


def _cmd_install_selected(args) -> int:
    """`pabel-connector install` with no agent: pick several at once.

    Scope is adjusted per agent rather than rejected the way the
    single-agent path does - a batch shouldn't abort halfway because one
    product happens to be global-only - and every adjustment is printed, so
    the result is never quietly different from what was asked for.

    --client-id/--client-secret are deliberately refused here. Each agent
    installation has its OWN Keycloak credential by design: that one-to-one
    mapping is what stops one agent product from authenticating as another
    (see core/decide.py's DENY_CONFIG_TAMPER and server/core.py's
    resolve_agent). Letting one pair be reused across a multi-select would
    hand out exactly the shared identity the rest of this project works to
    prevent, so each selected agent is prompted separately."""
    if args.client_id or args.client_secret:
        sys.stderr.write(
            "pabel-connector: --client-id/--client-secret only apply when installing "
            "one named agent. Each installation needs its own credential (that's what "
            "keeps one agent from authenticating as another), so a multi-select prompts "
            "for each - re-run as `pabel-connector install <agent> --client-id ...`.\n")
        return 2

    keys = sorted(INSTALLERS)
    print("Agents PABEL can be wired into:\n")
    for i, key in enumerate(keys, 1):
        installer = INSTALLERS[key]
        print(f"  {i:>2}. {key:15s} {STATUS_LABELS.get(installer.status, installer.status)}")
    print("\nEach one you pick needs its own client_id/client_secret from your admin\n"
          "(`agents_admin.py create-installation <agent>`).")

    selected = _parse_selection(
        input("\nInstall which? numbers or keys, comma-separated (or 'all'): "), keys)
    if not selected:
        sys.stderr.write("pabel-connector: nothing selected - nothing installed.\n")
        return 2

    base_dir = _base_dir(args)
    failed = []
    for key in selected:
        installer = INSTALLERS[key]
        global_, note = _resolve_scope(installer, args)
        print(f"\n--- {key} ({'machine-wide' if global_ else base_dir}) ---")
        if note:
            print(f"[!!] {note}")
        if _install_one(key, installer, base_dir, global_) != 0:
            failed.append(key)

    _install_audit_key(args)
    print(f"\nInstalled: {', '.join(k for k in selected if k not in failed) or 'none'}")
    if failed:
        sys.stderr.write(f"Failed: {', '.join(failed)}\n")
        return 1
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
    # Mirrors install's default exactly: uninstalling from the project
    # directory after a machine-wide install would report success while
    # leaving the real wiring in place.
    global_, _ = _resolve_scope(installer, args)
    base_dir = _base_dir(args)
    if hasattr(installer, "uninstall"):
        # codex-cli/chatgpt-desktop: TOML, not JSON - their own uninstall()
        # knows how to remove just their own entry from the shared file
        # without teaching this generic path about TOML at all.
        print(installer.uninstall(base_dir, global_=global_))
        return 0
    path = (base.global_config_path(installer.GLOBAL_CONFIG_RELATIVE_PATH) if global_
            else installer.config_path(base_dir))
    data = base.read_json(path)
    commands = {base.hook_command(k) for k in installer.HOOK_KEYS}
    if base.remove_matching_commands(data, commands):
        base.write_json(path, data)
        print(f"Removed pabel hooks from {path}")
    else:
        print(f"No pabel hooks found in {path} - nothing to do.")

    if hasattr(installer, "SKILL_RELATIVE_PATH"):
        skill_path = (base.global_config_path(installer.SKILL_RELATIVE_PATH) if global_
                      else base_dir / installer.SKILL_RELATIVE_PATH)
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
    documented-gap agents - cline, continue-dev) are skipped, not flagged.
    Also skipped: installers with a config_path but no HOOK_KEYS at all
    (codex-cli, chatgpt-desktop) - neither has any hook/interception
    mechanism to check, only MCP tool registration; without this guard,
    the HOOK_KEYS lookup below would raise AttributeError instead."""
    installer = INSTALLERS.get(agent_id)
    if installer is None:
        return f"no installer registered for {agent_id!r} - can't check its hook wiring"

    global_problem = (_wiring_problem_at(installer, agent_id, base_dir, True)
                      if _supports_global(installer) else None)
    if _supports_global(installer) and global_problem is None:
        return None
    if _global_only(installer):
        return global_problem

    project_problem = _wiring_problem_at(installer, agent_id, base_dir, False)
    if project_problem is None and _supports_global(installer):
        # Wired here but not machine-wide: the agent is enforced in this one
        # directory and unenforced everywhere else, while its credential works
        # from anywhere. Silent until now, and it fails open.
        return (f"PABEL is wired for {agent_id!r} inside {base_dir} but NOT machine-wide, "
                f"so it stops applying as soon as {agent_id!r} is started from any other "
                f"directory - while its stored credential keeps working there. "
                f"Run: pabel-connector install {agent_id}")
    return project_problem


def _wiring_problem_at(installer, agent_id: str, base_dir: Path,
                       global_: bool) -> "str | None":
    """The wiring check for one scope. Split out of `_hook_wiring_ok` so the
    same check can be run against the global and the project location."""
    # opencode's enforcement is a .js bridge plugin, not a command string in a
    # JSON config, so the scan below can't see it at all - it supplies its own
    # equivalent check rather than being silently skipped (which is exactly how
    # vscode's never-installed hook went unnoticed).
    if hasattr(installer, "hook_wiring_problem"):
        return installer.hook_wiring_problem(base_dir, global_=global_)
    if not hasattr(installer, "config_path") or not hasattr(installer, "HOOK_KEYS"):
        return None  # e.g. claude-code: no config file of its own to check
    path = (base.global_config_path(installer.GLOBAL_CONFIG_RELATIVE_PATH) if global_
            else installer.config_path(base_dir))
    data = base.read_json(path)
    commands = {base.hook_command(k) for k in installer.HOOK_KEYS}
    found = {c for c in commands if _command_present(data, c)}
    if found == commands:
        return None
    where = "" if global_ else f" --dir {base_dir}"
    return (f"hook config at {path} is missing or doesn't call the pabel hook "
            f"({len(commands) - len(found)}/{len(commands)} hook point(s) not wired) - "
            f"nothing will actually enforce PABEL for {agent_id!r} until this is fixed. "
            f"Run: pabel-connector install {agent_id}{where}")


def _command_present(node, command: str) -> bool:
    if isinstance(node, dict):
        return any(_command_present(v, command) for v in node.values())
    if isinstance(node, list):
        return any(
            (isinstance(item, dict) and item.get("command") == command)
            or _command_present(item, command)
            for item in node)
    return False


def _credential_problem(agent_id: str) -> "str | None":
    """None if this installation's stored credential still authenticates.

    A stored credential can stop working without anything local changing -
    an admin revoked the installation, or Keycloak was rebuilt and the
    client no longer exists. Nothing else here would notice: the file is
    still present and still well-formed, so `installations()` keeps
    reporting it and the hook keeps trying to use it, failing only at the
    moment a real document read is attempted. This is the same class of
    silent breakage `_hook_wiring_ok` exists for, on the other axis."""
    try:
        agent_session.access_token(agent_id)
        return None
    except KeycloakUnreachable:
        raise
    except AuthError as e:
        return (f"its stored credential no longer authenticates ({e}) - the "
                f"installation was probably revoked, or Keycloak rebuilt. Ask "
                f"your admin for a new one: agents_admin.py create-installation "
                f"{agent_id}")


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
        # One unreachable-Keycloak report for the whole run, not one per
        # installation: they would all fail identically, burying the actual
        # problem (a stopped container) under noise that reads like several
        # broken credentials.
        keycloak_down = None
        for agent_id, client_id in installed.items():
            problems = [p for p in [_hook_wiring_ok(agent_id, base_dir)] if p]
            if keycloak_down is None:
                try:
                    credential = _credential_problem(agent_id)
                except KeycloakUnreachable as e:
                    keycloak_down = str(e)
                    credential = None
                if credential:
                    problems.append(credential)
            if not problems:
                print(f"  [ok] agent installation for {agent_id!r}: {client_id}")
            else:
                for problem in problems:
                    print(f"  [!!] agent installation for {agent_id!r} ({client_id}) "
                          f"exists, but: {problem}")
                ok = False
        if keycloak_down:
            print(f"  [!!] could not check whether the stored credentials still "
                  f"authenticate: {keycloak_down}")
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

    p_install = sub.add_parser("install", help="wire the PABEL relay hook into one or more agents")
    p_install.add_argument("agent", nargs="?", default=None,
                           help="agent key, see `pabel-connector list`. Omit to pick "
                                "several from a list instead")
    p_install.add_argument("--dir", default=None,
                           help="install into ONE project directory instead of machine-wide. "
                                "Opt-in: enforcement scoped to a directory stops applying the "
                                "moment the agent runs anywhere else, while its credential keeps "
                                "working everywhere. Only needed for an agent with no global "
                                "location, or to deliberately confine a trial install")
    p_install.add_argument("--global", dest="global_", action="store_true",
                           help="the default, kept for explicitness: install to this agent's "
                                "user-level config so it applies to every directory on the "
                                "machine. Agents with no confirmed global location (see "
                                "`pabel-connector list`) are rejected rather than written to a "
                                "guessed path")
    p_install.add_argument("--client-id", default=None,
                           help="this installation's Keycloak client_id (from your admin) - prompted if omitted")
    p_install.add_argument("--client-secret", default=None,
                           help="this installation's Keycloak client_secret (from your admin) - "
                                "prompted with hidden input if omitted")
    p_install.add_argument("--audit-public-key", default=None,
                           help="optional: path to the company-wide audit-log public key "
                                "(from your admin's `agents_admin.py generate-audit-keypair`) - "
                                "turns on a local, confidentiality-only encrypted log of "
                                "PABEL-relevant decisions (see docs/known-gaps.md). Omit to "
                                "leave this feature off, the default.")
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="remove the PABEL relay hook from an agent")
    p_uninstall.add_argument("agent", help="agent key, see `pabel-connector list`")
    p_uninstall.add_argument("--dir", default=None,
                             help="remove a PROJECT-scoped install from this directory. Opt-in, "
                                  "matching `install`: without it the machine-wide install is "
                                  "removed")
    p_uninstall.add_argument("--global", dest="global_", action="store_true",
                             help="the default, kept for explicitness: remove the machine-wide "
                                  "install")
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
